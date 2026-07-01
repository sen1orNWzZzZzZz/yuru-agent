"""
AgentScheduler - 依赖驱动的并行 Agent 调度器（生产级执行核心）

与"分层 barrier"不同，本调度器"依赖一就绪就提交"：
restaurant 只依赖 hotel，hotel 完成即起跑，不必等 attraction 依赖的 weather。
这样能榨取最大并行度，是更贴近生产的做法。

特性：
- 依赖驱动：agent 的 depends_on 全部完成后才提交执行。
- 线程池并行：I/O 密集（查库/地图/天气/LLM）在线程池中并发。
- 每 agent 超时：单个 agent 卡死不拖垮整个规划（超时后放弃该 future，其结果不写回 state）。
- 取消：支持 cancel_event，客户端断连时尽快停止提交新任务。
- Trace 透传：每个工作线程开头 restore_context，保证并发下 span 父子关系不丢。
"""
from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from itertools import count
from typing import Any, Callable

from app.agents.v3.base import AgentResult, BaseAgentV3
from app.agents.v3.state import PlanningState
from app.agents.v3.tools import agent_result_to_observation
from app.tracing import current_context, restore_context

logger = logging.getLogger(__name__)

# 单个 agent 的默认执行超时（秒）
DEFAULT_AGENT_TIMEOUT = 30.0
# 调度循环轮询间隔（秒）
_POLL_INTERVAL = 0.2


class AgentScheduler:
    def __init__(
        self,
        agents_by_type: dict[str, BaseAgentV3],
        max_workers: int = 5,
        agent_timeout: float = DEFAULT_AGENT_TIMEOUT,
        cancel_event: threading.Event | None = None,
    ):
        self.agents_by_type = agents_by_type
        self.max_workers = max_workers
        self.agent_timeout = agent_timeout
        self.cancel_event = cancel_event

    def _cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

    def run(
        self,
        state: PlanningState,
        plan: list[str] | None = None,
        emit: Callable[..., None] | None = None,
        agent_inputs: dict[str, dict] | None = None,
    ) -> PlanningState:
        """
        执行 plan 中列出的 agent（按依赖顺序、最大并行度）。

        Args:
            state: 共享黑板，agent 结果写入其中。
            plan: 要执行的 agent_type 列表；None 表示执行全部已注册 agent。
            emit: 可选回调，接收 (step_dict) 或 (step_dict, AgentResult)，用于 trace/SSE。
                  step_dict 形如 {"type": "tool_call"|"observation", "step": n, "tool": t, ...}
            agent_inputs: 可选，agent_type -> 额外参数，合并进该 agent 的执行 context
                          （形态 B 中由 LLM 单次规划为每个 agent 指定的专属参数）。
        """
        plan = list(plan) if plan is not None else list(self.agents_by_type.keys())
        plan = [t for t in plan if t in self.agents_by_type]
        plan_set = set(plan)
        agent_inputs = agent_inputs or {}

        # 每个待执行 agent 的未满足依赖（仅计入本次 plan 内、且尚未在 state 中的依赖）
        remaining: dict[str, set[str]] = {}
        for t in plan:
            if state.has(t):
                continue  # 断点续跑：已完成的直接跳过
            deps = set(self.agents_by_type[t].depends_on) & plan_set
            remaining[t] = {d for d in deps if not state.has(d)}

        parent_ctx = current_context()
        step_counter = count(1)
        emit_lock = threading.Lock()

        def do_emit(step: dict, result: AgentResult | None = None) -> None:
            if not emit:
                return
            with emit_lock:
                try:
                    if result is not None:
                        emit(step, result)
                    else:
                        emit(step)
                except Exception as e:  # emit 失败不应影响调度
                    logger.warning(f"[AgentScheduler] emit 失败: {e}")

        def run_agent(agent_type: str) -> AgentResult:
            restore_context(parent_ctx)  # 线程内透传 trace 上下文
            agent = self.agents_by_type[agent_type]
            # as_legacy_context 提供 {agent_type}_result（兼容 RiskAgent 等未迁移的读法），
            # 同时注入 _state 供已迁移的下游 agent 通过领域访问器读上游；
            # agent_inputs[agent_type] 是 LLM 为该 agent 指定的专属参数（形态 B）。
            ctx = {**state.as_legacy_context(), **agent_inputs.get(agent_type, {}), "_state": state}
            return agent.execute(ctx)

        def unblock(done_type: str) -> None:
            for deps in remaining.values():
                deps.discard(done_type)

        futures: dict[concurrent.futures.Future, str] = {}
        submitted_at: dict[str, float] = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while remaining or futures:
                if self._cancelled():
                    logger.info("[AgentScheduler] 收到取消信号，停止提交新任务")
                    break

                # 1) 提交所有依赖已满足的 agent
                ready = [t for t, deps in remaining.items() if not deps]
                for t in ready:
                    del remaining[t]
                    do_emit({"type": "tool_call", "step": next(step_counter), "tool": t, "tool_input": {}})
                    fut = pool.submit(run_agent, t)
                    futures[fut] = t
                    submitted_at[t] = time.time()

                if not futures:
                    # 无可提交、也无运行中 → 依赖不可满足（异常情况），退出避免死循环
                    if remaining:
                        logger.warning(f"[AgentScheduler] 依赖无法满足，跳过: {list(remaining)}")
                    break

                # 2) 等待至少一个完成（带轮询，以便检查超时与取消）
                done, _ = concurrent.futures.wait(
                    futures, timeout=_POLL_INTERVAL,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )

                for fut in done:
                    t = futures.pop(fut)
                    submitted_at.pop(t, None)
                    try:
                        result = fut.result()
                    except Exception as e:
                        logger.exception(f"[AgentScheduler] agent {t} 执行异常")
                        result = AgentResult(agent_type=t, agent_name=t, status="failed", error=str(e))
                    state.put(result)
                    unblock(t)
                    do_emit(
                        {"type": "observation", "step": next(step_counter), "tool": t,
                         "tool_input": {}, "result": agent_result_to_observation(result)},
                        result,
                    )

                # 3) 超时检查：超过 agent_timeout 仍未完成的，标记失败并放弃（其线程结果不写回 state）
                now = time.time()
                timed_out = [
                    (fut, t) for fut, t in list(futures.items())
                    if now - submitted_at.get(t, now) > self.agent_timeout
                ]
                for fut, t in timed_out:
                    logger.warning(f"[AgentScheduler] agent {t} 超时（>{self.agent_timeout}s），放弃")
                    futures.pop(fut, None)
                    submitted_at.pop(t, None)
                    fut.cancel()
                    result = AgentResult(
                        agent_type=t, agent_name=t, status="failed",
                        error=f"执行超时（>{self.agent_timeout}s）",
                    )
                    state.put(result)
                    unblock(t)
                    do_emit(
                        {"type": "observation", "step": next(step_counter), "tool": t,
                         "tool_input": {}, "result": agent_result_to_observation(result)},
                        result,
                    )

        return state
