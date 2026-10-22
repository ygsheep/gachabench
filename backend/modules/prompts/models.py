# -*- coding: utf-8 -*-
"""提示词工程的持久化：只存「人工改绑」这一件事。

中间层（分镜表解析结果）和渲染结果都是**现算**的，md 仍是唯一真相；
唯一需要落库的是人手动指定 / 忽略的素材绑定 —— 那是人的判断，
重新解析分镜表不该把它冲掉。

表按「段 + 占位」唯一：`@人物3` 在 55-4 这一段里只有一条记录。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from modules.platform.db import Base


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PromptBinding(Base):
    __tablename__ = "prompt_bindings"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    # 段 ID，形如 "55-4"（集-段）
    segment_id: Mapped[str] = mapped_column(Text, nullable=False)
    # 占位，形如 "@人物3"
    placeholder: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="")
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 工作区相对路径（POSIX）；空串 = 没指文件（只可能是 ignored）
    file: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ignored: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # manual / ignored —— 自动匹配不进这张表，改了才进来
    source: Mapped[str] = mapped_column(Text, nullable=False, default="manual")
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_iso)

    __table_args__ = (
        UniqueConstraint("segment_id", "placeholder", name="uq_prompt_bindings"),
    )
