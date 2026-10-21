# -*- coding: utf-8 -*-
"""拆分 API：段号枚举、读 §④、shots 解析、split（甲/乙）。P3 完整实现。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import get_config
from db import get_db
from splitter import (
    compose_segment,
    ensure_plan,
    find_chunk,
    find_segment_md,
    parse_codeblock_sections,
    parse_shot_blocks,
)

router = APIRouter(prefix="/api/segments", tags=["split"])

router2 = APIRouter(prefix="/api/split", tags=["split"])


def _active():
    cfg = get_config()
    root = cfg.ws_root()
    if root is None:
        raise HTTPException(status_code=400, detail="未激活任何工作区")
    return cfg, root


# ---------------------------------------------------------------------------
# GET /api/segments —— 可用段号（从提示词 md / 卡池推断）
# ---------------------------------------------------------------------------

@router.get("")
async def segments_list():
    cfg, root = _active()
    prompt_dir = cfg.subdir("prompt")
    segs: set[str] = set()
    if prompt_dir and prompt_dir.is_dir():
        for md in sorted(prompt_dir.glob("*_正片_第*段_15s.md")):
            m = re.search(r"第(\d+)段", md.name)
            if m:
                segs.add(m.group(1).zfill(2))
        # 兜底：卡池里跑过、但文件名不规范（没有「_正片_第N段_15s」）的段
        db = get_db(root)
        from repo import list_cards
        for c in list_cards(db, {"workspace": cfg.active}):
            seg = (c["seg"] or "").strip()
            if seg:
                segs.add(seg.zfill(2))

    # 只留「真能切」的段：卡池里的 00 / 004 这类脏段号、只有一两个镜的测试 md，
    # 塞进列表会让拆分台一进页面就对着一个切不了的段报错。
    out: list[str] = []
    for seg in sorted(segs):
        md = find_segment_md(cfg, seg)
        if md is None:
            continue
        try:
            if len(parse_shot_blocks(md.read_text(encoding="utf-8"))) < 3:
                continue
        except OSError:
            continue
        out.append(seg)
    return {"segments": out}


# ---------------------------------------------------------------------------
# GET /api/segments/{seg}/prompt —— 读 md 的 §④ 代码块 + 各分节
# ---------------------------------------------------------------------------

@router.get("/{seg}/prompt")
async def segment_prompt(seg: str):
    cfg, root = _active()
    md = find_segment_md(cfg, seg)
    if md is None:
        raise HTTPException(status_code=404, detail=f"未找到 {seg} 段提示词 md")
    text = md.read_text(encoding="utf-8")
    sections = parse_codeblock_sections(text)
    return {
        "seg": seg,
        "title": text.splitlines()[0] if text.splitlines() else "",
        "file": str(md.relative_to(cfg.ws_root())),
        "sections": sections,
        "codeblock": sections.get("detailed_description", "") or "",
    }


# ---------------------------------------------------------------------------
# GET /api/segments/{seg}/shots —— 解析 [Shot N]
# ---------------------------------------------------------------------------

@router.get("/{seg}/shots")
async def segment_shots(seg: str):
    cfg, root = _active()
    md = find_segment_md(cfg, seg)
    if md is None:
        raise HTTPException(status_code=404, detail=f"未找到 {seg} 段提示词 md")
    text = md.read_text(encoding="utf-8")
    shots = parse_shot_blocks(text)
    return {"seg": seg, "shots": shots}


# ---------------------------------------------------------------------------
# GET /api/segments/{seg}/chunks —— 按当前源 md 实时分块（不落盘）
# GET /api/segments/{seg}/block  —— 单块正文（跑片/审片器用的就是这一份）
# ---------------------------------------------------------------------------

@router.get("/{seg}/chunks")
async def segment_chunks(seg: str, strategy: str = "jia"):
    """实时分块结果。前端块列表以这里为准 —— 前后端共用同一套切组逻辑，不会漂移。"""
    cfg, _root = _active()
    try:
        composed = compose_segment(cfg, seg, strategy)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        **{k: v for k, v in composed.items() if k != "chunks"},
        "chunks": [{k: v for k, v in c.items() if k != "text"} for c in composed["chunks"]],
    }


@router.get("/{seg}/block")
async def segment_block(seg: str, chunk: str, strategy: str = "jia"):
    cfg, _root = _active()
    try:
        composed = compose_segment(cfg, seg, strategy)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    hit = find_chunk(composed, chunk)
    if hit is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"当前提示词已切成 {len(composed['chunks'])} 块，没有块 {chunk}。"
                "镜结构变了，刷新一下拆分台再跑。"
            ),
        )
    return {
        "seg": seg,
        "chunk": chunk,
        "strategy": strategy,
        "source_md": composed["source_md"],
        **{k: v for k, v in hit.items() if k != "chunk"},
    }


# ---------------------------------------------------------------------------
# POST /api/segments/{seg}/split —— 确认分块策略（只记策略，不写 txt）
# ---------------------------------------------------------------------------

class SplitBody(BaseModel):
    strategy: str = "jia"  # jia | yi


@router.post("/{seg}/split")
async def do_split(seg: str, body: SplitBody):
    """确认「这段用这个策略」，返回 plan_id。

    不写任何 txt：块正文由 compose_segment 每次按当前源 md 现算。这个接口只负责
    给跑片一个归属（split_plans 一行）并把当前分块快照记下来。
    """
    cfg, _root = _active()
    try:
        result = ensure_plan(cfg, seg, body.strategy)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result