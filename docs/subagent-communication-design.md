# Subagent 数据依赖通信 —— 设计方案

> 目标：让下游 agent 在**查询时**就拿到上游 agent 的输出，实现真正的 subagent 间数据通信，
> 而不是像现在这样由 Planner 事后做后处理。
>
> 范围：V3 (`app/agents/v3/`)。本文只做设计，不含实现。

---

## 1. 现状回顾（问题定义）

当前是**中枢-辐条（star）架构**，子 agent 是无状态叶子节点：

- 每个 agent 只被 `BaseAgentV3.execute(context)` 单向调用（`base.py:71`），彼此不感知。
- Planner 用弱类型 `results: dict[str, AgentResult]` 单向收集输出（`planner_runtime.py:219,299`），
  靠 `_TOOL_KEY_PREFIX` 拼 `xxx_result` 键名维系一致性。
- 只有 RiskAgent 通过 `tools.py:130-134` 被动拿到 `**results`，其余 agent 互不通信。
- "空间推荐"（酒店位置排序景点/餐厅）是 Planner **拿到全部结果后的后处理**
  （`planner.py:494-507`），agent 运行时根本不知道酒店在哪。
- 声称并行的 `_execute_sub_agents()`（`planner.py:427-451`，`ThreadPoolExecutor`）是**死代码**，从未被调用；
  两条实际路径（LLM 决策循环 / fallback）都是串行。

### 真实存在但未被利用的数据依赖

| 上游 | 下游 | 应传递 | 现状 |
|---|---|---|---|
| Hotel → | Restaurant / Attraction | 酒店经纬度 → 就近查询/排序 | ❌ Planner 事后排序 |
| Weather → | Attraction | 雨天 → 优先室内景点 | ❌ 完全没用 |
| Attraction → | Risk | 高反景点 → 安全预警 | ⚠️ 有，但仅在最后 |
| Transport → | Risk / Hotel | 交通花费 → 预算联动 | ❌ 各花各的 |

---

## 2. 设计总览

三个组件，从下到上：

```
┌──────────────────────────────────────────────────────────┐
│  PlanningState  (类型化黑板 / 单一事实源, 线程安全)          │
│  - inputs: 原始业务参数                                       │
│  - results: agent_type -> AgentResult  (Lock 保护)          │
│  - 领域访问器: hotel_location / is_rainy / high_altitude_pois │
└──────────────────────────────────────────────────────────┘
             ▲ 读              ▲ 写
             │                 │
┌──────────────────────────────────────────────────────────┐
│  依赖声明 depends_on  (谁读谁的数据, 声明式)                  │
└──────────────────────────────────────────────────────────┘
             ▲
┌──────────────────────────────────────────────────────────┐
│  形态 B 执行 (生产级)                                        │
│  1) LLM 单次规划: 产出"调哪些 agent + 参数" (1 次往返)        │
│  2) AgentScheduler: 依赖就绪即提交, 线程池并行, 每 agent 超时 │
│     - 依赖驱动 (非分层 barrier): hotel 完成 restaurant 立即起 │
│     - 线程内 restore_context 透传 trace                      │
└──────────────────────────────────────────────────────────┘
```

核心思想：**LLM 只做一次高层规划（调谁+参数），执行顺序由 `depends_on` 决定、
由调度器并行执行**。下游 agent 在查询时通过 `PlanningState` 读到上游输出，实现数据依赖通信。

---

## 3. 组件一：PlanningState（类型化黑板）

用一个对象替换裸 `results` dict，把"通信语义"收敛到领域访问器里。

### 结构

```
PlanningState
  inputs: dict                      # destination/days/budget/... （原 context 业务字段）
  _results: dict[str, AgentResult]  # agent_type -> result

  # 基础读写
  put(result: AgentResult)          # 按 result.agent_type 存
  get(agent_type) -> AgentResult | None
  has(agent_type) -> bool

  # 领域化访问器（下游 agent 通过这些"读"上游，通信语义在此）
  selected_hotel   -> dict | None            # hotels[0]
  hotel_location   -> tuple[float,float]|None # 酒店经纬度
  is_rainy         -> bool                    # 由 weather_result 派生
  weather_summary  -> dict
  high_altitude_pois -> list                  # 由 attraction_result 派生
  transport_cost   -> int | None
```

### 收益
- **类型安全**：下游用 `state.hotel_location` 而不是 `results["hotel_result"].data["hotels"][0][...]`。
- **通信语义集中**：新增一条依赖 = 加一个访问器，agent 侧代码干净。
- **向后兼容**：`inputs` 就是今天的业务 context；可保留 `state.as_legacy_context()` 供过渡期使用。

---

## 4. 组件二：依赖声明 + 解析器

### 4.1 在 BaseAgentV3 上加依赖声明

```
class BaseAgentV3:
    agent_type: str = ""          # 已有语义，显式化
    depends_on: list[str] = []    # 新增：上游 agent_type 列表
```

各 agent 的依赖（这就是"谁跟谁通信"的声明式描述）：

| Agent | agent_type | depends_on |
|---|---|---|
| WeatherAgent | weather | `[]` |
| HotelAgent | hotel | `[]` |
| TransportAgent | transport | `[]` |
| RestaurantAgent | restaurant | `["hotel"]` |
| AttractionAgent | attraction | `["hotel", "weather"]` |
| RiskAgent | risk | `["hotel", "attraction", "weather", "transport"]` |

拓扑分层（并行度自然浮现）：

```
Layer 0 (并行):  Weather   Hotel   Transport
Layer 1 (并行):  Restaurant   Attraction
Layer 2:         Risk
```

### 4.2 agent 读取上游的接口约定

改造 `_execute_with_db`，让它能拿到 state（两种落地方式二选一）：

- **方案 P1（最小改动）**：把 state 注入到传入的 context —— `context["_state"] = state`，
  agent 内 `state = context["_state"]`。签名不变，改动面最小。
- **方案 P2（更干净）**：新增 `execute(state: PlanningState)` 重载，逐步迁移子 agent。

推荐 **P1 起步**（不破坏现有两条路径与 tools.py 的 lambda 包装），后续再演进到 P2。

下游 agent 改造示意（AttractionAgent）：

```
def _execute_with_db(self, context):
    state = context["_state"]
    hotel_loc = state.hotel_location          # ← 读上游 Hotel
    prefer_indoor = state.is_rainy            # ← 读上游 Weather
    pois = self._query_pois(context, prefer_indoor=prefer_indoor)
    if hotel_loc:                             # ← 查询时就按酒店就近排序
        pois.sort(key=lambda p: haversine(hotel_loc, poi_loc(p)))
    return {"attractions": pois, ...}
```

→ 这样 `planner.py:494-507` 的事后排序逻辑可以**删除**（职责下沉到 agent，架构更内聚）。

### 4.3 依赖不变式（由调度器强制执行）

依赖关系表达的核心不变式：**任一 agent 执行时，其 `depends_on` 的上游必已在 state 中。**

本期由 §5.2 的 **AgentScheduler** 强制这条不变式（依赖就绪才提交），不再需要递归 `ensure()`。
`depends_on` 是声明，调度器是执行者——声明与执行分离，比递归解析更适合并发。

> （递归 `ensure()` 只在需要"惰性按需拉取单个 agent"时才有用，本期用不到；留作后续
> 反馈回环阶段的备选。）

---

## 5. 组件三：生产级执行 —— 形态 B（LLM 规划一次 + 并行执行）

> 决策：采用**形态 B**。抛弃当前 PlannerRuntime 的 per-step ReAct 循环
> （`planner_runtime.py:228-237` 每步一次 LLM 往返，串行且昂贵），
> 改为 **LLM 一次规划 → 依赖驱动调度器并行执行**。
>
> 理由：per-step 循环下即使有线程池，agent 也只能被 LLM 逐个"点名"，并发用不上；
> 延迟 = N 次串行 LLM 往返。形态 B 把 LLM 往返压到 1 次，其余时间全花在并行 I/O 上。

### 5.1 单次规划调用（Plan-Once）

用一次 `chat_structured`（复用 `llm_client.py:502` 的结构化输出能力）产出一份"执行计划"，
而不是每步问一次：

```
PLAN_SCHEMA = {
  "agents": [                       # LLM 决定要调哪些 agent 及其参数
    {"type": "hotel",      "input": {"district": "西湖区", "budget_level": "high"}},
    {"type": "weather",    "input": {}},
    {"type": "attraction", "input": {"keywords": "自然风光"}},
    {"type": "restaurant", "input": {"cuisine_type": "本帮菜"}},
    {"type": "transport",  "input": {}},
    ...
  ],
  "thought": "..."                  # 供 trace 展示的规划理由
}
```

- LLM 只决定 **调谁 + 传什么参数**，**不决定顺序**——顺序由 `depends_on` 拓扑推导。
- 若 LLM 漏掉关键 agent（hotel/restaurant/attraction/risk），调度前用现有
  `_force_call_tool` 同款逻辑补齐（`planner_runtime.py:311-317`）。
- LLM 不可用时，退化为"全量 agent + 空参数"的默认计划——即原 fallback 语义，无需单独一条代码路径。

### 5.2 依赖驱动调度器（AgentScheduler）—— 生产级并发核心

**不是简单的分层 barrier**，而是"依赖一就绪就提交"的完成驱动调度。
因为 restaurant 只依赖 hotel、attraction 依赖 hotel+weather——分层会让 restaurant
白等 weather；依赖驱动则 hotel 一完成 restaurant 立即起跑。

```
class AgentScheduler:
    def run(self, plan, state, registry):
        pool = ThreadPoolExecutor(max_workers=self.max_workers)
        pending = {a.type: set(agent.depends_on) for a in plan.agents}
        completed, futures = set(), {}
        parent_ctx = current_context()                      # 主线程 trace 上下文

        def run_agent(agent_type):
            restore_context(parent_ctx)                     # ← 线程内透传 trace，父子 span 不丢
            agent = registry.by_type(agent_type)
            ctx = {**state.inputs, "_state": state}
            return agent.execute(ctx)                       # 复用现有 execute，零改动

        while pending or futures:
            # 依赖已满足的，立即提交（不等同层其他人）
            for t in [t for t, deps in pending.items() if deps <= completed]:
                futures[pool.submit(run_agent, t)] = t
                del pending[t]
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for f in done:
                t = futures.pop(f)
                try:
                    result = f.result(timeout=AGENT_TIMEOUT) # ← 每 agent 超时
                except Exception as e:
                    result = AgentResult(agent_type=t, status="failed", error=str(e))
                state.put(result)                            # ← 线程安全写（见 §5.4）
                completed.add(t)
```

- **最大并行度自然浮现**：Layer0(weather/hotel/transport) 三个真并行，
  hotel 完成后 restaurant/attraction 立即接力，risk 等全部上游。
- **救活死代码**：`_execute_sub_agents`（`planner.py:427-451`）的 `restore_context` 范式直接复用。
- **兑现文档承诺**："并行执行子 agent"从宣称变成事实。

### 5.3 生产级韧性（这才是"贴近生产"的关键，不只是线程池）

| 关注点 | 现状 | 本设计 |
|---|---|---|
| **每 agent 超时** | ❌ 无，一个慢 API 拖垮整个 plan | `future.result(timeout=AGENT_TIMEOUT)` |
| **错误隔离/降级** | ⚠️ 异常包成 failed（`base.py:149`）但下游不降级 | 上游 failed → 下游用降级输入（如拿不到酒店坐标退回市中心排序）继续 |
| **取消/断连** | ❌ 客户端断开（`asyncio.to_thread` main.py:545）线程仍跑 | 传入 `cancel_event`，调度循环每轮检查，已提交的 future 尽力 cancel |
| **有界并发** | ❌ 死代码里 `max_workers=5` 硬编码 | `max_workers` 与 httpx 连接池、SQLite 写并发对齐配置 |
| **可观测性** | ✅ trace 板块已具备 | 保证线程内 `restore_context`，并发下 span 父子不丢（见 §9） |

### 5.4 线程安全的 state

`PlanningState.put/get` 及领域访问器（聚合读 _results）用 `threading.Lock` 保护。
调度器保证"下游提交前上游已 `state.put` 并 join"，故跨依赖读天然 happens-before；
锁只防同层并发写 _results 与访问器聚合读的竞态。

### 5.5 tools.py 的收敛

- 单次规划后，**不再需要 per-tool 的 LLM 决策**，`ToolRegistry` 退化为"agent_type → agent"的查找表，
  `Tool` 的 `input_schema` 转而喂给 `PLAN_SCHEMA` 供 LLM 一次性填参。
- RiskAgent 手动拼 `**results` 的 hack（`tools.py:130-134`）**移除**，
  改由 `depends_on=["hotel","attraction","weather","transport"]` + state 统一供给。

---

## 6. 对 Trace 板块的增强（顺带收益）

你刚做的全局 trace 正好能可视化通信：

- `ensure()` 触发的"自动依赖补跑"记为带 `parent` 的 span，trace 树上能看到
  "attraction ← 触发 hotel"的因果边。
- 在 `agent_result_to_observation`（`tools.py:139`）的 summary 里加一个 `consumed_from`
  字段（如 attraction 记录 `consumed_from: {hotel_location, is_rainy}`），
  前端 trace/result 页能直接展示"这个 agent 用了上游谁的什么数据"。

---

## 7. 分阶段落地建议（供实现时参考）

1. **阶段 0 — SQLite 并发加固**（前置，最小改动）：在 `get_db_connection()`（`database.py:16`）
   加 `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=5000`。并发写的前提，先做（见 §9）。
2. **阶段 1 — 引入 PlanningState**：新增 `state.py`（含 `threading.Lock`），Planner 内部用它包装 `results`，
   对外行为不变（纯重构，可回归验证）。
3. **阶段 2 — 加 depends_on + AgentScheduler**：新增调度器（§5.2），先接**默认全量计划**（等价原 fallback），
   验证依赖驱动并行 + 超时 + trace 透传跑通。
4. **阶段 3 — 下游读上游**：Attraction 读 hotel/weather、Restaurant 读 hotel，
   删除 `planner.py:494-507` 事后排序。
5. **阶段 4 — 形态 B 单次规划**：用 `PLAN_SCHEMA` 一次性 LLM 规划取代 PlannerRuntime 的 per-step 循环，
   移除 RiskAgent 的 results hack；PlannerRuntime 保留为兜底或删除。
6. **阶段 5 — 韧性与 Trace 增强**：接入 `cancel_event`、错误降级；加依赖 span 与 `consumed_from`，
   更新文档中"并行"描述与本设计对齐。

每个阶段都可独立验证、独立回滚。阶段 0/1 是纯基础设施，风险最低，先落。

**已实现落地**：本设计文档的 6 个阶段已全部在代码中实现：
- `database.py` 加了 WAL + busy_timeout
- 新增 `app/agents/v3/state.py`、`scheduler.py`
- 删除旧 `planner_runtime.py`；`PlannerRuntime` per-step 循环被移除
- `PlannerAgent` 接入形态 B（`_plan_once` + `_execute_agents`）
- 下游 agent（Attraction/Restaurant/Risk）通过 `PlanningState` 消费上游数据
- 并发下 trace 经 `restore_context` 透传，attraction trace 含 `consumed_from`

---

## 8. 关键取舍与风险

- **形态 B 而非 per-step 循环**：LLM 往返从 N 次压到 1 次，其余时间并行 I/O；代价是失去"看一步走一步"的
  动态性——但旅行规划的 agent 集合基本固定，一次规划足够。
- **依赖驱动调度而非分层 barrier**：多榨取并行度（restaurant 不等 weather），代价是调度逻辑略复杂。
- **不引入消息队列/事件总线**：对 6 个 agent 的规模，DAG + 黑板足够，event bus 只增加调试成本。
- **不做 A2A 自然语言对话**：本期只做"数据依赖"，agent 不互发消息，由 state 中转，最易调试。
- **并行下的 trace 透传**：必须在线程内 `restore_context`，否则 span 丢父子关系（现有代码已有此模式）。
- **SQLite 单写者**：即便 WAL，SQLite 仍只允许一个写者；本期写入量极低（每 agent 几条 span/cache），
  WAL + busy_timeout 足够，无需把写抽回主线程（见 §9）。
- **循环依赖**：Risk→Attraction 的反馈回环**不在本期范围**（那是"反馈回环"目标，需收敛控制），
  本期依赖图保持无环 DAG。

---

## 9. SQLite 并发加固（诊断结论）

**现状诊断：**
- `get_db_connection()`（`database.py:16-21`）每次新建连接 + `check_same_thread=False` → 线程各开各的连接，✅ 无跨线程连接错。
- **全库未设 WAL / busy_timeout**（默认 `journal_mode=DELETE`、`busy_timeout=0`）。
- 并行时每个 agent 都写库：`record_span`（`tracing.py:101`，每个 agent 完成写一条）、`map.py:58,88`（POI 缓存）、llm_cache。
- 默认配置下多线程并发写 → **`database is locked` 立即报错**。
- 更隐蔽：`record_span` 静默吞写失败（`tracing.py:127-131`）→ **并发时 trace span 会静默丢失**，最需要观测时缺数据。

**方案（改一处，全项目受益）：** 在 `get_db_connection()` 内加：
```python
conn.execute("PRAGMA journal_mode = WAL")     # 读写不互斥
conn.execute("PRAGMA busy_timeout = 5000")    # 写冲突等待重试而非报错
```
- 以本期写入量，WAL + busy_timeout **足够**，**不采用**"写操作全回主线程序列化"（过度设计）。
- 注意：WAL 是 per-DB-file 持久设置，首次设置即生效；busy_timeout 是 per-connection，每次连接都设。
```
