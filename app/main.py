"""
智能旅游多Agent规划系统 V3 - FastAPI主入口
PlannerAgent + 子Agent架构
集成LLM/天气API/地图API，数据库存储Mock数据
"""
import asyncio
import logging
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Mount

from app.agents.v3.base import AgentResult
from app.agents.v3.planner import PlannerAgent
from app.agents.v3.planning_run import PlanningRunService
from app.auth import (
    get_current_user,
    get_password_hash,
    get_user_by_id,
    get_user_by_username,
    get_user_profile,
    merge_profile_with_request,
    profile_to_public,
    require_user,
    serialize_json_field,
    verify_password,
)
from app.db.database import execute, get_db_connection, init_db, query_all, query_one
from app.integrations.config_manager import IntegrationConfig
from app.knowledge import (
    ChecklistGenerator,
    RAG_AVAILABLE,
    TipsRetriever,
    ensure_ingested,
)
from app.mcp_server import MCPMessagesRoute, handle_mcp_sse

# ============================================================
# 初始化
# ============================================================
app = FastAPI(
    title="智能旅游多Agent规划系统 V3",
    description="PlannerAgent + 子Agent架构 | LLM/天气/地图API集成 | 数据库Mock",
    version="3.0.0",
)

# Session 中间件（用于登录态）
SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "lingdong-lvxing-v3-default-secret")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=86400 * 7)

# MCP Server 挂载到独立 Starlette 子应用，避免经过 FastAPI 的 HTTP 中间件
mcp_app = Starlette(routes=[
    Mount("/sse", app=handle_mcp_sse),
    Mount("/messages/", app=MCPMessagesRoute()),
])
app.mount("/mcp", mcp_app)

# 静态文件和模板
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


# ============================================================
# 请求日志中间件
# ============================================================
EXCLUDED_LOG_PATHS = {"/static", "/favicon.ico"}


def _should_log_path(path: str) -> bool:
    """判断是否需要记录请求日志"""
    for excluded in EXCLUDED_LOG_PATHS:
        if path.startswith(excluded):
            return False
    return True


def save_request_log(
    method: str,
    path: str,
    query_params: str,
    status_code: int | None,
    duration_ms: float,
    client_ip: str | None,
    user_agent: str | None,
    error_message: str | None,
) -> None:
    """将请求日志写入 SQLite"""
    try:
        conn = get_db_connection()
        try:
            execute(
                conn,
                """
                INSERT INTO request_logs
                (method, path, query_params, status_code, duration_ms, client_ip, user_agent, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    method,
                    path,
                    query_params,
                    status_code,
                    duration_ms,
                    client_ip,
                    user_agent,
                    error_message,
                ),
            )
        finally:
            conn.close()
    except Exception as e:
        # 日志写入失败不能影响主请求
        print(f"[RequestLog] 写入失败: {e}")


class RequestLoggingMiddleware:
    """纯 ASGI 请求日志中间件，不破坏 SSE 等流式响应."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.time()
        status_code = None
        error_message = None

        async def wrapped_send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status")
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        except Exception as e:
            status_code = 500
            error_message = str(e)
            raise
        finally:
            path = scope.get("path", "")
            if _should_log_path(path):
                duration_ms = (time.time() - start_time) * 1000
                save_request_log(
                    method=scope.get("method", ""),
                    path=path,
                    query_params=str(scope.get("query_string", b""), encoding="utf-8"),
                    status_code=status_code,
                    duration_ms=duration_ms,
                    client_ip=scope.get("client", (None, None))[0] if scope.get("client") else None,
                    user_agent=dict(scope.get("headers", [])).get(b"user-agent", b"").decode("utf-8", errors="ignore"),
                    error_message=error_message,
                )


app.add_middleware(RequestLoggingMiddleware)


@app.on_event("startup")
async def startup_event():
    """启动时初始化数据库，并释放上次卡住的任务"""
    init_db()
    # 服务重启后，把长时间未更新的 running Run 重置为 pending，避免任务永远卡住
    PlanningRunService.release_stuck_runs()
    # 若 RAG 依赖已安装，自动确保知识库已导入（首次启动时建库）
    if RAG_AVAILABLE:
        try:
            ensure_ingested()
        except Exception as e:
            logger.warning(f"[Startup] 知识库自动导入失败: {e}")
    else:
        logger.info("[Startup] RAG 依赖未安装，跳过知识库初始化")


# ============================================================
# Planning Run 执行辅助函数
# ============================================================

def _worker_id() -> str:
    """生成当前 worker 标识（进程 PID + 线程标识）"""
    import os
    import threading
    return f"pid-{os.getpid()}-thread-{threading.current_thread().ident}"


def _build_planning_context(req: PlanRequest, preferences: dict | None) -> dict:
    """从请求构建 Planner 上下文"""
    context = {
        "destination": req.destination,
        "days": req.days,
        "travelers": req.travelers,
        "budget": req.budget,
        "origin": req.origin,
        "style": req.style,
    }
    if preferences:
        context.update(preferences)
    return context


def _step_to_sse_event(step: dict) -> dict | None:
    """把 planning_steps 表记录转成 SSE 事件"""
    tool_to_agent = {
        "get_weather": "weather",
        "search_hotels": "hotel",
        "search_restaurants": "restaurant",
        "search_attractions": "attraction",
        "plan_transport": "transport",
        "risk_check": "risk",
    }
    step_type = step.get("step_type")
    tool_name = step.get("tool_name") or ""
    agent_name = tool_to_agent.get(tool_name, tool_name)
    if step_type == "thought":
        return {"type": "planner_thought", "step": step.get("step_number"), "thought": step.get("content") or ""}
    if step_type == "tool_call":
        return {"type": "planner_tool_call", "step": step.get("step_number"), "tool": tool_name, "tool_input": step.get("tool_input") or {}}
    if step_type == "observation":
        result = {}
        try:
            result = json.loads(step.get("observation_json") or "{}")
        except Exception:
            pass
        return {
            "type": "planner_observation",
            "step": step.get("step_number"),
            "tool": tool_name,
            "agent": agent_name,
            "result": result,
        }
    return None


def _run_planning_worker(run_id: int, user_id: int | None, context: dict) -> None:
    """
    后台 worker：认领并执行一个 PlanningRun。
    执行完成后把状态写回数据库，前端通过 SSE 轮询或 /api/v3/runs 查看进度。
    """
    import time
    run_service = PlanningRunService()
    if not run_service.claim_run_for_execution(run_id, _worker_id()):
        logger.info(f"[Worker] run_id={run_id} 已被其他 worker 认领，跳过")
        return

    start_time = time.time()
    planner = PlannerAgent()
    try:
        result = planner.plan_stream(context, user_id=user_id, run_id=run_id)
        total_duration_ms = int((time.time() - start_time) * 1000)
        result["total_duration_ms"] = total_duration_ms
        result["destination"] = context.get("destination")
        result["days"] = context.get("days")
        result["travelers"] = context.get("travelers")
        result["budget"] = context.get("budget")
        result["origin"] = context.get("origin")
        logger.info(f"[Worker] run_id={run_id} 规划完成，itinerary_id={result.get('itinerary_id')}")
    except Exception as e:
        logger.exception(f"[Worker] run_id={run_id} 规划失败")
        run_service.update_status(run_id, "failed", error_message=str(e))


# ============================================================
# 请求模型
# ============================================================
class PlanRequest(BaseModel):
    """行程规划请求"""
    destination: str = Field(..., min_length=1, description="目的地城市")
    days: int = Field(default=3, ge=1, le=14, description="行程天数")
    travelers: int = Field(default=2, ge=1, le=20, description="出行人数")
    budget: int | None = Field(default=None, description="预算(元)")
    origin: str = Field(default="上海", description="出发城市")
    style: str = Field(default="balanced", description="旅行风格: slow/intensive/family/foodie/photography/balanced")
    # 个性化输入
    interests: str | None = Field(default=None, description="兴趣标签，如摄影/美食/历史/自然")
    special_needs: str | None = Field(default=None, description="特殊需求，如带老人/亲子/高原/宠物")
    season: str | None = Field(default=None, description="出行季节/月份，如夏季/6月")
    pace: str | None = Field(default=None, description="旅行节奏: slow/relaxed/balanced/intensive")
    must_visit: str | None = Field(default=None, description="必去景点，逗号分隔")
    avoid: str | None = Field(default=None, description="不想去的地方或活动，逗号分隔")


class ChecklistRequest(BaseModel):
    """旅行 Checklist 生成请求"""
    destination: str = Field(..., min_length=1, description="目的地城市")
    days: int = Field(default=3, ge=1, le=30, description="行程天数")
    travelers: int = Field(default=2, ge=1, le=20, description="出行人数")
    season: str | None = Field(default=None, description="出行季节/月份")
    special_needs: str | None = Field(default=None, description="特殊需求")
    style: str | None = Field(default=None, description="旅行风格/兴趣")


class LLMConfigRequest(BaseModel):
    """LLM配置请求"""
    id: int | None = None
    name: str = "default"
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model_name: str = "gpt-4o-mini"
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: int = 60
    use_llm: bool = True


class APIConfigRequest(BaseModel):
    """外部API配置请求"""
    id: int | None = None
    config_type: str = Field(..., description="类型: weather/map")
    provider: str = Field(..., description="提供商: openweathermap/qweather/amap/baidu")
    api_key: str
    base_url: str = ""
    extra_params: str = "{}"


class RegisterRequest(BaseModel):
    """用户注册请求"""
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=4)
    email: str | None = None


class LoginRequest(BaseModel):
    """用户登录请求"""
    username: str
    password: str


class UserProfileRequest(BaseModel):
    """用户画像更新请求"""
    display_name: str | None = None
    age_group: str | None = None
    companion_type: str | None = None
    interests: list[str] | None = None
    pace: str | None = None
    budget_range: int | None = None
    dietary_restrictions: list[str] | None = None
    accessibility_needs: str | None = None
    preferred_transport: str | None = None
    home_city: str | None = None
    must_visit_tags: list[str] | None = None
    avoid_tags: list[str] | None = None


# ============================================================
# 页面路由
# ============================================================
@app.get("/")
async def index(request: Request):
    """首页"""
    return templates.TemplateResponse(request, "index.html")


@app.get("/plan")
async def plan_page(request: Request):
    """规划页面"""
    return templates.TemplateResponse(request, "plan.html")


@app.get("/result/{itinerary_id}")
async def result_page(request: Request, itinerary_id: int):
    """结果展示页"""
    return templates.TemplateResponse(request, "result.html", {
        "itinerary_id": itinerary_id,
    })


@app.get("/history")
async def history_page(request: Request):
    """我的行程历史页"""
    return templates.TemplateResponse(request, "history.html")


@app.get("/admin")
async def admin_page(request: Request):
    """管理后台"""
    return templates.TemplateResponse(request, "admin_v3.html")


@app.get("/admin/dashboard")
async def dashboard_page(request: Request):
    """系统观测大盘"""
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/login")
async def login_page(request: Request):
    """登录/注册页"""
    return templates.TemplateResponse(request, "login.html")


# ============================================================
# API路由 - 核心规划
# ============================================================
@app.post("/api/v3/plan")
async def create_plan(req: PlanRequest, request: Request, idempotency_key: str | None = None):
    """
    创建旅行规划 (V3核心API)
    PlannerAgent调度所有子Agent并行执行
    已登录用户会自动合并画像偏好，并在规划完成后更新画像。
    支持 idempotency_key 幂等创建。
    """
    try:
        user = get_current_user(request)
        preferences = None
        user_id = None
        if user:
            profile = get_user_profile(user["id"])
            preferences = merge_profile_with_request(profile, req.model_dump())
            user_id = user["id"]

        context = _build_planning_context(req, preferences)
        run_service = PlanningRunService()
        run_id, is_new = run_service.create_run_idempotent(user_id, context, idempotency_key)

        if not is_new:
            # 幂等命中：如果已有 Run 已完成/失败，直接返回；否则等待执行
            run = run_service.get_run(run_id)
            if run.get("status") == "completed" and run.get("itinerary_id"):
                return {"success": True, "data": {"run_id": run_id, "itinerary_id": run["itinerary_id"]}}

        # 同步执行（当前线程阻塞，适合非流式调用）
        planner = PlannerAgent()
        result = planner.plan(
            destination=req.destination,
            days=req.days,
            travelers=req.travelers,
            budget=req.budget,
            origin=req.origin,
            style=req.style,
            preferences=preferences,
            user_id=user_id,
            run_id=run_id,
        )
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "message": f"规划失败: {str(e)}"}


@app.post("/api/v3/plan/stream")
async def create_plan_stream(
    req: PlanRequest,
    request: Request,
    run_id: int | None = None,
    idempotency_key: str | None = None,
):
    """
    流式创建旅行规划 (SSE)

    - 支持 run_id 参数：前端断线重连时可重新订阅已有 Run。
    - 支持 idempotency_key：重复请求幂等，不重复创建 Run。
    - Run 创建后由后台 worker 认领执行；SSE 只负责读取 planning_steps 并实时推送。
    - 服务重启后，卡住的 running Run 会被重置为 pending，重新连接后可继续。
    """
    user = get_current_user(request)
    profile = get_user_profile(user["id"]) if user else None
    preferences = merge_profile_with_request(profile, req.model_dump()) if user else None
    user_id = user["id"] if user else None

    context = _build_planning_context(req, preferences)
    run_service = PlanningRunService()

    if run_id:
        # 订阅已有 Run，校验存在性
        run = run_service.get_run(run_id)
        if not run:
            return {"success": False, "message": "Run 不存在"}
    else:
        run_id, is_new = run_service.create_run_idempotent(user_id, context, idempotency_key)

    run = run_service.get_run(run_id)

    # 如果 Run 还在 pending/retrying，启动后台 worker 认领执行
    if run and run.get("status") in ("pending", "retrying"):
        asyncio.create_task(asyncio.to_thread(_run_planning_worker, run_id, user_id, context))

    async def event_generator():
        import asyncio

        def sse_payload(event: dict) -> str:
            return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        yield sse_payload({"type": "start", "run_id": run_id, "message": "开始规划..."})

        last_step_id = 0
        conn = get_db_connection()
        try:
            # 先推送已经执行过的历史步骤（支持断线重连）
            history_steps = query_all(
                conn,
                "SELECT * FROM planning_steps WHERE run_id = ? ORDER BY step_number, id",
                (run_id,),
            )
            for step in history_steps:
                event = _step_to_sse_event(step)
                if event:
                    yield sse_payload(event)
                last_step_id = step["id"]
        finally:
            conn.close()

        # 轮询新步骤，直到 Run 结束
        while True:
            await asyncio.sleep(0.5)
            run = run_service.get_run(run_id)
            if not run:
                yield sse_payload({"type": "error", "message": "Run 不存在"})
                yield "data: [DONE]\n\n"
                break

            conn = get_db_connection()
            try:
                new_steps = query_all(
                    conn,
                    "SELECT * FROM planning_steps WHERE run_id = ? AND id > ? ORDER BY id",
                    (run_id, last_step_id),
                )
            finally:
                conn.close()

            for step in new_steps:
                event = _step_to_sse_event(step)
                if event:
                    yield sse_payload(event)
                last_step_id = step["id"]

            status = run.get("status")
            # 在 Run 结束前再推送一次剩余步骤，避免 worker 执行过快导致 SSE 漏掉中间步骤
            if status in ("completed", "failed"):
                conn = get_db_connection()
                try:
                    remaining = query_all(
                        conn,
                        "SELECT * FROM planning_steps WHERE run_id = ? AND id > ? ORDER BY id",
                        (run_id, last_step_id),
                    )
                    for step in remaining:
                        event = _step_to_sse_event(step)
                        if event:
                            yield sse_payload(event)
                        last_step_id = step["id"]
                finally:
                    conn.close()

            if status == "completed":
                itinerary_id = run.get("itinerary_id")
                result = {"run_id": run_id, "itinerary_id": itinerary_id}
                if itinerary_id:
                    result["redirect_url"] = f"/result/{itinerary_id}"
                yield sse_payload({"type": "itinerary_generating"})
                yield sse_payload({"type": "complete", "data": result})
                yield "data: [DONE]\n\n"
                break
            if status == "failed":
                yield sse_payload({"type": "error", "message": run.get("error_message") or "规划失败"})
                yield "data: [DONE]\n\n"
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get("/api/v3/plan/{itinerary_id}")
async def get_plan(itinerary_id: int):
    """获取行程详情（含完整行程 JSON 与 Agent 执行记录）"""
    conn = get_db_connection()
    try:
        itinerary_row = query_one(conn, "SELECT * FROM itineraries WHERE id = ?", (itinerary_id,))
        if not itinerary_row:
            return {"success": False, "message": "行程不存在"}

        logs = query_all(conn,
            "SELECT * FROM agent_logs WHERE itinerary_id = ? ORDER BY id", (itinerary_id,))

        # 解析保存的完整行程 JSON
        itinerary_json = itinerary_row.get("itinerary_json") or "{}"
        try:
            plan = json.loads(itinerary_json)
        except json.JSONDecodeError:
            plan = {}

        # 解析 Planner 思考链
        planning_trace_raw = itinerary_row.get("planning_trace") or "[]"
        try:
            planning_trace = json.loads(planning_trace_raw)
        except json.JSONDecodeError:
            planning_trace = []

        # 从 agent_logs 重建 agent_results 与 risk（用于结果页可视化）
        agent_results = {}
        risk_result = {"data": {"warnings": []}, "status": "completed"}
        for log in logs:
            agent_type = log.get("agent_type", "")
            key = f"{agent_type}_result"
            try:
                output_data = json.loads(log.get("output_result") or "{}")
            except Exception:
                output_data = {"raw": log.get("output_result", "")}
            result_obj = {
                "agent_type": agent_type,
                "agent_name": log.get("agent_name", ""),
                "status": log.get("status", "completed"),
                "duration_ms": log.get("duration_ms", 0),
                "error": log.get("error_message", ""),
                "data": output_data,
                "reasoning": "",
            }
            if agent_type == "risk":
                risk_result = result_obj
            else:
                agent_results[key] = result_obj

        return {
            "success": True,
            "data": {
                "itinerary_id": itinerary_row["id"],
                "run_id": (query_one(
                    conn,
                    "SELECT id FROM planning_runs WHERE itinerary_id = ? ORDER BY id DESC LIMIT 1",
                    (itinerary_id,),
                ) or {}).get("id"),
                "destination": itinerary_row["destination"],
                "origin": itinerary_row["origin"],
                "days": (plan.get("days") or [{}]).__len__(),
                "travelers": itinerary_row.get("traveler_count", 2),
                "budget": itinerary_row.get("budget"),
                "llm_used": itinerary_row.get("llm_used", False),
                "total_duration_ms": 0,
                "itinerary": itinerary_row,
                "plan": plan,
                "planning_trace": planning_trace,
                "agent_results": agent_results,
                "risk": risk_result,
            },
        }
    finally:
        conn.close()


@app.get("/api/v3/runs/{run_id}")
async def get_planning_run(run_id: int):
    """获取 PlanningRun 详情与所有步骤"""
    run = PlanningRunService.get_run(run_id)
    if not run:
        return {"success": False, "message": "Run 不存在"}
    steps = PlanningRunService.get_steps(run_id)
    return {"success": True, "data": {"run": run, "steps": steps}}


@app.post("/api/v3/runs/{run_id}/retry")
async def retry_planning_run(run_id: int):
    """
    重试失败的 PlanningRun（断点续跑）。
    - 恢复之前已完成的 observation 结果，跳过成功步骤。
    - 只重新执行失败的步骤及其依赖。
    - 成功后把新的 itinerary_id 写回原 Run。
    """
    run_service = PlanningRunService()
    info = run_service.prepare_retry(run_id)
    if not info:
        return {"success": False, "message": "Run 不存在"}

    # 恢复已完成的中间结果，用于断点续跑
    initial_results = info.get("initial_results", {})

    try:
        planner = PlannerAgent()
        params = info["input_params"]
        context = {
            "destination": params.get("destination", ""),
            "days": params.get("days", 3),
            "travelers": params.get("travelers", 2),
            "budget": params.get("budget"),
            "origin": params.get("origin", "上海"),
            "style": params.get("style", "balanced"),
        }
        # 同步执行重试（也会实时写入 planning_steps）
        result = planner.plan_stream(
            context,
            user_id=info.get("user_id"),
            run_id=run_id,
            initial_results=initial_results,
        )
        return {"success": True, "data": result}
    except Exception as e:
        run_service.update_status(run_id, "failed", error_message=str(e))
        return {"success": False, "message": f"重试失败: {str(e)}"}


@app.get("/api/v3/plans")
async def list_plans(limit: int = 20):
    """获取行程列表"""
    conn = get_db_connection()
    try:
        plans = query_all(conn,
            "SELECT * FROM itineraries ORDER BY created_at DESC LIMIT ?", (limit,))
        return {"success": True, "data": plans, "total": len(plans)}
    finally:
        conn.close()


# ============================================================
# API路由 - 认证与用户画像
# ============================================================
@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    """用户注册"""
    if get_user_by_username(req.username):
        return {"success": False, "message": "用户名已存在"}
    conn = get_db_connection()
    try:
        user_id = execute(conn, """
            INSERT INTO users (username, email, password_hash)
            VALUES (?, ?, ?)
        """, (req.username, req.email or "", get_password_hash(req.password)))
        execute(conn, """
            INSERT INTO user_profiles (user_id) VALUES (?)
        """, (user_id,))
        return {"success": True, "data": {"user_id": user_id}, "message": "注册成功"}
    except Exception as e:
        return {"success": False, "message": f"注册失败: {str(e)}"}
    finally:
        conn.close()


@app.post("/api/auth/login")
async def login(req: LoginRequest, request: Request):
    """用户登录，写入 Session"""
    user = get_user_by_username(req.username)
    if not user or not verify_password(req.password, user.get("password_hash")):
        return {"success": False, "message": "用户名或密码错误"}
    request.session["user_id"] = user["id"]
    return {"success": True, "data": {"user_id": user["id"], "username": user["username"]}}


@app.post("/api/auth/logout")
async def logout(request: Request):
    """退出登录"""
    request.session.pop("user_id", None)
    return {"success": True, "message": "已退出登录"}


@app.get("/api/auth/me")
async def me(request: Request):
    """获取当前登录用户信息及画像"""
    user = get_current_user(request)
    if not user:
        return {"success": False, "message": "未登录"}
    profile = get_user_profile(user["id"])
    return {
        "success": True,
        "data": {
            "user_id": user["id"],
            "username": user["username"],
            "email": user.get("email"),
            "profile": profile_to_public(profile),
        },
    }


@app.get("/api/users/me/profile")
async def get_my_profile(request: Request):
    """获取我的画像"""
    user = get_current_user(request)
    if not user:
        return {"success": False, "message": "未登录"}
    profile = get_user_profile(user["id"])
    return {"success": True, "data": profile_to_public(profile)}


@app.put("/api/users/me/profile")
async def update_my_profile(req: UserProfileRequest, request: Request):
    """更新我的画像（手动填写）"""
    user = get_current_user(request)
    if not user:
        return {"success": False, "message": "未登录"}
    conn = get_db_connection()
    try:
        execute(conn, """
            UPDATE user_profiles SET
                display_name = ?,
                age_group = ?,
                companion_type = ?,
                interests = ?,
                pace = ?,
                budget_range = ?,
                dietary_restrictions = ?,
                accessibility_needs = ?,
                preferred_transport = ?,
                home_city = ?,
                must_visit_tags = ?,
                avoid_tags = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (
            req.display_name,
            req.age_group,
            req.companion_type,
            serialize_json_field(req.interests),
            req.pace,
            req.budget_range,
            serialize_json_field(req.dietary_restrictions),
            req.accessibility_needs,
            req.preferred_transport,
            req.home_city,
            serialize_json_field(req.must_visit_tags),
            serialize_json_field(req.avoid_tags),
            user["id"],
        ))
        return {"success": True, "message": "画像已更新"}
    except Exception as e:
        return {"success": False, "message": f"更新失败: {str(e)}"}
    finally:
        conn.close()


@app.get("/api/users/me/plans")
async def get_my_plans(request: Request, limit: int = 50):
    """获取当前登录用户的规划历史"""
    user = get_current_user(request)
    if not user:
        return {"success": False, "message": "未登录"}
    conn = get_db_connection()
    try:
        plans = query_all(
            conn,
            """SELECT id, title, destination, origin, start_date, end_date,
                      traveler_count, budget, travel_style, status, llm_used,
                      created_at, updated_at
               FROM itineraries
               WHERE user_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (user["id"], limit),
        )
        return {"success": True, "data": plans, "total": len(plans)}
    finally:
        conn.close()


# ============================================================
# API路由 - 配置管理
# ============================================================
@app.get("/api/admin/configs")
async def get_all_configs():
    """获取所有API配置（分类列表，含启用状态）"""
    return {
        "success": True,
        "data": {
            "llm": IntegrationConfig.get_llm_configs(),
            "weather": IntegrationConfig.get_all_api_configs("weather"),
            "map": IntegrationConfig.get_all_api_configs("map"),
        }
    }


@app.get("/api/admin/configs/llm")
async def list_llm_configs():
    """LLM 配置列表"""
    return {"success": True, "data": IntegrationConfig.get_llm_configs()}


@app.get("/api/admin/configs/weather")
async def list_weather_configs():
    """天气 API 配置列表"""
    return {"success": True, "data": IntegrationConfig.get_all_api_configs("weather")}


@app.get("/api/admin/configs/map")
async def list_map_configs():
    """地图 API 配置列表"""
    return {"success": True, "data": IntegrationConfig.get_all_api_configs("map")}


@app.get("/api/admin/configs/llm/{config_id}")
async def get_llm_config_detail(config_id: int):
    """获取单个 LLM 配置详情（含完整 API Key，用于编辑）"""
    conn = get_db_connection()
    try:
        config = query_one(conn, "SELECT * FROM llm_configs WHERE id = ?", (config_id,))
        if not config:
            return {"success": False, "message": "配置不存在"}
        return {"success": True, "data": config}
    finally:
        conn.close()


@app.get("/api/admin/configs/{config_type}/{config_id}")
async def get_api_config_detail(config_type: str, config_id: int):
    """获取单个天气/地图配置详情（含完整 API Key，用于编辑）"""
    if config_type not in {"weather", "map"}:
        return {"success": False, "message": "无效的配置类型"}
    conn = get_db_connection()
    try:
        config = query_one(conn, """
            SELECT * FROM api_configs WHERE id = ? AND config_type = ?
        """, (config_id, config_type))
        if not config:
            return {"success": False, "message": "配置不存在"}
        if config.get("extra_params"):
            try:
                config["extra_params"] = json.loads(config["extra_params"])
            except json.JSONDecodeError:
                config["extra_params"] = {}
        return {"success": True, "data": config}
    finally:
        conn.close()


@app.post("/api/admin/configs/llm/{config_id}/activate")
async def activate_llm_config(config_id: int):
    """启用指定 LLM 配置"""
    conn = get_db_connection()
    try:
        execute(conn, "UPDATE llm_configs SET is_active = 0")
        execute(conn, "UPDATE llm_configs SET is_active = 1 WHERE id = ?", (config_id,))
        return {"success": True, "message": "已启用"}
    finally:
        conn.close()


@app.post("/api/admin/configs/{config_type}/{config_id}/activate")
async def activate_api_config(config_type: str, config_id: int):
    """启用指定天气/地图配置"""
    if config_type not in {"weather", "map"}:
        return {"success": False, "message": "无效的配置类型"}
    conn = get_db_connection()
    try:
        execute(conn, "UPDATE api_configs SET is_active = 0 WHERE config_type = ?", (config_type,))
        execute(conn, """
            UPDATE api_configs SET is_active = 1 WHERE id = ? AND config_type = ?
        """, (config_id, config_type))
        return {"success": True, "message": "已启用"}
    finally:
        conn.close()


@app.delete("/api/admin/configs/llm/{config_id}")
async def delete_llm_config_v2(config_id: int):
    """删除 LLM 配置"""
    conn = get_db_connection()
    try:
        execute(conn, "DELETE FROM llm_configs WHERE id = ?", (config_id,))
        return {"success": True, "message": "已删除"}
    finally:
        conn.close()


@app.delete("/api/admin/configs/{config_type}/{config_id}")
async def delete_api_config(config_type: str, config_id: int):
    """删除天气/地图配置"""
    if config_type not in {"weather", "map"}:
        return {"success": False, "message": "无效的配置类型"}
    conn = get_db_connection()
    try:
        execute(conn, "DELETE FROM api_configs WHERE id = ? AND config_type = ?", (config_id, config_type))
        return {"success": True, "message": "已删除"}
    finally:
        conn.close()


@app.post("/api/admin/llm/config")
async def save_llm_config(req: LLMConfigRequest):
    """保存或更新 LLM 配置"""
    conn = get_db_connection()
    try:
        if req.id:
            # 更新现有配置，不改变激活状态
            execute(conn, """
                UPDATE llm_configs SET
                    name = ?, api_key = ?, base_url = ?, model_name = ?,
                    temperature = ?, max_tokens = ?, timeout = ?, use_llm = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (req.name, req.api_key, req.base_url, req.model_name,
                  req.temperature, req.max_tokens, req.timeout, req.use_llm, req.id))
            return {"success": True, "data": {"id": req.id}, "message": "LLM配置更新成功"}

        # 新增配置：取消其他激活配置，插入并激活
        execute(conn, "UPDATE llm_configs SET is_active = 0")
        config_id = execute(conn, """
            INSERT INTO llm_configs (name, api_key, base_url, model_name, temperature,
                max_tokens, timeout, is_active, use_llm)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
        """, (req.name, req.api_key, req.base_url, req.model_name,
              req.temperature, req.max_tokens, req.timeout, req.use_llm))
        return {"success": True, "data": {"id": config_id}, "message": "LLM配置保存成功"}
    except Exception as e:
        return {"success": False, "message": f"保存失败: {str(e)}"}
    finally:
        conn.close()


@app.delete("/api/admin/llm/config/{config_id}")
async def delete_llm_config(config_id: int):
    """删除LLM配置"""
    conn = get_db_connection()
    try:
        execute(conn, "DELETE FROM llm_configs WHERE id = ?", (config_id,))
        return {"success": True, "message": "已删除"}
    finally:
        conn.close()


@app.post("/api/admin/api/config")
async def save_api_config(req: APIConfigRequest):
    """保存或更新天气/地图API配置"""
    success = IntegrationConfig.save_api_config(
        req.config_type, req.provider, req.api_key, req.base_url, req.extra_params, req.id
    )
    if success:
        action = "更新" if req.id else "保存"
        return {"success": True, "message": f"{req.config_type}配置{action}成功"}
    return {"success": False, "message": "保存失败"}


@app.post("/api/admin/test/{config_type}")
async def test_connection_api(config_type: str):
    """测试API连接"""
    result = IntegrationConfig.test_connection(config_type)
    return result


@app.get("/api/admin/status")
async def get_system_status():
    """获取系统状态"""
    llm_config = IntegrationConfig.get_llm_config()
    weather_config = IntegrationConfig.get_weather_config()
    map_config = IntegrationConfig.get_map_config()

    conn = get_db_connection()
    try:
        stats = {
            "llm": {"configured": llm_config is not None, "model": llm_config.get("model_name") if llm_config else None},
            "weather": {"configured": weather_config is not None, "provider": weather_config.get("provider") if weather_config else None},
            "map": {"configured": map_config is not None, "provider": map_config.get("provider") if map_config else None},
            "database": {
                "hotels": query_one(conn, "SELECT COUNT(*) as c FROM poi_data WHERE poi_type='hotel'")["c"],
                "restaurants": query_one(conn, "SELECT COUNT(*) as c FROM poi_data WHERE poi_type='restaurant'")["c"],
                "attractions": query_one(conn, "SELECT COUNT(*) as c FROM poi_data WHERE poi_type='attraction'")["c"],
                "xiaohongshu": query_one(conn, "SELECT COUNT(*) as c FROM xiaohongshu_notes")["c"],
                "itineraries": query_one(conn, "SELECT COUNT(*) as c FROM itineraries")["c"],
            }
        }
        return {"success": True, "data": stats}
    finally:
        conn.close()


@app.get("/api/admin/metrics")
async def get_metrics():
    """
    获取系统可观测性指标

    - 缓存命中率、命中次数、平均延迟
    - Agent 执行成功率
    - LLM Token 消耗统计（总量、按 Agent 类型、按天趋势）
    - API 请求统计
    """
    from app.integrations.llm_client import PromptCache

    cache_stats = PromptCache().stats()
    total_entries = cache_stats["total_entries"]
    total_hits = cache_stats["total_hits"]
    total_requests = total_hits + total_entries
    hit_rate = total_hits / total_requests if total_requests > 0 else 0.0
    avg_latency = cache_stats["avg_latency_ms"] or 0

    conn = get_db_connection()
    try:
        total_logs = query_one(conn, "SELECT COUNT(*) AS c FROM agent_logs")["c"]
        completed_logs = query_one(
            conn, "SELECT COUNT(*) AS c FROM agent_logs WHERE status = 'completed'"
        )["c"]
        success_rate = completed_logs / total_logs if total_logs > 0 else 0.0

        token_row = query_one(
            conn,
            "SELECT COALESCE(SUM(prompt_tokens), 0) AS p, "
            "COALESCE(SUM(completion_tokens), 0) AS c FROM agent_logs",
        )

        llm_call_count = query_one(
            conn,
            "SELECT COUNT(*) AS c FROM agent_logs WHERE prompt_tokens > 0 OR completion_tokens > 0",
        )["c"]

        # 按 Agent 类型统计 token 消耗和调用次数
        by_type = query_all(
            conn,
            """
            SELECT
                agent_type,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COUNT(*) AS call_count
            FROM agent_logs
            GROUP BY agent_type
            ORDER BY call_count DESC
            """,
        )

        # 近 30 天 token 消耗趋势
        trends = query_all(
            conn,
            """
            SELECT
                DATE(created_at) AS day,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COUNT(*) AS call_count
            FROM agent_logs
            WHERE created_at >= DATE('now', '-30 days')
            GROUP BY DATE(created_at)
            ORDER BY day ASC
            """,
        )

        # API 请求统计
        request_stats = query_one(
            conn,
            """
            SELECT
                COUNT(*) AS total_requests,
                COALESCE(AVG(duration_ms), 0) AS avg_duration_ms,
                SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS error_requests
            FROM request_logs
            """,
        )

        return {
            "success": True,
            "data": {
                "cache": {
                    "total_entries": total_entries,
                    "total_hits": total_hits,
                    "expired_entries": cache_stats["expired_entries"],
                    "hit_rate": round(hit_rate, 4),
                    "avg_latency_ms": round(avg_latency, 2) if avg_latency else 0,
                },
                "llm": {
                    "total_prompt_tokens": token_row["p"],
                    "total_completion_tokens": token_row["c"],
                    "total_tokens": token_row["p"] + token_row["c"],
                    "call_count": llm_call_count,
                },
                "agents": {
                    "total_logs": total_logs,
                    "completed_logs": completed_logs,
                    "success_rate": round(success_rate, 4),
                    "by_type": by_type,
                },
                "trends": trends,
                "requests": {
                    "total_requests": request_stats["total_requests"],
                    "avg_duration_ms": round(request_stats["avg_duration_ms"] or 0, 2),
                    "error_requests": request_stats["error_requests"],
                },
            },
        }
    finally:
        conn.close()


# ============================================================
# API路由 - Dashboard 可观测性（分级展示）
# ============================================================

def _dashboard_range_clause(range_str: str) -> tuple[str, tuple]:
    """把前端 range 参数转成 SQLite 时间窗口片段和参数"""
    if range_str not in {"24h", "7d", "30d"}:
        range_str = "24h"
    window = {"24h": "-1 day", "7d": "-7 days", "30d": "-30 days"}[range_str]
    return "created_at >= DATETIME('now', ?)", (window,)


@app.get("/api/admin/dashboard/summary")
async def dashboard_summary(range: str = "24h"):
    """L1 全局概览指标"""
    range_sql, range_param = _dashboard_range_clause(range)
    from app.integrations.llm_client import PromptCache

    cache_stats = PromptCache().stats()
    total_entries = cache_stats["total_entries"]
    total_hits = cache_stats["total_hits"]
    total_cache_requests = total_hits + total_entries
    hit_rate = total_hits / total_cache_requests if total_cache_requests > 0 else 0.0
    avg_latency = cache_stats["avg_latency_ms"] or 0

    conn = get_db_connection()
    try:
        request_stats = query_one(
            conn,
            f"""
            SELECT
                COUNT(*) AS total_requests,
                COALESCE(AVG(duration_ms), 0) AS avg_duration_ms,
                SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS error_requests
            FROM request_logs
            WHERE {range_sql}
            """,
            range_param,
        )

        durations = query_all(
            conn,
            f"SELECT duration_ms FROM request_logs WHERE {range_sql} ORDER BY duration_ms ASC",
            range_param,
        )
        p95_duration_ms = 0
        if durations:
            p95_idx = int(len(durations) * 0.95)
            if p95_idx >= len(durations):
                p95_idx = len(durations) - 1
            p95_duration_ms = durations[p95_idx]["duration_ms"]

        total_logs = query_one(
            conn, f"SELECT COUNT(*) AS c FROM agent_logs WHERE {range_sql}", range_param
        )["c"]
        completed_logs = query_one(
            conn,
            f"SELECT COUNT(*) AS c FROM agent_logs WHERE status = 'completed' AND {range_sql}",
            range_param,
        )["c"]
        success_rate = completed_logs / total_logs if total_logs > 0 else 0.0

        token_row = query_one(
            conn,
            f"""
            SELECT
                COALESCE(SUM(prompt_tokens), 0) AS p,
                COALESCE(SUM(completion_tokens), 0) AS c
            FROM agent_logs
            WHERE {range_sql}
            """,
            range_param,
        )

        estimated_row = query_one(
            conn,
            f"""
            SELECT
                COALESCE(SUM(estimated_prompt_tokens), 0) AS p,
                COALESCE(SUM(estimated_completion_tokens), 0) AS c
            FROM agent_logs
            WHERE {range_sql}
            """,
            range_param,
        )

        llm_call_count = query_one(
            conn,
            f"""
            SELECT COUNT(*) AS c FROM agent_logs
            WHERE (prompt_tokens > 0 OR completion_tokens > 0 OR estimated_prompt_tokens > 0) AND {range_sql}
            """,
            range_param,
        )["c"]

        slow_requests = query_one(
            conn,
            f"SELECT COUNT(*) AS c FROM request_logs WHERE duration_ms > 1000 AND {range_sql}",
            range_param,
        )["c"]

        itineraries = query_one(conn, "SELECT COUNT(*) AS c FROM itineraries")["c"]

        actual_total = token_row["p"] + token_row["c"]
        estimated_total = estimated_row["p"] + estimated_row["c"]
        estimate_accuracy = max(
            0.0,
            1 - abs(estimated_total - actual_total) / max(actual_total, 1)
            if estimated_total > 0 else 0.0
        )

        return {
            "success": True,
            "data": {
                "requests": {
                    "total_requests": request_stats["total_requests"],
                    "avg_duration_ms": round(request_stats["avg_duration_ms"] or 0, 2),
                    "error_requests": request_stats["error_requests"],
                    "error_rate": (
                        request_stats["error_requests"] / request_stats["total_requests"]
                        if request_stats["total_requests"] else 0.0
                    ),
                    "p95_duration_ms": round(p95_duration_ms, 2),
                    "slow_requests": slow_requests,
                },
                "cache": {
                    "total_entries": total_entries,
                    "total_hits": total_hits,
                    "hit_rate": round(hit_rate, 4),
                    "avg_latency_ms": round(avg_latency, 2) if avg_latency else 0,
                },
                "llm": {
                    "total_prompt_tokens": token_row["p"],
                    "total_completion_tokens": token_row["c"],
                    "total_tokens": actual_total,
                    "estimated_prompt_tokens": estimated_row["p"],
                    "estimated_completion_tokens": estimated_row["c"],
                    "estimated_total_tokens": estimated_total,
                    "estimate_accuracy": round(estimate_accuracy, 4),
                    "call_count": llm_call_count,
                },
                "agents": {
                    "total_logs": total_logs,
                    "completed_logs": completed_logs,
                    "success_rate": round(success_rate, 4),
                },
                "itineraries": itineraries,
            },
        }
    finally:
        conn.close()


@app.get("/api/admin/dashboard/trends")
async def dashboard_trends(range: str = "7d"):
    """L2 趋势分析：Token 趋势 + 请求趋势"""
    range_sql, range_param = _dashboard_range_clause(range)

    if range == "24h":
        time_group = "STRFTIME('%Y-%m-%d %H:00', created_at)"
        time_label = "label"
    else:
        time_group = "DATE(created_at)"
        time_label = "label"

    conn = get_db_connection()
    try:
        token_trends = query_all(
            conn,
            f"""
            SELECT
                {time_group} AS {time_label},
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COUNT(*) AS call_count
            FROM agent_logs
            WHERE {range_sql}
            GROUP BY {time_label}
            ORDER BY {time_label} ASC
            """,
            range_param,
        )

        request_trends = query_all(
            conn,
            f"""
            SELECT
                {time_group} AS {time_label},
                COUNT(*) AS total_requests,
                COALESCE(AVG(duration_ms), 0) AS avg_duration_ms,
                SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS error_requests
            FROM request_logs
            WHERE {range_sql}
            GROUP BY {time_label}
            ORDER BY {time_label} ASC
            """,
            range_param,
        )

        return {
            "success": True,
            "data": {
                "token_trends": token_trends,
                "request_trends": request_trends,
            },
        }
    finally:
        conn.close()


@app.get("/api/admin/dashboard/agents")
async def dashboard_agents(range: str = "24h"):
    """L3 细粒度分布：Agent 调用占比与成功率明细"""
    range_sql, range_param = _dashboard_range_clause(range)
    conn = get_db_connection()
    try:
        by_type = query_all(
            conn,
            f"""
            SELECT
                agent_type,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COUNT(*) AS call_count,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS success_count
            FROM agent_logs
            WHERE {range_sql}
            GROUP BY agent_type
            ORDER BY call_count DESC
            """,
            range_param,
        )
        return {"success": True, "data": {"by_type": by_type}}
    finally:
        conn.close()


@app.get("/api/admin/dashboard/requests")
async def dashboard_requests(range: str = "24h", limit: int = 50, offset: int = 0):
    """L4 明细：请求日志"""
    range_sql, range_param = _dashboard_range_clause(range)
    conn = get_db_connection()
    try:
        logs = query_all(
            conn,
            f"""
            SELECT * FROM request_logs
            WHERE {range_sql}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (*range_param, limit, offset),
        )
        total = query_one(
            conn,
            f"SELECT COUNT(*) AS c FROM request_logs WHERE {range_sql}",
            range_param,
        )["c"]
        return {"success": True, "data": logs, "total": total, "limit": limit, "offset": offset}
    finally:
        conn.close()


@app.get("/api/admin/dashboard/agent-logs")
async def dashboard_agent_logs(range: str = "24h", limit: int = 50, offset: int = 0):
    """L4 明细：Agent 调用日志"""
    range_sql, range_param = _dashboard_range_clause(range)
    conn = get_db_connection()
    try:
        logs = query_all(
            conn,
            f"""
            SELECT * FROM agent_logs
            WHERE {range_sql}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (*range_param, limit, offset),
        )
        total = query_one(
            conn,
            f"SELECT COUNT(*) AS c FROM agent_logs WHERE {range_sql}",
            range_param,
        )["c"]
        return {"success": True, "data": logs, "total": total, "limit": limit, "offset": offset}
    finally:
        conn.close()


@app.post("/api/admin/knowledge/ingest")
async def ingest_knowledge(force: bool = False):
    """手动触发旅行知识库向量化（首次启动或文档更新后调用）."""
    if not RAG_AVAILABLE:
        return {"success": False, "message": "RAG 依赖未安装，请先执行 pip install -r requirements.txt"}
    try:
        count = ensure_ingested() if not force else ingest_documents(force=True)
        return {"success": True, "message": f"知识库导入完成，共 {count} 篇文档"}
    except Exception as e:
        logger.exception("[Admin] 知识库导入失败")
        return {"success": False, "message": f"导入失败: {str(e)}"}


@app.get("/api/admin/knowledge/status")
async def knowledge_status():
    """查看旅行知识库状态."""
    if not RAG_AVAILABLE:
        return {"success": True, "enabled": False, "count": 0, "message": "RAG 依赖未安装"}
    try:
        from app.knowledge.vector_store import KnowledgeVectorStore

        store = KnowledgeVectorStore()
        count = store.count()
        return {"success": True, "enabled": True, "count": count}
    except Exception as e:
        logger.exception("[Admin] 获取知识库状态失败")
        return {"success": False, "message": f"获取状态失败: {str(e)}"}


@app.post("/api/v3/checklist")
async def generate_checklist(req: ChecklistRequest):
    """基于 RAG 生成旅行 Checklist."""
    if not RAG_AVAILABLE:
        return {"success": False, "message": "RAG 依赖未安装"}
    try:
        generator = ChecklistGenerator()
        result = generator.generate(
            destination=req.destination,
            days=req.days,
            travelers=req.travelers,
            season=req.season,
            special_needs=req.special_needs,
            style=req.style,
        )
        return {"success": True, "data": result}
    except Exception as e:
        logger.exception("[API] 生成 Checklist 失败")
        return {"success": False, "message": f"生成失败: {str(e)}"}


@app.get("/api/v3/knowledge/tips")
async def get_travel_tips(
    destination: str,
    season: str | None = None,
    special_needs: str | None = None,
    style: str | None = None,
    n_results: int = 5,
):
    """语义检索旅行知识 Tips."""
    if not RAG_AVAILABLE:
        return {"success": False, "message": "RAG 依赖未安装"}
    try:
        query_parts = [f"{destination}旅行"]
        if season:
            query_parts.append(f"{season}出行")
        if special_needs:
            query_parts.append(special_needs)
        if style:
            query_parts.append(f"{style}旅行")

        retriever = TipsRetriever()
        results = retriever.retrieve(
            query=" ".join(query_parts),
            n_results=n_results,
        )
        # 扁平化返回，便于前端直接渲染
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        tips = [
            {"text": doc, "metadata": meta}
            for doc, meta in zip(documents, metadatas)
            if doc
        ]
        return {"success": True, "data": {"query": " ".join(query_parts), "tips": tips}}
    except Exception as e:
        logger.exception("[API] 检索旅行知识失败")
        return {"success": False, "message": f"检索失败: {str(e)}"}
async def get_request_logs(limit: int = 100, offset: int = 0, path: str | None = None):
    """
    获取 API 请求日志

    Args:
        limit: 返回条数
        offset: 分页偏移
        path: 按请求路径过滤（可选）
    """
    conn = get_db_connection()
    try:
        if path:
            logs = query_all(
                conn,
                """
                SELECT * FROM request_logs
                WHERE path = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (path, limit, offset),
            )
            total = query_one(
                conn,
                "SELECT COUNT(*) AS c FROM request_logs WHERE path = ?",
                (path,),
            )["c"]
        else:
            logs = query_all(
                conn,
                """
                SELECT * FROM request_logs
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
            total = query_one(conn, "SELECT COUNT(*) AS c FROM request_logs")["c"]

        return {"success": True, "data": logs, "total": total, "limit": limit, "offset": offset}
    finally:
        conn.close()


# ============================================================
# API路由 - 数据查询
# ============================================================
@app.get("/api/v3/poi/{city}")
async def get_city_poi(city: str, poi_type: str | None = None):
    """获取城市POI数据"""
    conn = get_db_connection()
    try:
        if poi_type:
            pois = query_all(conn,
                "SELECT * FROM poi_data WHERE city = ? AND poi_type = ? ORDER BY rating DESC",
                (city, poi_type))
        else:
            pois = query_all(conn,
                "SELECT * FROM poi_data WHERE city = ? ORDER BY poi_type, rating DESC",
                (city,))
        return {"success": True, "data": pois, "total": len(pois)}
    finally:
        conn.close()


@app.get("/api/v3/xiaohongshu/{city}")
async def get_city_xiaohongshu(city: str, suspicious_only: bool = False):
    """获取城市小红书数据"""
    conn = get_db_connection()
    try:
        if suspicious_only:
            notes = query_all(conn,
                "SELECT * FROM xiaohongshu_notes WHERE city = ? AND is_suspicious = 1",
                (city,))
        else:
            notes = query_all(conn,
                "SELECT * FROM xiaohongshu_notes WHERE city = ? ORDER BY credibility_score DESC",
                (city,))
        return {"success": True, "data": notes, "total": len(notes)}
    finally:
        conn.close()


@app.get("/api/v3/weather/{city}")
async def get_weather(city: str, days: int = 3):
    """获取城市天气"""
    from app.integrations.weather import WeatherClient
    client = WeatherClient()
    current = client.get_current_weather(city)
    forecast = client.get_forecast(city, days)
    advice = client.get_clothing_advice(current.get("temp", 20), current.get("description", ""))
    return {
        "success": True,
        "data": {"current": current, "forecast": forecast, "clothing_advice": advice}
    }


@app.get("/api/ping")
async def ping():
    """健康检查"""
    return {"ok": True, "version": "3.0.0", "features": ["planner-agent", "llm", "weather", "map", "db-mock", "mcp-server", "request-logs", "metrics-viz"]}


# 兼容旧版入口
@app.get("/api/plan")
async def plan_redirect():
    return RedirectResponse(url="/api/v3/plan", status_code=308)
