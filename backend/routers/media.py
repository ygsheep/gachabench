# -*- coding: utf-8 -*-
"""媒体 API：GET /api/media/{card_id} 视频流（Starlette FileResponse 天然支持 Range）；
GET /api/comfy-input/{filename} 供拆分台预览参考图/音频（严格防路径穿越）。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from config import get_config
from db import get_db
from repo import card_file_path, get_card

router = APIRouter(prefix="/api/media", tags=["media"])

input_router = APIRouter(prefix="/api/comfy-input", tags=["media"])

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


@router.get("/{card_id}")
async def media_stream(card_id: int):
    cfg = get_config()
    root = cfg.ws_root()
    if root is None:
        raise HTTPException(status_code=400, detail="未激活任何工作区")
    db = get_db(root)
    card = get_card(db, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="卡片不存在")
    path = card_file_path(cfg, card)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="卡片视频不存在")
    # 通过绝对路径 + 相对工作区根的 rel_path 拼接，避免路径穿越（rel_path 已入库）
    return FileResponse(path, media_type="video/mp4", filename=path.name, content_disposition_type="inline")


@input_router.get("/{filename}")
async def comfy_input(filename: str):
    """服务 ComfyUI input 目录下的参考图/音频（只允许纯文件名，不允许任何目录分隔）。"""
    if "/" in filename or "\\" in filename or filename in (".", "..") or ".." in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    cfg = get_config()
    base = Path(cfg.comfyui.input_dir).resolve()
    path = (base / filename).resolve()
    if base != path.parent or not path.is_file():  # 严格限制在 input 目录单层内
        raise HTTPException(status_code=404, detail="文件不存在")
    ext = path.suffix.lower()
    media = None
    if ext in _IMAGE_EXT:
        media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".webp": "image/webp", ".bmp": "image/bmp", ".gif": "image/gif"}[ext]
    elif ext in _AUDIO_EXT:
        media = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
                 ".aac": "audio/aac", ".flac": "audio/flac", ".ogg": "audio/ogg"}[ext]
    return FileResponse(path, media_type=media, filename=path.name, content_disposition_type="inline")
