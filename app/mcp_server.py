"""MCP Server 封装 - 将旅行规划能力暴露为标准 MCP 工具."""

import json
import logging

from mcp.server import Server
from mcp.server.sse import SseServerTransport, TransportSecuritySettings
from mcp.types import TextContent, Tool

from app.agents.v3.planner import PlannerAgent
from app.db.database import get_db_connection, query_one
from app.integrations.weather import WeatherClient
from app.knowledge import ChecklistGenerator

logger = logging.getLogger(__name__)

# MCP Server 实例
mcp_server = Server("travel-planner")

# SSE 传输层：客户端通过 GET /mcp/sse 建立 SSE 连接，
# 然后通过 POST /mcp/messages/?session_id=xxx 发送消息
# 测试环境关闭 DNS rebinding 保护，生产环境应配置 allowed_hosts
mcp_transport = SseServerTransport(
    "/mcp/messages/",
    security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


PLAN_TRAVEL_SCHEMA = {
    "type": "object",
    "properties": {
        "destination": {"type": "string", "description": "目的地城市，如杭州、北京"},
        "days": {"type": "integer", "minimum": 1, "maximum": 14, "description": "行程天数"},
        "travelers": {"type": "integer", "minimum": 1, "maximum": 20, "description": "出行人数"},
        "budget": {"type": "integer", "description": "预算（元），可选"},
        "origin": {"type": "string", "description": "出发城市，默认上海"},
        "style": {
            "type": "string",
            "enum": ["slow", "relaxed", "balanced", "intensive", "family", "foodie", "photography"],
            "description": "旅行风格",
        },
    },
    "required": ["destination", "days"],
}


GET_ITINERARY_SCHEMA = {
    "type": "object",
    "properties": {
        "itinerary_id": {"type": "integer", "description": "行程 ID"},
    },
    "required": ["itinerary_id"],
}


GET_WEATHER_SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string", "description": "城市名称"},
        "days": {"type": "integer", "minimum": 1, "maximum": 7, "description": "预报天数，默认 3"},
    },
    "required": ["city"],
}


GENERATE_CHECKLIST_SCHEMA = {
    "type": "object",
    "properties": {
        "destination": {"type": "string", "description": "目的地城市"},
        "days": {"type": "integer", "minimum": 1, "maximum": 30, "description": "行程天数"},
        "travelers": {"type": "integer", "minimum": 1, "maximum": 20, "description": "出行人数"},
        "season": {"type": "string", "description": "出行季节/月份，如夏季、6月"},
        "special_needs": {"type": "string", "description": "特殊需求，如带老人、亲子、高原"},
        "style": {"type": "string", "description": "旅行风格/兴趣，如摄影、美食"},
    },
    "required": ["destination", "days"],
}


@mcp_server.list_tools()
async def list_tools() -> list[Tool]:
    """列出可用的 MCP 工具."""
    return [
        Tool(
            name="plan_travel",
            description="为指定目的地和天数创建旅行规划，返回完整行程安排",
            inputSchema=PLAN_TRAVEL_SCHEMA,
        ),
        Tool(
            name="get_itinerary",
            description="根据行程 ID 查询已生成的行程详情",
            inputSchema=GET_ITINERARY_SCHEMA,
        ),
        Tool(
            name="get_weather",
            description="查询指定城市的当前天气和未来几天预报",
            inputSchema=GET_WEATHER_SCHEMA,
        ),
        Tool(
            name="generate_checklist",
            description="根据目的地、天数、季节、特殊需求生成旅行准备清单",
            inputSchema=GENERATE_CHECKLIST_SCHEMA,
        ),
    ]


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """处理 MCP 工具调用."""
    logger.info(f"[MCP] 调用工具: {name}, 参数: {arguments}")

    try:
        if name == "plan_travel":
            result = _plan_travel(arguments)
        elif name == "get_itinerary":
            result = _get_itinerary(arguments)
        elif name == "get_weather":
            result = _get_weather(arguments)
        elif name == "generate_checklist":
            result = _generate_checklist(arguments)
        else:
            return [TextContent(type="text", text=json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False))]

        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))]
    except Exception as e:
        logger.exception(f"[MCP] 工具调用失败: {name}")
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, ensure_ascii=False))]


def _plan_travel(arguments: dict) -> dict:
    """执行旅行规划."""
    planner = PlannerAgent()
    result = planner.plan(
        destination=arguments["destination"],
        days=arguments.get("days", 3),
        travelers=arguments.get("travelers", 2),
        budget=arguments.get("budget"),
        origin=arguments.get("origin", "上海"),
        style=arguments.get("style", "balanced"),
    )
    return {"success": True, "data": result}


def _get_itinerary(arguments: dict) -> dict:
    """查询行程详情."""
    itinerary_id = arguments["itinerary_id"]
    conn = get_db_connection()
    try:
        itinerary = query_one(conn, "SELECT * FROM itineraries WHERE id = ?", (itinerary_id,))
        if not itinerary:
            return {"success": False, "message": "行程不存在"}
        logs = query_one(conn, "SELECT COUNT(*) AS c FROM agent_logs WHERE itinerary_id = ?", (itinerary_id,))
        return {"success": True, "data": {"itinerary": itinerary, "agent_log_count": logs["c"]}}
    finally:
        conn.close()


def _get_weather(arguments: dict) -> dict:
    """查询天气."""
    client = WeatherClient()
    city = arguments["city"]
    days = arguments.get("days", 3)
    current = client.get_current_weather(city)
    forecast = client.get_forecast(city, days)
    advice = client.get_clothing_advice(current.get("temp", 20), current.get("description", ""))
    return {
        "success": True,
        "data": {"current": current, "forecast": forecast, "clothing_advice": advice},
    }


def _generate_checklist(arguments: dict) -> dict:
    """生成旅行 checklist."""
    generator = ChecklistGenerator()
    result = generator.generate(
        destination=arguments["destination"],
        days=arguments.get("days", 3),
        travelers=arguments.get("travelers", 2),
        season=arguments.get("season"),
        special_needs=arguments.get("special_needs"),
        style=arguments.get("style"),
    )
    return {"success": True, "data": result}


async def handle_mcp_sse(scope, receive, send):
    """ASGI handler for MCP SSE endpoint."""
    async with mcp_transport.connect_sse(scope, receive, send) as streams:
        await mcp_server.run(
            streams[0], streams[1], mcp_server.create_initialization_options()
        )


class MCPMessagesRoute:
    """ASGI callable wrapper for MCP message POST endpoint."""

    async def __call__(self, scope, receive, send):
        await mcp_transport.handle_post_message(scope, receive, send)
