# -*- coding: utf-8 -*-
"""知识库接口：检索调试与统计。

**为什么要给知识库单独做接口**
检索质量是 RAG 系统成败的关键，而它恰恰是最不透明的一环——出问题时你只能
看到「答案不对」，看不到「检索到了什么」。这个接口把混合检索的内部过程
暴露出来：BM25 排了什么、向量排了什么、RRF 融合后是什么，三路并列展示。

- **工具价值**：调检索参数时不用改代码、不用重启，改一个 query 就能看到效果
- **演示价值**：能直观讲清「混合检索为什么比单路好」，而不是空口下论断
"""
import logging

from fastapi import APIRouter, HTTPException

from ..kb.hybrid import get_kb
from .schemas import KbSearchRequest, KbSearchResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/kb", tags=["知识库"])


@router.get(
    "/stats",
    summary="知识库统计",
    description="""
返回知识库的当前状态：总块数、分类分布、向量索引是否可用。

**排查检索质量问题时第一个该看的接口。** 重点看 `vector_index` 字段：

- `true` — 混合检索生效（BM25 + 向量 + RRF 融合）
- `false` — **已降级为纯 BM25**，检索质量会明显下降（同义改写的问法会检索不到）

若为 false，说明 `backend/kb/data/vectors.npy` 缺失或损坏，
需重新运行 `python scripts/build_index.py` 全量重建。
""",
)
def kb_stats():
    kb = get_kb()
    return {"ready": kb.ready, **kb.stats}


@router.post(
    "/search",
    response_model=KbSearchResponse,
    summary="知识库检索 · 三路对比",
    description="""
检索知识库，并**同时返回 BM25 单路、向量单路、RRF 融合三组结果**。

### 返回结构怎么读

- `hits` — RRF 融合后的最终结果（实际被采用的那一份）
- `paths.bm25` — 纯关键词检索的原始排名
- `paths.vector` — 纯语义检索的原始排名

把三者并列看，就能直观理解两种检索的差异。**典型例子**：用
`{"query": "多少度算高温"}` 调这个接口，会发现

| 路径 | 首位命中 | 结果 |
|---|---|---|
| BM25 | 「算参考：6 级风约 39-49 公里/小时…」 | ✗ 被「度」字误导到风力等级 |
| 向量 | 「气象灾害分级标准 > 高温标准」 | ✓ |
| RRF 融合 | 「气象灾害分级标准 > 高温标准」 | ✓ |

BM25 强在专有名词精确命中，弱在会被字面误导；向量强在语义泛化，
弱在专有名词可能被相近概念干扰。**两者互补，所以融合。**

### 想对比单路效果

把 `mode` 分别设为 `bm25` 和 `hybrid` 各调一次，对比首位命中即可。
""",
)
def kb_search(req: KbSearchRequest):
    kb = get_kb()
    if not kb.ready:
        raise HTTPException(
            status_code=503,
            detail="知识库索引未构建。请在项目根目录运行：python scripts/build_index.py")
    mode = None if req.mode == "auto" else req.mode
    result = kb.search(req.query, top_k=req.top_k, categories=req.categories,
                       mode=mode, neighbors=True, return_paths=True)
    return KbSearchResponse(**{k: v for k, v in result.items() if k != "error"})


@router.post(
    "/reload",
    summary="重新加载索引（免重启）",
    description="""
重新从磁盘加载知识库索引，**不需要重启服务**。

**使用场景**：更新了 `knowledge_raw/` 下的资料并重新跑了建索引脚本之后，
调一次这个接口就能让新内容生效。

⚠️ 注意：本接口只重新读盘，**不会自动重建索引**。新增或修改了知识库资料，
必须先在项目根目录执行 `python scripts/build_index.py`，否则加载到的还是旧索引。

> 为什么不自动重建？重建要跑嵌入模型编码，耗时可达数分钟（取决于资料量），
> 放在 HTTP 请求里会超时。建索引是离线任务，加载索引才是在线操作——
> 两者分开是刻意设计。
""",
)
def kb_reload():
    kb = get_kb()
    ok = kb.load()
    return {"reloaded": ok, **kb.stats}
