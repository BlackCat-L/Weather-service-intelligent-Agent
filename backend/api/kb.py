# -*- coding: utf-8 -*-
"""知识库接口：检索调试台与统计。

**为什么要给知识库单独做接口**
检索质量是 RAG 系统成败的关键，而它恰恰是最不透明的一环——出问题时你只能
看到「答案不对」，看不到「检索到了什么」。这个接口把混合检索的内部过程
暴露出来：BM25 排了什么、向量排了什么、RRF 融合后是什么，三路并列展示。

工具价值：调检索参数时不用改代码、不用重启，改一个 query 就能看到效果。
演示价值：面试时能直观讲清「混合检索为什么比单路好」，而不是空口说。
"""
import logging

from fastapi import APIRouter, HTTPException

from ..kb.hybrid import get_kb
from .schemas import KbSearchRequest, KbSearchResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])


@router.get("/stats")
def kb_stats():
    """知识库统计：块数、分类分布、向量索引是否可用。"""
    kb = get_kb()
    return {"ready": kb.ready, **kb.stats}


@router.post("/search", response_model=KbSearchResponse)
def kb_search(req: KbSearchRequest):
    """检索知识库。mode=auto 时自动选择混合/单路，并可返回两路排名对比。"""
    kb = get_kb()
    if not kb.ready:
        raise HTTPException(
            status_code=503,
            detail="知识库索引未构建。请运行 python scripts/build_index.py")
    mode = None if req.mode == "auto" else req.mode
    result = kb.search(req.query, top_k=req.top_k, categories=req.categories,
                       mode=mode, neighbors=True, return_paths=True)
    return KbSearchResponse(**{k: v for k, v in result.items() if k != "error"})


@router.post("/reload")
def kb_reload():
    """重新加载索引（重建索引后无需重启服务）。"""
    kb = get_kb()
    ok = kb.load()
    return {"reloaded": ok, **kb.stats}
