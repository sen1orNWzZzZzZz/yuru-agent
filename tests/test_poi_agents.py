"""POI Agent 测试."""


from app.agents.v3.poi_agent import AttractionAgent, HotelAgent, RestaurantAgent, TransportAgent


class TestHotelAgent:
    """酒店推荐 Agent 测试."""

    def test_search_hotels_by_city(self, no_llm, sample_data):
        agent = HotelAgent()
        result = agent._execute_with_db({"destination": "杭州", "travelers": 2})

        assert result["city"] == "杭州"
        assert len(result["hotels"]) > 0
        assert result["total"] > 0
        # 前 6 条应包含小红书口碑字段
        for hotel in result["hotels"]:
            assert "xiaohongshu" in hotel

    def test_budget_filter(self, no_llm, sample_data):
        agent = HotelAgent()
        result = agent._execute_with_db({"destination": "杭州", "budget": 500, "travelers": 2})

        for hotel in result["hotels"]:
            price = hotel.get("price_value") or 0
            assert price <= 500


class TestRestaurantAgent:
    """餐饮推荐 Agent 测试."""

    def test_search_restaurants_by_city(self, no_llm, sample_data):
        agent = RestaurantAgent()
        result = agent._execute_with_db({"destination": "杭州"})

        assert result["city"] == "杭州"
        assert len(result["restaurants"]) > 0
        assert "by_price" in result

    def test_default_reasoning(self, no_llm, sample_data):
        agent = RestaurantAgent()
        result = agent._execute_with_db({"destination": "杭州"})
        reasoning = agent._default_reasoning({}, result)
        assert "杭州" in reasoning


class TestAttractionAgent:
    """景点推荐 Agent 测试."""

    def test_search_attractions_by_city(self, no_llm, sample_data):
        agent = AttractionAgent()
        result = agent._execute_with_db({"destination": "杭州", "days": 3})

        assert result["city"] == "杭州"
        assert len(result["attractions"]) > 0

    def test_altitude_risk_detection(self, no_llm, sample_data):
        agent = AttractionAgent()
        result = agent._execute_with_db({"destination": "丽江", "days": 3})

        # 丽江有海拔>2000的景点，应触发高反风险
        assert len(result["altitude_risks"]) > 0


class TestTransportAgent:
    """交通规划 Agent 测试."""

    def test_distance_lookup(self, no_llm):
        agent = TransportAgent()
        result = agent._execute_with_db({"origin": "上海", "destination": "杭州", "travelers": 2})

        assert result["origin"] == "上海"
        assert result["destination"] == "杭州"
        assert result["distance_km"] == 173
        assert len(result["options"]) >= 2

    def test_long_distance_flight_option(self, no_llm):
        agent = TransportAgent()
        result = agent._execute_with_db({"origin": "北京", "destination": "西安", "travelers": 2})

        types = {opt["type"] for opt in result["options"]}
        assert "flight" in types
        assert "train" in types
        assert "drive" in types
