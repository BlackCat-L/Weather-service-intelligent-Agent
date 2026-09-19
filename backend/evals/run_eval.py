# -*- coding: utf-8 -*-
"""评估跑分脚本。

**为什么 Agent 项目必须有评估集**
提示词、工具描述、检索策略任何一处改动都会影响行为，而影响是好是坏靠「感觉」
判断不可靠——你可能修好了一类问题、同时悄悄弄坏了另一类。有了评估集，每次
改动跑一遍，看数字而不是凭印象。

这与前一项目的做法一脉相承：那边的评估集验证「混合检索 vs 纯 BM25」，
这里的评估集验证「Agent 的工具选择与回答质量」。

**四个评分维度**
| 维度 | 含义 | 出问题时说明什么 |
|---|---|---|
| 工具选择 | 该调的调了吗 | 工具描述写得不好，模型没看懂 |
| 越界调用 | 不该调的调了吗 | 工具描述太宽泛，或提示词边界不清 |
| 关键词命中 | 回答覆盖要点了吗 | 工具返回的数据没被用上，或提示词引导不足 |
| 数值归因率 | 数字有出处吗 | 模型在编数字，或工具该给的聚合值没给 |

用法：
    python -m backend.evals.run_eval              # 全量
    python -m backend.evals.run_eval --limit 5    # 只跑前 5 题（冒烟）
    python -m backend.evals.run_eval --ids pv-02,kb-01
    python -m backend.evals.run_eval --multi      # 用多智能体模式跑
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend import checks                                     # noqa: E402
from backend.agent.loop import run as run_single               # noqa: E402
from backend.agent.orchestrator import run_multi               # noqa: E402
from backend.prompts import system_prompt                      # noqa: E402
from backend.tools import build_registry                       # noqa: E402

SET_PATH = Path(__file__).resolve().parent / "qa_set.json"
REPORT_PATH = Path(__file__).resolve().parent / "last_report.json"

C_OK, C_BAD, C_DIM, C_HL, C_END = "\033[32m", "\033[31m", "\033[2m", "\033[36m", "\033[0m"


def check_tools(case: dict, called: list[str]) -> tuple[bool, list[str]]:
    """检查必须调用的工具是否都调了。required 里每个子列表是『至少一个』。"""
    missing = []
    for group in case.get("required") or []:
        if not any(t in called for t in group):
            missing.append(" 或 ".join(group))
    return (not missing), missing


def check_forbidden(case: dict, called: list[str]) -> list[str]:
    return [t for t in (case.get("forbidden") or []) if t in called]


def check_keywords(case: dict, answer: str) -> tuple[bool, list[str]]:
    """检查回答是否覆盖期望要点。

    每个关键词可以是字符串（必须出现），也可以是**字符串列表（出现任一即可）**。
    后者用于同义表述——比如问「哪个时段合适」，回答写「时间窗口 00:00~23:00」
    同样正确。评测题若只认字面「时段」，会把正确答案判为失败，
    这种假阴性比漏测更危险：它会让人去"修"一个本来没坏的东西。
    """
    kws = case.get("expect_keywords") or []
    missed: list[str] = []
    for k in kws:
        if isinstance(k, list):
            if not any(alt in answer for alt in k):
                missed.append(" 或 ".join(k))
        elif k not in answer:
            missed.append(k)
    return (not missed), missed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题")
    ap.add_argument("--ids", default="", help="只跑指定 id，逗号分隔")
    ap.add_argument("--multi", action="store_true", help="用多智能体模式")
    ap.add_argument("--silent", action="store_true", help="不打印每题过程")
    args = ap.parse_args()

    spec = json.loads(SET_PATH.read_text(encoding="utf-8"))
    cases = spec["cases"]
    if args.ids:
        want = {x.strip() for x in args.ids.split(",") if x.strip()}
        cases = [c for c in cases if c["id"] in want]
    if args.limit:
        cases = cases[: args.limit]

    reg = build_registry()
    print(f"\n评估题集：{len(cases)} 题 ｜ 模式：{'多智能体' if args.multi else '单 Agent'}")
    print(f"工具注册表：{len(reg)} 个工具\n")
    print("─" * 84)

    results = []
    t0 = time.time()

    for i, case in enumerate(cases, 1):
        question = case["question"]
        if not args.silent:
            print(f"\n[{i}/{len(cases)}] {C_HL}{case['id']}{C_END} {question}")

        start = time.time()
        try:
            if args.multi:
                out = run_multi(question, reg)
            else:
                out = run_single(question, reg, system_prompt())
            answer = out.get("answer") or ""
            trace = out.get("trace") or []
            err = None
        except Exception as e:                                  # noqa: BLE001
            answer, trace, err = "", [], str(e)

        called = [t["name"] for t in trace]
        # 去重保序，报告里更好读
        called_uniq = list(dict.fromkeys(called))

        ok_tools, missing = check_tools(case, called)
        bad_tools = check_forbidden(case, called)
        ok_kw, missed_kw = check_keywords(case, answer)
        verify = checks.verify(answer, trace, question=question)
        rate = verify.get("grounded_rate")

        passed = ok_tools and not bad_tools and ok_kw and err is None

        results.append({
            "id": case["id"],
            "category": case.get("category"),
            "question": question,
            "called": called_uniq,
            "missing": missing,
            "forbidden_hit": bad_tools,
            "missed_keywords": missed_kw,
            "grounded_rate": rate,
            "ungrounded": verify.get("ungrounded") or [],
            "ungrounded_dates": (verify.get("dates") or {}).get("ungrounded_dates") or [],
            "elapsed": round(time.time() - start, 1),
            "passed": passed,
            "error": err,
            "answer_preview": answer[:200],
        })

        if not args.silent:
            mark = f"{C_OK}✓{C_END}" if passed else f"{C_BAD}✗{C_END}"
            print(f"    工具：{', '.join(called_uniq) or '（无）'}")
            if missing:
                print(f"    {C_BAD}缺少必需工具：{', '.join(missing)}{C_END}")
            if bad_tools:
                print(f"    {C_BAD}越界调用：{', '.join(bad_tools)}{C_END}")
            if missed_kw:
                print(f"    {C_BAD}未覆盖要点：{', '.join(missed_kw)}{C_END}")
            if err:
                print(f"    {C_BAD}异常：{err}{C_END}")
            rtxt = f"{rate * 100:.0f}%" if rate is not None else "n/a"
            print(f"    {mark} 归因 {rtxt} ｜ 耗时 {results[-1]['elapsed']}s")

    # ---------- 汇总 ----------
    n = len(results)
    passed = sum(1 for r in results if r["passed"])
    tool_ok = sum(1 for r in results if not r["missing"])
    no_forbid = sum(1 for r in results if not r["forbidden_hit"])
    kw_ok = sum(1 for r in results if not r["missed_keywords"])
    rates = [r["grounded_rate"] for r in results if r["grounded_rate"] is not None]
    avg_rate = sum(rates) / len(rates) if rates else 0

    print("\n" + "═" * 84)
    print(f"评估结果（总耗时 {time.time() - t0:.0f}s）\n")
    print(f"  通过率        {passed}/{n}  ({passed / n * 100:.0f}%)")
    print(f"  工具选择正确   {tool_ok}/{n}  ({tool_ok / n * 100:.0f}%)")
    print(f"  无越界调用     {no_forbid}/{n}  ({no_forbid / n * 100:.0f}%)")
    print(f"  要点覆盖       {kw_ok}/{n}  ({kw_ok / n * 100:.0f}%)")
    print(f"  平均数值归因率 {avg_rate * 100:.1f}%")

    failed = [r for r in results if not r["passed"]]
    if failed:
        print(f"\n未通过的题：")
        for r in failed:
            reason = []
            if r["missing"]:
                reason.append(f"缺工具({', '.join(r['missing'])})")
            if r["forbidden_hit"]:
                reason.append(f"越界({', '.join(r['forbidden_hit'])})")
            if r["missed_keywords"]:
                reason.append(f"缺要点({', '.join(r['missed_keywords'])})")
            if r["error"]:
                reason.append(f"异常({r['error'][:40]})")
            print(f"  · {r['id']:8s} {' / '.join(reason)}")

    REPORT_PATH.write_text(
        json.dumps({"summary": {
            "total": n, "passed": passed, "tool_selection_ok": tool_ok,
            "no_forbidden": no_forbid, "keywords_ok": kw_ok,
            "avg_grounded_rate": round(avg_rate, 3),
        }, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n详细报告：{REPORT_PATH}")
    print("═" * 84 + "\n")
    return 0 if passed == n else 1


if __name__ == "__main__":
    sys.exit(main())
