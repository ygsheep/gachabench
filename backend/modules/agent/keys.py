# -*- coding: utf-8 -*-
"""模型密钥的存放。

**不进 config.json**：那个文件在仓库里、会被打包、也会被随手发出去。
密钥单独放用户配置目录：

    Windows  %APPDATA%\\GachaBench\\agent.json
    其他     ~/.gachabench/agent.json

文件权限尽量收到 0600（Windows 上 chmod 只是尽力而为）。
接口一律只回末四位，日志里也只打末四位。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

_LOCK = threading.RLock()


def config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "GachaBench"
    return Path.home() / ".gachabench"


def key_path() -> Path:
    return config_dir() / "agent.json"


def _read_all() -> dict[str, Any]:
    path = key_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_key() -> str:
    with _LOCK:
        return str(_read_all().get("api_key") or "")


def save_key(key: str) -> None:
    """空串 = 删除。原子写：先写临时文件再 replace。"""
    with _LOCK:
        path = key_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = _read_all()
        key = (key or "").strip()
        if key:
            data["api_key"] = key
        else:
            data.pop("api_key", None)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        try:  # Windows 上不一定生效，失败不影响功能
            os.chmod(path, 0o600)
        except OSError:
            pass


def clear_key() -> None:
    save_key("")


def mask(key: Optional[str] = None) -> str:
    """只给末四位：够人确认「我填的是哪一把」，不够被拿去用。"""
    value = (load_key() if key is None else key) or ""
    if not value:
        return ""
    tail = value[-4:]
    return f"…{tail}" if len(value) > 4 else "…"
