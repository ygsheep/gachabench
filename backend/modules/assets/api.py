# -*- coding: utf-8 -*-
"""P1 资产索引 HTTP 接口。

与旧 `routers/assets.py` 的分工见 `plan/P1/spec.md`：
- 旧分组列表/同步重扫在 `/api/assets/groups`、`/api/assets/groups/scan`；
- 这里的 `/api/assets` 是新索引分页列表。
"""
from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from config import get_config
from modules.assets import roles, search, thumbs
from modules.assets.models import (
    AssetMeta,
    AssetRef,
    Collection,
    IndexAsset,
    now_iso,
)
from modules.assets.service import get_scan_manager
from modules.platform.db import session_scope

router = APIRouter(prefix="/api/assets", tags=["asset-index"])

BUILTIN_COLLECTIONS: list[tuple[str, str, dict]] = [
    ("recent", "最近修改", {"mtime_within_days": 7}),
    ("missing", "已丢失", {"status": "missing"}),
    ("unreferenced", "未被引用", {"unreferenced": True}),
    ("large", "大文件", {"min_size": 50 * 1024 * 1024}),
]

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------

class AssetItem(BaseModel):
    id: str
    path: str
    name: str
    dir_path: str = ""
    role: str = "other"
    subject: str = ""
    category: str = "other"
    ext: str = ""
    size: int = 0
    mtime: float = 0
    sha256: str | None = None
    hash_status: str = "full"
    media_kind: str = "other"
    width: int | None = None
    height: int | None = None
    status: str = "active"
    tags: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    thumb_url: str | None = None
    media_url: str = ""


class AssetRefItem(BaseModel):
    id: str
    file_path: str
    line: int | None = None
    col: int | None = None
    kind: str = "mention"
    segment: str | None = None


class AssetDetail(AssetItem):
    note: str = ""
    subject_override: str | None = None
    refs: list[AssetRefItem] = Field(default_factory=list)


class AssetListResp(BaseModel):
    items: list[AssetItem]
    next_cursor: str | None = None
    total: int = 0
    root: str = ""
    workspace: str = ""


class MetaPatch(BaseModel):
    tags: list[str] | None = None
    aliases: list[str] | None = None
    note: str | None = None
    subject_override: str | None = None


class RoleCount(BaseModel):
    key: str
    label: str
    count: int


class CollectionCount(BaseModel):
    id: str
    name: str
    kind: str
    builtin: bool
    count: int


class CollectionsResp(BaseModel):
    roles: list[RoleCount]
    collections: list[CollectionCount]


class ScanRequest(BaseModel):
    force: bool = False


class ScanStartResp(BaseModel):
    task_id: str
    status: str


class ScanStatusResp(BaseModel):
    id: str
    status: str
    started_at: str = ""
    finished_at: str | None = None
    stats: dict = Field(default_factory=dict)
    force: bool = False


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _ctx() -> tuple:
    cfg = get_config()
    root = cfg.ws_root()
    if root is None or not cfg.active:
        raise HTTPException(status_code=400, detail="未激活任何工作区")
    return cfg, root.resolve(), cfg.active


def _json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [str(x) for x in data] if isinstance(data, list) else []
    except Exception:
        return []


def _meta_map(session: Session, ids: list[str]) -> dict[str, AssetMeta]:
    if not ids:
        return {}
    rows = session.scalars(select(AssetMeta).where(AssetMeta.asset_id.in_(ids))).all()
    return {m.asset_id: m for m in rows}


def _to_item(asset: IndexAsset, meta: AssetMeta | None) -> AssetItem:
    subject = (meta.subject_override if meta and meta.subject_override else asset.subject) or ""
    media_url = f"/api/media?path={quote(asset.path)}"
    thumb_url = None
    if asset.media_kind == "image" and asset.status == "active":
        thumb_url = f"/api/thumb?path={quote(asset.path)}"
    return AssetItem(
        id=asset.id,
        path=asset.path,
        name=asset.name,
        dir_path=asset.dir_path,
        role=asset.role,
        subject=subject,
        category=asset.category,
        ext=asset.ext,
        size=asset.size or 0,
        mtime=asset.mtime or 0,
        sha256=asset.sha256,
        hash_status=asset.hash_status,
        media_kind=asset.media_kind,
        width=asset.width,
        height=asset.height,
        status=asset.status,
        tags=_json_list(meta.tags_json if meta else None),
        aliases=_json_list(meta.aliases_json if meta else None),
        thumb_url=thumb_url,
        media_url=media_url,
    )


def _detail(session: Session, asset: IndexAsset, meta: AssetMeta | None) -> AssetDetail:
    item = _to_item(asset, meta)
    refs = session.scalars(
        select(AssetRef).where(AssetRef.asset_id == asset.id).order_by(AssetRef.file_path)
    ).all()
    return AssetDetail(
        **item.model_dump(),
        note=(meta.note if meta else "") or "",
        subject_override=meta.subject_override if meta else None,
        refs=[
            AssetRefItem(
                id=r.id,
                file_path=r.file_path,
                line=r.line,
                col=r.col,
                kind=r.kind,
                segment=r.segment,
            )
            for r in refs
        ],
    )


def _safe_path(root: Path, raw: str) -> Path:
    path = (raw or "").strip()
    if not path or "\x00" in path:
        raise HTTPException(status_code=400, detail="路径不能为空")
    if path.startswith(("/", "\\")) or _DRIVE_RE.match(path):
        raise HTTPException(status_code=400, detail="只接受工作区内的相对路径")
    if ".." in Path(path).parts:
        raise HTTPException(status_code=400, detail="路径越界")
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(status_code=400, detail="路径越界")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return target


def _encode_cursor(asset: IndexAsset) -> str:
    raw = f"{asset.updated_at}|{asset.id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        updated_at, asset_id = raw.split("|", 1)
        return updated_at, asset_id
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="无效的分页游标") from exc


def _seed_builtins(session: Session) -> None:
    have = {
        c.id
        for c in session.scalars(select(Collection).where(Collection.builtin == 1)).all()
    }
    for cid, name, filter_json in BUILTIN_COLLECTIONS:
        if cid in have:
            continue
        session.add(
            Collection(
                id=cid,
                name=name,
                kind="builtin",
                filter_json=json.dumps(filter_json, ensure_ascii=False),
                builtin=1,
                created_at=now_iso(),
            )
        )
    session.flush()


def _collection_row(session: Session, collection: str) -> Collection | None:
    if not collection:
        return None
    _seed_builtins(session)
    row = session.get(Collection, collection)
    if row is None:
        raise HTTPException(status_code=404, detail="集合不存在")
    return row


def _collection_filter(session: Session, collection: str) -> tuple[dict, bool]:
    """返回 (过滤条件, 是否手动集合)。"""
    row = _collection_row(session, collection)
    if row is None:
        return {}, False
    try:
        flt = json.loads(row.filter_json or "{}")
    except Exception:
        flt = {}
    return (flt if isinstance(flt, dict) else {}), row.kind == "manual"


def _conditions(
    session: Session,
    role: str,
    category: str,
    q: str,
    collection: str,
) -> list:
    conds: list = []
    flt, manual = _collection_filter(session, collection)
    status = flt.get("status") or "active"
    conds.append(IndexAsset.status == status)
    if role:
        conds.append(IndexAsset.role == role)
    if category:
        conds.append(IndexAsset.category == category)
    if manual:
        from modules.assets.models import CollectionItem

        conds.append(
            IndexAsset.id.in_(
                select(CollectionItem.asset_id).where(
                    CollectionItem.collection_id == collection
                )
            )
        )
    if flt.get("unreferenced"):
        conds.append(~IndexAsset.id.in_(select(AssetRef.asset_id)))
    if flt.get("min_size"):
        conds.append(IndexAsset.size >= int(flt["min_size"]))
    if flt.get("mtime_within_days"):
        cutoff = time.time() - float(flt["mtime_within_days"]) * 86400
        conds.append(IndexAsset.mtime >= cutoff)
    if q.strip():
        ids = search.search_ids(session, q)
        if not ids:
            conds.append(IndexAsset.id == "__no_match__")
        else:
            conds.append(IndexAsset.id.in_(ids))
    return conds


# ---------------------------------------------------------------------------
# 路由（具体路径必须先于 /{asset_id}）
# ---------------------------------------------------------------------------

@router.get("/collections", response_model=CollectionsResp)
async def collections_api():
    _cfg, root, _ws = _ctx()
    with session_scope(root) as session:
        _seed_builtins(session)
        rows = session.execute(
            select(IndexAsset.role, func.count())
            .where(IndexAsset.status == "active")
            .group_by(IndexAsset.role)
        ).all()
        counts = {r[0]: int(r[1]) for r in rows}
        labels = dict(roles.ROLE_LABELS)
        ordered = [k for k, _ in roles.ROLE_LABELS]
        extra = sorted(k for k in counts if k not in labels)
        role_items = [
            RoleCount(key=k, label=labels.get(k, k), count=counts.get(k, 0))
            for k in ordered + extra
        ]

        cols = []
        for row in session.scalars(
            select(Collection).where(Collection.builtin == 1).order_by(Collection.id)
        ).all():
            conds = _conditions(session, "", "", "", row.id)
            total = session.scalar(
                select(func.count()).select_from(IndexAsset).where(*conds)
            )
            cols.append(
                CollectionCount(
                    id=row.id,
                    name=row.name,
                    kind=row.kind,
                    builtin=bool(row.builtin),
                    count=int(total or 0),
                )
            )
        return CollectionsResp(roles=role_items, collections=cols)


@router.post("/scan", response_model=ScanStartResp)
async def scan_api(body: ScanRequest | None = None):
    _cfg, root, _ws = _ctx()
    result = get_scan_manager().start(root, force=bool(body.force if body else False))
    return ScanStartResp(**result)


@router.get("/scan/{task_id}", response_model=ScanStatusResp)
async def scan_status_api(task_id: str):
    _cfg, root, _ws = _ctx()
    data = get_scan_manager().status(root, task_id)
    if data is None:
        raise HTTPException(status_code=404, detail="扫描任务不存在")
    return ScanStatusResp(**data)


@router.get("", response_model=AssetListResp)
async def list_assets_api(
    role: str = Query(""),
    category: str = Query(""),
    collection: str = Query(""),
    q: str = Query(""),
    cursor: str = Query(""),
    limit: int = Query(80, ge=1, le=500),
):
    _cfg, root, ws = _ctx()
    with session_scope(root) as session:
        conds = _conditions(session, role, category, q, collection)
        total = session.scalar(
            select(func.count()).select_from(IndexAsset).where(*conds)
        )

        page_conds = list(conds)
        if cursor:
            updated_at, after_id = _decode_cursor(cursor)
            page_conds.append(
                or_(
                    IndexAsset.updated_at < updated_at,
                    and_(
                        IndexAsset.updated_at == updated_at,
                        IndexAsset.id < after_id,
                    ),
                )
            )

        rows = session.scalars(
            select(IndexAsset)
            .where(*page_conds)
            .order_by(IndexAsset.updated_at.desc(), IndexAsset.id.desc())
            .limit(limit + 1)
        ).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        metas = _meta_map(session, [r.id for r in rows])
        return AssetListResp(
            items=[_to_item(r, metas.get(r.id)) for r in rows],
            next_cursor=_encode_cursor(rows[-1]) if has_more and rows else None,
            total=int(total or 0),
            root=str(root),
            workspace=ws,
        )


@router.get("/{asset_id}", response_model=AssetDetail)
async def asset_detail_api(asset_id: str):
    _cfg, root, _ws = _ctx()
    with session_scope(root) as session:
        asset = session.get(IndexAsset, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="资产不存在")
        meta = session.get(AssetMeta, asset_id)
        return _detail(session, asset, meta)


@router.patch("/{asset_id}", response_model=AssetDetail)
async def asset_patch_api(asset_id: str, body: MetaPatch):
    _cfg, root, _ws = _ctx()
    with session_scope(root) as session:
        asset = session.get(IndexAsset, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="资产不存在")
        meta = session.get(AssetMeta, asset_id)
        if meta is None:
            meta = AssetMeta(asset_id=asset_id)
            session.add(meta)
        if body.tags is not None:
            meta.tags_json = json.dumps(body.tags, ensure_ascii=False)
        if body.aliases is not None:
            meta.aliases_json = json.dumps(body.aliases, ensure_ascii=False)
        if body.note is not None:
            meta.note = body.note
        if body.subject_override is not None:
            meta.subject_override = body.subject_override or None
        meta.updated_at = now_iso()
        session.flush()
        search.sync_fts(session, asset_id)
        return _detail(session, asset, meta)


# ---------------------------------------------------------------------------
# 媒体流与缩略图
# ---------------------------------------------------------------------------

media_router = APIRouter(prefix="/api", tags=["asset-index"])


@media_router.get("/media")
async def media_api(path: str = Query(...)):
    _cfg, root, _ws = _ctx()
    target = _safe_path(root, path)
    return FileResponse(
        target,
        media_type=roles.mime_for_ext(target.suffix),
        filename=target.name,
        content_disposition_type="inline",
    )


@media_router.get("/thumb")
async def thumb_api(path: str = Query(...)):
    _cfg, root, _ws = _ctx()
    target = _safe_path(root, path)
    if not roles.is_image(target.suffix):
        raise HTTPException(status_code=415, detail="只有图片能生成缩略图")
    rel = target.relative_to(root).as_posix()
    with session_scope(root) as session:
        asset = session.scalar(select(IndexAsset).where(IndexAsset.path == rel))
        sha256 = asset.sha256 if asset else None
    dest = thumbs.ensure_thumb(root, target, sha256, rel)
    if dest is None:
        raise HTTPException(status_code=415, detail="缩略图生成失败")
    return FileResponse(dest, media_type="image/webp")
