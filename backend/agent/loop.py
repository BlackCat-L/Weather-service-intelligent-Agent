# -*- coding: utf-8 -*-
"""Function Calling 主循环 —— 本项目的技术核心。

流程：
    用户提问 → 模型决定调哪些工具 → 并行执行 → 结果回填 → 模型再决定
    → …… → 直到模型不再要求调工具，输出最终回答

**这是与前一项目的分水岭**：那边的模型只能基于后端预先拼好的上下文作答
（"知识问答"），这边模型可以主动要数据、自己决定查什么怎么算
（"数据驱动决策"）。

几个容易踩的坑，这里都处理了：
- 模型返回的 assistant 消息可能带供应商私有字段，直接回传会报错 → 只保留协议字段
- 同一轮可能返回多个 tool_calls，它们之间无依赖 → 并行执行而非串行
- tool_calls 的 arguments 是 JSON 字符串，可能为空或格式错误 → 容错解析
- 模型可能陷入无限调工具 → 用 max_rounds 兜底
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from ..infra import config, llm

log = logging.getLogger(__name__)

# 同一工具累计调用超过这个次数，就注入一条「你在绕圈」的提醒（见下方注释）
_LOOP_HINT_AFTER = 3


def _exec_one(registry, tc: dict):
    """执行单个工具调用，返回 (工具名, 参数, 结果)。"""
    fn = tc.get("function") or {}
    name = fn.get("name")
    raw = fn.get("arguments") or "{}"
    try:
        args = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        args = {}
    if not isinstance(args, dict):
        args = {}
    return name, args, registry.call(name, args)


def run(question: str, registry, system_prompt: str,
        history: list[dict] | None = None,
        max_rounds: int | None = None,
        on_event=None) -> dict:
    """执行一次完整问答。

    Args:
        question:      用户问题
        registry:      ToolRegistry 实例
        system_prompt: 系统提示词
        history:       多轮对话历史（不含本轮问题）
        max_rounds:    最大工具调用轮次，防止模型陷入死循环
        on_event:      可选回调 on_event(type, payload)，用于把执行过程实时推给前端；
                       事件类型：tool_calls / tool_result / answer_start
    Returns:
        {"answer": str, "trace": [...], "messages": [...], "rounds": int}
    """
    max_rounds = max_rounds or config.max_tool_rounds()

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})

    trace: list[dict] = []
    emit = on_event or (lambda *a, **k: None)

    for round_no in range(1, max_rounds + 1):
        msg = llm.chat(messages, tools=registry.schemas())
        tool_calls = msg.get("tool_calls")

        # 模型认为信息够了，给出最终回答
        if not tool_calls:
            emit("answer_start", {"rounds": round_no - 1})
            return {
                "answer": msg.get("content") or "",
                "trace": trace,
                "messages": messages,
                "rounds": round_no - 1,
            }

        messages.append(msg)
        emit("tool_calls", {
            "round": round_no,
            "calls": [{"name": (tc.get("function") or {}).get("name"),
                       "args": (tc.get("function") or {}).get("arguments")} for tc in tool_calls],
        })

        # 同轮多个工具调用之间没有依赖关系，并行执行
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(tool_calls)))) as pool:
            futures = [pool.submit(_exec_one, registry, tc) for tc in tool_calls]
            results = [f.result() for f in futures]

        for tc, (name, args, result) in zip(tool_calls, results):
            entry = {"round": round_no, "name": name, "args": args, "result": result}
            trace.append(entry)
            emit("tool_result", entry)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id"),
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

            # ---- 重复调用检测（防检索循环）----
            #
            # 实测踩过的坑：模型给「户外作业」问题选错了知识库分类，
            # 被过滤条件卡住检索不到，于是不断换措辞重试——一次问答调了
            # 7 次知识库、耗尽轮次上限才停。单次调用都「成功」了（返回了结果），
            # 所以错误被静默吞掉，只在耗时上体现。
            #
            # 对策：同一工具累计调用超过阈值就注入一条提醒，让模型基于已有结果作答。
            # 这是纯提示层面的干预——不阻断调用（万一真的需要多次查不同参数），
            # 只把「你在绕圈」这个事实告诉模型。
            count = sum(1 for t in trace if t["name"] == name)
            if count == _LOOP_HINT_AFTER:
                log.warning("工具 %s 已被调用 %s 次，可能是检索循环", name, count)
                messages.append({
                    "role": "system",
                    "content": (f"注意：你已经调用 {name} {count} 次了。"
                                "如果这些调用的目的相同，说明方向不对——"
                                "请基于**已经拿到的结果**作答，不要再重复检索。"
                                "如果确实还需要别的信息，请说明缺什么，或换个工具。"),
                })

    log.warning("达到最大工具调用轮次 %s，强制结束", max_rounds)
    return {
        "answer": "（已达到最大工具调用轮次，未能得出最终结论。请把问题拆细一点再问）",
        "trace": trace,
        "messages": messages,
        "rounds": max_rounds,
    }
