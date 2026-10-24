# -*- coding: utf-8 -*-
"""工作区索引器：递归扫描、目录 mtime 增量、sha256、missing 标记。

写入策略：
- 以相对 POSIX 路径为键 upsert，保留原 id 与 asset_meta；
- 每 500 个文件提交一次；
- 哈希读取并发上限 2；
- 磁盘上消失的文件只标 missing，不删除。
"""
from __future__ import annotations

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import select

from config import get_config
from modules.assets import roles, search, sidecar
from modules.assets.models import IndexAsset, IndexDir, now_iso
from modules.platform.db import ensure_schema_once, session_scope
from modules.platform.ids import ulid

PROGRESS_EVERY = 200
COMMIT_EVERY = 500
HASH_THREADS = 2
QUICK_HASH_LIMIT = 2 * 1024 ** 3


@dataclass
class FileMeta:
    rel: str
    abs_path: Path
    name: str
    dir_path: str
    ext: str
    size: int
    mtime: float
    mtime_ns: int


def _hash_file(path: Path, size: int) -> tuple[Optional[str], str]:
    if size > QUICK_HASH_LIMIT:
        return None, "quick"
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest(), "full"


def _needs_hash(old: Optional[IndexAsset], meta: FileMeta) -> bool:
    if old is None:
        return True
    if old.size != meta.size or old.mtime != meta.mtime:
        return True
    return not old.sha256


def _stats0() -> dict:
    return {
        "total": 0,
        "added": 0,
        "updated": 0,
        "missing": 0,
        "skipped": 0,
        "scanned_dirs": 0,
        "elapsed_ms": 0,
    }


def scan_workspace(
    root: Path,
    force: bool = False,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> dict:
    root = Path(root).resolve()
    stats = _stats0()
    if not root.is_dir():
        return stats

    started = time.monotonic()
    cfg = get_config()
    folder_names = cfg.folder_names_for(cfg.active_ws())

    files: list[FileMeta] = []
    seen: set[str] = set()
    present_dirs: set[str] = {""}
    enumerated: set[str] = set()

    with session_scope(root) as session:
        existing = {a.path: a for a in session.scalars(select(IndexAsset)).all()}
        dir_rows = {d.path: d for d in session.scalars(select(IndexDir)).all()}

        def walk(rel_dir: str, abs_dir: Path) -> None:
            try:
                st = abs_dir.stat()
            except OSError:
                return
            prev = dir_rows.get(rel_dir)
            # 目录 mtime 只反映直属子项：本目录未变时不重列文件，
            # 但子目录仍要递归，否则多层嵌套的改动会漏掉。
            unchanged = (
                not force and prev is not None and prev.mtime_ns == st.st_mtime_ns
            )
            present_dirs.add(rel_dir)
            if unchanged:
                stats["skipped"] += 1
            else:
                enumerated.add(rel_dir)
                stats["scanned_dirs"] += 1
            try:
                entries = list(os.scandir(abs_dir))
            except OSError:
                return

            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if roles.is_skipped_dir(entry.name):
                            continue
                        child = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                        walk(child, Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        if unchanged:
                            continue
                        # sidecar（X.prompt.json）不是资产：它是图片的附属元数据。
                        # 不跳过的话资产页会多出一堆 json 卡片，而且它们没有缩略图。
                        if sidecar.is_sidecar(entry.name):
                            continue
                        rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                        fst = entry.stat(follow_symlinks=False)
                        seen.add(rel)
                        files.append(
                            FileMeta(
                                rel=rel,
                                abs_path=Path(entry.path),
                                name=entry.name,
                                dir_path=rel_dir,
                                ext=Path(entry.name).suffix.lower(),
                                size=fst.st_size,
                                mtime=fst.st_mtime,
                                mtime_ns=fst.st_mtime_ns,
                            )
                        )
                except OSError:
                    continue

            row = dir_rows.get(rel_dir)
            if row is None:
                row = IndexDir(
                    path=rel_dir, mtime_ns=st.st_mtime_ns, scanned_at=now_iso()
                )
                session.add(row)
                dir_rows[rel_dir] = row
            else:
                row.mtime_ns = st.st_mtime_ns
                row.scanned_at = now_iso()

        walk("", root)
        stats["total"] = len(files)

        # 消失的目录：删除目录行，其下资产在下面统一标 missing
        vanished = [d for d in dir_rows if d and d not in present_dirs]
        vanished_prefixes = tuple(d + "/" for d in vanished)
        for d in vanished:
            row = dir_rows.get(d)
            if row is not None:
                session.delete(row)

        to_hash = [f for f in files if _needs_hash(existing.get(f.rel), f)]
        hashed: dict[str, tuple[Optional[str], str]] = {}
        if to_hash:
            with ThreadPoolExecutor(max_workers=HASH_THREADS) as pool:
                for done, (rel, result) in enumerate(
                    pool.map(
                        lambda f: (f.rel, _hash_file(f.abs_path, f.size)), to_hash
                    ),
                    1,
                ):
                    hashed[rel] = result
                    if progress_cb and (
                        done % PROGRESS_EVERY == 0 or done == len(to_hash)
                    ):
                        progress_cb(done, stats["total"])

        processed = 0
        for meta in files:
            old = existing.get(meta.rel)
            role = roles.role_for_path(meta.rel, folder_names)
            subject = roles.subject_for_name(meta.name)
            category = roles.category_for_ext(meta.ext)
            media_kind = roles.media_kind_for_ext(meta.ext)
            sha256, hash_status = hashed.get(meta.rel, (None, "full"))
            if meta.rel not in hashed and old is not None:
                sha256, hash_status = old.sha256, old.hash_status

            if old is None:
                session.add(
                    IndexAsset(
                        id=ulid(),
                        path=meta.rel,
                        name=meta.name,
                        dir_path=meta.dir_path,
                        role=role,
                        subject=subject,
                        category=category,
                        ext=meta.ext,
                        size=meta.size,
                        mtime=meta.mtime,
                        sha256=sha256,
                        hash_status=hash_status,
                        media_kind=media_kind,
                        status="active",
                        created_at=now_iso(),
                        updated_at=now_iso(),
                    )
                )
                stats["added"] += 1
            else:
                changed = (
                    old.size != meta.size
                    or old.mtime != meta.mtime
                    or old.sha256 != sha256
                    or old.status != "active"
                )
                old.name = meta.name
                old.dir_path = meta.dir_path
                old.role = role
                old.subject = subject
                old.category = category
                old.ext = meta.ext
                old.size = meta.size
                old.mtime = meta.mtime
                old.sha256 = sha256
                old.hash_status = hash_status
                old.media_kind = media_kind
                if old.status != "active":
                    old.status = "active"
                    old.missing_at = None
                if changed:
                    old.updated_at = now_iso()
                    stats["updated"] += 1

            processed += 1
            if processed % COMMIT_EVERY == 0:
                session.flush()
                session.commit()

        # 磁盘上消失：只标 missing，不删记录
        for path, asset in existing.items():
            if asset.status != "active" or path in seen:
                continue
            if asset.dir_path in enumerated or (
                vanished_prefixes and path.startswith(vanished_prefixes)
            ):
                asset.status = "missing"
                asset.missing_at = now_iso()
                stats["missing"] += 1

        session.flush()
        search.rebuild_fts(session)
        # 提示词 sidecar：文件是真相，这里只做缓存。单个坏文件不拖垮整轮。
        sidecar_stats = sidecar.ingest(root, session)

    # P2：资产表落定后刷新引用表。引用扫描失败不能拖垮索引，只留日志。
    try:
        from modules.assets.refs import scan_refs

        with session_scope(root) as session:
            scan_refs(root, session)
    except Exception as exc:  # noqa: BLE001
        print(f"[index] 引用扫描失败：{exc}")

    stats["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    stats["sidecar"] = {
        k: v for k, v in sidecar_stats.items() if k != "problems"
    }
    return stats


def index_single(root: Path, rel_path: str) -> Optional[str]:
    """单个文件的 upsert（上传后用），返回资产 id。"""
    root = Path(root).resolve()
    ensure_schema_once(root)
    abs_path = root / rel_path
    if not abs_path.is_file():
        return None
    rel_path = Path(rel_path).as_posix()
    cfg = get_config()
    folder_names = cfg.folder_names_for(cfg.active_ws())
    try:
        st = abs_path.stat()
    except OSError:
        return None

    sha256, hash_status = _hash_file(abs_path, st.st_size)
    role = roles.role_for_path(rel_path, folder_names)
    subject = roles.subject_for_name(abs_path.name)
    category = roles.category_for_ext(abs_path.suffix.lower())
    media_kind = roles.media_kind_for_ext(abs_path.suffix.lower())

    with session_scope(root) as session:
        asset = session.scalar(select(IndexAsset).where(IndexAsset.path == rel_path))
        if asset is None:
            asset = IndexAsset(
                id=ulid(),
                path=rel_path,
                name=abs_path.name,
                dir_path=Path(rel_path).parent.as_posix() if "/" in rel_path else "",
                role=role,
                subject=subject,
                category=category,
                ext=abs_path.suffix.lower(),
                size=st.st_size,
                mtime=st.st_mtime,
                sha256=sha256,
                hash_status=hash_status,
                media_kind=media_kind,
                status="active",
                created_at=now_iso(),
                updated_at=now_iso(),
            )
            session.add(asset)
        else:
            asset.name = abs_path.name
            asset.dir_path = (
                Path(rel_path).parent.as_posix() if "/" in rel_path else ""
            )
            asset.role = role
            asset.subject = subject
            asset.category = category
            asset.ext = abs_path.suffix.lower()
            asset.size = st.st_size
            asset.mtime = st.st_mtime
            asset.sha256 = sha256
            asset.hash_status = hash_status
            asset.media_kind = media_kind
            asset.status = "active"
            asset.missing_at = None
            asset.updated_at = now_iso()
        session.flush()
        search.sync_fts(session, asset.id)
        # 上传/收编后立刻摄取它的 sidecar（若有），不必等下一次全量扫描
        sidecar.ingest(root, session)
        return asset.id
