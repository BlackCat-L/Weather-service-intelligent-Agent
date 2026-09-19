# -*- coding: utf-8 -*-
"""气象智能体命令行入口。

用法：
    python ask.py "余杭区明天下午光伏发电条件怎么样"
    python ask.py --multi "..."                 # 多智能体协同模式
    python ask.py -v "..."                      # 显示完整工具返回结果
    python ask.py --tools                       # 只看已注册的工具清单
    python ask.py --json "..."                  # 输出结构化 JSON（供脚本消费）

设计说明：
1. **本入口与 HTTP 接口共用 services 层**（`qa_service.answer`），
   业务逻辑只有一份，行为必然一致 —— CLI 能跑通的结果，接口也能跑通。
2. 执行过程会把工具调用轨迹打出来，既是调试工具也是演示工具：
   让对方直观看到模型确实在自主调工具，而不是在背答案。
"""
import argparse
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.services import qa_service            # noqa: E402

C_DIM, C_TOOL, C_OK, C_WARN, C_HL, C_END = (
    "\033[2m", "\033[36m", "\033[32m", "\033[33m", "\033[35m", "\033[0m")


def _short(obj, limit=220):
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + f"...(+{len(s) - limit}字)"


def main():
    ap = argparse.ArgumentParser(description="气象智能助手")
    ap.add_argument("question", nargs="?", help="要问的问题")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印完整工具返回结果")
    ap.add_argument("--multi", action="store_true",
                    help="多智能体协同编排（数据/知识并行 → 交叉分析 → 汇总）")
    ap.add_argument("--tools", action="store_true", help="只列出已注册的工具")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON，便于脚本消费")
    ap.add_argument("--trace-json", help="把工具调用轨迹写入指定 JSON 文件")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    reg = qa_service.registry()

    if args.tools:
        print(f"已注册 {len(reg)} 个工具：\n")
        for t in reg.schemas():
            f = t["function"]
            params = ", ".join(f["parameters"]["properties"].keys())
            print(f"  {C_TOOL}{f['name']}{C_END}({params})")
            print(f"      {C_DIM}{f['description'][:110]}...{C_END}")
        return 0

    if not args.question:
        ap.print_help()
        return 1

    quiet = args.json
    if not quiet:
        mode_label = "多智能体协同" if args.multi else "单 Agent"
        print(f"\n{C_HL}[{mode_label}]{C_END} 问题：{args.question}\n")
        print(f"{C_DIM}{'─' * 68}{C_END}")

    t0 = time.time()

    def on_event(kind, payload):
        if quiet:
            return
        if kind == "stage":
            print(f"\n{C_WARN}▶ {payload['stage']}{C_END}")
        elif kind == "stage_done":
            print(f"{C_DIM}  （{payload['stage']} 完成：数据 "
                  f"{'✓' if payload.get('data_ok') else '✗'} / 知识 "
                  f"{'✓' if payload.get('kb_ok') else '✗'}）{C_END}")
        elif kind == "tool_calls":
            print(f"{C_TOOL}  · 第 {payload['round']} 轮：调用 "
                  f"{len(payload['calls'])} 个工具{C_END}")
            for c in payload["calls"]:
                print(f"      → {c['name']}({c['args']})")
        elif kind == "tool_result":
            r = payload["result"]
            if isinstance(r, dict) and r.get("error"):
                print(f"      {C_WARN}✗ {r['error']}{C_END}")
            else:
                print(f"      {C_OK}✓ {_short(r, 400 if args.verbose else 130)}{C_END}")

    try:
        result = qa_service.answer(
            args.question, session_id=None,
            mode="multi" if args.multi else "single", on_event=on_event)
    except Exception as e:                                  # noqa: BLE001
        print(f"\n{C_WARN}执行失败：{e}{C_END}")
        return 1

    elapsed = time.time() - t0
    vr = result["verification"]

    if args.json:
        print(json.dumps({
            "question": args.question,
            "answer": result["answer"],
            "mode": result["mode"],
            "rounds": result["rounds"],
            "tools_called": list(dict.fromkeys(t["name"] for t in result["trace"])),
            "verification": vr,
            "elapsed_seconds": round(elapsed, 1),
        }, ensure_ascii=False, indent=2))
    else:
        print(f"{C_DIM}{'─' * 68}{C_END}")
        print(f"\n{C_OK}【回答】{C_END}\n")
        print(result["answer"])
        print(f"\n{C_DIM}{'─' * 68}{C_END}")

        rate = vr.get("grounded_rate")
        color = C_WARN if vr.get("verdict") == "warn" else C_OK
        if rate is None:
            print(f"{color}数值归因：无法校验（本轮无工具调用）{C_END}")
        else:
            line = f"数值归因：{rate * 100:.0f}%（{vr['grounded']}/{vr['total']} 个数字可追溯到工具结果）"
            if vr.get("ungrounded"):
                line += f" ⚠ 未归因数值：{', '.join(str(v) for v in vr['ungrounded'][:6])}"
            bad_dates = (vr.get("dates") or {}).get("ungrounded_dates") or []
            if bad_dates:
                line += f" ⚠ 无依据日期：{', '.join(bad_dates)}"
            print(f"{color}{line}{C_END}")

        tools = list(dict.fromkeys(t["name"] for t in result["trace"]))
        print(f"{C_DIM}（工具调用 {len(result['trace'])} 次 / {len(tools)} 个："
              f"{', '.join(tools) or '无'} ｜ {result['rounds']} 轮 ｜ {elapsed:.1f}s）{C_END}\n")

    if args.trace_json:
        with open(args.trace_json, "w", encoding="utf-8") as f:
            json.dump({"question": args.question, "answer": result["answer"],
                       "trace": result["trace"], "verification": vr},
                      f, ensure_ascii=False, indent=2)
        print(f"{C_DIM}轨迹已写入 {args.trace_json}{C_END}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
