"""
外部API配置管理模块
统一管理天气API、地图API、LLM API的配置读取
"""
import json
import logging
from typing import Any

from app.db.database import get_db_connection, query_all, query_one

logger = logging.getLogger(__name__)


def _mask_key(key: str | None) -> str:
    """对 API Key 进行脱敏展示：保留前 8 位和后 4 位，中间用 **** 代替。"""
    if not key:
        return ""
    if len(key) <= 12:
        return "*" * len(key)
    return f"{key[:8]}****{key[-4:]}"


def _is_valid_key(key: str | None) -> bool:
    """过滤掉明显无效或占位符的 API Key。"""
    if not key:
        return False
    k = str(key).strip()
    if len(k) < 10 or "your-" in k.lower() or "placeholder" in k.lower() or "example" in k.lower():
        return False
    return True


class IntegrationConfig:
    """
    外部服务集成配置管理器
    从数据库读取各类API配置，支持多provider切换
    """

    @staticmethod
    def get_llm_config() -> dict[str, Any] | None:
        """
        获取当前激活的LLM配置
        Returns: {"api_key", "base_url", "model_name", "temperature", "max_tokens", "timeout", "use_llm"}
        """
        conn = get_db_connection()
        try:
            config = query_one(
                conn,
                """SELECT api_key, base_url, model_name, temperature,
                          max_tokens, timeout, use_llm
                   FROM llm_configs WHERE is_active = 1 LIMIT 1"""
            )
            if config:
                logger.info(f"[Config] LLM配置加载: {config['model_name']} @ {config['base_url']}")
            return config
        finally:
            conn.close()

    @staticmethod
    def get_llm_configs() -> list[dict[str, Any]]:
        """获取所有 LLM 配置列表（API Key 已脱敏）"""
        conn = get_db_connection()
        try:
            configs = query_all(conn, "SELECT * FROM llm_configs ORDER BY id")
            for c in configs:
                c["api_key"] = _mask_key(c.get("api_key"))
            return configs
        finally:
            conn.close()

    @staticmethod
    def get_weather_config() -> dict[str, Any] | None:
        """
        获取天气API配置
        Returns: {"provider", "api_key", "base_url", "extra_params"}
        """
        conn = get_db_connection()
        try:
            config = query_one(
                conn,
                """SELECT provider, api_key, base_url, extra_params
                   FROM api_configs WHERE config_type = 'weather' AND is_active = 1 LIMIT 1"""
            )
            if config and config.get("extra_params"):
                try:
                    config["extra_params"] = json.loads(config["extra_params"])
                except json.JSONDecodeError:
                    config["extra_params"] = {}
            if config:
                logger.info(f"[Config] 天气API配置: {config['provider']}")
            return config
        finally:
            conn.close()

    @staticmethod
    def get_map_config() -> dict[str, Any] | None:
        """
        获取地图API配置（WebService API，用于后端 POI 搜索/距离计算）
        Returns: {"provider", "api_key", "base_url", "extra_params"}
        """
        conn = get_db_connection()
        try:
            # 优先取激活配置
            config = query_one(
                conn,
                """SELECT provider, api_key, base_url, extra_params
                   FROM api_configs WHERE config_type = 'map' AND is_active = 1 LIMIT 1"""
            )
            # 没有激活配置时，回退到最近更新的有效配置
            if not config:
                config = query_one(
                    conn,
                    """SELECT provider, api_key, base_url, extra_params
                       FROM api_configs WHERE config_type = 'map'
                       ORDER BY updated_at DESC, id DESC LIMIT 1"""
                )
            if config and config.get("extra_params"):
                try:
                    config["extra_params"] = json.loads(config["extra_params"])
                except json.JSONDecodeError:
                    config["extra_params"] = {}
            if config:
                logger.info(f"[Config] 地图API配置: {config['provider']}")
            return config
        finally:
            conn.close()

    @staticmethod
    def get_map_js_config() -> dict[str, Any] | None:
        """
        获取前端地图 JS API 配置（高德 JS API / 百度 JS API）
        Returns: {"provider", "api_key"}
        """
        conn = get_db_connection()
        try:
            config = query_one(
                conn,
                """SELECT provider, api_key
                   FROM api_configs
                   WHERE config_type = 'map_js' AND provider = 'amap_js' AND is_active = 1
                   LIMIT 1"""
            )
            if not config:
                config = query_one(
                    conn,
                    """SELECT provider, api_key
                       FROM api_configs
                       WHERE config_type = 'map_js' AND provider = 'amap_js'
                       ORDER BY updated_at DESC, id DESC LIMIT 1"""
                )
            if config and _is_valid_key(config.get("api_key")):
                logger.info("[Config] 地图 JS API 配置已加载")
                return config
            return None
        finally:
            conn.close()

    @staticmethod
    def get_all_api_configs(config_type: str | None = None) -> list:
        """获取所有API配置列表"""
        conn = get_db_connection()
        try:
            from app.db.database import query_all
            if config_type:
                configs = query_all(
                    conn,
                    "SELECT * FROM api_configs WHERE config_type = ? ORDER BY id",
                    (config_type,)
                )
            else:
                configs = query_all(conn, "SELECT * FROM api_configs ORDER BY id")
            # 脱敏API Key
            for c in configs:
                if c.get("api_key") and len(c["api_key"]) > 12:
                    c["api_key"] = c["api_key"][:8] + "****" + c["api_key"][-4:]
                if c.get("extra_params"):
                    try:
                        c["extra_params"] = json.loads(c["extra_params"])
                    except json.JSONDecodeError:
                        pass
            return configs
        finally:
            conn.close()

    @staticmethod
    def save_api_config(config_type: str, provider: str, api_key: str,
                        base_url: str = "", extra_params: str = "", config_id: int | None = None) -> bool:
        """保存或更新API配置"""
        conn = get_db_connection()
        try:
            from app.db.database import execute
            if config_id:
                execute(conn, """
                    UPDATE api_configs SET provider = ?, api_key = ?, base_url = ?,
                           extra_params = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (provider, api_key, base_url, extra_params, config_id))
            else:
                # 先取消同类型的激活状态
                execute(conn, "UPDATE api_configs SET is_active = 0 WHERE config_type = ?", (config_type,))
                execute(conn, """
                    INSERT INTO api_configs (config_type, provider, api_key, base_url, extra_params, is_active)
                    VALUES (?, ?, ?, ?, ?, 1)
                """, (config_type, provider, api_key, base_url, extra_params))
            return True
        except Exception as e:
            logger.error(f"[Config] 保存配置失败: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def test_connection(config_type: str) -> dict[str, Any]:
        """测试各类API连接"""
        if config_type == "weather":
            from app.integrations.weather import WeatherClient
            client = WeatherClient()
            return client.test_connection()
        elif config_type == "map":
            from app.integrations.map import MapClient
            client = MapClient()
            return client.test_connection()
        elif config_type == "llm":
            from app.integrations.llm_client import LLMClient
            client = LLMClient()
            return client.test_connection()
        return {"success": False, "message": "未知的配置类型"}
