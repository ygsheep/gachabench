# -*- coding: utf-8 -*-
"""P2：操作计划与回收站两张表。

全部幂等 `CREATE TABLE IF NOT EXISTS`，可对已有工作区库直接执行。
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_plans (
          id           TEXT PRIMARY KEY,
          kind         TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '{}',
          base_json    TEXT NOT NULL DEFAULT '{}',
          status       TEXT NOT NULL DEFAULT 'planned',
          created_at   TEXT NOT NULL DEFAULT '',
          expires_at   TEXT NOT NULL DEFAULT '',
          applied_at   TEXT,
          result_json  TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_operation_plans_status "
        "ON operation_plans(status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_operation_plans_created "
        "ON operation_plans(created_at)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS trash_entries (
          id            TEXT PRIMARY KEY,
          asset_id      TEXT NOT NULL,
          original_path TEXT NOT NULL,
          trash_path    TEXT NOT NULL,
          deleted_at    TEXT NOT NULL DEFAULT '',
          restored_at   TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_trash_entries_asset "
        "ON trash_entries(asset_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS trash_entries")
    op.execute("DROP TABLE IF EXISTS operation_plans")
