# -*- coding: utf-8 -*-
"""向量索引：本地 ONNX 嵌入（fastembed + bge-small-zh），零 API 依赖。

**为什么不用 API 做嵌入？**
前一项目真实踩过：嵌入走 API 时，后台建索引任务会和线上服务抢同一个 Key 的
配额，把线上服务挤死（账户级 429 限流，同 Key 下所有接口共享配额）。
本地 ONNX 推理永不限流，代价只是首次要下载约 100MB 模型。

**为什么用 bge-small-zh？**
中文语义检索效果好、体积小（512 维）、CPU 推理够快。索引 3 万块约 20 分钟。
"""
import logging
import os
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# 国内服务器直连 HuggingFace 会卡死，必须走镜像（前一项目踩过的坑）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
VECTOR_FILE = "vectors.npy"

_model = None


def _get_model():
    """懒加载嵌入模型——没建索引的场景不该被迫下载 100MB 模型。"""
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        log.info("加载本地嵌入模型 %s ...", MODEL_NAME)
        _model = TextEmbedding(model_name=MODEL_NAME)
    return _model


def embed(texts: list[str]) -> np.ndarray:
    """把文本编码成向量矩阵 (n, dim)。"""
    return np.array(list(_get_model().embed(texts)), dtype=np.float32)


def embed_query(query: str) -> np.ndarray:
    """编码单条查询。bge 系列建议查询侧加指令前缀以提升检索效果。"""
    return embed([f"为这个句子生成表示以用于检索相关文章：{query}"])[0]


def build(texts: list[str], out_path: Path, batch_log_every: int = 500) -> Path:
    """批量建索引并保存为 .npy。"""
    vecs = []
    for i, v in enumerate(_get_model().embed(texts, batch_size=64)):
        vecs.append(v)
        if (i + 1) % batch_log_every == 0:
            log.info("  已编码 %s / %s", i + 1, len(texts))
    arr = np.array(vecs, dtype=np.float32)
    np.save(out_path, arr)
    return out_path


class VectorIndex:
    """加载好的向量索引，提供余弦相似度检索。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.vectors: np.ndarray | None = None
        self._norms: np.ndarray | None = None
        self.load()

    def load(self) -> bool:
        if not self.path.exists():
            log.info("向量索引不存在：%s（将自动降级为纯 BM25）", self.path)
            return False
        try:
            v = np.load(self.path)
            if v.ndim != 2 or v.shape[0] == 0:
                log.warning("向量索引形状异常：%s", v.shape)
                return False
            self.vectors = v
            # 预先算好模长，检索时用点积代替完整余弦计算
            self._norms = np.linalg.norm(v, axis=1, keepdims=True)
            self._norms[self._norms == 0] = 1e-9
            return True
        except Exception as e:
            log.warning("向量索引加载失败：%s", e)
            return False

    @property
    def available(self) -> bool:
        return self.vectors is not None

    def search(self, query: str, depth: int) -> list[tuple[int, float]]:
        """返回 [(chunks 下标, 余弦相似度)]，按相似度降序。"""
        if not self.available:
            return []
        q = embed_query(query)
        qn = q / (np.linalg.norm(q) or 1e-9)
        sims = (self.vectors / self._norms) @ qn
        order = np.argsort(-sims)[:depth]
        return [(int(i), float(sims[i])) for i in order]
