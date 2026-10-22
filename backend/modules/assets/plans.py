# -*- coding: utf-8 -*-
"""P2 计划引擎：改名 / 移动 / 删除的影响面计算、应用与回滚。

约定：
- 计划 30 分钟过期，apply 时先校验有效期与 base 哈希/mtime；
- 应用顺序：备份 → journal → 临时名 + os.replace → 改写引用 → DB 事务 → 删 journal；
- 任一步失败按 journal 逆序还原，计划状态 `failed`；
- 只改大小写时经 `.tmp-<uuid>` 中转，绕开 Windows 的同名比较。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from modules.assets import refs, roles, search, trash
from modules.assets.models import AssetRef, IndexAsset, OperationPlan, TrashEntry, now_iso
from modules.platform.db import session_scope
from modules.platform.ids import ulid
from modules.prompts.rewrite import RewriteError, apply_spans

PLAN_TTL = timedelta(minutes=30)
BACKUP_DIR = ".gachabench/backups"
JOURNAL_DIR = ".gachabench/journal"
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_ILLEGAL_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _error(status: int, code: str, message: str) -> None:
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _sha256(path: Path) -> Optional[str]:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _file_stat(path: Path) -> dict:
    st = path.stat()
    return {"mtime": st.st_mtime, "size": int(st.st_size)}


def _join_rel(dir_rel: str, name: str) -> str:
    return f"{dir_rel}/{name}" if dir_rel else name


def _parent_rel(rel: str) -> str:
    parent = Path(rel).parent.as_posix()
    return "" if parent == "." else parent


def _validate_new_name(raw: Any) -> str:
    name = str(raw or "").strip()
    if not name:
        _error(400, "NAME_INVALID", "文件名不能为空")
    if name in (".", ".."):
        _error(400, "NAME_INVALID", "文件名不合法")
    if _ILLEGAL_NAME_RE.search(name):
        _error(400, "NAME_INVALID", "文件名不能包含路径分隔符或系统保留字符")
    if name != Path(name).name:
        _error(400, "NAME_INVALID", "只接受文件名，不接受路径")
    if name.endswith((" ", ".")):
        _error(400, "NAME_INVALID", "文件名不能以空格或点结尾")
    return name


def _normalize_rel_dir(root: Path, raw: Any) -> str:
    rel = str(raw or "").strip().replace("\\", "/")
    if rel in ("", "."):
        return ""
    if rel.startswith("/") or _DRIVE_RE.match(rel):
        _error(400, "PATH_INVALID", "只接受工作区内的相对目录")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        _error(400, "PATH_INVALID", "目标目录越界")
    if any(roles.is_skipped_dir(p) for p in parts):
        _error(400, "PATH_INVALID", "不能把文件移进系统目录")
    clean = "/".join(parts)
    target = (root / clean).resolve()
    if not target.is_relative_to(root):
        _error(400, "PATH_INVALID", "目标目录越界")
    return clean


def _normalize_ids(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        _error(400, "INVALID_REQUEST", "asset_ids 必须是数组")
    ids = [str(x).strip() for x in raw if str(x).strip()]
    if not ids:
        _error(400, "INVALID_REQUEST", "asset_ids 不能为空")
    # 去重但保留顺序
    return list(dict.fromkeys(ids))


# ---------------------------------------------------------------------------
# 计划生成
# ---------------------------------------------------------------------------

def _asset_base(asset: IndexAsset) -> dict:
    return {
        "id": asset.id,
        "path": asset.path,
        "sha256": asset.sha256,
        "mtime": asset.mtime,
        "size": asset.size,
    }


def _plan_rename(root: Path, session: Session, payload: dict) -> dict:
    asset_id = str(payload.get("asset_id") or "").strip()
    new_name = _validate_new_name(payload.get("new_name"))
    asset = session.get(IndexAsset, asset_id) if asset_id else None
    if asset is None:
        _error(404, "ASSET_NOT_FOUND", "资产不存在")
    if asset.status != "active":
        _error(409, "ASSET_NOT_ACTIVE", "只有正常状态的资产可以改名")

    old_name = asset.name
    target_rel = _join_rel(asset.dir_path or "", new_name)
    same_file = target_rel.casefold() == asset.path.casefold()

    blocked: list[dict] = []
    if not same_file:
        conflict = (root / target_rel).exists()
        if not conflict:
            row = session.scalar(
                select(IndexAsset).where(IndexAsset.path == target_rel)
            )
            conflict = row is not None and row.id != asset.id
        if conflict:
            blocked.append(
                {"asset_id": asset.id, "path": asset.path, "reason": "NAME_CONFLICT"}
            )

    normalized = {
        "asset_id": asset.id,
        "old_name": old_name,
        "new_name": new_name,
    }
    if blocked:
        return {
            "payload": normalized,
            "base": {"assets": {asset.id: _asset_base(asset)}, "files": {}},
            "files": [],
            "blocked": blocked,
            "stats": {"assets": 0, "files": 0, "refs": 0},
        }

    refs.scan_refs(root, session, filenames=[old_name])
    rows = session.scalars(
        select(AssetRef).where(AssetRef.asset_id == asset.id).order_by(AssetRef.file_path)
    ).all()
    by_file: dict[str, list[AssetRef]] = {}
    for row in rows:
        by_file.setdefault(row.file_path, []).append(row)

    files: list[dict] = []
    base_files: dict[str, dict] = {}
    total_refs = 0
    for file_path in sorted(by_file):
        abs_path = root / file_path
        loaded = refs.read_text_with_encoding(abs_path)
        if loaded is None:
            continue
        text, encoding = loaded
        spans = [(start, end, new_name) for start, end in refs.find_name_spans(text, old_name)]
        if not spans:
            continue
        try:
            after_text, _records = apply_spans(text, spans)
        except RewriteError as exc:
            _error(409, "REWRITE_FAILED", f"{file_path}：{exc}")
        first = min(by_file[file_path], key=lambda r: (r.line or 0, r.col or 0))
        stat = _file_stat(abs_path)
        files.append(
            {
                "file_path": file_path,
                "line": first.line,
                "kind": first.kind,
                "before": text,
                "after": after_text,
                "selected": True,
                "encoding": encoding,
                "refs": len(spans),
            }
        )
        base_files[file_path] = {
            "path": file_path,
            "sha256": _sha256(abs_path),
            "mtime": stat["mtime"],
            "size": stat["size"],
            "encoding": encoding,
        }
        total_refs += len(spans)

    return {
        "payload": normalized,
        "base": {"assets": {asset.id: _asset_base(asset)}, "files": base_files},
        "files": files,
        "blocked": [],
        "stats": {"assets": 1, "files": len(files), "refs": total_refs},
    }


def _plan_move(root: Path, session: Session, payload: dict) -> dict:
    asset_ids = _normalize_ids(payload.get("asset_ids"))
    dest_dir = _normalize_rel_dir(root, payload.get("dest_dir"))

    blocked: list[dict] = []
    actionable: list[IndexAsset] = []
    for asset_id in asset_ids:
        asset = session.get(IndexAsset, asset_id)
        if asset is None:
            _error(404, "ASSET_NOT_FOUND", f"资产不存在：{asset_id}")
        if asset.status != "active":
            blocked.append(
                {"asset_id": asset.id, "path": asset.path, "reason": "NOT_ACTIVE"}
            )
            continue
        target_rel = _join_rel(dest_dir, asset.name)
        if target_rel.casefold() != asset.path.casefold():
            conflict = (root / target_rel).exists()
            if not conflict:
                row = session.scalar(
                    select(IndexAsset).where(IndexAsset.path == target_rel)
                )
                conflict = row is not None and row.id != asset.id
            if conflict:
                blocked.append(
                    {"asset_id": asset.id, "path": asset.path, "reason": "NAME_CONFLICT"}
                )
                continue
        actionable.append(asset)

    names = [a.name for a in actionable]
    if names:
        refs.scan_refs(root, session, filenames=names)
    ref_count = 0
    if actionable:
        ref_count = int(
            session.scalar(
                select(func.count())
                .select_from(AssetRef)
                .where(AssetRef.asset_id.in_([a.id for a in actionable]))
            )
            or 0
        )

    return {
        "payload": {"asset_ids": [a.id for a in actionable], "dest_dir": dest_dir},
        "base": {
            "assets": {a.id: _asset_base(a) for a in actionable},
            "files": {},
        },
        "files": [],
        "blocked": blocked,
        "stats": {"assets": len(actionable), "files": 0, "refs": ref_count},
    }


def _plan_delete(root: Path, session: Session, payload: dict) -> dict:
    asset_ids = _normalize_ids(payload.get("asset_ids"))
    blocked: list[dict] = []
    actionable: list[IndexAsset] = []
    for asset_id in asset_ids:
        asset = session.get(IndexAsset, asset_id)
        if asset is None:
            _error(404, "ASSET_NOT_FOUND", f"资产不存在：{asset_id}")
        if asset.status != "active":
            blocked.append(
                {"asset_id": asset.id, "path": asset.path, "reason": "NOT_ACTIVE"}
            )
            continue
        actionable.append(asset)

    names = [a.name for a in actionable]
    if names:
        refs.scan_refs(root, session, filenames=names)
    ref_count = 0
    if actionable:
        ref_count = int(
            session.scalar(
                select(func.count())
                .select_from(AssetRef)
                .where(AssetRef.asset_id.in_([a.id for a in actionable]))
            )
            or 0
        )

    return {
        "payload": {"asset_ids": [a.id for a in actionable]},
        "base": {
            "assets": {a.id: _asset_base(a) for a in actionable},
            "files": {},
        },
        # 删除不改写引用，只列出引用数供确认。
        "files": [],
        "blocked": blocked,
        "stats": {"assets": len(actionable), "files": 0, "refs": ref_count},
    }


def create_plan(root: Path, kind: str, payload: dict) -> dict:
    root = Path(root).resolve()
    kind = (kind or "").strip().lower()
    if kind not in ("rename", "move", "delete"):
        _error(400, "UNKNOWN_KIND", "kind 只能是 rename、move 或 delete")

    with session_scope(root) as session:
        if kind == "rename":
            computed = _plan_rename(root, session, payload or {})
        elif kind == "move":
            computed = _plan_move(root, session, payload or {})
        else:
            computed = _plan_delete(root, session, payload or {})

        now = datetime.now(timezone.utc)
        plan = OperationPlan(
            id=ulid(),
            kind=kind,
            payload_json=json.dumps(computed["payload"], ensure_ascii=False),
            base_json=json.dumps(computed["base"], ensure_ascii=False),
            status="planned",
            created_at=_iso(now),
            expires_at=_iso(now + PLAN_TTL),
            result_json=json.dumps(
                {
                    "files": computed["files"],
                    "blocked": computed["blocked"],
                    "stats": computed["stats"],
                },
                ensure_ascii=False,
            ),
        )
        session.add(plan)
        session.flush()
        session.expunge(plan)
        return _plan_dict(plan)


def _plan_dict(plan: OperationPlan) -> dict:
    try:
        payload = json.loads(plan.payload_json or "{}")
    except Exception:
        payload = {}
    try:
        data = json.loads(plan.result_json or "{}")
    except Exception:
        data = {}
    return {
        "id": plan.id,
        "kind": plan.kind,
        "status": plan.status,
        "created_at": plan.created_at,
        "expires_at": plan.expires_at,
        "applied_at": plan.applied_at,
        "payload": payload,
        "stats": data.get("stats") or {"assets": 0, "files": 0, "refs": 0},
        "files": data.get("files") or [],
        "blocked": data.get("blocked") or [],
        "result": data.get("apply"),
    }


def get_plan(root: Path, plan_id: str) -> dict:
    root = Path(root).resolve()
    with session_scope(root) as session:
        plan = session.get(OperationPlan, plan_id)
        if plan is None:
            _error(404, "PLAN_NOT_FOUND", "计划不存在")
        session.expunge(plan)
        return _plan_dict(plan)


# ---------------------------------------------------------------------------
# 应用
# ---------------------------------------------------------------------------

def _journal_path(root: Path, plan_id: str) -> Path:
    return root / JOURNAL_DIR / f"{plan_id}.json"


def _write_journal(path: Path, journal: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(journal, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _record(journal: dict, path: Path, step: dict) -> None:
    journal["steps"].append(step)
    _write_journal(path, journal)


def _update_step(journal: dict, path: Path, step: dict, state: str) -> None:
    step["state"] = state
    _write_journal(path, journal)


def _backup(root: Path, plan_id: str, base: dict) -> Path:
    backup_dir = root / BACKUP_DIR / plan_id
    rels = {a["path"] for a in (base.get("assets") or {}).values()}
    rels |= set(base.get("files") or {})
    for rel in rels:
        src = root / rel
        if not src.is_file():
            continue
        dst = backup_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def _validate_base(root: Path, base: dict) -> None:
    for info in (base.get("assets") or {}).values():
        rel = info.get("path") or ""
        path = root / rel
        if not path.is_file():
            _error(409, "BASE_CHANGED", f"资产文件已变化：{rel}")
        st = path.stat()
        if int(st.st_size) != int(info.get("size") or 0):
            _error(409, "BASE_CHANGED", f"资产大小已变化：{rel}")
        if abs(float(st.st_mtime) - float(info.get("mtime") or 0)) > 1e-6:
            _error(409, "BASE_CHANGED", f"资产修改时间已变化：{rel}")
        sha = info.get("sha256")
        if sha and _sha256(path) != sha:
            _error(409, "BASE_CHANGED", f"资产内容已变化：{rel}")
    for rel, info in (base.get("files") or {}).items():
        path = root / rel
        if not path.is_file():
            _error(409, "BASE_CHANGED", f"引用文件已变化：{rel}")
        st = path.stat()
        if int(st.st_size) != int(info.get("size") or 0):
            _error(409, "BASE_CHANGED", f"引用文件大小已变化：{rel}")
        if abs(float(st.st_mtime) - float(info.get("mtime") or 0)) > 1e-6:
            _error(409, "BASE_CHANGED", f"引用文件修改时间已变化：{rel}")
        sha = info.get("sha256")
        if sha and _sha256(path) != sha:
            _error(409, "BASE_CHANGED", f"引用文件内容已变化：{rel}")


def _atomic_rename(
    root: Path, journal: dict, journal_path: Path, src_rel: str, dst_rel: str
) -> None:
    """临时名中转 + os.replace，确保同卷原子且绕开大小写同名。"""
    src = root / src_rel
    dst = root / dst_rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = src.with_name(f".tmp-{uuid4().hex}")
    step = {
        "op": "move",
        "from": src_rel,
        "to": dst_rel,
        "temp": tmp.relative_to(root).as_posix(),
        "state": "started",
    }
    _record(journal, journal_path, step)
    os.replace(src, tmp)
    _update_step(journal, journal_path, step, "temp")
    os.replace(tmp, dst)
    _update_step(journal, journal_path, step, "done")


def _read_text_exact(path: Path, encoding: str) -> str:
    """按字节解码，保留原始换行（避免 read_text 的 universal newline 改写）。"""
    return path.read_bytes().decode(encoding)


def _write_text_atomic(path: Path, text: str, encoding: str) -> None:
    tmp = path.with_name(f".tmp-{uuid4().hex}")
    tmp.write_bytes(text.encode(encoding))
    os.replace(tmp, path)


def _apply_files(
    root: Path,
    plan_id: str,
    kind: str,
    payload: dict,
    snapshot: dict,
    base: dict,
    journal: dict,
) -> dict:
    journal_path = _journal_path(root, plan_id)
    result: dict = {"moves": [], "trashed": [], "texts": [], "stats": snapshot.get("stats", {})}

    if kind in ("rename", "move"):
        dest_dir = payload.get("dest_dir", "") if kind == "move" else ""
        for asset_id, info in (base.get("assets") or {}).items():
            src_rel = info["path"]
            if kind == "rename":
                dst_rel = _join_rel(_parent_rel(src_rel), payload["new_name"])
            else:
                dst_rel = _join_rel(dest_dir, Path(src_rel).name)
            _atomic_rename(root, journal, journal_path, src_rel, dst_rel)
            target = root / dst_rel
            stat = _file_stat(target)
            result["moves"].append(
                {
                    "asset_id": asset_id,
                    "from": src_rel,
                    "to": dst_rel,
                    "sha256": _sha256(target),
                    "size": stat["size"],
                    "mtime": stat["mtime"],
                }
            )

        if kind == "rename":
            old_name = payload["old_name"]
            new_name = payload["new_name"]
            moved = {m["from"]: m["to"] for m in result["moves"]}
            for item in snapshot.get("files") or []:
                rel = item["file_path"]
                # 被改名的资产本身可能就是引用来源（例如 md 提到自己的文件名）。
                actual_rel = moved.get(rel, rel)
                path = root / actual_rel
                encoding = item.get("encoding") or "utf-8"
                try:
                    text = _read_text_exact(path, encoding)
                except (OSError, UnicodeDecodeError) as exc:
                    raise RewriteError(f"读取失败：{actual_rel}：{exc}") from exc
                spans = [
                    (start, end, new_name)
                    for start, end in refs.find_name_spans(text, old_name)
                ]
                if not spans:
                    continue
                after_text, _records = apply_spans(text, spans)
                if item.get("after") is not None and after_text != item["after"]:
                    raise RewriteError(f"计划差异与当前文件不一致：{actual_rel}")
                step = {
                    "op": "rewrite",
                    "path": actual_rel,
                    "backup": rel,
                    "state": "started",
                }
                _record(journal, journal_path, step)
                _write_text_atomic(path, after_text, encoding)
                _update_step(journal, journal_path, step, "done")
                stat = _file_stat(path)
                result["texts"].append(
                    {
                        "file_path": actual_rel,
                        "backup_rel": rel,
                        "sha256": _sha256(path),
                        "size": stat["size"],
                        "mtime": stat["mtime"],
                    }
                )

    elif kind == "delete":
        for asset_id, info in (base.get("assets") or {}).items():
            src_rel = info["path"]
            src = root / src_rel
            trash_abs, trash_rel = trash.trash_path_for(
                root, asset_id, Path(src_rel).name
            )
            trash_abs.parent.mkdir(parents=True, exist_ok=True)
            step = {
                "op": "trash",
                "asset_id": asset_id,
                "from": src_rel,
                "to": trash_rel,
                "state": "started",
            }
            _record(journal, journal_path, step)
            os.replace(src, trash_abs)
            _update_step(journal, journal_path, step, "done")
            result["trashed"].append(
                {"asset_id": asset_id, "from": src_rel, "to": trash_rel}
            )

    return result


def _apply_db(
    root: Path, session: Session, plan_id: str, kind: str, result: dict
) -> None:
    plan = session.get(OperationPlan, plan_id)
    if plan is None:
        raise RuntimeError("计划记录在应用过程中消失")

    for move in result.get("moves") or []:
        asset = session.get(IndexAsset, move["asset_id"])
        if asset is None:
            raise RuntimeError(f"资产记录消失：{move['asset_id']}")
        asset.path = move["to"]
        asset.name = Path(move["to"]).name
        asset.dir_path = _parent_rel(move["to"])
        asset.updated_at = now_iso()
        search.sync_fts(session, asset.id)
        # 被改写的文本本身也可能是一个资产，路径跟着换。
        session.execute(
            update(AssetRef)
            .where(AssetRef.file_path == move["from"])
            .values(file_path=move["to"])
        )

    for item in result.get("trashed") or []:
        asset = session.get(IndexAsset, item["asset_id"])
        if asset is None:
            raise RuntimeError(f"资产记录消失：{item['asset_id']}")
        asset.status = "trashed"
        asset.updated_at = now_iso()
        trash.create_entry(session, asset.id, item["from"], item["to"])

    session.flush()

    # 改名 / 移动后重建引用；删除保留原引用，供「可恢复」提示使用。
    if kind in ("rename", "move"):
        refs.scan_refs(root, session)

    snapshot = {}
    try:
        snapshot = json.loads(plan.result_json or "{}")
    except Exception:
        snapshot = {}
    snapshot["apply"] = result
    plan.status = "applied"
    plan.applied_at = now_iso()
    plan.result_json = json.dumps(snapshot, ensure_ascii=False)
    session.flush()


def _set_plan_status(
    root: Path, plan_id: str, status: str, error: Optional[str] = None
) -> None:
    with session_scope(root) as session:
        plan = session.get(OperationPlan, plan_id)
        if plan is None:
            return
        try:
            data = json.loads(plan.result_json or "{}")
        except Exception:
            data = {}
        if error:
            data["error"] = error
        plan.result_json = json.dumps(data, ensure_ascii=False)
        plan.status = status


def _reverse_steps(root: Path, journal: dict) -> None:
    backup_dir = root / BACKUP_DIR / journal.get("plan_id", "")
    for step in reversed(journal.get("steps") or []):
        state = step.get("state")
        try:
            if step.get("op") == "move":
                if state == "done":
                    _reverse_move(root, step["to"], step["from"])
                elif state == "temp":
                    tmp_rel = step.get("temp")
                    if tmp_rel and (root / tmp_rel).exists():
                        _reverse_move(root, tmp_rel, step["from"])
            elif step.get("op") == "trash":
                if state == "done":
                    src = root / step["to"]
                    dst = root / step["from"]
                    if src.is_file():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(src, dst)
            elif step.get("op") == "rewrite":
                if state == "done":
                    backup = backup_dir / step.get("backup", step["path"])
                    target = root / step["path"]
                    if backup.is_file():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup, target)
        except OSError as exc:  # 回滚本身失败不能掩盖原始错误
            print(f"[plan] 回滚步骤失败：{step.get('op')} {exc}")


def _reverse_move(root: Path, src_rel: str, dst_rel: str) -> None:
    src = root / src_rel
    dst = root / dst_rel
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = src.with_name(f".tmp-{uuid4().hex}")
    os.replace(src, tmp)
    os.replace(tmp, dst)


def apply_plan(root: Path, plan_id: str) -> dict:
    root = Path(root).resolve()
    with session_scope(root) as session:
        plan = session.get(OperationPlan, plan_id)
        if plan is None:
            _error(404, "PLAN_NOT_FOUND", "计划不存在")
        if plan.status == "stale":
            _error(409, "PLAN_STALE", "计划已过期，请重新计算影响面")
        if plan.status != "planned":
            _error(409, "PLAN_NOT_APPLICABLE", "计划已应用或已回滚")
        kind = plan.kind
        payload = json.loads(plan.payload_json or "{}")
        base = json.loads(plan.base_json or "{}")
        snapshot = json.loads(plan.result_json or "{}")
        expires_at = plan.expires_at

    expires = _parse_iso(expires_at)
    if expires is None or expires < datetime.now(timezone.utc):
        _set_plan_status(root, plan_id, "stale")
        _error(409, "PLAN_STALE", "计划已过期，请重新计算影响面")

    if snapshot.get("blocked"):
        _error(409, "PLAN_BLOCKED", "计划包含不可执行项，请重新计算")

    _validate_base(root, base)

    journal = {"plan_id": plan_id, "kind": kind, "steps": []}
    journal_path = _journal_path(root, plan_id)

    try:
        # 备份和 journal 也放在 try 里：它们失败同样要落到 failed 状态。
        _backup(root, plan_id, base)
        _write_journal(journal_path, journal)
        result = _apply_files(root, plan_id, kind, payload, snapshot, base, journal)
        with session_scope(root) as session:
            _apply_db(root, session, plan_id, kind, result)
    except Exception as exc:  # noqa: BLE001
        _reverse_steps(root, journal)
        _set_plan_status(root, plan_id, "failed", str(exc))
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, PermissionError):
            _error(409, "FILE_BUSY", f"文件被占用，已回滚：{exc}")
        if isinstance(exc, RewriteError):
            _error(409, "REWRITE_FAILED", f"引用改写失败，已回滚：{exc}")
        _error(409, "APPLY_FAILED", f"应用失败，已回滚：{exc}")

    try:
        journal_path.unlink(missing_ok=True)
    except OSError as exc:
        print(f"[plan] 删除 journal 失败：{exc}")
    return get_plan(root, plan_id)


# ---------------------------------------------------------------------------
# 回滚
# ---------------------------------------------------------------------------

def rollback_plan(root: Path, plan_id: str) -> dict:
    root = Path(root).resolve()
    with session_scope(root) as session:
        plan = session.get(OperationPlan, plan_id)
        if plan is None:
            _error(404, "PLAN_NOT_FOUND", "计划不存在")
        if plan.status != "applied":
            _error(409, "PLAN_NOT_APPLIED", "只有已应用的计划可以回滚")
        try:
            snapshot = json.loads(plan.result_json or "{}")
        except Exception:
            snapshot = {}
        result = snapshot.get("apply") or {}

    backup_dir = root / BACKUP_DIR / plan_id
    moved_back = {m["to"]: m["from"] for m in (result.get("moves") or [])}
    try:
        # 先还原资产路径，引用文本的备份路径仍按计划时刻的相对路径记录。
        for move in reversed(result.get("moves") or []):
            _reverse_move(root, move["to"], move["from"])
        for item in reversed(result.get("trashed") or []):
            src = root / item["to"]
            dst = root / item["from"]
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, dst)
        for text in reversed(result.get("texts") or []):
            backup_rel = text.get("backup_rel") or text["file_path"]
            target_rel = moved_back.get(text["file_path"], text["file_path"])
            backup = backup_dir / backup_rel
            target = root / target_rel
            if backup.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
    except OSError as exc:
        _error(409, "ROLLBACK_FAILED", f"回滚文件失败：{exc}")

    with session_scope(root) as session:
        plan = session.get(OperationPlan, plan_id)
        if plan is None:
            _error(404, "PLAN_NOT_FOUND", "计划不存在")
        for move in result.get("moves") or []:
            asset = session.get(IndexAsset, move["asset_id"])
            if asset is None:
                continue
            asset.path = move["from"]
            asset.name = Path(move["from"]).name
            asset.dir_path = _parent_rel(move["from"])
            asset.updated_at = now_iso()
            search.sync_fts(session, asset.id)
            session.execute(
                update(AssetRef)
                .where(AssetRef.file_path == move["to"])
                .values(file_path=move["from"])
            )
        for item in result.get("trashed") or []:
            asset = session.get(IndexAsset, item["asset_id"])
            if asset is not None:
                asset.status = "active"
                asset.missing_at = None
                asset.updated_at = now_iso()
            entry = session.scalar(
                select(TrashEntry).where(
                    TrashEntry.asset_id == item["asset_id"],
                    TrashEntry.trash_path == item["to"],
                    TrashEntry.restored_at.is_(None),
                )
            )
            if entry is not None:
                entry.restored_at = now_iso()
        session.flush()
        refs.scan_refs(root, session)

        try:
            snapshot = json.loads(plan.result_json or "{}")
        except Exception:
            snapshot = {}
        snapshot["rolled_back_at"] = now_iso()
        plan.result_json = json.dumps(snapshot, ensure_ascii=False)
        plan.status = "rolled_back"
        session.flush()
        session.expunge(plan)
        return _plan_dict(plan)
