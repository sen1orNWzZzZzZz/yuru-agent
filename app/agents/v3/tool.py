"""
Tool 抽象层
把子 Agent 包装成可被 Planner 动态调用的 Tool。
每个 Tool 包含：名称、描述、输入 Schema、执行函数。
"""
from typing import Any, Callable

from app.agents.v3.base import AgentResult


class Tool:
    """可被 Planner 调用的工具"""

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        execute_fn: Callable[[dict[str, Any]], AgentResult],
    ):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.execute_fn = execute_fn

    def definition(self) -> dict[str, Any]:
        """返回给 LLM 的 tool schema"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
        }

    def execute(self, inputs: dict[str, Any]) -> AgentResult:
        return self.execute_fn(inputs)


class ToolRegistry:
    """Tool 注册表"""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> "ToolRegistry":
        self._tools[tool.name] = tool
        return self

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def definitions(self) -> list[dict[str, Any]]:
        return [t.definition() for t in self._tools.values()]
