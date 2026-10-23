# -*- coding: utf-8 -*-
"""跑片 API：POST /api/split/{plan_id}/generate 提交本地跑片；GET /api/jobs 轮询；
GET /api/split/{plan_id}/preview 跑片前预览。SPEC §6/§7.3。

流程：建 job → 后台 asyncio 任务 → 解析块 txt 的 <Picture N> → （可选）末帧继承 →
comfy.build/submit/wait/collect → 复制 -audio.mp4 到草稿/ → 注册卡片 → 更新 job。
可调参数（工作流/模型/LoRA/采样器/步数/画质/画幅/时长/种子）经 body 透传给 comfy.build。
进度经 ComfyUI WebSocket 实时写回 job.progress。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from config import get_config
from db import get_db
import comfy
import ffmpeg_tools
import ingest
import splitter
from repo import (
    create_job,
    delete_job,
    get_card,
    get_job,
    get_split_plan,
    latest_card_for,
    list_assets,
    list_jobs,
    update_job,
)

router = APIRouter(prefix="/api/split", tags=["generate"])
jobs_router = APIRouter(prefix="/api/jobs", tags=["generate"])

PICTURE_RE = re.compile(
    r"<Picture (\d+)>\s*is\s+([^\s,]+\.(?:png|jpe?g|webp))",
    re.IGNORECASE,
)
AUDIO_TAG_RE = re.compile(r"<Audio (\d+)>", re.IGNORECASE)
# 源 md 音频表：| Audio 1 | `何聪_配音.mp3` | 或 | `Audio 1` | `何聪_配音.mp3` |
AUDIO_MAP_RE = re.compile(r"\|\s*`?Audio\s*(\d+)`?\s*\|\s*`([^`]+)`", re.IGNORECASE)


# 在跑的后台任务：job_id → asyncio.Task。进程级（单进程部署），用于「停止」时真的掐掉。
_TASKS: dict[int, asyncio.Task] = {}

# 视为「还在动」的状态
LIVE_STATUS = ("queued", "running")
# 终态
DONE_STATUS = ("done", "error", "stopped")


def _active():
    cfg = get_config()
    root = cfg.ws_root()
    if root is None:
        raise HTTPException(status_code=400, detail="未激活任何工作区")
    return cfg, root


def parse_pictures(text: str) -> list[str]:
    """按 <Picture N> 编号升序返回参考图文件名列表（顺序 == ref_image_N）。"""
    matches = PICTURE_RE.findall(text)
    return [m[1] for m in sorted(matches, key=lambda x: int(x[0]))]


def parse_picture_items(text: str) -> list[dict]:
    """返回 [{n, filename}]，按编号升序。"""
    matches = PICTURE_RE.findall(text)
    return [{"n": int(n), "filename": f}
            for n, f in sorted(matches, key=lambda x: int(x[0]))]


def parse_audio_indices(text: str) -> list[int]:
    """块 txt 中出现的 <Audio N> 编号（去重升序）。"""
    return sorted({int(m) for m in AUDIO_TAG_RE.findall(text)})


def parse_audio_map(md_text: str) -> dict[int, str]:
    """从源 md 的音频表解析 Audio N → 文件名（首次出现为准）。"""
    out: dict[int, str] = {}
    for n, fn in AUDIO_MAP_RE.findall(md_text):
        out.setdefault(int(n), fn)
    return out


def _audio_refs(root: Path, plan: dict, text: str) -> list[str]:
    """块 txt 的 <Audio N> → 参考音频文件名列表，位置 i == 节点 ref_audio_i。

    编号必须从 1 起连续、不跳号（段 md §② 的既有规范，也是节点 AUTOGROW 的要求）。
    这不是洁癖：参考音频是按**位置**接线的，跳号会把音色接给错的人，而画面照样出得来
    —— 光看片子根本发现不了。缺文件名同理，宁可跑不了也不能接错。
    """
    idxs = parse_audio_indices(text)
    if not idxs:
        return []
    if idxs != list(range(1, len(idxs) + 1)):
        raise ValueError(
            "块 txt 里的 <Audio N> 必须从 1 起连续、不跳号，实际为 "
            + "、".join(str(i) for i in idxs)
            + " —— 参考音频节点按位置接线，跳号会接错音色。"
        )
    amap = _audio_map_for(root, plan)
    lack = [n for n in idxs if not amap.get(n)]
    if lack:
        raise ValueError(
            "源 md 的音频表里缺 "
            + "、".join("Audio %d" % n for n in lack)
            + " 对应的文件名 —— 在 §🎵 音频参考 里补一行「| `Audio N` | `文件名` |」再跑。"
        )
    return [amap[n] for n in idxs]


def _append_carry_picture(text: str, n: int, fname: str) -> str:
    """在 subject_definitions 段尾补一句 <Picture N> 接续声明（SPEC §7.2）。"""
    line = (
        f"<Picture {n}> is {fname}, the exact last frame of the preceding segment, "
        "captured as a still image; this shot continues seamlessly from the previous "
        "segment's final frame."
    )
    marker = "\nsummary:"
    if marker in text:
        return text.replace(marker, f"\n{line}\n" + marker, 1)
    return text.rstrip("\n") + "\n\n" + line + "\n"


def _chunk_tag(seg: str, chunk: str) -> str:
    return f"草稿/{seg}_第{int(seg)}段_{chunk}"


# ---------------------------------------------------------------------------
# POST /api/split/{plan_id}/generate
# ---------------------------------------------------------------------------

class GenerateBody(BaseModel):
    chunk: str  # "1of3"
    carry_last_frame: bool = False
    prev_card_id: Optional[int] = None
    audio_files: Optional[list[str]] = None  # 存在于 ComfyUI input 目录的文件名，≤3
    picture_files: Optional[dict[str, str]] = None  # {Picture 编号: 覆盖文件名}，临时替换默认参考图
    megapixels: float = 0.5
    # ---- 可调参数（全部可选；缺省 = 保持工作流 JSON 原值）----
    workflow: Optional[str] = None      # {prompt} 下的工作流文件名
    model: Optional[str] = None         # UNET 模型
    lora: Optional[str] = None          # LoRA 名
    lora_strength: Optional[float] = None
    sampler: Optional[str] = None
    multiple: Optional[int] = None      # ResolutionSelector 对齐倍数（常用 8/16/32/64）
    sigmas: Optional[str] = None        # ManualSigmas 工作流的 sigma 序列字符串
    aspect_ratio: Optional[str] = None
    attention: Optional[str] = None
    duration: Optional[float] = None    # 覆盖块的 target_sec
    seed: Optional[int] = None


def _resolve_workflow(cfg, name: Optional[str]) -> Optional[str]:
    """把前端传来的工作流名解析为 {prompt} 目录内的绝对路径；None → 用配置默认。"""
    if not name:
        return None
    prompt_dir = cfg.subdir("prompt")
    p = Path(name)
    if not p.is_absolute():
        if prompt_dir is None:
            raise HTTPException(status_code=400, detail="未激活工作区，无法解析工作流")
        p = prompt_dir / name
    p = p.resolve()
    if prompt_dir is None or p.parent != prompt_dir.resolve() or not p.is_file():
        raise HTTPException(status_code=400, detail=f"工作流不存在或越界：{name}")
    return str(p)


def _check_params(body: GenerateBody) -> None:
    """入参范围预校验 —— 非法直接 400，避免把错误留到后台任务里。"""
    checks = [
        ("画质 megapixels", body.megapixels, 0.1, 1.0),
        ("时长 duration", body.duration, 0.5, 60.0),
        ("LoRA 强度", body.lora_strength, 0.0, 2.0),
        ("倍数 multiple", body.multiple, 8, 128),
        ("种子 seed", body.seed, 0, comfy.SEED_MAX),
    ]
    for label, v, lo, hi in checks:
        if v is None:
            continue
        if not (lo <= float(v) <= hi):
            raise HTTPException(status_code=400, detail=f"{label} 必须在 {lo}–{hi} 之间，收到 {v}")


@router.post("/{plan_id}/generate")
async def split_generate(plan_id: int, body: GenerateBody):
    cfg, root = _active()
    db = get_db(root)
    plan = get_split_plan(db, plan_id)
    if plan is None or plan["workspace"] != cfg.active:
        raise HTTPException(status_code=404, detail="拆分计划不存在")
    # 块正文按**当前源 md** 现算：改完提示词立刻生效，不再有「跑的是旧 txt」这回事
    plan, chunk_info, text = _load_chunk(cfg, root, db, plan_id, body.chunk)

    _check_params(body)
    wf_path = _resolve_workflow(cfg, body.workflow)

    # 产物 / 输入目录未配置时直接拦下：空值会退化成当前工作目录（Path("") == "."），
    # 参考图、参考音频、成片回捞全部落到错误位置，排查成本极高。这里让用户先去设置页选好。
    if not cfg.comfyui.comfy_output_dir:
        raise HTTPException(
            status_code=400,
            detail="尚未设置 ComfyUI 产物目录（跑完的片落哪里）—— 请到「设置」里选择",
        )
    if not cfg.comfyui.input_dir:
        raise HTTPException(
            status_code=400,
            detail="尚未设置 ComfyUI 输入目录（参考图/音频的汇合点）—— 请到「设置」里选择",
        )

    # 音频校验：必须已在 ComfyUI input 目录
    audios = body.audio_files or []
    if len(audios) > 3:
        raise HTTPException(status_code=400, detail="参考音频最多 3 条")
    for a in audios:
        if not (Path(cfg.comfyui.input_dir) / a).exists():
            raise HTTPException(
                status_code=400,
                detail=f"音频文件不在 ComfyUI input 目录：{a}",
            )

    # 参考图覆盖校验：编号必须真实存在于本块，文件必须在 ComfyUI input 目录
    pics_override = body.picture_files or {}
    if len(pics_override) > 8:
        raise HTTPException(status_code=400, detail="参考图覆盖数量过多（≤8）")
    if pics_override:
        block_nos = {str(i["n"]) for i in parse_picture_items(text)}
        for n, fn in pics_override.items():
            if n not in block_nos:
                raise HTTPException(status_code=400, detail=f"Picture {n} 不在本块参考图清单中，无法覆盖")
            if not (Path(cfg.comfyui.input_dir) / fn).exists():
                raise HTTPException(
                    status_code=400,
                    detail=f"覆盖图不在 ComfyUI input 目录：{fn}",
                )

    # 末帧继承预检：上一块必须有成片
    seg = plan["seg"]
    total = body.chunk.split("of")[1]
    idx = int(body.chunk.split("of")[0])
    if body.carry_last_frame:
        if idx <= 1:
            raise HTTPException(status_code=400, detail="首块无需末帧继承")
        prev_chunk = f"{idx-1}of{total}"
        card = None
        if body.prev_card_id:
            card = get_card(db, body.prev_card_id)
        if card is None or card.get("chunk") != prev_chunk:
            card = latest_card_for(db, cfg.active, seg, prev_chunk)
        if card is None or not (root / card["rel_path"]).exists():
            raise HTTPException(
                status_code=400,
                detail=f"未找到上一块 {prev_chunk} 的成片，无法继承末帧（请先跑上一块）",
            )

    # 落库要用的「这一版提示词」标识：源 md + 块名。快照正文另存，见下面的 create_job。
    prompt_ref = f"{plan['source_md']}#{body.chunk}"
    job_id = create_job(
        db, cfg.active, seg, body.chunk, prompt_ref,
        prompt_snapshot=text,
        params=body.model_dump(exclude_none=True),
    )
    update_job(db, job_id, {"stage": "排队中", "progress": 0})
    task = asyncio.create_task(
        _run_job(job_id, cfg.active, plan, chunk_info, text, body, wf_path)
    )
    _TASKS[job_id] = task
    task.add_done_callback(lambda _t, jid=job_id: _TASKS.pop(jid, None))
    return {"job_id": job_id, "status": "queued"}


async def _run_job(job_id, workspace, plan, chunk_info, text, body, wf_path=None):
    cfg = get_config()
    root = cfg.ws_root()
    db = get_db(root)
    seg = plan["seg"]

    def progress(msg: str, pct: Optional[int] = None):
        patch: dict = {"log_tail": msg}
        if pct is not None:
            patch["progress"] = int(pct)
        update_job(db, job_id, patch)

    try:
        update_job(db, job_id, {"status": "running", "stage": "准备中"})
        progress(f"块 {body.chunk}：正文按当前源 md 现算（{len(text)} 字）", 0)
        refs = parse_pictures(text)
        if not refs:
            raise RuntimeError("块 txt 中未解析到 <Picture N> is 文件名（参考图列表为空）")

        # 参考图覆盖：拆分台临时从资产复制 / 手动上传的图，替换默认解析结果
        if body.picture_files:
            items = parse_picture_items(text)
            by_n = {str(i["n"]): i["filename"] for i in items}
            for n, fn in body.picture_files.items():
                by_n[n] = fn
            refs = [by_n[str(i["n"])] for i in items]
            progress(f"参考图覆盖 {len(body.picture_files)} 个槽位", 0)

        # 参考默认从资产库补齐：提示词里写死了文件名，跑片前先把这些名字在 input 目录里落地，
        # 用户不必手动点「从资产选」。这就是「缺图」文案里那些按钮要做的事，只是改成自动做。
        input_dir = Path(cfg.comfyui.input_dir)
        filled = [
            fn for fn in refs
            if not (input_dir / fn).is_file() and _materialize(cfg, db, fn, "image").get("copied")
        ]
        if filled:
            progress(f"从资产库补齐 {len(filled)} 张参考图：{'、'.join(filled)}", 0)

        # 参考音频：块 txt 里的 <Audio N> → 节点 ref_audio_{N-1}，文件名取自源 md
        # 的音频表（解析与编号校验收口在 _audio_refs，见那里的说明）。
        audio_list: list[str] = _audio_refs(root, plan, text)

        if audio_list:
            # 音频和参考图同一套落位规则：按提示词里期望的文件名从资产库补齐
            filled_a = []
            for fn in audio_list:
                if (input_dir / fn).is_file():
                    continue
                if _materialize(cfg, db, fn, "audio").get("copied"):
                    filled_a.append(fn)
            if filled_a:
                progress(f"从资产库补齐 {len(filled_a)} 个参考音频：{'、'.join(filled_a)}", 0)

            missing_a = [fn for fn in audio_list if not (input_dir / fn).is_file()]
            if missing_a:
                raise RuntimeError(
                    "参考音频既不在 ComfyUI input 目录、资产库里也找不到："
                    + "、".join(missing_a)
                    + " —— 到这一块的参考条里换一段或直接上传再跑。"
                )

        # 显式覆盖（参考条上手动换过的槽位优先）
        if body.audio_files:
            audio_list = [fn for fn in body.audio_files][:3]
            miss_ov = [fn for fn in audio_list if fn and not (input_dir / fn).is_file()]
            if miss_ov:
                raise RuntimeError(
                    "覆盖的参考音频不在 ComfyUI input 目录：" + "、".join(miss_ov)
                )
            progress(f"参考音频覆盖 {len(audio_list)} 条", 0)

        still_missing = [fn for fn in refs if not (input_dir / fn).is_file()]
        if still_missing:
            raise RuntimeError(
                "参考图既不在 ComfyUI input 目录、资产库里也找不到："
                + "、".join(still_missing)
                + " —— 到这一块的参考条里换一张或直接上传再跑。"
            )

        # 末帧继承：抽上一块末帧 PNG → ComfyUI input 目录，作为额外参考图 + 补声明
        if body.carry_last_frame:
            idx = int(body.chunk.split("of")[0])
            prev_chunk = f"{idx-1}of{body.chunk.split('of')[1]}"
            card = None
            if body.prev_card_id:
                card = get_card(db, body.prev_card_id)
            if card is None or card.get("chunk") != prev_chunk:
                card = latest_card_for(db, workspace, seg, prev_chunk)
            if card is None:
                raise RuntimeError(f"未找到上一块 {prev_chunk} 的成片")
            src = root / card["rel_path"]
            carry_png = Path(cfg.comfyui.input_dir) / f"_carry_{seg}_{body.chunk}.png"
            if await ffmpeg_tools.extract_last_frame(cfg, src, carry_png) is None:
                raise RuntimeError(f"末帧抽取失败：{src.name}")
            refs.append(carry_png.name)
            text = _append_carry_picture(text, len(refs), carry_png.name)
            progress(f"末帧继承：{carry_png.name} → ref_image_{len(refs)-1}", 0)

        tag = _chunk_tag(seg, body.chunk)
        secs = float(body.duration if body.duration is not None else chunk_info["target_sec"])
        progress(f"构造工作流：{len(refs)} 图参考 · {secs}s · {body.megapixels}MP", 1)
        wf = comfy.build(
            cfg,
            text,
            refs,
            seconds=float(chunk_info["target_sec"]),
            mp=body.megapixels,
            tag=tag,
            audios=audio_list,
            seed=body.seed,
            workflow_path=wf_path,
            model=body.model,
            lora=body.lora,
            lora_strength=body.lora_strength,
            sampler=body.sampler,
            sigmas=body.sigmas,
            multiple=body.multiple,
            aspect_ratio=body.aspect_ratio,
            attention=body.attention,
            duration=body.duration,
        )
        progress("提交 ComfyUI…", 2)
        pid = await comfy.submit(cfg, wf)
        update_job(db, job_id, {"comfy_prompt_id": pid, "stage": "已提交", "progress": 3})
        progress(f"已提交 prompt_id={pid}，开始轮询…", 3)

        entry = await comfy.wait(cfg, pid, on_progress=progress)
        files = comfy.collect(cfg, entry, tag)
        if not files:
            raise RuntimeError("未找到产物文件（comfy_output_dir 下无 mp4）")

        # ---- 落库：先复制到草稿/，再逐个登记卡片 ----
        # 收口在 ingest.ingest_files（正常跑完与启动补收共用同一条路径）。
        # 任何一步失败都只记日志、不能中断整轮登记 —— 卡池是纯读库的，
        # 草稿里有文件却没有卡片，就等于「跑完了但找不到」。
        tags = [f"SH{int(seg)}{s:02d}" for s in chunk_info["shots"]]
        res = ingest.ingest_files(
            cfg,
            db,
            workspace,
            files,
            seg=seg,
            chunk=body.chunk,
            tags=tags,
            prompt_md=f"{plan['source_md']}#{body.chunk}",
            split_plan_id=plan["id"],
            prompt_snapshot=text,
            params=body.model_dump(exclude_none=True),
        )
        if not res["registered"]:
            reason = "；".join(res["problems"]) or "产物中没有可登记的 mp4"
            raise RuntimeError(f"产物未登记为草稿卡片：{reason}")
        if res["problems"] or not res["audio_ids"]:
            progress("落库提醒：" + ingest.summarize(res), 100)
        progress(
            f"完成：{len(res['saved'])} 个产物入草稿，"
            f"登记卡片 #{'、'.join(map(str, res['registered']))}",
            100,
        )
        update_job(db, job_id, {"status": "done", "stage": "完成", "progress": 100})
    except Exception as e:
        progress(f"失败：{str(e)[:2000]}")
        update_job(db, job_id, {"status": "error", "stage": "失败"})


# ---------------------------------------------------------------------------
# GET /api/jobs、GET /api/jobs/{id}
# ---------------------------------------------------------------------------

@jobs_router.get("")
async def jobs_list(workspace: Optional[str] = None, limit: int = 50):
    cfg, root = _active()
    db = get_db(root)
    owner = workspace or cfg.active
    return {"jobs": list_jobs(db, owner, limit)}


@jobs_router.get("/{job_id}")
async def job_detail(job_id: int):
    cfg, root = _active()
    db = get_db(root)
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@jobs_router.post("/{job_id}/stop")
async def job_stop(job_id: int):
    """停止一个还在跑的任务：先打断 ComfyUI，再掐掉后台协程，最后落成 stopped。

    只打断这个任务自己的 prompt_id —— 不无差别 /interrupt，别人在跑的片不受影响。
    """
    cfg, root = _active()
    db = get_db(root)
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job["status"] not in LIVE_STATUS:
        raise HTTPException(status_code=400, detail=f"任务已经是「{job['status']}」，没有可停的")

    note = await comfy.cancel(cfg, job.get("comfy_prompt_id"))
    task = _TASKS.pop(job_id, None)
    if task is not None and not task.done():
        task.cancel()

    # 取消是异步生效的，写回前再确认一次，别把刚好跑完的任务覆盖成「已停止」
    latest = get_job(db, job_id)
    if latest is not None and latest["status"] in LIVE_STATUS:
        tail = (job.get("log_tail") or "").rstrip()
        update_job(db, job_id, {
            "status": "stopped",
            "stage": "已停止",
            "log_tail": (tail + "\n[停止] " + note).strip(),
        })
        return {"job_id": job_id, "status": "stopped", "detail": note}
    return {"job_id": job_id, "status": (latest or job)["status"], "detail": "任务刚好自己结束了"}


@jobs_router.delete("/{job_id}")
async def job_delete(job_id: int):
    """删掉一条任务记录。还在跑的先停掉；**只动库，不动任何产物文件**。"""
    cfg, root = _active()
    db = get_db(root)
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    stopped = False
    if job["status"] in LIVE_STATUS:
        await comfy.cancel(cfg, job.get("comfy_prompt_id"))
        task = _TASKS.pop(job_id, None)
        if task is not None and not task.done():
            task.cancel()
        stopped = True
    delete_job(db, job_id)
    return {"ok": True, "deleted": job_id, "stopped_first": stopped}


# ---------------------------------------------------------------------------
# GET /api/split/{plan_id}/preview —— 跑片前预览（参考图 / 音频 / 资产匹配）
# POST /api/split/{plan_id}/refs/from-asset —— 从资产库复制参考图到 ComfyUI input
# POST /api/split/{plan_id}/refs/upload —— 手动上传临时参考图
# 后两者都不写资产库：仅供本次跑片临时测试，文件落在 ComfyUI input 目录
# ---------------------------------------------------------------------------

def _comfy_input_url(filename: Optional[str]) -> Optional[str]:
    return f"/api/comfy-input/{quote(filename)}" if filename else None


def _load_chunk(cfg, root, db, plan_id: int, chunk: str):
    """按 plan_id + chunk 取块信息与正文 —— 正文按**当前源 md** 现算，不读任何落盘 txt。

    这是「改完提示词立刻生效」的关键：块不再是一份会过期的副本，而是每次现切的视图。
    镜结构变了导致块名对不上时给 409，让前端刷新，绝不悄悄跑一个别的块。
    """
    plan = get_split_plan(db, plan_id)
    if plan is None or plan["workspace"] != cfg.active:
        raise HTTPException(status_code=404, detail="拆分计划不存在")
    try:
        composed = splitter.compose_segment(cfg, plan["seg"], plan["strategy"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    chunk_info = splitter.find_chunk(composed, chunk)
    if chunk_info is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"当前提示词已切成 {len(composed['chunks'])} 块，没有块 {chunk}。"
                "镜结构变了，刷新拆分台再跑。"
            ),
        )
    return plan, chunk_info, chunk_info["text"]


REF_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
REF_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def _assets_file_url(rel_path: str) -> str:
    return f"/api/assets/file/{quote(rel_path)}"


def _asset_match(db, workspace: str, filename: str, kind: str = "image") -> dict:
    """在资产库里找一个参考文件：先精确文件名，再按去扩展名 stem 宽松匹配。

    返回的 url 指向**资产库原文件** —— 即使它还没被送进 ComfyUI input 目录，
    前端也能直接预览到，不用等补齐。
    """
    if not filename:
        return {"found": False, "name": None, "rel": None, "fuzzy": False, "url": None}
    assets = [a for a in list_assets(db, workspace) if a.get("kind") == kind]
    want = Path(filename).name
    for a in assets:
        if a["name"] == want:
            return {"found": True, "name": a["name"], "rel": a["rel_path"],
                    "fuzzy": False, "url": _assets_file_url(a["rel_path"])}
    stem = Path(want).stem
    for a in assets:
        if Path(a["name"]).stem == stem:
            return {"found": True, "name": a["name"], "rel": a["rel_path"],
                    "fuzzy": True, "url": _assets_file_url(a["rel_path"])}
    return {"found": False, "name": None, "rel": None, "fuzzy": False, "url": None}


def _materialize(cfg, db, expected: str, kind: str, source_rel: Optional[str] = None) -> dict:
    """把资产库里的一个文件复制成 input_dir/<expected>，让提示词里的引用能解析到。

    这是「参考默认从资产库补齐」的唯一落地点：提示词里写死了文件名，
    所以必须**按那个文件名**落到 ComfyUI input 目录 —— 宽松命中时也一样，
    等于把最像的那张顶上去（前端会标明是"近似匹配"）。
    """
    input_dir = Path(cfg.comfyui.input_dir)
    asset_dir = cfg.subdir("asset")
    if asset_dir is None:
        return {"filename": expected, "copied": False, "reason": "当前工作区没有资产目录"}
    asset_dir = asset_dir.resolve()

    source_name = None
    if source_rel:
        src = (asset_dir / source_rel).resolve()
        if not src.is_relative_to(asset_dir) or not src.is_file():
            return {"filename": expected, "copied": False, "reason": f"资产文件不存在：{source_rel}"}
        hit = next((a for a in list_assets(db, cfg.active) if a["rel_path"] == source_rel), None)
        source_name = hit["name"] if hit else src.name
    else:
        m = _asset_match(db, cfg.active, expected, kind)
        if not m["found"]:
            return {"filename": expected, "copied": False, "reason": "资产库里没有可用的匹配"}
        src = (asset_dir / m["rel"]).resolve()
        source_name = m["name"]

    dst = input_dir / expected
    try:
        if src == dst.resolve():
            return {"filename": expected, "copied": False, "reason": "源就是目标，跳过"}
        input_dir.mkdir(parents=True, exist_ok=True)
        overwrote = dst.exists()
        shutil.copy2(src, dst)
    except OSError as e:
        return {"filename": expected, "copied": False, "reason": f"复制失败：{e}"}
    return {
        "filename": expected,
        "copied": True,
        "overwrote": overwrote,
        "source": source_name,
        "fuzzy": bool(source_rel is None and source_name != expected),
    }


def _audio_map_for(root: Path, plan: dict) -> dict[int, str]:
    """从源 md 的音频表解析 Audio N → 文件名。"""
    src_md = root / plan["source_md"]
    if not src_md.exists():
        return {}
    return parse_audio_map(src_md.read_text(encoding="utf-8"))


def _dedup_path(directory: Path, filename: str) -> Path:
    """同名已存在时加 _1/_2 后缀，返回不冲突的目标路径。"""
    p = directory / filename
    if not p.exists():
        return p
    stem, ext = os.path.splitext(filename)
    i = 1
    while (directory / f"{stem}_{i}{ext}").exists():
        i += 1
    return directory / f"{stem}_{i}{ext}"


@router.get("/{plan_id}/preview")
async def split_preview(plan_id: int, chunk: str):
    cfg, root = _active()
    db = get_db(root)
    plan, chunk_info, text = _load_chunk(cfg, root, db, plan_id, chunk)
    input_dir = Path(cfg.comfyui.input_dir)
    pictures = []
    for item in parse_picture_items(text):
        fn = item["filename"]
        in_input = (input_dir / fn).is_file()
        asset = _asset_match(db, cfg.active, fn, "image")
        pictures.append({
            **item,
            "kind": "image",
            "exists": in_input,
            # 还没送进 input 也要能预览 —— 退回资产库原文件的地址
            "url": _comfy_input_url(fn) if in_input else asset["url"],
            "asset": asset,
            "state": "ready" if in_input else ("auto" if asset["found"] else "missing"),
        })

    amap = _audio_map_for(root, plan)
    aidx = parse_audio_indices(text)
    audios = []
    for n in aidx:
        fn = amap.get(n)
        in_input = bool(fn) and (input_dir / fn).is_file()
        asset = _asset_match(db, cfg.active, fn or "", "audio")
        audios.append({
            "n": n,
            # 会接到哪个节点槽位：ref_audio_{n-1}
            "slot": n - 1,
            "filename": fn,
            "kind": "audio",
            "exists": in_input,
            "url": _comfy_input_url(fn) if in_input else asset["url"],
            "asset": asset,
            "state": "ready" if in_input else ("auto" if asset["found"] else "missing"),
        })
    # 编号必须从 1 起连续 —— 节点按位置接线，跳号会把音色接给错的人。这里提前告诉前端，
    # 别等跑片才失败。
    audio_contiguous = aidx == list(range(1, len(aidx) + 1))

    return {
        "plan_id": plan_id,
        "seg": plan["seg"],
        "chunk": chunk,
        "file": f"{plan['source_md']}#{chunk}",
        "strategy": plan["strategy"],
        "shots": chunk_info["shots"],
        "src_dur": chunk_info["src_dur"],
        "target_sec": chunk_info["target_sec"],
        "prompt_text": text,
        "pictures": pictures,
        "audios": audios,
        "audio_contiguous": audio_contiguous,
        "input_dir": str(input_dir),
    }


class RefItem(BaseModel):
    n: int
    filename: str


class RefFromAssetBody(BaseModel):
    chunk: str
    items: list[RefItem]


@router.post("/{plan_id}/refs/from-asset")
async def refs_from_asset(plan_id: int, body: RefFromAssetBody):
    cfg, root = _active()
    db = get_db(root)
    plan, chunk_info, text = _load_chunk(cfg, root, db, plan_id, body.chunk)
    block_nos = {i["n"] for i in parse_picture_items(text)}
    input_dir = Path(cfg.comfyui.input_dir)
    by_name = {a["name"]: a for a in list_assets(db, cfg.active)}
    out = []
    for item in body.items:
        if item.n not in block_nos:
            raise HTTPException(status_code=400, detail=f"Picture {item.n} 不在本块参考图清单中")
        a = by_name.get(item.filename)
        if a is None:
            raise HTTPException(status_code=404, detail=f"资产库中找不到 {item.filename}")
        src = (cfg.subdir("asset") or root) / a["rel_path"]
        if not src.is_file():
            raise HTTPException(status_code=404, detail=f"资产文件已不存在：{a['rel_path']}")
        existing = input_dir / item.filename
        if existing.is_file():
            # 同名已在 input 目录 → 直接复用，不重复复制
            out.append({
                "n": item.n,
                "filename": existing.name,
                "exists": True,
                "copied": False,
                "source": a["rel_path"],
            })
            continue
        dst = _dedup_path(input_dir, item.filename)
        shutil.copy2(src, dst)
        out.append({
            "n": item.n,
            "filename": dst.name,
            "exists": dst.is_file(),
            "copied": True,
            "source": a["rel_path"],
        })
    return {"items": out}


class RefSelectItem(BaseModel):
    n: int
    kind: str = "image"  # image | audio
    source_rel: Optional[str] = None  # 空 = 按提示词里的文件名自动匹配


class RefSelectBody(BaseModel):
    chunk: str
    items: list[RefSelectItem]


@router.post("/{plan_id}/refs/select")
async def refs_select(plan_id: int, body: RefSelectBody):
    """把资产库里的文件送进 ComfyUI input 目录，按提示词里期望的文件名落地。

    - `source_rel` 为空 = 按期望文件名自动匹配（「补齐」）
    - `source_rel` 指定 = 用选中的那个文件顶掉这一项（「换一张 / 换一段」）

    只动 ComfyUI input 目录，不写资产库 —— 跑片时后端本来就会自动做这件事，
    这个接口是留给用户手动换图 / 提前补齐的。
    """
    cfg, root = _active()
    db = get_db(root)
    plan, chunk_info, text = _load_chunk(cfg, root, db, plan_id, body.chunk)
    pic_by_n = {i["n"]: i["filename"] for i in parse_picture_items(text)}
    amap = _audio_map_for(root, plan)

    out = []
    for item in body.items:
        expected = amap.get(item.n) if item.kind == "audio" else pic_by_n.get(item.n)
        if not expected:
            raise HTTPException(status_code=400, detail=f"本块里没有 {item.kind} #{item.n}")
        out.append({"n": item.n, "kind": item.kind, **_materialize(cfg, db, expected, item.kind, item.source_rel)})
    return {"items": out}


@router.post("/{plan_id}/refs/upload")
async def refs_upload(
    plan_id: int,
    chunk: str = Form(...),
    n: int = Form(...),
    kind: str = Form("image"),
    file: UploadFile = File(...),
):
    """手动上传一个参考文件，**按提示词里期望的文件名**落到 ComfyUI input 目录。

    和 refs/select 汇合到同一条规则上：input 目录里必须是提示词写的那个名字，
    所以这里也按 expected 命名（同名覆盖）—— 不再另起名字去要求跑片带覆盖参数。
    """
    cfg, root = _active()
    db = get_db(root)
    plan, chunk_info, text = _load_chunk(cfg, root, db, plan_id, chunk)
    if kind == "audio":
        expected = _audio_map_for(root, plan).get(n)
        allowed = REF_AUDIO_EXTS
    else:
        expected = {i["n"]: i["filename"] for i in parse_picture_items(text)}.get(n)
        allowed = REF_IMAGE_EXTS
    if not expected:
        raise HTTPException(status_code=400, detail=f"本块里没有 {kind} #{n}")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"仅支持 {'/'.join(sorted(allowed))}，收到 {ext or '无后缀'}",
        )

    input_dir = Path(cfg.comfyui.input_dir)
    input_dir.mkdir(parents=True, exist_ok=True)
    dst = input_dir / expected
    overwrote = dst.exists()
    try:
        with dst.open("wb") as f:
            while True:
                buf = await file.read(1 << 20)
                if not buf:
                    break
                f.write(buf)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"写入失败：{e}")
    return {
        "items": [{
            "n": n,
            "kind": kind,
            "filename": dst.name,
            "copied": True,
            "overwrote": overwrote,
            "uploaded": True,
            "source": None,
        }]
    }
