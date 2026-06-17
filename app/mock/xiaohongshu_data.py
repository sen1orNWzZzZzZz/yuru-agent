"""
Mock小红书数据 - 模拟小红书平台的POI口碑数据
包含反水军检测：互动异常检测、作者信誉评估、内容一致性分析
21条笔记，覆盖8条水军样本（多种水军类型）
"""
import random
import time

# 小红书笔记数据
XIAOHONGSHU_NOTES = [
    {
        "id": "xhs-001",
        "title": "杭州西湖国宾馆真实入住体验",
        "content": "住了三晚，总体来说还可以。房间能看到西湖，早餐种类丰富。",
        "author": "旅行达人小王",
        "author_avatar": "avatar1.jpg",
        "likes": 328,
        "collects": 156,
        "comments": 89,
        "publish_date": "2024-03-15",
        "credibility_score": 85,
        "is_suspicious": False,
        "suspicious_indicators": [],
    },
    {
        "id": "xhs-002",
        "title": "成都博舍 | 设计感满分的精品酒店",
        "content": "这次出差住了博舍，位置很方便就在太古里。房间设计简约有质感，但是价格偏高。",
        "author": "设计控Alice",
        "author_avatar": "avatar2.jpg",
        "likes": 892,
        "collects": 445,
        "comments": 203,
        "publish_date": "2024-01-20",
        "credibility_score": 88,
        "is_suspicious": False,
        "suspicious_indicators": [],
    },
    {
        "id": "xhs-003",
        "title": "西安W酒店 | 曲江边的潮牌之选",
        "content": "冲着W的品牌去的，设计感确实很强。但服务有点跟不上，办理入住等了半小时。",
        "author": "酒店体验官",
        "author_avatar": "avatar3.jpg",
        "likes": 567,
        "collects": 234,
        "comments": 178,
        "publish_date": "2024-02-10",
        "credibility_score": 82,
        "is_suspicious": False,
        "suspicious_indicators": [],
    },
    {
        "id": "xhs-004",
        "title": "丽江玉龙雪山高反血泪教训",
        "content": "刚从雪山下来，真的有高反！建议大家在古城适应两天再去，带好氧气瓶。",
        "author": "户外探索家",
        "author_avatar": "avatar4.jpg",
        "likes": 2341,
        "collects": 1890,
        "comments": 567,
        "publish_date": "2024-04-05",
        "credibility_score": 92,
        "is_suspicious": False,
        "suspicious_indicators": [],
    },
    {
        "id": "xhs-005",
        "title": "北京故宫一日游攻略 | 避坑指南",
        "content": "提前7天在官网预约！现场不卖票。建议从午门进，神武门出。",
        "author": "北京土著小刘",
        "author_avatar": "avatar5.jpg",
        "likes": 4520,
        "collects": 3890,
        "comments": 890,
        "publish_date": "2024-01-15",
        "credibility_score": 90,
        "is_suspicious": False,
        "suspicious_indicators": [],
    },
    {
        "id": "xhs-006",
        "title": "绝了！这辈子一定要来这家酒店！",
        "content": "真的是绝绝子！姐妹们冲！不来真的会后悔！",
        "author": "种草小甜甜",
        "author_avatar": "avatar6.jpg",
        "likes": 8900,
        "collects": 12,
        "comments": 8,
        "publish_date": "2024-05-01",
        "credibility_score": 22,
        "is_suspicious": True,
        "suspicious_indicators": ['点赞/收藏比异常(741:1)', '评论极少'],
    },
    {
        "id": "xhs-007",
        "title": "哇哇哇！太美了！",
        "content": "天啊太美了！绝了！强烈推荐！",
        "author": "哇塞女孩",
        "author_avatar": "avatar7.jpg",
        "likes": 12000,
        "collects": 5,
        "comments": 3,
        "publish_date": "2024-05-02",
        "credibility_score": 15,
        "is_suspicious": True,
        "suspicious_indicators": ['点赞/收藏比异常(2400:1)', '内容空洞', '评论极少'],
    },
    {
        "id": "xhs-008",
        "title": "杭州XX酒店，全网最低价预订攻略",
        "content": "姐妹们！我找到了超低价预订的方法！通过下面链接可以拿到内部价...",
        "author": "酒店预订小助手",
        "author_avatar": "avatar8.jpg",
        "likes": 3400,
        "collects": 2890,
        "comments": 156,
        "publish_date": "2024-05-10",
        "credibility_score": 18,
        "is_suspicious": True,
        "suspicious_indicators": ['含外部链接引流', '营销号特征', '互动质量低'],
    },
    {
        "id": "xhs-009",
        "title": "成都最火火锅店，排队3小时也要吃！",
        "content": "这家火锅真的太好吃了！毛肚超新鲜！现在通过某音下单有优惠...",
        "author": "美食探店达人",
        "author_avatar": "avatar9.jpg",
        "likes": 5600,
        "collects": 3400,
        "comments": 89,
        "publish_date": "2024-05-12",
        "credibility_score": 25,
        "is_suspicious": True,
        "suspicious_indicators": ['含外部链接引流', '过度营销用语', '互动质量低'],
    },
    {
        "id": "xhs-010",
        "title": "西安必住酒店TOP1",
        "content": "这家酒店真的很好，房间大，服务也好，早餐丰富，推荐给大家！",
        "author": "旅行推荐师",
        "author_avatar": "avatar10.jpg",
        "likes": 890,
        "collects": 445,
        "comments": 23,
        "publish_date": "2024-05-15",
        "credibility_score": 35,
        "is_suspicious": True,
        "suspicious_indicators": ['内容模板化', '与其他笔记高度相似'],
    },
    {
        "id": "xhs-011",
        "title": "北京必住酒店TOP1",
        "content": "这家酒店真的很好，房间大，服务也好，早餐丰富，推荐给大家！",
        "author": "旅行推荐师",
        "author_avatar": "avatar10.jpg",
        "likes": 920,
        "collects": 456,
        "comments": 19,
        "publish_date": "2024-05-16",
        "credibility_score": 32,
        "is_suspicious": True,
        "suspicious_indicators": ['内容模板化', '与其他笔记高度相似', '批量发布特征'],
    },
    {
        "id": "xhs-012",
        "title": "厦门鼓浪屿超详细攻略！",
        "content": "（空白内容，只有标题和标签）#鼓浪屿 #厦门",
        "author": "新用户12345",
        "author_avatar": "avatar11.jpg",
        "likes": 5600,
        "collects": 2300,
        "comments": 45,
        "publish_date": "2024-05-20",
        "credibility_score": 20,
        "is_suspicious": True,
        "suspicious_indicators": ['内容为空', '新账号高互动', '疑似刷量'],
    },
    {
        "id": "xhs-013",
        "title": "杭州西湖国宾馆 | 不太值这个价",
        "content": "1680一晚但体验一般，设施有点老旧，浴室排水慢。",
        "author": "理性消费者",
        "author_avatar": "avatar12.jpg",
        "likes": 234,
        "collects": 56,
        "comments": 178,
        "publish_date": "2024-03-20",
        "credibility_score": 78,
        "is_suspicious": False,
        "suspicious_indicators": [],
    },
    {
        "id": "xhs-014",
        "title": "避雷！这家网红餐厅千万别去",
        "content": "排队2小时，味道很一般，性价比低，完全是营销出来的。",
        "author": "真实测评",
        "author_avatar": "avatar13.jpg",
        "likes": 1890,
        "collects": 890,
        "comments": 567,
        "publish_date": "2024-04-15",
        "credibility_score": 80,
        "is_suspicious": False,
        "suspicious_indicators": [],
    },
    {
        "id": "xhs-015",
        "title": "大理洱海民宿，住到不想走",
        "content": "这家民宿太美了！每天醒来就能看到洱海！老板人也超好！（文末附民宿名称和预订方式）",
        "author": "民宿体验家",
        "author_avatar": "avatar14.jpg",
        "likes": 3400,
        "collects": 2340,
        "comments": 234,
        "publish_date": "2024-05-08",
        "credibility_score": 55,
        "is_suspicious": False,
        "suspicious_indicators": ['疑似软广'],
    },
    {
        "id": "xhs-016",
        "title": "桂林漓江竹筏漂流攻略",
        "content": "漓江竹筏真的太美了！20元人民币背景就是这里！建议早上去，人少光线好。",
        "author": "摄影师阿明",
        "author_avatar": "avatar15.jpg",
        "likes": 5670,
        "collects": 4560,
        "comments": 890,
        "publish_date": "2024-04-20",
        "credibility_score": 86,
        "is_suspicious": False,
        "suspicious_indicators": [],
    },
    {
        "id": "xhs-017",
        "title": "重庆洪崖洞最佳拍照机位",
        "content": "整理了5个最佳拍照位置，避开人群，拍出大片感。",
        "author": "重庆本地人",
        "author_avatar": "avatar16.jpg",
        "likes": 12300,
        "collects": 9870,
        "comments": 1230,
        "publish_date": "2024-03-01",
        "credibility_score": 88,
        "is_suspicious": False,
        "suspicious_indicators": [],
    },
    {
        "id": "xhs-018",
        "title": "苏州拙政园 | 园林之美",
        "content": "拙政园真的值得去，虽然人有点多，但园林设计真的精妙。建议请个导游讲解。",
        "author": "文化旅行者",
        "author_avatar": "avatar17.jpg",
        "likes": 2340,
        "collects": 1890,
        "comments": 345,
        "publish_date": "2024-02-15",
        "credibility_score": 84,
        "is_suspicious": False,
        "suspicious_indicators": [],
    },
    {
        "id": "xhs-019",
        "title": "!!!超级推荐!!!",
        "content": "!!!!!!!!",
        "author": "用户778899",
        "author_avatar": "avatar18.jpg",
        "likes": 4500,
        "collects": 12,
        "comments": 2,
        "publish_date": "2024-05-25",
        "credibility_score": 8,
        "is_suspicious": True,
        "suspicious_indicators": ['内容无意义', '账号行为异常', '疑似僵尸粉', '刷量特征明显'],
    },
    {
        "id": "xhs-020",
        "title": "武汉热干面哪家强？实测5家老字号",
        "content": "去了蔡林记、老通城、四季美、户部巷、吉庆街，给大家对比一下口感和价格。",
        "author": "吃货小分队",
        "author_avatar": "avatar19.jpg",
        "likes": 8900,
        "collects": 6780,
        "comments": 2340,
        "publish_date": "2024-03-10",
        "credibility_score": 90,
        "is_suspicious": False,
        "suspicious_indicators": [],
    },
    {
        "id": "xhs-021",
        "title": "南京中山陵游览攻略",
        "content": "392级台阶，象征三亿九千二百万民众。建议穿舒适的鞋子。",
        "author": "历史迷小张",
        "author_avatar": "avatar20.jpg",
        "likes": 3450,
        "collects": 2340,
        "comments": 567,
        "publish_date": "2024-04-01",
        "credibility_score": 87,
        "is_suspicious": False,
        "suspicious_indicators": [],
    },
]


# 作者信誉档案
AUTHOR_REPUTATION_DB = {
    "旅行达人小王": {"reputation_score": 85, "risk_level": "low", "content_consistency": 88, "engagement_quality": 82, "commercial_suspicion": 15, "suspicious_behaviors": []},
    "设计控Alice": {"reputation_score": 88, "risk_level": "low", "content_consistency": 90, "engagement_quality": 85, "commercial_suspicion": 20, "suspicious_behaviors": []},
    "酒店体验官": {"reputation_score": 82, "risk_level": "low", "content_consistency": 85, "engagement_quality": 78, "commercial_suspicion": 25, "suspicious_behaviors": []},
    "户外探索家": {"reputation_score": 92, "risk_level": "low", "content_consistency": 90, "engagement_quality": 88, "commercial_suspicion": 10, "suspicious_behaviors": []},
    "北京土著小刘": {"reputation_score": 90, "risk_level": "low", "content_consistency": 92, "engagement_quality": 86, "commercial_suspicion": 12, "suspicious_behaviors": []},
    # 水军作者
    "种草小甜甜": {"reputation_score": 18, "risk_level": "high", "content_consistency": 15, "engagement_quality": 10, "commercial_suspicion": 95, "suspicious_behaviors": ["互动异常", "刷量嫌疑"]},
    "哇塞女孩": {"reputation_score": 12, "risk_level": "high", "content_consistency": 10, "engagement_quality": 8, "commercial_suspicion": 98, "suspicious_behaviors": ["内容空洞", "刷量嫌疑"]},
    "酒店预订小助手": {"reputation_score": 15, "risk_level": "high", "content_consistency": 20, "engagement_quality": 12, "commercial_suspicion": 92, "suspicious_behaviors": ["营销号", "引流行为"]},
    "美食探店达人": {"reputation_score": 22, "risk_level": "high", "content_consistency": 25, "engagement_quality": 18, "commercial_suspicion": 88, "suspicious_behaviors": ["营销号", "引流行为"]},
    "旅行推荐师": {"reputation_score": 28, "risk_level": "high", "content_consistency": 22, "engagement_quality": 25, "commercial_suspicion": 75, "suspicious_behaviors": ["模板化内容", "批量发布"]},
    "新用户12345": {"reputation_score": 10, "risk_level": "high", "content_consistency": 8, "engagement_quality": 5, "commercial_suspicion": 90, "suspicious_behaviors": ["新账号高互动", "疑似僵尸粉"]},
    "用户778899": {"reputation_score": 5, "risk_level": "high", "content_consistency": 5, "engagement_quality": 3, "commercial_suspicion": 95, "suspicious_behaviors": ["僵尸粉", "无意义内容"]},
    "理性消费者": {"reputation_score": 78, "risk_level": "low", "content_consistency": 82, "engagement_quality": 75, "commercial_suspicion": 20, "suspicious_behaviors": []},
    "真实测评": {"reputation_score": 80, "risk_level": "low", "content_consistency": 85, "engagement_quality": 78, "commercial_suspicion": 18, "suspicious_behaviors": []},
    "民宿体验家": {"reputation_score": 55, "risk_level": "medium", "content_consistency": 60, "engagement_quality": 58, "commercial_suspicion": 55, "suspicious_behaviors": ["疑似软广"]},
    "摄影师阿明": {"reputation_score": 86, "risk_level": "low", "content_consistency": 88, "engagement_quality": 84, "commercial_suspicion": 15, "suspicious_behaviors": []},
    "重庆本地人": {"reputation_score": 88, "risk_level": "low", "content_consistency": 90, "engagement_quality": 85, "commercial_suspicion": 12, "suspicious_behaviors": []},
    "文化旅行者": {"reputation_score": 84, "risk_level": "low", "content_consistency": 86, "engagement_quality": 82, "commercial_suspicion": 18, "suspicious_behaviors": []},
    "吃货小分队": {"reputation_score": 90, "risk_level": "low", "content_consistency": 92, "engagement_quality": 86, "commercial_suspicion": 10, "suspicious_behaviors": []},
    "历史迷小张": {"reputation_score": 87, "risk_level": "low", "content_consistency": 88, "engagement_quality": 84, "commercial_suspicion": 12, "suspicious_behaviors": []},
}


def get_poi_xiaohongshu_data(poi_name: str, poi_type: str = "") -> dict:
    """
    获取POI的小红书口碑数据（含反水军过滤）

    反水军检测流程：
    1. 获取该POI相关的所有笔记
    2. 逐条进行水军检测
    3. 过滤水军笔记后重新计算评分
    4. 返回可信度评分
    """
    time.sleep(random.uniform(0.3, 0.8))

    # 模拟查询相关笔记
    related_notes = [n for n in XIAOHONGSHU_NOTES if poi_name[:2] in n["title"] or poi_name[:2] in n["content"]]
    if not related_notes:
        # 没有直接相关笔记，生成模拟数据
        related_notes = random.sample(XIAOHONGSHU_NOTES, k=min(3, len(XIAOHONGSHU_NOTES)))

    # 反水军过滤
    suspicious_count = sum(1 for n in related_notes if n["is_suspicious"])
    normal_notes = [n for n in related_notes if not n["is_suspicious"]]

    # 计算可信度（基于正常笔记的平均分，降低水军影响）
    if normal_notes:
        avg_credibility = sum(n["credibility_score"] for n in normal_notes) / len(normal_notes)
    else:
        avg_credibility = 50  # 默认值

    # 水军惩罚因子
    spam_penalty = min(suspicious_count * 5, 30)
    final_score = max(avg_credibility - spam_penalty, 10)

    return {
        "poi_name": poi_name,
        "poi_type": poi_type,
        "total_mentions": len(related_notes),
        "normal_notes": len(normal_notes),
        "suspicious_notes": suspicious_count,
        "average_sentiment": round(random.uniform(3.5, 4.8), 1),
        "credibility_score": round(final_score, 1),
        "top_notes": [n["id"] for n in normal_notes[:3]],
    }


def get_author_reputation(author_id: str) -> dict:
    """获取作者信誉档案"""
    time.sleep(random.uniform(0.1, 0.3))
    rep = AUTHOR_REPUTATION_DB.get(author_id, {
        "reputation_score": 50,
        "risk_level": "unknown",
        "content_consistency": 50,
        "engagement_quality": 50,
        "commercial_suspicion": 50,
        "suspicious_behaviors": [],
    })
    rep["author_id"] = author_id
    rep["nickname"] = author_id
    return rep
