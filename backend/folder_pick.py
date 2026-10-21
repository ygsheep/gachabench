# -*- coding: utf-8 -*-
"""弹系统原生的「选择文件夹」对话框，把绝对路径拿回来。

为什么必须由后端来弹：浏览器里的 `showDirectoryPicker()` 出于安全只交出
`FileSystemDirectoryHandle`，**给不了绝对路径**，而后端要的正是绝对路径。
后端就跑在本机、和用户在同一个桌面会话里，所以它能弹真正的系统对话框。

两档实现（`available()` 会告诉前端当前是哪一档）：
- `pwsh`：PowerShell 7+ 跑 `tools/pick_folder.ps1` → `IFileOpenDialog`
  (FOS_PICKFOLDERS)，也就是 Win10/11 资源管理器里那个现代「选择文件夹」。
- `tkinter`：回退 `filedialog.askdirectory`，走的是上世纪的 SHBrowseForFolder
  老式树形框 —— 能用，但难看。留着只为「没装 pwsh 的机器不至于完全没法选」。

`pick_folder()` 是**同步阻塞**的（对话框是模态的），路由侧务必用线程池调用。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading

import runtime

# 走 runtime：打包成 exe 后脚本在解包目录里，不再和本文件同级。
# 源码运行时 = backend/tools/pick_folder.ps1，与原来一致。
_SCRIPT = runtime.resource("tools", "pick_folder.ps1")

# 同一时刻只允许一个对话框：连点两次不该弹出两个模态窗
_LOCK = threading.Lock()

# 对话框是模态的，请求会一直挂到用户选完/取消 —— 这个超时只是兜底，
# 防线程被永久占用（HTTP 请求也一并挂着，别设太长）。
DEFAULT_TIMEOUT = 300.0


class FolderPickerBusy(RuntimeError):
    """已经有一个对话框在等用户操作。"""


class FolderPickerUnavailable(RuntimeError):
    """本机没有任何可用的文件夹选择实现。"""


class FolderPickerTimeout(RuntimeError):
    """等用户操作超时。"""


def _find_shell() -> str | None:
    """优先 PowerShell 7+（现代对话框），退回 Windows PowerShell 5.1。"""
    for name in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def _tkinter_ok() -> bool:
    try:
        import tkinter  # noqa: F401
    except Exception:
        return False
    return True


def available() -> dict:
    """能力探测 —— 前端据此决定是否显示「浏览…」按钮，免得换台机器出现死按钮。"""
    if _find_shell() and _SCRIPT.is_file():
        return {"available": True, "backend": "pwsh"}
    if _tkinter_ok():
        return {"available": True, "backend": "tkinter"}
    return {"available": False, "backend": None}


def pick_folder(title: str = "选择文件夹", initial: str = "",
                timeout: float = DEFAULT_TIMEOUT) -> dict:
    """弹出选择框，返回 {"path": str|None, "cancelled": bool, "backend": str}。

    路径保持 Windows 原生反斜杠（用户选出来是什么就显示什么）。
    """
    if not _LOCK.acquire(blocking=False):
        raise FolderPickerBusy("已经有一个文件夹选择框在等操作了")

    try:
        exe = _find_shell()
        if exe and _SCRIPT.is_file():
            return _pick_ps(exe, title, initial, timeout)
        return _pick_tk(title, initial)
    finally:
        _LOCK.release()


def _pick_ps(exe: str, title: str, initial: str, timeout: float) -> dict:
    with tempfile.TemporaryDirectory(prefix="gb_pick_") as td:
        out = Path(td) / "result.txt"
        args = [
            exe,
            "-NoProfile",
            "-NonInteractive",
            "-STA",              # 模态对话框必须在 STA 线程上
            "-ExecutionPolicy", "Bypass",
            "-File", str(_SCRIPT),
            "-Title", title,
            "-OutFile", str(out),
        ]
        if initial:
            args += ["-Initial", initial]

        try:
            proc = subprocess.run(args, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise FolderPickerTimeout(f"等待超过 {int(timeout)} 秒，对话框已放弃") from None

        if not out.is_file():
            detail = (proc.stderr or b"").decode("utf-8", "replace").strip()
            raise FolderPickerUnavailable(
                "文件夹选择器没有返回结果"
                + (f"：{detail.splitlines()[-1]}" if detail else f"（退出码 {proc.returncode}）")
            )

        # 脚本用 UTF-8 无 BOM 写；utf-8-sig 同时兼容带 BOM 的情况
        line = out.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        first = (line[0] if line else "").strip()

    if first == "CANCEL":
        return {"path": None, "cancelled": True, "backend": "pwsh"}
    if first.startswith("ERR\t") or first.startswith("ERR "):
        raise FolderPickerUnavailable(first.split(None, 1)[1] if " " in first or "\t" in first
                                      else "文件夹选择器出错")
    if first.startswith("OK\t"):
        return {"path": first[3:].strip(), "cancelled": False, "backend": "pwsh"}
    raise FolderPickerUnavailable(f"文件夹选择器返回了看不懂的结果：{first[:120]}")


def _pick_tk(title: str, initial: str) -> dict:
    """回退实现：老的 SHBrowseForFolder 树形框。"""
    try:
        import tkinter
        from tkinter import filedialog
    except Exception as e:  # pragma: no cover - 取决于机器
        raise FolderPickerUnavailable("本机既没有 PowerShell，也用不了 tkinter") from e

    root = tkinter.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)  # 否则可能被浏览器窗口压在后面
    except Exception:
        pass
    try:
        path = filedialog.askdirectory(
            title=title,
            initialdir=initial or None,
            mustexist=False,
        )
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    return {"path": path or None, "cancelled": not path, "backend": "tkinter"}
