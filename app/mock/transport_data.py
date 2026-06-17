"""
Mock交通数据 - 模拟12306/携程/飞猪等平台的交通查询
覆盖15个热门城市间的飞机/高铁/自驾方案
"""
import random
import time

# 城市代码映射
CITY_CODES = {
    "北京": "BJS", "上海": "SHA", "广州": "CAN", "深圳": "SZX",
    "杭州": "HGH", "成都": "CTU", "西安": "XIY", "重庆": "CKG",
    "武汉": "WUH", "南京": "NKG", "苏州": "SZH", "厦门": "XMN",
    "桂林": "KWL", "丽江": "LJG", "大理": "DLU",
}

# 城市间距离（公里，近似值）
CITY_DISTANCES = {
    ("北京", "上海"): 1318, ("北京", "广州"): 2123, ("北京", "深圳"): 2160,
    ("北京", "杭州"): 1270, ("北京", "成都"): 1697, ("北京", "西安"): 1084,
    ("北京", "重庆"): 1761, ("北京", "武汉"): 1152, ("北京", "南京"): 1023,
    ("北京", "苏州"): 1140, ("北京", "厦门"): 2024, ("北京", "桂林"): 1960,
    ("上海", "杭州"): 173, ("上海", "成都"): 1660, ("上海", "西安"): 1350,
    ("上海", "重庆"): 1610, ("上海", "武汉"): 825, ("上海", "南京"): 300,
    ("上海", "苏州"): 100, ("上海", "厦门"): 1000, ("上海", "桂林"): 1450,
    ("上海", "丽江"): 2800, ("广州", "深圳"): 140, ("广州", "成都"): 1570,
    ("广州", "西安"): 1630, ("广州", "重庆"): 1390, ("广州", "武汉"): 950,
    ("广州", "厦门"): 650, ("广州", "桂林"): 380, ("深圳", "成都"): 1670,
    ("深圳", "西安"): 1750, ("深圳", "重庆"): 1450, ("深圳", "厦门"): 550,
    ("杭州", "成都"): 1600, ("杭州", "西安"): 1180, ("杭州", "重庆"): 1500,
    ("杭州", "武汉"): 700, ("杭州", "桂林"): 1250, ("成都", "西安"): 712,
    ("成都", "重庆"): 300, ("成都", "丽江"): 850, ("成都", "大理"): 900,
    ("西安", "重庆"): 700, ("重庆", "武汉"): 900, ("重庆", "桂林"): 850,
    ("南京", "武汉"): 500, ("南京", "苏州"): 200, ("苏州", "杭州"): 270,
    ("厦门", "桂林"): 980, ("桂林", "丽江"): 1100, ("丽江", "大理"): 180,
    ("武汉", "桂林"): 720,
}


def _get_distance(origin, destination):
    """获取两城市间距离"""
    if origin == destination:
        return 0
    key = (origin, destination)
    reverse_key = (destination, origin)
    return CITY_DISTANCES.get(key) or CITY_DISTANCES.get(reverse_key) or 1000


def search_inter_city_transport(origin: str, destination: str, preference: str = "comfortable") -> list:
    """
    搜索城际交通方案

    Args:
        origin: 出发城市
        destination: 目的地城市
        preference: 偏好 - fastest(最快)/cheapest(最便宜)/comfortable(舒适)

    Returns:
        交通方案列表（飞机/高铁/自驾）
    """
    time.sleep(random.uniform(0.3, 0.6))

    if origin == destination:
        return []

    distance = _get_distance(origin, destination)
    results = []

    # 方案1: 飞机（适合长距离 > 800km）
    if distance > 600:
        flight_duration = int(120 + distance / 800 * 60)  # 飞行时间
        flight_price = int(400 + distance * 0.3 + random.randint(-100, 300))
        results.append({
            "id": f"flight-{CITY_CODES.get(origin, 'UNK')}-{CITY_CODES.get(destination, 'UNK')}",
            "type": "flight",
            "transport_no": f"CZ{random.randint(1000, 9999)}",
            "origin": origin,
            "destination": destination,
            "departure_time": f"{random.randint(6, 22):02d}:{random.choice(['00', '05', '10', '15', '20', '25', '30', '35', '40', '45', '50', '55'])}",
            "arrival_time": "",  # 前端计算
            "duration": flight_duration,
            "price": max(flight_price, 300),
            "operator": random.choice(["中国国航", "东方航空", "南方航空", "海南航空"]),
            "seat_class": random.choice(["经济舱", "超级经济舱"]),
            "available": True,
            "remaining_seats": random.randint(5, 50),
            "tags": ["快速", "适合长途"],
        })

    # 方案2: 高铁
    train_duration = int(distance / 250 * 60 + 30)  # 高铁平均250km/h
    train_price = int(distance * 0.45 + random.randint(-30, 80))
    results.append({
        "id": f"train-{CITY_CODES.get(origin, 'UNK')}-{CITY_CODES.get(destination, 'UNK')}",
        "type": "train",
        "transport_no": f"G{random.randint(100, 9999)}",
        "origin": origin,
        "destination": destination,
        "departure_time": f"{random.randint(6, 20):02d}:{random.choice(['00', '15', '30', '45'])}",
        "arrival_time": "",
        "duration": max(train_duration, 60),
        "price": max(train_price, 80),
        "operator": "中国铁路",
        "seat_class": random.choice(["二等座", "一等座"]),
        "available": True,
        "remaining_seats": random.randint(10, 200),
        "tags": ["准点率高", "舒适"],
    })

    # 方案3: 自驾
    drive_duration = int(distance / 100 * 60)  # 高速平均100km/h
    drive_cost = int(distance * 0.8 + random.randint(50, 150))  # 油费+过路费
    results.append({
        "id": f"drive-{CITY_CODES.get(origin, 'UNK')}-{CITY_CODES.get(destination, 'UNK')}",
        "type": "rental",
        "transport_no": "自驾",
        "origin": origin,
        "destination": destination,
        "departure_time": "灵活",
        "arrival_time": "",
        "duration": drive_duration,
        "price": drive_cost,
        "operator": "自驾出行",
        "seat_class": "私家车",
        "available": True,
        "remaining_seats": None,
        "tags": ["灵活自由", "适合多人分摊", "沿途风景"],
    })

    # 根据偏好排序
    if preference == "fastest":
        results.sort(key=lambda x: x["duration"])
    elif preference == "cheapest":
        results.sort(key=lambda x: x["price"])
    else:  # comfortable - 综合
        results.sort(key=lambda x: x["duration"] * 0.5 + x["price"] * 0.5)

    return results
