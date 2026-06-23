"""
PlannerAgent - 中央调度器
协调所有子Agent，整合结果生成最终行程
"""
import concurrent.futures
import json
import logging
import math
import time
from typing import Any, Callable

from app.agents.v3.base import AgentResult
from app.agents.v3.planning_run import PlanningRunService
from app.agents.v3.planner_runtime import PlannerRuntime
from app.agents.v3.poi_agent import AttractionAgent, HotelAgent, RestaurantAgent, TransportAgent
from app.agents.v3.profile_agent import ProfileSummarizerAgent
from app.agents.v3.risk_agent import RiskAgent
from app.agents.v3.tools import agent_result_to_observation, build_tool_registry
from app.agents.v3.weather_agent import WeatherAgent
from app.integrations.llm_client import LLMClient

logger = logging.getLogger(__name__)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """计算两点间直线距离（公里）"""
    if not all((lat1, lon1, lat2, lon2)):
        return float("inf")
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


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

        # 初始化所有子Agent（用于无LLM时的并行兜底）
        self.weather_agent = WeatherAgent(self.llm)
        self.hotel_agent = HotelAgent(self.llm)
        self.restaurant_agent = RestaurantAgent(self.llm)
        self.attraction_agent = AttractionAgent(self.llm)
        self.transport_agent = TransportAgent(self.llm)
        self.risk_agent = RiskAgent(self.llm)

        # 注册 Tool，供 LLM 决策调用
        self.tool_registry = build_tool_registry(self.llm)

    def plan(
        self,
        destination: str,
        days: int = 3,
        travelers: int = 2,
        budget: int = None,
        origin: str = "上海",
        style: str = "balanced",
        preferences: dict | None = None,
        user_id: int | None = None,
        run_id: int | None = None,
    ) -> dict[str, Any]:
        """
        主规划流程（同步版）
        - 可外部传入 run_id，复用已有 PlanningRun
        - 有 LLM 时：使用 PlannerRuntime 工具决策循环
        - 无 LLM 时：串行执行子Agent + 模板生成（兜底）
        - 完成后更新 Run 状态
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
        if preferences:
            context.update(preferences)

        logger.info(f"[Planner] 开始规划: {destination} {days}天 {travelers}人")

        run_service = PlanningRunService()
        if run_id is None:
            run_id = run_service.create_run(user_id, context)
        else:
            run_service.update_status(run_id, "running")

        try:
            if self.use_llm:
                runtime = PlannerRuntime(
                    self.llm, self.tool_registry, max_steps=10,
                    run_service=run_service, run_id=run_id,
                )
                runtime_result = runtime.run(context)
                results = runtime_result["results"]
                risk_result = runtime_result["risk"]
                planning_trace = runtime_result["steps"]
                itinerary = self._generate_with_llm(context, results, risk_result)
            else:
                results, risk_result, planning_trace = self._execute_fallback(
                    context, run_service=run_service, run_id=run_id
                )
                itinerary = self._generate_template(context, results, risk_result)

            total_duration = int((time.time() - total_start) * 1000)
            itinerary_id = self._save_itinerary(
                context, itinerary, results,
                planning_trace=planning_trace,
                user_id=user_id,
            )
            run_service.update_status(
                run_id, "completed",
                itinerary_id=itinerary_id,
                total_steps=len(planning_trace),
            )

            if user_id:
                ProfileSummarizerAgent().summarize(user_id, context, itinerary)

            return {
                "success": True,
                "run_id": run_id,
                "itinerary_id": itinerary_id,
                "destination": destination,
                "days": days,
                "travelers": travelers,
                "budget": budget,
                "origin": origin,
                "style": style,
                "llm_used": self.use_llm,
                "itinerary": itinerary,
                "agent_results": {k: v.to_dict() for k, v in results.items()},
                "risk": risk_result.to_dict(),
                "total_duration_ms": total_duration,
                "planning_trace": planning_trace,
            }
        except Exception as e:
            logger.exception(f"[Planner] run_id={run_id} 规划失败")
            run_service.update_status(run_id, "failed", error_message=str(e))
            raise

    def plan_stream(
        self,
        context: dict[str, Any],
        user_id: int | None,
        on_step: Callable[[dict], None] | None = None,
        run_id: int | None = None,
        initial_results: dict[str, AgentResult] | None = None,
    ) -> dict[str, Any]:
        """
        流式规划流程
        与 plan() 逻辑一致，但每产生一个 step 都会调用 on_step 回调，供 SSE 实时推送。
        支持外部传入 run_id 和 initial_results（断点续跑）。
        """
        run_service = PlanningRunService()
        if run_id is None:
            run_id = run_service.create_run(user_id, context)
        else:
            run_service.update_status(run_id, "running")

        def combined_on_step(step: dict) -> None:
            if on_step:
                on_step(step)

        try:
            if self.use_llm:
                runtime = PlannerRuntime(
                    self.llm, self.tool_registry, max_steps=10,
                    on_step=combined_on_step,
                    run_service=run_service, run_id=run_id,
                    initial_results=initial_results,
                )
                runtime_result = runtime.run(context)
                results = runtime_result["results"]
                risk_result = runtime_result["risk"]
                planning_trace = runtime_result["steps"]
                itinerary = self._generate_with_llm(context, results, risk_result)
            else:
                results, risk_result, planning_trace = self._execute_fallback(
                    context,
                    run_service=run_service,
                    run_id=run_id,
                    on_step=combined_on_step,
                    initial_results=initial_results,
                )
                itinerary = self._generate_template(context, results, risk_result)

            itinerary_id = self._save_itinerary(
                context, itinerary, results,
                planning_trace=planning_trace,
                user_id=user_id,
            )
            run_service.update_status(
                run_id, "completed",
                itinerary_id=itinerary_id,
                total_steps=len(planning_trace),
            )

            if user_id:
                ProfileSummarizerAgent().summarize(user_id, context, itinerary)

            return {
                "success": True,
                "run_id": run_id,
                "itinerary_id": itinerary_id,
                "llm_used": self.use_llm,
                "itinerary": itinerary,
                "agent_results": {k: v.to_dict() for k, v in results.items()},
                "risk": risk_result.to_dict(),
                "planning_trace": planning_trace,
            }
        except Exception as e:
            logger.exception(f"[Planner] run_id={run_id} 流式规划失败")
            run_service.update_status(run_id, "failed", error_message=str(e))
            raise

    def _execute_fallback(
        self,
        context: dict,
        run_service: PlanningRunService,
        run_id: int,
        on_step: Callable[[dict], None] | None = None,
        initial_results: dict[str, AgentResult] | None = None,
    ) -> tuple[dict[str, AgentResult], Any, list[dict]]:
        """
        无 LLM 时的兜底执行：按固定顺序串行执行子Agent，并记录每一步。
        支持从 initial_results 恢复已完成的步骤（断点续跑）。
        Returns: (results, risk_result, planning_trace)
        """
        results: dict[str, AgentResult] = dict(initial_results or {})
        planning_trace: list[dict] = []

        def emit(step: dict, cached_result: AgentResult | None = None) -> None:
            planning_trace.append(step)
            run_service.add_step(
                run_id=run_id,
                step_number=step.get("step", 0),
                step_type=step["type"],
                content=step.get("content", ""),
                tool_name=step.get("tool"),
                tool_input=step.get("tool_input"),
                observation=step.get("result") if step["type"] == "observation" else None,
                cached_result=cached_result if step["type"] == "observation" else None,
                status="failed" if step.get("error") else "completed",
                duration_ms=step.get("result", {}).get("duration_ms", 0) if step.get("result") else 0,
            )
            if on_step:
                on_step(step)

        thought = {"type": "thought", "step": 0, "content": "LLM 未启用，使用串行子Agent兜底策略。"}
        emit(thought)

        agent_order = [
            ("weather", self.weather_agent),
            ("hotel", self.hotel_agent),
            ("restaurant", self.restaurant_agent),
            ("attraction", self.attraction_agent),
            ("transport", self.transport_agent),
        ]
        for idx, (name, agent) in enumerate(agent_order, start=1):
            result_key = f"{name}_result"
            if result_key in results and results[result_key].status == "completed":
                # 断点续跑：已完成的步骤直接回放，不再调用 Agent
                result = results[result_key]
                tool_step = {"type": "tool_call", "step": idx, "tool": name, "tool_input": {}}
                emit(tool_step)
                obs_step = {
                    "type": "observation",
                    "step": idx,
                    "tool": name,
                    "tool_input": {},
                    "result": agent_result_to_observation(result),
                }
                emit(obs_step, cached_result=result)
                continue

            tool_step = {"type": "tool_call", "step": idx, "tool": name, "tool_input": {}}
            emit(tool_step)

            result = agent.execute(context)
            results[result_key] = result

            obs_step = {
                "type": "observation",
                "step": idx,
                "tool": name,
                "tool_input": {},
                "result": agent_result_to_observation(result),
            }
            emit(obs_step, cached_result=result)

        risk_tool = {"type": "tool_call", "step": len(agent_order) + 1, "tool": "risk", "tool_input": {}}
        emit(risk_tool)
        risk_result = self.risk_agent.execute({**context, **results})
        risk_obs = {
            "type": "observation",
            "step": len(agent_order) + 1,
            "tool": "risk",
            "tool_input": {},
            "result": agent_result_to_observation(risk_result),
        }
        emit(risk_obs, cached_result=risk_result)

        run_service.update_status(run_id, "running", total_steps=len(planning_trace))
        return results, risk_result, planning_trace

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
        weather_result = results.get("weather_result")
        hotel_result = results.get("hotel_result")
        restaurant_result = results.get("restaurant_result")
        attraction_result = results.get("attraction_result")
        transport_result = results.get("transport_result")

        weather_data = weather_result.data if weather_result else {}
        hotels = hotel_result.data.get("hotels", []) if hotel_result else []
        restaurants = restaurant_result.data.get("restaurants", []) if restaurant_result else []
        attractions = attraction_result.data.get("attractions", []) if attraction_result else []
        transport = transport_result.data if transport_result else {}

        selected_hotel = hotels[0] if hotels else None
        h_lat = selected_hotel.get("latitude") if selected_hotel else None
        h_lon = selected_hotel.get("longitude") if selected_hotel else None

        # 按距离酒店远近排序，优先安排酒店附近的 POI
        def distance_to_hotel(poi: dict) -> float:
            return _haversine_km(
                h_lat, h_lon,
                poi.get("latitude") or 0, poi.get("longitude") or 0
            )

        if h_lat and h_lon:
            attractions = sorted(attractions, key=distance_to_hotel)
            restaurants = sorted(restaurants, key=distance_to_hotel)

        # 分配景点到每天，保证每天至少 1 个（景点总数不足时优先填满前几天）
        day_plans = []
        for i in range(days):
            day_attractions = []
            if i < len(attractions):
                day_attractions.append(attractions[i])
            # 若景点富余，每天再补一个
            second_idx = days + i
            if second_idx < len(attractions):
                day_attractions.append(attractions[second_idx])

            day_restaurants = restaurants[i * 2:(i + 1) * 2] if i * 2 < len(restaurants) else []

            activities = []
            if selected_hotel and i == 0:
                activities.append({"time": "入住", "name": selected_hotel["name"], "type": "hotel"})

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
            "hotel": selected_hotel["name"] if selected_hotel else "",
            "transport": transport.get("options", [{}])[0].get("name", ""),
            "days": day_plans,
            "tips": [r["message"] for r in risk_result.data.get("warnings", [])],
        }

    def _build_itinerary_prompt(self, context: dict, results: dict, risk_result) -> str:
        """构建LLM行程生成提示词"""
        empty = AgentResult(agent_type="", agent_name="")
        weather = results.get("weather_result", empty)
        hotel = results.get("hotel_result", empty)
        restaurant = results.get("restaurant_result", empty)
        attraction = results.get("attraction_result", empty)
        transport = results.get("transport_result", empty)

        return f"""请为以下旅行生成结构化行程：

【基本信息】
目的地: {context['destination']}
出发地: {context['origin']}
天数: {context['days']}天
人数: {context['travelers']}人
预算: ¥{context.get('budget', '未设定')}
风格: {context.get('style', 'balanced')}
节奏: {context.get('pace', context.get('style', 'balanced'))}

【个性化偏好】
兴趣: {context.get('interests', '无')}
必去: {context.get('must_visit', '无')}
避免: {context.get('avoid', '无')}
特殊需求: {context.get('special_needs', '无')}
季节: {context.get('season', '未设定')}
画像摘要: {context.get('llm_summary', '无')}

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

    def _save_itinerary(self, context: dict, itinerary: dict, results: dict,
                        planning_trace: list[dict] | None = None,
                        user_id: int | None = None) -> int:
        """保存行程到数据库"""
        try:
            from app.db.database import execute, get_db_connection
            conn = get_db_connection()

            # 保存行程
            it_id = execute(conn, """
                INSERT INTO itineraries (user_id, title, destination, origin, start_date, end_date,
                    traveler_count, budget, travel_style, status, llm_used, itinerary_json, planning_trace)
                VALUES (?, ?, ?, ?, date('now'), date('now', ?), ?, ?, ?, 'planned', ?, ?, ?)
            """, (
                user_id,
                itinerary.get("title", f"{context['destination']}之旅"),
                context["destination"],
                context["origin"],
                f"+{context['days']} days",
                context["travelers"],
                context.get("budget"),
                context.get("style", "balanced"),
                self.use_llm,
                json.dumps(itinerary, ensure_ascii=False),
                json.dumps(planning_trace or [], ensure_ascii=False),
            ))

            # 保存Agent日志
            for agent_type, result in results.items():
                usage = result.usage or {}
                execute(conn, """
                    INSERT INTO agent_logs (itinerary_id, agent_type, agent_name, status,
                        output_result, duration_ms, prompt_tokens, completion_tokens,
                        estimated_prompt_tokens, estimated_completion_tokens, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    it_id, result.agent_type, result.agent_name, result.status,
                    json.dumps(result.data, ensure_ascii=False),
                    result.duration_ms,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    usage.get("estimated_prompt_tokens", 0),
                    usage.get("estimated_completion_tokens", 0),
                    result.error or "",
                ))

            conn.close()
            return it_id

        except Exception as e:
            logger.error(f"[Planner] 保存行程失败: {e}")
            return 0
