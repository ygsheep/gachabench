# -*- coding: utf-8 -*-
"""收编器：把「额外监听目录」里的新产物复制进工作区 `草稿/`。

## 为什么是复制，不是就地索引

现有导出、删除、审片预览、`rel_path` 相对路径、路径前缀比较 —— 全都假设
「文件在工作区里」。就地索引等于把这些改成「可能是任意盘符的绝对路径」，
几十处改动，每一处漏改都是**静默出错**（预览黑屏、导出丢文件）。
复制的代价只是磁盘多一份；5 秒的 mp4 几十 MB，可接受。

## 只改能证实的名字

- 模板名 / 带 `NofM` 的 ComfyUI 落盘名 → 能解析出段号块号 → 按模板改名
  （`17_第17段_1of3_00001.mp4` → `17.1_成片.mp4`）
- 解析不出（`output_00042.mp4`）→ **原名一个字不动**，标记 `unclaimed`
- 素材名（`白兴学_参考.png`）→ 保持，它本来就是它的名字

不做 `00` 占位：`00` 会变成事实上的垃圾桶，三个月后没人敢动。

## 指纹

`{index_dir}/intake.json` 记 `源绝对路径 -> (size, mtime, dst)`。同一份文件
不会被收编两次；源被删掉后指纹顺手清掉。这张表是**缓存**，删了最多重收一遍
（重名会自动 `_02`，不会覆盖已有文件）。

## 不做什么

- 不删源文件（要删也得是显式开关，不是默认行为）。
- 不监听「工作区自身」及其内部目录 —— 那会把 `草稿/` 里已有的片子再复制一份，
  下一轮再复制一份，无限增殖。这种情况直接拒绝并记问题。
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Iterable, Optional

import naming

STATE_NAME = "intake.json"
MAX_FILES_PER_DIR = 2000
MAX_DIRS = 20

# 只收这些扩展名：`prompt.json`、`.md`、`.py` 这类附属文件不该被当成产物收编
MEDIA_EXT = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".avif",
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
}

SKIP_DIRS = {".gachabench", ".git", "node_modules", "__pycache__", "$RECYCLE.BIN"}


# ---------------------------------------------------------------------------
# 指纹
# ---------------------------------------------------------------------------

def state_path(index_dir: Path) -> Path:
    return Path(index_dir) / STATE_NAME


def load_state(index_dir: Path) -> dict:
    try:
        raw = state_path(index_dir).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        # 读不了就当没有：最坏结果是重收一遍，不是崩
        return {}


def save_state(index_dir: Path, state: dict) -> None:
    p = state_path(index_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------------------
# 命名：只改能证实的
# ---------------------------------------------------------------------------

def plan_name(name: str) -> tuple[str, str]:
    """源文件名 → (目标文件名, 依据)。依据 ∈ template / legacy / asset。

    🔴 不猜段号：解析不出段号就原样保留（依据记 asset）。宁可 `草稿/` 里有个
    丑名字，也不要它出现在错的段里 —— 后者不报警，前者一眼就能看见。
    """
    ext = Path(name).suffix
    kind = naming.classify(name)

    if kind == "shot":
        shot = naming.parse_shot(name)
        if shot is not None:
            return naming.compose_shot(shot.seg, shot.chunk, shot.use or "成片", ext), "template"

    if kind == "asset":
        return name, "asset"

    seg = naming.seg_from_path(name)
    if seg:
        chunk, source = naming.resolve_chunk(name)
        # 有段号没块号：只写段号，绝不编一个 .1 出来
        return naming.compose_shot(seg, chunk if source != "none" else None, "成片", ext), "legacy"
    return name, "asset"


def unique_path(dst_dir: Path, name: str) -> Path:
    """不覆盖：重名依次 `_02` `_03`…

    对镜头类名字来说 `_02` 正好是模板里的重名序号位，`parse_shot` 还能把它
    当 seq 读回来，不会污染主体/用途字段。
    """
    cand = dst_dir / name
    if not cand.exists():
        return cand
    stem, suf = os.path.splitext(name)
    for i in range(2, 1000):
        cand = dst_dir / f"{stem}_{i:02d}{suf}"
        if not cand.exists():
            return cand
    raise OSError(f"{name} 的重名副本太多（已到 _999）")


# ---------------------------------------------------------------------------
# 遍历
# ---------------------------------------------------------------------------

def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _walk_media(base: Path, limit: int) -> Iterable[Path]:
    """深度优先找媒体文件（跳过 .git / __pycache__ 这类）。"""
    count = 0
    stack = [base]
    while stack:
        cur = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in SKIP_DIRS or entry.name.startswith(".tmp-"):
                        continue
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    if Path(entry.name).suffix.lower() not in MEDIA_EXT:
                        continue
                    count += 1
                    if count > limit:
                        return
                    yield Path(entry.path)
            except OSError:
                continue


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def ingest_dirs(
    watch_dirs: Iterable[str | os.PathLike],
    draft_dir: str | os.PathLike,
    index_dir: str | os.PathLike,
    *,
    workspace_root: Optional[str | os.PathLike] = None,
    limit_per_dir: int = MAX_FILES_PER_DIR,
) -> dict:
    """收编一轮。返回统计，**永不抛异常**（单个文件失败只记 problems）。

    ``{"dirs","scanned","copied","skipped","renamed","unclaimed","problems"}``
    """
    draft = Path(draft_dir)
    idx = Path(index_dir)
    stats = {
        "dirs": 0,
        "scanned": 0,
        "copied": 0,
        "skipped": 0,
        "renamed": 0,
        "unclaimed": 0,
        "problems": [],
    }
    dirs = [Path(d) for d in watch_dirs if str(d or "").strip()]
    if not dirs:
        return stats

    draft.mkdir(parents=True, exist_ok=True)
    state = load_state(idx)
    seen: set[str] = set()

    for base in dirs[:MAX_DIRS]:
        if not base.is_dir():
            stats["problems"].append(f"监听目录不存在：{base}")
            continue
        if workspace_root is not None and _is_inside(base, Path(workspace_root)):
            # 监听工作区自己 = 把 草稿/ 里的片子再收一遍，无限增殖
            stats["problems"].append(f"监听目录在工作区内部，已跳过：{base}")
            continue
        stats["dirs"] += 1

        for src in _walk_media(base, limit_per_dir):
            stats["scanned"] += 1
            key = str(src.resolve())
            seen.add(key)
            try:
                st = src.stat()
            except OSError:
                continue
            prev = state.get(key)
            if (
                isinstance(prev, dict)
                and prev.get("size") == st.st_size
                and abs(float(prev.get("mtime") or 0) - st.st_mtime) < 1e-6
            ):
                stats["skipped"] += 1
                continue

            target_name, basis = plan_name(src.name)
            if target_name != src.name:
                stats["renamed"] += 1
            # 未认领 = 复制进草稿但没有任何段号可依（抽卡台无处安放，要人工指定）。
            # 只算视频/音频：图片本来就可能是素材，不该被算成「未认领的片子」。
            if (
                basis == "asset"
                and naming.seg_from_path(src.name) is None
                and naming.guess_kind(src.name) in ("video", "audio")
            ):
                stats["unclaimed"] += 1
            try:
                dst = unique_path(draft, target_name)
                shutil.copy2(src, dst)
            except OSError as exc:
                stats["problems"].append(f"{src.name} 复制失败：{exc}")
                continue

            stats["copied"] += 1
            state[key] = {
                "size": st.st_size,
                "mtime": st.st_mtime,
                "dst": draft.name + "/" + dst.name,
                "basis": basis,
            }

    # 源已经不在的指纹清掉（避免 json 无限长）
    for key in list(state):
        if key not in seen and not Path(key).exists():
            state.pop(key, None)

    try:
        save_state(idx, state)
    except OSError as exc:
        stats["problems"].append(f"指纹写入失败：{exc}")

    stats["problems"] = stats["problems"][:20]
    return stats


def intake_workspace(cfg, ws) -> dict:
    """按工作区配置收编一轮。没有配监听目录时是零成本（不 stat 任何东西）。"""
    dirs = list(getattr(ws, "watch_dirs", []) or [])
    if not dirs:
        return {"dirs": 0, "scanned": 0, "copied": 0, "skipped": 0,
                "renamed": 0, "unclaimed": 0, "problems": []}
    root = Path(ws.root)
    draft = cfg.subdir("draft")
    if draft is None:
        return {"dirs": 0, "scanned": 0, "copied": 0, "skipped": 0,
                "renamed": 0, "unclaimed": 0, "problems": ["工作区未配置「草稿」目录"]}
    return ingest_dirs(
        dirs,
        draft,
        root / cfg.index_dir,
        workspace_root=root,
    )
