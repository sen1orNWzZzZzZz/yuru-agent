"""
Trace 上下文管理模块

使用 contextvars 在异步/线程边界间传递 trace_id 与 span_id，
实现 HTTP 请求 → PlanningRun → Agent → 外部 API 的调用链串联。
"""
import contextvars
import json
import uuid
from datetime import datetime
from typing import Any

from app.db.database import execute, get_db_connection

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)
_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("span_id", default=None)
_parent_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("parent_span_id", default=None)


def generate_trace_id() -> str:
    """生成 16 位十六进制 trace_id"""
    return uuid.uuid4().hex[:16]


def generate_span_id() -> str:
    """生成 16 位十六进制 span_id"""
    return uuid.uuid4().hex[:16]


def get_trace_id() -> str | None:
    return _trace_id.get()


def get_span_id() -> str | None:
    return _span_id.get()


def get_parent_span_id() -> str | None:
    return _parent_span_id.get()


def set_trace_id(tid: str | None) -> None:
    _trace_id.set(tid)


def set_span_id(sid: str | None) -> None:
    _span_id.set(sid)


def set_parent_span_id(pid: str | None) -> None:
    _parent_span_id.set(pid)


def clear() -> None:
    """清空当前上下文"""
    _trace_id.set(None)
    _span_id.set(None)
    _parent_span_id.set(None)


def current_context() -> dict[str, Any]:
    """获取当前上下文快照，用于跨线程恢复"""
    return {
        "trace_id": get_trace_id(),
        "span_id": get_span_id(),
        "parent_span_id": get_parent_span_id(),
    }


def restore_context(ctx: dict[str, Any]) -> None:
    """从快照恢复上下文"""
    set_trace_id(ctx.get("trace_id"))
    set_span_id(ctx.get("span_id"))
    set_parent_span_id(ctx.get("parent_span_id"))


def record_span(
    name: str,
    service: str,
    start_time: datetime,
    end_time: datetime,
    status: str = "ok",
    meta: dict[str, Any] | None = None,
    error: str | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    trace_id: str | None = None,
) -> str | None:
    """
    将一条 Span 写入 trace_spans 表。
    若未传入 trace_id，自动从当前上下文读取；若无 trace_id 则跳过。
    返回写入的 span_id，未写入时返回 None。
    """
    tid = trace_id or get_trace_id()
    if not tid:
        return None
    sid = span_id or generate_span_id()
    pid = parent_span_id if parent_span_id is not None else get_span_id()
    duration_ms = max(0, int((end_time - start_time).total_seconds() * 1000))
    try:
        conn = get_db_connection()
        try:
            execute(
                conn,
                """
                INSERT INTO trace_spans
                (trace_id, span_id, parent_span_id, name, service,
                 start_time, end_time, duration_ms, status, meta_json, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tid,
                    sid,
                    pid,
                    name,
                    service,
                    start_time.isoformat(),
                    end_time.isoformat(),
                    duration_ms,
                    status,
                    json.dumps(meta or {}, ensure_ascii=False),
                    error or "",
                ),
            )
        finally:
            conn.close()
    except Exception as e:
        # 写入失败不应影响主流程
        import logging
        logging.getLogger(__name__).warning(f"[Tracing] 写入 span 失败: {e}")
        return None
    return sid
