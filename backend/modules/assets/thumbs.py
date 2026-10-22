# -*- coding: utf-8 -*-
"""缩略图：320px 宽 WebP，落在 `.gachabench/thumbs/<sha 前两位>/<sha>.webp`。

按需生成：首扫不为 1000 个文件预生成，避免拖慢索引；列表打开时再补。
P1 只做图片；视频用前端类型占位图。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

from PIL import Image

THUMB_WIDTH = 320
THUMB_QUALITY = 82


def thumb_key(sha256: Optional[str], rel_path: str) -> str:
    if sha256:
        return sha256
    return hashlib.sha256(rel_path.encode("utf-8")).hexdigest()


def thumb_path(ws_root: Path, key: str) -> Path:
    return Path(ws_root).resolve() / ".gachabench" / "thumbs" / key[:2] / f"{key}.webp"


def ensure_thumb(ws_root: Path, src: Path, sha256: Optional[str], rel_path: str) -> Optional[Path]:
    """命中缓存直接返回；否则生成。生成失败返回 None。"""
    key = thumb_key(sha256, rel_path)
    dest = thumb_path(ws_root, key)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    if not src.is_file():
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".tmp-{os.getpid()}-{dest.name}")
    try:
        with Image.open(src) as img:
            img.load()
            if img.width > THUMB_WIDTH:
                height = max(1, round(img.height * THUMB_WIDTH / img.width))
                img = img.resize((THUMB_WIDTH, height), Image.LANCZOS)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if "transparency" in img.info else "RGB")
            img.save(tmp, format="WEBP", quality=THUMB_QUALITY, method=4)
        os.replace(tmp, dest)
        return dest
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
