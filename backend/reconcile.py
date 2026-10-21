# -*- coding: utf-8 -*-
"""跑片补收：ComfyUI 已经跑完、本地却没入库的产物，按任务记录回捞进「草稿/」。

为什么需要它：落库原先只挂在进程内的轮询协程上（routers/generate._run_job）。
uvicorn reload、进程崩溃、轮询超时都会把那个协程带走 —— ComfyUI 那边的成品就永远
躺在它自己的 output 目录里，而卡池是纯读库的，于是「跑完了，草稿里却找不到」。

两条腿走路，缺一不可：
1. 按 prompt_id 回查 ComfyUI /history —— 历史还在时最准（知道这次到底出了哪些文件）；
2. 按产物命名前缀扫 ComfyUI output 目录 —— ComfyUI 重启会清空内存里的 history，
   只有这一条还能把成片捞回来（它纯读文件系统，ComfyUI 没开也照样能救）。

触发点：后端启动时补一次、之后每 RECONCILE_INTERVAL 秒补一次、
手动「重新扫描」时补一次（routers/workspace.scan_ws）。

幂等靠 job.log_tail 里的 [补收] 标记：补过的任务不再重复复制产物。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import comfy
from ingest import ingest_files, summarize
from repo import latest_split_plan, list_jobs, update_job

# 只补这两种状态：还在排队/运行的由 _run_job 自己收，别去抢它的活
PICK_STATUS = ("stopped", "error")
# 一次最多看多少条任务（按 id 倒序取最新）
SCAN_LIMIT = 60
# 单轮补收总预算（秒）—— 启动时不能把服务卡在这上面
ROUND_BUDGET = 25.0
# 周期补收的间隔（秒）
RECONCILE_INTERVAL = 60.0
# 幂等标记
MARK = "[补收]"


def _status_text(entry: dict) -> str:
    st = entry.get("status") or {}
    return str(st.get("status_str") or ("completed" if st.get("completed") else "未知"))


def _mark(db, job: dict, note: str, stage: Optional[str] = None) -> None:
    """往 job 日志追一行（[补收] 标记同时是幂等键），可选改 stage。"""
    prev = (job.get("log_tail") or "").strip()
    tail = f"{prev}\n{note}".strip() if prev else note
    fields: dict = {"log_tail": tail[-4000:]}
    if stage:
        fields["stage"] = stage
    try:
        update_job(db, job["id"], fields)
    except Exception:  # noqa: BLE001 —— 写日志失败不该影响已经完成的补收
        pass


def _tag_prefix(job: dict) -> Optional[str]:
    """这一块的产物名前缀，与 comfy.build 的 tag 同一套命名。

    tag = 「草稿/{seg}_第{int(seg)}段_{chunk}」，SaveVideo 落盘时取后半段当文件名前缀，
    所以 `17_第17段_1of3*.mp4` 就是这一块的产物。
    """
    seg = str(job.get("seg") or "").strip()
    chunk = str(job.get("chunk") or "").strip()
    if not seg or not chunk:
        return None
    try:
        return f"{seg}_第{int(seg)}段_{chunk}"
    except (TypeError, ValueError):
        return None


def _chunk_meta(db, workspace: str, job: dict) -> dict:
    """从最近一次拆分计划倒推 tags（SH1701 这类）与 plan_id。

    tags 只是检索词，还原不出来就留空 —— 不值得为它放弃一次补收。
    """
    meta: dict = {"tags": None, "plan_id": None}
    seg = str(job.get("seg") or "").strip()
    if not seg:
        return meta
    try:
        plan = latest_split_plan(db, workspace, seg)
    except Exception:
        return meta
    if not plan:
        return meta
    meta["plan_id"] = plan["id"]
    try:
        manifest = json.loads(plan["manifest_json"] or "{}")
        for c in manifest.get("chunks") or []:
            if str(c.get("chunk")) == str(job.get("chunk")):
                meta["tags"] = [f"SH{int(seg)}{s:02d}" for s in (c.get("shots") or [])]
                break
    except Exception:
        meta["tags"] = None
    return meta


async def reconcile_jobs(
    cfg,
    workspace: str,
    db,
    root: Path,
    *,
    limit: int = SCAN_LIMIT,
    budget: float = ROUND_BUDGET,
) -> dict:
    """补收一个工作区的产物，返回统计（永远不抛异常）。"""
    stats: dict = {
        "workspace": workspace,
        "checked": 0,
        "ingested": 0,
        "files": 0,
        "cards": 0,
        "waiting": 0,
        "no_output": 0,
        "offline": 0,
        "skipped": 0,
        "problems": [],
    }
    try:
        jobs = list_jobs(db, workspace, limit)
    except Exception as e:  # noqa: BLE001
        stats["problems"].append(f"读任务列表失败：{e}")
        return stats

    pending = [
        j for j in jobs
        if j.get("status") in PICK_STATUS
        and str(j.get("comfy_prompt_id") or "").strip()
        and MARK not in (j.get("log_tail") or "")
    ]
    stats["skipped"] = sum(
        1 for j in jobs
        if j.get("status") in PICK_STATUS and MARK in (j.get("log_tail") or "")
    )
    if not pending:
        return stats

    online = await comfy.alive(cfg)
    draft = cfg.subdir("draft")
    mp4_cache: Optional[list[Path]] = None

    def all_mp4() -> list[Path]:
        """ComfyUI output 下的全部 mp4（一轮只列一次）。"""
        nonlocal mp4_cache
        if mp4_cache is None:
            try:
                mp4_cache = [p for p in Path(cfg.comfyui.comfy_output_dir).rglob("*.mp4")]
            except OSError:
                mp4_cache = []
        return mp4_cache

    def by_prefix(prefix: str) -> list[Path]:
        """按命名前缀找回这一块的成片；草稿里已同名的跳过（收过了）。"""
        hits: list[Path] = []
        for p in all_mp4():
            if not p.name.startswith(prefix):
                continue
            if draft is not None and (draft / p.name).exists():
                continue
            hits.append(p)
        audio = [p for p in hits if "-audio" in p.stem]
        picked = audio or hits
        if len(picked) > 1:
            picked = sorted(picked, key=lambda q: q.stat().st_mtime, reverse=True)
        return picked

    loop = asyncio.get_event_loop()
    t0 = loop.time()
    for job in pending:
        if loop.time() - t0 > budget:
            stats["problems"].append(f"超过 {budget:.0f}s 预算，本轮提前收工")
            break

        stats["checked"] += 1
        files: list[Path] = []
        note = ""

        if online:
            pid = str(job.get("comfy_prompt_id") or "").strip()
            entry = await comfy.history(cfg, pid)
            if entry is None:
                if await comfy.is_queued(cfg, pid):
                    stats["waiting"] += 1
                    continue
                note = "（ComfyUI 历史里已没有这条记录）"
            elif not comfy.succeeded(entry):
                note = f"（ComfyUI 侧不是成功状态：{_status_text(entry)}）"
            else:
                files = comfy.collect(cfg, entry, "")
        else:
            note = "（ComfyUI 不可达）"

        # 历史这条路走不通时，按命名前缀直接在 output 目录里找（纯文件系统，不需要 ComfyUI）
        if not files:
            prefix = _tag_prefix(job)
            if prefix and draft is not None:
                files = by_prefix(prefix)
                if files:
                    note = "（按产物命名前缀从 ComfyUI output 目录捞回）"

        if not files:
            if online:
                # 在线却什么都没有：这一条没救了，标记掉，别每轮重扫一遍 output 目录
                _mark(db, job, f"{MARK} 没找到可入库的产物{note}")
            stats["offline" if not online else "no_output"] += 1
            continue

        meta = _chunk_meta(db, workspace, job)
        try:
            res = ingest_files(
                cfg,
                db,
                workspace,
                files,
                seg=job.get("seg"),
                chunk=job.get("chunk"),
                tags=meta.get("tags"),
                prompt_md=job.get("prompt_file"),
                split_plan_id=meta.get("plan_id"),
            )
        except Exception as e:  # noqa: BLE001
            stats["problems"].append(f"任务 #{job['id']} 补收失败：{str(e)[:200]}")
            continue

        if res["registered"]:
            stats["ingested"] += 1
            stats["files"] += len(res["saved"])
            stats["cards"] += len(res["registered"])
            _mark(db, job, f"{MARK} 已把 ComfyUI 成品补进草稿{note}：{summarize(res)}", stage="已补收")
        elif res["saved"]:
            # 文件已经复制进草稿，只是登记失败 —— 标记掉，避免每轮重复复制一份
            _mark(db, job, f"{MARK} 产物已复制进草稿，但登记卡片失败：{summarize(res)}")
        else:
            # 一个都没复制成功（例如文件被占用）→ 不标记，下一轮再试
            stats["problems"].append(
                f"任务 #{job['id']} 补收未成功：" + ("；".join(res["problems"]) or "空产物")
            )
        if res["problems"]:
            stats["problems"].extend(f"任务 #{job['id']}：{p}" for p in res["problems"])
    return stats


async def reconcile_all(cfg=None, **kw) -> list[dict]:
    """补收所有已建过索引的工作区。"""
    from config import get_config
    from db import get_db

    cfg = cfg or get_config()
    out: list[dict] = []
    for ws in cfg.workspaces:
        root = Path(ws.root)
        # 从没建过索引的工作区别去动它（get_db 会顺手把目录建出来）
        if not (root / cfg.index_dir / "cards.db").is_file():
            continue
        try:
            out.append(await reconcile_jobs(cfg, ws.name, get_db(root), root, **kw))
        except Exception as e:  # noqa: BLE001
            out.append({"workspace": ws.name, "problems": [str(e)[:200]]})
    return out


def format_stats(stats: list[dict]) -> str:
    """压成一行，给启动日志用。"""
    parts = []
    for s in stats:
        got = int(s.get("ingested") or 0)
        if got or s.get("problems"):
            parts.append(
                f"{s.get('workspace')}: 补收 {got} 条任务 / {int(s.get('files') or 0)} 个文件"
                + ("（" + "；".join(map(str, s["problems"]))[:300] + "）" if s.get("problems") else "")
            )
    return " | ".join(parts)
