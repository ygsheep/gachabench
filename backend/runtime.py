# -*- coding: utf-8 -*-
"""运行期路径解析：把「源码运行」和「PyInstaller 冻结运行」两套布局收拢到一处。

源码运行（`python backend/main.py` 或 uvicorn）——**行为与改造前完全一致**：
    BACKEND_DIR = backend/            模块与 tools/ 都在这里
    DATA_DIR    = backend/            config.json 就地读写
    DIST_DIR    = <项目根>/web/dist

冻结运行（GachaBench.exe，PyInstaller onefile）：
    BUNDLE_DIR  = sys._MEIPASS        只读解包目录，随包的数据（web/dist、ps1）都在这
    DATA_DIR    = exe 所在目录（不可写则退到 %LOCALAPPDATA%/GachaBench）
    DIST_DIR    = BUNDLE_DIR/web/dist

为什么必须把两者分开：onefile 每次运行都在 `%TEMP%` 下解出一个**新的**临时目录，
在那里写 config.json 等于每次启动都丢配置。而 exe 所在目录是用户看得见、改得动的地方。

⚠️ 本模块不能 import `config`（会成环）：它只依赖标准库。
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 基本形态
# ---------------------------------------------------------------------------

IS_FROZEN: bool = bool(getattr(sys, "frozen", False))

_HERE = Path(__file__).resolve().parent


def _exe_dir() -> Path:
    """exe 所在目录（源码运行时退化成 backend/）。"""
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    return _HERE


def _bundle_dir() -> Path:
    """随包只读资源所在目录。"""
    if IS_FROZEN:
        # onefile 与 onedir(PyInstaller 6) 都会设置 _MEIPASS
        return Path(getattr(sys, "_MEIPASS", _exe_dir()))
    return _HERE


BUNDLE_DIR: Path = _bundle_dir()
EXE_DIR: Path = _exe_dir()
BACKEND_DIR: Path = BUNDLE_DIR
PROJECT_ROOT: Path = BUNDLE_DIR.parent if IS_FROZEN else _HERE.parent


def resource(*parts: str) -> Path:
    """取随包资源（如 `resource("tools", "pick_folder.ps1")`）。"""
    return BUNDLE_DIR.joinpath(*parts)


# ---------------------------------------------------------------------------
# 数据目录（可写）与 config.json
# ---------------------------------------------------------------------------

def _writable(d: Path) -> bool:
    """真的试着写一个文件 —— 只看 os.access 在 Windows 上会骗人。"""
    probe = d / ".gb_write_probe"
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _pick_data_dir() -> Path:
    if not IS_FROZEN:
        return _HERE
    if _writable(EXE_DIR):
        return EXE_DIR
    # 放在 C:\Program Files 之类只读位置时的退路
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    fallback = Path(base) / "GachaBench"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


DATA_DIR: Path = _pick_data_dir()
CONFIG_PATH: Path = DATA_DIR / "config.json"

# 随包的数据资源（web/dist 就是被 StaticFiles 挂在 / 上的那份）
# 非冻结时必须在 PROJECT_ROOT 下 —— 改造前 main.py 用的就是 PROJECT_ROOT/"web"/"dist"。
DIST_DIR: Path = (BUNDLE_DIR / "web" / "dist") if IS_FROZEN else (PROJECT_ROOT / "web" / "dist")

# 首次运行时要播种的默认配置（构建时由 backend/config.json 复制成 seed/config.json）
SEED_CONFIG: Path = BACKEND_DIR / "seed" / "config.json"


def bundled_tool(name: str) -> Optional[str]:
    """随包的外部工具（ffmpeg / ffprobe）绝对路径；没打进去就返回 None。

    打包时 PyInstaller 把 ffmpeg 整个目录塞进 `_internal/ffmpeg/`，所以这里
    从 BUNDLE_DIR 找。源码运行时没有这个目录，返回 None，行为与改造前一致。
    """
    d = BUNDLE_DIR / "ffmpeg"
    if not d.is_dir():
        return None
    exe = d / (f"{name}.exe" if os.name == "nt" else name)
    return str(exe) if exe.is_file() else None


def seed_config() -> None:
    """冻结运行时：第一次启动把随包的默认配置落到可写目录。

    已经有了就一个字都不动 —— 用户手改过的配置不能被覆盖。
    """
    if not IS_FROZEN or CONFIG_PATH.exists() or not SEED_CONFIG.is_file():
        return
    try:
        shutil.copyfile(SEED_CONFIG, CONFIG_PATH)
        print(f"[init] 已写出默认配置：{CONFIG_PATH}")
    except OSError as e:  # 配置写不出来不该拦住启动：config.load_config() 会再兜一次
        print(f"[init] 写默认配置失败（{e}），将回退到内置默认值")


# ---------------------------------------------------------------------------
# 启动辅助
# ---------------------------------------------------------------------------

def pick_port(preferred: int = 8000, tries: int = 20) -> int:
    """端口被占就往后挪 —— 双击 exe 的用户不该因为另一个服务先占了 8000 就打不开。

    ⚠️ 故意不设 SO_REUSEADDR：Windows 上它允许绑到**正在使用**的端口，
    探活就永远探不出冲突了。
    """
    for port in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def open_browser_later(url: str, delay: float = 1.0) -> None:
    """延迟开浏览器（等 uvicorn 真正 listen 完）。`GACHABENCH_NO_BROWSER=1` 可关。"""
    if os.environ.get("GACHABENCH_NO_BROWSER"):
        print(f"[init] 已按要求跳过自动开浏览器：{url}")
        return

    def _go() -> None:
        time.sleep(delay)
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception as e:  # noqa: BLE001 - 开不了浏览器不算故障
            print(f"[init] 自动打开浏览器失败（{e}）：请手动访问 {url}")

    threading.Thread(target=_go, daemon=True, name="gb-open-browser").start()


def fix_streams() -> None:
    """`--noconsole` 构建（pythonw 式）里 sys.stdout/stderr 是 None。

    uvicorn 的默认日志配置会往 sys.stdout 写，不兜住就是启动即崩。把两者接到
    DATA_DIR 下的日志文件上（也能当「卡住了看看日志」的入口）。
    """
    if not IS_FROZEN:
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        log = open(DATA_DIR / "gachabench.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        log = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log
