# -*- coding: utf-8 -*-
"""目录职责重排的迁移：旧布局 → `资产/` + `素材/`。

## 为什么需要迁移

2026-10-24 之前 `asset`（资产库）的默认名就是「素材」，全库平铺在 `素材/`。
重排之后：

- `资产/` = 所有素材的总库（按 类别/主体 归档）
- `素材/` = 当前片段要用的备料区（按段分目录 `素材/01/`）

旧工作区如果不动，两个角色会抢同一个目录名。

## 迁移做什么（一步，可逆）

1. 磁盘：`{工作区}/素材` → `{工作区}/资产`（同一父目录，瞬时改名）
2. 配置：`ws.folders.asset = "资产"`、`ws.folders.material = "素材"`
3. 建空目录 `{工作区}/素材/`（备料区，按段自动长出子目录）
4. 索引：把 `index_assets.path` / `dir_path`、`index_dirs.path` 的 `素材/` 前缀
   改成 `资产/` —— 不改的话整库会变成「一片 missing + 一片新增」的幽灵。

## 为什么改名安全

提示词里的 `<Picture N>` 只写**文件名**，不写路径。改的是目录名，
提示词一个字节都不用动。真正怕改名的是文件名，不是目录名。

## 什么时候不该动

- 已经有 `资产/` 目录 → 认为已迁移，直接返回 `already`
- `素材/` 不存在 → 没东西可迁，返回 `nothing`
- 两个都在 / 盘上同名冲突 → 返回 `blocked`，交给人处理，**绝不猜**
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from config import get_config, set_config
from workspaces import WorkSpaceError

LEGACY_LIB = "素材"
NEW_LIB = "资产"
MATERIAL = "素材"


def _find(name: str):
    cfg = get_config()
    for ws in cfg.workspaces:
        if ws.name == name:
            return cfg, ws
    raise WorkSpaceError(f"工作区不存在：{name}")


def plan_migration(name: str) -> dict:
    """只读检查：该工作区要不要迁移、要动哪些东西。"""
    cfg, ws = _find(name)
    root = Path(ws.root)
    lib = cfg.folder_name("asset", ws) or ""
    src = root / LEGACY_LIB
    dst = root / NEW_LIB

    base = {
        "workspace": name,
        "root": str(root),
        "asset_folder": lib,
        "material_folder": cfg.folder_name("material", ws) or "",
        "from": LEGACY_LIB,
        "to": NEW_LIB,
    }
    if lib and lib != LEGACY_LIB:
        return {**base, "status": "already", "steps": [], "reason": f"资产目录已经是「{lib}」"}
    if lib in ("", None) and dst.is_dir():
        return {**base, "status": "already", "steps": [], "reason": f"已有「{NEW_LIB}」目录"}
    if not src.is_dir():
        return {**base, "status": "nothing", "steps": [], "reason": f"没有「{LEGACY_LIB}」目录，无需迁移"}
    if dst.exists():
        return {
            **base,
            "status": "blocked",
            "steps": [],
            "reason": f"「{NEW_LIB}」已存在，拒绝覆盖或合并；请先手工处理其中一个",
        }

    return {
        **base,
        "status": "pending",
        "steps": [
            f"磁盘改名 {src} → {dst}",
            f"配置：asset={NEW_LIB!r}，material={MATERIAL!r}",
            f"新建空目录 {root / MATERIAL}（备料区）",
            "索引：把 index_assets / index_dirs 的「素材/」前缀改成「资产/」",
        ],
        "reason": "",
    }


def _rewrite_index(root: Path) -> dict:
    """把索引里的路径前缀从 素材/ 换到 资产/。返回改了多少行。

    ⚠️ 应用可能正在跑（它握着同一个 cards.db）。首次连接偶尔会撞上
    `unable to open database file`（WAL 恢复 + 并发打开），重试两次即可；
    真不行就抛出去让调用方回滚改名，绝不留半迁移状态。
    """
    import time

    last: Optional[Exception] = None
    for attempt in range(3):
        try:
            return _rewrite_index_once(root)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.3 * (attempt + 1))
    raise last if last else RuntimeError("索引改写失败")


def _rewrite_index_once(root: Path) -> dict:
    """前缀改写 + 去重。

    🔴 必须容忍「同一个文件在两处各有一行」：应用在跑的时候，改名那几秒里
    后台扫描可能已经把 `资产/…` 收进索引，于是改前缀会撞 `UNIQUE(path)`。
    处理原则：**保留旧行**（它挂着 asset_meta / asset_ref —— 人写的标签和备注），
    删掉并发扫描新建的那一行（它只有 stat + 哈希，可重新探测）。
    """
    from modules.platform.db import session_scope
    from modules.assets.models import IndexAsset, IndexDir

    old, new = LEGACY_LIB, NEW_LIB
    stats = {"assets": 0, "dirs": 0, "merged": 0}

    with session_scope(root) as s:
        rows = s.query(IndexAsset).all()
        by_path = {r.path: r for r in rows}
        todo: list[tuple[object, str]] = []
        # ⚠️ 必须分两趟：SQLAlchemy 的单位工作把 UPDATE 排在 DELETE 之前，
        # 一趟做完会先撞 UNIQUE(path) 再删重复行。先删、flush，再改路径。
        with s.no_autoflush:
            for a in rows:
                p = a.path or ""
                if not (p == old or p.startswith(old + "/")):
                    continue
                target = new + p[len(old):]
                clash = by_path.get(target)
                if clash is not None and clash.id != a.id:
                    s.delete(clash)
                    by_path.pop(target, None)
                    stats["merged"] += 1
                todo.append((a, target))
        s.flush()
        for a, target in todo:
            a.path = target
            d = a.dir_path or ""
            if d == old or d.startswith(old + "/"):
                a.dir_path = new + d[len(old):]
            by_path[target] = a
            stats["assets"] += 1

        drows = s.query(IndexDir).all()
        dmap = {r.path: r for r in drows}
        dtodo: list[tuple[object, str]] = []
        with s.no_autoflush:
            for row in drows:
                p = row.path or ""
                if not (p == old or p.startswith(old + "/")):
                    continue
                target = new + p[len(old):]
                clash = dmap.get(target)
                if clash is not None and clash is not row:
                    s.delete(clash)
                    dmap.pop(target, None)
                    stats["merged"] += 1
                dtodo.append((row, target))
        s.flush()
        for row, target in dtodo:
            row.path = target
            dmap[target] = row
            stats["dirs"] += 1

    return stats


def apply_migration(name: str) -> dict:
    """执行迁移。先跑 plan，状态不是 pending 就直接返回，不做任何改动。"""
    plan = plan_migration(name)
    if plan["status"] != "pending":
        return plan

    cfg, ws = _find(name)
    root = Path(ws.root)
    src = root / LEGACY_LIB
    dst = root / NEW_LIB

    # ① 磁盘改名（失败就整件事不做，配置一个字都不改）
    try:
        src.rename(dst)
    except OSError as exc:
        return {**plan, "status": "blocked", "reason": f"目录改名失败：{exc}"}

    # ② 索引前缀改写（改名之后立刻做，别留一个「全 missing」的中间态）
    moved = {"assets": 0, "dirs": 0}
    try:
        moved = _rewrite_index(root)
    except Exception as exc:  # noqa: BLE001 —— 索引改失败要回滚改名，否则库里全是幽灵
        try:
            dst.rename(src)
        except OSError:
            pass
        return {**plan, "status": "blocked", "reason": f"索引改写失败，已回滚目录改名：{exc}"}

    # ③ 配置 + 备料区空目录
    ws.folders["asset"] = NEW_LIB
    ws.folders["material"] = MATERIAL
    set_config(cfg)
    material_dir = root / MATERIAL
    material_dir.mkdir(parents=True, exist_ok=True)

    return {
        **plan,
        "status": "done",
        "rewritten": moved,
        "material_dir": str(material_dir),
    }


def auto_migrate_if_needed(name: str) -> Optional[dict]:
    """打开/激活工作区时顺手做一次。只在计划明确是 pending 时执行。

    ⚠️ 这是**有磁盘副作用**的动作，但它是可逆的目录改名，且只在旧布局上触发；
    新工作区完全不受影响。返回 None 表示什么都不用做。
    """
    try:
        plan = plan_migration(name)
    except WorkSpaceError:
        return None
    if plan["status"] != "pending":
        return None
    return apply_migration(name)


def copy_tree(src: Path, dst: Path) -> None:  # pragma: no cover - 仅供人工兜底
    """跨盘时的兜底（rename 会 EXDEV）。目前不自动用，留个口子。"""
    shutil.copytree(src, dst)
