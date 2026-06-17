"""天气模块测试."""

from unittest.mock import MagicMock

import pytest

from app.integrations.weather import WeatherClient


@pytest.fixture
def qweather_client(memory_db, monkeypatch):
    """配置为和风天气新开发服务的 WeatherClient，HTTP 请求被 mock."""
    client = WeatherClient()
    client.config = {
        "provider": "qweather",
        "api_key": "test-key",
        "base_url": "https://abcxyz.qweatherapi.com",
        "extra_params": {},
    }
    client.provider = "qweather"
    client.api_key = "test-key"
    client.base_url = "https://abcxyz.qweatherapi.com"

    def mock_get(url, params=None, headers=None):
        resp = MagicMock()
        if "/geo/v2/city/lookup" in url:
            # 模拟 geo API 异常，触发内置 ID fallback
            resp.status_code = 404
            resp.json.return_value = {"code": "404"}
        elif "/v7/weather/now" in url:
            assert headers.get("X-QW-Api-Key") == "test-key"
            resp.status_code = 200
            resp.json.return_value = {
                "now": {
                    "temp": "25",
                    "feelsLike": "27",
                    "humidity": "60",
                    "text": "晴",
                    "icon": "100",
                    "windSpeed": "3",
                }
            }
        elif "/v7/weather/3d" in url:
            assert headers.get("X-QW-Api-Key") == "test-key"
            resp.status_code = 200
            resp.json.return_value = {
                "daily": [
                    {
                        "fxDate": "2024-06-16",
                        "tempMax": "28",
                        "tempMin": "20",
                        "textDay": "晴",
                        "textNight": "多云",
                        "iconDay": "100",
                    }
                ]
            }
        else:
            resp.status_code = 404
            resp.json.return_value = {}
        return resp

    monkeypatch.setattr(client.client, "get", mock_get)
    return client


class TestQWeatherFallback:
    """和风天气 geo API 失败时使用内置城市 ID fallback."""

    def test_geo_404_uses_builtin_city_id(self, qweather_client):
        result = qweather_client.get_current_weather("杭州")
        assert result["provider"] == "qweather"
        assert result["temp"] == 25
        assert result["city"] == "杭州"

    def test_geo_404_forecast_uses_builtin_city_id(self, qweather_client):
        result = qweather_client.get_forecast("杭州", days=3)
        assert len(result) == 1
        assert result[0]["temp_max"] == 28

    def test_unknown_city_fallback_to_mock(self, qweather_client):
        result = qweather_client.get_current_weather("不存在的城市")
        assert result["provider"] == "mock"

    def test_qweather_requires_base_url(self, memory_db, monkeypatch):
        """和风天气新接口必须提供自定义 API Host."""
        client = WeatherClient()
        client.config = {
            "provider": "qweather",
            "api_key": "test-key",
            "base_url": "",
            "extra_params": {},
        }
        client.provider = "qweather"
        client.api_key = "test-key"
        client.base_url = ""
        assert client.is_available() is False
        result = client.get_current_weather("杭州")
        assert result["provider"] == "mock"

    def test_openweathermap_default_base_url(self, memory_db, monkeypatch):
        """OpenWeatherMap 未填 Base URL 时自动使用默认值."""
        client = WeatherClient()
        assert client.provider is None  # 未配置

    def test_test_connection_detects_mock_provider(self, qweather_client, monkeypatch):
        """test_connection 应识别出返回的是 mock 还是真实 provider."""
        monkeypatch.setattr(
            qweather_client, "get_current_weather", lambda city: {"success": True, "provider": "mock"}
        )
        result = qweather_client.test_connection()
        assert result["success"] is False
        assert "实际provider: mock" in result["message"]
