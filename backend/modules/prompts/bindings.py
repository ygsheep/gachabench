# -*- coding: utf-8 -*-
"""绑定的读写。只碰 `prompt_bindings`，不碰资产表，也不碰 md。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from sqlalchemy import delete, select

from modules.platform.db import session_scope
from modules.platform.ids import ulid
from modules.prompts.models import PromptBinding, now_iso

# 占位前缀 → kind，用于人工指定时补全类型
KIND_BY_PREFIX = (
    ("@人物", "person"),
    ("@场景", "scene"),
    ("@道具", "prop"),
    ("@音色", "audio"),
)


def kind_of(placeholder: str) -> str:
    for prefix, kind in KIND_BY_PREFIX:
        if placeholder.startswith(prefix):
            return kind
    return "other"


def load_bindings(root: Path, segment_id: str) -> dict[str, dict[str, Any]]:
    """{placeholder: {file, ignored, name, kind}}"""
    with session_scope(root) as session:
        rows = session.scalars(
            select(PromptBinding).where(PromptBinding.segment_id == segment_id)
        ).all()
        return {
            r.placeholder: {
                "file": r.file,
                "ignored": bool(r.ignored),
                "name": r.name,
                "kind": r.kind or kind_of(r.placeholder),
                "source": r.source,
            }
            for r in rows
        }


def set_binding(
    root: Path,
    segment_id: str,
    placeholder: str,
    file: str = "",
    ignored: bool = False,
    name: str = "",
) -> Optional[dict[str, Any]]:
    """写一条绑定。`file` 与 `ignored` 都没有 = 删除记录（回到自动匹配）。"""
    placeholder = (placeholder or "").strip()
    if not placeholder.startswith("@"):
        raise ValueError("占位必须以 @ 开头")
    kind = kind_of(placeholder)
    with session_scope(root) as session:
        row = session.scalars(
            select(PromptBinding).where(
                PromptBinding.segment_id == segment_id,
                PromptBinding.placeholder == placeholder,
            )
        ).first()

        if not file and not ignored:
            if row is not None:
                session.delete(row)
            return None

        stamp = now_iso()
        if row is None:
            row = PromptBinding(
                id=ulid(),
                segment_id=segment_id,
                placeholder=placeholder,
                kind=kind,
                name=name,
                file=file,
                ignored=1 if ignored else 0,
                source="ignored" if ignored else "manual",
                created_at=stamp,
                updated_at=stamp,
            )
            session.add(row)
        else:
            row.file = file
            row.ignored = 1 if ignored else 0
            row.source = "ignored" if ignored else "manual"
            if name:
                row.name = name
            row.updated_at = stamp
        session.flush()
        return {
            "placeholder": placeholder,
            "file": row.file,
            "ignored": bool(row.ignored),
            "name": row.name,
            "kind": row.kind,
            "source": row.source,
        }


def clear_segment(root: Path, segment_id: str) -> int:
    with session_scope(root) as session:
        result = session.execute(
            delete(PromptBinding).where(PromptBinding.segment_id == segment_id)
        )
        return int(result.rowcount or 0)
