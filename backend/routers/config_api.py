# -*- coding: utf-8 -*-
"""配置 API：GET /api/config（读当前配置）、PUT /api/config（改全局配置）。

设计要点（对齐 docs/INTEGRATION-novel-booksource-engine.md §4.4）：

1. **两层配置要分清**。GB 的配置有全局层（`config.json` 的 comfyui/ffmpeg/folders/workflow）
   和每工作区层（`WorkspaceEntry.folders` 的目录名覆盖）。本路由**只动全局层**；
   工作区层走 `PATCH /api/workspaces/{name}/folders`。设置页面必须把两者分开展示，
   否则一次手滑就写出跨工作区污染。

2. **PUT 是合并，不是替换**。前端只提交自己改的那一节的字段，未提交的保持原值。
   直接 `Config.model_validate(部分字段)` 会把没提交的节全部重置成默认值，
   所以这里先在当前配置 dict 上做「只覆盖白名单内的已知键」。

3. **越界值钳制，不报错**。poll_interval / timeout 这类滑杆值前端可能传来奇怪的东西，
   后端夹回合法区间（沿用 NBE 的设置页约定：参数随请求带给后端、越界回落默认）。

4. **`workflow.nodes` 不开放写**。节点号映射改错会直接产出废片（而画面照样出得来），
   只允许切换 `workflow.json_path`（走 /api/workflows 列表选），节点映射保持只读。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import FOLDER_KEYS, get_config, set_config

router = APIRouter(prefix="/api/config", tags=["config"])

# 单源超时与轮询间隔的合法区间（秒 / 分钟）
_POLL_RANGE = (1, 60)
_TIMEOUT_RANGE = (1, 1440)


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _need_text(value: Any, field: str) -> str:
    if value is None:
        raise HTTPException(status_code=400, detail=f"{field} 不能为空")
    s = str(value).strip()
    if not s:
        raise HTTPException(status_code=400, detail=f"{field} 不能为空")
    return s


def _need_url(value: Any, field: str) -> str:
    s = _need_text(value, field)
    if not (s.startswith("http://") or s.startswith("https://")):
        raise HTTPException(status_code=400, detail=f"{field} 要以 http:// 或 https:// 开头")
    return s.rstrip("/")


def _need_folder_name(value: Any, field: str) -> str:
    """目录名必须是「单个文件夹名」—— 带路径分隔符会把子目录拼到别处去。"""
    s = _need_text(value, field)
    if any(c in s for c in "/\\") or s in (".", ".."):
        raise HTTPException(status_code=400, detail=f"{field} 只能是单个文件夹名，不能带路径")
    return s


class ConfigPatch(BaseModel):
    """只接受这几个节的局部字段；未提交的键原样保留。"""
    comfyui: Optional[dict] = None
    ffmpeg: Optional[dict] = None
    folders: Optional[dict] = None
    workflow: Optional[dict] = None
    search: Optional[dict] = None
    watch: Optional[dict] = None


@router.get("")
async def read_config():
    """读当前配置（含 workspaces / active，前端设置页要用）。"""
    return get_config().model_dump()


@router.put("")
async def write_config(patch: ConfigPatch):
    cfg = get_config()
    merged: dict = cfg.model_dump()

    # ---- comfyui ----
    if patch.comfyui is not None:
        cur = dict(merged.get("comfyui") or {})
        for key in ("base_url", "comfy_output_dir", "input_dir"):
            if key in patch.comfyui:
                cur[key] = (
                    _need_url(patch.comfyui[key], "ComfyUI 地址")
                    if key == "base_url"
                    else _need_text(patch.comfyui[key], key)
                )
        if "poll_interval_sec" in patch.comfyui:
            cur["poll_interval_sec"] = _clamp_int(
                patch.comfyui["poll_interval_sec"], *_POLL_RANGE, default=5
            )
        if "timeout_min" in patch.comfyui:
            cur["timeout_min"] = _clamp_int(
                patch.comfyui["timeout_min"], *_TIMEOUT_RANGE, default=60
            )
        merged["comfyui"] = cur

    # ---- ffmpeg ----
    if patch.ffmpeg is not None:
        cur = dict(merged.get("ffmpeg") or {})
        for key in ("binary", "probe_binary"):
            if key in patch.ffmpeg:
                cur[key] = _need_text(patch.ffmpeg[key], "ffmpeg 可执行文件" if key == "binary" else "ffprobe 可执行文件")
        merged["ffmpeg"] = cur

    # ---- folders（全局默认目录名）----
    if patch.folders is not None:
        cur = dict(merged.get("folders") or {})
        for key in FOLDER_KEYS:
            if key in patch.folders:
                cur[key] = _need_folder_name(patch.folders[key], f"目录名 {key}")
        merged["folders"] = cur

    # ---- workflow：只放开 json_path ----
    if patch.workflow is not None:
        cur = dict(merged.get("workflow") or {})
        if "json_path" in patch.workflow:
            cur["json_path"] = _need_text(patch.workflow["json_path"], "工作流文件")
        # nodes / ref_image_size 一律不接：改错会产出废片且画面看不出来
        merged["workflow"] = cur

    # ---- search（联网采集开关）----
    # 只放开「开关 + 免责确认 + 书源路径 + 两个数值」。
    # 🔴 enabled 只能在这里开，且必须由前端在确认免责声明后才提交 —— 后端不替用户点头。
    if patch.search is not None:
        cur = dict(merged.get("search") or {})
        for key in ("enabled", "disclaimer_acked", "attribution"):
            if key in patch.search:
                cur[key] = bool(patch.search[key])
        if "sources_path" in patch.search:
            cur["sources_path"] = str(patch.search["sources_path"] or "").strip()
        if "timeout_sec" in patch.search:
            cur["timeout_sec"] = _clamp_int(patch.search["timeout_sec"], 3, 120, default=15)
        if "max_bytes" in patch.search:
            cur["max_bytes"] = _clamp_int(
                patch.search["max_bytes"], 256 * 1024, 32 * 1024 * 1024, default=4 * 1024 * 1024
            )
        merged["search"] = cur

    # ---- watch（卡池自动刷新：间隔秒数，越界夹回）----
    # 这是「实时读盘」的节流阀：轮询只 stat 盘，但间隔太短也没意义（人手放不了那么快），
    # 太长又会让人以为「还是看不到新片」。2~120 秒是能用的区间。
    if patch.watch is not None:
        cur = dict(merged.get("watch") or {})
        if "enabled" in patch.watch:
            cur["enabled"] = bool(patch.watch["enabled"])
        if "interval_sec" in patch.watch:
            cur["interval_sec"] = _clamp_float(patch.watch["interval_sec"], 2.0, 120.0, default=8.0)
        if "request_gap_sec" in patch.watch:
            cur["request_gap_sec"] = _clamp_float(
                patch.watch["request_gap_sec"], 0.0, 60.0, default=1.5
            )
        merged["watch"] = cur

    # ---- 校验 + 落盘 ----
    from config import Config

    try:
        new_cfg = Config.model_validate(merged)
    except Exception as e:  # noqa: BLE001 —— 给用户看得懂的消息，而不是 pydantic 堆栈
        raise HTTPException(status_code=400, detail=f"配置校验失败：{e}") from e

    set_config(new_cfg)
    return new_cfg.model_dump()
