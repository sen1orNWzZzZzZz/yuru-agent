"""
PlannerAgent - 中央调度器
协调所有子Agent，整合结果生成最终行程
"""
import json
import logging
import time
from datetime import datetime
from typing import Any, Callable

from app.agents.v3.base import AgentResult
from app.agents.v3.planning_run import PlanningRunService
from app.agents.v3.poi_agent import AttractionAgent, HotelAgent, RestaurantAgent, TransportAgent
from app.agents.v3.profile_agent import ProfileSummarizerAgent
from app.agents.v3.risk_agent import RiskAgent
from app.agents.v3.scheduler import AgentScheduler
from app.agents.v3.state import PlanningState
from app.agents.v3.tools import build_tool_registry
from app.agents.v3.weather_agent import WeatherAgent
from app.integrations.llm_client import LLMClient
from app.tracing import (
    generate_span_id,
    get_span_id,
    get_trace_id,
    record_span,
    set_span_id,
)

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


# 形态 B：LLM 单次规划产出的执行计划 Schema
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string", "description": "规划思路：为什么选这些 agent、如何分工。"},
        "agents": {
            "type": "array",
            "description": "本次需要调用的 agent 列表（不必关心顺序，系统会按依赖自动编排并行执行）。",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["weather", "hotel", "restaurant", "attraction", "transport", "risk"],
                        "description": "agent 类型。",
                    },
                    "input": {
                        "type": "object",
                        "description": "传给该 agent 的专属参数（如酒店区域、菜系、景点关键词），可为空。",
                    },
                },
                "required": ["type"],
            },
        },
    },
    "required": ["agents"],
}

# 完整的默认执行计划（LLM 不可用或规划失败时使用）
DEFAULT_PLAN = ["weather", "hotel", "restaurant", "attraction", "transport", "risk"]
# 必须包含的关键 agent，缺失则强制补齐
ESSENTIAL_AGENTS = ["hotel", "restaurant", "attraction", "risk"]


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
        cancel_event=None,
    ) -> dict[str, Any]:
        """
        主规划流程（同步版）
        - 可外部传入 run_id，复用已有 PlanningRun
        - 形态 B：LLM 一次性规划要调用的 agent + 参数，AgentScheduler 依赖驱动并行执行
        - 完成后更新 Run 状态
        """
        total_start = time.time()
        plan_start_dt = datetime.now()
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

        trace_id = get_trace_id()
        plan_span_id = None
        plan_parent_span_id = get_span_id()
        if trace_id:
            plan_span_id = generate_span_id()
            set_span_id(plan_span_id)

        logger.info(f"[Planner] 开始规划: {destination} {days}天 {travelers}人 trace_id={trace_id}")

        run_service = PlanningRunService()
        if run_id is None:
            run_id = run_service.create_run(user_id, context, trace_id=trace_id)
        else:
            run_service.update_status(run_id, "running")

        try:
            if self.use_llm:
                plan_types, agent_inputs, plan_thought = self._plan_once(context)
            else:
                plan_types, agent_inputs, plan_thought = DEFAULT_PLAN, {}, "LLM 未启用，执行全部子 Agent。"

            results, risk_result, planning_trace = self._execute_agents(
                context, run_service, run_id,
                plan=plan_types, agent_inputs=agent_inputs, plan_thought=plan_thought,
                cancel_event=cancel_event,
            )

            if self.use_llm:
                itinerary = self._generate_with_llm(context, results, risk_result)
            else:
                itinerary = self._generate_template(context, results, risk_result)

            total_duration = int((time.time() - total_start) * 1000)
            itinerary_id = self._save_itinerary(
                context, itinerary, results,
                planning_trace=planning_trace,
                user_id=user_id,
                trace_id=trace_id,
            )
            run_service.update_status(
                run_id, "completed",
                itinerary_id=itinerary_id,
                total_steps=len(planning_trace),
            )

            if user_id:
                ProfileSummarizerAgent().summarize(user_id, context, itinerary)

            if trace_id and plan_span_id:
                record_span(
                    name="planner.plan",
                    service="planner",
                    start_time=plan_start_dt,
                    end_time=datetime.now(),
                    status="ok",
                    meta={"use_llm": self.use_llm, "run_id": run_id, "itinerary_id": itinerary_id},
                    span_id=plan_span_id,
                    parent_span_id=plan_parent_span_id,
                    trace_id=trace_id,
                )

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
            if trace_id and plan_span_id:
                record_span(
                    name="planner.plan",
                    service="planner",
                    start_time=plan_start_dt,
                    end_time=datetime.now(),
                    status="error",
                    meta={"use_llm": self.use_llm, "run_id": run_id},
                    error=str(e),
                    span_id=plan_span_id,
                    parent_span_id=plan_parent_span_id,
                    trace_id=trace_id,
                )
            run_service.update_status(run_id, "failed", error_message=str(e))
            raise
        finally:
            if trace_id:
                set_span_id(plan_parent_span_id)

    def plan_stream(
        self,
        context: dict[str, Any],
        user_id: int | None,
        on_step: Callable[[dict], None] | None = None,
        run_id: int | None = None,
        initial_results: dict[str, AgentResult] | None = None,
        cancel_event=None,
    ) -> dict[str, Any]:
        """
        流式规划流程
        与 plan() 逻辑一致，但每产生一个 step 都会调用 on_step 回调，供 SSE 实时推送。
        支持外部传入 run_id 和 initial_results（断点续跑）。
        """
        trace_id = get_trace_id()
        run_service = PlanningRunService()
        if run_id is None:
            run_id = run_service.create_run(user_id, context, trace_id=trace_id)
        else:
            run_service.update_status(run_id, "running")

        def combined_on_step(step: dict) -> None:
            if on_step:
                on_step(step)

        try:
            if self.use_llm:
                plan_types, agent_inputs, plan_thought = self._plan_once(context)
            else:
                plan_types, agent_inputs, plan_thought = DEFAULT_PLAN, {}, "LLM 未启用，执行全部子 Agent。"

            results, risk_result, planning_trace = self._execute_agents(
                context, run_service, run_id,
                plan=plan_types, agent_inputs=agent_inputs, plan_thought=plan_thought,
                on_step=combined_on_step,
                initial_results=initial_results,
                cancel_event=cancel_event,
            )

            if self.use_llm:
                itinerary = self._generate_with_llm(context, results, risk_result)
            else:
                itinerary = self._generate_template(context, results, risk_result)

            itinerary_id = self._save_itinerary(
                context, itinerary, results,
                planning_trace=planning_trace,
                user_id=user_id,
                trace_id=trace_id,
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

    def _agents_by_type(self) -> dict[str, Any]:
        """agent_type -> agent 实例，供调度器使用。"""
        return {
            "weather": self.weather_agent,
            "hotel": self.hotel_agent,
            "restaurant": self.restaurant_agent,
            "attraction": self.attraction_agent,
            "transport": self.transport_agent,
            "risk": self.risk_agent,
        }

    def _close_dependencies(self, plan: list[str]) -> list[str]:
        """把 plan 中每个 agent 的 depends_on 上游补齐（依赖闭包），保持相对顺序无关。"""
        agents = self._agents_by_type()
        result = list(plan)
        changed = True
        while changed:
            changed = False
            for t in list(result):
                for dep in getattr(agents.get(t), "depends_on", []):
                    if dep in agents and dep not in result:
                        result.append(dep)
                        changed = True
        return result

    def _plan_once(self, context: dict) -> tuple[list[str], dict[str, dict], str]:
        """
        形态 B：一次 LLM 调用产出执行计划（调哪些 agent + 各自参数）。
        失败时回退到完整默认计划。
        Returns: (plan_types, agent_inputs, thought)
        """
        tools_desc = json.dumps(self.tool_registry.definitions(), ensure_ascii=False, indent=2)
        system = (
            "你是旅行规划系统的中枢 PlannerAgent。你只需一次性决定要调用哪些子 Agent 以及各自参数，"
            "无需关心执行顺序（系统会按依赖关系自动并行编排）。请严格按 JSON Schema 输出。"
        )
        user = f"""根据用户需求，规划本次需要调用的子 Agent。

【用户需求】
目的地: {context.get('destination', '')}
天数: {context.get('days', 3)}天
人数: {context.get('travelers', 2)}人
预算: {context.get('budget', '未设定')}
出发地: {context.get('origin', '')}
风格: {context.get('style', '')}
兴趣: {context.get('interests', '无')}
必去: {context.get('must_visit', '无')}
避免: {context.get('avoid', '无')}
特殊需求: {context.get('special_needs', '无')}

【可用 Agent 及参数】
{tools_desc}

要求：
- 通常应包含 hotel、restaurant、attraction、risk；如需交通/天气也一并列出。
- 为需要特定参数的 agent 填写 input（如酒店区域 district、菜系 cuisine_type、景点关键词 keywords）。
- 不要重复列同一个 agent。"""

        result = self.llm.chat_structured(
            system, user, response_schema=PLAN_SCHEMA,
            temperature=0.3, max_tokens=800, use_cache=False,
        )
        if not result.get("success"):
            logger.warning(f"[Planner] 单次规划失败，回退默认计划: {result.get('error')}")
            return DEFAULT_PLAN, {}, f"规划调用失败（{result.get('error')}），执行全部子 Agent。"

        data = result.get("data", {})
        plan_types: list[str] = []
        agent_inputs: dict[str, dict] = {}
        agents = self._agents_by_type()
        for item in data.get("agents", []) or []:
            t = item.get("type")
            if t in agents and t not in plan_types:
                plan_types.append(t)
                if item.get("input"):
                    agent_inputs[t] = item["input"]

        # 补齐关键 agent，再做依赖闭包（如选了 attraction 会自动拉入 hotel/weather）
        for essential in ESSENTIAL_AGENTS:
            if essential not in plan_types:
                plan_types.append(essential)
        plan_types = self._close_dependencies(plan_types)

        return plan_types, agent_inputs, data.get("thought", "")

    def _execute_agents(
        self,
        context: dict,
        run_service: PlanningRunService,
        run_id: int,
        plan: list[str],
        agent_inputs: dict[str, dict],
        plan_thought: str,
        on_step: Callable[[dict], None] | None = None,
        initial_results: dict[str, AgentResult] | None = None,
        cancel_event=None,
    ) -> tuple[dict[str, AgentResult], Any, list[dict]]:
        """
        用 AgentScheduler 依赖驱动并行执行 plan 中的子 Agent，并记录每一步。
        支持从 initial_results 恢复已完成的步骤（断点续跑）。
        Returns: (results, risk_result, planning_trace)
        """
        state = PlanningState(context)

        # 断点续跑：把已完成的结果预置进 state，调度器会自动跳过
        for res in (initial_results or {}).values():
            if res is not None and getattr(res, "status", None) == "completed":
                state.put(res)

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
                trace_id=get_trace_id(),
            )
            if on_step:
                on_step(step)

        emit({"type": "thought", "step": 0,
              "content": plan_thought or f"依赖驱动并行执行子 Agent：{', '.join(plan)}"})

        scheduler = AgentScheduler(self._agents_by_type(), max_workers=5, cancel_event=cancel_event)
        scheduler.run(state, plan=plan, emit=emit, agent_inputs=agent_inputs)

        results = state.as_result_dict()
        risk_result = state.get("risk") or AgentResult(
            agent_type="risk", agent_name="风控Agent", status="completed",
            data={"warnings": [], "is_safe": True, "warning_count": 0},
        )

        run_service.update_status(run_id, "running", total_steps=len(planning_trace))
        return results, risk_result, planning_trace

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

        # 注：景点/餐厅的就近排序已下沉到 AttractionAgent / RestaurantAgent（依赖 hotel 位置），
        # 此处直接使用它们查询时排好序的结果，不再做事后排序。

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
                        user_id: int | None = None,
                        trace_id: str | None = None) -> int:
        """保存行程到数据库"""
        try:
            from app.db.database import execute, get_db_connection
            conn = get_db_connection()

            # 保存行程
            it_id = execute(conn, """
                INSERT INTO itineraries (user_id, title, destination, origin, start_date, end_date,
                    traveler_count, budget, travel_style, status, llm_used, itinerary_json, planning_trace, trace_id)
                VALUES (?, ?, ?, ?, date('now'), date('now', ?), ?, ?, ?, 'planned', ?, ?, ?, ?)
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
                trace_id,
            ))

            # 保存Agent日志
            for agent_type, result in results.items():
                usage = result.usage or {}
                execute(conn, """
                    INSERT INTO agent_logs (itinerary_id, agent_type, agent_name, status,
                        output_result, duration_ms, prompt_tokens, completion_tokens,
                        estimated_prompt_tokens, estimated_completion_tokens, error_message, trace_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    it_id, result.agent_type, result.agent_name, result.status,
                    json.dumps(result.data, ensure_ascii=False),
                    result.duration_ms,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    usage.get("estimated_prompt_tokens", 0),
                    usage.get("estimated_completion_tokens", 0),
                    result.error or "",
                    trace_id,
                ))

            conn.close()
            return it_id

        except Exception as e:
            logger.error(f"[Planner] 保存行程失败: {e}")
            return 0
