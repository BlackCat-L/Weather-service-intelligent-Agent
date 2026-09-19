# -*- coding: utf-8 -*-
"""API 契约：Pydantic 模型。

**为什么契约要单独成文件**
前端照着这里写接口调用，后端改字段时编译器/校验器会立刻报错，而不是等到
运行时前端拿到 undefined 才发现。前后端分离的项目里，**契约文件是两边唯一
的共识来源**——把它从路由代码里抽出来单独维护，是为了让它足够显眼。
"""
from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="用户问题")
    session_id: str | None = Field(None, description="会话 ID，不传则新建会话")
    mode: Literal["single", "multi"] = Field(
        "single", description="single=单 Agent（快）；multi=多智能体协同（慢但更全面）")
    stream: bool = Field(True, description="true=SSE 流式返回；false=一次性 JSON")


class ToolCallInfo(BaseModel):
    round: int
    name: str
    args: dict


class TraceEntry(BaseModel):
    round: int
    name: str
    args: dict
    result: dict | None = None


class DateCheck(BaseModel):
    dates_in_answer: list[str] = []
    ungrounded_dates: list[str] = []
    verdict: str = "unknown"


class Verification(BaseModel):
    grounded_rate: float | None = None
    total: int = 0
    grounded: int = 0
    ungrounded: list[float] = []
    dates: DateCheck | None = None
    verdict: str = "unknown"
    note: str | None = None


class ChatResponse(BaseModel):
    """非流式返回。流式模式下，同样结构的字段通过 SSE 事件逐个送达。"""
    session_id: str
    question: str
    answer: str
    trace: list[TraceEntry] = []
    verification: Verification | None = None
    rounds: int = 0
    mode: str = "single"


class KbSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    categories: list[str] | None = None
    mode: Literal["auto", "bm25", "hybrid"] = "auto"
    top_k: int = Field(6, ge=1, le=20)


class KbHit(BaseModel):
    id: str | None = None
    text: str | None = None
    category: str | None = None
    source: str | None = None
    score: float | None = None
    _neighbor_of: str | None = None


class KbSearchResponse(BaseModel):
    """检索调试台用：除结果外还返回两路各自排名，便于对比混合检索的效果。"""
    query: str
    method: str
    total_candidates: int = 0
    hits: list[KbHit] = []
    paths: dict | None = None


class HealthResponse(BaseModel):
    status: str
    llm_provider: str
    llm_model: str
    llm_mock: bool
    tools_count: int
    knowledge_base: dict
