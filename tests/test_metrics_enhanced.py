"""增强版 /api/admin/metrics 指标测试."""



class TestEnhancedMetrics:
    """测试 metrics 接口新增的按 Agent 分组、时间趋势、请求统计."""

    def test_metrics_contains_llm_call_count(self, client, db_conn):
        """metrics 应返回 LLM 调用次数."""
        response = client.get("/api/admin/metrics")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "call_count" in data["llm"]
        assert isinstance(data["llm"]["call_count"], int)
        assert "total_tokens" in data["llm"]
        assert data["llm"]["total_tokens"] == data["llm"]["total_prompt_tokens"] + data["llm"]["total_completion_tokens"]

    def test_metrics_contains_by_type(self, client, db_conn):
        """metrics 应返回按 Agent 类型分组统计."""
        response = client.get("/api/admin/metrics")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "by_type" in data["agents"]
        assert isinstance(data["agents"]["by_type"], list)

    def test_metrics_contains_trends(self, client, db_conn):
        """metrics 应返回 token 消耗趋势."""
        response = client.get("/api/admin/metrics")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "trends" in data
        assert isinstance(data["trends"], list)

    def test_metrics_contains_request_stats(self, client, db_conn):
        """metrics 应返回 API 请求统计."""
        client.get("/api/ping")
        response = client.get("/api/admin/metrics")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "requests" in data
        assert "total_requests" in data["requests"]
        assert "avg_duration_ms" in data["requests"]
        assert data["requests"]["total_requests"] >= 1

    def test_metrics_by_type_after_plan(self, client, db_conn, no_llm):
        """执行规划后，按 Agent 类型统计应有数据."""
        response = client.post(
            "/api/v3/plan",
            json={"destination": "杭州", "days": 2},
        )
        assert response.status_code == 200

        metrics = client.get("/api/admin/metrics").json()["data"]
        by_type = {row["agent_type"]: row for row in metrics["agents"]["by_type"]}
        assert "planner" in by_type or "weather" in by_type
        assert metrics["agents"]["total_logs"] >= 5
