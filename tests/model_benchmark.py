# -*- coding: utf-8 -*-
"""候选大模型对比测试 —— 用**我们真实的工具集**跑，而不是看排行榜。

**为什么要自己测**
模型排行榜衡量的是通用能力（知识、推理、代码），而本项目的核心需求是
「能不能稳定地把工具调对」——这是个很窄但很硬的能力。排行榜第一的模型
未必工具调用最稳，而工具调用不稳，整个 Agent 架构就不成立。

**测什么**（按重要性排序）
1. 连通性与延迟            —— 交互式演示的底线
2. 单工具调用              —— 最基础能力
3. 多工具并行调用          —— 我们真实场景需要（地名解析 + 数据查询可并行）
4. 工具选择克制性          —— 问知识类问题时会不会误调气象工具
5. 指令遵循                —— 是否按格式输出、是否编造数字
6. 多轮工具链              —— 能否在拿到第一轮结果后正确决定下一步

用法：
    python tests/model_benchmark.py                          # 测默认候选
    python tests/model_benchmark.py --models A,B,C           # 指定模型
    python tests/model_benchmark.py --provider doubao
    python tests/model_benchmark.py --probe                  # 只探测账号可用哪些模型
"""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests                                    # noqa: E402
from dotenv import load_dotenv                     # noqa: E402

load_dotenv(ROOT / ".env")

from backend.tools import build_registry           # noqa: E402

# 候选模型。开通新模型后加到这里即可。
DEFAULT_CANDIDATES = [
    "doubao-seed-1-6-flash-250828",
    "doubao-seed-1-6-250615",
    "doubao-seed-1-6-251015",
    "doubao-seed-1-6-flash",
    "doubao-seed-1-6",
    "doubao-seed-character-260628",     # 作为基线：当前在用
]

PROVIDER_ENDPOINTS = {
    "doubao": ("DOUBAO_API_KEY", "DOUBAO_API_URL",
               "https://ark.cn-beijing.volces.com/api/v3/chat/completions"),
    "zhipu": ("ZHIPU_API_KEY", "ZHIPU_API_URL",
              "https://open.bigmodel.cn/api/paas/v4/chat/completions"),
}

C_OK, C_BAD, C_WARN, C_DIM, C_HL, C_END = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[36m", "\033[0m")


def resolve(provider: str):
    key_env, url_env, default_url = PROVIDER_ENDPOINTS[provider]
    return (os.getenv(key_env, "").strip(),
            os.getenv(url_env, "").strip() or default_url)


def call(key, url, model, messages, tools=None, max_tokens=800, timeout=120):
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    # 带上 .env 里配置的供应商扩展参数（如豆包的 thinking 开关），
    # 否则测的是默认配置，而不是我们实际要部署的配置
    try:
        from backend.infra import config as _cfg
        payload.update(_cfg.provider().get("extra") or {})
    except Exception:                                      # noqa: BLE001
        pass
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    t0 = time.time()
    r = requests.post(url, headers={"Authorization": f"Bearer {key}",
                                    "Content-Type": "application/json"},
                      json=payload, timeout=timeout)
    elapsed = time.time() - t0
    if r.status_code != 200:
        try:
            msg = r.json()["error"]["message"]
        except Exception:                                  # noqa: BLE001
            msg = r.text[:160]
        raise RuntimeError(f"HTTP {r.status_code}: {msg}")
    return r.json(), elapsed


# ---------------- 测试用例 ----------------

def t_connect(key, url, model):
    """1. 连通性与延迟"""
    lats = []
    for _ in range(3):
        data, el = call(key, url, model, [{"role": "user", "content": "用一句话说明什么是体感温度。"}],
                        max_tokens=80)
        lats.append(el)
    return {
        "pass": True,
        "latency_avg": round(statistics.mean(lats), 2),
        "latency_max": round(max(lats), 2),
        "detail": f"平均 {statistics.mean(lats):.2f}s（{min(lats):.2f}~{max(lats):.2f}）",
    }


def t_single_tool(key, url, model, tools):
    """2. 单工具调用：明确需要查数据的问题"""
    msgs = [{"role": "user", "content": "帮我查一下杭州 2026-09-20 的天气。"}]
    data, el = call(key, url, model, msgs, tools=tools)
    m = data["choices"][0]["message"]
    tc = m.get("tool_calls")
    if not tc:
        return {"pass": False, "detail": f"未调用工具，直接回答：{(m.get('content') or '')[:60]}", "latency": round(el, 2)}
    fn = tc[0]["function"]
    try:
        args = json.loads(fn["arguments"])
    except Exception:                                      # noqa: BLE001
        args = {}
    ok = fn["name"] == "geocode_place" and args.get("place")
    return {
        "pass": ok,
        "detail": f"{fn['name']}({fn['arguments'][:60]})",
        "latency": round(el, 2),
    }


def t_parallel_tools(key, url, model, tools):
    """3. 并行工具调用：一次返回多个 tool_calls 的能力。
    我们的场景里「地名解析」和「知识库检索」互不依赖，能并行就省一半时间。"""
    msgs = [{
        "role": "user",
        "content": "余杭区明天适合作户外作业吗？同时告诉我高温作业的判定标准是什么。"
                   "如果需要多个工具请一次性全部调用。",
    }]
    data, el = call(key, url, model, msgs, tools=tools)
    tc = data["choices"][0]["message"].get("tool_calls") or []
    names = [c["function"]["name"] for c in tc]
    has_geo = "geocode_place" in names
    has_other = any(n in names for n in ("search_weather_knowledge", "assess_weather_risk",
                                         "get_forecast_daily", "get_forecast_hourly"))
    return {
        "pass": has_geo and has_other,
        "detail": f"返回 {len(tc)} 个调用：{', '.join(names) or '无'}"
                  + ("（未并行）" if len(tc) < 2 else ""),
        "latency": round(el, 2),
    }


def t_tool_restraint(key, url, model, tools):
    """4. 工具选择克制性：这是最容易暴露问题的测试。
    问一个纯知识问题，正确行为是**只**调知识库工具，不该去查实时天气。"""
    msgs = [{"role": "user", "content": "光伏组件的温度一般比气温高多少？为什么夏天发电效率反而可能下降？"}]
    data, el = call(key, url, model, msgs, tools=tools)
    tc = data["choices"][0]["message"].get("tool_calls") or []
    names = [c["function"]["name"] for c in tc]
    weather_tools = {"calc_pv_output", "calc_wind_output", "get_forecast_daily",
                     "get_forecast_hourly", "get_history_daily"}
    misused = [n for n in names if n in weather_tools]
    return {
        "pass": bool(names) and not misused and "search_weather_knowledge" in names,
        "detail": f"调用：{', '.join(names) or '无'}"
                  + (f"  ⚠ 误用气象工具：{', '.join(misused)}" if misused else "")
                  + ("  ⚠ 未查知识库" if "search_weather_knowledge" not in names else ""),
        "latency": round(el, 2),
    }


def t_instruction_following(key, url, model):
    """5. 指令遵循：要求结构化输出且不得出现未经提供的数据。"""
    sys_msg = ("你是气象助手。回答必须：1) 先给结论 2) 关键数据用**加粗** "
               "3) 只使用我提供的数据，绝不可补充其他数值 4) 结尾标注数据来源。\n"
               "供你使用的数据：杭州明天多云，气温 22-28°C，风速 12km/h。")
    data, el = call(key, url, model, [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": "杭州明天天气怎么样？"},
    ], max_tokens=400)
    ans = data["choices"][0]["message"].get("content") or ""
    has_bold = "**" in ans
    has_source = "数据来源" in ans or "来源" in ans
    # 检查是否编造了未提供的数值
    import re
    nums = set(re.findall(r"\d+\.?\d*", ans))
    allowed = {"22", "28", "12", "1", "2", "3", "0"}
    fabricated = sorted(n for n in nums if n not in allowed and len(n) > 1)
    return {
        "pass": has_bold and has_source and not fabricated,
        "detail": f"加粗:{'✓' if has_bold else '✗'} 来源标注:{'✓' if has_source else '✗'} "
                  f"编造数值:{fabricated if fabricated else '无'}",
        "latency": round(el, 2),
    }


def t_multi_turn_chain(key, url, model, tools):
    """6. 多轮工具链：拿到第一轮结果后能否正确决定下一步（Agent 的核心能力）"""
    msgs = [{"role": "user", "content": "余杭区明天适合安排光伏板清洗吗？"}]
    data, el1 = call(key, url, model, msgs, tools=tools)
    tc = data["choices"][0]["message"].get("tool_calls") or []
    if not tc:
        return {"pass": False, "detail": f"第一步就没调工具（{el1:.1f}s）"}

    # 回填一个地名解析结果，看它下一步会不会去调评估工具
    msgs.append(data["choices"][0]["message"])
    for c in tc:
        msgs.append({
            "role": "tool", "tool_call_id": c["id"],
            "content": json.dumps({"found": True, "name": "余杭区", "admin1": "浙江省",
                                   "latitude": 30.2762, "longitude": 119.9746},
                                  ensure_ascii=False),
        })
    data2, el2 = call(key, url, model, msgs, tools=tools)
    tc2 = data2["choices"][0]["message"].get("tool_calls") or []
    names2 = [c["function"]["name"] for c in tc2]
    return {
        "pass": "assess_weather_risk" in names2,
        "detail": f"第一轮 {[c['function']['name'] for c in tc]} → 第二轮 {names2 or '直接作答'}",
        "latency": round(el1 + el2, 2),
    }


TESTS = [
    ("连通与延迟", t_connect, False),
    ("单工具调用", t_single_tool, True),
    ("并行工具调用", t_parallel_tools, True),
    ("工具选择克制", t_tool_restraint, True),
    ("指令遵循", t_instruction_following, False),
    ("多轮工具链", t_multi_turn_chain, True),
]


def probe_models(key, url, models):
    """探测哪些模型当前账号可用"""
    print(f"\n{C_HL}账号可用模型探测{C_END}\n")
    avail = []
    for m in models:
        try:
            call(key, url, m, [{"role": "user", "content": "hi"}], max_tokens=5, timeout=30)
            print(f"  {C_OK}✓{C_END} {m}")
            avail.append(m)
        except RuntimeError as e:
            short = str(e)[:90]
            print(f"  {C_BAD}✗{C_END} {m}  {C_DIM}{short}{C_END}")
    print(f"\n可用：{len(avail)} / {len(models)}")
    return avail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", help="逗号分隔的模型列表")
    ap.add_argument("--provider", default="doubao", choices=list(PROVIDER_ENDPOINTS))
    ap.add_argument("--probe", action="store_true", help="只探测可用模型")
    ap.add_argument("--json", help="把结果写入 JSON 文件")
    args = ap.parse_args()

    key, url = resolve(args.provider)
    if not key:
        print(f"✗ 未配置 {args.provider} 的 API Key")
        return 1

    models = [m.strip() for m in args.models.split(",")] if args.models else DEFAULT_CANDIDATES
    print(f"\n供应商：{C_HL}{args.provider}{C_END}")
    print(f"端点：  {url}")
    print(f"候选：  {len(models)} 个模型")

    if args.probe:
        probe_models(key, url, models)
        return 0

    # 用**真实的工具集**，这样测出的结论直接对应本项目
    registry = build_registry()
    tools = registry.schemas()
    print(f"工具集：{len(tools)} 个（来自项目真实注册表）")

    results = {}
    for model in models:
        print(f"\n{'═' * 76}")
        print(f"{C_HL}{model}{C_END}")
        print("─" * 76)
        results[model] = {}

        # 先探连通性，不通就跳过后续测试，避免刷一屏错误
        try:
            call(key, url, model, [{"role": "user", "content": "hi"}], max_tokens=5, timeout=30)
        except RuntimeError as e:
            print(f"  {C_BAD}✗ 不可用：{str(e)[:110]}{C_END}")
            results[model]["_available"] = False
            continue
        results[model]["_available"] = True

        for name, fn, needs_tools in TESTS:
            try:
                r = fn(key, url, model, tools) if needs_tools else fn(key, url, model)
            except Exception as e:                          # noqa: BLE001
                r = {"pass": False, "detail": f"异常：{str(e)[:80]}"}
            mark = f"{C_OK}✓{C_END}" if r.get("pass") else f"{C_BAD}✗{C_END}"
            print(f"  {mark} {name:12s} {C_DIM}{r.get('detail', '')}{C_END}")
            results[model][name] = r

    # ---------- 汇总 ----------
    print(f"\n{'═' * 76}")
    print("汇总\n")
    header = f"  {'模型':34s} {'通过':>6s} {'平均延迟':>9s}"
    print(header)
    print("  " + "─" * 62)
    rank = []
    for model, r in results.items():
        if not r.get("_available"):
            print(f"  {model:34s} {'不可用':>6s}")
            continue
        scored = [v for k, v in r.items() if k != "_available"]
        passed = sum(1 for v in scored if v.get("pass"))
        lats = [v.get("latency_avg") or v.get("latency") for v in scored
                if v.get("latency_avg") or v.get("latency")]
        avg_lat = statistics.mean(lats) if lats else 0
        rank.append((model, passed, avg_lat))
        print(f"  {model:34s} {passed:>3d}/{len(scored):<2d} {avg_lat:>8.2f}s")

    if rank:
        print(f"\n{C_HL}推荐{C_END}")
        # 先按通过数排，通过数相同再看延迟——工具调用能力是硬门槛，速度是次要项
        for model, passed, lat in sorted(rank, key=lambda x: (-x[1], x[2]))[:3]:
            print(f"  · {model}  通过 {passed} 项，平均 {lat:.2f}s")

    if args.json:
        Path(args.json).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入 {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
