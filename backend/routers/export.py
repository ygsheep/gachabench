# -*- coding: utf-8 -*-
"""导出 API：POST /api/export —— 三态契约的统一入口。

契约形状见 `export.py` 模块 docstring。路由只负责「选形态」：

- `dest_dir` 为空 → web 形态，回 `download` 载荷（JSON + base64，前端自己 Blob）
- `dest_dir` 非空 → 桌面形态，写盘后回 `dir` 载荷

目标目录由前端先调 `/api/system/pick-folder` 拿到（复用现成的原生对话框），
所以这里**不弹窗** —— 弹窗是阻塞的，混在写盘逻辑里会让错误处理变复杂。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import get_config
from export import ExportError, build_items, payload_download, resolve_dest, write_to_dir
from exportrange import build_range

router = APIRouter(prefix="/api/export", tags=["export"])


def _active_root() -> Path:
    cfg = get_config()
    root = cfg.ws_root()
    if root is None:
        raise HTTPException(status_code=400, detail="未激活任何工作区")
    return root


class ExportBody(BaseModel):
    items: list = []
    dest_dir: Optional[str] = None
    zip_name: Optional[str] = None


@router.post("")
async def export(body: ExportBody):
    root = _active_root()
    try:
        items = build_items(root, body.items)
        if body.dest_dir and str(body.dest_dir).strip():
            dest = resolve_dest(str(body.dest_dir))
            return write_to_dir(items, dest)
        return payload_download(items, body.zip_name or "")
    except ExportError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e


class RangeBody(BaseModel):
    """范围导出（C4）：后端自己枚举条目，不用前端拼 rel_path。"""

    model_config = {"populate_by_name": True}

    what: str                      # prompts / chapters / cards
    # `from` 是 Python 关键字，字段只能叫 from_；别名让请求体写 `from` 也能收
    from_: int = Field(1, alias="from")
    to: int = 1
    book_id: Optional[int] = None  # what=chapters 必填
    status: str = "final"          # what=cards：final / draft / reject / all
    seg: str = ""                  # what=cards：限定段号，空 = 全部
    dest_dir: Optional[str] = None
    zip_name: Optional[str] = None


@router.post("/range")
async def export_range(body: RangeBody):
    cfg = get_config()
    if cfg.ws_root() is None:
        raise HTTPException(status_code=400, detail="未激活任何工作区")
    try:
        items = build_range(
            cfg,
            body.what,
            from_=body.from_,
            to_=body.to,
            book_id=body.book_id,
            status=body.status,
            seg=body.seg,
        )
        if body.dest_dir and str(body.dest_dir).strip():
            dest = resolve_dest(str(body.dest_dir))
            payload = write_to_dir(items, dest)
        else:
            payload = payload_download(items, body.zip_name or "")
        payload["what"] = body.what
        payload["count"] = len(items)
        return payload
    except ExportError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e
