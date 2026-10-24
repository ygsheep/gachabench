# -*- coding: utf-8 -*-
"""P4：素材提示词 sidecar 与「适用区间」时间线。

两张表，都不动 `index_assets` 的既有语义：

- `asset_prompt`：`X.png` 旁边的 `X.prompt.json` 的**缓存**。文件是真相，
  库只是索引；外部 Agent 写文件、系统只读，所以永不回写。
- `asset_window`：这素材从第几段到第几段有效。**一对多**——修好了又坏了的
  场景会同时拥有多个区间。区间刻意不进文件名：提示词里 `<Picture N>` 直接
  写素材文件名，改名会让所有引用它的段提示词静默失效。
"""
from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_prompt (
          asset_id       TEXT PRIMARY KEY,
          schema_version INTEGER NOT NULL DEFAULT 1,
          subject        TEXT NOT NULL DEFAULT '',
          variant        TEXT NOT NULL DEFAULT '',
          prompt         TEXT NOT NULL DEFAULT '',
          negative       TEXT NOT NULL DEFAULT '',
          caption        TEXT NOT NULL DEFAULT '',
          params_json    TEXT NOT NULL DEFAULT '{}',
          extra_json     TEXT NOT NULL DEFAULT '{}',
          sidecar_path   TEXT NOT NULL DEFAULT '',
          sidecar_mtime  REAL NOT NULL DEFAULT 0,
          created_at     TEXT NOT NULL DEFAULT '',
          updated_at     TEXT NOT NULL DEFAULT ''
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_window (
          id         TEXT PRIMARY KEY,
          asset_id   TEXT NOT NULL,
          seg_from   INTEGER NOT NULL,
          seg_to     INTEGER,
          source     TEXT NOT NULL DEFAULT 'manual',
          note       TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_asset_window_asset ON asset_window(asset_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_asset_window_from ON asset_window(seg_from)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS asset_window")
    op.execute("DROP TABLE IF EXISTS asset_prompt")
