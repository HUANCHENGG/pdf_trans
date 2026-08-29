"""JSON/SSE API：上传、任务状态、下载、端点 CRUD、术语表 CRUD。"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import fitz
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from .. import config, db, tasks

router = APIRouter(prefix="/api")


# ---------- 上传与任务 ----------

@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    endpoint_id: str = Form(""),
    pages_spec: str = Form(""),
    no_mono: bool = Form(False),
    use_glossary: bool = Form(True),
    dry_run: bool = Form(False),
    protect_toc: bool = Form(True),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "请上传 PDF 文件")
    settings = config.load_settings()
    if endpoint_id:
        endpoint = next((e for e in settings["endpoints"] if e["id"] == endpoint_id), None)
        if endpoint is None and not dry_run:
            raise HTTPException(400, "所选端点不存在，请刷新页面重选")
    else:
        endpoint = next((e for e in settings["endpoints"] if e["id"] == settings.get("default_endpoint")), None)
    if endpoint is None and not dry_run:
        raise HTTPException(400, "请先在「API 端点」页配置至少一个端点，或勾选管线测试")

    stored = tasks.new_upload_path(file.filename)
    size = 0
    with stored.open("wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            f.write(chunk)
    if size > config.MAX_UPLOAD_MB << 20:
        stored.unlink(missing_ok=True)
        raise HTTPException(400, f"文件超过 {config.MAX_UPLOAD_MB}MB 上限")

    try:
        doc = fitz.open(str(stored))
        n_pages = doc.page_count
        doc.close()
    except Exception:
        stored.unlink(missing_ok=True)
        raise HTTPException(400, "无法解析的 PDF 文件")

    task_id = db.create_task(
        filename=file.filename, stored_path=stored, endpoint=endpoint or {},
        pages_spec=pages_spec.strip() or None, no_mono=no_mono,
        use_glossary=use_glossary, dry_run=dry_run, protect_toc=protect_toc,
        pages=n_pages,
    )
    tasks.submit(task_id)
    return {"task_id": task_id, "pages": n_pages}


@router.get("/status")
async def service_status():
    """服务级监控（只读）：运行时长、队列、活动任务快照。"""
    return tasks.service_stats()


@router.get("/tasks")
async def list_tasks():
    out = []
    for t in db.list_tasks():
        d = dict(t)
        d.update(tasks.snapshot(t["id"]))
        out.append(d)
    return out


@router.get("/tasks/{task_id}")
async def task_detail(task_id: str):
    t = db.get_task(task_id)
    if t is None:
        raise HTTPException(404, "任务不存在")
    d = dict(t)
    snap = tasks.snapshot(task_id)
    if not snap.get("elapsed") and t["finished_at"] and t["created_at"]:
        snap["elapsed"] = round(t["finished_at"] - t["created_at"], 1)
    d.update(snap)
    return d


@router.post("/tasks/{task_id}/cancel")
async def task_cancel(task_id: str):
    ok = await tasks.cancel(task_id)
    if not ok:
        db.update_task(task_id, status="cancelled", finished_at=time.time())
        tasks.mark_cancelled(task_id)
    return {"cancelling": True}


@router.post("/tasks/{task_id}/retry")
async def task_retry(task_id: str):
    """用原参数重新入队（复用已上传的源文件）。"""
    t = db.get_task(task_id)
    if t is None:
        raise HTTPException(404, "任务不存在")
    if not Path(t["stored_path"]).exists():
        raise HTTPException(400, "源文件已清理，请重新上传")
    settings = config.load_settings()
    endpoint = next((e for e in settings["endpoints"] if e["id"] == t["endpoint_id"]), None)
    new_id = db.create_task(
        filename=t["filename"], stored_path=Path(t["stored_path"]), endpoint=endpoint or {},
        pages_spec=t["pages_spec"], no_mono=bool(t["no_mono"]),
        use_glossary=bool(t["use_glossary"]), dry_run=bool(t["dry_run"]),
        protect_toc=bool(t["protect_toc"]), pages=t["pages"],
    )
    tasks.submit(new_id)
    return {"task_id": new_id}


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str):
    async def gen():
        last = None
        while True:
            t = db.get_task(task_id)
            if t is None:
                yield "event: error\ndata: task not found\n\n"
                return
            snap = tasks.snapshot(task_id)
            status = snap.get("status") or t["status"]
            payload = json.dumps({
                "status": status,
                "progress": snap["progress"],
                "message": snap["message"],
                "db_status": t["status"],
            }, ensure_ascii=False)
            if payload != last:
                yield f"data: {payload}\n\n"
                last = payload
            if status in ("done", "failed", "cancelled"):
                return
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@router.get("/tasks/{task_id}/download/{kind}")
async def download(task_id: str, kind: str):
    t = db.get_task(task_id)
    if t is None:
        raise HTTPException(404, "任务不存在")
    path = t["dual_path"] if kind == "dual" else t["mono_path"]
    if not path:
        raise HTTPException(404, "该输出不存在")
    p = Path(path)
    stem = Path(t["filename"]).stem
    name = f"{stem}.双语.pdf" if kind == "dual" else f"{stem}.译文.pdf"
    return FileResponse(p, filename=name, media_type="application/pdf")


# ---------- 端点配置 ----------

def _ep_public(e: dict) -> dict:
    return {k: ("******" if k == "api_key" and e.get(k) else e.get(k)) for k in
            ("id", "name", "base_url", "model", "price_in", "price_out", "qps", "api_key")}


@router.get("/endpoints")
async def endpoints_list():
    return [_ep_public(e) for e in config.load_settings()["endpoints"]]


@router.post("/endpoints")
async def endpoints_save(
    id: str = Form(""),
    name: str = Form(...),
    base_url: str = Form(""),
    model: str = Form(...),
    api_key: str = Form(""),
    price_in: float = Form(0),
    price_out: float = Form(0),
    qps: int = Form(4),
):
    settings = config.load_settings()
    eps = settings.setdefault("endpoints", [])
    if not id:
        rec = {"id": uuid.uuid4().hex[:8]}
        eps.append(rec)
    else:
        rec = next((e for e in eps if e["id"] == id), None)
        if rec is None:
            raise HTTPException(404, "端点不存在")
    rec.update(name=name, base_url=base_url.strip(), model=model.strip(),
               price_in=price_in, price_out=price_out,
               qps=max(1, min(30, int(qps or 4))))
    if api_key and api_key != "******":
        rec["api_key"] = api_key.strip()
    rec.setdefault("api_key", "")
    if not settings.get("default_endpoint"):
        settings["default_endpoint"] = rec["id"]
    config.save_settings(settings)
    return {"ok": True, "id": rec["id"]}


@router.post("/endpoints/{endpoint_id}/default")
async def endpoints_default(endpoint_id: str):
    settings = config.load_settings()
    if not any(e["id"] == endpoint_id for e in settings["endpoints"]):
        raise HTTPException(404, "端点不存在")
    settings["default_endpoint"] = endpoint_id
    config.save_settings(settings)
    return {"ok": True}


@router.post("/endpoints/{endpoint_id}/delete")
async def endpoints_delete(endpoint_id: str):
    settings = config.load_settings()
    settings["endpoints"] = [e for e in settings["endpoints"] if e["id"] != endpoint_id]
    if settings.get("default_endpoint") == endpoint_id:
        settings["default_endpoint"] = settings["endpoints"][0]["id"] if settings["endpoints"] else None
    config.save_settings(settings)
    return {"ok": True}


@router.post("/endpoints/test")
async def endpoints_test(
    id: str = Form(""),
    base_url: str = Form(""),
    model: str = Form(""),
    api_key: str = Form(""),
):
    """连通性测试：发一次最小 chat 请求，分类报告失败原因。

    编辑已存端点时 key 留空/为掩码则用已保存的 key 测试。
    """
    if id:
        saved = next((e for e in config.load_settings()["endpoints"] if e["id"] == id), None)
        if saved:
            base_url = base_url or saved.get("base_url", "")
            model = model or saved.get("model", "")
            if not api_key or api_key == "******":
                api_key = saved.get("api_key", "")
    if not model:
        return {"ok": False, "category": "配置不完整", "detail": "缺少模型名"}
    if not api_key:
        return {"ok": False, "category": "配置不完整", "detail": "缺少 API Key"}

    url = base_url.rstrip("/") + "/chat/completions" if base_url else "https://api.openai.com/v1/chat/completions"
    body = {"model": model,
            "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
            "max_tokens": 10, "temperature": 0}
    headers = {"Authorization": f"Bearer {api_key}"}

    import httpx
    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=10.0)) as client:
            r = await client.post(url, json=body, headers=headers)
    except httpx.ConnectError as exc:
        return {"ok": False, "category": "连接失败",
                "detail": f"无法连接 {url}（网络不通、域名错误或服务未启动）：{exc}"[:300]}
    except httpx.TimeoutException:
        return {"ok": False, "category": "超时",
                "detail": f"{url} 在 25 秒内未响应（网络慢或端点无响应）"}
    except httpx.HTTPError as exc:
        return {"ok": False, "category": "请求异常", "detail": str(exc)[:300]}
    latency = round(time.time() - started, 1)

    if r.status_code in (401, 403):
        return {"ok": False, "category": "鉴权失败", "detail": f"HTTP {r.status_code}：API Key 无效或无权限。原始返回：{r.text[:200]}"}
    if r.status_code == 404:
        return {"ok": False, "category": "URL 不存在", "detail": f"HTTP 404：{url} 路径不存在——Base URL 通常需要以 /v1 结尾。"}
    if r.status_code == 429:
        return {"ok": False, "category": "限流", "detail": "HTTP 429：请求过于频繁或额度耗尽（Key 本身有效）。"}

    if r.status_code >= 400:
        # 常见：模型名不存在、余额不足等，尽力解析 OpenAI 风格错误体
        try:
            err = r.json().get("error", {})
            msg = err.get("message") or r.text[:200]
        except Exception:
            msg = r.text[:200]
        return {"ok": False, "category": f"HTTP {r.status_code}", "detail": msg}

    try:
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return {"ok": True, "category": "连接成功", "latency": latency,
                "detail": f"模型 {data.get('model', model)} 返回：{content.strip()[:60]!r}",
                "usage": {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}}
    except Exception:
        return {"ok": False, "category": "响应异常",
                "detail": f"HTTP {r.status_code} 但返回不是 OpenAI 兼容格式：{r.text[:200]}"}


# ---------- 术语表 ----------

@router.get("/terms")
async def terms_list():
    return [dict(r) for r in db.list_terms()]


@router.post("/terms")
async def terms_add(source: str = Form(...), target: str = Form(...), category: str = Form("")):
    if not source.strip() or not target.strip():
        raise HTTPException(400, "原文与译文不能为空")
    db.add_term(source, target, category)
    return {"ok": True}


@router.post("/terms/{term_id}/delete")
async def terms_delete(term_id: int):
    db.delete_term(term_id)
    return {"ok": True}


@router.get("/terms/export")
async def terms_export():
    rows = db.list_terms()
    out = config.DATA_DIR / f"terms_export_{uuid.uuid4().hex[:6]}.csv"
    from ..engine import export_glossary_csv
    export_glossary_csv(out, [{"source": r["source"], "target": r["target"]} for r in rows])
    return FileResponse(out, filename="glossary.csv", media_type="text/csv")
