"""
LLM通用客户端 - OpenAI格式
支持OpenAI / SiliconFlow / Azure / 自托管vLLM等
"""
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.integrations.config_manager import IntegrationConfig

logger = logging.getLogger(__name__)


class PromptCache:
    """
    LLM Prompt 缓存

    基于请求参数（model + messages + temperature + max_tokens）的 SHA256 哈希生成缓存键，
    将 LLM 响应持久化到 SQLite，支持 TTL 过期和命中统计。

    设计要点：
    - 使用 JSON sort_keys + ensure_ascii 保证相同语义请求生成相同 key
    - 命中时直接返回缓存内容，latency_ms=0，避免重复调用 API
    - 不同业务可设置不同 TTL（如天气 1h、行程生成 7d）
    """

    def __init__(self, get_conn=None):
        # 延迟导入，避免在模块加载阶段建立数据库连接
        from app.db.database import get_db_connection

        self.get_conn = get_conn or get_db_connection

    @staticmethod
    def make_key(model: str, messages: list[dict], temperature: float, max_tokens: int) -> str:
        """生成缓存键"""
        canonical = json.dumps(
            {
                "model": model,
                "messages": messages,
                "temperature": round(temperature, 6),
                "max_tokens": max_tokens,
            },
            sort_keys=True,
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def get(self, cache_key: str) -> dict | None:
        """
        查询缓存，命中时自动更新 hit_count
        Returns: {"content": str, "usage": dict} or None
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self.get_conn()
        try:
            row = conn.execute(
                """
                SELECT response_content, usage_json, latency_ms, expires_at
                FROM llm_cache
                WHERE cache_key = ?
                """,
                (cache_key,),
            ).fetchone()
            if not row:
                return None
            if row["expires_at"] and row["expires_at"] < now:
                return None

            conn.execute(
                """
                UPDATE llm_cache
                SET hit_count = hit_count + 1, last_hit_at = ?, updated_at = ?
                WHERE cache_key = ?
                """,
                (now, now, cache_key),
            )
            conn.commit()
            return {
                "content": row["response_content"],
                "usage": json.loads(row["usage_json"]) if row["usage_json"] else {},
                "latency_ms": row["latency_ms"] or 0,
            }
        finally:
            conn.close()

    def set(
        self,
        cache_key: str,
        model: str,
        messages: list[dict],
        response_content: str,
        usage: dict,
        ttl_seconds: int = 86400,
        latency_ms: int = 0,
    ) -> None:
        """写入缓存"""
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
        now_iso = now.isoformat()
        conn = self.get_conn()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO llm_cache
                (cache_key, model, messages_json, response_content, usage_json,
                 latency_ms, hit_count, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_key,
                    model,
                    json.dumps(messages, ensure_ascii=True),
                    response_content,
                    json.dumps(usage, ensure_ascii=True),
                    latency_ms,
                    0,
                    now_iso,
                    now_iso,
                    expires_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def stats(self) -> dict[str, int | float | None]:
        """返回缓存统计"""
        now = datetime.now(timezone.utc).isoformat()
        conn = self.get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) AS cnt FROM llm_cache").fetchone()["cnt"]
            total_hits = conn.execute(
                "SELECT COALESCE(SUM(hit_count), 0) AS total FROM llm_cache"
            ).fetchone()["total"]
            expired = conn.execute(
                "SELECT COUNT(*) AS cnt FROM llm_cache WHERE expires_at < ?", (now,)
            ).fetchone()["cnt"]
            avg_latency = conn.execute(
                "SELECT AVG(latency_ms) AS avg FROM llm_cache"
            ).fetchone()["avg"]
            return {
                "total_entries": total,
                "total_hits": total_hits,
                "expired_entries": expired,
                "avg_latency_ms": avg_latency,
            }
        finally:
            conn.close()

    def clear_expired(self) -> int:
        """清理过期缓存，返回删除条数"""
        now = datetime.now(timezone.utc).isoformat()
        conn = self.get_conn()
        try:
            cursor = conn.execute("DELETE FROM llm_cache WHERE expires_at < ?", (now,))
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()


class TokenEstimator:
    """
    调用前 Token 估算器（基于 tiktoken）

    - 对 OpenAI/兼容模型：使用 tiktoken 精确估算 prompt token
    - 对 Claude/其他模型：tiktoken 只能近似，会标注 method=tiktoken-cl100k_base
    - 若 tiktoken 不可用，回退到字符数近似
    """

    ENCODING_MAP: dict[str, str] = {
        "gpt-4o": "o200k_base",
        "gpt-4o-mini": "o200k_base",
        "o1": "o200k_base",
        "o3": "o200k_base",
        "gpt-4-turbo": "cl100k_base",
        "gpt-4": "cl100k_base",
        "gpt-3.5-turbo": "cl100k_base",
    }
    FALLBACK_ENCODING = "cl100k_base"

    @classmethod
    def _encoding_name_for_model(cls, model: str | None) -> str:
        if not model:
            return cls.FALLBACK_ENCODING
        model_lower = model.lower()
        for prefix, enc in cls.ENCODING_MAP.items():
            if prefix in model_lower:
                return enc
        return cls.FALLBACK_ENCODING

    @classmethod
    def estimate(
        cls,
        messages: list[dict],
        max_completion_tokens: int | None = None,
        model: str | None = None,
    ) -> dict[str, int | str]:
        """
        估算 prompt / completion token 数。

        prompt 估算按 OpenAI chat format 经验公式：
        - 每条 message 固定开销约 4 tokens（role + 分隔符）
        - 内容按 encoding 编码后 token 数
        - 最后 +2 表示 assistant priming
        """
        try:
            import tiktoken

            enc_name = cls._encoding_name_for_model(model)
            encoding = tiktoken.get_encoding(enc_name)
        except Exception as e:
            logger.warning(f"[TokenEstimator] tiktoken 不可用: {e}，使用字符数回退估算")
            return cls._fallback_estimate(messages, max_completion_tokens, model)

        prompt_tokens = 0
        for msg in messages:
            content = msg.get("content") or ""
            if isinstance(content, list):
                content = " ".join(str(part) for part in content)
            prompt_tokens += 4  # role + delimiters
            prompt_tokens += len(encoding.encode(content, disallowed_special=()))
        prompt_tokens += 2  # assistant priming

        completion_tokens = max_completion_tokens or 0
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "model": model or "",
            "method": f"tiktoken-{enc_name}",
        }

    @classmethod
    def _fallback_estimate(
        cls,
        messages: list[dict],
        max_completion_tokens: int | None,
        model: str | None,
    ) -> dict[str, int | str]:
        prompt_tokens = 0
        for msg in messages:
            content = msg.get("content") or ""
            if isinstance(content, list):
                content = " ".join(str(part) for part in content)
            # 中文约占 1/3 个 tiktoken 等效 token，英文约占 1/4
            prompt_tokens += max(1, len(content) // 3)
        completion_tokens = max_completion_tokens or 0
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "model": model or "",
            "method": "char-fallback",
        }


class LLMClient:
    """
    通用LLM客户端 - OpenAI Chat Completions格式
    自动从数据库读取配置，支持重试和降级
    """

    def __init__(self, config: dict | None = None):
        """
        初始化LLM客户端
        Args:
            config: 可选，直接传入配置字典。不传则从数据库读取
        """
        self.config = config or IntegrationConfig.get_llm_config()
        self._client: httpx.Client | None = None
        self.cache = PromptCache()
        self._init_http_client()

    def _init_http_client(self):
        """初始化HTTP客户端"""
        if self.config and self.config.get("api_key"):
            timeout = self.config.get("timeout", 60)
            self._client = httpx.Client(
                timeout=httpx.Timeout(timeout, connect=10),
                headers={
                    "Authorization": f"Bearer {self.config['api_key']}",
                    "Content-Type": "application/json",
                },
            )

    def is_available(self) -> bool:
        """检查LLM是否可用"""
        return (self.config is not None and
                self.config.get("api_key") and
                self.config.get("use_llm", True))

    def chat(self, system_prompt: str, user_prompt: str,
             temperature: float | None = None,
             max_tokens: int | None = None,
             use_cache: bool = True,
             cache_ttl: int = 86400) -> dict[str, Any]:
        """
        调用LLM进行对话
        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            temperature: 覆盖默认温度
            max_tokens: 覆盖默认max_tokens
            use_cache: 是否使用 Prompt 缓存
            cache_ttl: 缓存过期时间（秒），默认 24 小时
        Returns:
            {"success", "content", "usage", "latency_ms", "model", "cached"}
        """
        if not self.is_available():
            return {"success": False, "content": "", "error": "LLM未配置或未启用"}

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self.chat_messages(messages, temperature, max_tokens, use_cache, cache_ttl)

    def chat_messages(self, messages: list[dict[str, str]],
                      temperature: float | None = None,
                      max_tokens: int | None = None,
                      use_cache: bool = True,
                      cache_ttl: int = 86400,
                      extra_payload: dict | None = None) -> dict[str, Any]:
        """
        发送多轮对话请求
        Args:
            messages: OpenAI 格式的消息列表
            temperature: 覆盖默认温度
            max_tokens: 覆盖默认 max_tokens
            use_cache: 是否使用 Prompt 缓存
            cache_ttl: 缓存过期时间（秒）
            extra_payload: 额外请求参数（如 response_format）
        """
        if not self.is_available():
            return {"success": False, "content": "", "error": "LLM未配置或未启用"}

        base_url = self.config["base_url"].rstrip("/")
        url = f"{base_url}/chat/completions"

        payload = {
            "model": self.config["model_name"],
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.get("temperature", 0.7),
            "max_tokens": max_tokens if max_tokens is not None else self.config.get("max_tokens", 4096),
        }
        if extra_payload:
            payload.update(extra_payload)

        # 调用前估算 token 消耗
        estimated_usage = TokenEstimator.estimate(
            messages, payload["max_tokens"], payload["model"]
        )

        def _inject_estimate(usage: dict) -> dict:
            usage.setdefault("estimated_prompt_tokens", estimated_usage["prompt_tokens"])
            usage.setdefault("estimated_completion_tokens", estimated_usage["completion_tokens"])
            usage.setdefault("estimate_method", estimated_usage["method"])
            return usage

        # 1. 查询 Prompt 缓存
        cache_key = None
        if use_cache:
            cache_key = PromptCache.make_key(
                payload["model"], messages, payload["temperature"], payload["max_tokens"]
            )
            cached = self.cache.get(cache_key)
            if cached is not None:
                logger.info(f"[LLM] 缓存命中: key={cache_key[:12]}...")
                return {
                    "success": True,
                    "content": cached["content"],
                    "usage": _inject_estimate(cached["usage"].copy()),
                    "latency_ms": 0,
                    "model": payload["model"],
                    "cached": True,
                }

        # 2. 调用 LLM API
        start_time = time.time()
        try:
            logger.info(
                f"[LLM] 请求: model={payload['model']}, messages={len(messages)}, "
                f"预估 prompt={estimated_usage['prompt_tokens']} tokens"
            )
            response = self._client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            latency_ms = int((time.time() - start_time) * 1000)

            choice = data["choices"][0]
            content = choice["message"].get("content", "")
            usage = _inject_estimate(data.get("usage", {}).copy())

            logger.info(f"[LLM] 响应: latency={latency_ms}ms, "
                       f"tokens={usage.get('total_tokens', 0)}")

            # 3. 写入缓存
            if use_cache and cache_key:
                self.cache.set(
                    cache_key,
                    payload["model"],
                    messages,
                    content,
                    usage,
                    ttl_seconds=cache_ttl,
                    latency_ms=latency_ms,
                )

            return {
                "success": True,
                "content": content,
                "usage": usage,
                "latency_ms": latency_ms,
                "model": data.get("model", payload["model"]),
                "cached": False,
            }

        except httpx.TimeoutException:
            logger.error("[LLM] 请求超时")
            return {"success": False, "content": "", "error": "请求超时",
                    "usage": _inject_estimate({})}
        except httpx.HTTPStatusError as e:
            err_text = e.response.text[:200]
            logger.error(f"[LLM] HTTP错误 {e.response.status_code}: {err_text}")
            return {"success": False, "content": "", "error": f"API错误({e.response.status_code}): {err_text}",
                    "usage": _inject_estimate({})}
        except Exception as e:
            logger.error(f"[LLM] 异常: {e}")
            return {"success": False, "content": "", "error": str(e),
                    "usage": _inject_estimate({})}

    def chat_with_retry(self, messages: list[dict[str, str]],
                        max_retries: int = 2,
                        temperature: float | None = None,
                        max_tokens: int | None = None,
                        use_cache: bool = True,
                        cache_ttl: int = 86400) -> dict[str, Any]:
        """带重试的对话请求"""
        last_error = None
        for attempt in range(max_retries + 1):
            result = self.chat_messages(messages, temperature, max_tokens, use_cache, cache_ttl)
            if result["success"]:
                return result
            last_error = result.get("error", "")
            if attempt < max_retries:
                wait_time = (attempt + 1) * 2
                logger.warning(f"[LLM] 第{attempt+1}次失败，{wait_time}s后重试: {last_error}")
                time.sleep(wait_time)
        return {"success": False, "content": "", "error": f"重试{max_retries+1}次后仍失败: {last_error}"}

    def chat_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        use_cache: bool = True,
        cache_ttl: int = 86400,
    ) -> dict[str, Any]:
        """
        调用 LLM 并强制返回结构化 JSON。

        策略：
        1. 将 JSON Schema 拼接到 user_prompt 中，让模型明确输出格式
        2. 如果配置支持，额外传入 response_format={"type": "json_object"}
        3. 对返回内容使用 extract_json 提取，失败时返回错误

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            response_schema: JSON Schema 字典，描述期望输出结构
            temperature/max_tokens: 常规参数
            use_cache/cache_ttl: 复用 chat_messages 的缓存能力
        Returns:
            {"success": True, "data": dict, "usage": ..., "latency_ms": ..., "model": ...}
            或 {"success": False, "error": str}
        """
        if not self.is_available():
            return {"success": False, "content": "", "error": "LLM未配置或未启用"}

        enhanced_user_prompt = user_prompt
        if response_schema:
            schema_json = json.dumps(response_schema, ensure_ascii=False, indent=2)
            enhanced_user_prompt += (
                f"\n\n请严格按以下 JSON Schema 输出，只返回 JSON，不要 markdown 代码块：\n"
                f"{schema_json}"
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": enhanced_user_prompt},
        ]

        extra_payload = None
        supports_response_format = self.config.get("supports_response_format", True)
        if supports_response_format:
            extra_payload = {"response_format": {"type": "json_object"}}

        result = self.chat_messages(
            messages, temperature, max_tokens, use_cache, cache_ttl, extra_payload
        )
        if not result["success"]:
            return result

        parsed = self.extract_json(result["content"])
        if parsed is None:
            return {
                "success": False,
                "content": result["content"],
                "error": "无法解析为JSON",
            }

        return {
            "success": True,
            "data": parsed,
            "usage": result.get("usage", {}),
            "latency_ms": result.get("latency_ms", 0),
            "model": result.get("model", ""),
            "cached": result.get("cached", False),
        }

    def stream_chat(self, messages: list[dict[str, str]],
                    temperature: float | None = None,
                    max_tokens: int | None = None):
        """
        流式调用 LLM，按 token 块生成内容。

        采用 OpenAI 标准的 Server-Sent Events 格式，逐行解析 data: {...} 并产出
        content delta。适用于需要实时打字机效果的场景。

        部分 provider（如 OpenAI）会在流末尾发送 usage 信息，本方法会收集并在
        流正常结束时产出 {"usage": {...}} 事件。

        Yields:
            {"chunk": str} 或 {"usage": dict} 或 {"error": str}
        """
        if not self.is_available():
            yield {"error": "LLM未配置或未启用"}
            return

        base_url = self.config["base_url"].rstrip("/")
        url = f"{base_url}/chat/completions"

        payload = {
            "model": self.config["model_name"],
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.get("temperature", 0.7),
            "max_tokens": max_tokens if max_tokens is not None else self.config.get("max_tokens", 4096),
            "stream": True,
        }

        estimated_usage = TokenEstimator.estimate(
            messages, payload["max_tokens"], payload["model"]
        )

        def _inject_stream_estimate(usage: dict | None) -> dict:
            usage = usage.copy() if usage else {}
            usage.setdefault("estimated_prompt_tokens", estimated_usage["prompt_tokens"])
            usage.setdefault("estimated_completion_tokens", estimated_usage["completion_tokens"])
            usage.setdefault("estimate_method", estimated_usage["method"])
            return usage

        try:
            logger.info(
                f"[LLM Stream] 请求: model={payload['model']}, messages={len(messages)}, "
                f"预估 prompt={estimated_usage['prompt_tokens']} tokens"
            )
            usage = None
            with self._client.stream("POST", url, json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    text = line.decode("utf-8") if isinstance(line, bytes) else line
                    text = text.strip()
                    if not text.startswith("data: "):
                        continue
                    data = text[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)

                        # 收集 usage（通常出现在最后一个空 choices 的 chunk）
                        if chunk.get("usage"):
                            usage = chunk["usage"]

                        choices = chunk.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield {"chunk": content}
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

            if usage:
                yield {"usage": _inject_stream_estimate(usage)}
        except httpx.TimeoutException:
            logger.error("[LLM Stream] 请求超时")
            yield {"error": "请求超时"}
        except httpx.HTTPStatusError as e:
            err_text = e.response.text[:200]
            logger.error(f"[LLM Stream] HTTP错误 {e.response.status_code}: {err_text}")
            yield {"error": f"API错误({e.response.status_code}): {err_text}"}
        except Exception as e:
            logger.error(f"[LLM Stream] 异常: {e}")
            yield {"error": str(e)}

    def test_connection(self) -> dict[str, Any]:
        """测试LLM连接"""
        if not self.is_available():
            return {"success": False, "message": "LLM未配置或未启用"}
        try:
            result = self.chat("你是一个测试助手", "请回复'连接成功'两个字，不要其他内容", max_tokens=50)
            if result["success"] and "连接成功" in result["content"]:
                return {"success": True, "message": "连接成功", "model": result["model"],
                        "latency_ms": result["latency_ms"]}
            elif result["success"]:
                return {"success": True, "message": f"连接成功(响应: {result['content'][:20]}...)",
                        "model": result["model"], "latency_ms": result["latency_ms"]}
            return {"success": False, "message": result.get("error", "未知错误")}
        except Exception as e:
            return {"success": False, "message": f"测试失败: {str(e)}"}

    def extract_json(self, content: str) -> dict | None:
        """从LLM响应中提取JSON"""
        # 去除markdown代码块
        cleaned = content.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # 尝试找JSON子串
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(cleaned[start:end+1])
                except json.JSONDecodeError:
                    pass
            return None
