# -*- coding: utf-8 -*-
"""文件监听：watchfiles 300ms 去抖，变化触发增量扫描。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, Optional

from watchfiles import awatch

from modules.assets import roles
from modules.assets.service import get_scan_manager


def _watch_filter(root: Path) -> Callable:
    root = root.resolve()

    def keep(_change, path: str) -> bool:
        try:
            rel = Path(path).resolve().relative_to(root)
        except (ValueError, OSError):
            return False
        parts = rel.parts
        if any(roles.is_skipped_dir(part) for part in parts[:-1]):
            return False
        name = rel.name
        if name.startswith(".tmp-") or name.endswith((".tmp", ".swp", "~")):
            return False
        return True

    return keep


async def watch_workspace(
    root: Path, stop_event: Optional[asyncio.Event] = None
) -> None:
    manager = get_scan_manager()
    root = Path(root).resolve()
    if not root.is_dir():
        return
    while True:
        try:
            async for _changes in awatch(
                str(root),
                watch_filter=_watch_filter(root),
                debounce=300,
                stop_event=stop_event,
            ):
                if manager.is_running(root):
                    manager.mark_pending(root)
                else:
                    manager.start(root, force=False)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[watch] 监听失败，5 秒后重试：{exc}")
            await asyncio.sleep(5)
