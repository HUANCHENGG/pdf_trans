"""串行任务队列：asyncio.Queue + 单 worker，进度存内存供 SSE 轮询，状态落库。"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from . import config, db, engine, coverage

queue: asyncio.Queue = asyncio.Queue()
_states: dict[str, dict] = {}          # task_id -> 状态快照（含监控数据）
_running: dict[str, asyncio.Task] = {} # task_id -> asyncio.Task（供取消）
_started = False
_started_at = time.time()


class CancelRequested(engine.ProgressAbort):
    """协作式取消信号：由进度回调抛出，在引擎检查点退出任务。"""


def snapshot(task_id: str) -> dict:
    """任务状态快照。无内存记录时返回空 dict（不污染 DB 状态，如服务重启后）。"""
    st = _states.get(task_id)
    if st is None:
        return {}
    out = {k: v for k, v in st.items() if k not in ("block_hist", "last_block")}
    if st.get("status") == "translating":
        now = time.time()
        if st.get("start_ts"):
            out["elapsed"] = round(now - st["start_ts"], 1)
        let = st.get("last_event_ts")
        if let:
            out["last_event_ago"] = round(now - let, 1)
        # 块速：按最近窗口内的块号推进速度估算当前阶段剩余时间
        hist = st.get("block_hist") or []
        if len(hist) >= 2:
            (t1, b1), (t2, b2) = hist[0], hist[-1]
            if t2 > t1 and b2 > b1:
                bpm = (b2 - b1) / (t2 - t1) * 60
                out["speed_bpm"] = round(bpm, 1)
                tot = st.get("stage_total")
                if isinstance(tot, (int, float)) and tot > b2 and bpm > 0:
                    out["remain_seconds"] = round((tot - b2) / bpm * 60, 1)
    return out


def _set_state(task_id: str, status: str | None = None, progress: float | None = None,
               message: str | None = None) -> None:
    st = _states.setdefault(task_id, {"status": "pending", "progress": 0, "message": ""})
    if status is not None:
        st["status"] = status
        if status == "translating" and not st.get("start_ts"):
            st["start_ts"] = time.time()
        if status in ("done", "failed", "cancelled") and st.get("start_ts"):
            st["elapsed"] = round(time.time() - st["start_ts"], 1)
    if progress is not None:
        st["progress"] = max(st.get("progress", 0), progress)
    if message is not None:
        st["message"] = message


def _record_block(task_id: str, block, stage_total) -> None:
    """记录引擎翻译块号推进，用于块速与"最后活动"监控。"""
    st = _states.setdefault(task_id, {})
    now = time.time()
    st["last_event_ts"] = now
    if isinstance(stage_total, (int, float)) and stage_total > 0:
        st["stage_total"] = stage_total
    if isinstance(block, (int, float)) and block != st.get("last_block"):
        hist = st.setdefault("block_hist", [])
        hist.append((now, block))
        cutoff = now - 300  # 块速滑动窗口 5 分钟
        while hist and hist[0][0] < cutoff:
            hist.pop(0)
        st["last_block"] = block


def service_stats() -> dict:
    """服务级监控（只读，不影响翻译）。"""
    active = [{"task_id": tid, **snapshot(tid)}
              for tid, st in _states.items()
              if st.get("status") in ("pending", "translating")]
    return {"uptime": round(time.time() - _started_at, 1),
            "queue_size": queue.qsize(),
            "active_tasks": active}
    if st.get("start_ts") and st["status"] == "translating":
        st["elapsed"] = round(time.time() - st["start_ts"], 1)


async def start_worker() -> None:
    global _started
    if _started:
        return
    _started = True
    asyncio.create_task(_worker())


async def _worker() -> None:
    while True:
        task_id = await queue.get()
        try:
            await _run_task(task_id)
        except CancelRequested:
            db.update_task(task_id, status="cancelled",
                           message="已取消", finished_at=time.time())
            _set_state(task_id, status="cancelled", message="已取消")
        except asyncio.CancelledError:
            _set_state(task_id, status="cancelled", message="已取消")
            db.update_task(task_id, status="cancelled", finished_at=time.time())
        except Exception as exc:
            _set_state(task_id, status="failed", message=str(exc)[:500])
            db.update_task(task_id, status="failed", error=str(exc)[:2000], finished_at=time.time())
        finally:
            _running.pop(task_id, None)
            queue.task_done()


def submit(task_id: str) -> None:
    _set_state(task_id, status="pending", progress=0, message="排队中")
    _running[task_id] = asyncio.create_task(_delayed(task_id))


async def _delayed(task_id: str) -> None:
    await queue.put(task_id)


def mark_cancelled(task_id: str) -> None:
    """任务尚未开始执行时的取消：置标志防止 worker 稍后启动它。"""
    _states.setdefault(task_id, {})["cancel_requested"] = True
    _set_state(task_id, status="cancelled", message="已取消")


async def cancel(task_id: str) -> bool:
    """协作式取消：置标志，任务在下一个引擎进度检查点退出（asyncio.cancel 会被
    引擎内部资源清理阻塞，实测传播超过 30 秒，弃用）。"""
    t = _running.get(task_id)
    if not t or t.done():
        return False
    st = _states.setdefault(task_id, {})
    st["cancel_requested"] = True
    st["message"] = "正在取消…（等待引擎到达检查点，通常几秒内）"
    return True


def _extract_progress(event: dict) -> tuple[float | None, str | None]:
    """解析 babeldoc 进度事件。overall_progress 恒为 0~100 浮点。"""
    p = event.get("overall_progress")
    if p is None:
        return None, None
    try:
        p = float(p)
    except (TypeError, ValueError):
        return None, None
    p = max(0.0, min(100.0, p))

    parts: list[str] = []
    stage = (event.get("stage") or "").strip()
    if stage:
        parts.append(stage)
    cur, tot = event.get("stage_current"), event.get("stage_total")
    if isinstance(cur, (int, float)) and isinstance(tot, (int, float)) and tot > 0:
        parts.append(f"第 {int(cur)}/{int(tot)} 块")
    tp = event.get("total_parts")
    if isinstance(tp, (int, float)) and tp > 1:
        pi = event.get("part_index")
        parts.append(f"分片 {int(pi) if isinstance(pi, (int, float)) else '?'}/{int(tp)}")
    return p, (" ".join(parts) if parts else None)


async def _run_task(task_id: str) -> None:
    if _states.get(task_id, {}).get("cancel_requested"):
        raise CancelRequested()
    task = db.get_task(task_id)
    if task is None:
        return
    settings = config.load_settings()
    endpoint = next((e for e in settings["endpoints"] if e["id"] == task["endpoint_id"]), None)

    db.update_task(task_id, status="translating")
    _set_state(task_id, status="translating", progress=0, message="准备中")

    input_path = Path(task["stored_path"])
    out_dir = config.OUTPUT_DIR / task_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 目录页处理：检测 → 从引擎范围剔除 → 条目提取与批量翻译 → 引擎完成后渲染右栏
    from . import toc as toc_mod
    pages_str = task["pages_spec"] or None
    skipped_toc: list[int] = []
    toc_entries: dict[int, list[dict]] = {}
    if task["protect_toc"]:
        toc_pages = toc_mod.detect_toc_pages(input_path)
        if toc_pages:
            total = coverage.analyze_pdf(input_path)["total_pages"]
            allowed = toc_mod.parse_page_spec(task["pages_spec"], total)
            skipped_toc = sorted(set(toc_pages) & allowed)
            final = sorted(allowed - set(toc_pages))
            if final and skipped_toc:
                pages_str = toc_mod.compact_pages(final)
                # 提取条目；译文在引擎翻译前生成（独立于引擎的段落重组，避免目录碎裂）
                try:
                    toc_entries = toc_mod.extract_entries(input_path, skipped_toc)
                except Exception as exc:
                    _set_state(task_id, message=f"目录条目提取失败，目录将保留原文：{str(exc)[:80]}")

    input_report = coverage.analyze_pdf(input_path)
    warnings = input_report["warnings"]
    warn_msg = []
    if warnings["no_text_layer_pages"]:
        warn_msg.append(f"疑似无文字层页: {warnings['no_text_layer_pages']}")
    if warnings["suspect_garbled_pages"]:
        warn_msg.append(f"疑似乱码页: {warnings['suspect_garbled_pages']}")

    # 术语表导出（自定义优先，再预置）
    glossary_csv = None
    terms: list[dict] = []
    if task["use_glossary"]:
        rows = db.list_terms()
        terms = [{"source": r["source"], "target": r["target"]} for r in rows]
        if terms:
            glossary_csv = engine.export_glossary_csv(
                config.GLOSSARY_DIR / f"task_{task_id}.csv", terms)

    # 目录条目翻译阶段（引擎前）：结构化编号协议，独立于引擎段落重组
    dry_run = bool(task["dry_run"]) or endpoint is None
    toc_translations: dict[str, str] = {}
    toc_stats: dict = {}
    if toc_entries:
        n_all = sum(len(v) for v in toc_entries.values())
        _set_state(task_id, progress=0.5, message=f"翻译目录条目 0/{n_all}")

        def _on_toc(done: int, total_n: int) -> None:
            _set_state(task_id, progress=min(6.0, done / total_n * 6.0),
                       message=f"翻译目录条目 {done}/{total_n}")

        def _check_cancel() -> None:
            if _states.get(task_id, {}).get("cancel_requested"):
                raise CancelRequested()

        try:
            if dry_run:
                # 管线测试不调 API：占位译文用于验证渲染布局
                toc_translations = {e["id"]: f"〔目录〕{e['title'][:24]}"
                                    for es in toc_entries.values() for e in es}
            else:
                toc_translations, toc_stats = await toc_mod.translate_entries(
                    toc_entries,
                    base_url=endpoint.get("base_url", ""),
                    api_key=endpoint.get("api_key", ""),
                    model=endpoint.get("model", ""),
                    terms=terms,
                    qps=int(endpoint.get("qps") or config.DEFAULT_QPS),
                    on_progress=_on_toc, check_cancel=_check_cancel)
        except CancelRequested:
            raise
        except Exception as exc:
            _set_state(task_id, message=f"目录条目翻译失败（目录保留原文）：{str(exc)[:80]}")

    req = engine.TranslateRequest(
        input_path=input_path,
        output_dir=out_dir,
        base_url=(endpoint or {}).get("base_url", ""),
        api_key=(endpoint or {}).get("api_key", ""),
        model=(endpoint or {}).get("model", ""),
        pages=pages_str,
        no_mono=bool(task["no_mono"]),
        glossary_csv=glossary_csv,
        qps=int((endpoint or {}).get("qps") or config.DEFAULT_QPS),
        dry_run=dry_run,
    )

    async def on_progress(event: dict) -> None:
        if _states.get(task_id, {}).get("cancel_requested"):
            raise CancelRequested()
        p, stage = _extract_progress(event)
        _set_state(task_id, progress=p, message=stage)
        _record_block(task_id, event.get("stage_current"), event.get("stage_total"))

    result = await engine.translate(req, on_progress)

    coverage_data = None
    outputs = [p for p in (result.mono_path, result.dual_path) if p]
    if outputs:
        target = result.dual_path or result.mono_path
        coverage_data = coverage.compare(input_report, target)
        # 中文页面覆盖率（信息指标：试翻部分页时天然偏低）
        try:
            coverage_data["chinese_coverage"] = coverage.chinese_page_coverage(target)
        except Exception:
            pass

    # 目录译文渲染（引擎后处理）：双语版右栏 + 纯译文版原坐标重绘
    toc_rendered = 0
    if toc_entries and toc_translations:
        for path, mode in ((result.dual_path, "dual"), (result.mono_path, "mono")):
            if not path:
                continue
            try:
                _set_state(task_id, message=f"渲染中文目录（{mode}）…")
                r = toc_mod.render_toc_translation(str(path), toc_entries, toc_translations,
                                                   mode=mode)
                if mode == "dual":
                    toc_rendered = r["rendered"]
            except CancelRequested:
                raise
            except Exception as exc:
                warn_msg.append(f"目录渲染失败[{mode}]（目录保留原文）：{str(exc)[:80]}")

    if skipped_toc:
        coverage_data = coverage_data or {}
        coverage_data["skipped_toc_pages"] = skipped_toc
        coverage_data["toc_protected"] = True
        if toc_rendered:
            coverage_data["toc_translated_entries"] = toc_rendered
        if toc_stats:
            coverage_data["toc_validation"] = toc_stats
            if toc_stats.get("failed"):
                warn_msg.append(
                    f"目录校验：{toc_stats['failed']} 条未能译为中文（含豁免 "
                    f"{toc_stats['exempt']} 条），已保留原文对照")

    usage = dict(result.usage or {})
    usage["rate_limit_hits"] = result.rate_limit_hits
    if result.rate_limit_hits > 50:
        warn_msg.append(
            f"API 限流严重（429 ×{result.rate_limit_hits}）：端点 QPS 超过供应商 RPM 限额，"
            "请在「API 端点」页调低 QPS（建议 4-8 起步）后用原参数重试——已翻内容走缓存不会重算")
    if endpoint:
        pin = float(endpoint.get("price_in") or 0)
        pout = float(endpoint.get("price_out") or 0)
        usage["estimated_cost"] = round(
            (usage.get("prompt_tokens", 0) / 1e6) * pin
            + (usage.get("completion_tokens", 0) / 1e6) * pout, 4)

    done_msg = "完成"
    if skipped_toc:
        done_msg += f"；目录页 {toc_mod.compact_pages(set(skipped_toc))}"
        done_msg += f" 已译中文目录（{toc_rendered} 条）" if toc_rendered else " 保留原文"
    if warn_msg:
        done_msg += "；" + "；".join(warn_msg)

    db.update_task(
        task_id,
        status="done",
        progress=100,
        message=done_msg,
        mono_path=str(result.mono_path) if result.mono_path else None,
        dual_path=str(result.dual_path) if result.dual_path else None,
        coverage=json.dumps(coverage_data, ensure_ascii=False) if coverage_data else None,
        usage=json.dumps(usage, ensure_ascii=False),
        skipped_pages=json.dumps(skipped_toc) if skipped_toc else None,
        finished_at=time.time(),
    )
    _set_state(task_id, status="done", progress=100, message=done_msg)


def new_upload_path(filename: str) -> Path:
    return config.UPLOAD_DIR / f"{uuid.uuid4().hex[:12]}.pdf"
