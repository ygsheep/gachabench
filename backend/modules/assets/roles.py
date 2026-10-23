# -*- coding: utf-8 -*-
"""资产索引的命名口径：角色、类别、主体、扩展名。

角色目录不重新发明：以 `config.folder_names_for()` 为准；资产目录额外接受
历史候选名（素材/资产/assets…），保证换个人命名也能落到 `asset`。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from config import DEFAULT_ASSET_DIR_CANDIDATES

ROLE_LABELS: list[tuple[str, str]] = [
    ("asset", "资产"),
    ("storyboard", "分镜"),
    ("prompt", "提示词"),
    ("final", "成片"),
    ("draft", "草稿"),
    ("source", "原著"),
    ("other", "其他"),
]
ROLE_LABEL = dict(ROLE_LABELS)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".avif"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
TEXT_EXT = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".log", ".py", ".js", ".ts",
    ".tsx", ".jsx", ".html", ".css", ".toml", ".ini", ".xml",
}
ARCHIVE_EXT = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"}

SKIP_DIRS = {".gachabench", ".git", "node_modules", "__pycache__"}

_SPLIT_RE = re.compile(r"[_\s]+")

_CATEGORY = [
    (IMAGE_EXT, "image"),
    (VIDEO_EXT, "video"),
    (AUDIO_EXT, "audio"),
    (TEXT_EXT, "text"),
    (ARCHIVE_EXT, "archive"),
]

_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".avif": "image/avif",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".m4v": "video/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json",
}


def is_skipped_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.startswith(".tmp-")


def asset_aliases(folder_names: dict[str, str]) -> set[str]:
    names = {n.strip().casefold() for n in DEFAULT_ASSET_DIR_CANDIDATES if n.strip()}
    configured = (folder_names.get("asset") or "").strip().casefold()
    if configured:
        names.add(configured)
    return names


def role_for_path(rel_path: str, folder_names: dict[str, str]) -> str:
    """一级目录名匹配 config 角色名 → 角色 key；否则 other。"""
    first = rel_path.split("/", 1)[0] if "/" in rel_path else ""
    if not first:
        return "other"
    key = first.strip().casefold()
    if not key:
        return "other"
    for role, name in folder_names.items():
        if (name or "").strip().casefold() == key:
            return role
    if key in asset_aliases(folder_names):
        return "asset"
    return "other"


def subject_for_name(name: str) -> str:
    stem = Path(name).stem
    parts = [p for p in _SPLIT_RE.split(stem) if p]
    return parts[0] if parts else ""


def category_for_ext(ext: str) -> str:
    ext = (ext or "").lower()
    for exts, category in _CATEGORY:
        if ext in exts:
            return category
    return "other"


def media_kind_for_ext(ext: str) -> str:
    category = category_for_ext(ext)
    if category in ("image", "video", "audio"):
        return category
    # 文本 / 压缩也要如实返回：前端据此区分图标、是否可双击预览、卡片样式。
    # 之前一律落 "other"，导致 md/txt 在资产页显示成「其它」通用文件，且无法预览。
    if category == "text":
        return "text"
    if category == "archive":
        return "archive"
    return "other"


def is_image(ext: str) -> bool:
    return (ext or "").lower() in IMAGE_EXT


def mime_for_ext(ext: str) -> str:
    return _MIME.get((ext or "").lower(), "application/octet-stream")


def label_for_role(role: str) -> str:
    return ROLE_LABEL.get(role, role)


def normalize_collection_filter(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    import json

    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def iter_roles_ordered(keys: Iterable[str]) -> list[str]:
    known = [k for k, _ in ROLE_LABELS]
    extra = sorted({k for k in keys if k not in known})
    return [k for k in known if k in keys] + extra
