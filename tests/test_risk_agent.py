"""风控 Agent 测试."""

import pytest

from app.agents.v3.risk_agent import RiskAgent


@pytest.fixture
def risk_agent(no_llm):
    return RiskAgent()


class TestRiskAgent:
    """RiskAgent 风控规则测试."""

    def test_no_warnings_for_safe_plan(self, risk_agent):
        result = risk_agent.execute({
            "hotel_result": {"data": {"hotels": [{"price_value": 300}]}},
            "attraction_result": {"data": {"altitude_risks": []}},
            "weather_result": {"data": {"current": {"description": "晴"}}},
            "budget": 5000,
            "travelers": 2,
            "days": 3,
        })

        assert result.status == "completed"
        assert result.data["is_safe"] is True
        assert len(result.data["warnings"]) == 0

    def test_budget_warning(self, risk_agent):
        result = risk_agent.execute({
            "hotel_result": {"data": {"hotels": [{"price_value": 2000}]}},
            "attraction_result": {"data": {"altitude_risks": []}},
            "weather_result": {"data": {"current": {}}},
            "budget": 2000,
            "travelers": 2,
            "days": 3,
        })

        warnings = result.data["warnings"]
        budget_warnings = [w for w in warnings if w["type"] == "budget"]
        assert len(budget_warnings) > 0
        assert budget_warnings[0]["level"] == "medium"

    def test_altitude_warning(self, risk_agent):
        result = risk_agent.execute({
            "hotel_result": {"data": {"hotels": []}},
            "attraction_result": {"data": {"altitude_risks": [{"name": "玉龙雪山", "altitude": 4500}]}},
            "weather_result": {"data": {"current": {}}},
            "budget": 5000,
            "travelers": 2,
            "days": 3,
        })

        warnings = result.data["warnings"]
        altitude_warnings = [w for w in warnings if w["type"] == "safety"]
        assert len(altitude_warnings) > 0
        assert altitude_warnings[0]["level"] == "high"

    def test_weather_warning(self, risk_agent):
        result = risk_agent.execute({
            "hotel_result": {"data": {"hotels": []}},
            "attraction_result": {"data": {"altitude_risks": []}},
            "weather_result": {"data": {"current": {"description": "暴雨"}}},
            "budget": 5000,
            "travelers": 2,
            "days": 3,
        })

        warnings = result.data["warnings"]
        weather_warnings = [w for w in warnings if w["type"] == "weather"]
        assert len(weather_warnings) > 0
        assert weather_warnings[0]["level"] == "high"

    def test_large_group_warning(self, risk_agent):
        result = risk_agent.execute({
            "hotel_result": {"data": {"hotels": []}},
            "attraction_result": {"data": {"altitude_risks": []}},
            "weather_result": {"data": {"current": {}}},
            "budget": 5000,
            "travelers": 6,
            "days": 3,
        })

        warnings = result.data["warnings"]
        logistics_warnings = [w for w in warnings if w["type"] == "logistics"]
        assert len(logistics_warnings) > 0
        assert logistics_warnings[0]["level"] == "low"
