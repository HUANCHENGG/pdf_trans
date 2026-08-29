"""覆盖度报告（红线 6：防静默失败）。

对输入 PDF 逐页统计：可提取文本量、图片对象数、疑似扫描页（几乎无文字层）、
疑似乱码页（替换字符/私用区占比过高）。对输出 PDF 统计页数与图片总数，
量化双语版式造成的页面重排（L3）并做图片数量对账（红线 2）。
"""
from __future__ import annotations

import pymupdf  # 官方新名，fitz 为弃用别名


def analyze_pdf(path, text_threshold: int = 20) -> dict:
    doc = pymupdf.open(str(path))
    pages = []
    total_images = 0
    suspicious_scan, suspicious_garbled = [], []
    for i, page in enumerate(doc, start=1):
        text = page.get_text("text") or ""
        n_imgs = len(page.get_images(full=True))
        total_images += n_imgs
        # 乱码判定：替换字符或私用区字符占比
        private = sum(1 for ch in text if 0xE000 <= ord(ch) <= 0xF8FF or ord(ch) == 0xFFFD)
        garbled_ratio = (private / len(text)) if text else 0.0
        entry = {
            "page": i,
            "text_chars": len(text.strip()),
            "images": n_imgs,
        }
        if len(text.strip()) < text_threshold and n_imgs > 0:
            entry["flag"] = "no_text_layer"
            suspicious_scan.append(i)
        elif garbled_ratio > 0.05:
            entry["flag"] = "suspect_garbled"
            entry["garbled_ratio"] = round(garbled_ratio, 3)
            suspicious_garbled.append(i)
        pages.append(entry)
    doc.close()
    return {
        "total_pages": len(pages),
        "total_images": total_images,
        "pages": pages,
        "warnings": {
            "no_text_layer_pages": suspicious_scan,
            "suspect_garbled_pages": suspicious_garbled,
        },
    }


def chinese_page_coverage(pdf_path) -> dict:
    """统计输出 PDF 的中文页面覆盖率：含中文文本行的页数占比。"""
    doc = pymupdf.open(str(pdf_path))
    with_zh = 0
    try:
        for page in doc:
            for line in page.get_text("text").splitlines():
                if any("\u4e00" <= ch <= "\u9fff" for ch in line):
                    with_zh += 1
                    break
        return {"pages_total": doc.page_count, "pages_with_chinese": with_zh,
                "ratio": round(with_zh / doc.page_count, 3) if doc.page_count else 0}
    finally:
        doc.close()


def compare(input_report: dict, output_path) -> dict:
    """输出对账：页数变化（L3 重排）+ 图片数量对账（红线 2）。"""
    out = analyze_pdf(output_path)
    img_in, img_out = input_report["total_images"], out["total_images"]
    page_in, page_out = input_report["total_pages"], out["total_pages"]
    return {
        "input": input_report,
        "output": {k: out[k] for k in ("total_pages", "total_images")},
        "page_growth": page_out - page_in,
        "image_diff": img_out - img_in,
        "image_integrity_ok": img_out >= img_in,
    }
