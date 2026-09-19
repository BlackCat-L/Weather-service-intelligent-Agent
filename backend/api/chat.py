# -*- coding: utf-8 -*-
"""对话接口：SSE 流式 + JSON 双模式。

**为什么用 SSE 而不是 WebSocket**
Agent 回答是单向流（服务端 → 客户端），提问走普通 POST 就够了。SSE 是纯 HTTP、
反向代理友好、浏览器自带断线重连；WebSocket 需要 Upgrade 握手、要自己实现
重连逻辑，在本场景是纯粹的复杂度负担。

（前一项目的经验：按业务特征选通道——一次性请求走 HTTP、流式单向走 SSE、
双向实时同步才上 WebSocket。不为「高级」而引入复杂度。）

**同步 Agent 如何在异步框架里做流式推送**
Agent 循环内部是同步的 `requests`，跑在线程池里。事件通过
`loop.call_soon_threadsafe` 投递进 `asyncio.Queue`，再由生成器消费、
以 SSE 格式推给前端。这是「同步代码接入异步流式」的标准做法——
比把整个 Agent 改成 async 侵入性小得多。
"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..services import qa_service
from .schemas import ChatRequest, ChatResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["对话"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # nginx 默认会缓冲响应，导致流式变成一次性吐出。必须显式关闭缓冲
    "X-Accel-Buffering": "no",
}

_SSE_DOC = """
以 **Server-Sent Events** 流式返回 Agent 的执行过程与最终回答。

> 浏览器端请用 `fetch` + `ReadableStream` 解析（原生 `EventSource` 不支持 POST 请求体）。
> 项目前端的实现见 `frontend/src/api/client.ts`。

### 事件类型（按发生顺序）

| event | 含义 | data 示例 |
|---|---|---|
| `start` | 请求已接收，开始处理 | `{"question":"…","mode":"single"}` |
| `stage` | 多智能体模式的阶段提示 | `{"stage":"阶段 1/3：数据检索与规则检索（并行）"}` |
| `tool_calls` | **模型决定调用哪些工具** | `{"round":1,"calls":[{"name":"geocode_place","args":"{…}"}]}` |
| `tool_result` | 工具执行完成 | `{"round":1,"name":"geocode_place","result":{…}}` |
| `verification` | 数值归因校验结果 | `{"grounded_rate":1.0,"total":16,"grounded":16}` |
| `done` | 最终回答 | `{"session_id":"…","answer":"…","rounds":2}` |
| `error` | 执行出错 | `{"message":"…"}` |

### 为什么要暴露 `tool_calls` / `tool_result`

这两个事件是本接口的价值所在——它们让「模型在自主决定调什么工具」这件事
**可见**。前端据此渲染右侧的执行面板，用户能实时看到 Agent 在查什么、拿到了什么。
纯文本流式返回做不到这一点。
"""


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post(
    "/chat/stream",
    summary="对话 · SSE 流式（推荐）",
    description=_SSE_DOC,
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "SSE 事件流",
            "content": {"text/event-stream": {"example":
                "event: start\n"
                "data: {\"question\":\"余杭区明天适合安排光伏板清洗吗？\",\"mode\":\"single\"}\n\n"
                "event: tool_calls\n"
                "data: {\"round\":1,\"calls\":[{\"name\":\"geocode_place\",\"args\":\"{\\\"place\\\":\\\"余杭区\\\"}\"}]}\n\n"
                "event: done\n"
                "data: {\"session_id\":\"a1b2c3\",\"answer\":\"…\",\"rounds\":2}\n\n"}},
        },
    },
)
async def chat_stream(req: ChatRequest):
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(kind: str, payload: dict):
        loop.call_soon_threadsafe(queue.put_nowait, (kind, payload))

    def work():
        try:
            result = qa_service.answer(req.question, req.session_id, req.mode,
                                       on_event=emit)
            loop.call_soon_threadsafe(queue.put_nowait, ("__done__", result))
        except Exception as e:                      # noqa: BLE001
            log.exception("问答执行失败")
            loop.call_soon_threadsafe(queue.put_nowait, ("__error__", str(e)))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, ("__end__", None))

    loop.run_in_executor(None, work)

    async def gen():
        # 先发一个 meta 事件，让前端立刻拿到会话信息、不用等 Agent 跑完
        yield _sse("start", {"question": req.question, "mode": req.mode})
        while True:
            kind, payload = await queue.get()
            if kind == "__end__":
                break
            if kind == "__error__":
                yield _sse("error", {"message": payload})
                break
            if kind == "__done__":
                yield _sse("verification", payload.get("verification") or {})
                yield _sse("done", {
                    "session_id": payload["session_id"],
                    "answer": payload["answer"],
                    "rounds": payload.get("rounds", 0),
                    "mode": payload.get("mode", req.mode),
                })
                continue
            yield _sse(kind, payload)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="对话 · 一次性 JSON",
    description="""
与 `/api/chat/stream` 功能完全相同，只是**一次性返回**而非流式。

**什么时候用这个而不是流式接口：**

- 脚本调用或批量评估（不需要过程，只要结果）
- 不方便处理 SSE 的客户端（小程序 / APP 里解析事件流比较麻烦）
- 调试时想直接看到完整 JSON 结构

返回里包含 `trace`（工具调用轨迹）与 `verification`（数值归因校验），
所以即使是一次性接口，也保留了完整的可观测信息。
""",
)
async def chat(req: ChatRequest):
    try:
        # 同步阻塞调用放到线程池，避免堵住事件循环
        result = await asyncio.to_thread(
            qa_service.answer, req.question, req.session_id, req.mode)
    except Exception as e:                          # noqa: BLE001
        log.exception("问答执行失败")
        raise HTTPException(status_code=500, detail=f"问答执行失败：{e}") from e
    return ChatResponse(**result)
