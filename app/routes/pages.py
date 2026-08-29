"""页面路由（Jinja2 服务端渲染）。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import config, db, tasks

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _render(name: str, request: Request, **ctx):
    return templates.TemplateResponse(request, name, ctx)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    settings = config.load_settings()
    eps = settings["endpoints"]
    return _render("index.html", request, endpoints=eps,
                   default_endpoint=settings.get("default_endpoint"))


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request):
    rows = []
    for t in db.list_tasks():
        d = dict(t)
        d.update(tasks.snapshot(t["id"]))
        rows.append(d)
    return _render("tasks.html", request, tasks=rows)


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail(request: Request, task_id: str):
    t = db.get_task(task_id)
    if t is None:
        return HTMLResponse("任务不存在", status_code=404)
    d = dict(t)
    snap = tasks.snapshot(task_id)
    if not snap.get("elapsed") and t["finished_at"] and t["created_at"]:
        snap["elapsed"] = round(t["finished_at"] - t["created_at"], 1)
    d.update(snap)
    d["coverage"] = json.loads(d["coverage"]) if d.get("coverage") else None
    d["usage"] = json.loads(d["usage"]) if d.get("usage") else None
    return _render("task_detail.html", request, t=d, large_doc_pages=config.LARGE_DOC_PAGES)


@router.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    return _render("status.html", request)


@router.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    return _render("terms.html", request, terms=[dict(r) for r in db.list_terms()])


@router.get("/endpoints", response_class=HTMLResponse)
async def endpoints_page(request: Request):
    settings = config.load_settings()
    return _render("endpoints.html", request, endpoints=settings["endpoints"],
                   default_endpoint=settings.get("default_endpoint"))
