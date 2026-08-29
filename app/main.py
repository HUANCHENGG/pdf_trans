"""FastAPI 应用入口。启动：`.venv/Scripts/python -m uvicorn app.main:app --port 8765`"""
from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config, tasks
from .auth import TokenAuthMiddleware
from .routes import api, pages

app = FastAPI(title="PDF 电子手册翻译工具", docs_url=None, redoc_url=None)
app.add_middleware(TokenAuthMiddleware)
app.include_router(pages.router)
app.include_router(api.router)
app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "app" / "static")), name="static")


@app.on_event("startup")
async def _startup() -> None:
    # 服务重启后内存队列清空，遗留的 pending/translating 任务永远不会再执行，统一标记取消
    from . import db
    db.execute(
        "UPDATE tasks SET status='cancelled', error='服务重启，任务中断（v1 未实现断点续翻）'"
        " WHERE status IN ('pending','translating')"
    )
    await tasks.start_worker()


@app.get("/health")
async def health():
    return {"ok": True}


if __name__ == "__main__":
    print(f"\n=== PDF 翻译工具 ===\n访问 token: {config.APP_TOKEN}\n打开 http://127.0.0.1:8765/?token={config.APP_TOKEN}\n")
    uvicorn.run(app, host="127.0.0.1", port=8765)
