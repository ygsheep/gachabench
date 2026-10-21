# -*- coding: utf-8 -*-
"""工作区 API：列表 / 新建 / 打开 / 激活 / 目录角色 / 重命名 / 移除 / 扫描。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from workspaces import (
    WorkSpaceError,
    activate,
    create_workspace,
    inspect_root,
    list_workspaces,
    open_workspace,
    remove,
    rename,
    set_folders,
)

router = APIRouter(prefix="/api/workspaces", tags=["workspace"])


class CreateBody(BaseModel):
    parent_dir: str
    name: str
    # 资产目录名；不传 = 自动匹配已有目录（匹配不到则用全局默认）
    asset_dir: Optional[str] = None


class OpenBody(BaseModel):
    root: str
    name: Optional[str] = None
    # 资产目录名；不传 = 自动匹配；传空串 = 明确「未确定，稍后手动选」
    asset_dir: Optional[str] = None
    create_missing: bool = True


class InspectBody(BaseModel):
    root: str


class NameBody(BaseModel):
    name: str


class FoldersBody(BaseModel):
    # 形如 {"asset": "资产"}
    folders: dict[str, str]


def _err(e: WorkSpaceError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


@router.get("")
async def get_workspaces():
    return list_workspaces()


@router.post("/inspect")
async def inspect(body: InspectBody):
    """看一个目录（新建前 / 打开前都能用）：子目录清单 + 资产目录自动匹配结果。"""
    from config import get_config

    root = Path(body.root).expanduser()
    return inspect_root(root, get_config())


@router.post("")
async def create(body: CreateBody):
    try:
        entry = create_workspace(body.parent_dir, body.name, body.asset_dir)
    except WorkSpaceError as e:
        raise _err(e)
    return {"workspace": entry, "active": entry.name}


@router.post("/open")
async def open_ws(body: OpenBody):
    """把已有目录登记为工作区（自动匹配资产目录，未命中可前端手动指定）。"""
    try:
        entry = open_workspace(body.root, body.name, body.asset_dir, body.create_missing)
    except WorkSpaceError as e:
        raise _err(e)
    return {"workspace": entry, "active": entry.name}


@router.post("/{name}/activate")
async def activate_ws(name: str):
    try:
        ws = activate(name)
    except WorkSpaceError as e:
        raise _err(e)
    return {"workspace": ws, "active": ws.name}


@router.patch("/{name}/folders")
async def set_ws_folders(name: str, body: FoldersBody):
    """改目录角色名（当前主要用于「没有匹配到资产目录名 → 手动选择」）。"""
    try:
        ws = set_folders(name, body.folders)
    except WorkSpaceError as e:
        raise _err(e)
    return {"workspace": ws}


@router.patch("/{name}")
async def rename_ws(name: str, body: NameBody):
    try:
        ws = rename(name, body.name)
    except WorkSpaceError as e:
        raise _err(e)
    return {"workspace": ws}


@router.delete("/{name}")
async def delete_ws(name: str):
    try:
        return remove(name)
    except WorkSpaceError as e:
        raise _err(e)


@router.post("/{name}/scan")
async def scan_ws(name: str):
    """遍历 草稿/ + 生成/ 登记新卡（幂等 upsert）；顺带补收 ComfyUI 侧产物 + 重扫资产库。"""
    from config import get_config
    from db import get_db
    from repo import upsert_card_by_path

    cfg = get_config()
    ws = next((w for w in cfg.workspaces if w.name == name), None)
    if ws is None:
        raise HTTPException(status_code=404, detail=f"工作区不存在：{name}")

    root = Path(ws.root)
    db = get_db(root)
    added = updated = 0
    for folder_key in ("draft", "final"):
        folder = cfg.folder_name(folder_key, ws)
        if not folder:
            continue
        base = root / folder
        if not base.is_dir():
            continue
        for mp4 in sorted(base.rglob("*.mp4")):
            rel = str(mp4.relative_to(root)).replace("\\", "/")
            _, created = upsert_card_by_path(db, ws.name, rel, cfg)
            if created:
                added += 1
            else:
                updated += 1

    # 再补一层：ComfyUI 已经跑完、产物还躺在它自己 output 目录里的任务也捞回来。
    # 只看本地目录的扫描救不回这些成片 —— 那正是「跑完了但草稿里找不到」的另一半。
    rescued: dict = {"ingested": 0, "files": 0, "problems": []}
    try:
        import reconcile

        stats = await reconcile.reconcile_jobs(cfg, name, db, root)
        rescued = {
            "ingested": int(stats.get("ingested") or 0),
            "files": int(stats.get("files") or 0),
            "problems": [str(p) for p in (stats.get("problems") or [])],
        }
        added += int(stats.get("cards") or 0)
    except Exception as e:  # noqa: BLE001 —— 补收失败不该让扫描整个 500
        rescued["problems"] = [f"补收失败：{str(e)[:200]}"]

    asset_folder = cfg.folder_name("asset", ws)
    if asset_folder and (root / asset_folder).is_dir():
        from routers.assets import _scan

        _scan(root / asset_folder, root, ws.name)

    return {"workspace": name, "added": added, "updated": updated, "rescued": rescued}
