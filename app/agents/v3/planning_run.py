"""
PlanningRun 状态机服务
负责一次规划请求的生命周期管理：创建、推进步骤、状态流转、断点续跑、worker 认领。
"""
import json
import logging
from typing import Any

from app.agents.v3.base import AgentResult
from app.db.database import execute, get_db_connection, query_all, query_one

logger = logging.getLogger(__name__)

# worker 认领超时时间（分钟），超过后其他 worker 可重新认领
CLAIM_TIMEOUT_MINUTES = 5


class PlanningRunService:
    """规划运行服务"""

    @staticmethod
    def create_run(
        user_id: int | None,
        input_params: dict[str, Any],
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> int:
        """创建一个新的规划 Run（非幂等，内部使用）"""
        conn = get_db_connection()
        try:
            run_id = execute(
                conn,
                """
                INSERT INTO planning_runs
                (user_id, input_params, status, current_step, total_steps, idempotency_key, trace_id)
                VALUES (?, ?, 'pending', 0, 0, ?, ?)
                """,
                (user_id, json.dumps(input_params, ensure_ascii=False), idempotency_key, trace_id),
            )
            logger.info(f"[PlanningRun] 创建 run_id={run_id} trace_id={trace_id}")
            return run_id
        finally:
            conn.close()

    @staticmethod
    def create_run_idempotent(
        user_id: int | None,
        input_params: dict[str, Any],
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[int, bool]:
        """
        幂等创建 Run。
        如果 (user_id, idempotency_key) 已存在，直接返回已有 run_id（无论状态）。
        返回: (run_id, is_new)
        """
        if not idempotency_key:
            return PlanningRunService.create_run(user_id, input_params, None, trace_id), True

        conn = get_db_connection()
        try:
            existing = query_one(
                conn,
                """
                SELECT id, status FROM planning_runs
                WHERE user_id IS ? AND idempotency_key = ?
                ORDER BY id DESC LIMIT 1
                """,
                (user_id, idempotency_key),
            )
            if existing:
                logger.info(f"[PlanningRun] 幂等命中已有 run_id={existing['id']} status={existing['status']}")
                return existing["id"], False

            run_id = execute(
                conn,
                """
                INSERT INTO planning_runs
                (user_id, input_params, status, current_step, total_steps, idempotency_key, trace_id)
                VALUES (?, ?, 'pending', 0, 0, ?, ?)
                """,
                (user_id, json.dumps(input_params, ensure_ascii=False), idempotency_key, trace_id),
            )
            logger.info(f"[PlanningRun] 幂等创建新 run_id={run_id} trace_id={trace_id}")
            return run_id, True
        finally:
            conn.close()

    @staticmethod
    def get_run(run_id: int) -> dict | None:
        """获取 Run 基本信息"""
        conn = get_db_connection()
        try:
            return query_one(conn, "SELECT * FROM planning_runs WHERE id = ?", (run_id,))
        finally:
            conn.close()

    @staticmethod
    def get_steps(run_id: int) -> list[dict]:
        """获取 Run 的所有步骤"""
        conn = get_db_connection()
        try:
            return query_all(
                conn,
                "SELECT * FROM planning_steps WHERE run_id = ? ORDER BY step_number, id",
                (run_id,),
            )
        finally:
            conn.close()

    @staticmethod
    def add_step(
        run_id: int,
        step_number: int,
        step_type: str,
        content: str = "",
        tool_name: str | None = None,
        tool_input: dict | None = None,
        observation: dict | None = None,
        cached_result: AgentResult | None = None,
        status: str = "completed",
        duration_ms: int = 0,
        trace_id: str | None = None,
    ) -> int:
        """向 Run 追加一个步骤"""
        conn = get_db_connection()
        try:
            cached_json = None
            if cached_result is not None:
                cached_json = json.dumps(cached_result.to_dict(), ensure_ascii=False)
            step_id = execute(
                conn,
                """
                INSERT INTO planning_steps
                (run_id, step_number, step_type, tool_name, tool_input, content,
                 observation_json, cached_result_json, status, duration_ms, trace_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    step_number,
                    step_type,
                    tool_name,
                    json.dumps(tool_input, ensure_ascii=False) if tool_input else None,
                    content,
                    json.dumps(observation, ensure_ascii=False) if observation else None,
                    cached_json,
                    status,
                    duration_ms,
                    trace_id,
                ),
            )
            execute(
                conn,
                "UPDATE planning_runs SET current_step = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (step_number, run_id),
            )
            return step_id
        finally:
            conn.close()

    @staticmethod
    def update_status(
        run_id: int,
        status: str,
        error_message: str | None = None,
        itinerary_id: int | None = None,
        total_steps: int | None = None,
    ) -> None:
        """更新 Run 状态"""
        conn = get_db_connection()
        try:
            fields = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
            params: list[Any] = [status]
            if error_message is not None:
                fields.append("error_message = ?")
                params.append(error_message)
            if itinerary_id is not None:
                fields.append("itinerary_id = ?")
                params.append(itinerary_id)
            if total_steps is not None:
                fields.append("total_steps = ?")
                params.append(total_steps)
            params.append(run_id)
            execute(
                conn,
                f"UPDATE planning_runs SET {', '.join(fields)} WHERE id = ?",
                tuple(params),
            )
            logger.info(f"[PlanningRun] run_id={run_id} 状态更新为 {status}")
        finally:
            conn.close()

    @staticmethod
    def list_pending_for_worker(limit: int = 10) -> list[dict]:
        """列出待 worker 认领执行的 Run（含超时释放的 running Run）"""
        conn = get_db_connection()
        try:
            return query_all(
                conn,
                """
                SELECT * FROM planning_runs
                WHERE status IN ('pending', 'retrying')
                  AND (claimed_at IS NULL
                       OR claimed_at < DATETIME('now', ?))
                ORDER BY id ASC
                LIMIT ?
                """,
                (f"-{CLAIM_TIMEOUT_MINUTES} minutes", limit),
            )
        finally:
            conn.close()

    @staticmethod
    def claim_run_for_execution(run_id: int, worker_id: str) -> bool:
        """
        CAS 认领 Run。
        只有状态为 pending/retrying 且未认领或认领超时的 Run 才能被认领。
        返回是否认领成功。
        """
        conn = get_db_connection()
        try:
            execute(
                conn,
                """
                UPDATE planning_runs
                SET status = 'running',
                    claimed_at = CURRENT_TIMESTAMP,
                    claimed_by = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status IN ('pending', 'retrying')
                  AND (claimed_at IS NULL
                       OR claimed_at < DATETIME('now', ?))
                """,
                (worker_id, run_id, f"-{CLAIM_TIMEOUT_MINUTES} minutes"),
            )
            conn.commit()
            run = query_one(conn, "SELECT status, claimed_by FROM planning_runs WHERE id = ?", (run_id,))
            if run and run["status"] == "running" and run["claimed_by"] == worker_id:
                logger.info(f"[PlanningRun] run_id={run_id} 被 worker={worker_id} 认领")
                return True
            return False
        finally:
            conn.close()

    @staticmethod
    def release_stuck_runs(timeout_minutes: int = CLAIM_TIMEOUT_MINUTES) -> int:
        """
        释放长时间未更新的 running Run，重置为 pending 等待重新执行。
        服务启动时调用，避免重启后任务永远卡住。
        """
        conn = get_db_connection()
        try:
            cursor = conn.execute(
                """
                UPDATE planning_runs
                SET status = 'pending',
                    claimed_at = NULL,
                    claimed_by = NULL,
                    error_message = '服务重启，任务被重置为待执行',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'running'
                  AND updated_at < DATETIME('now', ?)
                """,
                (f"-{timeout_minutes} minutes",),
            )
            conn.commit()
            count = cursor.rowcount
            if count:
                logger.warning(f"[PlanningRun] 释放了 {count} 个卡住的 running Run")
            return count
        finally:
            conn.close()

    @staticmethod
    def clear_steps_for_retry(run_id: int) -> None:
        """重试前清空旧步骤（保留 Run 元信息）"""
        conn = get_db_connection()
        try:
            execute(conn, "DELETE FROM planning_steps WHERE run_id = ?", (run_id,))
            execute(
                conn,
                """
                UPDATE planning_runs
                SET current_step = 0, total_steps = 0, error_message = NULL,
                    claimed_at = NULL, claimed_by = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (run_id,),
            )
        finally:
            conn.close()

    @staticmethod
    def prepare_retry(run_id: int) -> dict | None:
        """
        准备重试：
        - 读取原 Run 的 input_params 和已完成的中间结果
        - 把原 Run 状态改为 retrying
        - 清空旧 steps（后续由 planner 根据 initial_results 回放）
        - 返回原 Run 的 input_params 与 initial_results，供重新执行
        """
        conn = get_db_connection()
        try:
            run = query_one(conn, "SELECT * FROM planning_runs WHERE id = ?", (run_id,))
            if not run:
                return None

            # 先恢复已完成的中间结果，再清空 steps
            initial_results = PlanningRunService.restore_completed_results(run_id)

            execute(
                conn,
                """
                UPDATE planning_runs
                SET status = 'retrying', error_message = NULL,
                    claimed_at = NULL, claimed_by = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (run_id,),
            )
            execute(conn, "DELETE FROM planning_steps WHERE run_id = ?", (run_id,))
            try:
                input_params = json.loads(run.get("input_params") or "{}")
            except json.JSONDecodeError:
                input_params = {}
            return {
                "run_id": run_id,
                "user_id": run.get("user_id"),
                "input_params": input_params,
                "initial_results": initial_results,
            }
        finally:
            conn.close()

    @staticmethod
    def restore_completed_results(run_id: int) -> dict[str, AgentResult]:
        """
        从 planning_steps 中恢复已完成的 tool observation 结果，用于断点续跑。
        返回: {tool_name}_result -> AgentResult
        """
        conn = get_db_connection()
        try:
            steps = query_all(
                conn,
                """
                SELECT * FROM planning_steps
                WHERE run_id = ? AND step_type = 'observation' AND status = 'completed'
                ORDER BY step_number, id
                """,
                (run_id,),
            )
            results: dict[str, AgentResult] = {}
            for step in steps:
                cached = step.get("cached_result_json")
                if not cached:
                    continue
                try:
                    data = json.loads(cached)
                    result = AgentResult(
                        agent_type=data.get("agent_type", step.get("tool_name", "")),
                        agent_name=data.get("agent_name", ""),
                        status=data.get("status", "completed"),
                        data=data.get("data", {}),
                        reasoning=data.get("reasoning", ""),
                        duration_ms=data.get("duration_ms", 0),
                        error=data.get("error", ""),
                        usage=data.get("usage", {}),
                    )
                    key = f"{step.get('tool_name')}_result"
                    results[key] = result
                except Exception as e:
                    logger.warning(f"[PlanningRun] 恢复 step 失败: {e}")
            logger.info(f"[PlanningRun] run_id={run_id} 恢复了 {len(results)} 个已完成结果")
            return results
        finally:
            conn.close()

    @staticmethod
    def list_runs(user_id: int | None = None, limit: int = 50) -> list[dict]:
        """列出 Run，可按用户过滤"""
        conn = get_db_connection()
        try:
            if user_id:
                return query_all(
                    conn,
                    "SELECT * FROM planning_runs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                )
            return query_all(
                conn,
                "SELECT * FROM planning_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        finally:
            conn.close()
