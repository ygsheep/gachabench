# -*- coding: utf-8 -*-
"""P2 引用扫描：把文本文件里出现的资产文件名写进 `asset_ref`。

匹配规则：
- 只扫 `.md` / `.txt` / `.json`，跳过 `.gachabench`、`.git`、`node_modules` 等目录；
- 二进制或超过 2MB 的文本直接跳过；
- 按完整文件名匹配，前后不能紧邻字母、数字、下划线或汉字，
  避免 `A.png` 命中 `AA.png`（不用 `\\b`，中文场景不可靠）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from modules.assets import roles
from modules.assets.models import AssetRef, IndexAsset, now_iso
from modules.platform.ids import ulid

TEXT_SUFFIXES = {".md", ".txt", ".json"}
MAX_TEXT_BYTES = 2 * 1024 * 1024

# 文件名前后允许作为边界的字符之外，这些字符紧邻时不算命中。
_BOUNDARY = (
    "A-Za-z0-9_"
    "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
)


def _dir_heuristic(rel_path: str) -> str:
    """索引里查不到角色时，用目录名猜 prompt / storyboard。"""
    parts = [p.casefold() for p in Path(rel_path).parts[:-1]]
    for part in parts:
        if "提示词" in part or "prompt" in part:
            return "prompt"
        if "分镜" in part or "storyboard" in part:
            return "storyboard"
    return "other"


def read_text_with_encoding(
    path: Path, max_bytes: int = MAX_TEXT_BYTES
) -> Optional[tuple[str, str]]:
    """读文本，返回 (内容, 编码)；二进制、过大或不可解码返回 None。

    只有原文件带 BOM 时才记 `utf-8-sig`，否则用裸 `utf-8`，避免回写时凭空多出 BOM。
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > max_bytes or b"\x00" in data[:8192]:
        return None
    first = "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
    for enc in (first, "gb18030"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return None


def iter_text_files(root: Path) -> Iterator[tuple[str, Path]]:
    """递归列出工作区内可扫描的文本文件（相对 POSIX 路径, 绝对路径）。"""
    root = Path(root).resolve()
    stack: list[tuple[str, Path]] = [("", root)]
    while stack:
        rel_dir, abs_dir = stack.pop()
        try:
            entries = list(os.scandir(abs_dir))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if roles.is_skipped_dir(entry.name):
                        continue
                    child = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                    stack.append((child, Path(entry.path)))
                elif entry.is_file(follow_symlinks=False):
                    name = entry.name
                    if name.startswith(".tmp-"):
                        continue
                    if Path(name).suffix.lower() not in TEXT_SUFFIXES:
                        continue
                    rel = f"{rel_dir}/{name}" if rel_dir else name
                    yield rel, Path(entry.path)
            except OSError:
                continue


def _compile(names: list[str]) -> Optional[re.Pattern[str]]:
    if not names:
        return None
    # 长名优先，先吃掉更长的完整文件名。
    ordered = sorted(set(names), key=len, reverse=True)
    body = "|".join(re.escape(n) for n in ordered)
    return re.compile(rf"(?<![{_BOUNDARY}])(?:{body})(?![{_BOUNDARY}])")


def find_name_spans(text: str, name: str) -> list[tuple[int, int]]:
    """返回文件名在文本中的完整名边界命中区间。"""
    pattern = re.compile(rf"(?<![{_BOUNDARY}]){re.escape(name)}(?![{_BOUNDARY}])")
    return [(m.start(), m.end()) for m in pattern.finditer(text)]


def _kind_for(rel_path: str, line_text: str, role_map: dict[str, str]) -> str:
    suffix = Path(rel_path).suffix.lower()
    role = role_map.get(rel_path) or _dir_heuristic(rel_path)
    if suffix == ".md":
        if role == "prompt":
            return "prompt_filename" if "|" in line_text else "prompt_body"
        if role == "storyboard":
            return "storyboard_material"
        return "mention"
    if suffix == ".json":
        return "workflow_input"
    return "mention"


def _line_of(text: str, start: int) -> tuple[int, int, str]:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start)
    if line_end < 0:
        line_end = len(text)
    return (
        text.count("\n", 0, start) + 1,
        start - line_start,
        text[line_start:line_end].strip()[:300],
    )


def scan_refs(
    root: Path, session: Session, filenames: Optional[list[str]] = None
) -> int:
    """刷新引用表，返回写入条数。

    `filenames` 为空表示整库扫描（先清空所有引用再重建）；给定文件名时只刷新
    这些资产的引用，其他资产的记录不动。
    """
    root = Path(root).resolve()
    assets = session.scalars(
        select(IndexAsset).where(IndexAsset.status != "missing")
    ).all()
    name_map: dict[str, str] = {}
    role_map: dict[str, str] = {}
    for asset in assets:
        if asset.name and asset.name not in name_map:
            name_map[asset.name] = asset.id
        role_map[asset.path] = asset.role

    partial = filenames is not None
    if partial:
        wanted = {n for n in (filenames or []) if n in name_map}
        if not wanted:
            return 0
        match_map = {n: name_map[n] for n in wanted}
        target_ids = set(match_map.values())
    else:
        match_map = dict(name_map)
        target_ids = set(name_map.values())

    if not match_map:
        if not partial:
            session.execute(delete(AssetRef))
        return 0

    pattern = _compile(list(match_map))
    if pattern is None:
        return 0

    if not partial:
        session.execute(delete(AssetRef))

    inserted = 0
    for rel_path, abs_path in iter_text_files(root):
        loaded = read_text_with_encoding(abs_path)
        if loaded is None:
            continue
        text, _enc = loaded
        matches = list(pattern.finditer(text))
        if partial:
            # 这个文件里可能已经不再写目标名了，旧记录也要清掉。
            session.execute(
                delete(AssetRef).where(
                    AssetRef.file_path == rel_path,
                    AssetRef.asset_id.in_(target_ids),
                )
            )
        for match in matches:
            name = match.group(0)
            asset_id = match_map.get(name)
            if asset_id is None:
                continue
            line, col, segment = _line_of(text, match.start())
            session.add(
                AssetRef(
                    id=ulid(),
                    asset_id=asset_id,
                    file_path=rel_path,
                    line=line,
                    col=col,
                    kind=_kind_for(rel_path, segment, role_map),
                    segment=segment,
                    created_at=now_iso(),
                )
            )
            inserted += 1
    session.flush()
    return inserted
