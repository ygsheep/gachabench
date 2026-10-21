# -*- coding: utf-8 -*-
"""定位 + 工单 API：GET /api/segments/{seg}/locate、POST /api/ticket。SPEC §7.5/7.6。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import get_config
from prompt_locator import locate_segment
from ticket import build_ticket

router = APIRouter(prefix="/api", tags=["locate"])


def _active():
    cfg = get_config()
    if cfg.ws_root() is None:
        raise HTTPException(status_code=400, detail="未激活任何工作区")
    return cfg


@router.get("/segments/{seg}/locate")
async def segment_locate(seg: str, tags: str):
    """镜号定位：tags=SH402,#13-#14 → 命中镜块（行号区间 + 原文）。"""
    cfg = _active()
    try:
        result = locate_segment(cfg, seg, tags)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


class TicketBody(BaseModel):
    seg: str
    shots: list[int] = []          # 已解析的镜号列表
    tags: str = ""                 # 原始标签（可选，服务端重新解析）
    comment: str = ""


@router.post("/ticket")
async def make_ticket(body: TicketBody):
    cfg = _active()
    if body.tags:
        tags = body.tags
    else:
        tags = ",".join(f"SH{int(body.seg)}{s:02d}" for s in body.shots)
    try:
        result = locate_segment(cfg, body.seg, tags)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result["hits"]:
        raise HTTPException(status_code=400, detail=f"标签未命中任何镜块：{tags}")
    return {"ticket": build_ticket(cfg, result, body.comment)}
