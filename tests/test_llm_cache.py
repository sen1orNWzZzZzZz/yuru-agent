"""LLM Prompt 缓存测试."""

from unittest.mock import MagicMock

import pytest

from app.integrations.llm_client import LLMClient, PromptCache


@pytest.fixture
def llm_client(memory_db, monkeypatch):
    """配置了可用 LLM 的客户端，但 HTTP 请求会被 mock."""
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
        "choices": [{"message": {"content": "AI generated response"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "model": "gpt-4o-mini",
    }
    monkeypatch.setattr(client._client, "post", lambda url, json: mock_response)
    return client


class TestPromptCache:
    """PromptCache 直接测试."""

    def test_make_key_deterministic(self):
        """相同参数生成相同 key."""
        messages = [{"role": "user", "content": "hello"}]
        key1 = PromptCache.make_key("gpt-4", messages, 0.5, 1024)
        key2 = PromptCache.make_key("gpt-4", messages, 0.5, 1024)
        assert key1 == key2
        assert len(key1) == 64  # SHA256 hex

    def test_make_key_sensitive_to_parameters(self):
        """参数不同 key 不同."""
        messages = [{"role": "user", "content": "hello"}]
        key1 = PromptCache.make_key("gpt-4", messages, 0.5, 1024)
        key2 = PromptCache.make_key("gpt-4", messages, 0.6, 1024)
        key3 = PromptCache.make_key("gpt-3.5", messages, 0.5, 1024)
        assert key1 != key2
        assert key1 != key3

    def test_get_set_and_hit_count(self, memory_db):
        """写入后读取，命中次数正确更新."""
        cache = PromptCache()
        messages = [{"role": "user", "content": "test"}]
        key = PromptCache.make_key("m", messages, 0.5, 100)

        assert cache.get(key) is None

        cache.set(key, "m", messages, "response", {"total_tokens": 10}, ttl_seconds=3600)
        cached = cache.get(key)
        assert cached is not None
        assert cached["content"] == "response"
        assert cached["usage"]["total_tokens"] == 10

        # 再次命中
        cache.get(key)
        stats = cache.stats()
        assert stats["total_hits"] == 2

    def test_expired_cache_returns_none(self, memory_db):
        """过期缓存应被视为不存在."""
        cache = PromptCache()
        messages = [{"role": "user", "content": "expire"}]
        key = PromptCache.make_key("m", messages, 0.5, 100)
        cache.set(key, "m", messages, "old", {}, ttl_seconds=-1)
        assert cache.get(key) is None

    def test_clear_expired(self, memory_db):
        """清理过期条目."""
        cache = PromptCache()
        messages = [{"role": "user", "content": "x"}]
        key = PromptCache.make_key("m", messages, 0.5, 100)
        cache.set(key, "m", messages, "x", {}, ttl_seconds=-1)
        assert cache.stats()["expired_entries"] == 1
        deleted = cache.clear_expired()
        assert deleted == 1
        assert cache.stats()["total_entries"] == 0


class TestLLMClientCache:
    """LLMClient 集成缓存测试."""

    def test_cache_miss_then_hit(self, llm_client):
        """第一次调用未命中，第二次命中."""
        messages = [{"role": "user", "content": "cache me"}]

        result1 = llm_client.chat_messages(messages, use_cache=True, cache_ttl=3600)
        assert result1["success"] is True
        assert result1.get("cached") is False
        assert result1["content"] == "AI generated response"
        assert result1["latency_ms"] >= 0

        result2 = llm_client.chat_messages(messages, use_cache=True, cache_ttl=3600)
        assert result2["success"] is True
        assert result2.get("cached") is True
        assert result2["content"] == result1["content"]
        assert result2["latency_ms"] == 0
        assert result2["usage"]["total_tokens"] == 15

    def test_cache_disabled(self, llm_client):
        """关闭缓存时不写入也不命中."""
        messages = [{"role": "user", "content": "no cache"}]

        result1 = llm_client.chat_messages(messages, use_cache=False)
        assert result1.get("cached") is False

        result2 = llm_client.chat_messages(messages, use_cache=False)
        assert result2.get("cached") is False
        assert result2["latency_ms"] >= 0

        # 确认没有写入缓存
        assert llm_client.cache.stats()["total_entries"] == 0

    def test_cache_respects_temperature(self, llm_client):
        """温度不同应生成不同缓存键."""
        messages = [{"role": "user", "content": "temp test"}]

        r1 = llm_client.chat_messages(messages, temperature=0.5, use_cache=True)
        assert r1.get("cached") is False

        r2 = llm_client.chat_messages(messages, temperature=0.7, use_cache=True)
        assert r2.get("cached") is False

        r3 = llm_client.chat_messages(messages, temperature=0.5, use_cache=True)
        assert r3.get("cached") is True

    def test_cache_expiration(self, llm_client):
        """过期后不应命中."""
        messages = [{"role": "user", "content": "expire me"}]

        r1 = llm_client.chat_messages(messages, use_cache=True, cache_ttl=-1)
        assert r1.get("cached") is False

        r2 = llm_client.chat_messages(messages, use_cache=True, cache_ttl=3600)
        assert r2.get("cached") is False

    def test_chat_method_uses_cache(self, llm_client):
        """chat() 方法默认启用缓存."""
        r1 = llm_client.chat("system", "user prompt", cache_ttl=7200)
        assert r1["success"] is True
        assert r1.get("cached") is False

        r2 = llm_client.chat("system", "user prompt", cache_ttl=7200)
        assert r2.get("cached") is True

    def test_cache_stats(self, llm_client):
        """缓存统计正确."""
        messages = [{"role": "user", "content": "stats"}]
        llm_client.chat_messages(messages, use_cache=True)

        stats = llm_client.cache.stats()
        assert stats["total_entries"] == 1
        assert stats["total_hits"] == 0

        llm_client.chat_messages(messages, use_cache=True)
        stats = llm_client.cache.stats()
        assert stats["total_hits"] == 1

    def test_failed_request_not_cached(self, llm_client, monkeypatch):
        """请求失败时不应写入缓存."""
        error_response = MagicMock()
        error_response.status_code = 500
        error_response.text = "Internal Server Error"
        error_response.raise_for_status.side_effect = Exception("boom")
        monkeypatch.setattr(llm_client._client, "post", lambda url, json: error_response)

        messages = [{"role": "user", "content": "fail"}]
        result = llm_client.chat_messages(messages, use_cache=True)
        assert result["success"] is False
        assert llm_client.cache.stats()["total_entries"] == 0
