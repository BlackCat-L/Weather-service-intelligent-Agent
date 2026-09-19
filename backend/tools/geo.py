# -*- coding: utf-8 -*-
"""地理编码工具：中文地名 → 经纬度。

这是所有气象查询的第一跳——气象数据都是按经纬度取的，而用户说的是
「余杭区」「杭州」这种地名。对应 JD 里「实验数据采集」链路的入口：
    地名 → 经纬度 → 格点 → 时段切片 → 取数

**双数据源设计（实测踩坑后的方案）**
Open-Meteo 的地理编码底层是 GeoNames，只有**城市级**中文数据：
    「杭州」✓  「北京」✓  「余杭区」✗  「浙江省杭州市余杭区」✗
但气象服务的用户经常问的是区县、园区、站点这种更细的粒度，所以加了
Nominatim（OpenStreetMap）做补充——它对中文行政区划支持好得多。

策略：查询词带行政区划后缀（区/县/镇/乡/街道/村）时优先走 Nominatim，
否则先走 Open-Meteo（更快、更稳定），失败再回退。两个源都拿不到才报错。
"""
import logging

import requests

log = logging.getLogger(__name__)

OPENMETEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
UA = {"User-Agent": "weather-agent-demo/1.0 (research prototype)"}

# 出现这些后缀说明是区县/乡镇级，Open-Meteo 大概率查不到
SUBDISTRICT_MARKERS = ("区", "县", "镇", "乡", "街道", "村", "开发区", "园区", "新区")


def _openmeteo(place: str, count: int = 5) -> dict | None:
    try:
        r = requests.get(OPENMETEO_URL, params={
            "name": place, "count": count, "language": "zh", "format": "json",
        }, timeout=15)
        if r.status_code != 200:
            return None
        res = r.json().get("results") or []
    except requests.RequestException as e:
        log.warning("Open-Meteo 地理编码失败：%s", e)
        return None
    if not res:
        return None
    loc = res[0]
    return {
        "name": loc.get("name"),
        "admin1": loc.get("admin1"),
        "admin2": loc.get("admin2"),
        "country": loc.get("country"),
        "latitude": round(loc["latitude"], 4),
        "longitude": round(loc["longitude"], 4),
        "timezone": loc.get("timezone") or "Asia/Shanghai",
        "elevation_m": loc.get("elevation"),
        "source": "Open-Meteo GeoNames",
        "other_candidates": [
            {"name": x.get("name"), "admin1": x.get("admin1"),
             "lat": round(x["latitude"], 3), "lon": round(x["longitude"], 3)}
            for x in res[1:4]
        ],
    }


def _nominatim(place: str) -> dict | None:
    try:
        r = requests.get(NOMINATIM_URL, params={
            "q": place, "format": "json", "limit": 3, "accept-language": "zh-CN",
        }, headers=UA, timeout=20)
        if r.status_code != 200:
            return None
        res = r.json()
    except requests.RequestException as e:
        log.warning("Nominatim 地理编码失败：%s", e)
        return None
    if not res:
        return None

    x = res[0]
    # Nominatim 的 display_name 形如「余杭区, 杭州市, 浙江省, 中国」，拆出层级
    parts = [p.strip() for p in (x.get("display_name") or "").split(",")]
    return {
        "name": parts[0] if parts else place,
        "admin1": parts[2] if len(parts) > 2 else None,
        "admin2": parts[1] if len(parts) > 1 else None,
        "country": parts[3] if len(parts) > 3 else None,
        "latitude": round(float(x["lat"]), 4),
        "longitude": round(float(x["lon"]), 4),
        "timezone": "Asia/Shanghai",
        "elevation_m": None,
        "source": "Nominatim (OpenStreetMap)",
        "other_candidates": [
            {"name": (y.get("display_name") or "").split(",")[0].strip(),
             "lat": round(float(y["lat"]), 3), "lon": round(float(y["lon"]), 3)}
            for y in res[1:3]
        ],
    }


def geocode(place: str, count: int = 5) -> dict:
    """把地名解析成经纬度。城市级走 Open-Meteo，区县级回退 Nominatim。"""
    place = (place or "").strip()
    if not place:
        return {"found": False, "error": "地名不能为空"}

    is_subdistrict = any(m in place for m in SUBDISTRICT_MARKERS)
    order = [_nominatim, _openmeteo] if is_subdistrict else [_openmeteo, _nominatim]

    for fn in order:
        hit = fn(place)
        if hit:
            hit["found"] = True
            hit["query"] = place
            # 行政区的坐标通常是区划中心点，未必是用户关心的那个点，提示一下
            if is_subdistrict:
                hit["precision_note"] = (
                    f"「{place}」的坐标取的是行政区中心点。如果关注的是区内某个具体位置"
                    "（如某个电站或园区），坐标可能偏差十几公里，对光伏/风电这类应用会有影响。"
                )
            return hit

    return {
        "found": False,
        "query": place,
        "hint": "两个地理编码源都没找到该地名。请换用更常见的名称，"
                "例如把「余杭」换成「杭州」，或直接提供经纬度。",
    }


def register(reg):
    reg.tool(
        name="geocode_place",
        description=(
            "把地名解析成经纬度坐标。**所有气象数据查询都必须在拿到经纬度之后进行**，"
            "所以只要用户提到了地名（城市、区县、园区、站点），第一步就要调用这个工具。"
            "中文地名可直接传入，支持城市与区县两级。"
        ),
        parameters={
            "place": {
                "type": "string",
                "description": "地名，例如「杭州」「余杭区」「浙江杭州余杭」「新疆哈密」「内蒙古乌兰察布」",
                "required": True,
            },
        },
    )(geocode)
