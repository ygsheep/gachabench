# -*- coding: utf-8 -*-
"""P2 回收站：删除不真删，先落到 `.gachabench/trash/<YYYYMMDD>/`。

文件命名 `<assetId>__<原名>`；恢复时目标已存在则依次追加 `_02`、`_03`，
绝不覆盖已有文件。
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from modules.assets import search
from modules.assets.models import (
    AssetMeta,
    AssetRef,
    IndexAsset,
    TrashEntry,
    now_iso,
)
from modules.platform.db import session_scope
from modules.platform.ids import ulid

TRASH_DIR = ".gachabench/trash"


def _error(status: int, code: str, message: str) -> None:
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def trash_path_for(root: Path, asset_id: str, name: str) -> tuple[Path, str]:
    """返回 (绝对路径, 相对 POSIX 路径)，必要时加 `_02` 避免撞名。"""
    root = Path(root).resolve()
    day = datetime.now().strftime("%Y%m%d")
    base_dir = root / TRASH_DIR / day
    stem, ext = Path(name).stem, Path(name).suffix
    candidates = [f"{asset_id}__{name}"]
    candidates += [f"{asset_id}__{stem}_{i:02d}{ext}" for i in range(2, 1000)]
    for filename in candidates:
        target = base_dir / filename
        if not target.exists():
            return target, target.relative_to(root).as_posix()
    _error(409, "TRASH_FULL", "同名回收站文件过多")


def create_entry(
    session: Session, asset_id: str, original_path: str, trash_rel: str
) -> TrashEntry:
    entry = TrashEntry(
        id=ulid(),
        asset_id=asset_id,
        original_path=original_path,
        trash_path=trash_rel,
        deleted_at=now_iso(),
    )
    session.add(entry)
    session.flush()
    return entry


def list_trash(root: Path) -> dict:
    root = Path(root).resolve()
    with session_scope(root) as session:
        rows = session.scalars(
            select(TrashEntry)
            .where(TrashEntry.restored_at.is_(None))
            .order_by(TrashEntry.deleted_at.desc())
        ).all()
        items = []
        for entry in rows:
            asset = session.get(IndexAsset, entry.asset_id)
            items.append(
                {
                    "id": entry.id,
                    "asset_id": entry.asset_id,
                    "name": asset.name if asset else Path(entry.original_path).name,
                    "original_path": entry.original_path,
                    "trash_path": entry.trash_path,
                    "deleted_at": entry.deleted_at,
                    "size": int(asset.size or 0) if asset else 0,
                    "ext": asset.ext if asset else Path(entry.original_path).suffix,
                    "media_kind": asset.media_kind if asset else "other",
                    "sha256": asset.sha256 if asset else None,
                }
            )
        return {"items": items, "total": len(items)}


def _unique_restore_path(target: Path) -> Path:
    if not target.exists():
        return target
    stem, ext = target.stem, target.suffix
    for i in range(2, 1000):
        candidate = target.with_name(f"{stem}_{i:02d}{ext}")
        if not candidate.exists():
            return candidate
    _error(409, "NAME_CONFLICT", "恢复目标重名过多")


def restore(root: Path, entry_id: str) -> dict:
    root = Path(root).resolve()
    with session_scope(root) as session:
        entry = session.get(TrashEntry, entry_id)
        if entry is None or entry.restored_at:
            _error(404, "TRASH_NOT_FOUND", "回收站条目不存在")
        asset = session.get(IndexAsset, entry.asset_id)
        if asset is None:
            _error(404, "ASSET_NOT_FOUND", "资产记录不存在")

        trash_abs = root / entry.trash_path
        if not trash_abs.is_file():
            _error(404, "FILE_NOT_FOUND", "回收站文件已丢失")

        target = root / entry.original_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target = _unique_restore_path(target)
        os.replace(trash_abs, target)

        rel = target.relative_to(root).as_posix()
        stat = target.stat()
        asset.path = rel
        asset.name = target.name
        asset.dir_path = target.parent.relative_to(root).as_posix()
        if asset.dir_path == ".":
            asset.dir_path = ""
        asset.size = int(stat.st_size)
        asset.mtime = stat.st_mtime
        asset.status = "active"
        asset.missing_at = None
        asset.updated_at = now_iso()
        entry.restored_at = now_iso()
        session.flush()

        # 引用表按新路径重建；文件名没变，原有引用继续有效。
        search.sync_fts(session, asset.id)
        from modules.assets.refs import scan_refs

        scan_refs(root, session)
        return {
            "ok": True,
            "id": entry.id,
            "asset_id": asset.id,
            "path": rel,
            "name": asset.name,
            "status": asset.status,
        }


def purge(root: Path, entry_id: str) -> dict:
    root = Path(root).resolve()
    with session_scope(root) as session:
        entry = session.get(TrashEntry, entry_id)
        if entry is None:
            _error(404, "TRASH_NOT_FOUND", "回收站条目不存在")
        if entry.restored_at:
            _error(409, "TRASH_RESTORED", "条目已恢复，不能彻底清除")

        trash_abs = root / entry.trash_path
        try:
            if trash_abs.is_file():
                trash_abs.unlink()
        except OSError as exc:
            _error(409, "FILE_BUSY", f"删除回收站文件失败：{exc}")

        session.execute(delete(AssetRef).where(AssetRef.asset_id == entry.asset_id))
        meta = session.get(AssetMeta, entry.asset_id)
        if meta is not None:
            session.delete(meta)
        asset = session.get(IndexAsset, entry.asset_id)
        if asset is not None:
            session.delete(asset)
        session.delete(entry)
        session.flush()
        search.sync_fts(session, entry.asset_id)
        return {"ok": True, "id": entry_id, "asset_id": entry.asset_id}

