# -*- coding: utf-8 -*-
"""多智能体协同编排。

**与「一个 Agent 多调几个工具」的本质区别**

单 Agent 模式下，工具是它的一双手——所有判断都由同一个模型循环完成。
遇到复合问题（「明天光伏出力怎么样，有什么运行风险，该怎么安排」），
一个 Agent 要同时扮演数据员、分析师和运维顾问，容易顾此失彼。

多 Agent 模式下，每个角色有**独立的提示词、独立的工具白名单、独立的产出**，
最后融合。这带来三个实际好处：
1. **每个环节的质量更可控**——查数据时专注准确性，给建议时专注领域规则
2. **可以并行**——数据获取与知识检索互不依赖，同时跑
3. **可解释**——每个角色的产出单独可见，出问题能定位到是哪一环

**编排形态（DAG，不是流水线）**

    阶段 1（并行）          阶段 2            阶段 3
    ┌──────────────┐
    │ 数据 Agent    │──┐
    │ 调气象工具     │  │
    └──────────────┘  ├──→ ┌───────────┐ ──→ ┌───────────┐
    ┌──────────────┐  │    │ 分析 Agent │     │ 汇总 Agent │
    │ 知识 Agent    │──┘    │ 交叉分析    │     │ 生成回答    │
    │ 查领域规则     │       └───────────┘     └───────────┘
    └──────────────┘

阶段 1 的两个角色互不依赖 → 并行；分析必须等两者都完成 → 串行；汇总在最后。

**为什么不让模型自己规划流程？**
那是更「高级」的做法（LLM 自主分解任务），但成本高、结果不稳定，
对气象服务这种流程相对固定的场景属于过度设计。**固定的编排骨架 + 角色内
由模型自主决定调什么工具**，是准确性、成本与可解释性的平衡点。
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from ..infra import config, llm
from ..prompts import with_today
from .loop import run as run_loop

log = logging.getLogger(__name__)

# ---------- 各角色的提示词 ----------

DATA_AGENT_PROMPT = """你是气象数据工程师，负责为团队提供准确的**事实数据**。

你的职责：
- 把地名转成经纬度，然后查询所需的气象数据
- 需要计算类指标（发电量、出力、风险扫描）时调用对应计算工具
- 只报告工具返回的数据，**绝不推测或补充任何未经工具验证的数值**

输出要求：用紧凑的结构化文本汇报，包含：地点与坐标、时间范围、关键数据
（逐时段）、数据来源与获取时间。不要给建议，不要做分析——那是其他同事的工作。
控制在 400 字以内。"""

KNOWLEDGE_AGENT_PROMPT = """你是气象领域的知识专员，负责从知识库中提供**行业规则与判定标准**。

你的职责：
- 检索知识库，找出与问题相关的：灾害分级标准、行业影响规则、作业门槛
- 明确给出**具体阈值**（如「日降水 10 毫米以上才有清洗效果」「6 级以上风停止高空作业」）
- 如果知识库里没有相关内容，如实说明「知识库中没有找到相关规则」

输出要求：分条列出检索到的规则，每条注明适用的行业场景。
不要获取实时气象数据，不要给出最终建议——那是其他同事的工作。控制在 400 字以内。"""

ANALYST_PROMPT = """你是气象分析师，负责把**数据**与**领域规则**结合起来做判断。

你会收到同事提供的两类材料，**必须分清它们**：
- **数据结果**：某地某时的实测或预报数值（气温、降水、风速、辐照、发电量…）
- **规则结果**：领域规范条文（灾害分级阈值、作业门槛、安全要求…）

⚠️ **最关键的一条：规则里的数值是「判定阈值」，不是该地该时的实测数据。**
规则说「日降水 10 毫米以上才有自然清洗效果」，这个 10mm 是门槛，
**不代表当地明天会下 10mm 的雨**。把阈值当数据去和实测值比对，
会得出「数据存在矛盾」这类**错误结论**——本项目实测踩过这个坑：
分析环节把知识库里的 10mm 阈值和实测的 0.0mm 当成两个打架的数据，
向用户报告了并不存在的「数据可信度不足」。

你的职责：
- 用规则中的**阈值去判定**实测数据是否达标（比较关系），而不是把两者当同类数据比对
- 找出数据中的关键特征：峰值时段、异常值、变化趋势
- 明确指出**哪些结论有充分依据、哪些存在不确定性**
- 如果数据或规则不足以下结论，直接说明缺什么

输出要求：分点给出分析结论，每条都要有数据支撑。不要编造任何数值。
控制在 400 字以内。"""

SUMMARIZER_PROMPT = """你是气象服务顾问，负责把团队的分析结果整理成**给用户的最终回答**。

你会收到数据、规则、分析三部分材料。要求：
- 先给结论（用户最想知道的是「能不能做、什么时候做」）
- 关键数据用**加粗**突出
- 给出具体可执行的建议，包含时间窗口
- 标注数据来源与时效
- **回答中出现的每一个数字，必须在材料中能找到出处**，不得自行补充或推算
- **不要把规则阈值当成实测数据**：规则里的「日降水 10 毫米」是判定门槛，
  不是当地明天的预报值。同一要素出现两个数字时，先判断它们是不是同类东西
  （实测值 vs 阈值），**不要轻易断言「数据矛盾」或「可信度不足」**
- 不要提及「数据 Agent」「分析 Agent」这类内部角色名称，直接给用户结论
- 控制在 300-500 字"""


def _call_agent(name: str, question: str, registry, system_prompt: str,
                context: str = "", history: list[dict] | None = None,
                on_event=None) -> dict:
    """运行一个专职 Agent，返回 {name, answer, trace}。

    history 必须逐角色传入：多智能体模式下每个角色都是**独立的 Agent 循环**，
    它们不共享会话记忆。如果只在汇总环节给历史，前面查数据的角色就看不到
    上下文——实测踩过：追问「那后天呢？」时，数据 Agent 拿不到上一轮的「杭州」，
    于是**自己编了一个城市**去查，答案看起来完全合理，不细看根本发现不了。
    """
    # 必须走 with_today()：角色提示词如果绕过日期注入，汇总环节会编日期
    prompt = with_today(system_prompt, label="时间基准（务必遵守）")
    if context:
        prompt += f"\n\n## 同事提供的材料\n{context}"
    user = question if not context else "请基于上面的材料完成你的工作。原始问题：" + question
    try:
        result = run_loop(user, registry, prompt, history=history, on_event=on_event)
        return {"name": name, "answer": result.get("answer") or "",
                "trace": result.get("trace") or [], "ok": True}
    except Exception as e:
        log.warning("Agent %s 执行失败：%s", name, e)
        return {"name": name, "answer": "", "trace": [], "ok": False, "error": str(e)}


def run_multi(question: str, registry, history: list[dict] | None = None,
              on_event=None, max_workers: int = 2) -> dict:
    """多智能体协同执行。

    Args:
        question: 用户问题
        registry: 完整的工具注册表（编排器负责按角色分配子集）
        history:  会话历史（问题+答案对）。**必须传**，否则追问会失去上下文——
                  例如用户问「那后天呢？」，各角色看不到上一轮说的是哪个城市，
                  会自己编一个。每个角色都要收到，不能只给汇总环节。
        on_event: 事件回调，事件类型额外含 stage / agent
    Returns:
        {"answer": str, "stages": {...}, "trace": [...], "rounds": int}
    """
    emit = on_event or (lambda *a, **k: None)

    # 按角色分配工具白名单——这是「最小权限原则」在 Agent 上的应用：
    # 数据 Agent 拿不到知识库工具，知识 Agent 拿不到气象工具，各司其职不越界
    data_tools = registry.subset(
        ["geocode_place", "get_forecast_daily", "get_forecast_hourly",
         "get_history_daily", "calc_pv_output", "calc_wind_output", "assess_weather_risk"])
    kb_tools = registry.subset(["search_weather_knowledge"])
    analysis_tools = registry.subset(["get_forecast_hourly", "calc_pv_output",
                                      "calc_wind_output", "assess_weather_risk"])

    # ---------- 阶段 1：数据与知识并行 ----------
    emit("stage", {"stage": "阶段 1/3：数据检索与规则检索（并行）"})
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        f_data = pool.submit(_call_agent, "数据", question, data_tools,
                             DATA_AGENT_PROMPT, "", history, on_event)
        f_kb = pool.submit(_call_agent, "知识", question, kb_tools,
                           KNOWLEDGE_AGENT_PROMPT, "", history, on_event)
        data_res, kb_res = f_data.result(), f_kb.result()

    emit("stage_done", {"stage": "阶段 1", "data_ok": data_res["ok"], "kb_ok": kb_res["ok"]})

    if not data_res["ok"] and not kb_res["ok"]:
        return {"answer": "抱歉，数据检索与知识检索都失败了，无法完成分析。",
                "stages": {"data": data_res, "knowledge": kb_res},
                "trace": [], "rounds": 0}

    context = (
        f"### 数据结果\n{data_res['answer'] or '（数据获取失败）'}\n\n"
        f"### 规则结果\n{kb_res['answer'] or '（知识库检索失败或无相关内容）'}"
    )

    # ---------- 阶段 2：交叉分析 ----------
    emit("stage", {"stage": "阶段 2/3：数据与规则交叉分析"})
    analysis_res = _call_agent("分析", question, analysis_tools, ANALYST_PROMPT,
                               context, history, on_event)

    # ---------- 阶段 3：汇总 ----------
    emit("stage", {"stage": "阶段 3/3：生成最终回答"})
    final_context = context + f"\n\n### 分析结果\n{analysis_res['answer'] or '（分析失败）'}"
    user = ("请基于以下材料回答用户的问题。用户问题：" + question +
            f"\n\n## 团队材料\n{final_context}")
    # 汇总 Agent 不分配任何工具：它的职责是综合已有材料成文，
    # 如果给它工具，它可能又去取一次数据，既浪费额度又可能引入未经验证的数值
    final_trace: list = []
    try:
        final = run_loop(user, registry.subset([]), SUMMARIZER_PROMPT,
                         history=history, on_event=on_event)
        answer = final.get("answer") or ""
        final_trace = final.get("trace") or []
    except Exception as e:
        log.warning("汇总失败，退回使用分析结果：%s", e)
        answer = analysis_res["answer"] or data_res["answer"]

    trace = data_res["trace"] + kb_res["trace"] + analysis_res["trace"] + final_trace
    return {
        "answer": answer,
        "stages": {"data": data_res, "knowledge": kb_res, "analysis": analysis_res},
        "trace": trace,
        "rounds": sum(len(s["trace"]) for s in (data_res, kb_res, analysis_res)),
    }
