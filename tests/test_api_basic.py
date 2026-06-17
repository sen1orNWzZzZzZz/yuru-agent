"""基础 API 测试."""



def test_ping(client):
    """健康检查端点."""
    response = client.get("/api/ping")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["version"] == "3.0.0"


def test_index_page(client):
    """首页渲染."""
    response = client.get("/")
    assert response.status_code == 200
    assert "灵动旅心" in response.text


def test_plan_page(client):
    """规划页面渲染."""
    response = client.get("/plan")
    assert response.status_code == 200
    assert "规划" in response.text


def test_admin_page(client):
    """管理后台渲染."""
    response = client.get("/admin")
    assert response.status_code == 200
    assert "管理" in response.text


def test_legacy_plan_redirect(client):
    """旧版入口重定向."""
    response = client.get("/api/plan", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/api/v3/plan"
