"""
风控Agent - 综合风险评估
检查预算/安全/时间/质量风险
"""
from typing import Any

from app.agents.v3.base import BaseAgentV3


class RiskAgent(BaseAgentV3):
    """风控专家Agent - 综合风险检测"""

    agent_type = "risk"
    agent_name = "风控Agent"

    @staticmethod
    def _unwrap(value: Any) -> Any:
        """兼容 dict 与 AgentResult：Planner 可能把 AgentResult 对象传进来"""
        if hasattr(value, "data"):
            return value.data
        return value

    def _execute_with_db(self, context: dict[str, Any]) -> dict[str, Any]:
        budget = context.get("budget")
        travelers = context.get("travelers", 2)
        days = context.get("days", 3)

        # 从其他Agent结果中提取数据（可能是 AgentResult 或 dict）
        hotel_data = self._unwrap(context.get("hotel_result", {}))
        attraction_data = self._unwrap(context.get("attraction_result", {}))
        weather_data = self._unwrap(context.get("weather_result", {}))

        warnings: list[dict] = []

        # 1. 预算风险
        if budget:
            hotels = hotel_data.get("hotels", [])
            if hotels:
                avg_hotel = sum(h.get("price_value", 0) for h in hotels[:3]) / min(len(hotels), 3)
                est_hotel = avg_hotel * days
                if est_hotel > budget * 0.5:
                    warnings.append({
                        "type": "budget", "level": "medium",
                        "message": f"预估住宿费用约¥{int(est_hotel)}，占预算比例较高",
                        "suggestion": "考虑选择经济型酒店或调整行程天数",
                    })

        # 2. 高反风险
        risks = attraction_data.get("altitude_risks", [])
        for r in risks:
            level = "high" if r["altitude"] > 3500 else "medium"
            warnings.append({
                "type": "safety", "level": level,
                "message": f"{r['name']}海拔{r['altitude']}米，存在高原反应风险",
                "suggestion": "建议提前适应，携带氧气，避免剧烈运动",
            })

        # 3. 天气风险
        current = weather_data.get("current", {})
        if current.get("description") in ["暴雨", "大雨", "台风"]:
            warnings.append({
                "type": "weather", "level": "high",
                "message": f"目的地当前{current['description']}，可能影响出行",
                "suggestion": "关注天气预报，准备室内备选方案",
            })

        # 4. 多人出行
        if travelers > 4:
            warnings.append({
                "type": "logistics", "level": "low",
                "message": f"{travelers}人团队出行，建议提前预订",
                "suggestion": "大团队建议提前联系餐厅/景点预约",
            })

        is_safe = not any(w["level"] == "high" for w in warnings)

        return {
            "warnings": warnings,
            "is_safe": is_safe,
            "warning_count": len(warnings),
        }

    def _build_prompt(self, context: dict, db_data: dict) -> tuple:
        system = "你是旅行安全顾问。评估行程风险并给出建议。输出JSON。"
        user = f"""评估以下行程风险:

风险项: {db_data.get('warnings', [])}

输出JSON:
{{
  "reasoning": "风险评估总结(100字)",
  "is_safe": true/false,
  "summary": "总体安全评估"
}}"""
        return system, user

    def _default_reasoning(self, context: dict, db_data: dict) -> str:
        count = db_data.get("warning_count", 0)
        if count == 0:
            return "暂未发现明显风险"
        levels = [w["level"] for w in db_data.get("warnings", [])]
        high = levels.count("high")
        return f"发现{count}项注意事项，其中高风险{high}项，请查看详情"
