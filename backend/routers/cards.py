# -*- coding: utf-8 -*-
"""抽卡 API：GET/PATCH cards、bulk、to-final（复制生成/NN.mp4，原件保留）。"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from config import get_config
from db import get_db
from repo import (
    bulk_update_status,
    card_file_path,
    get_card,
    list_cards,
    update_card,
)

router = APIRouter(prefix="/api/cards", tags=["cards"])


REJECT_TAGS = [
    "人物崩", "口型不准", "串词", "道具复读", "运镜糊", "画风漂",
    "时长不符", "手部穿模", "闪黑异常", "其他",
]


def _active_db():
    cfg = get_config()
    root = cfg.ws_root()
    if root is None:
        raise HTTPException(status_code=400, detail="未激活任何工作区")
    return cfg, get_db(root)


@router.get("")
async def cards_list(
    status: str = Query("all"),
    seg: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    workspace: Optional[str] = Query(None),
):
    cfg, db = _active_db()
    owner = workspace or cfg.active
    rows = list_cards(
        db,
        {"workspace": owner, "status": status, "seg": seg, "tag": tag, "q": q},
    )
    return {"cards": rows, "reject_tags": REJECT_TAGS}


@router.get("/{card_id}")
async def cards_detail(card_id: int):
    cfg, db = _active_db()
    card = get_card(db, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="卡片不存在")
    return card


class PatchBody(BaseModel):
    status: Optional[str] = None
    reject_tags: Optional[list[str]] = None
    reject_note: Optional[str] = None
    note: Optional[str] = None
    seg: Optional[str] = None
    tags: Optional[list[str]] = None
    chunk: Optional[str] = None
    prompt_md: Optional[str] = None


@router.patch("/{card_id}")
async def cards_patch(card_id: int, body: PatchBody):
    cfg, db = _active_db()
    card = update_card(db, card_id, body.model_dump(exclude_none=True))
    if card is None:
        raise HTTPException(status_code=404, detail="卡片不存在")
    return card


class BulkBody(BaseModel):
    ids: list[int]
    status: str


@router.post("/bulk")
async def cards_bulk(body: BulkBody):
    cfg, db = _active_db()
    n = bulk_update_status(db, body.ids, body.status)
    return {"updated": n}


class ToFinalBody(BaseModel):
    copy_to_final: bool = False


@router.post("/{card_id}/to-final")
async def cards_to_final(card_id: int, body: ToFinalBody):
    cfg, db = _active_db()
    card = get_card(db, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="卡片不存在")

    src = card_file_path(cfg, card)
    if src is None or not src.exists():
        raise HTTPException(status_code=404, detail=f"卡片视频不存在：{card['rel_path']}")

    update_card(db, card_id, {"status": "final"})

    if body.copy_to_final:
        final_dir = cfg.subdir("final")
        if final_dir is None:
            raise HTTPException(status_code=400, detail="未激活工作区")
        final_dir.mkdir(parents=True, exist_ok=True)
        seg = card.get("seg")
        dst_name = f"{seg}.mp4" if seg else f"card_{card_id}.mp4"
        dst = final_dir / dst_name
        # 若源已在最终位置（同名同路径）则跳过复制，避免自身覆盖冲突
        if dst.resolve() == src.resolve():
            pass
        elif dst.exists():
            raise HTTPException(
                status_code=409,
                detail=f"目标已存在且非源文件，拒绝覆盖：{dst}（如需替换请先手动删除或指定其它段号）",
            )
        else:
            shutil.copy2(src, dst)
        update_card(db, card_id, {"chunk": "final"})
        return {"card": get_card(db, card_id), "copied_to": str(dst)}

    return {"card": get_card(db, card_id), "copied_to": None}