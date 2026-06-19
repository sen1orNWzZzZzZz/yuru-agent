"""
用户画像自动总结 Agent
根据用户的每一次规划请求和最终行程，更新/完善用户画像。
"""
import json
import logging

from app.auth import get_user_profile, parse_json_field, serialize_json_field
from app.db.database import execute, get_db_connection, query_one
from app.integrations.llm_client import LLMClient

logger = logging.getLogger(__name__)

PROFILE_UPDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "interests": {
            "type": "array",
            "items": {"type": "string"},
            "description": "从本次规划中提炼出的兴趣标签，如 ['摄影', '美食', '历史']",
        },
        "pace": {
            "type": "string",
            "enum": ["slow", "relaxed", "balanced", "intensive"],
            "description": "用户偏好的旅行节奏",
        },
        "budget_range": {
            "type": "integer",
            "description": "人均单次出行预算参考（元）",
        },
        "must_visit_tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "用户明显偏好的景点/体验类型",
        },
        "avoid_tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "用户希望避免的内容",
        },
        "summary": {
            "type": "string",
            "description": "一段自然语言描述，总结该用户的旅行偏好",
        },
    },
}


class ProfileSummarizerAgent:
    """基于 LLM 或规则，自动总结并更新用户画像"""

    def __init__(self):
        self.llm = LLMClient()
        self.use_llm = self.llm.is_available()

    def summarize(self, user_id: int, request_context: dict, itinerary: dict) -> dict:
        """
        总结用户偏好并更新 user_profiles。
        返回更新的字段。
        """
        try:
            updates = self._generate_updates(user_id, request_context, itinerary)
            self._apply_updates(user_id, updates)
            return updates
        except Exception as e:
            logger.error(f"[ProfileSummarizer] 更新画像失败: {e}")
            return {}

    def _generate_updates(self, user_id: int, request_context: dict, itinerary: dict) -> dict:
        profile = get_user_profile(user_id) or {}
        existing_summary = profile.get("llm_summary") or ""

        if not self.use_llm:
            return self._rule_based_update(profile, request_context, itinerary)

        system_prompt = """你是用户画像分析助手。请根据用户的旅行规划请求和生成的行程，总结用户的旅行偏好。
只输出 JSON，不要 markdown 代码块。"""

        user_prompt = f"""【本次请求】
{json.dumps(request_context, ensure_ascii=False)}

【生成行程】
{json.dumps(itinerary, ensure_ascii=False)[:1500]}

【已有画像摘要】
{existing_summary or '无'}

请按 JSON Schema 输出对用户画像的更新建议。如果无法判断某个字段，请返回空数组或空字符串。"""

        result = self.llm.chat_structured(
            system_prompt, user_prompt,
            response_schema=PROFILE_UPDATE_SCHEMA,
            temperature=0.4, max_tokens=1200,
            use_cache=False,
        )
        if result.get("success") and result.get("data"):
            data = result["data"]
            return {
                "interests": data.get("interests") or self._extract_interests(request_context),
                "pace": data.get("pace") or request_context.get("pace", "balanced"),
                "budget_range": data.get("budget_range") or request_context.get("budget"),
                "must_visit_tags": data.get("must_visit_tags") or self._split_tags(request_context.get("must_visit", "")),
                "avoid_tags": data.get("avoid_tags") or self._split_tags(request_context.get("avoid", "")),
                "llm_summary": data.get("summary", ""),
            }
        return self._rule_based_update(profile, request_context, itinerary)

    def _rule_based_update(self, profile: dict, request_context: dict, itinerary: dict) -> dict:
        """LLM 不可用时使用的简单规则更新"""
        interests = parse_json_field(profile.get("interests"), [])
        new_interests = self._extract_interests(request_context)
        interests = list(dict.fromkeys(interests + new_interests))[:20]

        must_visit = parse_json_field(profile.get("must_visit_tags"), [])
        must_visit = list(dict.fromkeys(must_visit + self._split_tags(request_context.get("must_visit", ""))))[:20]

        avoid = parse_json_field(profile.get("avoid_tags"), [])
        avoid = list(dict.fromkeys(avoid + self._split_tags(request_context.get("avoid", ""))))[:20]

        summary_parts = [f"偏好目的地：{request_context.get('destination', '')}",
                         f"出行人数：{request_context.get('travelers', '')}",
                         f"预算：{request_context.get('budget', '未设定')}",
                         f"节奏：{request_context.get('pace', request_context.get('style', 'balanced'))}",]
        summary = (profile.get("llm_summary") or "") + "\n" + "；".join(summary_parts)
        summary = summary.strip()[-2000:]

        return {
            "interests": interests,
            "pace": request_context.get("pace") or profile.get("pace") or request_context.get("style", "balanced"),
            "budget_range": request_context.get("budget") or profile.get("budget_range"),
            "must_visit_tags": must_visit,
            "avoid_tags": avoid,
            "llm_summary": summary,
        }

    def _apply_updates(self, user_id: int, updates: dict) -> None:
        conn = get_db_connection()
        try:
            exists = query_one(conn, "SELECT id FROM user_profiles WHERE user_id = ?", (user_id,))
            if exists:
                execute(conn, """
                    UPDATE user_profiles SET
                        interests = COALESCE(?, interests),
                        pace = COALESCE(?, pace),
                        budget_range = COALESCE(?, budget_range),
                        must_visit_tags = COALESCE(?, must_visit_tags),
                        avoid_tags = COALESCE(?, avoid_tags),
                        llm_summary = COALESCE(?, llm_summary),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                """, (
                    serialize_json_field(updates.get("interests")),
                    updates.get("pace"),
                    updates.get("budget_range"),
                    serialize_json_field(updates.get("must_visit_tags")),
                    serialize_json_field(updates.get("avoid_tags")),
                    updates.get("llm_summary"),
                    user_id,
                ))
            else:
                execute(conn, """
                    INSERT INTO user_profiles
                    (user_id, interests, pace, budget_range, must_visit_tags, avoid_tags, llm_summary)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    user_id,
                    serialize_json_field(updates.get("interests")),
                    updates.get("pace"),
                    updates.get("budget_range"),
                    serialize_json_field(updates.get("must_visit_tags")),
                    serialize_json_field(updates.get("avoid_tags")),
                    updates.get("llm_summary"),
                ))
        finally:
            conn.close()

    @staticmethod
    def _extract_interests(context: dict) -> list[str]:
        interests = []
        if context.get("interests"):
            interests.extend([x.strip() for x in str(context["interests"]).split(",") if x.strip()])
        if context.get("style"):
            interests.append(str(context["style"]))
        return list(dict.fromkeys(interests))

    @staticmethod
    def _split_tags(text: str | None) -> list[str]:
        if not text:
            return []
        return [x.strip() for x in str(text).split(",") if x.strip()]
