# -*- coding: utf-8 -*-
"""提示词工程 HTTP 接口（P3 第一刀）：段列表 → 分镜/素材 → 三段式渲染 → 落盘。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from config import get_config
from modules.prompts import service

router = APIRouter(prefix="/api/prompts", tags=["prompts"])


def _cfg():
    cfg = get_config()
    if cfg.ws_root() is None:
        raise HTTPException(status_code=400, detail="未激活任何工作区")
    return cfg


@router.get("/segments")
async def segments() -> dict[str, Any]:
    cfg = _cfg()
    return {"segments": service.list_segments(cfg)}


@router.get("/context")
async def context() -> dict[str, Any]:
    """项目里已经定下来的风格 / 场景文字，用于新段继承。"""
    cfg = _cfg()
    return service.inherit_context(cfg)


@router.get("/segments/{seg}")
async def segment_detail(seg: str) -> dict[str, Any]:
    cfg = _cfg()
    try:
        data = service.load_segment(cfg, seg)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return data


class RenderBody(BaseModel):
    style: Optional[str] = None
    scene: Optional[str] = None
    era_region: Optional[str] = None
    aspect: Optional[str] = None
    chunk_index: int = Field(default=1, ge=1, le=9)


@router.post("/segments/{seg}/render")
async def render(seg: str, body: RenderBody | None = None) -> dict[str, Any]:
    cfg = _cfg()
    options = (body or RenderBody()).model_dump(exclude_none=True)
    try:
        return service.render_segment(cfg, seg, options)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class SaveBody(BaseModel):
    content: str
    filename: str = ""
    overwrite: bool = False


@router.post("/segments/{seg}/save")
async def save(seg: str, body: SaveBody) -> dict[str, Any]:
    cfg = _cfg()
    try:
        return service.save_prompt(cfg, seg, body.content, body.filename, body.overwrite)
    except FileExistsError as e:
        raise HTTPException(
            status_code=409,
            detail={"code": "FILE_EXISTS", "message": f"{e} 已存在，确认覆盖后再存"},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/files")
async def files(kind: str = Query("image"), q: str = Query("")) -> dict[str, Any]:
    """人工指定素材时的候选清单。只列工作区里真实存在的图 / 音频。"""
    cfg = _cfg()
    return {"files": service.list_files(cfg, kind=kind, q=q)}


class BindingBody(BaseModel):
    placeholder: str
    file: str = ""
    ignored: bool = False
    name: str = ""


@router.put("/segments/{seg}/bindings")
async def put_binding(seg: str, body: BindingBody) -> dict[str, Any]:
    """人工指定 / 忽略 / 恢复自动。

    - 给 `file`：手动绑定；
    - 给 `ignored=true`：忽略这一项，编号保留；
    - 两个都不给：删记录，回到自动匹配。
    """
    cfg = _cfg()
    try:
        return service.set_binding(
            cfg, seg, body.placeholder, body.file, body.ignored, body.name
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class CheckBody(BaseModel):
    content: Optional[str] = None


@router.post("/segments/{seg}/check")
async def check(seg: str, body: CheckBody | None = None) -> dict[str, Any]:
    """规则诊断。不传 content 就诊断当前渲染结果；传了就诊断手里这份草稿。"""
    cfg = _cfg()
    try:
        return service.check_segment(cfg, seg, (body or CheckBody()).content)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class PatchBody(BaseModel):
    content: str
    codes: list[str] = Field(default_factory=list)


@router.post("/segments/{seg}/patch")
async def make_patch(seg: str, body: PatchBody) -> dict[str, Any]:
    """把「可机械修」的结论攒成补丁：返回 hunks、改完的全文、改完还剩的问题。"""
    cfg = _cfg()
    try:
        return service.patch_segment(cfg, seg, body.content, body.codes)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
