"""
PlannerAgent - 中央调度器
协调所有子Agent，整合结果生成最终行程
"""
import concurrent.futures
import json
import logging
import time
from typing import Any

from app.agents.v3.poi_agent import AttractionAgent, HotelAgent, RestaurantAgent, TransportAgent
from app.agents.v3.risk_agent import RiskAgent
from app.agents.v3.weather_agent import WeatherAgent
from app.integrations.llm_client import LLMClient

logger = logging.getLogger(__name__)


ITINERARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "行程标题，包含目的地和天数"},
        "summary": {"type": "string", "description": "行程概述，100字以内"},
        "hotel": {"type": "string", "description": "推荐酒店名称"},
        "transport": {"type": "string", "description": "推荐交通方式"},
        "days": {
            "type": "array",
            "description": "每日行程列表",
            "items": {
                "type": "object",
                "properties": {
                    "day": {"type": "integer", "description": "第几天"},
                    "theme": {"type": "string", "description": "当天主题"},
                    "weather": {"type": "string", "description": "当天天气描述"},
                    "activities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "time": {"type": "string", "description": "活动时间，如 09:00"},
                                "name": {"type": "string", "description": "活动名称"},
                                "type": {
                                    "type": "string",
                                    "enum": ["attraction", "hotel", "restaurant", "transport"],
                                    "description": "活动类型",
                                },
                                "note": {"type": "string", "description": "备注或建议"},
                            },
                            "required": ["time", "name", "type"],
                        },
                    },
                },
                "required": ["day", "theme", "activities"],
            },
        },
        "tips": {
            "type": "array",
            "items": {"type": "string"},
            "description": "出行建议",
        },
        "budget_estimate": {
            "type": "object",
            "properties": {
                "hotel": {"type": "integer"},
                "food": {"type": "integer"},
                "tickets": {"type": "integer"},
                "transport": {"type": "integer"},
            },
        },
    },
    "required": ["title", "summary", "days"],
}


class PlannerAgent:
    """
    行程规划调度器 (PlannerAgent)
    参考hello-agents架构：
    1. 并行调用子Agent获取数据
    2. 综合所有数据，调用LLM生成最终行程
    3. 风控检查
    """

    def __init__(self):
        self.llm = LLMClient()
        self.use_llm = self.llm.is_available()

        # 初始化所有子Agent
        self.weather_agent = WeatherAgent(self.llm)
        self.hotel_agent = HotelAgent(self.llm)
        self.restaurant_agent = RestaurantAgent(self.llm)
        self.attraction_agent = AttractionAgent(self.llm)
        self.transport_agent = TransportAgent(self.llm)
        self.risk_agent = RiskAgent(self.llm)

    def plan(self, destination: str, days: int = 3, travelers: int = 2,
             budget: int = None, origin: str = "上海", style: str = "balanced") -> dict[str, Any]:
        """
        主规划流程
        1. 解析用户需求
        2. 并行执行子Agent
        3. 风控检查
        4. LLM整合生成行程 / 模板生成
        5. 保存到数据库
        """
        total_start = time.time()
        context = {
            "destination": destination,
            "days": days,
            "travelers": travelers,
            "budget": budget,
            "origin": origin,
            "style": style,
        }

        logger.info(f"[Planner] 开始规划: {destination} {days}天 {travelers}人")

        # Step 1: 并行执行子Agent (天气/酒店/餐厅/景点/交通)
        results = self._execute_sub_agents(context)

        # Step 2: 风控检查
        risk_context = {**context, **results}
        risk_result = self.risk_agent.execute(risk_context)

        # Step 3: 整合生成行程
        if self.use_llm:
            itinerary = self._generate_with_llm(context, results, risk_result)
        else:
            itinerary = self._generate_template(context, results, risk_result)

        total_duration = int((time.time() - total_start) * 1000)

        # 保存到数据库
        itinerary_id = self._save_itinerary(context, itinerary, results)

        return {
            "success": True,
            "itinerary_id": itinerary_id,
            "destination": destination,
            "days": days,
            "travelers": travelers,
            "llm_used": self.use_llm,
            "itinerary": itinerary,
            "agent_results": {k: v.to_dict() for k, v in results.items()},
            "risk": risk_result.to_dict(),
            "total_duration_ms": total_duration,
        }

    def _execute_sub_agents(self, context: dict) -> dict[str, Any]:
        """并行执行所有子Agent"""
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_weather = executor.submit(self.weather_agent.execute, context)
            future_hotel = executor.submit(self.hotel_agent.execute, context)
            future_restaurant = executor.submit(self.restaurant_agent.execute, context)
            future_attraction = executor.submit(self.attraction_agent.execute, context)
            future_transport = executor.submit(self.transport_agent.execute, context)

            results = {
                "weather_result": future_weather.result(),
                "hotel_result": future_hotel.result(),
                "restaurant_result": future_restaurant.result(),
                "attraction_result": future_attraction.result(),
                "transport_result": future_transport.result(),
            }

        logger.info("[Planner] 所有子Agent执行完成")
        return results

    def _generate_with_llm(self, context: dict, results: dict, risk_result) -> dict:
        """使用LLM生成结构化行程，通过 JSON Schema 约束输出格式"""
        try:
            system_prompt = """你是资深旅行规划师。根据各Agent提供的数据，生成结构化的每日行程。
请严格遵循用户提供的 JSON Schema，只输出合法 JSON。"""

            user_prompt = self._build_itinerary_prompt(context, results, risk_result)

            llm_result = self.llm.chat_structured(
                system_prompt, user_prompt,
                response_schema=ITINERARY_SCHEMA,
                temperature=0.6, max_tokens=3000,
                use_cache=True, cache_ttl=604800,  # 行程生成缓存 7 天
            )

            if llm_result["success"] and llm_result.get("data") and "days" in llm_result["data"]:
                return llm_result["data"]

            logger.warning("[Planner] LLM未返回有效JSON，使用模板")
            return self._generate_template(context, results, risk_result)

        except Exception as e:
            logger.error(f"[Planner] LLM行程生成失败: {e}")
            return self._generate_template(context, results, risk_result)

    def _generate_template(self, context: dict, results: dict, risk_result) -> dict:
        """模板方式生成行程（无LLM时的降级方案）"""
        days = context["days"]
        destination = context["destination"]
        weather_data = results["weather_result"].data
        hotels = results["hotel_result"].data.get("hotels", [])
        restaurants = results["restaurant_result"].data.get("restaurants", [])
        attractions = results["attraction_result"].data.get("attractions", [])
        transport = results["transport_result"].data

        # 分配景点到每天
        day_plans = []
        for i in range(days):
            day_attractions = attractions[i*2:(i+1)*2] if i*2 < len(attractions) else []
            day_restaurants = restaurants[i*2:(i+1)*2] if i*2 < len(restaurants) else []

            activities = []
            if hotels and i == 0:
                activities.append({"time": "入住", "name": hotels[0]["name"], "type": "hotel"})

            for j, attr in enumerate(day_attractions):
                activities.append({
                    "time": f"{'上午' if j == 0 else '下午'} 10:00-12:00" if j == 0 else "14:00-17:00",
                    "name": attr["name"],
                    "type": "attraction",
                    "duration": attr.get("visit_duration", 120),
                })

            for j, rest in enumerate(day_restaurants[:2]):
                activities.append({
                    "time": "午餐 12:00-13:30" if j == 0 else "晚餐 18:00-20:00",
                    "name": rest["name"],
                    "type": "restaurant",
                })

            forecast = weather_data.get("forecast", [])
            weather = forecast[i] if i < len(forecast) else {}

            day_plans.append({
                "day": i + 1,
                "theme": f"第{i+1}天行程",
                "weather": f"{weather.get('temp_max', '?')}°C / {weather.get('description', '晴')}" if weather else "",
                "activities": activities,
            })

        return {
            "title": f"{destination} {days}日深度游",
            "summary": f"{destination} {days}天行程，包含{len(attractions)}个景点、{len(restaurants)}家餐厅推荐",
            "hotel": hotels[0]["name"] if hotels else "",
            "transport": transport.get("options", [{}])[0].get("name", ""),
            "days": day_plans,
            "tips": [r["message"] for r in risk_result.data.get("warnings", [])],
        }

    def _build_itinerary_prompt(self, context: dict, results: dict, risk_result) -> str:
        """构建LLM行程生成提示词"""
        weather = results["weather_result"]
        hotel = results["hotel_result"]
        restaurant = results["restaurant_result"]
        attraction = results["attraction_result"]
        transport = results["transport_result"]

        return f"""请为以下旅行生成结构化行程：

【基本信息】
目的地: {context['destination']}
出发地: {context['origin']}
天数: {context['days']}天
人数: {context['travelers']}人
预算: ¥{context.get('budget', '未设定')}
风格: {context['style']}

【天气】
当前: {weather.data.get('current', {})}
预报: {weather.data.get('forecast', [])}
穿衣建议: {weather.data.get('clothing_advice', '')}

【酒店候选】
{json.dumps([{'name': h['name'], 'price': h.get('price_value'), 'rating': h['rating']} for h in hotel.data.get('hotels', [])[:4]], ensure_ascii=False)}

【景点候选】
{json.dumps([{'name': a['name'], 'duration': a.get('visit_duration'), 'price': a.get('price_value')} for a in attraction.data.get('attractions', [])[:6]], ensure_ascii=False)}

【餐厅候选】
{json.dumps([{'name': r['name'], 'price': r.get('price_value'), 'rating': r['rating']} for r in restaurant.data.get('restaurants', [])[:6]], ensure_ascii=False)}

【交通】
{json.dumps(transport.data.get('options', [])[:3], ensure_ascii=False)}

【风控】
{json.dumps([{'type': w['type'], 'level': w['level'], 'msg': w['message']} for w in risk_result.data.get('warnings', [])], ensure_ascii=False)}

请输出符合 JSON Schema 的行程对象，不要包含 markdown 代码块。"""

    def _save_itinerary(self, context: dict, itinerary: dict, results: dict) -> int:
        """保存行程到数据库"""
        try:
            from app.db.database import execute, get_db_connection
            conn = get_db_connection()

            # 保存行程
            it_id = execute(conn, """
                INSERT INTO itineraries (title, destination, origin, start_date, end_date,
                    traveler_count, budget, travel_style, status, llm_used)
                VALUES (?, ?, ?, date('now'), date('now', ?), ?, ?, ?, 'planned', ?)
            """, (
                itinerary.get("title", f"{context['destination']}之旅"),
                context["destination"],
                context["origin"],
                f"+{context['days']} days",
                context["travelers"],
                context.get("budget"),
                context.get("style", "balanced"),
                self.use_llm,
            ))

            # 保存Agent日志
            for agent_type, result in results.items():
                usage = result.usage or {}
                execute(conn, """
                    INSERT INTO agent_logs (itinerary_id, agent_type, agent_name, status,
                        output_result, duration_ms, prompt_tokens, completion_tokens, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    it_id, result.agent_type, result.agent_name, result.status,
                    json.dumps(result.data, ensure_ascii=False)[:2000],
                    result.duration_ms,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    result.error or "",
                ))

            conn.close()
            return it_id

        except Exception as e:
            logger.error(f"[Planner] 保存行程失败: {e}")
            return 0
