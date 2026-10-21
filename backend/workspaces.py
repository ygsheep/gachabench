# -*- coding: utf-8 -*-
"""工作区：新建 / 打开（登记已有目录）/ 切换 / 重命名 / 目录角色匹配。

「目录角色」= draft/final/prompt/storyboard/asset 五个内容目录。
新建与打开时都会**自动匹配**已有目录：asset（资产）目录名各家不同
（素材 / 资产 / assets …），按 config.asset_dir_candidates 顺序探测；
一个都没命中时不猜，留空让前端手动选择（见 PATCH /api/workspaces/{name}/folders）。
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import (
    FOLDER_KEYS,
    Config,
    WorkspaceEntry,
    get_config,
    set_config,
)


class WorkSpaceError(Exception):
    pass


# 目录名里不能出现的字符（Windows 保留字符 + 路径分隔符 + 控制字符）
_ILLEGAL_DIRNAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _clean_dirname(name: str) -> str:
    """清洗目录名：去保留字符、去首尾空白与点；非法则抛错。"""
    s = _ILLEGAL_DIRNAME_RE.sub("", (name or "").strip()).strip(" .")
    if not s or s in (".", ".."):
        raise WorkSpaceError("目录名非法：不能为空，且不能含 \\ / : * ? \" < > |")
    return s


# ---------------------------------------------------------------------------
# 目录探测 / 自动匹配
# ---------------------------------------------------------------------------

def _child_dirs(root: Path) -> list[str]:
    """根下一级子目录名（跳过隐藏目录如 .gachabench）。"""
    if not root.is_dir():
        return []
    return sorted(
        (p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=str.lower,
    )


def match_asset_dir(root: Path, cfg: Config) -> Optional[str]:
    """按候选名在 root 的一级子目录里找资产目录（大小写不敏感，按候选顺序）。

    root 不存在 / 一个候选都没命中 → 返回 None（交给前端手动选择）。
    """
    if not root.is_dir():
        return None
    by_lower = {n.lower(): n for n in _child_dirs(root)}
    for cand in cfg.asset_dir_candidates:
        hit = by_lower.get(cand.lower())
        if hit:
            return hit
    return None


def detect_asset_dir(root: Path, cfg: Config, manual: Optional[str] = None) -> Optional[str]:
    """给定「手动指定优先，其次自动匹配」。manual 为空串则代表「就是不要匹配」。"""
    if manual is not None:
        return _clean_dirname(manual) if manual.strip() else None
    return match_asset_dir(root, cfg)


def inspect_root(root: Path, cfg: Config) -> dict:
    """看一个已有目录：列出子目录、自动匹配资产目录、给出候选与已命中的角色目录。

    新建（目录还不存在）与打开（目录已存在）两种情况前端共用这个结果。
    """
    root = Path(root).expanduser()
    exists = root.is_dir()
    subdirs = _child_dirs(root)
    matched = match_asset_dir(root, cfg) if exists else None

    lower = {n.lower(): n for n in subdirs}
    hits: dict[str, str] = {}
    for key in FOLDER_KEYS:
        want = getattr(cfg.folders, key)
        got = lower.get(want.lower())
        if got:
            hits[key] = got
    if matched:
        hits["asset"] = matched

    return {
        "root": str(root),
        "exists": exists,
        "subdirs": subdirs,
        "asset_dir": matched,
        "asset_candidates": list(cfg.asset_dir_candidates),
        "matched": hits,
        "suggested_name": root.name if exists else "",
    }


# ---------------------------------------------------------------------------
# 目录骨架
# ---------------------------------------------------------------------------

def build_workspace_dirs(root: Path, cfg: Config, names: dict[str, str]) -> dict[str, Path]:
    """按 names 在工作区根下创建内容目录 + .gachabench/。幂等；空名（未确定）跳过。"""
    root.mkdir(parents=True, exist_ok=True)
    dirs: dict[str, Path] = {}
    for key in FOLDER_KEYS:
        folder = names.get(key)
        if not folder:
            continue
        p = root / folder
        p.mkdir(parents=True, exist_ok=True)
        dirs[key] = p
    (root / cfg.index_dir).mkdir(parents=True, exist_ok=True)
    return dirs


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_workspaces() -> dict:
    cfg = get_config()
    return {"workspaces": cfg.workspaces, "active": cfg.active}


def create_workspace(
    parent_dir: str, name: str, asset_dir: Optional[str] = None
) -> WorkspaceEntry:
    cfg = get_config()
    name = name.strip()
    if not name or not re.fullmatch(r"[\w\u4e00-\u9fff\- ]+", name):
        raise WorkSpaceError("工作区名称非法：仅允许中文/字母/数字/下划线/连线/空格")

    for ws in cfg.workspaces:
        if ws.name == name:
            raise WorkSpaceError(f"已存在同名工作区：{name}")

    root = Path(parent_dir).expanduser()
    if not root.is_dir():
        raise WorkSpaceError(f"父目录不存在：{root}")
    ws_root = root / name
    if ws_root.exists() and not (ws_root / cfg.index_dir).is_dir():
        # 已有目录里没有 .gachabench 标记：允许，但要求是空目录或纯内容目录
        if _child_dirs(ws_root):
            raise WorkSpaceError(
                f"目标目录已存在且看起来是别人的：{ws_root}（请改用「打开项目」）"
            )

    # 目录名解析：手动指定优先，其次自动匹配已有目录，最后落全局默认
    resolved = cfg.folder_names_for(None)
    if asset_dir is not None:
        resolved["asset"] = _clean_dirname(asset_dir) if asset_dir.strip() else None
    else:
        hit = match_asset_dir(ws_root, cfg)
        resolved["asset"] = hit or cfg.folders.asset

    names = {k: v for k, v in resolved.items() if v}
    build_workspace_dirs(ws_root, cfg, names)

    override: dict[str, str] = {}
    for key in FOLDER_KEYS:
        val = resolved.get(key) or ""
        if val != getattr(cfg.folders, key):
            override[key] = val
    # 资产目录未确定（打开但没命中）时写空串显式表达
    if not resolved.get("asset"):
        override["asset"] = ""

    entry = WorkspaceEntry(
        name=name,
        root=str(ws_root),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        folders=override,
    )
    cfg.workspaces.append(entry)
    cfg.active = name
    set_config(cfg)
    return entry


def open_workspace(
    root_dir: str,
    name: Optional[str] = None,
    asset_dir: Optional[str] = None,
    create_missing: bool = True,
) -> WorkspaceEntry:
    """登记一个已存在的目录为工作区（自动匹配 / 手动指定资产目录）。"""
    cfg = get_config()
    root = Path(root_dir).expanduser()
    if not root.is_dir():
        raise WorkSpaceError(f"目录不存在：{root}")
    if root == Path(root.anchor):
        raise WorkSpaceError("不能把盘符根目录当作工作区")

    ws_name = (name or "").strip() or root.name
    if not re.fullmatch(r"[\w\u4e00-\u9fff\- ]+", ws_name):
        raise WorkSpaceError("工作区名称非法：仅允许中文/字母/数字/下划线/连线/空格")
    for ws in cfg.workspaces:
        if ws.name == ws_name:
            if Path(ws.root).resolve() == root.resolve():
                return ws  # 已登记且指向同一目录：幂等
            raise WorkSpaceError(f"已存在同名工作区：{ws_name}")

    resolved = cfg.folder_names_for(None)
    resolved["asset"] = detect_asset_dir(root, cfg, asset_dir)

    if create_missing:
        names = {k: v for k, v in resolved.items() if v}
        build_workspace_dirs(root, cfg, names)
    else:
        (root / cfg.index_dir).mkdir(parents=True, exist_ok=True)

    override: dict[str, str] = {}
    for key in FOLDER_KEYS:
        val = resolved.get(key) or ""
        if val != getattr(cfg.folders, key):
            override[key] = val

    entry = WorkspaceEntry(
        name=ws_name,
        root=str(root),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        folders=override,
    )
    cfg.workspaces.append(entry)
    cfg.active = ws_name
    set_config(cfg)
    return entry


def activate(name: str) -> WorkspaceEntry:
    cfg = get_config()
    for ws in cfg.workspaces:
        if ws.name == name:
            cfg.active = name
            set_config(cfg)
            return ws
    raise WorkSpaceError(f"工作区不存在：{name}")


def set_folders(name: str, mapping: dict[str, str]) -> WorkspaceEntry:
    """设置某工作区的目录角色名（如 asset="资产"）。空串 = 标记为「未确定」。"""
    cfg = get_config()
    for ws in cfg.workspaces:
        if ws.name == name:
            root = Path(ws.root)
            for key, raw in mapping.items():
                if key not in FOLDER_KEYS:
                    raise WorkSpaceError(f"未知目录角色：{key}")
                val = _clean_dirname(raw) if (raw or "").strip() else ""
                ws.folders[key] = val
                if val:
                    (root / val).mkdir(parents=True, exist_ok=True)
            set_config(cfg)
            return ws
    raise WorkSpaceError(f"工作区不存在：{name}")


def rename(name: str, new_name: str) -> WorkspaceEntry:
    cfg = get_config()
    new_name = new_name.strip()
    if not new_name or not re.fullmatch(r"[\w\u4e00-\u9fff\- ]+", new_name):
        raise WorkSpaceError("新名称非法")
    for ws in cfg.workspaces:
        if ws.name == new_name:
            raise WorkSpaceError(f"已存在同名工作区：{new_name}")
    for ws in cfg.workspaces:
        if ws.name == name:
            old = Path(ws.root)
            new_root = old.parent / new_name
            if old.exists():
                old.rename(new_root)
            ws.name = new_name
            ws.root = str(new_root)
            if cfg.active == name:
                cfg.active = new_name
            set_config(cfg)
            return ws
    raise WorkSpaceError(f"工作区不存在：{name}")


def remove(name: str) -> dict:
    """仅从列表移除（不删盘）。"""
    cfg = get_config()
    before = len(cfg.workspaces)
    cfg.workspaces = [ws for ws in cfg.workspaces if ws.name != name]
    if len(cfg.workspaces) == before:
        raise WorkSpaceError(f"工作区不存在：{name}")
    if cfg.active == name:
        cfg.active = cfg.workspaces[0].name if cfg.workspaces else None
    set_config(cfg)
    return {"removed": name, "active": cfg.active}


# ---------------------------------------------------------------------------
# 扫描工作区内已注册的媒体目录（供 P2 复用）
# ---------------------------------------------------------------------------

def scan_workspace_dirs() -> dict[str, Optional[Path]]:
    cfg = get_config()
    if cfg.ws_root() is None:
        return {}
    return {key: cfg.subdir(key) for key in FOLDER_KEYS}
