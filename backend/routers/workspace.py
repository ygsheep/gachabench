# -*- coding: utf-8 -*-
"""工作区 API：列表 / 新建 / 打开 / 激活 / 目录角色 / 重命名 / 移除 / 扫描。"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from workspaces import (
    WorkSpaceError,
    WorkspaceEntry,
    activate,
    create_workspace,
    inspect_root,
    list_workspaces,
    open_workspace,
    remove,
    rename,
    set_folders,
    set_watch_dirs,
)

log = logging.getLogger("gachabench.workspace")

router = APIRouter(prefix="/api/workspaces", tags=["workspace"])


def _warm_index(entry: WorkspaceEntry) -> None:
    """工作区被新建 / 打开 / 切换后，立刻把它的库升到最新 schema 并起一次索引。

    以前 schema 只在进程启动时对「当时的活动工作区」建一次，切到别的工作区后
    cards.db 还停在旧结构，资产接口就会 `no such table: index_assets` 连着 500。
    这里补上切换路径的兜底；失败只记日志，不影响接口本身返回。
    """
    from modules.assets.service import get_scan_manager

    root = Path(entry.root)
    if not root.is_dir():
        return
    try:
        get_scan_manager().ensure_started(root)
    except Exception as exc:  # noqa: BLE001
        log.exception("[workspace] 「%s」索引预热失败：%s", entry.name, exc)

    _write_naming_doc(entry)


def _write_naming_doc(entry: WorkspaceEntry) -> None:
    """资产目录一确定就补 `README.md`（外部 Agent 读它才知道命名规范）。

    写失败不影响接口返回：这是文档，不是数据。
    """
    from config import get_config
    from naming_doc import ensure_for_workspace

    try:
        ensure_for_workspace(get_config(), entry)
    except Exception as exc:  # noqa: BLE001
        log.warning("[workspace] 「%s」命名规范 README 生成失败：%s", entry.name, exc)


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
    # 只改显示名（推荐）：不动目录、不动库
    label: Optional[str] = None
    # 真改目录名（危险）：会搬动整个工作区目录并改写库里五张表的 workspace 列
    name: Optional[str] = None


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
    _warm_index(entry)
    return {"workspace": entry, "active": entry.name}


@router.post("/open")
async def open_ws(body: OpenBody):
    """把已有目录登记为工作区（自动匹配资产目录，未命中可前端手动指定）。"""
    try:
        entry = open_workspace(body.root, body.name, body.asset_dir, body.create_missing)
    except WorkSpaceError as e:
        raise _err(e)
    _warm_index(entry)
    return {"workspace": entry, "active": entry.name}


@router.post("/{name}/activate")
async def activate_ws(name: str):
    try:
        ws = activate(name)
    except WorkSpaceError as e:
        raise _err(e)
    _warm_index(ws)
    return {"workspace": ws, "active": ws.name}


@router.patch("/{name}/folders")
async def set_ws_folders(name: str, body: FoldersBody):
    """改目录角色名（当前主要用于「没有匹配到资产目录名 → 手动选择」）。"""
    try:
        ws = set_folders(name, body.folders)
    except WorkSpaceError as e:
        raise _err(e)
    # 资产目录可能刚被改到另一个名字：新目录里也要有那份规范文档
    _write_naming_doc(ws)
    return {"workspace": ws}


class WatchDirsBody(BaseModel):
    dirs: list[str] = []


@router.get("/{name}/watch-dirs")
async def get_watch_dirs(name: str):
    """额外监听目录（收编源）。空数组 = 只收工作区自己的草稿目录。"""
    from config import get_config

    ws = next((w for w in get_config().workspaces if w.name == name), None)
    if ws is None:
        raise HTTPException(status_code=404, detail=f"工作区不存在：{name}")
    return {"name": ws.name, "watch_dirs": list(ws.watch_dirs or [])}


@router.patch("/{name}/watch-dirs")
async def patch_watch_dirs(name: str, body: WatchDirsBody):
    """整组替换。校验不过直接 400，**不静默丢弃**坏路径。"""
    try:
        ws = set_watch_dirs(name, body.dirs)
    except WorkSpaceError as e:
        raise _err(e)
    return {"name": ws.name, "watch_dirs": list(ws.watch_dirs or [])}


@router.get("/{name}/folder-migration")
async def get_folder_migration(name: str):
    """只读检查：旧的「库叫素材」布局要不要迁到 `资产/ + 素材/`。"""
    from folders_migrate import plan_migration

    try:
        return plan_migration(name)
    except WorkSpaceError as e:
        raise _err(e)


@router.post("/{name}/folder-migration")
async def run_folder_migration(name: str):
    """执行迁移（磁盘改名 + 配置 + 索引前缀改写）。可逆，但不自动重跑。"""
    from folders_migrate import apply_migration
    from config import get_config

    try:
        result = await asyncio.to_thread(apply_migration, name)
    except WorkSpaceError as e:
        raise _err(e)
    if result.get("status") == "done":
        _warm_index(next(w for w in get_config().workspaces if w.name == name))
    return result


@router.post("/{name}/intake")
async def run_intake(name: str):
    """立即收编一轮：额外监听目录的新文件 → 复制进 草稿/（只改能证实的名字）。"""
    from config import get_config
    from intake import intake_workspace

    cfg = get_config()
    ws = next((w for w in cfg.workspaces if w.name == name), None)
    if ws is None:
        raise HTTPException(status_code=404, detail=f"工作区不存在：{name}")

    stats = await asyncio.to_thread(intake_workspace, cfg, ws)
    if stats.get("copied"):
        # 收进来的 mp4 立刻登记成卡，不等下一轮后台刷新（人点了就要看到）
        try:
            import cardwatch
            from db import get_db

            root = Path(ws.root)
            await asyncio.to_thread(
                cardwatch.ensure_fresh,
                cfg,
                ws.name,
                get_db(root),
                root,
                min_gap=0.0,
                force=False,
            )
        except Exception as e:  # noqa: BLE001 —— 收编成功了，刷新失败不该让接口 500
            stats.setdefault("problems", []).append(f"卡池刷新失败：{e}")
    return stats


@router.patch("/{name}")
async def rename_ws(name: str, body: NameBody):
    """改名。`label` = 只改显示名（默认路径）；`name` = 连磁盘目录一起改（危险）。"""
    from config import get_config
    from workspaces import set_label

    if body.label is None and body.name is None:
        raise HTTPException(status_code=400, detail="要么给 label（显示名），要么给 name（目录名）")

    key = name
    ws: Optional[WorkspaceEntry] = None
    try:
        # 只有「确实要换目录名」才走危险路径
        if body.name is not None and body.name.strip() and body.name.strip() != name:
            ws = rename(name, body.name)
            key = ws.name  # 目录名已改，后续按新名找
        if body.label is not None:
            ws = set_label(key, body.label)
    except WorkSpaceError as e:
        raise _err(e)

    if ws is None:  # 传了 name 但和现名一样：当无操作，原样返回
        ws = next((w for w in get_config().workspaces if w.name == key), None)
        if ws is None:
            raise HTTPException(status_code=404, detail=f"工作区不存在：{key}")
    return {"workspace": ws}


@router.delete("/{name}")
async def delete_ws(name: str):
    try:
        return remove(name)
    except WorkSpaceError as e:
        raise _err(e)


@router.post("/{name}/scan")
async def scan_ws(name: str):
    """立即全量重探 草稿/ + 生成/（强制，不等指纹）；顺带补收 ComfyUI 产物 + 重扫资产库。

    平时不必点它：后台每 `config.watch.interval_sec` 秒会自己增量看一眼，
    打开审片台时也会先刷新一次。这个接口是「我现在就要，而且连旧片也重新探一遍」。
    """
    from config import get_config
    from db import get_db

    cfg = get_config()
    ws = next((w for w in cfg.workspaces if w.name == name), None)
    if ws is None:
        raise HTTPException(status_code=404, detail=f"工作区不存在：{name}")

    root = Path(ws.root)
    db = get_db(root)

    import cardwatch

    res = await asyncio.to_thread(cardwatch.ensure_fresh, cfg, ws.name, db, root, force=True)
    added, updated = int(res["added"]), int(res["updated"])

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

    # 补收会往 草稿/ 里写文件：把指纹推到最新，免得后台循环把刚落库的片子再探一遍
    try:
        await asyncio.to_thread(cardwatch.note_root, cfg, ws)
    except Exception:  # noqa: BLE001
        pass

    asset_folder = cfg.folder_name("asset", ws)
    if asset_folder and (root / asset_folder).is_dir():
        from routers.assets import _scan

        _scan(root / asset_folder, root, ws.name)

    return {"workspace": name, "added": added, "updated": updated, "rescued": rescued}
