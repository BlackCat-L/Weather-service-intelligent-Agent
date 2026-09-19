# -*- coding: utf-8 -*-
"""知识库建索引：把 knowledge_raw/ 下的资料切块，建 BM25 + 向量双索引。

用法：
    python scripts/build_index.py              # 全量重建（BM25 + 向量）
    python scripts/build_index.py --no-vector  # 只建 BM25，跳过耗时的向量编码

输出到 backend/kb/data/：
    chunks.json   切块结果（含 id / text / category / source）
    bm25.pkl      BM25 索引
    vectors.npy   向量索引

**血泪教训（前一项目真实踩过）**：两套索引必须一起重建。只更新其中一个，
索引与块列表就会错位——检索结果张冠李戴，而且**不会报任何错**。
所以本脚本默认全量重建，并强制两者出自同一份 chunks。

**切块策略**
按 Markdown 标题切分，把标题路径（如「行业影响规则：新能源与电力 > 光伏发电 >
温度对效率的影响」）作为前缀拼进块文本。这一步很关键：原文里「组件温度比气温
高约 30°C」这句话脱离标题后语义是残缺的，拼上标题路径检索命中率才高。
超长小节再按段落二次切分，保留 60 字重叠避免语义被切断。
"""
import argparse
import json
import pickle
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import jieba                              # noqa: E402
from rank_bm25 import BM25Okapi           # noqa: E402

from backend.kb import vector             # noqa: E402

RAW_DIR = ROOT / "knowledge_raw"
OUT_DIR = ROOT / "backend" / "kb" / "data"

MAX_CHARS = 600      # 单块目标上限
MIN_CHARS = 80       # 低于此长度的块合并进上一块，避免碎片
OVERLAP = 60         # 二次切分时的重叠字数

# 文件名 → 分类。新增资料时在这里登记，未登记的归入「通用」
# 分类名会作为检索过滤条件暴露给模型，**必须让模型能猜对**。
# 踩过的坑：原来「户外作业」的内容放在 `05-行业影响规则-农业与户外` 里，
# 分类名却是「行业规则-农业」——用户问户外作业时，模型合理地去猜「行业规则-交通」，
# 结果一直检索不到，就不断换措辞重试，一次问答耗掉 7 次知识库调用还没收敛。
# 教训：**分类名要反映内容，不要反映文件曾怎么组织过。**
CATEGORY_MAP = {
    "01-气象要素与术语": "术语",
    "02-气象灾害分级标准": "灾害标准",
    "03-行业影响规则-能源": "行业规则-能源",
    "04-行业影响规则-交通与低空": "行业规则-交通",
    "05-行业影响规则-农业": "行业规则-农业",
    "06-行业影响规则-户外作业": "行业规则-户外",
    "07-预报产品与数据说明": "数据说明",
}


def category_of(stem: str) -> str:
    for key, cat in CATEGORY_MAP.items():
        if stem.startswith(key):
            return cat
    return "通用"


def _split_long(text: str, max_chars: int) -> list[str]:
    """超长文本按段落聚合切分，保留尾部重叠。"""
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    out, buf = [], ""
    for p in paras:
        if not buf:
            buf = p
        elif len(buf) + len(p) + 1 <= max_chars:
            buf += "\n" + p
        else:
            out.append(buf)
            # 从上一块尾部取重叠内容，避免语义被硬切断
            tail = buf[-OVERLAP:] if len(buf) > OVERLAP else buf
            buf = tail + "\n" + p
    if buf:
        out.append(buf)
    return out


def split_markdown(text: str) -> list[str]:
    """按标题层级切分，标题路径作为前缀拼进块文本。"""
    sections: list[str] = []
    h1 = h2 = h3 = ""
    buf: list[str] = []

    def flush():
        body = "\n".join(buf).strip()
        buf.clear()
        if not body:
            return
        path = " > ".join(x for x in (h1, h2, h3) if x)
        blob = f"{path}\n{body}" if path else body
        if len(blob) <= MAX_CHARS:
            sections.append(blob)
        else:
            sections.extend(_split_long(blob, MAX_CHARS))

    for line in text.split("\n"):
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2).strip()
            if level == 1:
                h1, h2, h3 = title, "", ""
            elif level == 2:
                h2, h3 = title, ""
            else:
                h3 = title
        else:
            buf.append(line)
    flush()

    # 合并过短的碎片块
    merged: list[str] = []
    for s in sections:
        if merged and len(s) < MIN_CHARS:
            merged[-1] = merged[-1] + "\n" + s
        else:
            merged.append(s)
    return [s for s in merged if len(s) >= 20]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-vector", action="store_true", help="跳过向量索引（快，用于调试）")
    ap.add_argument("--raw", default=str(RAW_DIR))
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    raw_dir, out_dir = Path(args.raw), Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(raw_dir.glob("*.md")) + sorted(raw_dir.glob("*.txt"))
    if not files:
        print(f"✗ {raw_dir} 下没有找到 .md / .txt 资料")
        return 1

    chunks = []
    for f in files:
        stem = f.stem
        texts = split_markdown(f.read_text(encoding="utf-8"))
        for i, t in enumerate(texts):
            chunks.append({
                "id": f"{stem}-{i}",
                "text": t,
                "category": category_of(stem),
                "source": f.name,
            })
        print(f"  {f.name:36s} → {len(texts):3d} 块  [{category_of(stem)}]")

    print(f"\n合计 {len(chunks)} 块")

    # 1) chunks.json
    (out_dir / "chunks.json").write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ chunks.json")

    # 2) BM25 —— 用搜索模式分词，专有名词会被切成更细的词，提升召回
    corpus = [list(jieba.cut_for_search(c["text"])) for c in chunks]
    with open(out_dir / "bm25.pkl", "wb") as fp:
        pickle.dump(BM25Okapi(corpus), fp)
    print(f"  ✓ bm25.pkl（{len(corpus)} 篇）")

    # 3) 向量索引
    if args.no_vector:
        print("  - 已跳过向量索引（--no-vector）")
        print("\n注意：只建了 BM25，检索会自动降级为单路。补建向量请去掉 --no-vector 重跑。")
    else:
        print(f"  · 正在编码向量（首次会下载模型，约 100MB，请耐心等待）...")
        vector.build([c["text"] for c in chunks], out_dir / "vectors.npy")
        print(f"  ✓ vectors.npy")

    print(f"\n索引输出目录：{out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
