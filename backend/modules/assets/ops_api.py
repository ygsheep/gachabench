# -*- coding: utf-8 -*-
"""P2 文件操作接口：计划 / 应用 / 回滚、回收站、重复与未引用。

这个 router 必须比 `modules/assets/api.py` 的 `/api/assets/{asset_id}` 先注册，
否则 `/api/assets/duplicates` 会被当成资产详情（见 `main.py`）。
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from modules.assets import plans, trash
from modules.assets.api import _ctx, _meta_map, _to_item
from modules.assets.models import AssetRef, IndexAsset
from modules.platform.db import session_scope

router = APIRouter(prefix="/api", tags=["asset-operations"])


class PlanRequest(BaseModel):
    kind: str
    asset_id: str | None = None
    new_name: str | None = None
    asset_ids: list[str] = Field(default_factory=list)
    dest_dir: str | None = None


@router.post("/assets/operations/plan")
async def create_plan_api(body: PlanRequest):
    _cfg, root, _ws = _ctx()
    return plans.create_plan(root, body.kind, body.model_dump(exclude_none=True))


@router.post("/assets/operations/{plan_id}/apply")
async def apply_plan_api(plan_id: str):
    _cfg, root, _ws = _ctx()
    return plans.apply_plan(root, plan_id)


@router.post("/assets/operations/{plan_id}/rollback")
async def rollback_plan_api(plan_id: str):
    _cfg, root, _ws = _ctx()
    return plans.rollback_plan(root, plan_id)


@router.get("/assets/operations/{plan_id}")
async def plan_detail_api(plan_id: str):
    _cfg, root, _ws = _ctx()
    return plans.get_plan(root, plan_id)


@router.get("/assets/duplicates")
async def duplicates_api():
    _cfg, root, _ws = _ctx()
    with session_scope(root) as session:
        rows = session.scalars(
            select(IndexAsset)
            .where(
                IndexAsset.status == "active",
                IndexAsset.sha256.is_not(None),
                IndexAsset.hash_status != "quick",
            )
            .order_by(IndexAsset.path)
        ).all()
        grouped: dict[str, list[IndexAsset]] = {}
        for asset in rows:
            grouped.setdefault(asset.sha256 or "", []).append(asset)
        groups = [
            items for sha, items in grouped.items() if sha and len(items) > 1
        ]
        metas = _meta_map(
            session, [a.id for items in groups for a in items]
        )
        return {
            "groups": [
                {
                    "sha256": items[0].sha256,
                    "count": len(items),
                    "size": items[0].size or 0,
                    "items": [_to_item(a, metas.get(a.id)) for a in items],
                }
                for items in groups
            ],
            "total": len(groups),
        }


@router.get("/assets/unreferenced")
async def unreferenced_api(limit: int = Query(200, ge=1, le=1000)):
    _cfg, root, _ws = _ctx()
    with session_scope(root) as session:
        referenced = select(AssetRef.asset_id)
        conds = [
            IndexAsset.status == "active",
            ~IndexAsset.id.in_(referenced),
        ]
        total = session.scalar(
            select(func.count()).select_from(IndexAsset).where(*conds)
        )
        rows = session.scalars(
            select(IndexAsset)
            .where(*conds)
            .order_by(IndexAsset.updated_at.desc(), IndexAsset.id.desc())
            .limit(limit)
        ).all()
        metas = _meta_map(session, [a.id for a in rows])
        return {
            "items": [_to_item(a, metas.get(a.id)) for a in rows],
            "total": int(total or 0),
        }


@router.get("/trash")
async def list_trash_api():
    _cfg, root, _ws = _ctx()
    return trash.list_trash(root)


@router.post("/trash/{entry_id}/restore")
async def restore_trash_api(entry_id: str):
    _cfg, root, _ws = _ctx()
    return trash.restore(root, entry_id)


@router.delete("/trash/{entry_id}")
async def purge_trash_api(entry_id: str):
    _cfg, root, _ws = _ctx()
    return trash.purge(root, entry_id)
