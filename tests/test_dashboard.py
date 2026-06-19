"""Dashboard 可观测性接口测试."""
import pytest


@pytest.mark.parametrize("endpoint", [
    "/api/admin/dashboard/summary?range=24h",
    "/api/admin/dashboard/trends?range=7d",
    "/api/admin/dashboard/agents?range=24h",
    "/api/admin/dashboard/requests?range=24h&limit=5",
    "/api/admin/dashboard/agent-logs?range=24h&limit=5",
])
def test_dashboard_endpoints(client, endpoint):
    """Dashboard 各分级接口返回成功且结构完整."""
    response = client.get(endpoint)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "data" in data


def test_dashboard_page_loads(client):
    """Dashboard 页面可正常加载."""
    response = client.get("/admin/dashboard")
    assert response.status_code == 200
    assert "系统观测大盘" in response.text
