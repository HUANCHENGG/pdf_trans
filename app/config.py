"""应用配置：路径、限额、鉴权 token、API 端点档案。

端点（base_url/model/key/单价）保存在 data/settings.json，Web 界面可增删改。
API key 为明文存储（自用工具的已知取舍，见 PROJECT_PLAN 风险表 P3）。
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PDFTRANS_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
GLOSSARY_DIR = DATA_DIR / "glossary"
SETTINGS_FILE = DATA_DIR / "settings.json"

MAX_UPLOAD_MB = int(os.environ.get("PDFTRANS_MAX_UPLOAD_MB", "200"))
# 不再限制最大页数；该值仅作为"大文档"提示阈值（超过时在任务页提示耗时可能较久）
LARGE_DOC_PAGES = int(os.environ.get("PDFTRANS_LARGE_DOC_PAGES", "500"))
DEFAULT_QPS = int(os.environ.get("PDFTRANS_QPS", "4"))

for _d in (DATA_DIR, UPLOAD_DIR, OUTPUT_DIR, GLOSSARY_DIR):
    _d.mkdir(parents=True, exist_ok=True)

_settings_lock = threading.Lock()


def load_settings() -> dict:
    with _settings_lock:
        if SETTINGS_FILE.exists():
            try:
                return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"endpoints": [], "default_endpoint": None}


def save_settings(settings: dict) -> None:
    with _settings_lock:
        tmp = SETTINGS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_FILE)


def _resolve_token() -> str:
    """共享 token：环境变量优先；否则生成并落盘，控制台首次启动时告知。"""
    env = os.environ.get("PDFTRANS_TOKEN")
    if env:
        return env
    token_file = DATA_DIR / "token.txt"
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(24)
    token_file.write_text(token, encoding="utf-8")
    return token


APP_TOKEN = _resolve_token()
