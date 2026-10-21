# -*- coding: utf-8 -*-
"""FFmpeg 收尾 API：POST /api/ffmpeg/last-frame（抽末帧）、POST /api/ffmpeg/concat（拼接成片 + 注册新卡）。"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import get_config
from db import get_db
import ffmpeg_tools
from repo import card_file_path, get_card, upsert_card_by_path, update_card

router = APIRouter(prefix="/api/ffmpeg", tags=["ffmpeg"])


def _active():
    cfg = get_config()
    root = cfg.ws_root()
    if root is None:
        raise HTTPException(status_code=400, detail="未激活任何工作区")
    return cfg, root


class LastFrameBody(BaseModel):
    card_id: int


@router.post("/last-frame")
async def last_frame(body: LastFrameBody):
    cfg, root = _active()
    db = get_db(root)
    card = get_card(db, body.card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="卡片不存在")
    src = card_file_path(cfg, card)
    if src is None or not src.exists():
        raise HTTPException(status_code=404, detail=f"成片不存在：{card['rel_path']}")
    draft = cfg.subdir("draft")
    draft.mkdir(parents=True, exist_ok=True)
    out = draft / f"_lastframe_{body.card_id}.png"
    result = await ffmpeg_tools.extract_last_frame(cfg, src, out)
    if result is None:
        raise HTTPException(status_code=500, detail="末帧抽取失败（ffmpeg 不可用或文件损坏）")
    return {"card_id": body.card_id, "frame": str(out.relative_to(root)), "abs": str(out)}


class ConcatBody(BaseModel):
    card_ids: list[int]


@router.post("/concat")
async def concat(body: ConcatBody):
    cfg, root = _active()
    db = get_db(root)
    if len(body.card_ids) < 2:
        raise HTTPException(status_code=400, detail="至少需要 2 张卡拼接")
    sources: list[Path] = []
    cards: list[dict] = []
    for cid in body.card_ids:
        card = get_card(db, cid)
        if card is None:
            raise HTTPException(status_code=404, detail=f"卡片不存在：#{cid}")
        fp = card_file_path(cfg, card)
        if fp is None or not fp.exists():
            raise HTTPException(status_code=404, detail=f"成片不存在：{card['rel_path']}")
        sources.append(fp)
        cards.append(card)

    # 目标文件名：取第一张卡的段号/块号派生，落入草稿/
    seg = cards[0].get("seg")
    draft = cfg.subdir("draft")
    draft.mkdir(parents=True, exist_ok=True)
    if seg:
        base = f"{seg}_第{int(seg)}段_concat"
    else:
        base = "concat"
    out = draft / f"{base}.mp4"
    # 避免覆盖既有拼接产物
    k = 1
    while out.exists():
        out = draft / f"{base}_{k}.mp4"
        k += 1

    result = await ffmpeg_tools.concat_videos(cfg, sources, out)
    if result is None:
        raise HTTPException(status_code=500, detail="拼接失败（ffmpeg 不可用或视频格式不兼容）")

    # 注册新卡 + 标记标签继承
    rel = str(out.relative_to(root))
    card_id, _ = upsert_card_by_path(db, cfg.active, rel, cfg)
    merged_tags: list[str] = []
    for c in cards:
        for t in c.get("tags") or []:
            if t not in merged_tags:
                merged_tags.append(t)
    chunk_merged = "+".join(str(c.get("chunk") or "?") for c in cards)
    update_card(db, card_id, {"seg": seg, "chunk": chunk_merged, "tags": merged_tags})
    return {"card_id": card_id, "rel_path": rel, "chunks": [c.get("chunk") for c in cards]}
