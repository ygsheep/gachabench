# -*- coding: utf-8 -*-
"""抽卡 API：GET/PATCH cards、bulk、to-final（复制生成/NN.mp4，原件保留）。"""
from __future__ import annotations

import asyncio
import logging
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

log = logging.getLogger("gachabench.cards")

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

    # 实时读盘：列卡之前先按节流看一眼 草稿/ 与 生成/。
    # 只 stat 文件指纹，没新片就一个字节都不写库；间隔由 config.watch 控制，
    # 所以「来回切页 / 重复刷新」不会变成反复扫盘。失败只是这一轮不新鲜，不该 500。
    if owner == cfg.active and cfg.watch.enabled:
        try:
            import cardwatch

            await asyncio.to_thread(
                cardwatch.ensure_fresh,
                cfg,
                owner,
                db,
                Path(cfg.ws_root()),
                min_gap=float(cfg.watch.request_gap_sec),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[cards] 刷新卡池失败（返回上次的库内容）：%s", e)

    rows = list_cards(
        db,
        {"workspace": owner, "status": status, "seg": seg, "tag": tag, "q": q},
    )
    _annotate_missing(cfg, owner, rows)
    return {"cards": rows, "reject_tags": REJECT_TAGS}


def _annotate_missing(cfg, owner: str, rows: list[dict]) -> None:
    """标出「文件已不在盘上」的卡（改名 / 移动 / 删掉）。

    复用卡池刷新刚算好的指纹，不额外 stat 一遍；拿不到指纹（还没扫过）就一律不标，
    宁可晚一轮也不给用户看假警报。
    """
    try:
        import cardwatch

        ws = next((w for w in cfg.workspaces if w.name == owner), None)
        if ws is None:
            return
        snap = cardwatch.current_snap(Path(ws.root))
        cardwatch.mark_missing(rows, snap, cardwatch.scan_folder_names(cfg, ws))
    except Exception as e:  # noqa: BLE001 —— 标记失败不该让列卡 500
        log.warning("[cards] 缺失标记失败：%s", e)


@router.get("/{card_id}")
async def cards_detail(card_id: int):
    cfg, db = _active_db()
    card = get_card(db, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="卡片不存在")
    _annotate_missing(cfg, card["workspace"], [card])
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