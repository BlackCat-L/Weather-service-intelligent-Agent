# -*- coding: utf-8 -*-
"""派生指标与风险评估工具 —— 本项目的领域价值所在。

**为什么要有这一层？**
天气 App 是「数据查询」：把气温、风速原样呈现。
气象服务智能体是「决策支持」：用户问的是「我该不该做某事」，需要把气象
要素经过业务模型翻译成可直接使用的结论（发电量、作业窗口、风险等级）。

**一个重要的设计决策：计算工具自己去取数，而不是让模型传数组进来。**
如果把「逐小时辐照数组」当成工具参数，模型需要把 24 个数字原样抄一遍——
既浪费 token，又必然出现抄错（这是典型的幻觉来源）。所以这里的工具签名是
`(lat, lon, date, ...)`，内部自己取数、自己算，模型只传业务参数。

对应的防幻觉原则：**数值一律由程序算，模型只负责解释**——和前一项目
「排盘不让模型算」是同一条原则。
"""
import datetime as dt

from .forecast import forecast_hourly

# ---------- 物理模型参数（可在面试时解释依据）----------

# 光伏：晶硅组件的典型温度系数，约 -0.4%/°C，即组件温度每高于 25°C 一度，出力降 0.4%
PV_TEMP_COEFF = -0.004
# 光伏：系统性能比 Performance Ratio，含逆变器损耗、线损、积灰、遮挡等，典型 0.75~0.85
PV_DEFAULT_PR = 0.80
# 光伏：组件温度估算用（近似 NOCT 模型），辐照 800 W/m² 时组件比气温高约 30°C
PV_NOCT_RADIATION = 800.0
PV_NOCT_DELTA_T = 30.0

# 风电：典型机组功率曲线关键点（m/s）
WIND_CUT_IN = 3.0      # 切入风速：低于此不发电
WIND_RATED = 12.0      # 额定风速：达到满发
WIND_CUT_OUT = 25.0    # 切出风速：超过此保护停机


def _ms(kmh):
    """km/h → m/s"""
    return round(kmh / 3.6, 2) if isinstance(kmh, (int, float)) else None


def _segments(rows: list[dict], value_key: str) -> dict:
    """按「夜间/上午/中午/下午/傍晚」聚合，值的求和与峰值都算好。

    为什么要这么做：如果只给模型 24 行逐小时数据，它会自己去做加法——
    而模型心算出来的数字在证据里查不到出处，事后无法审计，正是幻觉的温床。
    把聚合值直接算好给它，「数值一律由程序算」这条原则才算落到底。
    """
    buckets = {
        "夜间(00-06)": range(0, 6),
        "上午(06-11)": range(6, 11),
        "中午(11-14)": range(11, 14),
        "下午(14-18)": range(14, 18),
        "傍晚(18-24)": range(18, 24),
    }
    out = {}
    for label, hours in buckets.items():
        vals = []
        for r in rows:
            try:
                h = int(str(r.get("time", ""))[:2])
            except ValueError:
                continue
            v = r.get(value_key)
            if h in hours and isinstance(v, (int, float)):
                vals.append(v)
        if vals:
            out[label] = {
                "合计": round(sum(vals), 1),
                "均值": round(sum(vals) / len(vals), 1),
                "峰值": round(max(vals), 1),
                "小时数": len(vals),
            }
    # 「下午」常被单独问，再给一个午后（12-18）的合并值
    noon_vals = []
    for r in rows:
        try:
            h = int(str(r.get("time", ""))[:2])
        except ValueError:
            continue
        v = r.get(value_key)
        if 12 <= h < 18 and isinstance(v, (int, float)):
            noon_vals.append(v)
    if noon_vals:
        out["午后(12-18)"] = {
            "合计": round(sum(noon_vals), 1),
            "均值": round(sum(noon_vals) / len(noon_vals), 1),
            "峰值": round(max(noon_vals), 1),
            "小时数": len(noon_vals),
        }
    return out


def dew_point(temp_c: float, rh_pct: float) -> float:
    """露点温度（Magnus 公式）。

    露点越接近气温，空气越接近饱和，越容易起雾/结露——对光伏组件积灰、
    输电线路、户外作业都有影响。
    """
    import math
    a, b = 17.27, 237.7
    gamma = (a * temp_c) / (b + temp_c) + math.log(max(rh_pct, 1) / 100.0)
    return round((b * gamma) / (a - gamma), 1)


def pv_output(lat: float, lon: float, date: str, capacity_kwp: float = 100.0,
              pr: float = PV_DEFAULT_PR) -> dict:
    """光伏电站出力预测（简化物理模型）。

    模型：
        G          = 短波辐照 W/m²
        T_cell     = T_air + (G / 800) * 30                      组件温度估算
        temp_factor= 1 + (-0.004) * (T_cell - 25)                温度效率修正
        P          = capacity_kwp * (G/1000) * PR * temp_factor   理论出力 kW
    """
    h = forecast_hourly(lat, lon, date)
    if "error" in h:
        return h

    rows, hourly_out = h["rows"], []
    peak_p, peak_t, total_kwh, rad_sum = 0.0, None, 0.0, 0.0
    for r in rows:
        g = r.get("radiation_wm2")
        t = r.get("temp_c")
        if not isinstance(g, (int, float)) or not isinstance(t, (int, float)):
            continue
        t_cell = t + (g / PV_NOCT_RADIATION) * PV_NOCT_DELTA_T
        factor = max(0.0, 1 + PV_TEMP_COEFF * (t_cell - 25))
        p = max(0.0, capacity_kwp * (g / 1000.0) * pr * factor)
        rad_sum += g
        total_kwh += p            # 小时分辨率 → kW × 1h = kWh
        if p > peak_p:
            peak_p, peak_t = p, r["time"]
        hourly_out.append({"time": r["time"], "radiation_wm2": g, "temp_c": t,
                           "cell_temp_c": round(t_cell, 1), "power_kw": round(p, 1)})

    if not hourly_out:
        return {"error": f"{date} 没有可用的逐小时数据"}

    # 折算成等效满发小时数：行业里衡量电站表现的标准指标
    eih = round(total_kwh / capacity_kwp, 2) if capacity_kwp else None
    return {
        "date": date, "capacity_kwp": capacity_kwp, "performance_ratio": pr,
        "data_source": "Open-Meteo 预报 + 简化物理模型",
        "summary": {
            "理论发电量_kwh": round(total_kwh, 1),
            "峰值功率_kw": round(peak_p, 1),
            "峰值时刻": peak_t,
            "等效满发小时数_h": eih,
            "平均辐照_wm2": round(rad_sum / len(hourly_out), 1),
        },
        # 分时段聚合值由程序算好，避免模型自己去加总小时数据（那会产生
        # "证据里查不到出处的数字"，是幻觉的温床）
        "segments": _segments(hourly_out, "power_kw"),
        "hourly": hourly_out,
        "model_note": (
            "模型：P = 装机容量 × (辐照/1000) × 性能比 × 温度修正系数；"
            f"温度系数 {PV_TEMP_COEFF}/°C，性能比 {pr}。"
            "未考虑逆变器限幅、遮挡、积雪与积灰，实际值通常略低于此估算。"
        ),
    }


def wind_output(lat: float, lon: float, date: str, rated_kw: float = 2000.0) -> dict:
    """风电机组出力预测（标准功率曲线模型）。"""
    h = forecast_hourly(lat, lon, date)
    if "error" in h:
        return h

    hourly_out, total_kwh, peak_p, peak_t, hours_ok = [], 0.0, 0.0, None, 0
    for r in h["rows"]:
        v = _ms(r.get("wind100_kmh"))      # 轮毂高度通常接近 100m
        if v is None:
            continue
        if v < WIND_CUT_IN or v >= WIND_CUT_OUT:
            p = 0.0
            state = "停机（低于切入风速）" if v < WIND_CUT_IN else "保护停机（超过切出风速）"
        elif v < WIND_RATED:
            p = rated_kw * (v ** 3 - WIND_CUT_IN ** 3) / (WIND_RATED ** 3 - WIND_CUT_IN ** 3)
            state = "发电中"
            hours_ok += 1
        else:
            p = rated_kw
            state = "满发"
            hours_ok += 1
        total_kwh += p
        if p > peak_p:
            peak_p, peak_t = p, r["time"]
        hourly_out.append({"time": r["time"], "wind_ms": v,
                           "power_kw": round(p, 1), "state": state})

    if not hourly_out:
        return {"error": f"{date} 没有可用的逐小时数据"}

    return {
        "date": date, "rated_kw": rated_kw,
        "data_source": "Open-Meteo 预报（100 米风速）+ 标准功率曲线",
        "summary": {
            "理论发电量_kwh": round(total_kwh, 1),
            "峰值功率_kw": round(peak_p, 1),
            "峰值时刻": peak_t,
            "有效发电小时数": hours_ok,
            "平均风速_ms": round(sum(x["wind_ms"] for x in hourly_out) / len(hourly_out), 2),
        },
        "segments": _segments(hourly_out, "power_kw"),
        "hourly": hourly_out,
        "model_note": (
            f"标准功率曲线：切入 {WIND_CUT_IN} m/s、额定 {WIND_RATED} m/s、"
            f"切出 {WIND_CUT_OUT} m/s；额定以下按风速立方关系折算。"
            "实际出力还受湍流、尾流、机组可用率影响。"
        ),
    }


# 场景 → 阈值规则。每条规则：(判定函数, 风险等级, 说明)
def _risk_rules(scenario: str):
    s = (scenario or "").lower()
    # 清洗作业：这里编码的是「作业适宜性」，与「设备风险」是两回事。
    # 关键规则：小雨（0.1-10mm）恰恰是最不该清洗的天气——会形成泥点，
    # 且雨量不足以冲走污渍，等于白干。这个规则靠设备风险模型推不出来，
    # 必须显式编码，否则会出现「工具报无风险 → 模型建议照常清洗」的错误结论。
    if "clean" in s or "清洗" in s or "洗板" in s:
        return [
            (lambda r: 0.1 <= (r.get("precip_mm") or 0) < 10, "高",
             "有小雨：雨水混合灰尘蒸发后会形成泥点，反而降低透光率，且雨量不足以冲刷干净——不宜清洗"),
            (lambda r: (r.get("precip_mm") or 0) >= 10, "中",
             "降水充足（≥10mm）可起到自然清洗作用，无需人工清洗，安排清洗属于浪费工时"),
            (lambda r: (r.get("wind10_kmh") or 0) >= 39, "高",
             "10米风速≥10.8m/s（6级），超出高空作业安全门槛，禁止上屋顶/支架作业"),
            (lambda r: (r.get("temp_c") or 0) >= 35, "中",
             "气温≥35°C，户外作业中暑风险高，且水渍蒸发过快易留痕"),
            (lambda r: (r.get("rh_pct") or 0) >= 95, "低",
             "相对湿度接近饱和，清洗后不易干燥，可能留下水渍"),
        ]
    if "pv" in s or "光伏" in s or "太阳能" in s:
        return [
            (lambda r: (r.get("wind10_kmh") or 0) >= 43, "高", "10米风速≥12级(43km/h)，组件支架与组件存在受损风险，且可能触发逆变器保护停机"),
            (lambda r: (r.get("radiation_wm2") or 0) >= 1000, "中", "辐照极强，组件温度将显著升高，出力效率下降（约每高 1°C 降 0.4%）"),
            (lambda r: (r.get("temp_c") or 99) >= 38, "中", "气温过高，组件散热受限，转换效率明显衰减"),
            (lambda r: (r.get("precip_mm") or 0) >= 10, "低", "较强降水可能冲刷积灰（有利）但伴随云量增大、出力下滑"),
            (lambda r: 0.1 <= (r.get("precip_mm") or 0) < 10, "低",
             "有小雨：会形成泥点降低透光率，且雨量不足以自然清洗，若计划清洗应改期"),
        ]
    if "wind" in s or "风电" in s:
        return [
            (lambda r: (_ms(r.get("wind100_kmh")) or 0) >= WIND_CUT_OUT, "高",
             f"100米风速≥{WIND_CUT_OUT} m/s，机组保护停机，出力归零"),
            (lambda r: (_ms(r.get("wind100_kmh")) or 0) >= WIND_RATED, "低",
             "风速达到额定以上，机组满发（有利）"),
            (lambda r: 0 < (_ms(r.get("wind100_kmh")) or 0) < WIND_CUT_IN, "低",
             "风速低于切入风速，机组不发电"),
        ]
    if "transport" in s or "交通" in s or "低空" in s:
        return [
            (lambda r: (r.get("wind10_kmh") or 0) >= 39, "高", "10米风速≥10.8m/s（6级），低空飞行器与高栏车辆侧风风险高"),
            (lambda r: (r.get("precip_mm") or 0) >= 8, "中", "降水较强，能见度与路面附着力下降"),
            (lambda r: (r.get("rh_pct") or 0) >= 95, "中", "相对湿度接近饱和，易起雾，能见度风险"),
        ]
    if "agri" in s or "农业" in s or "农" in s:
        return [
            (lambda r: (r.get("temp_c") or 99) <= 2, "高", "气温接近或低于冰点，作物霜冻风险"),
            (lambda r: (r.get("precip_mm") or 0) >= 15, "中", "短时降水强，注意田间排水与病害"),
            (lambda r: (r.get("rh_pct") or 0) >= 90, "中", "高湿环境易诱发真菌性病害"),
            (lambda r: (r.get("wind10_kmh") or 0) >= 30, "中", "风力较大，设施农业与高秆作物倒伏风险"),
        ]
    # 默认：户外作业
    return [
        (lambda r: (r.get("temp_c") or 0) >= 35, "高", "高温，户外作业中暑风险高，建议避开正午时段"),
        (lambda r: (r.get("temp_c") or 99) <= 0, "中", "低温，注意防冻与路面结冰"),
        (lambda r: (r.get("precip_mm") or 0) >= 8, "中", "降水明显，户外作业受限"),
        (lambda r: (r.get("wind10_kmh") or 0) >= 39, "中", "风力较强，高空与吊装作业风险"),
    ]


def _assess_one_day(rows: list[dict], scenario: str) -> dict:
    """对单日逐小时数据做扫描，返回该日的结论与风险时段。"""
    rules = _risk_rules(scenario)
    hits = []
    for r in rows:
        for fn, level, desc in rules:
            try:
                if fn(r):
                    hits.append({
                        "time": r["time"], "level": level, "reason": desc,
                        "wind_kmh": r.get("wind10_kmh"), "temp_c": r.get("temp_c"),
                        "precip_mm": r.get("precip_mm"),
                        "dew_point_c": dew_point(r["temp_c"], r["rh_pct"])
                        if isinstance(r.get("temp_c"), (int, float)) and
                        isinstance(r.get("rh_pct"), (int, float)) else None,
                    })
            except Exception:
                continue

    level_rank = {"高": 3, "中": 2, "低": 1}
    worst = max((x["level"] for x in hits), key=lambda l: level_rank[l], default="无")
    risky = {x["time"] for x in hits if x["level"] in ("高", "中")}
    safe_hours = [r["time"] for r in rows if r["time"] not in risky]

    top_rank = max((level_rank[x["level"]] for x in hits), default=0)
    level_inv = {3: "高", 2: "中", 1: "低"}
    if top_rank >= 3:
        suitable, conclusion = False, f"不适宜：存在{level_inv[3]}风险时段，建议改期或调整作业安排"
    elif top_rank == 2:
        suitable, conclusion = "conditional", f"需条件安排：存在{level_inv[2]}风险时段，请避开相关时段作业"
    else:
        suitable, conclusion = True, "适宜：全天未检出中高风险条件"

    return {
        "overall_level": worst,
        "suitable": suitable,
        "conclusion": conclusion,
        "risk_hits": hits[:24],
        "clear_window": safe_hours,
        "summary": {
            "风险命中小时数": len({x["time"] for x in hits}),
            "无中高风险时段": f"{safe_hours[0]}~{safe_hours[-1]}" if safe_hours else "无",
            "总计小时数": len(rows),
        },
    }


def assess_risk(lat: float, lon: float, date: str, scenario: str = "outdoor",
                end_date: str | None = None) -> dict:
    """按业务场景做风险与适宜性扫描。支持单日与**日期区间**。

    阈值取自公开的气象灾害分级标准与行业经验，返回命中的时段与等级，
    便于模型据此给出「哪个时段能干活、哪个时段要停工」的具体建议。

    **为什么必须支持区间**：评估集跑出的真实问题——用户问「未来三天适合作户外作业吗」，
    而本工具一次只评估一天，模型就得为每天调一次，三天吃掉三轮工具调用，
    再叠上地名解析与预报查询，直接撞到轮次上限而**未能给出结论**。
    把「多天」收进一次调用，既避免这类失败，也省掉两轮往返。
    """
    single = not end_date or end_date == date

    if single:
        h = forecast_hourly(lat, lon, date)
        if "error" in h:
            return h
        day = _assess_one_day(h["rows"], scenario)
        return {
            "date": date, "scenario": scenario,
            "overall_level": day["overall_level"],
            "suitable": day["suitable"],
            "conclusion": day["conclusion"],
            "risk_hits": day["risk_hits"],
            "clear_window": day["clear_window"],
            "summary": day["summary"],
            "note": ("本工具同时评估【设备风险】与【作业适宜性】。注意二者不同："
                     "无设备风险不等于适合作业。conclusion 字段已给出综合结论，"
                     "请以它为准。阈值为公开气象灾害标准与行业经验的简化取值，"
                     "实际决策请结合现场条件与设备手册。"),
        }

    # ---- 区间模式：逐日评估后汇总 ----
    import datetime as _dt
    try:
        d0 = _dt.date.fromisoformat(date)
        d1 = _dt.date.fromisoformat(end_date)
    except ValueError:
        return {"error": "日期格式应为 YYYY-MM-DD"}
    if d1 < d0:
        d0, d1 = d1, d0
    span = (d1 - d0).days + 1
    if span > 8:
        return {"error": f"一次最多评估 8 天，当前请求跨度 {span} 天，请分批查询"}

    days = []
    cur = d0
    while cur <= d1:
        ds = cur.isoformat()
        hd = forecast_hourly(lat, lon, ds)
        if "error" not in hd:
            day = _assess_one_day(hd["rows"], scenario)
            days.append({
                "date": ds,
                "overall_level": day["overall_level"],
                "suitable": day["suitable"],
                "conclusion": day["conclusion"],
                "risk_hours": day["summary"]["风险命中小时数"],
                "clear_window": f"{day['clear_window'][0]}~{day['clear_window'][-1]}"
                                if day["clear_window"] else "无",
                # 只保留高/中风险明细，低风险时段罗列出来对决策无意义
                "key_risks": [x for x in day["risk_hits"] if x["level"] in ("高", "中")][:6],
            })
        cur += _dt.timedelta(days=1)

    if not days:
        return {"error": f"{date}~{end_date} 没有可用数据（预报最多覆盖未来 16 天）"}

    best = next((d for d in days if d["suitable"] is True), None)
    suitable_days = [d["date"] for d in days if d["suitable"] is True]
    return {
        "period": f"{date} ~ {end_date}",
        "scenario": scenario,
        "days": days,
        "summary": {
            "适宜天数": len(suitable_days),
            "适宜日期": suitable_days or "无",
            "建议首选": best["date"] if best else "无完全适宜的日期，需按 key_risks 避开时段",
        },
        "note": ("区间模式：已逐日评估。请直接引用各日的 conclusion 与 suitable 字段，"
                 "不要再为单日重复调用本工具。low 风险时段未列出以免干扰决策。"),
    }



def register(reg):
    reg.tool(
        name="calc_pv_output",
        description=(
            "计算光伏电站的逐小时理论发电出力与全天发电量。"
            "**用户问「光伏发电条件怎么样」「今天能发多少电」「适不适合安排检修」时用这个**。"
            "需要先拿到经纬度。工具会自行取数计算，你不需要传入任何气象数值。"
        ),
        parameters={
            "lat": {"type": "number", "description": "纬度", "required": True},
            "lon": {"type": "number", "description": "经度", "required": True},
            "date": {"type": "string", "description": "日期 YYYY-MM-DD（须在未来 16 天内）", "required": True},
            "capacity_kwp": {"type": "number", "description": "装机容量 kWp，默认 100"},
            "pr": {"type": "number", "description": "系统性能比 0.7-0.9，默认 0.80"},
        },
    )(pv_output)

    reg.tool(
        name="calc_wind_output",
        description=(
            "计算风电机组的逐小时出力与全天发电量（基于 100 米风速的标准功率曲线）。"
            "**用户问风电、风力发电条件、风机能不能满发时用这个**。"
        ),
        parameters={
            "lat": {"type": "number", "description": "纬度", "required": True},
            "lon": {"type": "number", "description": "经度", "required": True},
            "date": {"type": "string", "description": "日期 YYYY-MM-DD", "required": True},
            "rated_kw": {"type": "number", "description": "机组额定功率 kW，默认 2000"},
        },
    )(wind_output)

    reg.tool(
        name="assess_weather_risk",
        description=(
            "按业务场景对某一天做逐小时气象评估，返回：风险时段与等级、"
            "**综合结论（suitable / conclusion 字段）**、以及可作业时间窗口。\n"
            "**用户问「适不适合做某事」「有没有风险」「哪个时段合适」时用这个。**\n"
            "重要：本工具同时评估「设备风险」与「作业适宜性」——无设备风险**不等于**"
            "适合作业（例如小雨天对光伏设备无害，但恰恰不宜清洗）。"
            "务必以返回的 conclusion 字段为准，不要仅凭 overall_level 下判断。\n"
            "**问「未来几天」「这几天哪天合适」时，请一次传入 end_date 做区间查询"
            "（最多 8 天），不要为每天重复调用本工具——那样会浪费轮次且容易超时。**"
        ),
        parameters={
            "lat": {"type": "number", "description": "纬度", "required": True},
            "lon": {"type": "number", "description": "经度", "required": True},
            "date": {"type": "string", "description": "起始日期 YYYY-MM-DD", "required": True},
            "end_date": {
                "type": "string",
                "description": ("结束日期 YYYY-MM-DD。**查询多天时必须传这个参数**，"
                                "一次即可覆盖整个区间（最多 8 天），返回逐日结论与适宜日期。"),
            },
            "scenario": {
                "type": "string",
                "description": (
                    "业务场景：\n"
                    "· pv — 光伏发电设备风险\n"
                    "· cleaning — 光伏板清洗作业适宜性（问「能不能洗板」时用这个）\n"
                    "· wind — 风电\n"
                    "· transport — 交通与低空飞行\n"
                    "· agri — 农业\n"
                    "· outdoor — 户外作业（默认）"
                ),
                "enum": ["pv", "cleaning", "wind", "transport", "agri", "outdoor"],
            },
        },
    )(assess_risk)
