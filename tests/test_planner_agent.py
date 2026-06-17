"""PlannerAgent 测试."""

import pytest

from app.agents.v3.planner import PlannerAgent


@pytest.fixture
def planner(no_llm):
    """返回禁用 LLM 的 PlannerAgent."""
    return PlannerAgent()


def test_planner_basic(no_llm, sample_data):
    """基础规划流程，验证返回结构完整."""
    planner = PlannerAgent()
    result = planner.plan(
        destination="杭州",
        days=2,
        travelers=2,
        budget=3000,
        origin="上海",
        style="balanced",
    )

    assert result["success"] is True
    assert result["destination"] == "杭州"
    assert result["days"] == 2
    assert result["travelers"] == 2
    assert "itinerary_id" in result
    assert "itinerary" in result
    assert "agent_results" in result
    assert "risk" in result
    assert result["llm_used"] is False

    agent_results = result["agent_results"]
    for key in ["weather_result", "hotel_result", "restaurant_result", "attraction_result", "transport_result"]:
        assert key in agent_results
        assert agent_results[key]["status"] == "completed"


def test_planner_itinerary_days(no_llm, sample_data):
    """验证生成行程包含正确天数."""
    planner = PlannerAgent()
    result = planner.plan(destination="杭州", days=3, travelers=2)

    itinerary = result["itinerary"]
    assert "days" in itinerary
    assert len(itinerary["days"]) == 3
    for i, day in enumerate(itinerary["days"], start=1):
        assert day["day"] == i
        assert "activities" in day


def test_planner_saves_itinerary(no_llm, sample_data, db_conn):
    """验证规划结果会写入数据库."""
    planner = PlannerAgent()
    result = planner.plan(destination="杭州", days=2, travelers=2)

    itinerary_id = result["itinerary_id"]
    assert itinerary_id > 0

    row = db_conn.execute("SELECT * FROM itineraries WHERE id = ?", (itinerary_id,)).fetchone()
    assert row is not None
    assert row["destination"] == "杭州"

    logs = db_conn.execute("SELECT * FROM agent_logs WHERE itinerary_id = ?", (itinerary_id,)).fetchall()
    assert len(logs) >= 5
