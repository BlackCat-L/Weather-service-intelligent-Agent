# -*- coding: utf-8 -*-
"""FastAPI 应用装配。

**这个文件只做装配，不写业务逻辑。**
把中间件、路由、静态资源拼起来交给 uvicorn 就完事。保持「一眼看完全貌」，
是为了让新接手的人（以及三个月后的自己）能在 30 秒内搞清楚这个服务有哪些入口。

启动：
    python run.py                     # 开发模式（自动重载）
    uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""
import logging
import mimetypes
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Windows 专属陷阱的修复：显式注册前端资源的 MIME 类型
#
# 症状：本地开发用 Vite 一切正常，但部署后访问首页是**白屏**，控制台报
#   "Failed to load module script: Expected a JavaScript-or-Wasm module script
#    but the server responded with a MIME type of text/plain"
#
# 根因：Python 的 mimetypes 在 Windows 上从**注册表**读取 MIME 映射，而注册表里
#   .js 的 Content Type 常常缺失或被装某些软件时改坏，于是 guess_type 回退成
#   text/plain。浏览器对 <script type="module"> 执行**严格 MIME 校验**，
#   类型不对就直接拒绝执行 —— 表现就是整个应用白屏，且没有任何服务端报错。
#   Linux 上读 /etc/mime.types，所以不会有这个问题。
#
# 教训：**不要依赖操作系统的 MIME 配置**。前端要被正确提供服务所需的那几个
#   类型，必须在代码里显式声明 —— 这是部署健壮性的一部分，不是多余代码。
# ---------------------------------------------------------------------------
for _ext, _type in {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".wasm": "application/wasm",
    ".ico": "image/x-icon",
}.items():
    mimetypes.add_type(_type, _ext)

from .api import chat, kb
from .infra import config
from .kb.hybrid import get_kb
from .services import qa_service
from .services.session_store import get_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("weather-agent")

# 接口分组：Swagger UI 会按这个顺序分区展示，并显示每组的中文说明
TAGS_METADATA = [
    {
        "name": "对话",
        "description": (
            "Agent 问答接口——**这是产品的核心能力**。\n\n"
            "用户用自然语言提问，Agent 自主决定调用哪些工具（解析地名坐标、拉取气象数据、"
            "计算发电出力、检索行业规则），最终给出可直接执行的决策结论。\n\n"
            "两种调用方式功能相同，按场景选：**流式**（能看到执行过程，适合前端）、"
            "**一次性 JSON**（适合脚本与批量评估）。"
        ),
    },
    {
        "name": "知识库",
        "description": (
            "检索层的调试接口。把混合检索的内部过程暴露出来——"
            "BM25 单路排了什么、向量单路排了什么、RRF 融合后是什么。\n\n"
            "检索质量是 RAG 系统成败的关键，却也是最不透明的一环。"
            "这组接口就是把它变得可见：调参数不用改代码不用重启，"
            "也便于直观对比「混合检索为什么优于单路」。"
        ),
    },
    {
        "name": "系统",
        "description": "健康检查与能力清单。排查配置问题时先看这两个接口。",
    },
]

app = FastAPI(
    title="气象服务智能体 · API",
    description="""
面向 **能源 / 交通 / 农业** 的气象服务 Agent 接口。

用自然语言提问，Agent 自主决定调用哪些工具——解析地名坐标、拉取气象数据、
计算发电出力、检索行业规则——最终给出**可直接执行的决策结论**，
而不是一堆原始气象数字。

> 目标是回答「**明天下午能不能安排光伏板清洗**」，而不是「明天天气怎么样」。

---

### 这个 API 有什么不一样

| 特性 | 说明 |
|---|---|
| **Function Calling 工具编排** | 8 个工具，模型自主决定调用路径，无硬编码流程；支持并行调用与失败容错 |
| **混合检索知识库** | BM25 + 向量双路 + RRF 融合，向量索引缺失时自动降级为单路 |
| **数值归因校验** | 回答中每个数字与日期都必须能追溯到工具返回结果，防大模型编造气象数值 |
| **完整可观测性** | 每次问答返回结构化 trace（工具调用序列、参数、返回值）与归因报告 |

### 快速上手

1. 调 `POST /api/chat` 问一个问题，看返回的 `trace` 与 `verification`
2. 调 `POST /api/kb/search` 传 `{"query": "多少度算高温"}`，对比三路检索结果
3. 调 `GET /api/tools` 查看 Agent 当前具备哪些工具能力

### 关于默认值

请求体里的示例值可以直接点「Try it out」发送，不需要改任何字段。
`mode` 默认 `single`（单 Agent，快），`stream` 默认 `true`。
""",
    version="1.0.0",
    openapi_tags=TAGS_METADATA,
    contact={"name": "杨林灵", "url": "https://github.com/BlackCat-L/Weather-service-intelligent-Agent"},
    license_info={"name": "项目源码", "url": "https://github.com/BlackCat-L/Weather-service-intelligent-Agent"},
)

# 开发时前端跑在 Vite 的 5173 端口，需要跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(kb.router)

_START = time.time()


@app.get("/api/health", tags=["系统"], summary="健康检查")
def health():
    """排查配置问题最快的入口——一眼看出用的哪个模型、知识库有没有加载、工具注册了几个。

    重点看 `knowledge_base.vector_index`：为 `false` 说明向量索引不可用、
    已降级为纯 BM25，检索质量会明显下降。"""
    p = config.provider()
    try:
        kb_stats = get_kb().stats
    except Exception as e:                              # noqa: BLE001
        kb_stats = {"error": f"知识库未加载：{e}"}
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - _START, 1),
        "llm_provider": p["name"],
        "llm_model": p["model"],
        "llm_mock": config.mock(),
        "tools_count": len(qa_service.registry()),
        "knowledge_base": kb_stats,
        "sessions": get_store().stats,
    }


@app.get("/api/tools", tags=["系统"], summary="工具能力清单")
def tools():
    """列出 Agent 当前具备的全部工具及其参数。

    这份清单就是 Agent 的能力边界——模型只能从这些工具里选。
    新增工具只需在 `backend/tools/` 下写模块并登记一行，无需改动其它代码。"""
    reg = qa_service.registry()
    return {
        "count": len(reg),
        "tools": [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "parameters": list(t["function"]["parameters"]["properties"].keys()),
            }
            for t in reg.schemas()
        ],
    }


# ---------- 前端静态资源 ----------
# 构建产物存在就直接托管，实现「一个端口跑完整站」
_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if _DIST.exists():
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    if (_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        """SPA 兜底路由。

        前端用的是 history 模式路由（createWebHistory），/kb 这类路径在服务端
        并不存在对应文件。如果只是把 dist 目录 mount 到 /，用户直接访问或刷新
        /kb 就会 404 —— 这是 SPA 部署最常见的坑。

        做法：真实存在的文件（favicon 等）照常返回；其余路径一律回 index.html，
        由前端路由自己解析。`/api` 开头的要排除掉，否则接口 404 会被伪装成
        200 的 HTML，排查起来非常难受。
        """
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="接口不存在")
        target = _DIST / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(_DIST / "index.html")

    log.info("已挂载前端构建产物：%s", _DIST)
else:
    @app.get("/")
    def _no_frontend():
        return JSONResponse({
            "message": "前端尚未构建。",
            "hint": "cd frontend && pnpm install && pnpm build；开发时请另开终端跑 pnpm dev（Vite 默认 5173 端口）",
            "api_docs": "/docs",
        })


@app.on_event("startup")
def _warmup():
    """启动时预热：构建工具注册表会加载知识库与嵌入模型，
    放到启动阶段做，避免第一个请求卡住好几秒。"""
    log.info("正在预热工具注册表与知识库…")
    reg = qa_service.registry()
    log.info("就绪：%d 个工具，模型 %s/%s",
             len(reg), config.provider()["name"], config.provider()["model"])
