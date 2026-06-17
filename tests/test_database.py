"""数据库迁移测试."""

from app.db.database import migrate_db


class TestDatabaseMigration:
    """验证 migrate_db 能补齐旧数据库缺失的表和字段."""

    def test_migrate_creates_llm_cache(self, memory_db, db_conn):
        # 模拟旧数据库：只保留 agent_logs 老字段，删除 llm_cache
        db_conn.execute("DROP TABLE IF EXISTS llm_cache")
        db_conn.execute("DROP TABLE IF EXISTS agent_logs")
        db_conn.execute(
            """
            CREATE TABLE agent_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_type VARCHAR(50),
                agent_name VARCHAR(100),
                status VARCHAR(20),
                output_result TEXT,
                duration_ms INTEGER,
                error_message TEXT
            )
            """
        )
        db_conn.commit()

        migrate_db()

        tables = {
            row[0]
            for row in db_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "llm_cache" in tables

        columns = {
            row["name"]
            for row in db_conn.execute("PRAGMA table_info(agent_logs)").fetchall()
        }
        assert "prompt_tokens" in columns
        assert "completion_tokens" in columns

    def test_migrate_is_idempotent(self, memory_db, db_conn):
        """多次迁移不应报错."""
        migrate_db()
        migrate_db()

        tables = {
            row[0]
            for row in db_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "llm_cache" in tables
