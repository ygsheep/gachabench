# -*- coding: utf-8 -*-
"""baseline：现有 6 张旧表。

对已有库是幂等的：CREATE TABLE IF NOT EXISTS + PRAGMA 判列。
不重建、不搬数据，旧功能继续读写这些表。
"""
from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _add_column_if_missing(table: str, name: str, decl: str) -> None:
    bind = op.get_bind()
    cols = {row[1] for row in bind.exec_driver_sql(f"PRAGMA table_info({table})")}
    if cols and name not in cols:
        op.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def upgrade() -> None:
    op.execute(
        """
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
          prompt_snapshot TEXT,
          params_json  TEXT,
          duration     REAL,
          width        INTEGER,
          height       INTEGER,
          has_audio    INTEGER,
          created_at   TEXT,
          updated_at   TEXT,
          UNIQUE(workspace, rel_path)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS split_plans (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          workspace TEXT,
          seg TEXT,
          strategy TEXT,
          source_md TEXT,
          manifest_json TEXT,
          created_at TEXT
        )
        """
    )
    op.execute(
        """
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
        )
        """
    )
    op.execute(
        """
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
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS books (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          workspace   TEXT NOT NULL,
          title       TEXT NOT NULL,
          author      TEXT DEFAULT '',
          source      TEXT DEFAULT '',
          dir_rel     TEXT NOT NULL,
          created_at  TEXT,
          UNIQUE(workspace, dir_rel)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chapters (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          book_id INTEGER NOT NULL,
          idx INTEGER NOT NULL,
          title TEXT NOT NULL,
          rel_path TEXT NOT NULL,
          word_count INTEGER DEFAULT 0,
          UNIQUE(book_id, idx)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_assets_ws ON assets(workspace)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_assets_subject ON assets(workspace, subject)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_books_ws ON books(workspace)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chapters_book ON chapters(book_id)")

    # 旧库可能缺这几列（旧 db.py 在连接时补；迁移里也补一次，保证 stamp 后结构一致）
    for name, decl in (
        ("progress", "INTEGER DEFAULT 0"),
        ("stage", "TEXT"),
        ("prompt_snapshot", "TEXT"),
        ("params_json", "TEXT"),
    ):
        _add_column_if_missing("jobs", name, decl)
    for name, decl in (("prompt_snapshot", "TEXT"), ("params_json", "TEXT")):
        _add_column_if_missing("cards", name, decl)


def downgrade() -> None:
    # baseline 对应旧库本身，降级不删旧表。
    pass
