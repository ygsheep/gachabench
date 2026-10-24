# -*- coding: utf-8 -*-
"""备料区：`{工作区}/{素材}/{段}/` —— 当前片段要用的素材。

## 它解决什么

提示词里写 `<Picture 1>` + 一个**文件名**（`白陌_写实_四视图.png`）。跑片时后端
得去资产库里猜哪张是它，猜完复制进 ComfyUI input —— 这个过程对人是不可见的：
你看不到这一段到底用了哪张图，也没法只换某一段而不动提示词。

备料区把这件事**显式化**：

    素材/01/白陌_写实_四视图.png     ← 这一段实际要送进 ComfyUI 的那份
    素材/02/白陌_写实_四视图.png     ← 第 2 段用的（可以是另一版）

- 文件名 == 提示词里写的名字 → **换素材直接换文件，提示词一个字都不用改**
- 它是那一段的**快照**，不是真相：改它不伤资产库，跨盘也能用
- 备料区里多出来的文件不影响跑片（跑片只按提示词要的名字取）

## 谁写

- `apply_prep()` 从资产库补（按需、不覆盖已有文件）
- 也可以人直接往 `素材/01/` 里拖、外部 Agent 生成

## 为什么不覆盖

备料区的文件**可能已经被人手工换过**（"这一段我想用这张"）。重新补料时如果
无条件覆盖，人的修改就被系统悄悄抹掉了。所以默认只补缺失的；
真要重置，传 `overwrite=True`（UI 上必须是显式动作）。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import quote

from naming import seg2

# 段 md 的素材上传表：`| Picture 1 | \`白陌_写实_四视图.png\` | 用途 |`
_ROW_RE = re.compile(
    r"^\s*\|\s*(Picture|Audio)\s*(\d+)\s*\|\s*`?([^`|\n]+?)`?\s*\|",
    re.IGNORECASE | re.MULTILINE,
)

KIND_BY_LABEL = {"picture": "image", "audio": "audio"}


def requirements(text: str) -> list[dict]:
    """从段 md 里抽出这一段要的素材名（按出现顺序去重）。

    返回 ``[{"n": 1, "kind": "image", "filename": "白陌_写实_四视图.png"}]``。

    🔴 只认表格里 `Picture N` / `Audio N` 那两列。散文里出现的文件名不算——
    那多半是在举例或否定（"撤掉 `风格_国漫3D_参考.webp`"），照它备料是灾难。
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for m in _ROW_RE.finditer(text):
        label, n, raw = m.group(1).lower(), int(m.group(2)), m.group(3).strip()
        name = raw.strip().strip("`").strip()
        if not name or name in ("-", "—", "无", "空"):
            continue
        kind = KIND_BY_LABEL.get(label, "image")
        key = (kind, name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"n": n, "kind": kind, "filename": name})
    return out


def find_asset(asset_dir: Optional[Path], filename: str) -> Optional[Path]:
    """在资产库里找这个文件：先精确同名，再同名不同扩展名（`.jpg`/`.png` 都算）。"""
    if asset_dir is None or not asset_dir.is_dir():
        return None
    want = Path(filename).name
    hits = [p for p in asset_dir.rglob(want) if p.is_file()]
    if hits:
        return sorted(hits, key=lambda p: (len(p.parts), str(p)))[0]
    stem = Path(want).stem
    loose = [
        p
        for p in asset_dir.rglob(f"{stem}.*")
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    ]
    return sorted(loose, key=lambda p: (len(p.parts), str(p)))[0] if loose else None


def seg_dir(cfg, seg: object) -> Optional[Path]:
    """`素材/01`（不创建）。material 目录未配置时返回 None。"""
    s = seg2(seg)
    base = cfg.subdir("material")
    if s is None or base is None:
        return None
    return base / s


def _preview_url(root: Optional[Path], path: Optional[Path]) -> Optional[str]:
    """工作区内的文件 → 预览 URL（`/api/assets/file/{rel}`）。越界一律不给。"""
    if root is None or path is None:
        return None
    try:
        rel = path.resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return None
    return f"/api/assets/file/{quote(rel)}"


def plan_prep(cfg, seg: object, *, text: Optional[str] = None) -> dict:
    """算出这一段要什么、哪些已就位、哪些能从资产库补、哪些补不了。**只读。**"""
    from prompt_locator import find_segment_md

    s = seg2(seg)
    out: dict = {
        "seg": s,
        "dir": None,
        "prompt_file": None,
        "items": [],
        "ready": 0,
        "copyable": 0,
        "missing": 0,
        "problems": [],
    }
    if s is None:
        out["problems"].append("段号不可用")
        return out

    md = find_segment_md(cfg, s)
    if md is None:
        out["problems"].append(f"未找到第 {s} 段的提示词 md")
        return out
    out["prompt_file"] = md.name
    body = text if text is not None else md.read_text(encoding="utf-8")

    root = cfg.ws_root()
    target_dir = seg_dir(cfg, s)
    out["dir"] = str(target_dir) if target_dir else None
    asset_dir = cfg.subdir("asset")

    for req in requirements(body):
        prep_file = target_dir / req["filename"] if target_dir else None
        hit = None
        if prep_file is not None and prep_file.is_file():
            status, source, preview = "in-prep", None, _preview_url(root, prep_file)
        else:
            hit = find_asset(asset_dir, req["filename"])
            if hit is None:
                status, source, preview = "missing", None, None
            else:
                status = "copyable"
                source = hit.name
                preview = _preview_url(root, hit)
        out["items"].append(
            {**req, "status": status, "source": source, "preview": preview}
        )
        out[{"in-prep": "ready", "copyable": "copyable", "missing": "missing"}[status]] += 1

    if target_dir is None:
        out["problems"].append("工作区未配置「素材」目录，无法备料")
    return out


def apply_prep(
    cfg,
    seg: object,
    *,
    overwrite: bool = False,
    only: Optional[Iterable[str]] = None,
) -> dict:
    """按计划把缺失的素材从资产库复制进 `素材/{段}/`。

    - 默认**不覆盖**已在备料区的文件（人可能手工换过）
    - ``only`` = 只补指定的几个文件名
    - 单文件失败只记 problems，不中断
    """
    plan = plan_prep(cfg, seg)
    stats = {"seg": plan["seg"], "dir": plan["dir"], "copied": 0, "skipped": 0,
             "missing": [], "problems": list(plan["problems"])}
    if plan["dir"] is None:
        return stats

    target_dir = Path(plan["dir"])
    wanted = set(only) if only else None

    for item in plan["items"]:
        name = item["filename"]
        if wanted is not None and name not in wanted:
            continue
        dst = target_dir / name
        if dst.exists() and not overwrite:
            stats["skipped"] += 1
            continue
        # 覆盖模式下「已在备料区」的那一条也要重新找源，所以不能只看 item["source"]
        src = find_asset(cfg.subdir("asset"), name)
        if src is None:
            stats["missing"].append(name)
            continue
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            stats["copied"] += 1
        except OSError as exc:
            stats["problems"].append(f"{name} 复制失败：{exc}")

    return stats


def prep_path(cfg, seg: object, filename: str) -> Optional[Path]:
    """跑片时优先取这里的那份（`_materialize` 用）。"""
    d = seg_dir(cfg, seg)
    if d is None:
        return None
    p = d / Path(filename).name
    return p if p.is_file() else None
