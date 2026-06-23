"""
把现有子 Agent 注册为 Tool，供 PlannerRuntime 动态调用。
支持 LLM 为每个 Tool 传入动态参数。
"""
from app.agents.v3.base import AgentResult
from app.agents.v3.poi_agent import AttractionAgent, HotelAgent, RestaurantAgent, TransportAgent
from app.agents.v3.risk_agent import RiskAgent
from app.agents.v3.tool import Tool, ToolRegistry
from app.agents.v3.weather_agent import WeatherAgent


def _merge_tool_input(inputs: dict) -> dict:
    """把 LLM 传入的 tool_input 合并到 context 中，tool_input 优先级更高"""
    context = dict(inputs.get("context") or {})
    tool_input = inputs.get("tool_input") or {}
    if tool_input:
        context.update(tool_input)
    return context


def build_tool_registry(llm_client) -> ToolRegistry:
    """构建默认的 Tool 集合"""
    registry = ToolRegistry()

    weather_agent = WeatherAgent(llm_client)
    hotel_agent = HotelAgent(llm_client)
    restaurant_agent = RestaurantAgent(llm_client)
    attraction_agent = AttractionAgent(llm_client)
    transport_agent = TransportAgent(llm_client)
    risk_agent = RiskAgent(llm_client)

    registry.register(Tool(
        name="get_weather",
        description="查询目的地城市的当前天气和未来几天预报，用于安排行程和穿衣建议。",
        input_schema={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "要查询的城市，不填则使用用户请求中的目的地。",
                }
            },
            "required": [],
        },
        execute_fn=lambda inputs: weather_agent.execute(_merge_tool_input(inputs)),
    ))

    registry.register(Tool(
        name="search_hotels",
        description="根据目的地、预算和出行人数搜索推荐酒店。",
        input_schema={
            "type": "object",
            "properties": {
                "district": {
                    "type": "string",
                    "description": "希望酒店所在的区域或商圈，例如'西湖区'、'市中心'。",
                },
                "budget_level": {
                    "type": "string",
                    "enum": ["luxury", "high", "medium", "low"],
                    "description": "酒店价位档次，不填则根据用户预算自动判断。",
                },
            },
            "required": [],
        },
        execute_fn=lambda inputs: hotel_agent.execute(_merge_tool_input(inputs)),
    ))

    registry.register(Tool(
        name="search_restaurants",
        description="搜索目的地城市的推荐餐厅和特色美食。",
        input_schema={
            "type": "object",
            "properties": {
                "cuisine_type": {
                    "type": "string",
                    "description": "想搜索的菜系类型，例如'川菜'、'海鲜'、'西餐'。",
                },
                "district": {
                    "type": "string",
                    "description": "希望餐厅所在的区域。",
                },
            },
            "required": [],
        },
        execute_fn=lambda inputs: restaurant_agent.execute(_merge_tool_input(inputs)),
    ))

    registry.register(Tool(
        name="search_attractions",
        description="搜索目的地城市的景点，支持缓存和高德/百度 POI 兜底，保证景点数量足够。",
        input_schema={
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "string",
                    "description": "景点搜索关键词，例如'自然风光'、'博物馆'、'古镇'。不填则搜索通用景点。",
                },
                "min_count": {
                    "type": "integer",
                    "description": "期望返回的景点最少数量，默认至少保证每天 1 个且整体不少于 5 个。",
                },
            },
            "required": [],
        },
        execute_fn=lambda inputs: attraction_agent.execute(_merge_tool_input(inputs)),
    ))

    registry.register(Tool(
        name="plan_transport",
        description="规划出发地到目的地之间的交通方案（飞机、高铁、自驾等）。",
        input_schema={
            "type": "object",
            "properties": {
                "transport_type": {
                    "type": "string",
                    "enum": ["flight", "train", "drive"],
                    "description": "偏好的交通方式，不填则返回所有可选方案。",
                }
            },
            "required": [],
        },
        execute_fn=lambda inputs: transport_agent.execute(_merge_tool_input(inputs)),
    ))

    registry.register(Tool(
        name="risk_check",
        description="综合评估预算、高反、天气、团队规模等风险，必须在收集完其他信息后调用。",
        input_schema={"type": "object", "properties": {}, "required": []},
        execute_fn=lambda inputs: risk_agent.execute({
            **_merge_tool_input(inputs),
            **inputs.get("results", {}),
        }),
    ))

    return registry


def agent_result_to_observation(result: AgentResult) -> dict[str, Any]:
    """把 AgentResult 转换为 observation 摘要，用于写入 trace"""
    data = result.data or {}
    summary = {}
    if result.agent_type == "weather":
        summary = {"current": data.get("current", {}), "forecast_days": len(data.get("forecast", []))}
    elif result.agent_type == "hotel":
        summary = {"hotel_count": len(data.get("hotels", [])), "total": data.get("total", 0)}
    elif result.agent_type == "restaurant":
        summary = {"restaurant_count": len(data.get("restaurants", [])), "total": data.get("total", 0)}
    elif result.agent_type == "attraction":
        summary = {
            "attraction_count": len(data.get("attractions", [])),
            "total": data.get("total", 0),
            "trace": data.get("_trace", {}),
        }
    elif result.agent_type == "transport":
        summary = {"distance_km": data.get("distance_km"), "options": len(data.get("options", []))}
    elif result.agent_type == "risk":
        summary = {"warning_count": len(data.get("warnings", [])), "is_safe": data.get("is_safe", True)}

    return {
        "agent_type": result.agent_type,
        "agent_name": result.agent_name,
        "status": result.status,
        "duration_ms": result.duration_ms,
        "error": result.error,
        "summary": summary,
    }
