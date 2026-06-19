"""MCP Server 测试.

注意：MCP 的 SSE transport 依赖 EventSourceResponse + data_sender_callable，
在 ASGI/TestClient 环境下存在事件循环调度死锁，因此本测试只覆盖工具定义和
直接调用逻辑。SSE 端到端连接请使用真实 uvicorn 服务手动验证。
"""

import pytest

from app.mcp_server import call_tool, list_tools


class TestMCPServer:
    """测试 MCP Server 暴露的旅行规划能力."""

    async def test_list_tools(self):
        """应返回 4 个可用工具."""
        tools = await list_tools()
        assert len(tools) == 4
        names = {tool.name for tool in tools}
        assert names == {"plan_travel", "get_itinerary", "get_weather", "generate_checklist"}

    async def test_plan_travel_tool_call(self, no_llm):
        """调用 plan_travel 应返回成功结果."""
        result = await call_tool("plan_travel", {"destination": "杭州", "days": 2})
        assert len(result) == 1
        data = result[0].text
        assert "success" in data
        assert "杭州" in data

    async def test_get_weather_tool_call(self):
        """调用 get_weather 应返回天气数据."""
        result = await call_tool("get_weather", {"city": "杭州", "days": 3})
        assert len(result) == 1
        data = result[0].text
        assert "current" in data
        assert "forecast" in data

    async def test_generate_checklist_tool_call(self):
        """调用 generate_checklist 应返回 checklist（需已安装 ChromaDB）."""
        pytest.importorskip("chromadb")
        result = await call_tool("generate_checklist", {"destination": "杭州", "days": 3})
        assert len(result) == 1
        data = result[0].text
        assert "categories" in data

    async def test_unknown_tool_returns_error(self):
        """调用未知工具应返回错误."""
        result = await call_tool("unknown_tool", {})
        assert len(result) == 1
        assert "error" in result[0].text
