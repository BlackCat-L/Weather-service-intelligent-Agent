# -*- coding: utf-8 -*-
"""气象数据工具：预报 / 实况 / 历史（数据源：Open-Meteo，免费无需 API Key）。

设计要点 —— **返回给模型的数据必须是压缩过的**：
逐小时数据 16 天 × 24 小时 × 8 个要素是三千多个数字，全塞进上下文既贵又会
稀释模型注意力。所以这里按「日」聚合后返回，只有明确要看某一天逐小时时
才展开，且只返回用户关心的那几个要素。
"""
import datetime as dt

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# WMO 天气代码 → 中文描述。模型看到数字不知道什么意思，必须转成人话。
WMO = {
    0: "晴", 1: "基本晴朗", 2: "少云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨",
    85: "小阵雪", 86: "大阵雪",
    95: "雷阵雨", 96: "雷阵雨伴小冰雹", 99: "雷阵雨伴大冰雹",
}

DAILY_VARS = [
    "temperature_2m_max", "temperature_2m_min", "precipitation_sum",
    "wind_speed_10m_max", "shortwave_radiation_sum", "weather_code",
]

HOURLY_VARS = [
    "temperature_2m", "relative_humidity_2m", "precipitation",
    "wind_speed_10m", "wind_speed_100m", "shortwave_radiation",
]


def _get(url: str, params: dict) -> dict:
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"气象接口返回 {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def forecast_daily(lat: float, lon: float, days: int = 3,
                   timezone: str = "Asia/Shanghai") -> dict:
    """未来若干天的逐日预报概要。"""
    days = max(1, min(int(days), 16))
    data = _get(FORECAST_URL, {
        "latitude": lat, "longitude": lon,
        "daily": ",".join(DAILY_VARS),
        "timezone": timezone,
        "forecast_days": days,
    })
    daily = data.get("daily") or {}
    out = []
    for i, date in enumerate(daily.get("time", [])):
        code = (daily.get("weather_code") or [None])[i]
        out.append({
            "date": date,
            "temp_max_c": (daily.get("temperature_2m_max") or [None])[i],
            "temp_min_c": (daily.get("temperature_2m_min") or [None])[i],
            "precip_mm": (daily.get("precipitation_sum") or [None])[i],
            "wind_max_kmh": (daily.get("wind_speed_10m_max") or [None])[i],
            "radiation_sum_mj_m2": (daily.get("shortwave_radiation_sum") or [None])[i],
            "weather": WMO.get(code, f"代码{code}"),
        })
    return {
        "place": {"lat": data.get("latitude"), "lon": data.get("longitude")},
        "timezone": data.get("timezone"),
        "data_source": "Open-Meteo 预报",
        "unit_note": "风速 km/h；辐照总量 MJ/m²（除以 3.6 得 kWh/m²）",
        "days": out,
    }


def forecast_hourly(lat: float, lon: float, date: str,
                    start_hour: int = 0, end_hour: int = 23,
                    timezone: str = "Asia/Shanghai") -> dict:
    """指定某一天的逐小时数据（用于光伏/风电出力这类需要小时分辨率的分析）。"""
    data = _get(FORECAST_URL, {
        "latitude": lat, "longitude": lon,
        "hourly": ",".join(HOURLY_VARS),
        "timezone": timezone,
        "start_date": date, "end_date": date,
    })
    hourly = data.get("hourly") or {}
    times = hourly.get("time", [])
    rows = []
    for i, ts in enumerate(times):
        h = int(ts[11:13])
        if not (start_hour <= h <= end_hour):
            continue
        rows.append({
            "time": ts[11:16],
            "temp_c": (hourly.get("temperature_2m") or [None])[i],
            "rh_pct": (hourly.get("relative_humidity_2m") or [None])[i],
            "precip_mm": (hourly.get("precipitation") or [None])[i],
            "wind10_kmh": (hourly.get("wind_speed_10m") or [None])[i],
            "wind100_kmh": (hourly.get("wind_speed_100m") or [None])[i],
            "radiation_wm2": (hourly.get("shortwave_radiation") or [None])[i],
        })
    if not rows:
        return {"error": f"没有取到 {date} 的数据。该日期可能超出预报范围（预报最多未来 16 天）。"}
    return {
        "date": date,
        "hours": f"{start_hour:02d}:00-{end_hour:02d}:00",
        "data_source": "Open-Meteo 预报",
        "unit_note": "风速 km/h，辐照 W/m²",
        "rows": rows,
    }


def _shift_year(date_str: str, years: int = -1) -> str:
    """把日期前后推 N 年。2月29日在非闰年会越界，退到 2月28日处理。"""
    import datetime as _dt
    d = _dt.date.fromisoformat(date_str)
    try:
        return d.replace(year=d.year + years).isoformat()
    except ValueError:
        return d.replace(year=d.year + years, day=28).isoformat()


def _fetch_archive(lat: float, lon: float, start_date: str, end_date: str,
                   timezone: str) -> dict:
    """拉取一段历史数据并做统计汇总。"""
    data = _get(ARCHIVE_URL, {
        "latitude": lat, "longitude": lon,
        "daily": ",".join(DAILY_VARS),
        "timezone": timezone,
        "start_date": start_date, "end_date": end_date,
    })
    daily = data.get("daily") or {}
    times = daily.get("time", [])
    out = []
    for i, date in enumerate(times):
        code = (daily.get("weather_code") or [None])[i]
        out.append({
            "date": date,
            "temp_max_c": (daily.get("temperature_2m_max") or [None])[i],
            "temp_min_c": (daily.get("temperature_2m_min") or [None])[i],
            "precip_mm": (daily.get("precipitation_sum") or [None])[i],
            "radiation_sum_mj_m2": (daily.get("shortwave_radiation_sum") or [None])[i],
            "weather": WMO.get(code, f"代码{code}"),
        })

    def avg(key):
        vals = [r[key] for r in out if isinstance(r.get(key), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None

    def total(key):
        vals = [r[key] for r in out if isinstance(r.get(key), (int, float))]
        return round(sum(vals), 1) if vals else None

    summary = {}
    if out:
        summary = {
            "days": len(out),
            "avg_temp_max_c": avg("temp_max_c"),
            "avg_temp_min_c": avg("temp_min_c"),
            "avg_radiation_sum_mj_m2": avg("radiation_sum_mj_m2"),
            "total_precip_mm": total("precip_mm"),
        }
    return {"period": f"{start_date} ~ {end_date}", "summary": summary, "days": out}


def historical_daily(lat: float, lon: float, start_date: str, end_date: str,
                     timezone: str = "Asia/Shanghai", compare_yoy: bool = False) -> dict:
    """历史再分析数据（ERA5），用于算气候均值、做同比分析。

    **compare_yoy 为什么存在**：评估集跑出的真实失败——用户问「最近一周和往年
    同期比气温偏高还是偏低」，模型查完最近一周的数据后，**没有意识到需要再查一次
    去年同期**，而是转去反复检索知识库，最终耗尽轮次没能给出结论。

    根因是：这类问题需要「对同一工具用不同参数调用两次」，而模型在只有一次调用
    结果时容易误判任务已完成。把「去年同期」内建进一次调用，既消除这类失败，
    也省掉一轮往返。**凡是需要对同一工具多次调用才能回答的问题，
    都应当考虑把多步收进一次工具调用。**
    """
    cur = _fetch_archive(lat, lon, start_date, end_date, timezone)
    if not compare_yoy:
        return {
            "period": cur["period"],
            "data_source": "Open-Meteo 历史再分析（ERA5）",
            "summary": cur["summary"],
            "days": cur["days"][-31:],   # 最多返回 31 天明细，避免上下文爆炸
        }

    ly_start = _shift_year(start_date, -1)
    ly_end = _shift_year(end_date, -1)
    ly = _fetch_archive(lat, lon, ly_start, ly_end, timezone)

    cs, ls = cur["summary"], ly["summary"]
    delta = {}
    for k in ("avg_temp_max_c", "avg_temp_min_c", "avg_radiation_sum_mj_m2", "total_precip_mm"):
        a, b = cs.get(k), ls.get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            delta[k] = round(a - b, 2)

    # 直接给出「偏高还是偏低」的结论，避免模型在数值比较上自由发挥
    cmax_d, lmax_d = cs.get("avg_temp_max_c"), ls.get("avg_temp_max_c")
    if isinstance(cmax_d, (int, float)) and isinstance(lmax_d, (int, float)):
        diff = round(cmax_d - lmax_d, 2)
        if diff >= 1.0:
            verdict = f"气温偏高：平均最高气温较去年同期高 {abs(diff)}°C"
        elif diff <= -1.0:
            verdict = f"气温偏低：平均最高气温较去年同期低 {abs(diff)}°C"
        else:
            verdict = f"与去年同期基本持平（相差 {diff}°C）"
    else:
        verdict = "数据不足，无法判断偏高或偏低"

    return {
        "data_source": "Open-Meteo 历史再分析（ERA5）",
        "compare": "与去年同期对比",
        "current": {"period": cur["period"], "summary": cs},
        "last_year": {"period": ly["period"], "summary": ls, "note": "去年同期同长度时段"},
        "delta": {"说明": "current 减去 last_year", **delta},
        "conclusion": verdict,
        "note_type": "已内建同比对比，无需再次调用本工具查询去年同期。",
    }


def register(reg):
    reg.tool(
        name="get_forecast_daily",
        description=(
            "查询指定经纬度未来几天的逐日天气预报（气温、降水、最大风速、辐照总量、天气现象）。"
            "**用户问「明天/未来几天天气怎么样」时用这个**。"
            "必须先通过 geocode_place 拿到经纬度。"
        ),
        parameters={
            "lat": {"type": "number", "description": "纬度", "required": True},
            "lon": {"type": "number", "description": "经度", "required": True},
            "days": {"type": "integer", "description": "预报天数 1-16，默认 3"},
        },
    )(forecast_daily)

    reg.tool(
        name="get_forecast_hourly",
        description=(
            "查询指定经纬度**某一天**的逐小时气象数据（逐小时气温、湿度、降水、"
            "10米/100米风速、短波辐照）。"
            "**做光伏或风电出力分析、判断某个时段（如「明天下午」）的具体条件时用这个**。"
            "注意日期必须在未来 16 天内。"
        ),
        parameters={
            "lat": {"type": "number", "description": "纬度", "required": True},
            "lon": {"type": "number", "description": "经度", "required": True},
            "date": {"type": "string", "description": "日期，格式 YYYY-MM-DD", "required": True},
            "start_hour": {"type": "integer", "description": "起始小时 0-23，默认 0"},
            "end_hour": {"type": "integer", "description": "结束小时 0-23，默认 23"},
        },
    )(forecast_hourly)

    reg.tool(
        name="get_history_daily",
        description=(
            "查询指定经纬度**历史**逐日气象数据（ERA5 再分析），返回明细与统计均值。"
            "用于算气候同期均值、做同比分析、判断「今年比往年热吗」这类问题。\n"
            "**问「和往年/去年同期比偏高还是偏低」时，务必传 compare_yoy=true**——"
            "它会一次性返回今年与去年同期的数据、差值和结论，"
            "不要为去年同期再单独调用一次本工具。\n"
            "历史数据最早可到 1940 年，但 ERA5 有约 5 天延迟，最近的日期可能取不到。"
        ),
        parameters={
            "lat": {"type": "number", "description": "纬度", "required": True},
            "lon": {"type": "number", "description": "经度", "required": True},
            "start_date": {"type": "string", "description": "起始日期 YYYY-MM-DD", "required": True},
            "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD", "required": True},
            "compare_yoy": {
                "type": "boolean",
                "description": ("是否同时返回去年同期数据并给出对比结论。"
                                "**用户提到「往年」「去年同期」「比去年」时必须传 true。**"),
            },
        },
    )(historical_daily)
