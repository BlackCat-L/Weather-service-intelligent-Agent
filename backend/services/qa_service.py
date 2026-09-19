# -*- coding: utf-8 -*-
"""问答服务：把「Agent 执行 + 校验 + 会话管理」编排在一起。

**为什么要有服务层，而不是让路由直接调 agent？**
路由层只该做协议转换（HTTP → 函数调用）。如果把「跑哪个模式的 Agent、
跑完要校验什么、会话怎么存」都写在路由里，将来换一个入口（CLI、定时任务、
消息队列消费者）就得把这些逻辑抄一遍。

服务层是**唯一的业务真相来源**：CLI 和 HTTP 接口都调它，行为必然一致。
本项目的 `ask.py`（CLI）与 `/api/chat`（HTTP）就是共用这一层。
"""
import logging

from .. import checks
from ..agent.loop import run as run_single
from ..agent.orchestrator import run_multi
from ..prompts import system_prompt
from ..tools import build_registry
from .session_store import Session, get_store

log = logging.getLogger(__name__)

# 工具注册表是只读的，构建一次全局复用（构建时会加载知识库，不能每请求一次）
_registry = None


def registry():
    global _registry
    if _registry is None:
        _registry = build_registry()
        log.info("工具注册表已构建：%d 个工具", len(_registry))
    return _registry


def answer(question: str, session_id: str | None = None, mode: str = "single",
           on_event=None) -> dict:
    """执行一次问答。

    Args:
        question:   用户问题
        session_id: 会话 ID，用于多轮对话
        mode:       single=单 Agent / multi=多智能体协同
        on_event:   事件回调，用于 SSE 推送执行过程
    Returns:
        {session_id, question, answer, trace, verification, rounds, mode}
    """
    store = get_store()
    session: Session = store.get(session_id)

    reg = registry()
    # 两种模式都必须传会话历史。多智能体模式下这点尤其容易漏——
    # 它的每个角色都是独立 Agent 循环，不共享记忆，漏传就表现为
    # 「追问『那后天呢？』时凭空编出一个城市」。
    if mode == "multi":
        result = run_multi(question, reg, history=session.history, on_event=on_event)
    else:
        result = run_single(question, reg, system_prompt(),
                            history=session.history, on_event=on_event)

    ans = result.get("answer") or ""
    trace = result.get("trace") or []
    verification = checks.verify(ans, trace, question=question)

    store.append_turn(session, question, ans, trace, verification, mode)

    return {
        "session_id": session.id,
        "question": question,
        "answer": ans,
        "trace": trace,
        "verification": verification,
        "rounds": result.get("rounds", 0),
        "mode": mode,
    }
