"""time_utils —— 通用时间与天气工具（v9.5 PulseNet）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import threading
import time
from typing import Any

from config import EXTERNAL_CALL_TIMEOUTS

# ========== 时间获取（无状态，线程安全） ==========

def get_current_datetime() -> dict[str, Any]:
    """
    获取当前完整日期时间信息。
    
    Returns:
        包含年、月、日、时、分、星期、时段、格式化字符串的字典
    """
    import datetime
    now = datetime.datetime.now()
    weekday_map = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    
    hour = now.hour
    if 5 <= hour < 8:
        period = "清晨"
    elif 8 <= hour < 12:
        period = "上午"
    elif 12 <= hour < 14:
        period = "中午"
    elif 14 <= hour < 18:
        period = "下午"
    elif 18 <= hour < 21:
        period = "傍晚"
    elif 21 <= hour < 23:
        period = "晚上"
    else:
        period = "深夜"
    
    return {
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "hour": now.hour,
        "minute": now.minute,
        "weekday": weekday_map[now.weekday()],
        "period": period,
        "date_str": f"{now.year}年{now.month}月{now.day}日",
        "full_str": f"{now.year}年{now.month}月{now.day}日 {weekday_map[now.weekday()]} {period}",
    }




# ========== 天气获取（有状态，线程安全） ==========

_weather_cache: dict[str, Any] = {}
_last_weather_check: float = 0.0
_weather_cache_lock = threading.Lock()
_WEATHER_CHECK_INTERVAL = 1800  # 30分钟


def get_weather(city: str = "许昌") -> dict[str, Any]:
    """
    获取指定城市的天气信息（带缓存和线程安全保护）。
    
    Args:
        city: 城市名称，默认"许昌"
    
    Returns:
        包含城市、温度、天气描述、湿度的字典
    """
    global _weather_cache, _last_weather_check
    
    now = time.time()
    
    # 缓存命中：直接返回
    with _weather_cache_lock:
        if _weather_cache and (now - _last_weather_check) < _WEATHER_CHECK_INTERVAL:
            return dict(_weather_cache)
    
    # 缓存未命中：发起网络请求
    try:
        import urllib.parse

        encoded_city = urllib.parse.quote(city)
        url = f"https://wttr.in/{encoded_city}?format=j1"
        
        # ★FIX(SSRF): 统一经 ssrf_guard 出站校验，防止 scheme/私网地址逃逸
        from nucleus.ssrf_guard import safe_http_json

        _ok, data = safe_http_json(
            url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=EXTERNAL_CALL_TIMEOUTS["http_read"]
        )
        if not _ok or not isinstance(data, dict):
            return dict(_weather_cache) if _weather_cache else {}
        current = data.get("current_condition", [{}])[0]

        new_cache = {
            "city": city,
            "temperature": current.get("temp_C", "--"),
            "description": current.get("weatherDesc", [{"value": "未知"}])[0]["value"],
            "humidity": current.get("humidity", "--"),
            "updated_at": now,
        }

        with _weather_cache_lock:
            _weather_cache = new_cache
            _last_weather_check = now

        return dict(new_cache)
            
    except Exception as e:
        print(f"[WARNING] time_utils.py:128: {type(e).__name__}: {e}")
        fallback = {"city": city, "temperature": "--", "description": "未知", "error": "获取失败"}
        with _weather_cache_lock:
            _weather_cache = fallback
            _last_weather_check = now
        return dict(fallback)