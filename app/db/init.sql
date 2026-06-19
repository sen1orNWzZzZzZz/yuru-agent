-- ============================================================
-- 智能旅游多Agent规划系统 V3 - 数据库初始化脚本
-- SQLite 兼容
-- 支持: LLM配置 / 天气&地图API配置 / POI数据 / 小红书评论 / 行程数据
-- ============================================================

-- 删除旧表（如果存在）
DROP TABLE IF EXISTS itinerary_items;
DROP TABLE IF EXISTS itinerary_days;
DROP TABLE IF EXISTS itineraries;
DROP TABLE IF EXISTS agent_logs;
DROP TABLE IF EXISTS llm_cache;
DROP TABLE IF EXISTS xiaohongshu_notes;
DROP TABLE IF EXISTS poi_data;
DROP TABLE IF EXISTS api_configs;
DROP TABLE IF EXISTS llm_configs;
DROP TABLE IF EXISTS users;

-- ============================================================
-- 1. 用户表
-- ============================================================
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(50) NOT NULL UNIQUE,
    email VARCHAR(100),
    password_hash VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 1.1 用户画像表
-- ============================================================
CREATE TABLE user_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    display_name VARCHAR(100),
    age_group VARCHAR(20),                  -- 青少年/青年/中年/老年
    companion_type VARCHAR(50),             -- 单人/情侣/家庭/朋友/商务
    interests TEXT,                         -- JSON 数组
    pace VARCHAR(20),                       -- slow/relaxed/balanced/intensive
    budget_range INTEGER,                   -- 单次出行人均预算参考
    dietary_restrictions TEXT,              -- JSON 数组
    accessibility_needs TEXT,
    preferred_transport VARCHAR(50),
    home_city VARCHAR(100),
    must_visit_tags TEXT,                   -- JSON 数组
    avoid_tags TEXT,                        -- JSON 数组
    llm_summary TEXT,                       -- LLM 自动总结的偏好描述
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 2. LLM配置表 (支持多配置切换)
-- ============================================================
CREATE TABLE llm_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(100) NOT NULL DEFAULT 'default',
    api_key VARCHAR(500) NOT NULL,
    base_url VARCHAR(500) DEFAULT 'https://api.openai.com/v1',
    model_name VARCHAR(100) DEFAULT 'gpt-4o-mini',
    temperature REAL DEFAULT 0.7 CHECK (temperature >= 0 AND temperature <= 2),
    max_tokens INTEGER DEFAULT 4096,
    timeout INTEGER DEFAULT 60,
    is_active BOOLEAN DEFAULT 1,
    use_llm BOOLEAN DEFAULT 1,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 3. 外部API配置表 (天气/地图等)
-- ============================================================
CREATE TABLE api_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_type VARCHAR(50) NOT NULL,        -- 'weather', 'map', 'transport'
    provider VARCHAR(50) NOT NULL,           -- 'openweathermap', 'amap', 'baidu'
    api_key VARCHAR(500) NOT NULL,
    base_url VARCHAR(500),
    extra_params TEXT,                       -- JSON格式额外参数
    is_active BOOLEAN DEFAULT 1,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 4. POI数据表 (酒店/餐厅/景点 - 通过数据库Mock)
-- ============================================================
CREATE TABLE poi_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poi_id VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(200) NOT NULL,
    name_en VARCHAR(200),
    poi_type VARCHAR(50) NOT NULL,           -- 'hotel', 'restaurant', 'attraction'
    city VARCHAR(50) NOT NULL,
    district VARCHAR(100),
    address VARCHAR(300),
    latitude REAL,
    longitude REAL,
    rating REAL DEFAULT 4.0,
    review_count INTEGER DEFAULT 0,
    price_level VARCHAR(20),                 -- 'low', 'medium', 'high', 'luxury'
    price_value INTEGER,                     -- 具体价格数值
    tags TEXT,                               -- JSON数组
    description TEXT,
    extras TEXT,                             -- JSON格式额外信息(amenities/signature_dishes等)
    open_hours VARCHAR(100),
    needs_booking BOOLEAN DEFAULT 0,
    altitude INTEGER,                        -- 景点海拔(用于高反评估)
    visit_duration INTEGER,                  -- 建议游览时长(分钟)
    xiaohongshu_mentions INTEGER DEFAULT 0,
    xiaohongshu_score INTEGER DEFAULT 70,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 5. 小红书笔记表 (Mock评论数据)
-- ============================================================
CREATE TABLE xiaohongshu_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id VARCHAR(50) NOT NULL UNIQUE,
    title VARCHAR(300) NOT NULL,
    content TEXT,
    author VARCHAR(100) NOT NULL,
    author_avatar VARCHAR(200),
    likes INTEGER DEFAULT 0,
    collects INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    publish_date DATE,
    credibility_score REAL DEFAULT 70,       -- 可信度评分 0-100
    is_suspicious BOOLEAN DEFAULT 0,         -- 是否水军
    suspicious_indicators TEXT,              -- JSON数组
    poi_name VARCHAR(200),                   -- 关联的POI名称
    poi_type VARCHAR(50),                    -- 'hotel', 'restaurant', 'attraction'
    city VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 6. 行程表
-- ============================================================
CREATE TABLE itineraries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    title VARCHAR(200) NOT NULL,
    destination VARCHAR(100) NOT NULL,
    origin VARCHAR(100) DEFAULT '上海',
    start_date DATE,
    end_date DATE,
    traveler_count INTEGER DEFAULT 2,
    budget INTEGER,
    travel_style VARCHAR(50) DEFAULT 'balanced',  -- 'slow', 'intensive', 'family', 'foodie', 'photography'
    status VARCHAR(20) DEFAULT 'draft',           -- 'draft', 'planned', 'confirmed'
    total_cost INTEGER,
    llm_used BOOLEAN DEFAULT 0,                   -- 是否使用了LLM
    weather_data TEXT,                            -- JSON格式天气数据
    map_data TEXT,                                -- JSON格式地图数据
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 7. 行程天表
-- ============================================================
CREATE TABLE itinerary_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    itinerary_id INTEGER NOT NULL REFERENCES itineraries(id) ON DELETE CASCADE,
    day_number INTEGER NOT NULL,
    date DATE,
    weather_summary VARCHAR(200),
    temperature VARCHAR(50),
    day_theme VARCHAR(200),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 8. 行程项表
-- ============================================================
CREATE TABLE itinerary_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day_id INTEGER NOT NULL REFERENCES itinerary_days(id) ON DELETE CASCADE,
    item_type VARCHAR(50) NOT NULL,          -- 'hotel', 'restaurant', 'attraction', 'transport', 'activity'
    name VARCHAR(200) NOT NULL,
    description TEXT,
    start_time TIME,
    end_time TIME,
    duration INTEGER,
    latitude REAL,
    longitude REAL,
    address VARCHAR(300),
    estimated_cost INTEGER DEFAULT 0,
    agent_recommendation TEXT,               -- Agent推荐理由
    poi_reference VARCHAR(50),               -- 关联poi_data.poi_id
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 9. Agent执行日志表
-- ============================================================
CREATE TABLE agent_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    itinerary_id INTEGER REFERENCES itineraries(id),
    agent_type VARCHAR(50) NOT NULL,         -- 'planner', 'weather', 'map', 'hotel', 'restaurant', 'attraction', 'transport', 'risk'
    agent_name VARCHAR(100),
    status VARCHAR(20) DEFAULT 'completed',  -- 'pending', 'running', 'completed', 'failed'
    input_params TEXT,                       -- JSON输入参数
    output_result TEXT,                      -- JSON输出结果
    duration_ms INTEGER,
    prompt_tokens INTEGER DEFAULT 0,         -- LLM prompt tokens
    completion_tokens INTEGER DEFAULT 0,     -- LLM completion tokens
    estimated_prompt_tokens INTEGER DEFAULT 0,     -- tiktoken 预估 prompt tokens
    estimated_completion_tokens INTEGER DEFAULT 0, -- tiktoken 预估 completion tokens
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 10. LLM Prompt 缓存表
-- ============================================================
CREATE TABLE llm_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key VARCHAR(64) NOT NULL UNIQUE,    -- SHA256 十六进制哈希
    model VARCHAR(100) NOT NULL,
    messages_json TEXT NOT NULL,              -- 原始 messages 序列化
    response_content TEXT NOT NULL,
    usage_json TEXT,                          -- tokens 使用情况
    latency_ms INTEGER,                       -- 首次请求延迟（命中时不更新）
    hit_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_hit_at TIMESTAMP,
    expires_at TIMESTAMP NOT NULL             -- TTL 过期时间
);

-- ============================================================
-- 10.1 外部 POI 缓存表（高德/百度等）
-- ============================================================
CREATE TABLE external_poi_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider VARCHAR(50) NOT NULL,
    city VARCHAR(100) NOT NULL,
    keywords VARCHAR(200) NOT NULL,
    poi_type VARCHAR(50),
    results_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_external_poi_cache_lookup ON external_poi_cache(provider, city, keywords, poi_type);

-- ============================================================
-- 11. API 请求日志表
-- ============================================================
CREATE TABLE request_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    method VARCHAR(10) NOT NULL,
    path TEXT NOT NULL,
    query_params TEXT,
    status_code INTEGER,
    duration_ms REAL,
    client_ip TEXT,
    user_agent TEXT,
    error_message TEXT
);

CREATE INDEX idx_request_logs_created ON request_logs(created_at);
CREATE INDEX idx_request_logs_path ON request_logs(path);

-- ============================================================
-- 索引优化
-- ============================================================
CREATE INDEX idx_poi_city ON poi_data(city);
CREATE INDEX idx_poi_type ON poi_data(poi_type);
CREATE INDEX idx_poi_city_type ON poi_data(city, poi_type);
CREATE INDEX idx_xhs_poi ON xiaohongshu_notes(poi_name);
CREATE INDEX idx_xhs_city ON xiaohongshu_notes(city);
CREATE INDEX idx_itinerary_user ON itineraries(user_id);
CREATE INDEX idx_agent_logs_itinerary ON agent_logs(itinerary_id);
CREATE INDEX idx_user_profiles_user ON user_profiles(user_id);
CREATE INDEX idx_api_configs_type ON api_configs(config_type);
CREATE INDEX idx_llm_cache_key ON llm_cache(cache_key);
CREATE INDEX idx_llm_cache_expires ON llm_cache(expires_at);
