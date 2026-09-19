# -*- coding: utf-8 -*-
"""启动后端服务。

    python run.py              # 默认 8000 端口，带热重载
    python run.py --port 9000
    python run.py --no-reload  # 生产/演示用，不带重载（更省资源）
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-reload", action="store_true")
    args = ap.parse_args()

    print(f"\n  气象服务智能体")
    print(f"  API 文档   http://{args.host}:{args.port}/docs")
    print(f"  健康检查   http://{args.host}:{args.port}/api/health")
    print(f"  前端开发   cd frontend && pnpm dev\n")

    uvicorn.run(
        "backend.main:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
        reload_dirs=[str(Path(__file__).parent / "backend")],
        log_level="info",
    )


if __name__ == "__main__":
    main()
