# -*- coding: utf-8 -*-
"""备料区 API：`{素材}/{段}/` —— 当前片段要用的素材。

- `GET  /api/prep/{seg}`  只读计划：要什么、哪些已就位、哪些能补、哪些补不了
- `POST /api/prep/{seg}`  执行补料（默认不覆盖已在备料区的文件）

跑片本身不依赖这个接口（`_materialize` 会优先读备料区），这里只是让人能
**看见**这一段用了哪几张图，并且能提前备好 / 手动换掉。
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import get_config
import prep

router = APIRouter(prefix="/api/prep", tags=["prep"])


def _cfg():
    cfg = get_config()
    if cfg.ws_root() is None:
        raise HTTPException(status_code=400, detail="未激活任何工作区")
    return cfg


@router.get("/{seg}")
async def prep_plan(seg: str):
    cfg = _cfg()
    got = await asyncio.to_thread(prep.plan_prep, cfg, seg)
    if got["seg"] is None:
        raise HTTPException(status_code=400, detail="段号不可用")
    return got


class ApplyBody(BaseModel):
    overwrite: bool = False
    only: Optional[list[str]] = None


@router.post("/{seg}")
async def prep_apply(seg: str, body: ApplyBody | None = None):
    cfg = _cfg()
    body = body or ApplyBody()
    stats = await asyncio.to_thread(
        prep.apply_prep, cfg, seg, overwrite=body.overwrite, only=body.only
    )
    if stats["dir"] is None:
        raise HTTPException(status_code=400, detail="工作区未配置「素材」目录，无法备料")
    return stats
