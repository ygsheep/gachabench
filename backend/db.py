# -*- coding: utf-8 -*-
"""SQLite：每个工作区一个持久连接（进程内单例） + 一个锁，WAL + busy_timeout。"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace    TEXT NOT NULL,
  rel_path     TEXT NOT NULL,
  seg          TEXT,
  chunk        TEXT,
  status       TEXT NOT NULL DEFAULT 'draft',
  reject_tags  TEXT DEFAULT '[]',
  reject_note  TEXT DEFAULT '',
  tags         TEXT DEFAULT '[]',
  note         TEXT DEFAULT '',
  prompt_md    TEXT,
  split_plan_id INTEGER,
  -- 跑片当时的快照：提示词正文 + 参数，事后能回答「这张卡到底是什么跑出来的」
  prompt_snapshot TEXT,
  params_json  TEXT,
  duration     REAL,
  width        INTEGER,
  height       INTEGER,
  has_audio    INTEGER,
  created_at   TEXT,
  updated_at   TEXT,
  UNIQUE(workspace, rel_path)
);

CREATE TABLE IF NOT EXISTS split_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace TEXT,
  seg TEXT,
  strategy TEXT,
  source_md TEXT,
  manifest_json TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace TEXT,
  seg TEXT,
  chunk TEXT,
  prompt_file TEXT,
  comfy_prompt_id TEXT,
  status TEXT,
  log_tail TEXT,
  progress INTEGER DEFAULT 0,
  stage TEXT,
  prompt_snapshot TEXT,
  params_json TEXT,
  created_at TEXT,
  updated_at TEXT
);

-- 资产索引：递归扫描 {asset} 目录后的完整记录（目录 + 文件名 + 元信息）
CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace   TEXT NOT NULL,
  rel_path    TEXT NOT NULL,
  name        TEXT NOT NULL,
  dir_rel     TEXT DEFAULT '',
  subject     TEXT,
  tags        TEXT DEFAULT '[]',
  category    TEXT,
  kind        TEXT,
  ext         TEXT,
  size        INTEGER,
  mtime       REAL,
  updated_at  TEXT,
  UNIQUE(workspace, rel_path)
);

CREATE INDEX IF NOT EXISTS idx_assets_ws ON assets(workspace);
CREATE INDEX IF NOT EXISTS idx_assets_subject ON assets(workspace, subject);

-- 本地原著 / 剧本库（C2）：书在盘上（{工作区}/原著/<书名>/NNNN 标题.md），
-- 库里只存索引。跟着工作区一起拷走，不建全局库。
CREATE TABLE IF NOT EXISTS books (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace   TEXT NOT NULL,
  title       TEXT NOT NULL,
  author      TEXT DEFAULT '',
  source      TEXT DEFAULT '',
  dir_rel     TEXT NOT NULL,
  created_at  TEXT,
  UNIQUE(workspace, dir_rel)
);

CREATE TABLE IF NOT EXISTS chapters (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id     INTEGER NOT NULL,
  idx         INTEGER NOT NULL,
  title       TEXT NOT NULL,
  rel_path    TEXT NOT NULL,
  word_count  INTEGER DEFAULT 0,
  UNIQUE(book_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_books_ws ON books(workspace);
CREATE INDEX IF NOT EXISTS idx_chapters_book ON chapters(book_id);
"""

# 老库补列：SQLite 不支持 ADD COLUMN IF NOT EXISTS，用 PRAGMA 判存在
_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "jobs": [
        ("progress", "INTEGER DEFAULT 0"),
        ("stage", "TEXT"),
        ("prompt_snapshot", "TEXT"),
        ("params_json", "TEXT"),
    ],
    "cards": [
        ("prompt_snapshot", "TEXT"),
        ("params_json", "TEXT"),
    ],
}


@dataclass
class Db:
    conn: sqlite3.Connection
    lock: threading.Lock
    root: str


# 进程级单例：key = 工作区根绝对路径
_db_registry: dict[str, Db] = {}
_registry_guard = threading.Lock()


def get_db(ws_root: Path) -> Db:
    """打开（必要时创建）工作区数据库并缓存，返回线程安全句柄。"""
    ws_root = ws_root.resolve()
    key = str(ws_root)
    with _registry_guard:
        if key in _db_registry:
            return _db_registry[key]
        index_dir = ws_root / ".gachabench"
        index_dir.mkdir(parents=True, exist_ok=True)
        db_path = index_dir / "cards.db"
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        _apply_migrations(conn)
        conn.commit()
        db = Db(conn=conn, lock=threading.RLock(), root=key)
        _db_registry[key] = db
        return db


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """对已存在的旧表补齐新列（幂等）。"""
    for table, cols in _MIGRATIONS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def close_workspace(ws_root: Path) -> None:
    """关掉某个工作区的连接并从缓存里摘掉（目录要改名 / 要搬走时必须先走这里）。

    Windows 上父目录里只要还有打开的句柄，`Path.rename()` 就是 WinError 5，
    所以「改目录名」这类动作必须先松手，再改完按新路径重新 get_db()。
    """
    key = str(Path(ws_root).resolve())
    with _registry_guard:
        db = _db_registry.pop(key, None)
    if db is not None:
        try:
            db.conn.close()
        except Exception:  # noqa: BLE001 —— 关不掉也不能挡住改名
            pass


def close_all() -> None:
    with _registry_guard:
        for db in _db_registry.values():
            try:
                db.conn.close()
            except Exception:
                pass
        _db_registry.clear()