# -*- coding: utf-8 -*-
"""P1 资产索引表模型。

与旧 `assets` 表（INTEGER id、workspace + rel_path）刻意分开：新表叫
`index_assets`，用稳定字符串 ID。旧功能继续读旧表，互不覆盖。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Float, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from modules.platform.db import Base


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class IndexAsset(Base):
    __tablename__ = "index_assets"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    path: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    dir_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    role: Mapped[str] = mapped_column(Text, nullable=False, default="other")
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(Text, nullable=False, default="other")
    ext: Mapped[str] = mapped_column(Text, nullable=False, default="")
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mtime: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sha256: Mapped[str | None] = mapped_column(Text)
    hash_status: Mapped[str] = mapped_column(Text, nullable=False, default="full")
    media_kind: Mapped[str] = mapped_column(Text, nullable=False, default="other")
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[float | None] = mapped_column(Float)
    fps: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    missing_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_iso)


Index("ix_index_assets_role", IndexAsset.role)
Index("ix_index_assets_category", IndexAsset.category)
Index("ix_index_assets_subject", IndexAsset.subject)
Index("ix_index_assets_status", IndexAsset.status)
Index("ix_index_assets_dir", IndexAsset.dir_path)
Index("ix_index_assets_sha", IndexAsset.sha256)


class AssetMeta(Base):
    __tablename__ = "asset_meta"

    asset_id: Mapped[str] = mapped_column(
        Text, ForeignKey("index_assets.id", ondelete="CASCADE"), primary_key=True
    )
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    aliases_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    subject_override: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_iso)


class AssetRef(Base):
    __tablename__ = "asset_ref"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    asset_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    line: Mapped[int | None] = mapped_column(Integer)
    col: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="mention")
    segment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_iso)


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="manual")
    filter_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    builtin: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_iso)


class CollectionItem(Base):
    __tablename__ = "collection_items"

    collection_id: Mapped[str] = mapped_column(Text, primary_key=True)
    asset_id: Mapped[str] = mapped_column(Text, primary_key=True)


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_iso)
    finished_at: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    stats_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    force: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class IndexDir(Base):
    __tablename__ = "index_dirs"

    path: Mapped[str] = mapped_column(Text, primary_key=True)
    mtime_ns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scanned_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_iso)


class OperationPlan(Base):
    """P2 计划 / 应用 / 回滚三态。

    payload_json 存归一化后的请求，base_json 存计划时刻的哈希与 mtime 快照，
    result_json 存计划差异（files/blocked/stats）与 apply 结果。
    """

    __tablename__ = "operation_plans"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    base_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="planned")
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_iso)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False, default="")
    applied_at: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


Index("ix_operation_plans_status", OperationPlan.status)
Index("ix_operation_plans_created", OperationPlan.created_at)


class TrashEntry(Base):
    """回收站条目：文件移到 .gachabench/trash 后保留原路径与时间。"""

    __tablename__ = "trash_entries"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    asset_id: Mapped[str] = mapped_column(Text, nullable=False)
    original_path: Mapped[str] = mapped_column(Text, nullable=False)
    trash_path: Mapped[str] = mapped_column(Text, nullable=False)
    deleted_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_iso)
    restored_at: Mapped[str | None] = mapped_column(Text)


Index("ix_trash_entries_asset", TrashEntry.asset_id)


class OpLog(Base):
    __tablename__ = "op_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="")
    plan_id: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    result: Mapped[str] = mapped_column(Text, nullable=False, default="ok")
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_iso)
    seq: Mapped[int | None] = mapped_column(Integer)
    prev_hash: Mapped[str | None] = mapped_column(Text)
    op_hash: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(Text)
