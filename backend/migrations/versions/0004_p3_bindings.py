# -*- coding: utf-8 -*-
"""P3：人工改绑的素材绑定表。

只加一张 `prompt_bindings`：中间层与渲染都是现算的，不需要落库；
但「这一镜的 Q 版小人到底用哪张图」是人的判断，重新解析不该冲掉它。
"""
from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_bindings (
          id          TEXT PRIMARY KEY,
          segment_id  TEXT NOT NULL,
          placeholder TEXT NOT NULL,
          kind        TEXT NOT NULL DEFAULT '',
          name        TEXT NOT NULL DEFAULT '',
          file        TEXT NOT NULL DEFAULT '',
          ignored     INTEGER NOT NULL DEFAULT 0,
          source      TEXT NOT NULL DEFAULT 'manual',
          created_at  TEXT NOT NULL DEFAULT '',
          updated_at  TEXT NOT NULL DEFAULT ''
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_prompt_bindings "
        "ON prompt_bindings(segment_id, placeholder)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS prompt_bindings")
