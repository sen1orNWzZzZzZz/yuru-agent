"""
数据库管理模块 - SQLite连接和初始化
"""
import os
import sqlite3
from pathlib import Path

# 数据库文件路径：可通过环境变量覆盖，便于 Docker 持久化
DB_DIR = Path(__file__).parent
DEFAULT_DB_PATH = DB_DIR / "travel_v3.db"
DB_PATH = Path(os.environ.get("SQLITE_DB_PATH", DEFAULT_DB_PATH))
INIT_SQL = DB_DIR / "init.sql"
SEED_SQL = DB_DIR / "seed.sql"


def get_db_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(force=False):
    """
    初始化数据库
    Args:
        force: 如果为True，强制重新初始化（删除旧数据库）
    """
    if force and DB_PATH.exists():
        DB_PATH.unlink()
        print(f"[DB] 已删除旧数据库: {DB_PATH}")

    if not DB_PATH.exists():
        print("[DB] 正在初始化数据库...")
        conn = get_db_connection()
        cursor = conn.cursor()

        # 执行建表脚本
        if INIT_SQL.exists():
            with open(INIT_SQL, encoding="utf-8") as f:
                cursor.executescript(f.read())
            print("[DB] 表结构创建完成")

        # 执行种子数据
        if SEED_SQL.exists():
            with open(SEED_SQL, encoding="utf-8") as f:
                cursor.executescript(f.read())
            print("[DB] 种子数据导入完成")

        conn.commit()
        conn.close()
        print(f"[DB] 数据库初始化完成: {DB_PATH}")
    else:
        print(f"[DB] 数据库已存在: {DB_PATH}")
        # 对已存在的数据库执行迁移，补齐后续新增的表和字段
        migrate_db()


def migrate_db():
    """
    数据库迁移：为已存在的数据库补齐后续版本新增的表和字段。

    当前处理：
    - 创建 llm_cache 表（如缺失）
    - 为 agent_logs 增加 prompt_tokens / completion_tokens 字段（如缺失）
    - 创建 request_logs 表（如缺失）
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cache_key VARCHAR(64) NOT NULL UNIQUE,
                model VARCHAR(100) NOT NULL,
                messages_json TEXT NOT NULL,
                response_content TEXT NOT NULL,
                usage_json TEXT,
                latency_ms INTEGER,
                hit_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_hit_at TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_llm_cache_key ON llm_cache(cache_key)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_llm_cache_expires ON llm_cache(expires_at)")

        columns = {row["name"] for row in cursor.execute("PRAGMA table_info(agent_logs)").fetchall()}
        if "prompt_tokens" not in columns:
            cursor.execute("ALTER TABLE agent_logs ADD COLUMN prompt_tokens INTEGER DEFAULT 0")
        if "completion_tokens" not in columns:
            cursor.execute("ALTER TABLE agent_logs ADD COLUMN completion_tokens INTEGER DEFAULT 0")
        if "estimated_prompt_tokens" not in columns:
            cursor.execute("ALTER TABLE agent_logs ADD COLUMN estimated_prompt_tokens INTEGER DEFAULT 0")
        if "estimated_completion_tokens" not in columns:
            cursor.execute("ALTER TABLE agent_logs ADD COLUMN estimated_completion_tokens INTEGER DEFAULT 0")

        user_columns = {row["name"] for row in cursor.execute("PRAGMA table_info(users)").fetchall()}
        if "password_hash" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)")

        itinerary_columns = {row["name"] for row in cursor.execute("PRAGMA table_info(itineraries)").fetchall()}
        if "itinerary_json" not in itinerary_columns:
            cursor.execute("ALTER TABLE itineraries ADD COLUMN itinerary_json TEXT")
        if "planning_trace" not in itinerary_columns:
            cursor.execute("ALTER TABLE itineraries ADD COLUMN planning_trace TEXT")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                display_name VARCHAR(100),
                age_group VARCHAR(20),
                companion_type VARCHAR(50),
                interests TEXT,
                pace VARCHAR(20),
                budget_range INTEGER,
                dietary_restrictions TEXT,
                accessibility_needs TEXT,
                preferred_transport VARCHAR(50),
                home_city VARCHAR(100),
                must_visit_tags TEXT,
                avoid_tags TEXT,
                llm_summary TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_profiles_user ON user_profiles(user_id)")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS external_poi_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider VARCHAR(50) NOT NULL,
                city VARCHAR(100) NOT NULL,
                keywords VARCHAR(200) NOT NULL,
                poi_type VARCHAR(50),
                results_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_external_poi_cache_lookup ON external_poi_cache(provider, city, keywords, poi_type)")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS request_logs (
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
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_created ON request_logs(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_path ON request_logs(path)")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS planning_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id),
                itinerary_id INTEGER REFERENCES itineraries(id),
                parent_run_id INTEGER REFERENCES planning_runs(id),
                status VARCHAR(20) DEFAULT 'pending',
                input_params TEXT,
                idempotency_key VARCHAR(64),
                current_step INTEGER DEFAULT 0,
                total_steps INTEGER DEFAULT 0,
                error_message TEXT,
                claimed_at TIMESTAMP,
                claimed_by VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS planning_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES planning_runs(id) ON DELETE CASCADE,
                step_number INTEGER NOT NULL,
                step_type VARCHAR(50) NOT NULL,
                tool_name VARCHAR(50),
                tool_input TEXT,
                content TEXT,
                observation_json TEXT,
                cached_result_json TEXT,
                status VARCHAR(20) DEFAULT 'completed',
                duration_ms INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # 为已存在的 planning_runs 补齐后续新增字段
        run_columns = {row["name"] for row in cursor.execute("PRAGMA table_info(planning_runs)").fetchall()}
        if "parent_run_id" not in run_columns:
            cursor.execute("ALTER TABLE planning_runs ADD COLUMN parent_run_id INTEGER REFERENCES planning_runs(id)")
        if "idempotency_key" not in run_columns:
            cursor.execute("ALTER TABLE planning_runs ADD COLUMN idempotency_key VARCHAR(64)")
        if "claimed_at" not in run_columns:
            cursor.execute("ALTER TABLE planning_runs ADD COLUMN claimed_at TIMESTAMP")
        if "claimed_by" not in run_columns:
            cursor.execute("ALTER TABLE planning_runs ADD COLUMN claimed_by VARCHAR(100)")

        # 为已存在的 planning_steps 补齐后续新增字段
        step_columns = {row["name"] for row in cursor.execute("PRAGMA table_info(planning_steps)").fetchall()}
        if "cached_result_json" not in step_columns:
            cursor.execute("ALTER TABLE planning_steps ADD COLUMN cached_result_json TEXT")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_planning_runs_user ON planning_runs(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_planning_runs_status ON planning_runs(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_planning_runs_idempotency ON planning_runs(user_id, idempotency_key)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_planning_runs_claimed ON planning_runs(claimed_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_planning_steps_run ON planning_steps(run_id)")
        print("[DB] 数据库迁移完成")
        conn.commit()
    finally:
        conn.close()


def get_db():
    """
    FastAPI依赖注入用的数据库连接生成器
    Usage:
        @app.get("/api/items")
        def get_items(db = Depends(get_db)):
            ...
    """
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()


def query_one(conn, sql, params=()):
    """查询单条记录"""
    cursor = conn.execute(sql, params)
    row = cursor.fetchone()
    cursor.close()
    return dict(row) if row else None


def query_all(conn, sql, params=()):
    """查询多条记录"""
    cursor = conn.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()
    return [dict(row) for row in rows]


def execute(conn, sql, params=()):
    """执行SQL"""
    cursor = conn.execute(sql, params)
    conn.commit()
    last_id = cursor.lastrowid
    cursor.close()
    return last_id


def count_table(conn, table_name):
    """统计表数据量"""
    result = query_one(conn, f"SELECT COUNT(*) as cnt FROM {table_name}")
    return result["cnt"] if result else 0
