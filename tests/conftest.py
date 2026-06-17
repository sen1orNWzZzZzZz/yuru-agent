"""测试基础设施和共享 fixtures."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def memory_db(monkeypatch, tmp_path):
    """每个测试使用独立的临时 SQLite 数据库，仅加载表结构."""
    db_file = tmp_path / "test.db"
    # 必须在导入 app.main 之前 patch，确保 FastAPI startup 使用临时库
    monkeypatch.setattr("app.db.database.DB_PATH", Path(db_file))

    from app.db import database as db_module

    # 仅执行 init.sql 建表，不加载 seed.sql
    conn = db_module.get_db_connection()
    try:
        init_sql = db_module.INIT_SQL.read_text(encoding="utf-8")
        cursor = conn.cursor()
        cursor.executescript(init_sql)
        conn.commit()
    finally:
        conn.close()
    yield


@pytest.fixture
def client(memory_db):
    """FastAPI TestClient."""
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def no_llm(monkeypatch):
    """禁用 LLM，确保测试走 Mock/模板路径."""
    monkeypatch.setattr("app.integrations.llm_client.LLMClient.is_available", lambda self: False)
    yield


@pytest.fixture
def sample_data(db_conn):
    """插入测试用 POI 和小红书数据."""
    # 杭州酒店
    db_conn.executemany(
        """INSERT INTO poi_data
        (poi_id, name, poi_type, city, district, address, latitude, longitude,
         rating, review_count, price_level, price_value, tags, description, extras)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                "hz-h1", "杭州西湖国宾馆", "hotel", "杭州", "西湖区", "杭州市西湖区杨公堤18号",
                30.2456, 120.1389, 4.9, 3420, "luxury", 1680,
                '["湖景", "奢华"]', "西湖边的奢华酒店", '{"amenities": ["泳池", "SPA"]}',
            ),
            (
                "hz-h2", "杭州柏悦酒店", "hotel", "杭州", "上城区", "杭州市上城区钱江路1366号",
                30.2567, 120.1689, 4.7, 2890, "high", 1380,
                '["城市景观", "商务"]', "钱江新城高空酒店", '{"amenities": ["室内泳池"]}',
            ),
            (
                "hz-h3", "杭州全季酒店", "hotel", "杭州", "西湖区", "杭州市西湖区文三路",
                30.2789, 120.0789, 4.5, 4560, "medium", 400,
                '["性价比"]', "经济型连锁酒店", '{"amenities": ["早餐"]}',
            ),
        ],
    )

    # 杭州餐厅
    db_conn.executemany(
        """INSERT INTO poi_data
        (poi_id, name, poi_type, city, district, address, latitude, longitude,
         rating, review_count, price_level, price_value, tags, description, extras)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                "hz-r1", "楼外楼", "restaurant", "杭州", "西湖区", "杭州市西湖区孤山路30号",
                30.2589, 120.1489, 4.5, 8900, "medium", 150,
                '["老字号", "杭帮菜"]', "百年老店", '{"signature_dishes": ["西湖醋鱼"]}',
            ),
            (
                "hz-r2", "知味观", "restaurant", "杭州", "上城区", "杭州市上城区延安路249号",
                30.2550, 120.1650, 4.6, 12000, "low", 60,
                '["老字号", "小吃"]', "杭州小吃集合", '{"signature_dishes": ["小笼包"]}',
            ),
        ],
    )

    # 杭州景点
    db_conn.executemany(
        """INSERT INTO poi_data
        (poi_id, name, poi_type, city, district, address, latitude, longitude,
         rating, review_count, price_level, price_value, tags, description,
         altitude, visit_duration)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                "hz-a1", "西湖风景区", "attraction", "杭州", "西湖区", "杭州市西湖区龙井路1号",
                30.2489, 120.1489, 4.9, 45000, "free", 0,
                '["世界遗产", "必游"]', "中国十大风景名胜之一", None, 300,
            ),
            (
                "hz-a2", "灵隐寺", "attraction", "杭州", "西湖区", "杭州市西湖区灵隐路法云弄1号",
                30.2389, 120.0989, 4.7, 23000, "medium", 75,
                '["千年古刹"]', "著名佛教寺庙", None, 180,
            ),
        ],
    )

    # 丽江高海拔景点
    db_conn.execute(
        """INSERT INTO poi_data
        (poi_id, name, poi_type, city, district, address, latitude, longitude,
         rating, review_count, price_level, price_value, tags, description,
         altitude, visit_duration)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "lj-a1", "玉龙雪山", "attraction", "丽江", "玉龙县", "丽江市玉龙纳西族自治县",
            27.1000, 100.1800, 4.8, 234000, "high", 100,
            '["雪山", "冰川"]', "纳西族神山", 4500, 480,
        ),
    )

    # 小红书笔记
    db_conn.executemany(
        """INSERT INTO xiaohongshu_notes
        (note_id, title, content, author, likes, collects, comments, publish_date,
         credibility_score, is_suspicious, suspicious_indicators, poi_name, poi_type, city)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                "xhs-001", "西湖国宾馆真实入住体验", "房间很大，服务很好", "旅行达人",
                328, 120, 45, "2024-01-15", 82, 0, '[]', "杭州西湖国宾馆", "hotel", "杭州",
            ),
            (
                "xhs-002", "楼外楼避雷", "排队太久，味道一般", "美食博主",
                892, 445, 203, "2024-02-10", 78, 0, '[]', "楼外楼", "restaurant", "杭州",
            ),
            (
                "xhs-003", "!!!强烈推荐!!!", "!!!!!!!!", "用户778899",
                4500, 12, 2, "2024-05-25", 8, 1, '["营销用语", "僵尸粉"]', None, None, "成都",
            ),
        ],
    )

    db_conn.commit()
    yield


@pytest.fixture
def db_conn(memory_db):
    """返回当前测试数据库连接."""
    from app.db.database import get_db_connection

    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()
