"""SQLite 持久层：任务表 + 术语表。同步 sqlite3 + 全局锁（自用单进程规模足够）。"""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .config import DATA_DIR

DB_PATH = DATA_DIR / "app.db"
_lock = threading.RLock()  # 可重入：execute() 持锁时 get_conn() 会再次取锁
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    endpoint_id TEXT,
    endpoint_name TEXT,
    model TEXT,
    pages_spec TEXT,
    no_mono INTEGER DEFAULT 0,
    use_glossary INTEGER DEFAULT 1,
    dry_run INTEGER DEFAULT 0,
    progress REAL DEFAULT 0,
    message TEXT DEFAULT '',
    mono_path TEXT,
    dual_path TEXT,
    coverage TEXT,
    usage TEXT,
    error TEXT,
    created_at REAL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    category TEXT DEFAULT '',
    is_custom INTEGER DEFAULT 0,
    created_at REAL,
    UNIQUE(source, is_custom)
);
"""


def get_conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(_SCHEMA)
            _migrate(_conn)
            _conn.commit()
        return _conn


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    if "protect_toc" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN protect_toc INTEGER DEFAULT 1")
    if "skipped_pages" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN skipped_pages TEXT")
    if "pages" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN pages INTEGER")


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        cur = get_conn().execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows


def execute(sql: str, params: tuple = ()) -> int:
    with _lock:
        cur = get_conn().execute(sql, params)
        get_conn().commit()
        return cur.lastrowid


# ---------- 任务 ----------

def create_task(filename: str, stored_path: Path, endpoint: dict, pages_spec: str | None,
                no_mono: bool, use_glossary: bool, dry_run: bool = False,
                protect_toc: bool = True, pages: int | None = None) -> str:
    task_id = uuid.uuid4().hex[:12]
    execute(
        "INSERT INTO tasks (id, filename, stored_path, status, endpoint_id, endpoint_name, model,"
        " pages_spec, no_mono, use_glossary, dry_run, protect_toc, pages, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (task_id, filename, str(stored_path), "pending", endpoint.get("id", ""), endpoint.get("name", ""),
         endpoint.get("model", ""), pages_spec, int(no_mono), int(use_glossary), int(dry_run),
         int(protect_toc), pages, time.time()),
    )
    return task_id


def update_task(task_id: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE tasks SET {cols} WHERE id=?", (*fields.values(), task_id))


def get_task(task_id: str) -> sqlite3.Row | None:
    rows = query("SELECT * FROM tasks WHERE id=?", (task_id,))
    return rows[0] if rows else None


def list_tasks(limit: int = 100) -> list[sqlite3.Row]:
    return query("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))


# ---------- 术语表 ----------

def add_term(source: str, target: str, category: str = "", is_custom: bool = True) -> None:
    execute(
        "INSERT INTO terms (source, target, category, is_custom, created_at) VALUES (?,?,?,?,?)"
        " ON CONFLICT(source, is_custom) DO UPDATE SET target=excluded.target, category=excluded.category",
        (source.strip(), target.strip(), category.strip(), int(is_custom), time.time()),
    )


def delete_term(term_id: int) -> None:
    execute("DELETE FROM terms WHERE id=?", (term_id,))


def list_terms() -> list[sqlite3.Row]:
    return query("SELECT * FROM terms ORDER BY is_custom DESC, source COLLATE NOCASE")
