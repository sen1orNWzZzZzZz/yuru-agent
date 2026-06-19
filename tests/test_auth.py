"""用户认证与画像测试."""
import pytest


def test_register_and_login(client):
    """注册并登录后，/api/auth/me 能拿到用户信息."""
    r1 = client.post("/api/auth/register", json={"username": "tester", "password": "1234"})
    assert r1.status_code == 200
    assert r1.json()["success"] is True

    r2 = client.post("/api/auth/login", json={"username": "tester", "password": "1234"})
    assert r2.status_code == 200
    assert r2.json()["success"] is True

    r3 = client.get("/api/auth/me")
    assert r3.status_code == 200
    data = r3.json()["data"]
    assert data["username"] == "tester"


def test_update_profile(client):
    """登录后可更新并读取画像."""
    client.post("/api/auth/register", json={"username": "profile_user", "password": "1234"})
    client.post("/api/auth/login", json={"username": "profile_user", "password": "1234"})

    update = client.put("/api/users/me/profile", json={
        "interests": ["摄影", "美食"],
        "pace": "relaxed",
        "budget_range": 2000,
        "must_visit_tags": ["西湖"],
    })
    assert update.status_code == 200
    assert update.json()["success"] is True

    profile = client.get("/api/users/me/profile").json()["data"]
    assert profile["pace"] == "relaxed"
    assert profile["budget_range"] == 2000
    assert "摄影" in profile["interests"]


def test_plan_with_user_merges_profile(client, no_llm):
    """登录状态下规划行程会写入 user_id，并触发画像总结."""
    client.post("/api/auth/register", json={"username": "plan_user", "password": "1234"})
    client.post("/api/auth/login", json={"username": "plan_user", "password": "1234"})
    client.put("/api/users/me/profile", json={
        "interests": ["摄影"],
        "pace": "slow",
        "must_visit_tags": ["西湖"],
    })

    plan_resp = client.post("/api/v3/plan", json={
        "destination": "杭州",
        "days": 2,
        "travelers": 2,
        "budget": 1500,
        "origin": "上海",
        "style": "balanced",
    })
    assert plan_resp.status_code == 200
    result = plan_resp.json()
    assert result["success"] is True
    itinerary_id = result["data"]["itinerary_id"]

    # 验证行程属于当前用户
    detail = client.get(f"/api/v3/plan/{itinerary_id}").json()["data"]
    assert detail["itinerary"]["user_id"] is not None

    # 验证画像被总结更新
    profile = client.get("/api/users/me/profile").json()["data"]
    assert profile["llm_summary"] or profile["interests"]
    assert "杭州" in (profile["llm_summary"] or "")
