"""SSE 流式接口与 LLM 流式调用测试."""

import json

import pytest

from app.integrations.llm_client import LLMClient


@pytest.fixture
def streaming_llm_client(memory_db, monkeypatch):
    """LLMClient，HTTP stream 被 mock 为逐 token 返回."""
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

    class MockStream:
        def __enter__(self):
            return self

        def __exit__(self, *args, **kwargs):
            return False

        def raise_for_status(self):
            pass

        def iter_lines(self):
            chunks = [
                b'data: {"choices":[{"delta":{"content":"Hello"}}]}',
                b'data: {"choices":[{"delta":{"content":" "}}]}',
                b'data: {"choices":[{"delta":{"content":"World"}}]}',
                b'data: [DONE]',
            ]
            yield from chunks

    monkeypatch.setattr(client._client, "stream", lambda *args, **kwargs: MockStream())
    return client


class TestStreamChat:
    """LLMClient.stream_chat 测试."""

    def test_stream_chat_yields_chunks(self, streaming_llm_client):
        chunks = list(streaming_llm_client.stream_chat([{"role": "user", "content": "hi"}]))
        contents = [c["chunk"] for c in chunks if "chunk" in c]
        assert contents == ["Hello", " ", "World"]

    def test_stream_chat_unavailable(self, memory_db):
        client = LLMClient(config={})
        chunks = list(client.stream_chat([{"role": "user", "content": "hi"}]))
        assert chunks == [{"error": "LLM未配置或未启用"}]

    def test_stream_chat_http_error(self, streaming_llm_client, monkeypatch):
        class ErrorStream:
            def __enter__(self):
                return self

            def __exit__(self, *args, **kwargs):
                return False

            def raise_for_status(self):
                raise RuntimeError("connection refused")

        monkeypatch.setattr(
            streaming_llm_client._client, "stream", lambda *args, **kwargs: ErrorStream()
        )
        chunks = list(streaming_llm_client.stream_chat([{"role": "user", "content": "hi"}]))
        assert len(chunks) == 1
        assert "error" in chunks[0]
        assert "connection refused" in chunks[0]["error"]


class TestPlanStreamEndpoint:
    """/api/v3/plan/stream 测试."""

    def test_stream_returns_sse(self, no_llm, sample_data, client):
        response = client.post(
            "/api/v3/plan/stream",
            json={
                "destination": "杭州",
                "days": 2,
                "travelers": 2,
                "origin": "上海",
                "style": "balanced",
            },
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

    def test_stream_events_sequence(self, no_llm, sample_data, client):
        response = client.post(
            "/api/v3/plan/stream",
            json={
                "destination": "杭州",
                "days": 2,
                "travelers": 2,
                "origin": "上海",
                "style": "balanced",
            },
        )

        events = []
        for chunk in response.text.split("\n\n"):
            if not chunk.startswith("data: "):
                continue
            payload = chunk[6:]
            if payload == "[DONE]":
                continue
            events.append(json.loads(payload))

        types = [e["type"] for e in events]
        assert types[0] == "start"
        assert types.count("agent_start") == 6
        assert types.count("agent_complete") == 6
        assert "itinerary_generating" in types
        assert types[-1] == "complete"

    def test_stream_complete_payload(self, no_llm, sample_data, client):
        response = client.post(
            "/api/v3/plan/stream",
            json={
                "destination": "杭州",
                "days": 2,
                "travelers": 2,
                "origin": "上海",
                "style": "balanced",
            },
        )

        complete_event = None
        for chunk in response.text.split("\n\n"):
            if not chunk.startswith("data: "):
                continue
            payload = chunk[6:]
            if payload == "[DONE]":
                continue
            event = json.loads(payload)
            if event["type"] == "complete":
                complete_event = event
                break

        assert complete_event is not None
        data = complete_event["data"]
        assert data["destination"] == "杭州"
        assert data["days"] == 2
        assert data["itinerary_id"] > 0
        assert "itinerary" in data
        assert "agent_results" in data
        assert "risk" in data
        assert data["llm_used"] is False

    def test_stream_saves_itinerary(self, no_llm, sample_data, client, db_conn):
        response = client.post(
            "/api/v3/plan/stream",
            json={
                "destination": "杭州",
                "days": 2,
                "travelers": 2,
                "origin": "上海",
                "style": "balanced",
            },
        )

        itinerary_id = None
        for chunk in response.text.split("\n\n"):
            if not chunk.startswith("data: "):
                continue
            payload = chunk[6:]
            if payload == "[DONE]":
                continue
            event = json.loads(payload)
            if event["type"] == "complete":
                itinerary_id = event["data"]["itinerary_id"]
                break

        row = db_conn.execute("SELECT * FROM itineraries WHERE id = ?", (itinerary_id,)).fetchone()
        assert row is not None
        assert row["destination"] == "杭州"
