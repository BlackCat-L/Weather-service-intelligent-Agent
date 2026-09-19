# -*- coding: utf-8 -*-
"""数值归因校验：防止模型编造气象数值。

**为什么气象场景必须做这件事？**
一般问答里模型编个数字可能无所谓；但气象服务的用户会拿这些数字去做真实决策——
安排户外作业、调度光伏检修、决定农机下田时间。模型随口说「明天 26 度」而实际
是 33 度，后果是实打实的。所以必须能回答一个问题：

    这个回答里的每个数字，是工具真的给过，还是模型编的？

**做法**
1. 递归收集工具调用轨迹里的全部数值 → 「证据集」
2. 抽取回答里的全部数字
3. 逐个在证据集里找出处，容忍浮点误差与常见单位换算
4. 输出归因率与未归因清单

**与前一项目的区别**
那边校验的是「字符串一致性」——模型有没有把干支抄错；
这边校验的是「数值有没有出处」——模型有没有凭空造数。
同一个思路（把确定性来源当唯一事实），但对象从文本变成了数据。

**一个反过来的价值**：未归因的数字往往不是模型在编，而是「工具该给的聚合值
没给，模型只好自己算」。实测中就是靠这个发现了 `calc_pv_output` 没返回分时段
合计、逼得模型自己加总小时数据的问题——于是给工具补上了 `segments` 字段。
所以这个校验器同时是**工具设计缺陷的探测器**。
"""
import json
import re

# 常见单位换算关系：同一个物理量的不同表述不应被判为「无出处」
# 风速 km/h ↔ m/s；辐照 W/m² ↔ kW/m²；能量 MJ ↔ kWh；比例 ↔ 百分比
_CONVERSIONS = (
    lambda v: v / 3.6,      # km/h → m/s
    lambda v: v * 3.6,      # m/s → km/h
    lambda v: v / 1000.0,   # W → kW
    lambda v: v * 1000.0,
    lambda v: v / 100.0,    # 比例 → 百分比
    lambda v: v * 100.0,
    lambda v: v * 24,       # 小时均值 → 日累计（数量级相近，易混）
    lambda v: v / 24.0,
    lambda v: -v,
)

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

# 日期识别（两套正则，用途不同）
#
# ⚠️ 务必区分「日期」与「数值范围」：`22-28°C` 这种温度区间绝不能被当成日期，
# 否则会被剥离掉，反而丢失真实的数值证据。
# 所以只认两种确定的形式：
#   ISO   : 2026-09-20 / 2026/09/20 / 2026年9月20日
#   中文月日: 9月20日 / 9月20
# 不认 `09-20` 这类无年份的裸数字对——歧义太大。
_ISO_DATE_RE = re.compile(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?")
_CN_MD_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日")
# 日期枚举里的省略形式：`9月20日、21日、22日` 中后半段没有「月」字。
# 不处理的话 21、22 会残留在数值统计里被当成"编造的气象数值"——
# 这是日期碎片问题的第三种形态（前两种：ISO 日期的 -09/-19、有序列表编号）。
#
# ⚠️ 但不能无脑剥离所有 `N日`：「历时 3日」里的 3 是时长不是日期，
# 剥掉就丢了真实数据。所以只在两种高置信语境下剥离：
#   · 前面是列举分隔符（、，,和至到）——「9月20日、21日」的后半段
#   · 出现在行首——「21日：晴」
# 保留分隔符本身（用捕获组回填），避免把句子粘连在一起。
_CN_DAY_ONLY_RE = re.compile(r"([、，,和至到]|^)(\s*)\d{1,2}\s*日", re.M)

# 小于这个值的数字噪音太大（步骤编号、等级数字等），不纳入统计
_MIN_ABS = 1.0


# 有序列表的编号标记：`1. ` / `2、` / `3) ` / `（4）`
# 这些是排版符号不是数据，但会被数字正则捕获（尤其编号 1 与 _MIN_ABS 阈值撞上）
_LIST_MARKER_RE = re.compile(r"(?m)^\s*(?:\d+\s*[.、)]|[（(]\s*\d+\s*[）)])\s*")


def strip_noise(text: str) -> str:
    """剥离会污染数值统计的非数据成分：日期与列表编号。

    **为什么必须做**：这两类东西都会被数字正则捕获，造成"凭空多出未归因数值"，
    让归因率这个指标失真。实测踩过两次：

    1. `2026-09-19` → 拆成 `2026` / `-09` / `-19`，于是 -9.0、-19.0 被当成
       待归因的气象数值。极端的例子是一道关于预报原理、不含任何气象数值的题
       被误判为 25%。
    2. 有序列表的编号「1.」→ 被当成数值 1.0（恰好又因为 `_MIN_ABS=1.0` 用 `>=`
       判断而没能被阈值挡掉），使一道完全正确的回答停在 80%。

    **不做会怎样**：指标失真，然后你会去优化一个根本不存在的问题——
    因为模型本来没做错。测量工具本身有偏差时，优化方向会被带偏。
    """
    t = _ISO_DATE_RE.sub(" ", text or "")
    t = _CN_MD_RE.sub(" ", t)
    t = _CN_DAY_ONLY_RE.sub(r"\1\2", t)   # 兜住「、21日、22日」这类省略月份的枚举
    return _LIST_MARKER_RE.sub(" ", t)


# 保留旧名字，避免外部调用点（如测试脚本）失效
strip_dates = strip_noise


def _walk_numbers(obj, out: set):
    """递归收集任意嵌套结构里的所有数值。"""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk_numbers(v, out)
    elif isinstance(obj, str):
        for m in _NUM_RE.findall(strip_noise(obj)):
            try:
                out.add(float(m))
            except ValueError:
                pass


def evidence_set(trace: list[dict]) -> set:
    """从工具调用轨迹里提取全部数值作为「证据」。"""
    ev: set = set()
    for entry in trace or []:
        if isinstance(entry.get("result"), dict) and entry["result"].get("error"):
            continue                      # 报错信息里的数字不算证据
        _walk_numbers(entry.get("result"), ev)
        _walk_numbers(entry.get("args"), ev)   # 工具入参里的数字也是模型用过的
    return ev


def _expand(ev: set, decimals: int = 2) -> set:
    """把证据数值扩展出常见换算形态与舍入形态。"""
    out = set()
    for v in ev:
        out.add(round(v, decimals))
        out.add(round(v, 0))
        out.add(round(v, 1))
        for fn in _CONVERSIONS:
            try:
                out.add(round(fn(v), decimals))
            except (OverflowError, ValueError):
                continue
    return out


def _grounded(x: float, expanded: set, tol: float = 0.02) -> bool:
    """判断某个数值是否能在证据集里找到出处（容忍浮点误差）。"""
    if abs(x) < _MIN_ABS:
        return True                       # 太小，忽略
    # 1% 相对误差或 0.02 绝对误差，取大者
    if x in expanded:
        return True
    for e in expanded:
        if abs(e - x) <= max(tol, abs(x) * 0.01):
            return True
    return False


def extract_answer_numbers(answer: str) -> list[float]:
    """抽取回答里的数值。先剥离日期与列表编号，避免它们被当成气象数值。"""
    nums = []
    for m in _NUM_RE.findall(strip_noise(answer or "")):
        try:
            v = float(m)
        except ValueError:
            continue
        if abs(v) >= _MIN_ABS:
            nums.append(v)
    return nums


def extract_dates(text: str, default_year: int | None = None) -> set[str]:
    """抽取文本中的日期，统一成 YYYY-MM-DD。

    没有年份的（如「9月20日」）用 default_year 补全——中文回答里省略年份很常见。
    """
    import datetime as _dt
    year_default = default_year or _dt.date.today().year
    src = text or ""
    out = set()
    for m in _ISO_DATE_RE.finditer(src):
        try:
            out.add(_dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat())
        except ValueError:
            continue
    for m in _CN_MD_RE.finditer(src):
        try:
            out.add(_dt.date(year_default, int(m.group(1)), int(m.group(2))).isoformat())
        except ValueError:
            continue
    return out


def verify_dates(answer: str, trace: list[dict], question: str = "") -> dict:
    """校验回答里出现的日期是否有依据。

    **为什么数值校验防不住这个**
    「获取时间 2025-06-18」被数值抽取拆成 2025 / -06 / -18 三个数，
    而 -6、-18 很可能在气象数据里恰好存在（气温、风速），于是被误判为「有出处」。
    日期幻觉往往看起来非常合理，必须单独作为一类校验。

    合法日期来源：今天、用户问题中提到的日期、工具调用参数与返回里的日期。
    """
    import datetime as _dt

    today = _dt.date.today().isoformat()
    allowed = {today} | extract_dates(question)
    for entry in trace or []:
        allowed |= extract_dates(json.dumps(entry.get("args") or {}, ensure_ascii=False))
        allowed |= extract_dates(json.dumps(entry.get("result") or {}, ensure_ascii=False))

    found = extract_dates(answer)
    bad = sorted(d for d in found if d not in allowed)
    return {
        "dates_in_answer": sorted(found),
        "ungrounded_dates": bad,
        "verdict": "pass" if not bad else "warn",
    }


def verify(answer: str, trace: list[dict], question: str = "") -> dict:
    """校验回答里的数值与日期是否都有出处。

    Returns:
        {
          "grounded_rate": 归因率 0~1,
          "total": 回答中的数值总数,
          "grounded": 有出处的数量,
          "ungrounded": [未归因的数值],   ← 重点看这些
          "dates": {...},                 ← 日期校验结果
          "verdict": "pass" / "warn"
        }
    """
    dates = verify_dates(answer, trace, question)
    ev = evidence_set(trace)
    if not ev:
        return {"grounded_rate": None, "total": 0, "grounded": 0,
                "ungrounded": [], "dates": dates, "verdict": "unknown",
                "note": "本轮没有工具调用，无法校验——若回答里出现气象数值，即为凭空捏造"}

    expanded = _expand(ev)
    nums = extract_answer_numbers(answer)
    if not nums:
        return {"grounded_rate": 1.0, "total": 0, "grounded": 0,
                "ungrounded": [], "dates": dates,
                "verdict": "warn" if dates["ungrounded_dates"] else "pass"}

    ungrounded, grounded = [], 0
    for v in nums:
        if _grounded(v, expanded):
            grounded += 1
        else:
            ungrounded.append(v)

    rate = grounded / len(nums)
    ok = rate >= 0.9 and not dates["ungrounded_dates"]
    return {
        "grounded_rate": round(rate, 3),
        "total": len(nums),
        "grounded": grounded,
        "ungrounded": sorted(set(ungrounded), reverse=True)[:12],
        "dates": dates,
        "verdict": "pass" if ok else "warn",
    }


def report_markdown(result: dict) -> str:
    """把校验结果渲染成一行可读报告，供 CLI / 前端展示。"""
    rate = result.get("grounded_rate")
    if rate is None:
        return "数值归因：无法校验（本轮无工具调用）"
    pct = f"{rate * 100:.0f}%"
    line = f"数值归因：{pct}（{result['grounded']}/{result['total']} 个数字可追溯到工具结果）"
    if result["ungrounded"]:
        vals = ", ".join(str(v) for v in result["ungrounded"][:6])
        line += f" ⚠ 未归因数值：{vals}"
    bad_dates = (result.get("dates") or {}).get("ungrounded_dates") or []
    if bad_dates:
        line += f" ⚠ 无依据日期：{', '.join(bad_dates)}"
    return line


if __name__ == "__main__":
    # 自测：构造一份轨迹与回答
    demo_trace = [{"name": "calc_pv_output", "args": {"lat": 30.27},
                   "result": {"summary": {"理论发电量_kwh": 272.4, "峰值功率_kw": 49.7},
                              "segments": {"午后(12-18)": {"合计": 179.4}}}}]
    demo_answer = "理论发电量 272.4 kWh，峰值 49.7 kW，午后合计 179.4 kWh，风速 5 m/s，误差 3.3"
    print(json.dumps(verify(demo_answer, demo_trace), ensure_ascii=False, indent=2))
    print(report_markdown(verify(demo_answer, demo_trace)))
