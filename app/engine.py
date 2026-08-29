"""BabelDOC 引擎封装（M0 探测结论：走 Python API 路径，async_translate 事件流）。

关键事实（babeldoc 0.6.4）：
- 必需参数：translator / input_file / lang_in / lang_out / doc_layout_model
- pages 为字符串（如 "1-5"），原生支持试翻
- watermark 默认开启，必须显式 NoWatermark
- 术语表：Glossary.from_csv(path, target_lang)，CSV 列 source,target[,tgt_lng]
- 事件流：type ∈ progress/error/finish，finish 携带 TranslateResult(mono/dual path)
- token 用量挂在 translator.token_count / prompt_token_count / completion_token_count
- QPS 超过供应商 RPM 限额时引擎内部 429 重试（tenacity 日志可见），用日志计数器统计
"""
from __future__ import annotations

import asyncio
import csv
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

ProgressCb = Callable[[dict], Awaitable[None]]

_model_lock = threading.Lock()
_layout_model = None


class _RateLimitCounter(logging.Handler):
    """统计引擎 429 限流重试次数（tenacity 经 root logger 输出 RateLimitError）。"""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if "RateLimitError" in record.getMessage():
                self.count += 1
        except Exception:
            pass


def _get_layout_model():
    global _layout_model
    with _model_lock:
        if _layout_model is None:
            from babeldoc.docvision.doclayout import DocLayoutModel
            _layout_model = DocLayoutModel.load_onnx()
        return _layout_model


class EngineError(RuntimeError):
    pass


class ProgressAbort(Exception):
    """由进度回调抛出，用于协作式取消：在下一个引擎事件点退出翻译循环。"""


@dataclass
class TranslateRequest:
    input_path: Path
    output_dir: Path
    lang_in: str = "en"
    lang_out: str = "zh"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    pages: str | None = None        # 如 "1-5"；None 为全本
    no_mono: bool = False           # True 则不输出纯译文版
    no_dual: bool = False
    glossary_csv: Path | None = None
    qps: int = 4
    dry_run: bool = False           # skip_translation：只解析重建，不调 API


@dataclass
class TranslateResult:
    mono_path: Path | None = None
    dual_path: Path | None = None
    usage: dict = field(default_factory=dict)
    elapsed: float = 0.0
    rate_limit_hits: int = 0


def export_glossary_csv(dest: Path, terms: list[dict]) -> Path:
    """把术语表导出为 BabelDOC 可读 CSV（source,target）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source", "target"])
        for t in terms:
            if t.get("source") and t.get("target"):
                w.writerow([t["source"], t["target"]])
    return dest


async def translate(req: TranslateRequest, on_progress: ProgressCb | None = None) -> TranslateResult:
    from babeldoc.format.pdf.high_level import async_translate
    from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
    from babeldoc.translator.translator import OpenAITranslator

    translator = OpenAITranslator(
        lang_in=req.lang_in,
        lang_out=req.lang_out,
        model=req.model or "dry-run",
        base_url=req.base_url or None,
        api_key=req.api_key or "dry-run-placeholder",  # skip_translation 下不发起调用，仅满足 client 构造
        ignore_cache=False,
    )
    config = TranslationConfig(
        translator=translator,
        input_file=req.input_path,
        lang_in=req.lang_in,
        lang_out=req.lang_out,
        doc_layout_model=_get_layout_model(),
        pages=req.pages,
        output_dir=req.output_dir,
        no_dual=req.no_dual,
        no_mono=req.no_mono,
        watermark_output_mode=WatermarkOutputMode.NoWatermark,
        glossaries=None if req.glossary_csv is None else _load_glossary(req.glossary_csv, req.lang_out),
        qps=req.qps,
        skip_translation=req.dry_run,
        use_rich_pbar=False,
        auto_extract_glossary=False,  # 自动术语提取会额外耗 token，v1 关闭，v2 再开
    )

    started = time.monotonic()
    result: TranslateResult | None = None
    current_stage = ""
    counter = _RateLimitCounter()
    root = logging.getLogger()
    root.addHandler(counter)
    try:
        async for event in async_translate(config):
            etype = event.get("type")
            if etype == "progress_start":
                # progress_start 事件自带当前阶段名（权威来源）
                current_stage = str(event.get("stage") or current_stage)
            elif etype == "stage_summary":
                # 事件流开始时的阶段快照：取第一个未完成阶段作初始值
                for s in event.get("stages") or []:
                    if isinstance(s, dict) and s.get("percent", 100) < 100:
                        current_stage = str(s.get("name") or "")
                        break
            if etype == "error":
                raise EngineError(str(event.get("error")))
            if etype == "finish":
                tr = event["translate_result"]
                result = TranslateResult(
                    mono_path=Path(tr.mono_pdf_path) if tr.mono_pdf_path else None,
                    dual_path=Path(tr.dual_pdf_path) if tr.dual_pdf_path else None,
                    elapsed=time.monotonic() - started,
                )
                break  # 引擎不会主动结束流，官方实现收到 finish 即退出循环
            if on_progress is not None:
                payload = {"stage": current_stage,
                           "overall_progress": event.get("overall_progress"),
                           "stage_current": event.get("stage_current"),
                           "stage_total": event.get("stage_total"),
                           "part_index": event.get("part_index"),
                           "total_parts": event.get("total_parts")}
                try:
                    await on_progress(payload)
                except ProgressAbort:
                    raise  # 协作式取消：穿透向上，尽快退出引擎循环
                except Exception:
                    pass
    except asyncio.CancelledError:
        raise
    except (EngineError, ProgressAbort):
        raise
    except Exception as exc:
        raise EngineError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        root.removeHandler(counter)

    if result is None:
        raise EngineError("翻译流程未返回 finish 事件")

    result.rate_limit_hits = counter.count
    result.usage = {
        "total_tokens": translator.token_count.value,
        "prompt_tokens": translator.prompt_token_count.value,
        "completion_tokens": translator.completion_token_count.value,
        "cache_hit_prompt_tokens": translator.cache_hit_prompt_token_count.value,
        "elapsed_seconds": round(result.elapsed, 1),
    }
    return result


def _load_glossary(csv_path: Path, lang_out: str):
    from babeldoc.glossary import Glossary
    return [Glossary.from_csv(csv_path, lang_out)]
