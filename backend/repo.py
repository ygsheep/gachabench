# -*- coding: utf-8 -*-
"""仓储：cards / split_plans / jobs 纯 SQL 读写（无 ORM）。每个函数接收 Db 句柄，内部加锁。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import ffmpeg_tools


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def _uj(s) -> list:
    if not s:
        return []
    try:
        return json.loads(s)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# cards
# ---------------------------------------------------------------------------

def seg_from_filename(rel_path: str) -> Optional[str]:
    """从文件名正则解析段号：`^NN_` / `^NN-` / `第N段`。返回 'NN' 或 None。"""
    import re
    base = Path(rel_path).name
    m = re.match(r"^(\d{1,3})[_\-]", base)
    if m:
        return m.group(1).zfill(2) if int(m.group(1)) < 100 else m.group(1)
    m = re.search(r"第\s*(\d{1,3})\s*段", base)
    if m:
        return m.group(1).zfill(2) if int(m.group(1)) < 100 else m.group(1)
    # 段号两位前缀但无分隔符，如 04xxx.mp4：只在纯段号情形匹配
    m = re.match(r"^(\d{2})", base)
    if m:
        return m.group(1)
    return None


def upsert_card_by_path(db, workspace: str, rel_path: str, cfg) -> tuple[int, bool]:
    """按 (workspace, rel_path) 幂等 upsert，探测媒体信息。返回 (id, created)。"""
    with db.lock:
        conn = db.conn
        row = conn.execute(
            "SELECT * FROM cards WHERE workspace=? AND rel_path=?",
            (workspace, rel_path),
        ).fetchone()
        created = row is None
        if row is None:
            cur = conn.execute(
                "INSERT INTO cards (workspace, rel_path, status, created_at, updated_at)"
                " VALUES (?,?, 'draft', ?, ?)",
                (workspace, rel_path, _now(), _now()),
            )
            card_id = cur.lastrowid
        else:
            card_id = row["id"]
            conn.execute("UPDATE cards SET updated_at=? WHERE id=?", (_now(), card_id))

        info = ffmpeg_tools.probe_media(cfg, Path(db.root) / rel_path)
        seg = seg_from_filename(rel_path)
        conn.execute(
            "UPDATE cards SET duration=?, width=?, height=?, has_audio=?,"
            " seg=COALESCE(seg, ?) WHERE id=?",
            (info.get("duration"), info.get("width"), info.get("height"),
             int(bool(info.get("has_audio"))), seg, card_id),
        )
        conn.commit()
        return card_id, created


def _ujo(s):
    """任意 JSON 反序列化（对象/数组都有），坏数据返回 None。"""
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def get_card(db, card_id: int) -> Optional[dict]:
    with db.lock:
        row = db.conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        return _row_to_card(row, with_snapshot=True) if row else None


def _row_to_card(row, *, with_snapshot: bool = False) -> dict:
    out = {
        "id": row["id"],
        "workspace": row["workspace"],
        "rel_path": row["rel_path"],
        "seg": row["seg"],
        "chunk": row["chunk"],
        "status": row["status"],
        "reject_tags": _uj(row["reject_tags"]),
        "reject_note": row["reject_note"],
        "tags": _uj(row["tags"]),
        "note": row["note"],
        "prompt_md": row["prompt_md"],
        "split_plan_id": row["split_plan_id"],
        # 列表不背全文（一张卡十几 KB），只给「有没有」，正文走详情接口
        "has_prompt_snapshot": bool(row["prompt_snapshot"]),
        "params": _ujo(row["params_json"]),
        "duration": row["duration"],
        "width": row["width"],
        "height": row["height"],
        "has_audio": row["has_audio"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if with_snapshot:
        out["prompt_snapshot"] = row["prompt_snapshot"]
    return out


def list_cards(db, filters: Optional[dict] = None) -> list[dict]:
    filters = filters or {}
    sql, args = "SELECT * FROM cards WHERE 1=1", []
    if filters.get("workspace"):
        sql += " AND workspace=?"; args.append(filters["workspace"])
    if filters.get("status") and filters["status"] != "all":
        sql += " AND status=?"; args.append(filters["status"])
    if filters.get("seg"):
        sql += " AND seg=?"; args.append(filters["seg"])
    if filters.get("tag"):
        sql += " AND tags LIKE ?"; args.append(f"%\"{filters['tag']}\"%")
    if filters.get("q"):
        sql += " AND (rel_path LIKE ? OR note LIKE ? OR reject_note LIKE ?)"
        like = f"%{filters['q']}%"; args += [like, like, like]
    sql += " ORDER BY updated_at DESC"
    with db.lock:
        rows = db.conn.execute(sql, args).fetchall()
        return [_row_to_card(r) for r in rows]


def update_card(db, card_id: int, patch: dict) -> Optional[dict]:
    fields = ["status", "reject_tags", "reject_note", "note", "seg", "tags",
              "chunk", "prompt_md", "split_plan_id", "prompt_snapshot", "params_json"]
    sets, args = [], []
    for f in fields:
        if f in patch:
            sets.append(f"{f}=?")
            v = patch[f]
            args.append(_j(v) if f in ("reject_tags", "tags", "params_json") else v)
    with db.lock:
        if not sets:
            row = db.conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
            return _row_to_card(row) if row else None
        sets.append("updated_at=?"); args.append(_now()); args.append(card_id)
        db.conn.execute(f"UPDATE cards SET {', '.join(sets)} WHERE id=?", args)
        db.conn.commit()
        row = db.conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        return _row_to_card(row) if row else None


def bulk_update_status(db, ids: list[int], status: str) -> int:
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    with db.lock:
        cur = db.conn.execute(
            f"UPDATE cards SET status=?, updated_at=? WHERE id IN ({marks})",
            (status, _now(), *ids),
        )
        db.conn.commit()
        return cur.rowcount


def card_file_path(cfg, card: dict) -> Optional[Path]:
    root = cfg.ws_root()
    if root is None:
        return None
    return root / card["rel_path"]


def media_exists(db, rel_path: str, workspace: str) -> bool:
    with db.lock:
        row = db.conn.execute(
            "SELECT 1 FROM cards WHERE workspace=? AND rel_path=? LIMIT 1",
            (workspace, rel_path),
        ).fetchone()
        return row is not None


def latest_card_for(db, workspace: str, seg: str, chunk: str) -> Optional[dict]:
    """取 (workspace, seg, chunk) 的最新一张卡（用于末帧继承）。"""
    with db.lock:
        row = db.conn.execute(
            "SELECT * FROM cards WHERE workspace=? AND seg=? AND chunk=?"
            " ORDER BY updated_at DESC, id DESC LIMIT 1",
            (workspace, seg, chunk),
        ).fetchone()
        return _row_to_card(row) if row else None


# ---------------------------------------------------------------------------
# assets（{asset} 目录的递归索引）
# ---------------------------------------------------------------------------

def upsert_asset(db, workspace: str, item: dict) -> bool:
    """按 (workspace, rel_path) 幂等 upsert 一条资产。返回是否新建。"""
    with db.lock:
        conn = db.conn
        row = conn.execute(
            "SELECT 1 FROM assets WHERE workspace=? AND rel_path=?",
            (workspace, item["rel_path"]),
        ).fetchone()
        created = row is None
        vals = (
            workspace,
            item["rel_path"],
            item["name"],
            item.get("dir_rel", ""),
            item.get("subject"),
            _j(item.get("tags", [])),
            item.get("category"),
            item.get("kind"),
            item.get("ext"),
            item.get("size"),
            item.get("mtime"),
            _now(),
        )
        if created:
            conn.execute(
                "INSERT INTO assets (workspace, rel_path, name, dir_rel, subject, tags,"
                " category, kind, ext, size, mtime, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                vals,
            )
        else:
            conn.execute(
                "UPDATE assets SET name=?, dir_rel=?, subject=?, tags=?, category=?,"
                " kind=?, ext=?, size=?, mtime=?, updated_at=? WHERE workspace=? AND rel_path=?",
                (*vals[2:], workspace, item["rel_path"]),
            )
        conn.commit()
        return created


def list_assets(db, workspace: str) -> list[dict]:
    with db.lock:
        rows = db.conn.execute(
            "SELECT * FROM assets WHERE workspace=? ORDER BY rel_path COLLATE NOCASE",
            (workspace,),
        ).fetchall()
        return [_row_to_asset(r) for r in rows]


def prune_assets(db, workspace: str, keep: set[str]) -> int:
    """删掉磁盘上已不存在的资产记录（keep = 本次扫到的 rel_path 集合）。"""
    with db.lock:
        rows = db.conn.execute(
            "SELECT rel_path FROM assets WHERE workspace=?", (workspace,)
        ).fetchall()
        gone = [r["rel_path"] for r in rows if r["rel_path"] not in keep]
        if gone:
            marks = ",".join("?" for _ in gone)
            db.conn.execute(
                f"DELETE FROM assets WHERE workspace=? AND rel_path IN ({marks})",
                (workspace, *gone),
            )
            db.conn.commit()
        return len(gone)


def delete_asset(db, workspace: str, rel_path: str) -> bool:
    with db.lock:
        cur = db.conn.execute(
            "DELETE FROM assets WHERE workspace=? AND rel_path=?", (workspace, rel_path)
        )
        db.conn.commit()
        return cur.rowcount > 0


def _row_to_asset(row) -> dict:
    return {
        "rel_path": row["rel_path"],
        "name": row["name"],
        "dir_rel": row["dir_rel"] or "",
        "subject": row["subject"] or "",
        "tags": _uj(row["tags"]),
        "category": row["category"] or "character",
        "kind": row["kind"] or "other",
        "ext": row["ext"] or "",
        "size": row["size"] or 0,
        "mtime": row["mtime"],
        "updated_at": row["updated_at"],
    }


def asset_dirs_for_subject(db, workspace: str, subject: str) -> list[str]:
    """该主体已出现过的目录（浅→深），用于上传时「自动定位资产目录位置」。"""
    with db.lock:
        rows = db.conn.execute(
            "SELECT DISTINCT dir_rel FROM assets WHERE workspace=? AND subject=?",
            (workspace, subject),
        ).fetchall()
    return sorted({(r["dir_rel"] or "") for r in rows}, key=lambda d: (len(d.split("/")) if d else 0, d))


# ---------------------------------------------------------------------------
# split_plans
# ---------------------------------------------------------------------------

def get_split_plan(db, plan_id: int) -> Optional[dict]:
    with db.lock:
        row = db.conn.execute(
            "SELECT * FROM split_plans WHERE id=?", (plan_id,)
        ).fetchone()
        return _row_to_plan(row) if row else None


def latest_split_plan(db, workspace: str, seg: str) -> Optional[dict]:
    """取某段最近一次的拆分计划（补收时用来还原 tags / plan_id）。"""
    with db.lock:
        row = db.conn.execute(
            "SELECT * FROM split_plans WHERE workspace=? AND seg=?"
            " ORDER BY id DESC LIMIT 1",
            (workspace, seg),
        ).fetchone()
        return _row_to_plan(row) if row else None


def _row_to_plan(row) -> dict:
    return {
        "id": row["id"],
        "workspace": row["workspace"],
        "seg": row["seg"],
        "strategy": row["strategy"],
        "source_md": row["source_md"],
        "manifest_json": row["manifest_json"],
        "created_at": row["created_at"],
    }


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------

def create_job(
    db,
    workspace: str,
    seg: str,
    chunk: str,
    prompt_file: str,
    status: str = "queued",
    *,
    prompt_snapshot: Optional[str] = None,
    params: Optional[dict] = None,
) -> int:
    with db.lock:
        cur = db.conn.execute(
            "INSERT INTO jobs (workspace, seg, chunk, prompt_file, status,"
            " prompt_snapshot, params_json, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (workspace, seg, chunk, prompt_file, status, prompt_snapshot,
             _j(params) if params else None, _now(), _now()),
        )
        db.conn.commit()
        return cur.lastrowid


def update_job(db, job_id: int, fields: dict) -> Optional[dict]:
    allowed = {"comfy_prompt_id", "status", "log_tail", "progress", "stage",
               "prompt_snapshot", "params_json"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            args.append(_j(v) if k == "params_json" and v is not None else v)
    if not sets:
        return get_job(db, job_id)
    sets.append("updated_at=?"); args.append(_now()); args.append(job_id)
    with db.lock:
        db.conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", args)
        db.conn.commit()
    return get_job(db, job_id)


def get_job(db, job_id: int) -> Optional[dict]:
    with db.lock:
        row = db.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None


def list_jobs(db, workspace: Optional[str] = None, limit: int = 50) -> list[dict]:
    sql, args = "SELECT * FROM jobs WHERE 1=1", []
    if workspace:
        sql += " AND workspace=?"; args.append(workspace)
    sql += " ORDER BY id DESC LIMIT ?"; args.append(limit)
    with db.lock:
        rows = db.conn.execute(sql, args).fetchall()
        return [_row_to_job(r) for r in rows]


def delete_job(db, job_id: int) -> bool:
    """删掉一条任务记录（只动库，不碰产物文件）。"""
    with db.lock:
        cur = db.conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        db.conn.commit()
        return cur.rowcount > 0


def mark_stale_jobs(db, workspace: Optional[str] = None) -> int:
    """把还挂在 queued/running 的任务落成 stopped，返回改了几条。

    后端一重启，进程里那些跑片协程就全没了 —— 这些记录不可能再被更新。
    不清理的话，「活动任务」会永远显示成进行中，还会一直轮询。
    """
    note = "[中断] 后端重启，已停止追踪"
    # 注意：SQLite 的 trim(X) 只去空格，不去换行 —— 必须显式给字符集，
    # 否则 log_tail 为空时会留下一个开头的空行。
    sql = (
        "UPDATE jobs SET status='stopped', stage='已中断',"
        " log_tail=trim(COALESCE(log_tail,'') || char(10) || ?,"
        "               char(10) || char(13) || char(9) || ' '),"
        " updated_at=? WHERE status IN ('queued','running')"
    )
    args: list = [note, _now()]
    if workspace:
        sql += " AND workspace=?"
        args.append(workspace)
    with db.lock:
        cur = db.conn.execute(sql, args)
        db.conn.commit()
        return cur.rowcount


def _row_to_job(row) -> dict:
    return {
        "id": row["id"],
        "workspace": row["workspace"],
        "seg": row["seg"],
        "chunk": row["chunk"],
        "prompt_file": row["prompt_file"],
        "comfy_prompt_id": row["comfy_prompt_id"],
        "status": row["status"],
        "log_tail": row["log_tail"],
        "progress": row["progress"],
        "stage": row["stage"],
        # 快照：这次跑片用的是哪一版正文与哪组参数（事后追溯的唯一依据）
        "has_prompt_snapshot": bool(row["prompt_snapshot"]),
        "params": _ujo(row["params_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }