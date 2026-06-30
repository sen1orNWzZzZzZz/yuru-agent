"""
地图API集成模块
支持多provider：高德地图(AMap) / 百度地图
提供POI搜索、地理编码、距离计算等功能
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.db.database import execute, get_db_connection, query_one
from app.integrations.config_manager import IntegrationConfig
from app.tracing import record_span

logger = logging.getLogger(__name__)


# POI 类型映射（简化版）
AMAP_TYPE_MAP = {
    "hotel": "100000",      # 住宿服务
    "restaurant": "050000", # 餐饮服务
    "attraction": "110000", # 风景名胜
}

BAIDU_TYPE_MAP = {
    "hotel": "酒店",
    "restaurant": "美食",
    "attraction": "旅游景点",
}


class MapClient:
    """
    地图服务客户端
    支持高德地图/百度地图，POI搜索和地理编码
    """

    DEFAULT_BASE_URLS = {
        "amap": "https://restapi.amap.com/v3",
        "baidu": "https://api.map.baidu.com",
    }

    def __init__(self):
        self.config = IntegrationConfig.get_map_config() or {}
        self.provider = self.config.get("provider")
        self.api_key = self.config.get("api_key")
        self.base_url = self.config.get("base_url") or self.DEFAULT_BASE_URLS.get(self.provider, "")
        self.extra_params = self.config.get("extra_params", {})
        self.client = httpx.Client(timeout=15)

    def _get_cached_poi(self, city: str, keywords: str, poi_type: str) -> list[dict] | None:
        """读取 POI 搜索结果缓存（7天有效）"""
        if not self.provider:
            return None
        try:
            conn = get_db_connection()
            try:
                row = query_one(
                    conn,
                    """
                    SELECT results_json, created_at FROM external_poi_cache
                    WHERE provider = ? AND city = ? AND keywords = ? AND poi_type = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (self.provider, city, keywords, poi_type),
                )
                if row:
                    created = row.get("created_at", "")
                    expiry = (datetime.now() - timedelta(days=7)).isoformat()
                    if created and created > expiry:
                        cached = json.loads(row["results_json"])
                        # 防止旧代码写入的空缓存一直命中
                        if cached:
                            return cached
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[Map] 读取 POI 缓存失败: {e}")
        return None

    def _set_cached_poi(self, city: str, keywords: str, poi_type: str, results: list[dict]) -> None:
        """写入 POI 搜索结果缓存"""
        if not self.provider:
            return
        try:
            conn = get_db_connection()
            try:
                execute(
                    conn,
                    """
                    INSERT OR REPLACE INTO external_poi_cache
                    (provider, city, keywords, poi_type, results_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (self.provider, city, keywords, poi_type, json.dumps(results, ensure_ascii=False)),
                )
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[Map] 写入 POI 缓存失败: {e}")

    def is_available(self) -> bool:
        if not self.config or not self.api_key:
            return False
        # 过滤掉明显是占位符的 Key
        key = str(self.api_key).strip()
        if len(key) < 10 or "your-" in key.lower() or "placeholder" in key.lower() or "example" in key.lower():
            return False
        return True

    def search_poi(self, city: str, keywords: str, poi_type: str = "", page_size: int = 10, raise_on_error: bool = False) -> list[dict]:
        """
        搜索POI（兴趣点）
        Args:
            city: 城市名称
            keywords: 搜索关键词
            poi_type: POI类型（如'酒店'、'景点'）
            page_size: 返回数量
            raise_on_error: 为 True 时外部调用失败抛出异常（用于测试连接）
        Returns:
            [{"name", "address", "location", "type", "tel"}, ...]
        """
        start_dt = datetime.now()
        status = "ok"
        error = None
        cached = False
        result_count = 0
        provider = self.provider or "none"

        try:
            if not self.is_available():
                return []

            cached_result = self._get_cached_poi(city, keywords, poi_type)
            if cached_result is not None:
                logger.info(f"[Map] POI 缓存命中: {city}/{keywords}/{poi_type}")
                cached = True
                result_count = len(cached_result)
                return cached_result

            results = []
            if self.provider == "amap":
                results = self._amap_search(city, keywords, poi_type, page_size)
            elif self.provider == "baidu":
                results = self._baidu_search(city, keywords, poi_type, page_size)
            if results:
                self._set_cached_poi(city, keywords, poi_type, results)
            else:
                logger.warning(f"[Map] {self.provider} 搜索返回空结果，不写入缓存: {city}/{keywords}/{poi_type}")
            result_count = len(results)
            return results
        except Exception as e:
            logger.error(f"[Map] POI搜索失败: {e}")
            status = "error"
            error = str(e)
            if raise_on_error:
                raise
            return []
        finally:
            record_span(
                name="map.search_poi",
                service="map",
                start_time=start_dt,
                end_time=datetime.now(),
                status=status,
                meta={
                    "provider": provider,
                    "city": city,
                    "keywords": keywords,
                    "poi_type": poi_type,
                    "cached": cached,
                    "result_count": result_count,
                },
                error=error,
            )

    def geocode(self, address: str, city: str = "") -> dict | None:
        """
        地理编码：地址转坐标
        Returns: {"lng", "lat", "formatted_address"}
        """
        start_dt = datetime.now()
        status = "ok"
        error = None
        provider = self.provider or "none"

        try:
            if not self.is_available():
                return None

            if self.provider == "amap":
                return self._amap_geocode(address, city)
            elif self.provider == "baidu":
                return self._baidu_geocode(address, city)
            return None
        except Exception as e:
            logger.error(f"[Map] 地理编码失败: {e}")
            status = "error"
            error = str(e)
            return None
        finally:
            record_span(
                name="map.geocode",
                service="map",
                start_time=start_dt,
                end_time=datetime.now(),
                status=status,
                meta={"provider": provider, "address": address, "city": city},
                error=error,
            )

    def calculate_distance(self, origin: str, destination: str) -> dict | None:
        """
        计算两点间距离
        Returns: {"distance", "duration"}
        """
        start_dt = datetime.now()
        status = "ok"
        error = None
        provider = self.provider or "none"

        try:
            if not self.is_available():
                return None

            if self.provider == "amap":
                return self._amap_distance(origin, destination)
            elif self.provider == "baidu":
                return self._baidu_distance(origin, destination)
            return None
        except Exception as e:
            logger.error(f"[Map] 距离计算失败: {e}")
            status = "error"
            error = str(e)
            return None
        finally:
            record_span(
                name="map.calculate_distance",
                service="map",
                start_time=start_dt,
                end_time=datetime.now(),
                status=status,
                meta={"provider": provider, "origin": origin, "destination": destination},
                error=error,
            )

    def test_connection(self) -> dict[str, Any]:
        """测试地图API连接"""
        if not self.is_available():
            return {"success": False, "message": "地图API未配置或Key无效"}
        try:
            results = self.search_poi("杭州", "西湖", page_size=1, raise_on_error=True)
            if results:
                return {"success": True, "message": "连接成功", "provider": self.provider, "sample": results[0]}
            return {"success": False, "message": "API返回空结果", "provider": self.provider}
        except Exception as e:
            return {"success": False, "message": f"连接失败: {str(e)}", "provider": self.provider}

    # ---- 高德地图实现 ----
    def _amap_search(self, city, keywords, poi_type, page_size):
        type_code = AMAP_TYPE_MAP.get(poi_type, "")
        expected_prefix = {
            "hotel": "10",
            "restaurant": "05",
            "attraction": "11",
        }.get(poi_type)

        def _parse_results(data: dict) -> list[dict]:
            results = []
            for poi in data.get("pois", []):
                typecode = poi.get("typecode", "")
                # 如果传了 poi_type，用 typecode 前缀做后过滤，剔除交叉类型
                if expected_prefix and not str(typecode).startswith(expected_prefix):
                    continue
                loc = poi.get("location", "")
                lng_lat = loc.split(",") if loc else [None, None]
                results.append({
                    "name": poi.get("name"),
                    "address": poi.get("address"),
                    "location": loc,
                    "longitude": float(lng_lat[0]) if lng_lat[0] else None,
                    "latitude": float(lng_lat[1]) if lng_lat[1] else None,
                    "type": poi.get("type"),
                    "typecode": typecode,
                    "tel": poi.get("tel"),
                    "city": poi.get("cityname"),
                    "district": poi.get("adname"),
                    "rating": float(poi.get("biz_ext", {}).get("rating", 0) or 0) or 4.0,
                    "photos": poi.get("photos", []),
                })
            return results

        def _do_search(params: dict) -> list[dict]:
            logger.info(f"[Map] 高德请求: {self.base_url}/place/text params={params}")
            resp = self.client.get(f"{self.base_url}/place/text", params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "1":
                info = data.get("info", "未知错误")
                logger.warning(f"[Map] 高德搜索失败: {info}")
                raise Exception(f"AMap API error: {info}")
            return _parse_results(data)

        base_params = {
            "key": self.api_key,
            "city": city,
            "citylimit": "true",
            "offset": page_size,
            "page": 1,
            "extensions": "all",
            "output": "JSON",
        }

        # 策略1：优先使用 keywords（城市限定）
        if keywords:
            params = {**base_params, "keywords": keywords}
            results = _do_search(params)
            if results:
                return results

        # 策略2：keywords 无结果，改用 types（城市限定）
        if type_code:
            params = {**base_params, "types": type_code}
            results = _do_search(params)
            if results:
                return results

        # 策略3：仍无结果，放宽 citylimit 用 keywords 再搜一次
        if keywords:
            params = {
                **base_params,
                "keywords": keywords,
                "citylimit": "false",
            }
            results = _do_search(params)
            if results:
                return results

        # 策略4：最后尝试不指定城市限制的类型搜索
        if type_code:
            params = {
                **base_params,
                "types": type_code,
                "citylimit": "false",
            }
            return _do_search(params)

        return []

    def _amap_geocode(self, address, city):
        url = f"{self.base_url}/geocode/geo"
        params = {
            "key": self.api_key,
            "address": address,
            "city": city,
            "output": "JSON",
        }
        resp = self.client.get(url, params=params)
        data = resp.json()
        if data.get("status") == "1" and data.get("geocodes"):
            geo = data["geocodes"][0]
            return {
                "lng": geo.get("location", "").split(",")[0],
                "lat": geo.get("location", "").split(",")[1],
                "formatted_address": geo.get("formatted_address"),
            }
        return None

    def _amap_distance(self, origin, destination):
        url = f"{self.base_url}/distance"
        params = {
            "key": self.api_key,
            "origins": origin,
            "destination": destination,
            "type": 1,  # 驾车
            "output": "JSON",
        }
        resp = self.client.get(url, params=params)
        data = resp.json()
        if data.get("status") == "1" and data.get("results"):
            result = data["results"][0]
            return {
                "distance": int(result.get("distance", 0)),
                "duration": int(result.get("duration", 0)),
            }
        return None

    # ---- 百度地图实现 ----
    def _baidu_search(self, city, keywords, poi_type, page_size):
        url = f"{self.base_url}/place/v2/search"
        params = {
            "ak": self.api_key,
            "query": keywords,
            "region": city,
            "output": "json",
            "page_size": page_size,
        }
        type_label = BAIDU_TYPE_MAP.get(poi_type)
        if type_label:
            params["tag"] = type_label
        resp = self.client.get(url, params=params)
        data = resp.json()
        if data.get("status") != 0:
            msg = data.get("message") or data.get("msg") or "未知错误"
            logger.warning(f"[Map] 百度搜索失败: {msg}")
            raise Exception(f"Baidu API error: {msg}")
        results = []
        for poi in data.get("results", []):
            results.append({
                "name": poi.get("name"),
                "address": poi.get("address"),
                "location": f"{poi.get('location', {}).get('lng', '')},{poi.get('location', {}).get('lat', '')}",
                "type": poi.get("detail_info", {}).get("tag", ""),
                "tel": "",
                "city": city,
                "district": poi.get("area"),
            })
        return results

    def _baidu_geocode(self, address, city):
        url = f"{self.base_url}/geocoding/v3/"
        params = {
            "ak": self.api_key,
            "address": address,
            "city": city,
            "output": "json",
        }
        resp = self.client.get(url, params=params)
        data = resp.json()
        if data.get("status") == 0:
            loc = data["result"]["location"]
            return {"lng": loc["lng"], "lat": loc["lat"], "formatted_address": address}
        return None

    def _baidu_distance(self, origin, destination):
        url = f"{self.base_url}/direction/v2/driving"
        params = {
            "ak": self.api_key,
            "origin": origin,
            "destination": destination,
            "output": "json",
        }
        resp = self.client.get(url, params=params)
        data = resp.json()
        if data.get("status") == 0:
            route = data["result"]["routes"][0]
            return {"distance": route["distance"], "duration": route["duration"]}
        return None
