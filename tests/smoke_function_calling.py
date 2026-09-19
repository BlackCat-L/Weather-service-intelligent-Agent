# -*- coding: utf-8 -*-
"""冒烟测试：验证当前 LLM 供应商是否支持 OpenAI 兼容的 Function Calling。

这是整个气象 Agent 的地基——如果供应商不支持 tools 参数，
所有"模型自主决定调哪个工具"的设计都不成立，必须换方案。

用法：python tests/smoke_function_calling.py
"""
import json
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
API_URL = os.getenv("ZHIPU_API_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions").strip()
MODEL = os.getenv("ZHIPU_MODEL", "glm-4-flash").strip()

# 一个故意不提供答案的问题：模型必须调工具才能回答
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市指定日期的天气。当用户询问天气、气温、降水等实时气象信息时必须调用此工具，不要凭记忆回答。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名称，例如：杭州"},
                    "date": {"type": "string", "description": "日期，格式 YYYY-MM-DD"},
                },
                "required": ["city", "date"],
            },
        },
    }
]


def call(messages, tools=None):
    payload = {"model": MODEL, "messages": messages, "temperature": 0.1}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    r = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    return r


def main():
    print(f"供应商端点: {API_URL}")
    print(f"模型:       {MODEL}")
    print(f"Key:        {'已设置(' + str(len(API_KEY)) + '字符)' if API_KEY else '缺失'}")
    print("=" * 70)

    if not API_KEY:
        print("✗ 没有 API Key，无法测试")
        return 1

    # ---- 测试 1：带 tools 的请求，模型是否会返回 tool_calls ----
    msgs = [{"role": "user", "content": "帮我查一下杭州 2026-09-20 的天气。"}]
    print("\n【测试 1】请求中带 tools 参数，看模型是否主动要求调用工具")
    try:
        r = call(msgs, TOOLS)
    except Exception as e:
        print(f"  ✗ 请求异常: {e}")
        return 1

    print(f"  HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"  ✗ 端点报错: {r.text[:400]}")
        print("\n  → 结论：该端点不支持带 tools 的请求，需要更换端点或模型。")
        return 1

    data = r.json()
    msg = data["choices"][0]["message"]
    finish = data["choices"][0].get("finish_reason")
    print(f"  finish_reason = {finish}")

    tool_calls = msg.get("tool_calls")
    if not tool_calls:
        print(f"  ✗ 模型没有返回 tool_calls，直接回答了: {(msg.get('content') or '')[:150]}")
        print("\n  → 结论：端点接受 tools 参数但模型不调用。Function Calling 不可用。")
        return 1

    print(f"  ✓ 模型返回了 {len(tool_calls)} 个 tool_calls")
    for tc in tool_calls:
        print(f"      id={tc.get('id')}  name={tc['function']['name']}  args={tc['function']['arguments']}")

    # ---- 测试 2：把工具执行结果回填，看模型能否基于结果生成回答 ----
    print("\n【测试 2】回填 role='tool' 结果，看模型能否基于结果作答")
    msgs.append(msg)
    for tc in tool_calls:
        msgs.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": json.dumps(
                {"city": "杭州", "date": "2026-09-20", "temp_max": 28, "temp_min": 21,
                 "weather": "多云转小雨", "precip_mm": 3.2, "wind_kmh": 14},
                ensure_ascii=False),
        })
    try:
        r2 = call(msgs)
    except Exception as e:
        print(f"  ✗ 请求异常: {e}")
        return 1

    if r2.status_code != 200:
        print(f"  ✗ 回填 tool 结果后报错: {r2.text[:400]}")
        print("\n  → 结论：支持返回 tool_calls，但不接受 role='tool' 消息，需要改用其它回填方式。")
        return 1

    content = r2.json()["choices"][0]["message"].get("content") or ""
    print(f"  ✓ 模型基于工具结果作答：\n     {content[:250]}")

    if "28" in content or "3.2" in content or "小雨" in content:
        print("\n  ✓ 回答中引用了工具返回的真实数据")
    else:
        print("\n  ⚠ 回答里似乎没引用工具给的数据，需检查提示词")

    print("\n" + "=" * 70)
    print("✓ 结论：Function Calling 完全可用，架构按原计划推进。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
