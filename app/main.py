"""
智能旅游多Agent规划系统 V3 - FastAPI主入口
PlannerAgent + 子Agent架构
集成LLM/天气API/地图API，数据库存储Mock数据
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.agents.v3.planner import PlannerAgent
from app.db.database import execute, get_db_connection, init_db, query_all, query_one
from app.integrations.config_manager import IntegrationConfig
from app.knowledge import ensure_ingested

# ============================================================
# 初始化
# ============================================================
app = FastAPI(
    title="智能旅游多Agent规划系统 V3",
    description="PlannerAgent + 子Agent架构 | LLM/天气/地图API集成 | 数据库Mock",
    version="3.0.0",
)

# 静态文件和模板
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

@app.on_event("startup")
async def startup_event():
    """启动时初始化数据库"""
    init_db()
    # 知识库向量化暂不自动执行，通过 /api/admin/knowledge/ingest 手动触发


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
    config_type: str = Field(..., description="类型: weather/map")
    provider: str = Field(..., description="提供商: openweathermap/qweather/amap/baidu")
    api_key: str
    base_url: str = ""
    extra_params: str = "{}"


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


@app.get("/admin")
async def admin_page(request: Request):
    """管理后台"""
    return templates.TemplateResponse(request, "admin_v3.html")


# ============================================================
# API路由 - 核心规划
# ============================================================
@app.post("/api/v3/plan")
async def create_plan(req: PlanRequest):
    """
    创建旅行规划 (V3核心API)
    PlannerAgent调度所有子Agent并行执行
    """
    try:
        planner = PlannerAgent()
        result = planner.plan(
            destination=req.destination,
            days=req.days,
            travelers=req.travelers,
            budget=req.budget,
            origin=req.origin,
            style=req.style,
        )
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "message": f"规划失败: {str(e)}"}


@app.post("/api/v3/plan/stream")
async def create_plan_stream(req: PlanRequest):
    """
    流式创建旅行规划 (SSE)

    串行执行 Agent 并实时推送进度事件，前端可通过 ReadableStream 消费。
    与 POST /api/v3/plan 并行版互为补充：并行版性能更高，流式版体验更好。
    """
    async def event_generator():
        import time

        start_time = time.time()
        planner = PlannerAgent()
        context = {
            "destination": req.destination,
            "days": req.days,
            "travelers": req.travelers,
            "budget": req.budget,
            "origin": req.origin,
            "style": req.style,
        }
        results = {}

        def sse_payload(event: dict) -> str:
            return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        yield sse_payload({"type": "start", "message": "开始规划..."})

        agent_order = [
            ("weather", planner.weather_agent),
            ("hotel", planner.hotel_agent),
            ("restaurant", planner.restaurant_agent),
            ("attraction", planner.attraction_agent),
            ("transport", planner.transport_agent),
        ]
        for name, agent in agent_order:
            yield sse_payload({"type": "agent_start", "agent": name})
            result = await run_in_threadpool(agent.execute, context)
            results[f"{name}_result"] = result
            yield sse_payload({
                "type": "agent_complete",
                "agent": name,
                "status": result.status,
                "duration_ms": result.duration_ms,
            })

        # 风控Agent需要前面所有Agent的结果
        yield sse_payload({"type": "agent_start", "agent": "risk"})
        risk_result = await run_in_threadpool(
            planner.risk_agent.execute, {**context, **results}
        )
        yield sse_payload({
            "type": "agent_complete",
            "agent": "risk",
            "status": risk_result.status,
            "duration_ms": risk_result.duration_ms,
        })

        yield sse_payload({"type": "itinerary_generating"})
        if planner.use_llm:
            itinerary = await run_in_threadpool(
                planner._generate_with_llm, context, results, risk_result
            )
        else:
            itinerary = await run_in_threadpool(
                planner._generate_template, context, results, risk_result
            )

        total_duration_ms = int((time.time() - start_time) * 1000)
        itinerary_id = await run_in_threadpool(
            planner._save_itinerary, context, itinerary, results
        )

        final_data = {
            "success": True,
            "itinerary_id": itinerary_id,
            "destination": req.destination,
            "days": req.days,
            "travelers": req.travelers,
            "budget": req.budget,
            "origin": req.origin,
            "llm_used": planner.use_llm,
            "itinerary": itinerary,
            "agent_results": {k: v.to_dict() for k, v in results.items()},
            "risk": risk_result.to_dict(),
            "total_duration_ms": total_duration_ms,
        }
        yield sse_payload({"type": "complete", "data": final_data})
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get("/api/v3/plan/{itinerary_id}")
async def get_plan(itinerary_id: int):
    """获取行程详情"""
    conn = get_db_connection()
    try:
        itinerary = query_one(conn, "SELECT * FROM itineraries WHERE id = ?", (itinerary_id,))
        if not itinerary:
            return {"success": False, "message": "行程不存在"}

        logs = query_all(conn,
            "SELECT * FROM agent_logs WHERE itinerary_id = ? ORDER BY id", (itinerary_id,))

        return {"success": True, "data": {"itinerary": itinerary, "logs": logs}}
    finally:
        conn.close()


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
# API路由 - 配置管理
# ============================================================
@app.get("/api/admin/configs")
async def get_all_configs():
    """获取所有API配置"""
    return {
        "success": True,
        "data": {
            "llm": IntegrationConfig.get_all_api_configs(),
            "weather": IntegrationConfig.get_all_api_configs("weather"),
            "map": IntegrationConfig.get_all_api_configs("map"),
        }
    }


@app.post("/api/admin/llm/config")
async def save_llm_config(req: LLMConfigRequest):
    """保存LLM配置"""
    conn = get_db_connection()
    try:
        # 取消其他激活配置
        execute(conn, "UPDATE llm_configs SET is_active = 0")
        # 插入新配置
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
    """保存天气/地图API配置"""
    success = IntegrationConfig.save_api_config(
        req.config_type, req.provider, req.api_key, req.base_url, req.extra_params
    )
    if success:
        return {"success": True, "message": f"{req.config_type}配置保存成功"}
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
    - LLM Token 消耗统计
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
                },
                "agents": {
                    "total_logs": total_logs,
                    "completed_logs": completed_logs,
                    "success_rate": round(success_rate, 4),
                },
            },
        }
    finally:
        conn.close()


@app.post("/api/admin/knowledge/ingest")
async def ingest_knowledge():
    """手动触发旅行知识库向量化（首次启动或文档更新后调用）."""
    try:
        count = ensure_ingested()
        return {"success": True, "message": f"知识库导入完成，共 {count} 篇文档"}
    except Exception as e:
        return {"success": False, "message": f"导入失败: {str(e)}"}


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
    return {"ok": True, "version": "3.0.0", "features": ["planner-agent", "llm", "weather", "map", "db-mock"]}


# 兼容旧版入口
@app.get("/api/plan")
async def plan_redirect():
    return RedirectResponse(url="/api/v3/plan", status_code=308)
