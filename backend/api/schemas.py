# -*- coding: utf-8 -*-
"""API 契约：Pydantic 模型。

**为什么契约要单独成文件**
前端照着这里写接口调用，后端改字段时编译器/校验器会立刻报错，而不是等到
运行时前端拿到 undefined 才发现。前后端分离的项目里，**契约文件是两边唯一
的共识来源**——把它从路由代码里抽出来单独维护，是为了让它足够显眼。

**为什么每个模型都带 example**
`/docs` 里的请求示例是直接读这里的配置生成的。没有示例时，调用方只看到
字段名和类型，不知道该填什么（比如 question 该写多长、mode 该选哪个）。
补上示例后，打开文档就能直接点「Try it out」发请求。
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "question": "余杭区明天适合安排光伏板清洗吗？",
            "mode": "single",
            "stream": True,
        }
    })

    question: str = Field(
        ..., min_length=1, max_length=2000,
        description="用户的自然语言问题。用日常说法即可，不需要特定格式。"
                    "例如「余杭区明天适合安排光伏板清洗吗？」「杭州未来三天天气怎么样？」")
    session_id: str | None = Field(
        None,
        description="会话 ID，用于多轮对话。**首次提问不传**，接口会返回一个；"
                    "后续追问带上它，Agent 才能理解「那后天呢？」这类省略主语的问句。")
    mode: Literal["single", "multi"] = Field(
        "single",
        description="`single` = 单 Agent（默认，快，覆盖绝大多数问题）；"
                    "`multi` = 多智能体协同（数据与知识角色并行检索 → 交叉分析 → 汇总，"
                    "更全面但慢约 30%）")
    stream: bool = Field(
        True,
        description="`true` = SSE 流式返回（推荐，能看到执行过程）；"
                    "`false` = 一次性返回完整 JSON（适合脚本调用）")


class TraceEntry(BaseModel):
    round: int = Field(..., description="第几轮工具调用")
    name: str = Field(..., description="工具名")
    args: dict = Field(..., description="调用参数")
    result: dict | None = Field(None, description="工具返回结果")


class DateCheck(BaseModel):
    dates_in_answer: list[str] = Field([], description="回答中出现的日期")
    ungrounded_dates: list[str] = Field([], description="无依据的日期（工具未提供过）")
    verdict: str = Field("unknown", description="pass / warn")


class Verification(BaseModel):
    """数值归因校验结果——本项目防幻觉机制的核心输出。"""
    grounded_rate: float | None = Field(
        None, description="归因率 0~1。回答中的数值能在工具返回结果里找到出处的比例")
    total: int = Field(0, description="回答中检出的数值总数")
    grounded: int = Field(0, description="其中有出处的数量")
    ungrounded: list[float] = Field([], description="未找到出处的数值（重点看这个）")
    dates: DateCheck | None = Field(None, description="日期校验结果")
    verdict: str = Field("unknown", description="pass = 通过；warn = 存在未归因数值")
    note: str | None = Field(None, description="补充说明")


class ChatResponse(BaseModel):
    """非流式返回。流式模式下，同样结构的字段通过 SSE 事件逐个送达。"""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "session_id": "a1b2c3d4e5f6a7b8",
            "question": "余杭区明天适合安排光伏板清洗吗？",
            "answer": "余杭区明天（2026-09-20）**不适宜安排光伏板清洗**，因为……",
            "rounds": 2,
            "mode": "single",
        }
    })

    session_id: str = Field(..., description="会话 ID，追问时带上它")
    question: str = Field(..., description="原始问题")
    answer: str = Field(..., description="Agent 生成的最终回答")
    trace: list[TraceEntry] = Field(
        [], description="工具调用轨迹（每轮调了什么工具、传了什么参数、返回了什么）")
    verification: Verification | None = Field(
        None, description="数值归因校验报告——回答里的数字是否都有出处")
    rounds: int = Field(0, description="工具调用轮次")
    mode: str = Field("single", description="实际使用的模式")


class KbSearchRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {"query": "多少度算高温", "mode": "auto", "top_k": 6}
    })

    query: str = Field(..., min_length=1, max_length=500,
                       description="检索问题，自然语言即可")
    categories: list[str] | None = Field(
        None, description="按分类过滤，不传则全库检索。可选值见接口下方枚举")
    mode: Literal["auto", "bm25", "hybrid"] = Field(
        "auto",
        description="检索模式。`auto` = 有向量索引就走混合检索（推荐）；"
                    "`bm25` = 强制只用关键词检索（用于对比）；"
                    "`hybrid` = 强制混合检索")
    top_k: int = Field(6, ge=1, le=20, description="返回条数 1~20")


class KbHit(BaseModel):
    id: str | None = Field(None, description="知识块 ID（源文件-序号）")
    text: str | None = Field(None, description="知识块正文")
    category: str | None = Field(None, description="分类：术语/灾害标准/行业规则-能源 等")
    source: str | None = Field(None, description="来源文件")
    score: float | None = Field(None, description="RRF 融合得分")


class KbSearchResponse(BaseModel):
    """检索调试台用：除结果外还返回两路各自排名，便于对比混合检索的效果。

    `paths` 字段是本接口区别于普通检索接口的关键——它把 BM25 与向量两路
    各自的原始排名都返回，让「混合检索为什么优于单路」成为可见的事实而非论断。
    """
    query: str = Field(..., description="原始查询")
    method: str = Field(..., description="实际使用的检索方式：hybrid / bm25")
    total_candidates: int = Field(0, description="融合前的候选数量")
    hits: list[KbHit] = Field([], description="RRF 融合后的最终结果")
    paths: dict | None = Field(
        None, description="两路原始排名：{bm25: [...], vector: [...]}，用于对比")


class HealthResponse(BaseModel):
    status: str = Field(..., description="服务状态")
    llm_provider: str = Field(..., description="当前大模型供应商")
    llm_model: str = Field(..., description="当前模型名")
    llm_mock: bool = Field(..., description="是否处于演示模式（不调真实模型）")
    tools_count: int = Field(..., description="已注册工具数量")
    knowledge_base: dict = Field(..., description="知识库统计：块数、向量索引是否可用、分类分布")
