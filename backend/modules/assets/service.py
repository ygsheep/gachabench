# -*- coding: utf-8 -*-
"""扫描任务管理：进程内单飞、scan_runs 状态、SSE 进度。"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select

from config import get_config
from modules.assets.indexer import index_single, scan_workspace
from modules.assets.models import IndexAsset, ScanRun, now_iso
from modules.platform.db import ensure_schema, session_scope
from modules.platform.ids import ulid
from modules.system.events import bus

_TERMINAL = {"done", "error", "cancelled"}

log = logging.getLogger("gachabench.index")


class ScanManager:
    def __init__(self) -> None:
        self._guard = threading.RLock()
        self._active: dict[str, str] = {}
        self._pending: set[str] = set()

    # ------------------------------------------------------------------
    # 对外
    # ------------------------------------------------------------------

    def is_running(self, root: Path) -> bool:
        with self._guard:
            return str(Path(root).resolve()) in self._active

    def mark_pending(self, root: Path) -> None:
        with self._guard:
            self._pending.add(str(Path(root).resolve()))

    def start(self, root: Path, force: bool = False) -> dict:
        root = Path(root).resolve()
        key = str(root)
        with self._guard:
            current = self._active.get(key)
            if current:
                if force:
                    self._pending.add(key)
                return {"task_id": current, "status": "running"}

            task_id = ulid()
            self._active[key] = task_id

        cfg = get_config()
        ws_name = cfg.active if cfg.ws_root() == root else root.name
        with session_scope(root) as session:
            session.add(
                ScanRun(
                    id=task_id,
                    workspace=ws_name,
                    started_at=now_iso(),
                    status="running",
                    stats_json="{}",
                    force=int(force),
                )
            )

        thread = threading.Thread(
            target=self._run,
            args=(root, task_id, force),
            name=f"gb-scan-{task_id[:8]}",
            daemon=True,
        )
        thread.start()
        return {"task_id": task_id, "status": "running"}

    def status(self, root: Path, task_id: str) -> Optional[dict]:
        root = Path(root).resolve()
        with session_scope(root) as session:
            run = session.scalar(select(ScanRun).where(ScanRun.id == task_id))
            if run is None:
                return None
            try:
                stats = json.loads(run.stats_json or "{}")
            except Exception:
                stats = {}
            return {
                "id": run.id,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "stats": stats,
                "force": bool(run.force),
            }

    def ensure_started(self, root: Path) -> None:
        """启动时：schema 刚建/升级，或索引为空 → 触发一次全量扫描。"""
        root = Path(root).resolve()
        if not root.is_dir():
            return
        try:
            migrated = ensure_schema(root)
        except Exception as exc:  # noqa: BLE001
            # 这里必须留日志：schema 没建好，资产接口会连着 500，静默会把排查成本拉满
            log.exception("[index] schema 升级失败（%s）：%s", root, exc)
            return
        with session_scope(root) as session:
            count = session.scalar(select(func.count()).select_from(IndexAsset)) or 0
        if migrated or count == 0:
            self.start(root, force=True)

    def index_one(self, root: Path, rel_path: str) -> Optional[str]:
        return index_single(root, rel_path)

    def shutdown(self) -> None:
        # 扫描线程是 daemon；这里只需要避免退出时再派发新任务。
        with self._guard:
            self._pending.clear()

    # ------------------------------------------------------------------
    # 线程体
    # ------------------------------------------------------------------

    def _run(self, root: Path, task_id: str, force: bool) -> None:
        cfg = get_config()
        ws_name = cfg.active if cfg.ws_root() == root else root.name
        bus.publish(
            "index.progress",
            {
                "task_id": task_id,
                "workspace": ws_name,
                "processed": 0,
                "total": 0,
                "done": False,
            },
        )

        def progress(done: int, total: int) -> None:
            bus.publish(
                "index.progress",
                {
                    "task_id": task_id,
                    "workspace": ws_name,
                    "processed": done,
                    "total": total,
                    "done": False,
                },
            )

        status = "done"
        stats: dict = {}
        try:
            stats = scan_workspace(root, force=force, progress_cb=progress)
        except Exception as exc:  # noqa: BLE001
            status = "error"
            stats = {"error": str(exc)}
            log.exception("[index] 扫描失败：%s", exc)
        finally:
            try:
                with session_scope(root) as session:
                    run = session.scalar(select(ScanRun).where(ScanRun.id == task_id))
                    if run is not None:
                        run.status = status
                        run.finished_at = now_iso()
                        run.stats_json = json.dumps(stats, ensure_ascii=False)
            except Exception as exc:  # noqa: BLE001
                log.warning("[index] 写扫描状态失败：%s", exc)

            bus.publish(
                "index.progress",
                {
                    "task_id": task_id,
                    "workspace": ws_name,
                    "processed": int(stats.get("total", 0) or 0),
                    "total": int(stats.get("total", 0) or 0),
                    "done": True,
                    "status": status,
                },
            )
            bus.publish(
                "file.changed",
                {
                    "task_id": task_id,
                    "workspace": ws_name,
                    "status": status,
                    "stats": stats,
                },
            )

            rerun = False
            with self._guard:
                self._active.pop(str(root), None)
                if str(root) in self._pending:
                    self._pending.discard(str(root))
                    rerun = True
            if rerun:
                self.start(root, force=False)


_manager = ScanManager()


def get_scan_manager() -> ScanManager:
    return _manager
