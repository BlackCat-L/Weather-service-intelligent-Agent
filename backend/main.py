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

app = FastAPI(
    title="气象服务智能体",
    description="面向能源/交通/农业的气象服务 Agent，支持 Function Calling 与混合检索 RAG",
    version="1.0.0",
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


@app.get("/api/health")
def health():
    """健康检查。也是排查配置问题最快的入口——一眼看出用的哪个模型、
    知识库有没有加载、工具注册了几个。"""
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


@app.get("/api/tools")
def tools():
    """已注册工具清单。前端用它展示 Agent 的能力边界。"""
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
