"""
天气API集成模块
支持多provider：OpenWeatherMap / 和风天气(QWeather)
提供天气查询、 forecasts、穿着建议等功能
"""
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.integrations.config_manager import IntegrationConfig
from app.tracing import record_span

logger = logging.getLogger(__name__)

# 和风天气常见城市 ID 映射（geo API 不可用时作为 fallback）
QWEATHER_CITY_IDS = {
    "北京": "101010100",
    "上海": "101020100",
    "广州": "101280101",
    "深圳": "101280601",
    "杭州": "101210101",
    "成都": "101270101",
    "西安": "101110101",
    "重庆": "101040100",
    "武汉": "101200101",
    "南京": "101190101",
    "苏州": "101190401",
    "厦门": "101230201",
    "桂林": "101300501",
    "丽江": "101291401",
    "大理": "101290201",
}


class WeatherClient:
    """
    天气服务客户端
    支持多个天气API provider，自动降级到内置Mock数据
    """

    def __init__(self):
        self.config = IntegrationConfig.get_weather_config()
        self.provider = self.config["provider"] if self.config else None
        self.api_key = self.config["api_key"] if self.config else None
        self.base_url = self.config["base_url"] if self.config else None
        self.extra_params = self.config.get("extra_params", {}) if self.config else {}
        self.client = httpx.Client(timeout=15)

        # OpenWeatherMap 提供默认 base_url；
        # 和风天气新开发服务使用自定义 API Host，必须由用户在控制台获取后填写。
        if not self.base_url and self.provider == "openweathermap":
            self.base_url = "https://api.openweathermap.org/data/2.5"

    def is_available(self) -> bool:
        """检查天气API是否可用"""
        if self.config is None or self.api_key is None:
            return False
        # 和风天气新接口必须提供自定义 API Host
        if self.provider == "qweather" and not self.base_url:
            return False
        return True

    def get_current_weather(self, city: str) -> dict[str, Any]:
        """
        获取当前天气
        Args:
            city: 城市名称(中文)
        Returns:
            {"temp", "feels_like", "humidity", "description", "icon", "wind_speed", "success"}
        """
        start_dt = datetime.now()
        status = "ok"
        error = None
        provider = self.provider or "mock"
        try:
            if not self.is_available():
                result = self._mock_weather(city)
                provider = result.get("provider", "mock")
                return result

            if self.provider == "openweathermap":
                result = self._owm_current(city)
                provider = result.get("provider", "openweathermap")
                return result
            elif self.provider == "qweather":
                result = self._qweather_current(city)
                provider = result.get("provider", "qweather")
                return result
            else:
                result = self._mock_weather(city)
                provider = result.get("provider", "mock")
                return result
        except Exception as e:
            logger.error(f"[Weather] 获取天气失败: {e}")
            status = "error"
            error = str(e)
            return self._mock_weather(city)
        finally:
            record_span(
                name="weather.get_current_weather",
                service="weather",
                start_time=start_dt,
                end_time=datetime.now(),
                status=status,
                meta={"provider": provider, "city": city, "mock": provider == "mock"},
                error=error,
            )

    def get_forecast(self, city: str, days: int = 3) -> list:
        """
        获取天气预报
        Args:
            city: 城市名称
            days: 预报天数
        Returns:
            [{"date", "temp_max", "temp_min", "description", "icon"}, ...]
        """
        start_dt = datetime.now()
        status = "ok"
        error = None
        provider = self.provider or "mock"
        try:
            if not self.is_available():
                result = self._mock_forecast(city, days)
                provider = result[0].get("provider", "mock") if result else "mock"
                return result

            if self.provider == "openweathermap":
                result = self._owm_forecast(city, days)
                provider = "openweathermap"
                return result
            elif self.provider == "qweather":
                result = self._qweather_forecast(city, days)
                provider = "qweather"
                return result
            else:
                result = self._mock_forecast(city, days)
                provider = "mock"
                return result
        except Exception as e:
            logger.error(f"[Weather] 获取预报失败: {e}")
            status = "error"
            error = str(e)
            return self._mock_forecast(city, days)
        finally:
            record_span(
                name="weather.get_forecast",
                service="weather",
                start_time=start_dt,
                end_time=datetime.now(),
                status=status,
                meta={"provider": provider, "city": city, "days": days, "mock": provider == "mock"},
                error=error,
            )

    def get_clothing_advice(self, temp: float, weather_desc: str) -> str:
        """
        根据温度和天气给出穿着建议
        """
        if temp < 0:
            return "极寒天气，务必穿羽绒服/棉服，戴帽子手套围巾，穿保暖鞋"
        elif temp < 10:
            return "寒冷天气，建议穿大衣/厚外套，内搭毛衣，注意保暖"
        elif temp < 18:
            return "凉爽天气，建议穿薄外套/风衣，内搭长袖T恤"
        elif temp < 26:
            return "舒适天气，建议穿长袖/薄卫衣，可带一件薄外套"
        elif temp < 32:
            return "炎热天气，建议穿短袖/短裤/裙子，注意防晒"
        else:
            return "极热天气，务必做好防晒，避免正午外出，多喝水"

    def test_connection(self) -> dict[str, Any]:
        """测试天气API连接"""
        if not self.is_available():
            return {"success": False, "message": "天气API未配置"}
        try:
            result = self.get_current_weather("杭州")
            if result.get("success") and result.get("provider") == self.provider:
                return {"success": True, "message": "连接成功", "provider": self.provider, "sample": result}
            actual_provider = result.get("provider", "unknown")
            return {"success": False, "message": f"API返回异常或未命中真实接口 (实际provider: {actual_provider})", "provider": self.provider}
        except Exception as e:
            return {"success": False, "message": f"连接失败: {str(e)}", "provider": self.provider}

    # ---- OpenWeatherMap实现 ----
    def _owm_current(self, city: str) -> dict[str, Any]:
        url = f"{self.base_url}/weather"
        params = {
            "q": city,
            "appid": self.api_key,
            "units": self.extra_params.get("units", "metric"),
            "lang": self.extra_params.get("lang", "zh_cn"),
        }
        resp = self.client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        return {
            "success": True,
            "temp": round(data["main"]["temp"]),
            "feels_like": round(data["main"]["feels_like"]),
            "humidity": data["main"]["humidity"],
            "description": data["weather"][0]["description"],
            "icon": data["weather"][0]["icon"],
            "wind_speed": data["wind"]["speed"],
            "city": city,
            "provider": "openweathermap",
        }

    def _owm_forecast(self, city: str, days: int) -> list:
        url = f"{self.base_url}/forecast"
        params = {
            "q": city,
            "appid": self.api_key,
            "units": self.extra_params.get("units", "metric"),
            "lang": self.extra_params.get("lang", "zh_cn"),
            "cnt": days * 8,  # OWM每3小时一条数据
        }
        resp = self.client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        # 简化为日预报（取每天中午的数据）
        daily = []
        seen_dates = set()
        for item in data.get("list", []):
            date = item["dt_txt"][:10]
            if date not in seen_dates and "12:00" in item["dt_txt"]:
                seen_dates.add(date)
                daily.append({
                    "date": date,
                    "temp_max": round(item["main"]["temp_max"]),
                    "temp_min": round(item["main"]["temp_min"]),
                    "description": item["weather"][0]["description"],
                    "icon": item["weather"][0]["icon"],
                })
            if len(daily) >= days:
                break
        return daily

    # ---- 和风天气实现 ----
    def _qweather_headers(self) -> dict[str, str]:
        """和风天气新开发服务使用 Header 认证"""
        return {"X-QW-Api-Key": self.api_key}

    def _get_qweather_city_id(self, city: str) -> str | None:
        """
        获取和风天气城市 ID。

        优先调用 geo API（新开发服务格式：{base_url}/geo/v2/city/lookup）；
        若 geo API 返回异常，回退到内置的常见城市 ID 映射。
        """
        try:
            geo_url = f"{self.base_url.rstrip('/')}/geo/v2/city/lookup"
            geo_resp = self.client.get(
                geo_url,
                params={"location": city},
                headers=self._qweather_headers(),
            )
            if geo_resp.status_code == 200:
                geo_data = geo_resp.json()
                if geo_data.get("location"):
                    return geo_data["location"][0]["id"]
            else:
                logger.warning(
                    f"[Weather] 和风天气 geo API 状态异常: {geo_resp.status_code}, "
                    f"尝试使用内置城市 ID 映射"
                )
        except Exception as e:
            logger.warning(f"[Weather] 和风天气 geo API 调用失败: {e}")

        city_id = QWEATHER_CITY_IDS.get(city)
        if city_id:
            logger.info(f"[Weather] 使用内置城市 ID: {city} -> {city_id}")
        else:
            logger.warning(f"[Weather] 未找到城市 ID: {city}")
        return city_id

    def _qweather_current(self, city: str) -> dict[str, Any]:
        try:
            city_id = self._get_qweather_city_id(city)
            if not city_id:
                return self._mock_weather(city)

            url = f"{self.base_url.rstrip('/')}/v7/weather/now"
            resp = self.client.get(
                url,
                params={"location": city_id},
                headers=self._qweather_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            now = data.get("now", {})
            return {
                "success": True,
                "temp": int(now.get("temp", 20)),
                "feels_like": int(now.get("feelsLike", 20)),
                "humidity": int(now.get("humidity", 50)),
                "description": now.get("text", "晴"),
                "icon": now.get("icon", "100"),
                "wind_speed": float(now.get("windSpeed", 0)),
                "city": city,
                "provider": "qweather",
            }
        except Exception as e:
            logger.error(f"[Weather] 和风天气当前天气获取失败: {e}")
            return self._mock_weather(city)

    def _qweather_forecast(self, city: str, days: int) -> list:
        try:
            city_id = self._get_qweather_city_id(city)
            if not city_id:
                return self._mock_forecast(city, days)

            url = f"{self.base_url.rstrip('/')}/v7/weather/{days}d"
            resp = self.client.get(
                url,
                params={"location": city_id},
                headers=self._qweather_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            daily = []
            for d in data.get("daily", [])[:days]:
                daily.append({
                    "date": d["fxDate"],
                    "temp_max": int(d["tempMax"]),
                    "temp_min": int(d["tempMin"]),
                    "description": f"{d['textDay']}/{d['textNight']}",
                    "icon": d["iconDay"],
                })
            return daily
        except Exception as e:
            logger.error(f"[Weather] 和风天气预报获取失败: {e}")
            return self._mock_forecast(city, days)

    # ---- Mock数据回退 ----
    def _mock_weather(self, city: str) -> dict[str, Any]:
        """内置Mock天气数据"""
        mock_data = {
            "北京": {"temp": 15, "description": "晴", "humidity": 45},
            "上海": {"temp": 22, "description": "多云", "humidity": 65},
            "广州": {"temp": 28, "description": "晴", "humidity": 70},
            "深圳": {"temp": 29, "description": "晴", "humidity": 72},
            "杭州": {"temp": 20, "description": "小雨", "humidity": 78},
            "成都": {"temp": 18, "description": "阴", "humidity": 82},
            "西安": {"temp": 16, "description": "晴", "humidity": 50},
            "重庆": {"temp": 24, "description": "多云", "humidity": 75},
            "武汉": {"temp": 21, "description": "晴", "humidity": 60},
            "南京": {"temp": 19, "description": "多云", "humidity": 62},
            "苏州": {"temp": 20, "description": "阴", "humidity": 68},
            "厦门": {"temp": 26, "description": "晴", "humidity": 65},
            "桂林": {"temp": 25, "description": "小雨", "humidity": 80},
            "丽江": {"temp": 14, "description": "晴", "humidity": 40},
            "大理": {"temp": 16, "description": "晴", "humidity": 45},
        }
        data = mock_data.get(city, {"temp": 20, "description": "晴", "humidity": 60})
        return {
            "success": True,
            "temp": data["temp"],
            "feels_like": data["temp"],
            "humidity": data["humidity"],
            "description": data["description"],
            "icon": "01d",
            "wind_speed": 3.5,
            "city": city,
            "provider": "mock",
        }

    def _mock_forecast(self, city: str, days: int) -> list:
        """内置Mock天气预报"""
        current = self._mock_weather(city)
        base_temp = current["temp"]
        descriptions = ["晴", "多云", "阴", "小雨", "晴", "多云"]
        result = []
        for i in range(days):
            date = (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")
            result.append({
                "date": date,
                "temp_max": base_temp + 3,
                "temp_min": base_temp - 5,
                "description": descriptions[i % len(descriptions)],
                "icon": "02d",
            })
        return result
