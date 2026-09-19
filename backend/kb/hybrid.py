# -*- coding: utf-8 -*-
"""混合检索：BM25 关键词 + 向量语义 双路融合（RRF）。

**为什么是两路，不是一路？**
- BM25 强在**专有名词精确命中**：「副热带高压」「切变线」「PM10」这类词，
  向量模型容易把它们和周边概念混在一起，BM25 则一抓一个准。
- 向量强在**换个说法也能找到**：用户问「为什么光伏板中午发电反而少」，
  知识库里写的是「组件温度升高导致转换效率衰减」——字面完全不重合，
  BM25 找不到，向量能找到。

二者互补，所以两路都要，再用 RRF 融合。

**RRF（Reciprocal Rank Fusion）为什么好？**
它只用「排名」不用「分数」：score = Σ 1/(k + rank)，k 取 60（论文常用值）。
好处是**不用调权重**——BM25 的分数是几十到几百，余弦相似度是 0~1，
量纲完全不同，直接加权要反复调参；RRF 只看名次，天然免疫量纲问题。
实测（前一项目 38 题评估集）：混合 53% vs 纯 BM25 42%。

**降级策略**
向量索引缺失或嵌入失败 → 自动退回纯 BM25，接口不变、服务不中断。
多外部依赖的时代，**每个依赖都要有「没有它也能跑」的路径**。
"""
import json
import logging
import pickle
from pathlib import Path

import jieba

from .vector import VectorIndex, embed_query

log = logging.getLogger(__name__)

# jieba 默认往 stderr 打 DEBUG 级日志（"Building prefix dict..."），
# 会淹没 CLI 输出、污染日志采集。压到 WARNING 级。
jieba.setLogLevel(logging.WARNING)

RRF_K = 60          # RRF 融合常数
PATH_DEPTH = 40     # 每路先各取 40 个候选再融合
NEIGHBORS_FOR = 4   # 只给排名前 N 的命中块扩展相邻块，控制上下文长度

KB_DIR = Path(__file__).resolve().parent / "data"


class HybridSearch:
    """知识库检索器。索引不存在时构造成功但检索返回空，调用方无需特殊处理。"""

    def __init__(self, kb_dir: Path | None = None):
        self.kb_dir = Path(kb_dir) if kb_dir else KB_DIR
        self.chunks: list[dict] = []
        self.bm25 = None
        self._by_stem: dict[str, dict[int, int]] = {}
        self._vector: VectorIndex | None = None
        self.last_method = "none"        # 最近一次检索实际走的路径，用于可观测性
        self.load()

    # ---------- 加载 ----------
    def load(self) -> bool:
        chunks_file = self.kb_dir / "chunks.json"
        bm25_file = self.kb_dir / "bm25.pkl"
        if not chunks_file.exists() or not bm25_file.exists():
            log.warning("知识库索引未构建（%s）。运行 scripts/build_index.py 生成。", self.kb_dir)
            return False
        self.chunks = json.loads(chunks_file.read_text(encoding="utf-8"))
        with open(bm25_file, "rb") as f:
            self.bm25 = pickle.load(f)

        # 邻居表：把 id 形如「文件名-3」的块按源文件分组，便于取相邻块补上下文
        for i, c in enumerate(self.chunks):
            stem, _, seq = str(c.get("id", "")).rpartition("-")
            if seq.isdigit():
                self._by_stem.setdefault(stem, {})[int(seq)] = i

        self._vector = VectorIndex(self.kb_dir / "vectors.npy")
        log.info("知识库加载完成：%s 块，向量索引 %s",
                 len(self.chunks), "可用" if self._vector.available else "不可用(降级BM25)")
        return True

    @property
    def ready(self) -> bool:
        return bool(self.chunks) and self.bm25 is not None

    @property
    def stats(self) -> dict:
        cats: dict[str, int] = {}
        for c in self.chunks:
            k = c.get("category") or "未分类"
            cats[k] = cats.get(k, 0) + 1
        return {
            "chunks": len(self.chunks),
            "vector_index": self._vector.available if self._vector else False,
            "categories": cats,
        }

    # ---------- 单路排序 ----------
    def _filter(self, categories):
        if not categories:
            return None
        cat = set(categories)
        return [i for i, c in enumerate(self.chunks) if c.get("category") in cat]

    def _bm25_rank(self, query: str, categories, depth: int) -> list[tuple[int, float]]:
        tokens = list(jieba.cut_for_search(query))
        scores = self.bm25.get_scores(tokens)
        cand = self._filter(categories)
        if cand is None:
            cand = range(len(scores))
        order = sorted(cand, key=lambda i: scores[i], reverse=True)[:depth]
        return [(i, float(scores[i])) for i in order if scores[i] > 0]

    def _vector_rank(self, query: str, categories, depth: int) -> list[tuple[int, float]]:
        if not (self._vector and self._vector.available):
            return []
        try:
            hits = self._vector.search(query, depth * 2 if categories else depth)
        except Exception as e:
            log.warning("向量检索失败，降级 BM25：%s", e)
            return []
        if categories:
            cat = set(categories)
            hits = [(i, s) for i, s in hits if self.chunks[i].get("category") in cat]
        return hits[:depth]

    # ---------- 融合 ----------
    @staticmethod
    def _rrf(paths: list[list[tuple[int, float]]]) -> list[tuple[int, float]]:
        """倒数排名融合：只看名次，免疫两路分数的量纲差异。"""
        fused: dict[int, float] = {}
        for path in paths:
            for rank, (idx, _score) in enumerate(path):
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)
        return sorted(fused.items(), key=lambda kv: kv[1], reverse=True)

    def _with_neighbors(self, hits: list[dict]) -> list[dict]:
        """给靠前的命中块补上同源相邻块——原文被切块截断的上下文补回来。"""
        extra = []
        seen = {h["id"] for h in hits}
        for h in hits[:NEIGHBORS_FOR]:
            stem, _, seq = str(h["id"]).rpartition("-")
            if not seq.isdigit():
                continue
            group = self._by_stem.get(stem, {})
            n = int(seq)
            for nb in (n - 1, n + 1):
                idx = group.get(nb)
                if idx is None:
                    continue
                c = self.chunks[idx]
                if c["id"] in seen:
                    continue
                seen.add(c["id"])
                extra.append({**c, "_neighbor_of": h["id"]})
        return hits + extra

    # ---------- 对外接口 ----------
    def search(self, query: str, top_k: int = 6, categories: list[str] | None = None,
               mode: str | None = None, neighbors: bool = True,
               return_paths: bool = False) -> dict:
        """检索知识库。

        Args:
            mode: None=自动（有向量走混合）/ "bm25"=强制单路 / "hybrid"=强制混合
            return_paths: 是否额外返回两路各自的排名（供知识库调试台可视化）
        Returns:
            {"hits": [...], "method": "hybrid"|"bm25", "paths": {...}}
        """
        if not self.ready:
            return {"hits": [], "method": "none",
                    "error": "知识库索引未构建，请先运行 python scripts/build_index.py"}

        bm25_path = self._bm25_rank(query, categories, PATH_DEPTH)
        use_vector = mode != "bm25" and self._vector is not None and self._vector.available
        vec_path = self._vector_rank(query, categories, PATH_DEPTH) if use_vector else []

        if vec_path:
            fused = self._rrf([bm25_path, vec_path])
            method = "hybrid"
        else:
            fused = [(i, s) for i, s in bm25_path]
            method = "bm25"
        self.last_method = method

        hits = []
        for idx, score in fused[:top_k]:
            c = self.chunks[idx]
            hits.append({
                "id": c.get("id"),
                "text": c.get("text"),
                "category": c.get("category"),
                "source": c.get("source"),
                "score": round(score, 5),
            })
        if neighbors:
            hits = self._with_neighbors(hits)

        out = {"hits": hits, "method": method, "query": query,
               "categories": categories or "全部", "total_candidates": len(fused)}
        if return_paths:
            out["paths"] = {
                "bm25": [{"id": self.chunks[i]["id"], "score": round(s, 4),
                          "text": self.chunks[i]["text"][:120]} for i, s in bm25_path[:10]],
                "vector": [{"id": self.chunks[i]["id"], "score": round(s, 4),
                            "text": self.chunks[i]["text"][:120]} for i, s in vec_path[:10]],
            }
        return out


# 全局单例：知识库是只读的，常驻内存避免每次请求重复加载
_instance: HybridSearch | None = None


def get_kb() -> HybridSearch:
    global _instance
    if _instance is None:
        _instance = HybridSearch()
    return _instance
