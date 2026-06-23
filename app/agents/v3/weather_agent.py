"""
天气Agent - 查询目的地天气，给出出行建议
"""
from typing import Any

from app.agents.v3.base import BaseAgentV3
from app.integrations.weather import WeatherClient


class WeatherAgent(BaseAgentV3):
    """天气查询Agent - 获取目的地当前天气和预报"""

    agent_type = "weather"
    agent_name = "天气查询Agent"

    def __init__(self, llm_client=None):
        super().__init__(llm_client)
        self.weather = WeatherClient()
        # 天气数据变化快，Prompt 缓存 1 小时
        self.llm_cache_ttl = 3600

    def _execute_with_db(self, context: dict[str, Any]) -> dict[str, Any]:
        city = context.get("city") or context.get("destination", "杭州")
        days = context.get("days", 3)

        current = self.weather.get_current_weather(city)
        forecast = self.weather.get_forecast(city, days)
        advice = self.weather.get_clothing_advice(
            current.get("temp", 20),
            current.get("description", "")
        )

        return {
            "city": city,
            "current": current,
            "forecast": forecast,
            "clothing_advice": advice,
            "source": current.get("provider", "mock"),
        }

    def _build_prompt(self, context: dict, db_data: dict) -> tuple:
        city = context.get("city") or context.get("destination", "")
        days = context.get("days", 3)
        style = context.get("style", "")

        system = "你是一位专业的旅行天气顾问。根据天气数据给出出行建议和注意事项。输出JSON格式。"
        user = f"""目的地: {city}, 行程天数: {days}天, 旅行风格: {style}

当前天气: {db_data['current']}
预报: {db_data['forecast']}

请分析并输出JSON:
{{
  "reasoning": "整体天气评估和出行建议(100字内)",
  "recommendations": ["具体建议1", "具体建议2"],
  "warnings": ["注意事项1"],
  "best_activities": ["适合的活动1"]
}}"""
        return system, user

    def _default_reasoning(self, context: dict, db_data: dict) -> str:
        c = db_data.get("current", {})
        return f"{db_data['city']}当前{c.get('temp')}°C，{c.get('description')}，适宜出行。{db_data.get('clothing_advice', '')}"
