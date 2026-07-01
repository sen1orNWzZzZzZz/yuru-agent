"""
POI数据Agent - 从数据库查询酒店/餐厅/景点
集成反水军评分，支持高德/百度 POI 搜索 fallback
"""
import json
import logging
import math
from datetime import datetime, timedelta
from typing import Any

from app.agents.v3.base import BaseAgentV3
from app.db.database import execute, get_db_connection, query_all, query_one
from app.integrations.map import MapClient

logger = logging.getLogger(__name__)

# 户外类景点关键词：雨天时下沉排序，优先室内
_OUTDOOR_KEYWORDS = ("公园", "山", "湖", "海", "徒步", "露营", "古镇", "峡谷", "瀑布", "沙滩", "花海", "草原", "湿地")


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    """两点间直线距离（公里）；坐标缺失时返回 inf。"""
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return float("inf")
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _distance_to(loc: tuple[float, float], poi: dict) -> float:
    return _haversine_km(loc[0], loc[1], poi.get("latitude"), poi.get("longitude"))


def _is_outdoor(poi: dict) -> bool:
    text = f"{poi.get('name', '')} {poi.get('tags', '')} {poi.get('description', '')}"
    return any(k in text for k in _OUTDOOR_KEYWORDS)


def _external_to_poi_dict(external: dict, poi_type: str, city: str) -> dict:
    """把地图 API 返回的 POI 统一转换为 poi_data 风格字典"""
    name = external.get("name", "")
    tags = external.get("type", "")
    return {
        "poi_id": f"ext-{poi_type}-{name}"[:50],
        "name": name,
        "poi_type": poi_type,
        "city": city,
        "district": external.get("district"),
        "address": external.get("address"),
        "latitude": external.get("latitude"),
        "longitude": external.get("longitude"),
        "rating": external.get("rating", 4.0),
        "review_count": 0,
        "price_value": external.get("price_value"),
        "tags": json.dumps([tags], ensure_ascii=False) if tags else "[]",
        "description": external.get("type", ""),
        "extras": json.dumps({
            "source": "map_api",
            "tel": external.get("tel"),
            "photos": external.get("photos", []),
        }, ensure_ascii=False),
        "open_hours": None,
        "needs_booking": 0,
        "altitude": None,
        "visit_duration": None,
        "xiaohongshu_mentions": 0,
        "xiaohongshu_score": 70,
        "source": "map_api",
    }


def _merge_pois(external: list[dict], local: list[dict]) -> list[dict]:
    """
    按名称合并外部（高德/百度）与本地 POI 列表。
    外部数据优先作为主体，本地数据补充更丰富的字段（如 visit_duration、price_value、tags 等）。
    """
    by_name: dict[str, dict] = {}
    for p in external:
        name = p.get("name")
        if name:
            by_name[name] = p

    for p in local:
        name = p.get("name")
        if not name:
            continue
        if name in by_name:
            current = by_name[name]
            # 本地字段更全/更可信时，补充或覆盖外部缺失字段
            for key, value in p.items():
                if value is None or value == "" or value == []:
                    continue
                cur_val = current.get(key)
                if cur_val is None or cur_val == "" or cur_val == []:
                    current[key] = value
            # 保留外部来源标记；若外部没有，则使用本地来源
            if not current.get("source"):
                current["source"] = p.get("source", "local_db")
        else:
            by_name[name] = p

    return list(by_name.values())


class HotelAgent(BaseAgentV3):
    """酒店推荐Agent"""

    agent_type = "hotel"
    agent_name = "酒店推荐Agent"

    def _execute_with_db(self, context: dict[str, Any]) -> dict[str, Any]:
        city = context.get("destination", "")
        budget = context.get("budget")
        travelers = context.get("travelers", 2)
        district = context.get("district", "")

        conn = get_db_connection()
        try:
            # 优先调用高德/百度 POI 搜索真实数据
            hotels: list[dict] = []
            api_error = None
            client = MapClient()
            if client.is_available():
                keywords = f"{district}酒店" if district else "酒店"
                try:
                    external = client.search_poi(city, keywords=keywords, poi_type="hotel", page_size=20)
                    hotels = [_external_to_poi_dict(e, "hotel", city) for e in external]
                except Exception as e:
                    api_error = f"地图 API 调用失败: {e}"
                    logger.warning(f"[HotelAgent] {api_error}")
            else:
                api_error = "地图 API 未配置或 Key 无效"

            # 外部数据不足时，用本地 mock 数据补充
            if len(hotels) < 6:
                sql = """SELECT * FROM poi_data
                         WHERE poi_type = 'hotel' AND city = ?
                         ORDER BY rating DESC, review_count DESC"""
                local_hotels = query_all(conn, sql, (city,))
                for h in local_hotels:
                    h.setdefault("source", "local_db")
                hotels = _merge_pois(hotels, local_hotels)

            # 预算过滤（外部 POI 通常没有 price_value，未知价格直接保留）
            if budget:
                max_price = budget * (1.5 if travelers > 2 else 1.0)
                hotels = [h for h in hotels if h.get("price_value") is None or h["price_value"] <= max_price]

            # 补充小红书口碑
            for h in hotels[:10]:
                xhs = self._get_xiaohongshu_score(conn, h["name"])
                h["xiaohongshu"] = xhs

            return {"city": city, "hotels": hotels[:8], "total": len(hotels), "api_error": api_error}
        finally:
            conn.close()

    def _get_xiaohongshu_score(self, conn, poi_name: str) -> dict:
        """获取POI的小红书口碑评分（含反水军过滤）"""
        notes = query_all(conn,
            "SELECT * FROM xiaohongshu_notes WHERE poi_name = ? AND is_suspicious = 0",
            (poi_name,))
        if not notes:
            notes = query_all(conn,
                "SELECT * FROM xiaohongshu_notes WHERE city = (SELECT city FROM poi_data WHERE name = ? LIMIT 1) AND is_suspicious = 0 LIMIT 3",
                (poi_name,))

        suspicious = query_all(conn,
            "SELECT COUNT(*) as cnt FROM xiaohongshu_notes WHERE poi_name = ? AND is_suspicious = 1",
            (poi_name,))
        suspicious_count = suspicious[0]["cnt"] if suspicious else 0

        avg_score = 70
        if notes:
            avg_score = sum(n["credibility_score"] for n in notes) / len(notes)
            avg_score = max(avg_score - suspicious_count * 5, 10)

        return {
            "credibility_score": round(avg_score, 1),
            "note_count": len(notes),
            "suspicious_count": suspicious_count,
        }

    def _build_prompt(self, context: dict, db_data: dict) -> tuple:
        city = context.get("destination", "")
        travelers = context.get("travelers", 2)
        style = context.get("style", "")
        hotels = db_data.get("hotels", [])

        system = "你是专业酒店推荐顾问。根据用户需求和酒店数据，给出精准推荐和理由。输出JSON。"
        user = f"""为{city}的{travelers}人{style}旅行推荐酒店：

候选酒店({len(hotels)}家):
{json.dumps([{"name": h["name"], "price": h.get("price_value"), "rating": h["rating"], "tags": h.get("tags")} for h in hotels[:6]], ensure_ascii=False)}

请输出JSON:
{{
  "reasoning": "推荐逻辑说明(100字)",
  "top_pick": "最推荐的一家名称",
  "recommendations": ["推荐理由1", "推荐理由2"]
}}"""
        return system, user

    def _default_reasoning(self, context: dict, db_data: dict) -> str:
        return f"从{db_data.get('total', 0)}家酒店中筛选，已过滤小红书水军内容"


class RestaurantAgent(BaseAgentV3):
    """餐饮推荐Agent"""

    agent_type = "restaurant"
    agent_name = "餐饮推荐Agent"
    depends_on = ["hotel"]  # 读酒店位置做就近推荐

    def _execute_with_db(self, context: dict[str, Any]) -> dict[str, Any]:
        city = context.get("destination", "")
        _budget = context.get("budget")
        cuisine_type = context.get("cuisine_type", "")
        district = context.get("district", "")

        conn = get_db_connection()
        try:
            # 优先调用高德/百度 POI 搜索真实餐厅数据
            restaurants: list[dict] = []
            api_error = None
            client = MapClient()
            if client.is_available():
                keywords_parts = [p for p in [district, cuisine_type, "美食"] if p]
                keywords = "".join(keywords_parts) if keywords_parts else "美食"
                try:
                    external = client.search_poi(city, keywords=keywords, poi_type="restaurant", page_size=20)
                    restaurants = [_external_to_poi_dict(e, "restaurant", city) for e in external]
                except Exception as e:
                    api_error = f"地图 API 调用失败: {e}"
                    logger.warning(f"[RestaurantAgent] {api_error}")
            else:
                api_error = "地图 API 未配置或 Key 无效"

            # 外部数据不足时，用本地 mock 数据补充
            if len(restaurants) < 8:
                sql = """SELECT * FROM poi_data
                         WHERE poi_type = 'restaurant' AND city = ?
                         ORDER BY rating DESC, review_count DESC"""
                local_restaurants = query_all(conn, sql, (city,))
                for r in local_restaurants:
                    r.setdefault("source", "local_db")
                restaurants = _merge_pois(restaurants, local_restaurants)

            # 读上游酒店位置做就近排序（依赖 hotel）
            state = context.get("_state")
            hotel_loc = state.hotel_location if state and state.status("hotel") == "completed" else None
            consumed_from = {"hotel_location": hotel_loc} if hotel_loc else {}
            if state and state.status("hotel") != "completed":
                consumed_from["hotel_failed"] = True
            if hotel_loc:
                restaurants.sort(key=lambda r: _distance_to(hotel_loc, r))

            # 按价位分类（外部数据通常没有 price_level，优先按本地字段分类）
            categories = {"luxury": [], "high": [], "medium": [], "low": []}
            for r in restaurants:
                pl = r.get("price_level")
                if not pl:
                    # 根据 price_value 粗略推断价位
                    pv = r.get("price_value") or 0
                    if pv >= 300:
                        pl = "luxury"
                    elif pv >= 150:
                        pl = "high"
                    elif pv >= 60:
                        pl = "medium"
                    else:
                        pl = "low"
                categories.setdefault(pl, []).append(r)

            return {
                "city": city,
                "restaurants": restaurants[:10],
                "by_price": {k: v[:3] for k, v in categories.items() if v},
                "total": len(restaurants),
                "api_error": api_error,
            }
        finally:
            conn.close()

    def _build_prompt(self, context: dict, db_data: dict) -> tuple:
        city = context.get("destination", "")
        style = context.get("style", "")
        restaurants = db_data.get("restaurants", [])

        system = "你是资深美食推荐专家。推荐当地特色餐厅，覆盖不同价位。输出JSON。"
        user = f"""推荐{city}的美食，旅行风格: {style}

候选餐厅({len(restaurants)}家):
{json.dumps([{"name": r["name"], "cuisine": json.loads(r.get("extras") or '{}').get('signature_dishes', []), "price": r.get('price_value'), "rating": r['rating']} for r in restaurants[:6]], ensure_ascii=False)}

输出JSON:
{{
  "reasoning": "美食推荐思路(100字)",
  "must_try": ["必吃菜品1", "必吃菜品2"],
  "recommendations": ["推荐餐厅及理由"]
}}"""
        return system, user

    def _default_reasoning(self, context: dict, db_data: dict) -> str:
        city = db_data.get("city", "")
        return f"精选{db_data.get('total', 0)}家{city}特色餐厅，覆盖老字号到网红店"


class AttractionAgent(BaseAgentV3):
    """景点推荐Agent

    策略：
    1. 读取 7 天内的城市景点缓存池。
    2. 合并本地 poi_data 景点（本地字段更全，优先覆盖）。
    3. 若合并后数量仍不足以支撑完整行程，调用高德/百度 POI 接口兜底。
    4. 将新搜到的景点更新进缓存池。
    5. 返回带 _trace 诊断信息的结果，供前端可视化。
    """

    agent_type = "attraction"
    agent_name = "景点推荐Agent"
    depends_on = ["hotel", "weather"]  # 读酒店位置做就近排序、读天气偏好室内/室外

    # 景点池在 external_poi_cache 中的标记
    _CACHE_PROVIDER = "attraction_pool"
    _CACHE_KEYWORDS = "pool"
    _CACHE_TTL_DAYS = 7

    def _load_attraction_pool(self, conn, city: str) -> list[dict] | None:
        """读取 7 天内有效的城市景点缓存池"""
        row = query_one(
            conn,
            """
            SELECT results_json, created_at FROM external_poi_cache
            WHERE provider = ? AND city = ? AND keywords = ? AND poi_type = 'attraction'
            ORDER BY id DESC LIMIT 1
            """,
            (self._CACHE_PROVIDER, city, self._CACHE_KEYWORDS),
        )
        if not row:
            return None
        created = row.get("created_at", "")
        expiry = (datetime.now() - timedelta(days=self._CACHE_TTL_DAYS)).isoformat()
        if created and created > expiry:
            try:
                return json.loads(row["results_json"])
            except Exception:
                return None
        return None

    def _save_attraction_pool(self, conn, city: str, attractions: list[dict]) -> None:
        """保存城市景点缓存池"""
        execute(
            conn,
            """
            INSERT OR REPLACE INTO external_poi_cache
            (provider, city, keywords, poi_type, results_json, updated_at)
            VALUES (?, ?, ?, 'attraction', ?, CURRENT_TIMESTAMP)
            """,
            (self._CACHE_PROVIDER, city, self._CACHE_KEYWORDS, json.dumps(attractions, ensure_ascii=False)),
        )

    @staticmethod
    def _merge_attractions(existing: list[dict], additions: list[dict], source: str) -> list[dict]:
        """按名称合并景点列表，去重；本地数据字段更全时覆盖旧值，来源以本地为优先"""
        by_name = {a["name"]: a for a in existing}
        for add in additions:
            add = dict(add)
            add.setdefault("source", source)
            name = add.get("name")
            if not name:
                continue
            if name in by_name:
                current = by_name[name]
                # 用新数据中的非空字段补充/覆盖
                for key, value in add.items():
                    if key == "source":
                        continue
                    if value is not None and value != "" and value != []:
                        current[key] = value
                # 来源优先级：local_db > 任何其他
                if current.get("source") != "local_db":
                    current["source"] = source
            else:
                by_name[name] = add
        return list(by_name.values())

    def _execute_with_db(self, context: dict[str, Any]) -> dict[str, Any]:
        city = context.get("city") or context.get("destination", "")
        days = context.get("days", 3)
        keywords = context.get("keywords") or "景点"
        target_count = max(days, context.get("min_count", 5))

        conn = get_db_connection()
        try:
            from app.db.database import execute

            # 1. 优先调用高德/百度 POI 搜索真实景点数据
            pool: list[dict] = []
            fetched_count = 0
            api_called = False
            api_provider = None
            cache_updated = False
            api_error = None

            client = MapClient()
            if client.is_available():
                api_provider = client.provider
                api_called = True
                try:
                    external_raw = client.search_poi(
                        city, keywords=keywords, poi_type="attraction", page_size=30
                    )
                    external = [_external_to_poi_dict(e, "attraction", city) for e in external_raw]
                    fetched_count = len(external)
                    if external:
                        pool = external
                        self._save_attraction_pool(conn, city, pool)
                        cache_updated = True
                except Exception as e:
                    api_error = f"地图 API 调用失败: {e}"
                    logger.warning(f"[AttractionAgent] {api_error}")
            else:
                api_error = "地图 API 未配置或 Key 无效"

            # 2. 读本地 poi_data，用更全的字段补充/覆盖外部数据
            local = query_all(
                conn,
                """SELECT * FROM poi_data
                   WHERE poi_type = 'attraction' AND city = ?
                   ORDER BY rating DESC, review_count DESC""",
                (city,),
            )
            for loc in local:
                loc["source"] = "local_db"
            local_count = len(local)

            # 3. 合并：外部数据为主体，本地数据补充字段
            pool = self._merge_attractions(pool, local, "local_db")

            # 4. 外部 API 未调用/失败或数量不足时，回退到 7 天缓存池
            cached_count = 0
            if (not api_called or len(pool) < target_count) and not api_error:
                cached = self._load_attraction_pool(conn, city)
                if cached:
                    cached_count = len(cached)
                    pool = self._merge_attractions(pool, cached, "map_api")

            # 5. 最终排序：评分优先，再按评论数
            pool.sort(key=lambda a: (a.get("rating") or 0, a.get("review_count") or 0), reverse=True)

            # 5b. 读上游数据做通信驱动的重排（依赖 hotel / weather）
            state = context.get("_state")
            consumed_from: dict[str, Any] = {}
            hotel_loc = state.hotel_location if state and state.status("hotel") == "completed" else None
            is_rainy = state.is_rainy if state and state.status("weather") == "completed" else False
            if hotel_loc:
                # 就近排序：优先安排酒店附近的景点（Python stable sort）
                pool.sort(key=lambda a: _distance_to(hotel_loc, a))
                consumed_from["hotel_location"] = hotel_loc
            if is_rainy:
                # 雨天把户外景点稳定下沉到末尾，室内优先
                pool.sort(key=lambda a: _is_outdoor(a))
                consumed_from["is_rainy"] = True

            # 降级标记：如果依赖的上游失败了，trace 里也能看出来
            if state:
                if state.status("hotel") != "completed":
                    consumed_from["hotel_failed"] = True
                if state.status("weather") != "completed":
                    consumed_from["weather_failed"] = True

            # 6. 高反风险评估
            altitude_risks = []
            for a in pool:
                alt = a.get("altitude")
                if alt and alt > 2000:
                    level = "high" if alt > 3500 else "medium" if alt > 2500 else "low"
                    altitude_risks.append({
                        "name": a["name"], "altitude": alt, "risk_level": level,
                    })

            final_count = len(pool)
            trace = {
                "local_count": local_count,
                "cached_count": cached_count,
                "cache_hit": cached_count > 0,
                "fetched_count": fetched_count,
                "api_called": api_called,
                "api_provider": api_provider,
                "cache_updated": cache_updated,
                "api_error": api_error,
                "final_count": final_count,
                "target_count": target_count,
                "keywords": keywords,
                "days": days,
                "shortage_warning": final_count < days,
                "warning": "景点数量不足以支撑每天 1 个" if final_count < days else None,
                "consumed_from": consumed_from,
            }

            return {
                "city": city,
                "attractions": pool[:max(target_count, 8)],
                "altitude_risks": altitude_risks,
                "total": final_count,
                "_trace": trace,
            }
        finally:
            conn.close()

    def _build_prompt(self, context: dict, db_data: dict) -> tuple:
        city = context.get("city") or context.get("destination", "")
        days = context.get("days", 3)
        style = context.get("style", "")

        system = "你是专业旅行规划师。根据景点数据和旅行天数，规划最优游览路线。输出JSON。"
        user = f"""规划{city} {days}天{style}旅行的景点安排：

候选景点({db_data.get('total', 0)}个):
{json.dumps([{"name": a["name"], "duration": a.get("visit_duration"), "price": a.get("price_value"), "booking": a.get("needs_booking")} for a in db_data.get('attractions', [])[:8]], ensure_ascii=False)}

高反风险: {db_data.get('altitude_risks', [])}

输出JSON:
{{
  "reasoning": "景点安排思路(100字)",
  "day_plan": ["第1天安排", "第2天安排"],
  "warnings": ["注意事项"],
  "must_see": ["必去景点"]
}}"""
        return system, user

    def _default_reasoning(self, context: dict, db_data: dict) -> str:
        city = db_data.get("city", "")
        total = db_data.get("total", 0)
        trace = db_data.get("_trace", {})
        risks = db_data.get("altitude_risks", [])
        parts = [f"精选{total}个{city}景点"]
        if trace.get("api_called"):
            parts.append(f"其中高德/百度补充{trace.get('fetched_count', 0)}个")
        if trace.get("shortage_warning"):
            parts.append("注意：景点数量偏少")
        if risks:
            parts.append(f"，发现{len(risks)}个高海拔景点")
        return "；".join(parts)


class TransportAgent(BaseAgentV3):
    """交通规划Agent"""

    agent_type = "transport"
    agent_name = "交通规划Agent"

    # 城市间距离（公里）
    CITY_DISTANCES = {
        ("上海", "杭州"): 173, ("上海", "苏州"): 100, ("上海", "南京"): 300,
        ("上海", "厦门"): 1000, ("广州", "深圳"): 140, ("北京", "西安"): 1084,
        ("北京", "上海"): 1318, ("成都", "重庆"): 300, ("成都", "丽江"): 850,
        ("杭州", "苏州"): 150, ("杭州", "南京"): 250, ("武汉", "长沙"): 350,
        ("西安", "成都"): 712, ("桂林", "阳朔"): 65, ("昆明", "大理"): 330,
        ("昆明", "丽江"): 500, ("大理", "丽江"): 180,
    }

    def _execute_with_db(self, context: dict[str, Any]) -> dict[str, Any]:
        origin = context.get("origin", "上海")
        destination = context.get("destination", "")
        _travelers = context.get("travelers", 2)

        dist = self._get_distance(origin, destination)
        options = []

        # 飞机（长距离）
        if dist > 600:
            options.append({
                "type": "flight", "duration": int(120 + dist/800*60),
                "price": int(400 + dist*0.3), "name": f"{origin}→{destination}航班",
                "tags": ["快速"],
            })

        # 高铁
        options.append({
            "type": "train", "duration": int(dist/250*60 + 30),
            "price": int(dist*0.45), "name": f"{origin}→{destination}高铁",
            "tags": ["准点率高", "舒适"],
        })

        # 自驾
        drive_cost = int(dist * 0.8)
        options.append({
            "type": "drive", "duration": int(dist/100*60),
            "price": drive_cost, "name": f"自驾{origin}→{destination}",
            "tags": ["灵活自由", "沿途风景"],
        })

        # 市内交通建议
        city_transport = ["地铁", "打车/网约车", "共享单车"]

        return {
            "origin": origin, "destination": destination,
            "distance_km": dist, "options": options,
            "city_transport": city_transport,
        }

    def _get_distance(self, origin, destination):
        if origin == destination:
            return 0
        key = (origin, destination)
        reverse = (destination, origin)
        return self.CITY_DISTANCES.get(key) or self.CITY_DISTANCES.get(reverse) or 800

    def _build_prompt(self, context: dict, db_data: dict) -> tuple:
        system = "你是交通规划专家。分析最优出行方案。输出JSON。"
        user = f"""{db_data['origin']}→{db_data['destination']}({db_data['distance_km']}km)

方案: {json.dumps(db_data['options'], ensure_ascii=False)}

输出JSON:
{{
  "reasoning": "推荐思路(100字)",
  "best_option": "最推荐的交通方式",
  "recommendations": ["出行建议1"]
}}"""
        return system, user

    def _default_reasoning(self, context: dict, db_data: dict) -> str:
        return f"提供{len(db_data.get('options', []))}种交通方案，距离{db_data.get('distance_km')}km"
