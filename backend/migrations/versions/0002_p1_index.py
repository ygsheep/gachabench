# -*- coding: utf-8 -*-
"""P1：资产索引新表、目录 mtime、FTS 与 op_log。

全部幂等，可对已有工作区库直接执行。
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS index_assets (
          id            TEXT PRIMARY KEY,
          path          TEXT NOT NULL UNIQUE,
          name          TEXT NOT NULL,
          dir_path      TEXT NOT NULL DEFAULT '',
          role          TEXT NOT NULL DEFAULT 'other',
          subject       TEXT NOT NULL DEFAULT '',
          category      TEXT NOT NULL DEFAULT 'other',
          ext           TEXT NOT NULL DEFAULT '',
          size          INTEGER NOT NULL DEFAULT 0,
          mtime         REAL NOT NULL DEFAULT 0,
          sha256        TEXT,
          hash_status   TEXT NOT NULL DEFAULT 'full',
          media_kind    TEXT NOT NULL DEFAULT 'other',
          width         INTEGER,
          height        INTEGER,
          duration      REAL,
          fps           REAL,
          status        TEXT NOT NULL DEFAULT 'active',
          missing_at    TEXT,
          created_at    TEXT NOT NULL DEFAULT '',
          updated_at    TEXT NOT NULL DEFAULT ''
        )
        """
    )
    for name, cols in (
        ("ix_index_assets_role", "role"),
        ("ix_index_assets_category", "category"),
        ("ix_index_assets_subject", "subject"),
        ("ix_index_assets_status", "status"),
        ("ix_index_assets_dir", "dir_path"),
        ("ix_index_assets_sha", "sha256"),
    ):
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON index_assets({cols})")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_meta (
          asset_id         TEXT PRIMARY KEY,
          tags_json        TEXT NOT NULL DEFAULT '[]',
          aliases_json     TEXT NOT NULL DEFAULT '[]',
          note             TEXT NOT NULL DEFAULT '',
          subject_override TEXT,
          updated_at       TEXT NOT NULL DEFAULT '',
          FOREIGN KEY (asset_id) REFERENCES index_assets(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_ref (
          id         TEXT PRIMARY KEY,
          asset_id   TEXT NOT NULL,
          file_path  TEXT NOT NULL,
          line       INTEGER,
          col        INTEGER,
          kind       TEXT NOT NULL DEFAULT 'mention',
          segment    TEXT,
          created_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_asset_ref_asset ON asset_ref(asset_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_asset_ref_file ON asset_ref(file_path)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS collections (
          id          TEXT PRIMARY KEY,
          name        TEXT NOT NULL,
          kind        TEXT NOT NULL DEFAULT 'manual',
          filter_json TEXT NOT NULL DEFAULT '{}',
          builtin     INTEGER NOT NULL DEFAULT 0,
          created_at  TEXT NOT NULL DEFAULT ''
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS collection_items (
          collection_id TEXT NOT NULL,
          asset_id      TEXT NOT NULL,
          PRIMARY KEY (collection_id, asset_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_runs (
          id          TEXT PRIMARY KEY,
          workspace   TEXT NOT NULL DEFAULT '',
          started_at  TEXT NOT NULL DEFAULT '',
          finished_at TEXT,
          status      TEXT NOT NULL DEFAULT 'running',
          stats_json  TEXT NOT NULL DEFAULT '{}',
          force       INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS index_dirs (
          path       TEXT PRIMARY KEY,
          mtime_ns   INTEGER NOT NULL DEFAULT 0,
          scanned_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS op_log (
          id           INTEGER PRIMARY KEY AUTOINCREMENT,
          kind         TEXT NOT NULL DEFAULT '',
          plan_id      TEXT,
          payload_json TEXT NOT NULL DEFAULT '{}',
          result       TEXT NOT NULL DEFAULT 'ok',
          created_at   TEXT NOT NULL DEFAULT '',
          seq          INTEGER,
          prev_hash    TEXT,
          op_hash      TEXT,
          actor        TEXT
        )
        """
    )

    # trigram 分词依赖 SQLite 版本；失败就不建 FTS，搜索退回 LIKE。
    try:
        op.get_bind().exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS asset_fts USING fts5("
            "asset_id UNINDEXED, name, subject, tags, aliases, tokenize='trigram')"
        )
    except Exception:
        pass


def downgrade() -> None:
    for table in (
        "collection_items", "collections", "asset_ref", "asset_meta",
        "scan_runs", "index_dirs", "index_assets",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP TABLE IF EXISTS asset_fts")
    op.execute("DROP TABLE IF EXISTS op_log")
