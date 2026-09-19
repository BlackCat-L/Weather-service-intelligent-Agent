# -*- coding: utf-8 -*-
"""大模型调用层：多供应商（OpenAI 兼容协议），支持 Function Calling 与流式输出。

与前一项目的关键区别：这里的 chat() 支持 tools 参数——模型不再只能被动接收
后端拼好的上下文，而是可以主动决定「我要调用哪个工具、传什么参数」。
"""
import json
import logging
import os
import time

import requests

from . import config

log = logging.getLogger(__name__)


class LlmNotConfigured(Exception):
    """还没填 API Key"""


# 限流与临时故障的重试策略。
# 真实环境里 429（配额/限流）与 5xx（服务波动）几乎必然出现，批量任务尤其容易撞上——
# 前一项目就踩过「后台批处理把线上服务配额挤干」的坑。不做退避重试的话，
# 一次限流就会让整批任务前功尽弃。
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRY = 3


def _post_with_retry(url: str, headers: dict, payload: dict, timeout: int,
                     stream: bool = False) -> requests.Response:
    last_err = None
    for attempt in range(_MAX_RETRY + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload,
                                 timeout=timeout, stream=stream)
        except requests.RequestException as e:
            last_err = e
            if attempt >= _MAX_RETRY:
                break
            time.sleep(2 ** attempt)
            continue

        if resp.status_code in _RETRY_STATUS and attempt < _MAX_RETRY:
            wait = 2 ** attempt
            log.warning("大模型返回 %s，%s 秒后重试（第 %s/%s 次）",
                        resp.status_code, wait, attempt + 1, _MAX_RETRY)
            time.sleep(wait)
            continue
        return resp

    raise RuntimeError(f"请求大模型失败（已重试 {_MAX_RETRY} 次）：{last_err}")


def _resolve(model: str | None, url: str | None) -> tuple[str, str, str]:
    p = config.provider()
    key = p["api_key"]
    if not key:
        raise LlmNotConfigured(
            f"未配置 {p['name'].upper()} 的 API Key：请在 .env 里填 {p['name'].upper()}_API_KEY 后重试")
    return key, model or p["model"], url or p["url"]


def _mock_message(messages, tools):
    """演示模式：不调真实模型，返回一个假的工具调用或文本，用于无 Key 联调。"""
    if tools and not any(m.get("role") == "tool" for m in messages):
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "mock_call_1",
                "type": "function",
                "function": {"name": tools[0]["function"]["name"], "arguments": "{}"},
            }],
        }
    return {"role": "assistant", "content": "（演示模式）已收到工具返回的数据。请填入真实 API Key 以获取完整回答。"}


def chat(messages: list[dict], tools: list[dict] | None = None,
         temperature: float | None = None, model: str | None = None,
         tool_choice: str | None = None) -> dict:
    """非流式调用。返回 message dict，可能带 tool_calls 字段。

    Args:
        messages: OpenAI 格式的消息列表
        tools:    工具 schema 列表（Function Calling 用），None 表示不启用工具
    Returns:
        {"role": "assistant", "content": str|None, "tool_calls": [...]|None}
    """
    if config.mock():
        return _mock_message(messages, tools)

    key, model, url = _resolve(model, None)
    payload = {
        "model": model,
        "messages": messages,
        "temperature": config.temperature() if temperature is None else temperature,
    }
    # 供应商扩展参数（如豆包的 thinking 开关），来自 .env 的 *_EXTRA
    payload.update(config.provider().get("extra") or {})
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"

    resp = _post_with_retry(
        url,
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        payload,
        config.timeout(),
    )

    if resp.status_code != 200:
        raise RuntimeError(f"大模型接口返回 {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    msg = data["choices"][0]["message"]

    # 只保留协议需要的字段，避免把供应商私有字段回传导致报错
    clean = {"role": "assistant", "content": msg.get("content")}
    if msg.get("tool_calls"):
        clean["tool_calls"] = msg["tool_calls"]
    return clean


def stream(messages: list[dict], temperature: float | None = None,
           model: str | None = None):
    """流式调用，逐段 yield 文本。用于最终回答的逐字输出。

    注意：流式下不支持 tool_calls（工具调用阶段用非流式 chat），
    这样分工最简单也最不容易出错。
    """
    if config.mock():
        for ch in "（演示模式）这是流式输出的示例文本。":
            yield ch
        return

    key, model, url = _resolve(model, None)
    payload = {
        "model": model,
        "messages": messages,
        "temperature": config.temperature() if temperature is None else temperature,
        "stream": True,
    }
    payload.update(config.provider().get("extra") or {})
    resp = _post_with_retry(
        url,
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        payload,
        config.timeout(),
        stream=True,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"大模型接口返回 {resp.status_code}: {resp.text[:300]}")

    for raw in resp.iter_lines():
        if not raw:
            continue
        # 显式按 UTF-8 解码：部分供应商 SSE 响应头不带 charset，
        # requests 会按 Latin-1 解码导致中文乱码（前一项目踩过的坑）
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if chunk == "[DONE]":
            break
        try:
            delta = json.loads(chunk)["choices"][0].get("delta") or {}
        except (json.JSONDecodeError, KeyError, IndexError):
            continue
        text = delta.get("content")
        if text:
            yield text
