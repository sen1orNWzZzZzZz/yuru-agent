# 灵动旅心 V3 - 智能旅游多Agent规划系统

> **PlannerAgent + 子Agent架构** | LLM/天气/地图API集成 | 数据库Mock数据

---

## 一、架构设计

参考 [hello-agents 智能旅行助手](https://github.com/datawhalechina/hello-agents) 架构，采用 **PlannerAgent（中央调度器）+ 多个子Agent** 协作模式：

```
用户请求
  │
  ▼
PlannerAgent.plan() ── 解析需求，协调执行
  │
  ├── WeatherAgent ─────── 查询目的地天气 + 穿着建议
  ├── HotelAgent ───────── 数据库查询酒店 + 小红书口碑 + 高德POI fallback
  ├── RestaurantAgent ──── 数据库查询餐厅 + 特色菜品 + 高德POI fallback
  ├── AttractionAgent ──── 数据库查询景点 + 高反风险评估 + 高德POI fallback
  ├── TransportAgent ───── 飞机/高铁/自驾方案对比
  │         (以上5个Agent并行执行 ThreadPool)
  │
  └── RiskAgent ────────── 综合风控检查（预算/安全/时间）
  │
  ▼
LLM整合生成行程 / 模板生成（降级）
  │
  ▼
SQLite数据库持久化 + 结果返回
  │
  ▼
登录用户：自动总结并更新 user_profiles
```

---

## 二、项目结构

```
python-travel-agent/
├── app/
│   ├── db/                          # 数据库模块
│   │   ├── init.sql                 # 建表脚本 (users/user_profiles/itineraries/agent_logs/request_logs/llm_cache/external_poi_cache 等)
│   │   ├── seed.sql                 # 种子数据 (15城市/60酒店/45餐厅/75景点/30小红书)
│   │   └── database.py              # SQLite连接管理 + 工具函数
│   │
│   ├── integrations/                # 外部服务集成
│   │   ├── config_manager.py        # API配置统一管理 (LLM/天气/地图)
│   │   ├── llm_client.py            # 通用OpenAI格式LLM客户端 + tiktoken 预估
│   │   ├── weather.py               # 天气API (OpenWeatherMap/和风天气)
│   │   └── map.py                   # 地图API (高德/百度) + POI 缓存
│   │
│   ├── agents/v3/                   # V3 Agent架构
│   │   ├── base.py                  # Agent抽象基类
│   │   ├── planner.py               # PlannerAgent 中央调度器
│   │   ├── profile_agent.py         # 用户画像自动总结 Agent
│   │   ├── weather_agent.py         # 天气查询Agent
│   │   ├── poi_agent.py             # POI数据Agent (酒店/餐厅/景点)
│   │   └── risk_agent.py            # 风控Agent
│   │
│   ├── auth.py                      # 极简本地认证 (bcrypt + Session Cookie)
│   ├── templates/                   # Jinja2模板
│   │   ├── base_v3.html             # 基础模板
│   │   ├── index.html               # 首页
│   │   ├── plan.html                # 规划页面
│   │   ├── result.html              # 结果页面
│   │   ├── login.html               # 登录/注册页
│   │   ├── admin_v3.html            # 管理后台
│   │   └── dashboard.html           # 系统观测大盘
│   │
│   ├── static/                      # 静态资源
│   ├── main.py                      # FastAPI主入口 (V3)
│   ├── mcp_server.py                # MCP SSE 服务器
│   └── llm/                         # V2 LLM模块 (兼容保留)
│
├── run.py                           # 启动脚本
└── README.md                        # 本文档
```

---

## 三、核心功能

### 3.1 Agent协作

| Agent | 职责 | 数据源 |
|-------|------|--------|
| **PlannerAgent** | 调度协调，整合结果生成行程 | 所有子Agent输出 |
| **WeatherAgent** | 天气查询 + 穿着建议 | OpenWeatherMap/和风天气/Mock |
| **HotelAgent** | 酒店推荐 + 反水军评分 | SQLite数据库 |
| **RestaurantAgent** | 餐厅推荐 + 特色菜品 | SQLite数据库 |
| **AttractionAgent** | 景点推荐 + 高反风险评估 | SQLite数据库 |
| **TransportAgent** | 飞机/高铁/自驾方案 | 内置距离数据 |
| **RiskAgent** | 预算/安全/时间风险检测 | 所有Agent结果 |

### 3.2 可观测大盘

独立页面 `/admin/dashboard` 按数据层级展示：

- **L1 全局概览**：请求数、延迟、缓存命中率、Agent 成功率、Token 消耗、LLM 调用次数
- **L2 趋势分析**：Token 消耗趋势、请求量/错误/延迟趋势
- **L3 细粒度分布**：Agent 调用占比、Agent 成功率明细
- **L4 明细排查**：请求日志表、Agent 调用日志表（含预估/实际 Token）

支持 24h/7d/30d 切换与自动刷新。

### 3.3 tiktoken Token 预估

每次调用 LLM 前，使用 `tiktoken` 估算 prompt/completion token，并记录到 `agent_logs`。
Dashboard 同步展示“预估 vs 实际 Token”与估算准确率。

### 3.4 用户画像与自动总结

- 极简本地认证：用户名/密码 + Session Cookie（`/login`）
- 用户可手动维护画像：`/api/users/me/profile`
- 每次规划结束后，自动调用 `ProfileSummarizerAgent` 总结偏好并更新画像
- 规划时会合并画像默认值与本次请求参数（请求参数优先级更高）

### 3.5 高德/百度 POI 搜索 fallback

POI Agent 在本地数据不足时，自动调用配置好的高德/百度地图 API 补充结果，并缓存 7 天。
模板生成行程时，会按酒店位置对景点/餐厅做距离排序。

### 3.6 API配置管理

通过管理后台 (`/admin`) 可配置：

- **LLM API**: OpenAI / SiliconFlow / Azure / 自托管vLLM
- **天气 API**: OpenWeatherMap / 和风天气(QWeather)
- **地图 API**: 高德地图(AMap) / 百度地图

未配置API时自动使用 **Mock数据降级**，不影响核心功能。

### 3.3 反水军检测

小红书笔记数据包含 **10条水军样本**，检测指标：
- 点赞/收藏比异常（如 2400:1）
- 内容空洞/模板化
- 新账号高互动
- 含外部链接引流
- 僵尸粉行为

---

## 四、快速开始

### 4.1 环境要求

- Python 3.10+
- pip

### 4.2 安装依赖

```bash
cd python-travel-agent
pip install -r requirements.txt
```

### 4.3 启动服务

```bash
python run.py
```

服务启动后访问：
- 首页: http://localhost:8000/
- 规划页面: http://localhost:8000/plan
- 登录/注册: http://localhost:8000/login
- 管理后台: http://localhost:8000/admin
- 观测大盘: http://localhost:8000/admin/dashboard
- API文档: http://localhost:8000/docs

### 4.4 配置API（可选）

1. 打开管理后台 http://localhost:8000/admin
2. 配置LLM API Key（支持OpenAI格式）
3. 配置天气API Key
4. 配置地图API Key（高德/百度）
5. 点击"测试连接"验证

**不配置也可使用**，系统会自动使用Mock数据。

---

## 五、API接口

### 核心规划

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v3/plan` | 创建行程规划（已登录用户合并画像） |
| POST | `/api/v3/plan/stream` | 流式创建行程规划 |
| GET | `/api/v3/plan/{id}` | 获取行程详情 |
| GET | `/api/v3/plans` | 行程列表 |

### 认证与用户画像

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/register` | 用户注册 |
| POST | `/api/auth/login` | 用户登录 |
| POST | `/api/auth/logout` | 退出登录 |
| GET | `/api/auth/me` | 当前用户信息及画像 |
| GET | `/api/users/me/profile` | 获取我的画像 |
| PUT | `/api/users/me/profile` | 更新我的画像 |

### 配置管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/admin/llm/config` | 保存LLM配置 |
| POST | `/api/admin/api/config` | 保存天气/地图配置 |
| POST | `/api/admin/test/{type}` | 测试API连接 |
| GET | `/api/admin/status` | 系统状态 |
| GET | `/api/admin/dashboard/summary` | 大盘概览 |
| GET | `/api/admin/dashboard/trends` | 大盘趋势 |
| GET | `/api/admin/dashboard/agents` | Agent 分布 |
| GET | `/api/admin/dashboard/requests` | 请求日志 |
| GET | `/api/admin/dashboard/agent-logs` | Agent 日志 |

### 数据查询

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v3/poi/{city}` | 城市POI数据 |
| GET | `/api/v3/xiaohongshu/{city}` | 小红书笔记 |
| GET | `/api/v3/weather/{city}` | 天气查询 |

---

## 六、数据库

### 6.1 初始化

首次启动自动初始化，或手动执行：

```bash
sqlite3 app/db/travel_v3.db < app/db/init.sql
sqlite3 app/db/travel_v3.db < app/db/seed.sql
```

### 6.2 数据规模

| 数据表 | 记录数 | 说明 |
|--------|--------|------|
| users / user_profiles | - | 用户与画像 |
| poi_data | 180+ | 60酒店 + 45餐厅 + 75景点 |
| external_poi_cache | - | 高德/百度 POI 缓存 |
| xiaohongshu_notes | 30 | 20正常 + 10水军样本 |
| llm_configs | 2 | 默认示例配置 |
| api_configs | 4 | 天气/地图示例配置 |
| itineraries / agent_logs / request_logs | - | 行程与运行日志 |

---

## 七、技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI |
| 数据库 | SQLite |
| LLM | OpenAI格式通用客户端 (httpx) |
| Token 预估 | tiktoken |
| 认证 | bcrypt + itsdangerous (Session Cookie) |
| 天气 | OpenWeatherMap / QWeather API |
| 地图 | 高德地图 / 百度地图 API |
| 前端 | Jinja2模板 + 原生JS + Chart.js |
| 并发 | ThreadPoolExecutor |

---

## 八、扩展开发

### 8.1 新增子Agent

1. 在 `app/agents/v3/` 下创建新Agent文件
2. 继承 `BaseAgentV3`，实现 `_execute_with_db()` 和 `_build_prompt()`
3. 在 `PlannerAgent.__init__()` 中注册
4. 在 `PlannerAgent._execute_sub_agents()` 中添加并行调用

### 8.2 新增API Provider

在 `weather.py` 或 `map.py` 中添加新的provider实现方法即可。

---

*V3 版本 | 基于 hello-agents 智能旅行助手架构参考*
