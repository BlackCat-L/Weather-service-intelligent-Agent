# -*- coding: utf-8 -*-
"""知识库检索工具：把领域知识接入 Agent 的工具层。

**为什么知识库要作为一个「工具」，而不是像前一项目那样直接拼进上下文？**
前一项目是单一的知识问答场景，每次必检索，直接拼进提示词最简单。
但气象 Agent 是**多工具协作**场景——用户问「明天适不适合洗光伏板」时：
    · 需要查知识库（清洗与降水的关系、作业风速门槛）
    · 但不该去查「明天天气」（那是气象工具的事）
让模型自己决定「这次要不要查知识库」，比无条件拼进去更准确，也更省上下文。

**这体现了 Function Calling 架构的扩展性**：把知识库从一个「固定环节」
变成「一个可选工具」，加进来零成本，不影响任何已有逻辑。
"""
import logging

from ..kb.hybrid import get_kb

log = logging.getLogger(__name__)


def kb_search(query: str, categories: list[str] | None = None, top_k: int = 5) -> dict:
    """在气象知识库中检索相关条文。

    **重要的兜底设计：分类过滤是优化项，不是硬约束。**
    实测踩过的坑：模型给「户外作业」问题选了 `行业规则-交通` 这个错误的分类，
    被过滤条件卡住检索不到内容，于是不断换措辞重试，一次问答调了 7 次知识库
    都没收敛。所以这里加了一道兜底——过滤后结果太少就自动去掉过滤重试一次，
    让模型第一次调用就能拿到可用结果，而不是陷入检索循环。
    """
    kb = get_kb()
    if not kb.ready:
        return {
            "error": "知识库索引尚未构建",
            "hint": "请先运行 python scripts/build_index.py，或直接用你的通用知识回答并说明无知识库支撑",
        }

    result = kb.search(query, top_k=top_k, categories=categories, neighbors=True)
    fell_back = False

    # 兜底：带分类过滤时结果过少，说明分类可能选错，去掉过滤重试
    if categories and len(result["hits"]) < 2:
        unfiltered = kb.search(query, top_k=top_k, categories=None, neighbors=True)
        if len(unfiltered["hits"]) > len(result["hits"]):
            result = unfiltered
            fell_back = True

    if not result["hits"]:
        return {"query": query, "hits": [], "note": "知识库中没有找到相关内容"}

    # 精简返回：模型只需要正文与出处，不需要分数与内部 id
    hits = [{"category": h.get("category"), "text": h.get("text"),
             "source": h.get("source")} for h in result["hits"]]
    out = {
        "query": query,
        "method": result["method"],          # hybrid / bm25，便于排查检索质量问题
        "hits": hits,
    }
    if fell_back:
        out["note"] = ("指定的分类下结果过少，已自动改为全库检索。"
                       "如果这些结果与问题无关，说明分类选错了——"
                       "请直接使用本结果作答，不要再用其它分类重复检索。")
    return out


def register(reg):
    reg.tool(
        name="search_weather_knowledge",
        description=(
            "检索气象专业知识库，内容包含：气象要素与术语释义、气象灾害分级标准"
            "（暴雨/大风/高温/霜冻等阈值）、**面向能源/交通/农业/户外作业的行业影响规则**、"
            "预报产品的局限说明。\n"
            "**以下情况必须调用本工具，不要凭记忆作答**：\n"
            "· 需要判定某天气条件是否达到灾害或预警标准时（高温/暴雨/大风/霜冻等阈值）\n"
            "· 需要给出某个行业的作业建议、安全事项或风险门槛时——包括"
            "「雷暴天户外作业注意什么」「光伏板什么时候该清洗」「无人机抗风限制」"
            "「农业防冻措施」等，即使是常识性问题也要检索\n"
            "· 用户询问气象概念或原理时（体感温度、露点、为什么预报会不准）\n"
            "· 需要解释预报数据为何有误差、格点与站点差异等问题时\n"
            "本工具返回的是知识条文，不是实时数据——实时气象数据请用预报类工具。\n"
            "**注意：标准与规定有版本、有行业差异，凭记忆容易出错且无法追溯。"
            "检索一次的成本远低于答错一次。**"
        ),
        parameters={
            "query": {"type": "string", "description": "检索问题，用自然语言描述即可，例如「光伏组件清洗对降水的要求」", "required": True},
            "categories": {
                "type": "array",
                "description": ("可选的分类过滤。**不确定内容属于哪个分类时不要传**——"
                                "传错分类会被过滤掉，反而检索不到；工具在分类结果过少时"
                                "会自动改为全库检索，但直接不传更省一轮"),
                "enum": ["术语", "灾害标准", "行业规则-能源", "行业规则-交通",
                         "行业规则-农业", "行业规则-户外", "数据说明"],
            },
            "top_k": {"type": "integer", "description": "返回条数，默认 5"},
        },
    )(kb_search)
