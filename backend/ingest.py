# -*- coding: utf-8 -*-
"""产物落库：把 ComfyUI 输出的 mp4 复制进工作区「草稿/」并登记成卡片。

落库只有这一条路径：正常跑完（routers/generate._run_job）与启动/周期补收
（main._reconcile_jobs）都走这里。任何地方另写一套，就会出现
「ComfyUI 跑完了，草稿里却找不到」—— 这正是这个模块存在的理由。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable, Optional

from repo import update_card, upsert_card_by_path


def rel_path(root: Path, p: Path) -> str:
    """cards.rel_path 一律正斜杠（Windows 下 str(Path) 是反斜杠，会和扫描结果对不上）。"""
    try:
        r = p.relative_to(root)
    except ValueError:
        r = p
    return str(r).replace("\\", "/")


def same_file(a: Path, b: Path) -> bool:
    """两个路径是否指向同一个文件（极端配置下 src 与 dst 可能重合，别自我覆盖）。"""
    try:
        return a.samefile(b)
    except OSError:
        return False


def dedup_path(dst: Path) -> Path:
    """目标已存在时找一个不冲突的名字：name.mp4 → name_2.mp4 → name_3.mp4 …"""
    stem, suffix, parent = dst.stem, dst.suffix, dst.parent
    for i in range(2, 1000):
        cand = parent / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
    return parent / f"{stem}_{os.getpid()}{suffix}"


def ingest_files(
    cfg,
    db,
    workspace: str,
    files: Iterable[Path],
    *,
    seg: Optional[str] = None,
    chunk: Optional[str] = None,
    tags: Optional[list] = None,
    prompt_md: Optional[str] = None,
    split_plan_id: Optional[int] = None,
    prompt_snapshot: Optional[str] = None,
    params: Optional[dict] = None,
) -> dict:
    """把产物复制进「草稿/」并逐个登记卡片，返回落库结果摘要。

    {folder, saved, registered, audio_ids, problems}

    - 单个文件失败只记 problems，不中断其余文件：宁可少一条也不整轮丢掉。
    - 重名不覆盖：同一块重跑时保留上一版，新产物自动加序号后缀。
    - 「没有 -audio 版本」不是失败（SPEC §10.5 的偏好项，不是硬门槛）。

    落库一段都没有成功时由调用方决定怎么报错；这里只把原因写进 problems。
    """
    root = Path(db.root)
    draft = cfg.subdir("draft")
    if draft is None:
        raise RuntimeError("工作区未配置「草稿」目录，无法落库")
    draft.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []
    registered: list[int] = []
    audio_ids: list[int] = []
    problems: list[str] = []

    for p in files:
        dst = draft / p.name
        if dst.exists() and not same_file(p, dst):
            dst = dedup_path(dst)
        try:
            if not same_file(p, dst):
                shutil.copy2(p, dst)
        except OSError as e:
            problems.append(f"{p.name} 复制失败：{e}")
            continue
        saved.append(dst)
        try:
            card_id, _created = upsert_card_by_path(db, workspace, rel_path(root, dst), cfg)
            fields: dict = {"seg": seg, "chunk": chunk}
            if tags:
                fields["tags"] = tags
            if prompt_md:
                fields["prompt_md"] = prompt_md
            if split_plan_id:
                fields["split_plan_id"] = split_plan_id
            # 跑片快照：正文 + 参数。块正文现在每次从源 md 现算，不存这一份的话，
            # 事后永远说不清「这条片子是哪一版提示词跑出来的」。
            if prompt_snapshot:
                fields["prompt_snapshot"] = prompt_snapshot
            if params:
                fields["params_json"] = params
            update_card(db, card_id, fields)
            registered.append(card_id)
            if "-audio" in dst.stem:
                audio_ids.append(card_id)
        except Exception as e:  # noqa: BLE001 —— 一错就整轮丢是原缺陷，这里必须兜住
            problems.append(f"{dst.name} 登记失败：{str(e)[:200]}")

    # 刚落库的产物不必再等后台指纹轮去探一遍：把指纹推到最新，
    # 否则下一轮它会把这几个刚登记过的文件当成「新片」再跑一次 ffprobe。
    try:
        import cardwatch

        ws = next((w for w in cfg.workspaces if w.name == workspace), None)
        if ws is not None and saved:
            cardwatch.note_root(cfg, ws)
    except Exception:  # noqa: BLE001 —— 只是省一次探测，失败无所谓
        pass

    return {
        "folder": str(draft),
        "saved": saved,
        "registered": registered,
        "audio_ids": audio_ids,
        "problems": problems,
    }


def summarize(result: dict) -> str:
    """把落库结果压成一行中文，用来写 job 的 log_tail。"""
    n = len(result["saved"])
    ids = "、".join(f"#{i}" for i in result["registered"])
    parts = [f"{n} 个产物入草稿"]
    if ids:
        parts.append(f"登记卡片 {ids}")
    if not result["audio_ids"]:
        parts.append("没有 `-audio` 版本（按普通草稿入库）")
    if result["problems"]:
        parts.append("；".join(result["problems"]))
    return "，".join(parts)
