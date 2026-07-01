"""
PlanningState - 多 Agent 规划的类型化黑板（单一事实源）

设计目标：让下游 Agent 在执行时通过领域访问器读到上游 Agent 的输出，
实现真正的 subagent 间数据依赖通信，而不是由 Planner 事后拼装。

- inputs: 原始业务参数（destination/days/budget/...），即原 context 业务字段。
- _results: agent_type -> AgentResult，用 Lock 保护，支撑多线程并行写入。
- 领域访问器（hotel_location / is_rainy / high_altitude_pois 等）：
  把"读上游数据"的语义集中在这里，下游 Agent 不必知道底层数据结构。
"""
from __future__ import annotations

import threading
from typing import Any

from app.agents.v3.base import AgentResult

# 天气 description 中代表降水的关键词
_RAINY_KEYWORDS = ("雨", "雪", "暴雨", "大雨", "雷阵雨", "台风", "冰雹")


class PlanningState:
    """线程安全的规划黑板。"""

    def __init__(self, inputs: dict[str, Any] | None = None):
        self.inputs: dict[str, Any] = dict(inputs or {})
        self._results: dict[str, AgentResult] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 基础读写
    # ------------------------------------------------------------------
    def put(self, result: AgentResult) -> None:
        """按 result.agent_type 存入结果（线程安全）。"""
        if result is None or not getattr(result, "agent_type", None):
            return
        with self._lock:
            self._results[result.agent_type] = result

    def get(self, agent_type: str) -> AgentResult | None:
        with self._lock:
            return self._results.get(agent_type)

    def has(self, agent_type: str) -> bool:
        with self._lock:
            return agent_type in self._results

    def data(self, agent_type: str) -> dict[str, Any]:
        """取某 agent 结果的 data（不存在或失败时返回空 dict）。"""
        res = self.get(agent_type)
        if res is None or getattr(res, "data", None) is None:
            return {}
        return res.data

    def status(self, agent_type: str) -> str:
        """取某 agent 的状态（'completed'/'failed'/''）。"""
        res = self.get(agent_type)
        return getattr(res, "status", "") or ""

    def all_results(self) -> dict[str, AgentResult]:
        with self._lock:
            return dict(self._results)

    # ------------------------------------------------------------------
    # 兼容层：产出旧代码期望的 {agent_type}_result 结构
    # ------------------------------------------------------------------
    def as_result_dict(self) -> dict[str, AgentResult]:
        """返回 {"hotel_result": AgentResult, ...}，与旧 results dict 键名一致。"""
        with self._lock:
            return {f"{t}_result": r for t, r in self._results.items()}

    def as_legacy_context(self) -> dict[str, Any]:
        """业务参数 + {agent_type}_result，供尚未迁移到 state 的 Agent（如 RiskAgent）使用。"""
        ctx = dict(self.inputs)
        ctx.update(self.as_result_dict())
        return ctx

    # ------------------------------------------------------------------
    # 领域访问器：下游 Agent 通过这些"读"上游（通信语义集中于此）
    # ------------------------------------------------------------------
    @property
    def selected_hotel(self) -> dict[str, Any] | None:
        """当前选中的酒店（取推荐列表第一家）。"""
        hotels = self.data("hotel").get("hotels") or []
        return hotels[0] if hotels else None

    @property
    def hotel_location(self) -> tuple[float, float] | None:
        """选中酒店的 (纬度, 经度)，缺失则返回 None。"""
        hotel = self.selected_hotel
        if not hotel:
            return None
        lat = hotel.get("latitude")
        lon = hotel.get("longitude")
        if lat is None or lon is None:
            return None
        try:
            return float(lat), float(lon)
        except (TypeError, ValueError):
            return None

    @property
    def weather_current(self) -> dict[str, Any]:
        return self.data("weather").get("current") or {}

    @property
    def is_rainy(self) -> bool:
        """当前或预报中是否有降水（下游可据此偏好室内景点）。"""
        weather = self.data("weather")
        desc = (self.weather_current.get("description") or "")
        if any(k in desc for k in _RAINY_KEYWORDS):
            return True
        for day in weather.get("forecast") or []:
            d = (day.get("description") or day.get("weather") or "")
            if any(k in d for k in _RAINY_KEYWORDS):
                return True
        return False

    @property
    def high_altitude_pois(self) -> list[dict[str, Any]]:
        """上游景点 Agent 识别出的高海拔景点（供风控使用）。"""
        return self.data("attraction").get("altitude_risks") or []

    @property
    def transport_min_cost(self) -> int | None:
        """交通方案中的最低花费（供预算联动）。"""
        options = self.data("transport").get("options") or []
        prices = [o.get("price") for o in options if isinstance(o.get("price"), (int, float))]
        return int(min(prices)) if prices else None
