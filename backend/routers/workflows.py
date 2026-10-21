# -*- coding: utf-8 -*-
"""工作流 API：GET /api/workflows —— 列出 {prompt} 下的工作流 JSON 及其可调参数元数据。

每个工作流的参数（模型/LoRA/采样器/步数/画质/画幅/注意力后端…）由 comfy.describe_workflow
按节点自动探测生成，select 类参数的候选项优先从 ComfyUI /object_info 拉取，失败回退当前值。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from config import get_config
import comfy

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


def _active(cfg):
    if cfg.ws_root() is None:
        raise HTTPException(status_code=400, detail="未激活任何工作区")


def _fill_static_options(params: list[dict]) -> None:
    """ComfyUI 不可达时的降级：select 用内置候选表填，至少能选到常见值。

    总比把参数条卡在「读工作流…」强 —— 工作流 JSON 里本来就有当前值，
    枚举只是让人能换。
    """
    for p in params:
        if p.get("type") != "select" or p.get("options"):
            continue
        key = f"{p.get('class_type')}.{p.get('field')}"
        opts = list(comfy.FALLBACK_OPTIONS.get(key) or [])
        cur = p.get("value")
        # 当前值必须占一个选项位，否则 <select> 会显示成空白 —— 看起来像「没设置」
        if cur is not None and cur != "":
            opts = list(dict.fromkeys([cur, *opts]))
        if opts:
            p["options"] = opts


@router.get("")
async def list_workflows():
    cfg = get_config()
    _active(cfg)
    files = comfy.list_workflow_files(cfg)
    described: list[dict] = []
    for f in files:
        try:
            meta = comfy.describe_workflow(cfg, f["path"])
        except Exception as e:  # 单个坏 JSON 不影响整体列表
            described.append({"name": f["name"], "rel": f["rel"], "params": [], "nodes": {},
                              "error": f"解析失败：{e}"})
            continue
        described.append(meta)
    # 并发补齐枚举候选。
    # ⚠️ ComfyUI 没起时必须快速放弃：以前这里是无条件 gather，每个 select 都去
    # /object_info 各等 30s，整个接口能拖到十几秒 —— 前端参数条一直「读工作流…」。
    # 先花 1.5s 探一下，不可达就用内置候选表填，功能照常可用。
    if await comfy.alive(cfg, timeout=1.0):
        await asyncio.gather(*[comfy.enrich_params(cfg, m["params"]) for m in described])
    else:
        for m in described:
            _fill_static_options(m["params"])

    default = Path(cfg.resolve_placeholder(cfg.workflow.json_path)).name
    return {"workflows": described, "default": default}


@router.get("/{name}")
async def workflow_detail(name: str):
    cfg = get_config()
    _active(cfg)
    for f in comfy.list_workflow_files(cfg):
        if f["name"] == name or f["rel"].endswith(name):
            meta = comfy.describe_workflow(cfg, f["path"])
            if await comfy.alive(cfg, timeout=1.0):
                await comfy.enrich_params(cfg, meta["params"])
            else:
                _fill_static_options(meta["params"])
            return meta
    raise HTTPException(status_code=404, detail=f"工作流不存在：{name}")
