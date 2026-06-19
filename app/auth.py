"""
极简本地认证模块
- 用户名/密码 + Session Cookie
- 不强制邮箱验证，适合 V3 原型快速使用
"""
import json
import os
from typing import Any

import bcrypt
from fastapi import HTTPException, Request

from app.db.database import get_db_connection, query_one


def verify_password(plain_password: str, hashed_password: str | None) -> bool:
    if not hashed_password:
        return False
    if isinstance(hashed_password, str):
        hashed_password = hashed_password.encode("utf-8")
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password)


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def get_user_by_username(username: str) -> dict | None:
    conn = get_db_connection()
    try:
        return query_one(conn, "SELECT * FROM users WHERE username = ?", (username,))
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> dict | None:
    conn = get_db_connection()
    try:
        return query_one(conn, "SELECT * FROM users WHERE id = ?", (user_id,))
    finally:
        conn.close()


def get_user_profile(user_id: int) -> dict | None:
    conn = get_db_connection()
    try:
        return query_one(conn, "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,))
    finally:
        conn.close()


def get_current_user(request: Request) -> dict | None:
    """从 Session 读取当前登录用户"""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return get_user_by_id(int(user_id))


def require_user(request: Request) -> dict:
    """依赖注入：要求已登录，否则抛 401"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def parse_json_field(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def serialize_json_field(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except json.JSONDecodeError:
            return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def profile_to_public(profile: dict | None) -> dict:
    """把 user_profiles 行转为前端友好格式"""
    if not profile:
        return {}
    return {
        "display_name": profile.get("display_name"),
        "age_group": profile.get("age_group"),
        "companion_type": profile.get("companion_type"),
        "interests": parse_json_field(profile.get("interests"), []),
        "pace": profile.get("pace"),
        "budget_range": profile.get("budget_range"),
        "dietary_restrictions": parse_json_field(profile.get("dietary_restrictions"), []),
        "accessibility_needs": profile.get("accessibility_needs"),
        "preferred_transport": profile.get("preferred_transport"),
        "home_city": profile.get("home_city"),
        "must_visit_tags": parse_json_field(profile.get("must_visit_tags"), []),
        "avoid_tags": parse_json_field(profile.get("avoid_tags"), []),
        "llm_summary": profile.get("llm_summary"),
    }


def merge_profile_with_request(profile: dict | None, req_data: dict) -> dict:
    """
    把用户画像默认值与本次请求参数合并，请求参数优先级更高。
    返回一个新的偏好字典，供 Planner 使用。
    """
    p = profile_to_public(profile)
    merged = {}

    # 行程基础信息：请求为准
    merged["destination"] = req_data.get("destination", "")
    merged["days"] = req_data.get("days", 3)
    merged["travelers"] = req_data.get("travelers", 2)
    merged["origin"] = req_data.get("origin", "上海")

    # 预算：请求 > 画像 > 默认
    merged["budget"] = req_data.get("budget") or p.get("budget_range")

    # 风格/节奏：请求 > 画像 > 默认 balanced
    merged["style"] = req_data.get("style") or p.get("pace") or "balanced"
    merged["pace"] = req_data.get("pace") or p.get("pace") or "balanced"

    # 兴趣与标签：请求与画像合并，请求在前
    merged["interests"] = req_data.get("interests") or ", ".join(p.get("interests", []))
    merged["must_visit"] = req_data.get("must_visit") or ", ".join(p.get("must_visit_tags", []))
    merged["avoid"] = req_data.get("avoid") or ", ".join(p.get("avoid_tags", []))
    merged["special_needs"] = req_data.get("special_needs") or ""
    merged["season"] = req_data.get("season") or ""

    # 画像中其他可用于推荐的字段
    merged["companion_type"] = p.get("companion_type", "")
    merged["dietary_restrictions"] = p.get("dietary_restrictions", [])
    merged["accessibility_needs"] = p.get("accessibility_needs", "")
    merged["preferred_transport"] = p.get("preferred_transport", "")
    merged["home_city"] = p.get("home_city", "")
    merged["llm_summary"] = p.get("llm_summary", "")

    return merged
