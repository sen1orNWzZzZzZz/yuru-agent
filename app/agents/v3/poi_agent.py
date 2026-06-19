"""
POI数据Agent - 从数据库查询酒店/餐厅/景点
集成反水军评分，支持高德/百度 POI 搜索 fallback
"""
import json
from typing import Any

from app.agents.v3.base import BaseAgentV3
from app.db.database import get_db_connection, query_all
from app.integrations.map import MapClient


def _external_to_poi_dict(external: dict, poi_type: str, city: str) -> dict:
    """把地图 API 返回的 POI 统一转换为 poi_data 风格字典"""
    return {
        "poi_id": f"ext-{poi_type}-{external.get('name', '')}"[:50],
        "name": external.get("name"),
        "poi_type": poi_type,
        "city": city,
        "district": external.get("district"),
        "address": external.get("address"),
        "latitude": external.get("latitude"),
        "longitude": external.get("longitude"),
        "rating": external.get("rating", 4.0),
        "review_count": 0,
        "price_value": None,
        "tags": "[]",
        "description": "",
        "extras": json.dumps({"source": "map_api", "photos": external.get("photos", [])}, ensure_ascii=False),
        "open_hours": None,
        "needs_booking": 0,
        "altitude": None,
        "visit_duration": None,
        "xiaohongshu_mentions": 0,
        "xiaohongshu_score": 70,
        "source": "map_api",
    }


class HotelAgent(BaseAgentV3):
    """酒店推荐Agent"""

    agent_type = "hotel"
    agent_name = "酒店推荐Agent"

    def _execute_with_db(self, context: dict[str, Any]) -> dict[str, Any]:
        city = context.get("destination", "")
        budget = context.get("budget")
        travelers = context.get("travelers", 2)

        conn = get_db_connection()
        try:
            sql = """SELECT * FROM poi_data
                     WHERE poi_type = 'hotel' AND city = ?
                     ORDER BY rating DESC, review_count DESC"""
            hotels = query_all(conn, sql, (city,))

            # 预算过滤
            if budget:
                max_price = budget * (1.5 if travelers > 2 else 1.0)
                hotels = [h for h in hotels if (h.get("price_value") or 9999) <= max_price]

            # 本地数据不足时，fallback 高德/百度 POI 搜索
            if len(hotels) < 3:
                client = MapClient()
                if client.is_available():
                    external = client.search_poi(city, keywords="酒店", poi_type="hotel", page_size=10)
                    for e in external[:10]:
                        hotels.append(_external_to_poi_dict(e, "hotel", city))

            # 补充小红书口碑
            for h in hotels[:8]:
                xhs = self._get_xiaohongshu_score(conn, h["name"])
                h["xiaohongshu"] = xhs

            return {"city": city, "hotels": hotels[:6], "total": len(hotels)}
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

    def _execute_with_db(self, context: dict[str, Any]) -> dict[str, Any]:
        city = context.get("destination", "")
        _budget = context.get("budget")

        conn = get_db_connection()
        try:
            sql = """SELECT * FROM poi_data
                     WHERE poi_type = 'restaurant' AND city = ?
                     ORDER BY rating DESC, review_count DESC"""
            restaurants = query_all(conn, sql, (city,))

            # 按价位分类
            categories = {"luxury": [], "high": [], "medium": [], "low": []}
            for r in restaurants:
                pl = r.get("price_level", "medium")
                categories.setdefault(pl, []).append(r)

            # 本地数据不足时，fallback 高德/百度 POI 搜索
            if len(restaurants) < 5:
                client = MapClient()
                if client.is_available():
                    external = client.search_poi(city, keywords="美食", poi_type="restaurant", page_size=10)
                    for e in external[:10]:
                        restaurants.append(_external_to_poi_dict(e, "restaurant", city))

            return {
                "city": city,
                "restaurants": restaurants[:8],
                "by_price": {k: v[:3] for k, v in categories.items() if v},
                "total": len(restaurants),
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
    """景点推荐Agent"""

    agent_type = "attraction"
    agent_name = "景点推荐Agent"

    def _execute_with_db(self, context: dict[str, Any]) -> dict[str, Any]:
        city = context.get("destination", "")
        _days = context.get("days", 3)

        conn = get_db_connection()
        try:
            sql = """SELECT * FROM poi_data
                     WHERE poi_type = 'attraction' AND city = ?
                     ORDER BY rating DESC, review_count DESC"""
            attractions = query_all(conn, sql, (city,))

            # 高反风险评估
            altitude_risks = []
            for a in attractions:
                alt = a.get("altitude")
                if alt and alt > 2000:
                    level = "high" if alt > 3500 else "medium" if alt > 2500 else "low"
                    altitude_risks.append({
                        "name": a["name"], "altitude": alt, "risk_level": level,
                    })

            # 本地数据不足时，fallback 高德/百度 POI 搜索
            if len(attractions) < 5:
                client = MapClient()
                if client.is_available():
                    external = client.search_poi(city, keywords="景点", poi_type="attraction", page_size=10)
                    for e in external[:10]:
                        attractions.append(_external_to_poi_dict(e, "attraction", city))

            return {
                "city": city,
                "attractions": attractions[:8],
                "altitude_risks": altitude_risks,
                "total": len(attractions),
            }
        finally:
            conn.close()

    def _build_prompt(self, context: dict, db_data: dict) -> tuple:
        city = context.get("destination", "")
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
        risks = db_data.get("altitude_risks", [])
        risk_note = f"发现{len(risks)}个高海拔景点，请注意高反风险" if risks else ""
        return f"精选{db_data.get('total', 0)}个{city}景点{risk_note}"


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
