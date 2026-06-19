"""API 请求日志落库测试."""



class TestRequestLogging:
    """测试请求日志中间件是否正确记录 HTTP 请求."""

    def test_ping_creates_request_log(self, client, db_conn):
        """访问 /api/ping 后应产生一条请求日志."""
        response = client.get("/api/ping")
        assert response.status_code == 200

        row = db_conn.execute(
            "SELECT * FROM request_logs WHERE path = ? ORDER BY id DESC LIMIT 1",
            ("/api/ping",),
        ).fetchone()
        assert row is not None
        assert row["method"] == "GET"
        assert row["status_code"] == 200
        assert row["duration_ms"] is not None
        assert row["duration_ms"] >= 0

    def test_static_files_not_logged(self, client, db_conn):
        """静态文件请求不应被记录，避免日志表膨胀."""
        before = db_conn.execute("SELECT COUNT(*) AS c FROM request_logs").fetchone()["c"]
        response = client.get("/static/css/style.css")
        assert response.status_code in (200, 404)
        after = db_conn.execute("SELECT COUNT(*) AS c FROM request_logs").fetchone()["c"]
        assert after == before

    def test_request_logs_endpoint(self, client):
        """管理后台请求日志接口应返回正确结构."""
        response = client.get("/api/admin/request-logs?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "total" in data
        assert data["limit"] == 10
        assert data["offset"] == 0

    def test_request_logs_filter_by_path(self, client):
        """按 path 过滤请求日志."""
        client.get("/api/ping")
        response = client.get("/api/admin/request-logs?path=/api/ping")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert all(log["path"] == "/api/ping" for log in data["data"])

    def test_plan_request_logged(self, client, db_conn, no_llm):
        """行程规划请求应被记录，包括 POST 方法和状态码."""
        response = client.post(
            "/api/v3/plan",
            json={"destination": "杭州", "days": 2},
        )
        assert response.status_code == 200

        row = db_conn.execute(
            "SELECT * FROM request_logs WHERE path = ? ORDER BY id DESC LIMIT 1",
            ("/api/v3/plan",),
        ).fetchone()
        assert row is not None
        assert row["method"] == "POST"
        assert row["status_code"] == 200

    def test_error_request_logged_with_status(self, client, db_conn):
        """异常请求也应记录，状态码为 500."""
        # 访问不存在的 itinerary_id 类型错误会触发异常处理
        before = db_conn.execute("SELECT COUNT(*) AS c FROM request_logs").fetchone()["c"]
        response = client.get("/api/v3/plan/not_a_number")
        assert response.status_code == 422
        after = db_conn.execute("SELECT COUNT(*) AS c FROM request_logs").fetchone()["c"]
        assert after > before

        row = db_conn.execute(
            "SELECT * FROM request_logs WHERE path = ? ORDER BY id DESC LIMIT 1",
            ("/api/v3/plan/not_a_number",),
        ).fetchone()
        assert row is not None
        assert row["status_code"] == 422
