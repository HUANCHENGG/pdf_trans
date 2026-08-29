"""目录页检测、条目提取与页码集合工具。

双语模式下引擎的段落重组会把"短节号行 + 标题点线行"交替的目录结构合并/劈裂
（见 docs/m0-findings.md），且目录页码在重排后必然失真——因此目录不走引擎：
  1. detect_toc_pages: 按点线特征识别目录页
  2. extract_entries:  按 y 坐标聚类行，提取"节号 + 标题 + 页码 + 缩进"结构化条目
  3. parse/compact:    babeldoc pages 串与页码集合互转
条目译文由 tasks 层批量调用 API 生成后，渲染到双语版目录页右栏（格式保留）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf

_TOC_LINE = re.compile(r"[.\u2026]{6,}\s*\d{1,3}\s*$")
_TITLE_PAGE = re.compile(r"^(?P<title>.*?)\s*[.\u2026]{4,}\s*(?P<page>\d{1,3})\s*$")
TOC_MIN_LINES = 8
TOC_MAX_RATIO = 0.5
# 页眉页脚纵向范围（标准 A4 595x842，页眉 ~86，页脚 ~785）
_HEADER_Y = 100.0
_FOOTER_Y = 770.0


def detect_toc_pages(pdf_path: str) -> list[int]:
    """返回 1-based 目录页页码列表（有序）。pdf_path 为文档路径。"""
    doc = pymupdf.open(str(pdf_path))
    hit_pages: list[int] = []
    try:
        total = doc.page_count
        for i in range(total):
            lines = [l.strip() for l in doc[i].get_text("text").splitlines() if l.strip()]
            hits = sum(1 for l in lines if _TOC_LINE.search(l))
            if hits >= TOC_MIN_LINES:
                hit_pages.append(i + 1)
    finally:
        doc.close()
    # 判据失效保护：过半页面都像目录时不可信
    if hit_pages and len(hit_pages) > total * TOC_MAX_RATIO:
        return []
    return hit_pages


def parse_page_spec(spec: str | None, total_pages: int) -> set[int]:
    """解析 babeldoc 风格页码串（"1,3,5-7,-3,10-"）为 1-based 页码集合。"""
    if not spec or not spec.strip():
        return set(range(1, total_pages + 1))
    result: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            s, e = part.split("-", 1)
            start = int(s) if s.strip() else 1
            end = int(e) if e.strip() else total_pages
        else:
            start = end = int(part)
        result.update(range(max(1, start), min(total_pages, end) + 1))
    return result


def compact_pages(pages: set[int]) -> str:
    """把页码集合压缩为 babeldoc pages 串（连续段合并为 a-b）。"""
    if not pages:
        return ""
    nums = sorted(pages)
    parts: list[str] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}-{prev}" if prev > start else str(start))
        start = prev = n
    parts.append(f"{start}-{prev}" if prev > start else str(start))
    return ",".join(parts)


# ---------- 目录条目提取 ----------

def extract_entries(pdf_path: str, page_numbers: list[int]) -> dict[int, list[dict]]:
    """从目录页提取结构化条目。

    返回 {page(1-based): [entry]}，entry 字段：
      sec     节号（如 "4.6.8"，可能为 None）
      title   标题文本（已去除点线与页码）
      page_no 目标页码字符串
      y       行基线附近纵坐标（取行 bbox y0）
      indent  标题起始 x（体现层级缩进）
      sec_x   节号起始 x
      size    字号
      right   页码右对齐 x（原标题行右端）
    """
    doc = pymupdf.open(str(pdf_path))
    result: dict[int, list[dict]] = {}
    try:
        for pno in page_numbers:
            page = doc[pno - 1]
            rows: dict[float, dict] = {}
            d = page.get_text("dict")
            for blk in d["blocks"]:
                if blk["type"] != 0:
                    continue
                for line in blk["lines"]:
                    text = "".join(s["text"] for s in line["spans"]).strip()
                    if not text:
                        continue
                    x0, y0, x1, y1 = line["bbox"]
                    if y0 < _HEADER_Y or y0 > _FOOTER_Y:
                        continue
                    key = round(y0 * 2) / 2  # 0.5pt 聚类
                    size = line["spans"][0]["size"]
                    if _TITLE_PAGE.match(text) and _TOC_LINE.search(text):
                        m = _TITLE_PAGE.match(text)
                        row = rows.setdefault(key, {"y": y0, "size": size})
                        row.update(title=m.group("title").strip().rstrip("."),
                                   page_no=m.group("page"), indent=x0,
                                   right=x1)
                    elif text.replace(".", "").replace(" ", "").isdigit() and len(text) <= 12:
                        # 节号行（4 / 4.6 / 4.6.8）
                        row = rows.setdefault(key, {"y": y0, "size": size})
                        row.setdefault("sec", text)
                        row.setdefault("sec_x", x0)
            entries = []
            for key in sorted(rows):
                row = rows[key]
                if not row.get("title"):
                    continue
                entries.append({
                    "id": f"{pno}:{int(key*10)}",
                    "sec": row.get("sec"),
                    "title": row["title"],
                    "page_no": row["page_no"],
                    "y": row["y"],
                    "indent": row["indent"],
                    "sec_x": row.get("sec_x"),
                    "size": row["size"],
                    "right": row["right"],
                })
            if entries:
                result[pno] = entries
    finally:
        doc.close()
    return result


# ---------- 目录译文渲染（双语版右栏） ----------

def find_cjk_font() -> str | None:
    """优先 babeldoc 下载的思源宋体，回退 Windows 系统字体。"""
    candidates = [
        Path.home() / ".cache" / "babeldoc" / "fonts" / "SourceHanSerifCN-Regular.ttf",
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def render_toc_translation(dual_pdf_path: str, entries_by_page: dict[int, list[dict]],
                           translations: dict[str, str],
                           mode: str = "dual") -> dict:
    """把目录译文渲染到输出 PDF 的目录页。

    dual 模式：双语版页面为 2x 加宽横排，右栏（x≥595）覆盖为白底后绘制中文目录，
    坐标 = 原文坐标 + 595；mono 模式：纯译文版目录页与原 PDF 坐标一致（未翻译页
    原样保留），仅覆盖条目纵向区域（页眉页脚保留），按原坐标重绘中文目录。
    未获得译文的条目跳过。返回渲染统计。
    """
    fontfile = find_cjk_font()
    if fontfile is None:
        raise RuntimeError("未找到可用中文字体")
    font = pymupdf.Font(fontfile=fontfile)  # 用真实字体度量计算点线起点
    doc = pymupdf.open(str(dual_pdf_path))
    drawn = missing = 0
    try:
        for pno, entries in entries_by_page.items():
            page = doc[pno - 1]
            page_w, page_h = page.rect.width, page.rect.height
            if mode == "dual":
                if page_w <= 595.0:
                    continue  # 非加宽页（单语版等）
                col_offset = 595.0
                cover = pymupdf.Rect(col_offset, 0, page_w, page_h)
            else:  # mono
                col_offset = 0.0
                cover = pymupdf.Rect(0, _HEADER_Y, page_w, _FOOTER_Y)
            page.draw_rect(cover, color=None, fill=(1, 1, 1))
            for e in entries:
                text = translations.get(e["id"], "").strip()
                if not text:
                    missing += 1
                    continue
                size = e["size"]
                baseline = e["y"] + size * 0.98  # bbox 顶 → 基线近似
                fname = f"tocfont{pno}"
                if e.get("sec"):
                    page.insert_text((e["sec_x"] + col_offset, baseline), e["sec"],
                                     fontfile=fontfile, fontname=fname, fontsize=size)
                x = e["indent"] + col_offset
                page.insert_text((x, baseline), text,
                                 fontfile=fontfile, fontname=fname + "t", fontsize=size)
                # 点线 + 右对齐页码
                tw = font.text_length(text, fontsize=size)
                page_w_right = e["right"] + col_offset
                pno_w = pymupdf.get_text_length(e["page_no"], fontname="helv", fontsize=size)
                dot_start = min(x + tw + 10, page_w_right - pno_w - 12)
                dots = "." * max(4, int((page_w_right - pno_w - 8 - dot_start) / max(1.0, size * 0.33)))
                page.insert_text((dot_start, baseline), dots + " " + e["page_no"],
                                 fontname="helv", fontsize=size)
                drawn += 1
        tmp = str(dual_pdf_path) + ".toc.pdf"
        doc.save(tmp, garbage=1)
    finally:
        doc.close()
    Path(tmp).replace(dual_pdf_path)
    return {"rendered": drawn, "missing": missing}


# ---------- 目录条目批量翻译（结构化协议，独立于引擎的段落重组） ----------

import asyncio

import httpx

_BATCH = 25
_SYS_PROMPT = (
    "你是电子行业技术文档《数据手册/协议规范》的目录翻译器。逐条翻译目录条目标题："
    "1. 使用中文电子行业惯用术语；2. 保留方括号标记（如 [DEPRECATED] 译为 [已弃用]）；"
    "3. 型号、寄存器名、命令名中不应翻译的部分保留原文；4. 译文尽量简短。"
    "输出格式严格为每行一条：`条目ID<TAB>中文译文`，不要输出任何其他内容。"
)


def _match_terms(titles: list[str], terms: list[dict], limit: int = 60) -> list[dict]:
    """按条目文本命中收集相关术语子集，避免全量注入。"""
    joined = " || ".join(titles).casefold()
    out, seen = [], set()
    for t in terms:
        src = (t.get("source") or "").strip()
        if src and src.casefold() in joined and src.casefold() not in seen:
            out.append({"source": src, "target": t.get("target", "")})
            seen.add(src.casefold())
        if len(out) >= limit:
            break
    return out


# 中文覆盖校验：除约定不翻译的词外，译文必须含中文
_CH = re.compile(r"[\u4e00-\u9fff]")


def build_no_translate_set(terms: list[dict]) -> set[str]:
    """用户术语表中 source==target 的条目视为约定不翻译。"""
    return {t["source"].strip().casefold() for t in terms
            if t.get("source") and t.get("target")
            and t["source"].strip().casefold() == t["target"].strip().casefold()}


def needs_chinese(title: str, no_translate: set[str]) -> bool:
    """判断条目标题是否要求中文译文（False = 豁免）。

    豁免：约定不翻译的术语；纯数字/型号/符号；短全大写缩写串（I2C、SOP-8、GPIO5 等）。
    """
    t = title.strip()
    if not t or t.casefold() in no_translate:
        return False
    if not re.search(r"[A-Za-z]", t):
        return False  # 纯数字/符号
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]*", t)
    if words and all(re.fullmatch(r"[A-Z0-9][A-Z0-9\-]{0,5}", w) for w in words):
        return False  # 全部为短大写缩写/型号
    return True


async def translate_entries(entries_by_page: dict[int, list[dict]],
                            base_url: str, api_key: str, model: str,
                            terms: list[dict] | None = None,
                            qps: int = 4,
                            on_progress=None, check_cancel=None) -> tuple[dict[str, str], dict]:
    """按批翻译目录条目，返回 ({entry_id: 中文标题}, 校验统计)。

    中文覆盖校验（用户红线）：needs_chinese 判定为需翻译的条目，译文不含中文时
    以严格指令重试一次；仍不合格则丢弃该译文（渲染时跳过该条，宁缺毋滥），
    并计入统计.failed。缺失条目同样重试一次。
    """
    items = [(e["id"], e["title"]) for entries in entries_by_page.values() for e in entries]
    if not items:
        return {}, {"total": 0, "translated": 0, "exempt": 0, "failed": 0}
    no_translate = build_no_translate_set(terms or [])
    terms = _match_terms([t for _, t in items], terms or [])
    term_lines = "\n".join(f"{t['source']} = {t['target']}" for t in terms)
    url = (base_url.rstrip("/") + "/chat/completions") if base_url else "https://api.openai.com/v1/chat/completions"
    results: dict[str, str] = {}
    titles = {eid: title for eid, title in items}
    done = 0
    total = len(items)
    delay = 1.0 / max(1, qps)

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        for i in range(0, total, _BATCH):
            if check_cancel is not None:
                check_cancel()
            batch = items[i:i + _BATCH]
            expect = {eid for eid, _ in batch}
            title_map = dict(batch)
            user = (f"术语表（优先采用）：\n{term_lines}\n\n" if term_lines else "") + \
                   "目录条目：\n" + "\n".join(f"{eid}\t{title}" for eid, title in batch)
            payload = {"model": model, "temperature": 0,
                       "messages": [{"role": "system", "content": _SYS_PROMPT},
                                    {"role": "user", "content": user}]}
            strict = False
            for attempt in range(2):  # 首次 + 缺失/非中文严格重试一次
                r = await client.post(url, json=payload,
                                      headers={"Authorization": f"Bearer {api_key}"})
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"]
                for line in text.splitlines():
                    if "\t" in line:
                        eid, _, zh = line.partition("\t")
                    else:
                        eid, _, zh = line.partition(" ")
                    eid = eid.strip().strip("`").rstrip(":： ")
                    zh = zh.strip()
                    if eid in expect and zh:
                        results[eid] = zh
                # 校验：缺失 + （需中文却无中文）
                bad_missing = [eid for eid in expect if eid not in results]
                bad_nonzh = [eid for eid in expect if eid in results
                             and needs_chinese(title_map[eid], no_translate)
                             and not _CH.search(results[eid])]
                for eid in bad_nonzh:
                    results.pop(eid, None)  # 丢弃不合格译文，重试
                todo = bad_missing + bad_nonzh
                if not todo:
                    break
                batch = [(eid, title_map[eid]) for eid in todo]
                expect = {eid for eid, _ in batch}
                strict = bool(bad_nonzh)
                retry_note = ("注意：以下条目上一轮的译文不是中文，必须翻译成完整的中文"
                              "（型号/寄存器名等专有名词除外）：\n" if strict
                              else "上一批以下条目缺失或格式错误，请重新翻译，格式 `条目ID<TAB>译文`：\n")
                payload = dict(payload)
                payload["messages"] = payload["messages"][:1] + [
                    {"role": "user",
                     "content": retry_note + "\n".join(f"{eid}\t{t}" for eid, t in batch)}]
            done += len(items[i:i + _BATCH])
            if on_progress is not None:
                try:
                    on_progress(done, total)
                except Exception:
                    pass
            await asyncio.sleep(delay)

    # 任务级统计
    exempt = translated = failed = 0
    for eid, title in items:
        zh = results.get(eid, "")
        if not needs_chinese(title, no_translate):
            exempt += 1
        elif zh and _CH.search(zh):
            translated += 1
        else:
            failed += 1
            results.pop(eid, None)
    stats = {"total": total, "translated": translated, "exempt": exempt, "failed": failed}
    return results, stats

