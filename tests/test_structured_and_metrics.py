"""结构化 LLM 输出与可观测性 Metrics 测试."""

from unittest.mock import MagicMock

import pytest

from app.integrations.llm_client import LLMClient, PromptCache


@pytest.fixture
def structured_llm_client(memory_db, monkeypatch):
    """返回 JSON 的 LLMClient，用于测试 chat_structured."""
    client = LLMClient(
        config={
            "api_key": "test-key",
            "base_url": "https://api.test.com/v1",
            "model_name": "gpt-4o-mini",
            "temperature": 0.7,
            "max_tokens": 4096,
            "timeout": 60,
            "use_llm": True,
        }
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": '{"title": "杭州2日游", "summary": "西湖与美食", "days": []}'
                }
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        "model": "gpt-4o-mini",
    }
    monkeypatch.setattr(client._client, "post", lambda url, json: mock_response)
    return client


class TestChatStructured:
    """LLMClient.chat_structured 测试."""

    def test_chat_structured_with_schema(self, structured_llm_client):
        schema = {"type": "object", "properties": {"title": {"type": "string"}}}
        result = structured_llm_client.chat_structured(
            "system", "user", response_schema=schema, use_cache=False
        )
        assert result["success"] is True
        assert result["data"]["title"] == "杭州2日游"
        assert result["usage"]["prompt_tokens"] == 100
        assert result["usage"]["completion_tokens"] == 50

    def test_chat_structured_unavailable(self, memory_db):
        client = LLMClient(config={})
        result = client.chat_structured("system", "user")
        assert result["success"] is False
        assert "未配置" in result["error"]

    def test_chat_structured_parse_error(self, structured_llm_client, monkeypatch):
        bad_response = MagicMock()
        bad_response.status_code = 200
        bad_response.json.return_value = {
            "choices": [{"message": {"content": "not json at all"}}],
            "usage": {},
        }
        monkeypatch.setattr(structured_llm_client._client, "post", lambda url, json: bad_response)

        result = structured_llm_client.chat_structured(
            "system", "user", response_schema={"type": "object"}, use_cache=False
        )
        assert result["success"] is False
        assert "无法解析为JSON" in result["error"]

    def test_chat_structured_without_response_format(self, structured_llm_client, monkeypatch):
        """即使 provider 不支持 response_format，靠 prompt 内嵌 schema 也能解析."""
        structured_llm_client.config["supports_response_format"] = False
        schema = {"type": "object", "properties": {"title": {"type": "string"}}}
        result = structured_llm_client.chat_structured(
            "system", "user", response_schema=schema, use_cache=False
        )
        assert result["success"] is True
        assert result["data"]["title"] == "杭州2日游"


class TestMetricsEndpoint:
    """/api/admin/metrics 测试."""

    def test_metrics_structure(self, client):
        response = client.get("/api/admin/metrics")
        assert response.status_code == 200
        data = response.json()["data"]

        assert "cache" in data
        assert "llm" in data
        assert "agents" in data

        cache = data["cache"]
        assert "total_entries" in cache
        assert "total_hits" in cache
        assert "hit_rate" in cache
        assert "avg_latency_ms" in cache

        agents = data["agents"]
        assert "total_logs" in agents
        assert "completed_logs" in agents
        assert "success_rate" in agents

    def test_metrics_after_plan(self, no_llm, sample_data, client, db_conn):
        """执行一次规划后，metrics 应反映 Agent 日志."""
        client.post(
            "/api/v3/plan",
            json={
                "destination": "杭州",
                "days": 2,
                "travelers": 2,
                "origin": "上海",
                "style": "balanced",
            },
        )

        response = client.get("/api/admin/metrics")
        data = response.json()["data"]

        assert data["agents"]["total_logs"] >= 5
        assert data["agents"]["completed_logs"] == data["agents"]["total_logs"]
        assert data["agents"]["success_rate"] == 1.0

    def test_metrics_cache_stats(self, memory_db):
        """手动写入缓存后，metrics 应反映命中率和平均延迟."""
        cache = PromptCache()
        messages = [{"role": "user", "content": "metrics test"}]
        key = PromptCache.make_key("m", messages, 0.5, 100)
        cache.set(key, "m", messages, "response", {"total_tokens": 10}, ttl_seconds=3600, latency_ms=120)
        cache.get(key)
        cache.get(key)

        stats = cache.stats()
        assert stats["total_entries"] == 1
        assert stats["total_hits"] == 2
        assert stats["avg_latency_ms"] == 120.0

        total_requests = stats["total_hits"] + stats["total_entries"]
        assert stats["total_hits"] / total_requests == 2 / 3
