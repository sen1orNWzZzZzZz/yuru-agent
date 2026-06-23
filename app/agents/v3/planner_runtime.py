"""
PlannerRuntime - 让 LLM 决定调用哪个 Tool 的执行引擎
支持 thought / tool_call / observation 步骤记录，用于前端可视化思考链。
"""
import json
import logging
from typing import Any, Callable

from app.agents.v3.base import AgentResult
from app.agents.v3.planning_run import PlanningRunService
from app.agents.v3.tool import ToolRegistry
from app.agents.v3.tools import agent_result_to_observation
from app.integrations.llm_client import LLMClient

logger = logging.getLogger(__name__)


# 工具名称 -> results 中使用的短 key 前缀
# 例如 search_attractions 的结果应保存为 attraction_result，
# 与 _build_itinerary_prompt / _generate_template 中使用的 key 保持一致。
_TOOL_KEY_PREFIX = {
    "get_weather": "weather",
    "search_hotels": "hotel",
    "search_restaurants": "restaurant",
    "search_attractions": "attraction",
    "plan_transport": "transport",
    "risk_check": "risk",
}


DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {
            "type": "string",
            "description": "Planner 当前的思考过程，说明为什么需要调用下一个工具或为什么结束。",
        },
        "next_tool": {
            "type": "string",
            "description": "下一步要调用的工具名称。如果不需要再调用工具，请留空或设为 null。",
        },
        "tool_input": {
            "type": "object",
            "description": "传递给 next_tool 的参数对象，必须匹配该工具的 parameters schema。如果不需要参数，可留空。",
        },
        "finish": {
            "type": "boolean",
            "description": "是否已收集到足够信息，可以生成最终行程。",
        },
    },
    "required": ["thought", "finish"],
}


class PlannerRuntime:
    """
    工具驱动型规划运行时

    执行流程：
    1. 根据当前已收集的 context 和 tool 结果，询问 LLM 下一步该调哪个 tool。
    2. 执行选中的 tool，记录 observation。
    3. 重复直到 LLM 认为信息足够（finish=true）或达到最大步数。
    4. 使用收集到的结果生成最终行程。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        max_steps: int = 10,
        on_step: Callable[[dict], None] | None = None,
        run_service: PlanningRunService | None = None,
        run_id: int | None = None,
        initial_results: dict[str, AgentResult] | None = None,
    ):
        self.llm = llm_client
        self.registry = tool_registry
        self.max_steps = max_steps
        self.on_step = on_step
        self.run_service = run_service
        self.run_id = run_id
        self.initial_results = initial_results or {}

    def _emit(self, step: dict, cached_result: AgentResult | None = None) -> None:
        if self.run_service and self.run_id:
            try:
                duration_ms = 0
                status = "failed" if step.get("error") else "completed"
                if step["type"] == "observation" and step.get("result"):
                    duration_ms = step["result"].get("duration_ms", 0) or 0
                self.run_service.add_step(
                    run_id=self.run_id,
                    step_number=step.get("step", 0),
                    step_type=step["type"],
                    content=step.get("content", ""),
                    tool_name=step.get("tool"),
                    tool_input=step.get("tool_input"),
                    observation=step.get("result") if step["type"] == "observation" else None,
                    cached_result=cached_result if step["type"] == "observation" else None,
                    status=status,
                    duration_ms=duration_ms,
                )
            except Exception as e:
                logger.warning(f"[PlannerRuntime] 持久化 step 失败: {e}")
        if self.on_step:
            try:
                self.on_step(step)
            except Exception as e:
                logger.warning(f"[PlannerRuntime] on_step 回调失败: {e}")

    def _build_decision_prompt(self, context: dict, results: dict[str, AgentResult]) -> str:
        tools_desc = json.dumps(self.registry.definitions(), ensure_ascii=False, indent=2)

        collected = {}
        for key, res in results.items():
            collected[key] = {
                "status": res.status,
                "duration_ms": res.duration_ms,
                "summary": agent_result_to_observation(res)["summary"],
            }

        return f"""你是旅行规划中枢 PlannerAgent。你需要根据用户需求，决定下一步调用哪个工具来收集信息。

【用户需求】
目的地: {context.get('destination', '')}
天数: {context.get('days', 3)}天
人数: {context.get('travelers', 2)}人
预算: {context.get('budget', '未设定')}
出发地: {context.get('origin', '')}
风格: {context.get('style', '')}
兴趣: {context.get('interests', '无')}
必去: {context.get('must_visit', '无')}
避免: {context.get('avoid', '无')}
特殊需求: {context.get('special_needs', '无')}
季节: {context.get('season', '未设定')}

【可用工具】
{tools_desc}

【已收集信息】
{json.dumps(collected, ensure_ascii=False, indent=2)}

请输出 JSON 决定下一步：
- 如果还需要更多信息，选择 next_tool（必须是可用工具名称之一）。
- 如果某个工具需要特定参数（如查询特定区域酒店、特定类型景点），在 tool_input 中按 schema 传入。
- 如果信息已足够，设置 finish=true，next_tool 为空，tool_input 为空。
- 在 thought 中说明你的思考过程。

注意：
1. 不要重复调用已经调用过且状态为 completed 的工具，除非有明确理由。
2. risk_check 应该在其他信息都收集完后再调用。
3. 如果某个工具调用失败（status != completed），可以考虑重试一次或继续。
4. 生成最终行程前，必须至少调用过 search_hotels、search_restaurants、search_attractions，否则无法生成含酒店、餐厅、景点的完整行程。
"""

    def _force_call_tool(
        self,
        tool_name: str,
        context: dict[str, Any],
        results: dict[str, AgentResult],
        steps: list[dict],
        step_idx: int,
    ) -> int:
        """强制调用一个工具（用于补全 LLM 漏掉的关键工具），返回新的 step_idx。"""
        tool = self.registry.get(tool_name)
        if not tool:
            return step_idx

        result_key = f"{_TOOL_KEY_PREFIX.get(tool_name, tool_name)}_result"
        if result_key in results:
            return step_idx

        tool_call_step = {
            "type": "tool_call",
            "step": step_idx,
            "tool": tool_name,
            "tool_input": {},
        }
        steps.append(tool_call_step)
        self._emit(tool_call_step)

        logger.info(f"[PlannerRuntime] 强制补调工具: {tool_name}")
        try:
            result = tool.execute({"context": context, "results": results, "tool_input": {}})
        except Exception as e:
            logger.exception(f"[PlannerRuntime] 工具 {tool_name} 强制调用异常")
            result = AgentResult(
                agent_type=tool_name,
                agent_name=tool_name,
                status="failed",
                error=str(e),
            )

        results[result_key] = result

        observation = {
            "type": "observation",
            "step": step_idx,
            "tool": tool_name,
            "tool_input": {},
            "result": agent_result_to_observation(result),
        }
        steps.append(observation)
        self._emit(observation, cached_result=result)
        return step_idx + 1

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        运行 Planner 决策循环
        Returns: {
            "results": dict[str, AgentResult],
            "steps": list[dict],
            "risk": AgentResult,
            "finished": bool,
        }
        """
        results: dict[str, AgentResult] = dict(self.initial_results)
        steps: list[dict] = []
        finished = False

        system_prompt = (
            "你是旅行规划系统的中枢 PlannerAgent。你会根据用户需求逐步调用工具收集信息，"
            "每次只决定下一个工具，并解释原因。请严格按 JSON Schema 输出。"
        )

        for step_idx in range(self.max_steps):
            user_prompt = self._build_decision_prompt(context, results)
            decision_result = self.llm.chat_structured(
                system_prompt,
                user_prompt,
                response_schema=DECISION_SCHEMA,
                temperature=0.3,
                max_tokens=500,
                use_cache=False,
            )

            if not decision_result.get("success"):
                logger.warning(f"[PlannerRuntime] LLM 决策失败: {decision_result.get('error')}")
                thought_step = {
                    "type": "thought",
                    "step": step_idx,
                    "content": f"决策失败: {decision_result.get('error')}，将使用已收集信息继续生成行程。",
                }
                steps.append(thought_step)
                self._emit(thought_step)
                break

            decision = decision_result.get("data", {})
            thought = decision.get("thought", "")
            next_tool_name = decision.get("next_tool") or None
            tool_input = decision.get("tool_input") or {}
            finish = bool(decision.get("finish", False))

            thought_step = {"type": "thought", "step": step_idx, "content": thought}
            steps.append(thought_step)
            self._emit(thought_step)

            if finish or not next_tool_name:
                finished = True
                break

            tool = self.registry.get(next_tool_name)
            tool_call_step = {
                "type": "tool_call",
                "step": step_idx,
                "tool": next_tool_name,
                "tool_input": tool_input,
            }
            steps.append(tool_call_step)
            self._emit(tool_call_step)

            if not tool:
                observation = {
                    "type": "observation",
                    "step": step_idx,
                    "tool": next_tool_name,
                    "tool_input": tool_input,
                    "error": f"工具 {next_tool_name} 不存在",
                }
                steps.append(observation)
                self._emit(observation)
                continue

            logger.info(f"[PlannerRuntime] 调用工具: {next_tool_name}, 输入: {tool_input}")
            try:
                result = tool.execute({"context": context, "results": results, "tool_input": tool_input})
            except Exception as e:
                logger.exception(f"[PlannerRuntime] 工具 {next_tool_name} 执行异常")
                result = AgentResult(
                    agent_type=next_tool_name,
                    agent_name=next_tool_name,
                    status="failed",
                    error=str(e),
                )

            result_key = f"{_TOOL_KEY_PREFIX.get(tool.name, tool.name)}_result"
            results[result_key] = result

            observation = {
                "type": "observation",
                "step": step_idx,
                "tool": next_tool_name,
                "tool_input": tool_input,
                "result": agent_result_to_observation(result),
            }
            steps.append(observation)
            self._emit(observation, cached_result=result)

        # LLM 可能提前 finish，漏掉关键工具；强制补全酒店/餐厅/景点
        step_idx = len(steps)
        for essential in ["search_hotels", "search_restaurants", "search_attractions"]:
            step_idx = self._force_call_tool(essential, context, results, steps, step_idx)

        # 确保 risk 至少有个默认结果；如果还没调用也补一个
        step_idx = self._force_call_tool("risk_check", context, results, steps, step_idx)
        risk = results.get("risk_check_result", AgentResult(
            agent_type="risk", agent_name="风控Agent", status="completed",
            data={"warnings": [], "is_safe": True, "warning_count": 0},
        ))

        return {
            "results": results,
            "steps": steps,
            "risk": risk,
            "finished": finished,
        }
