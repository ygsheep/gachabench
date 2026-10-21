# -*- coding: utf-8 -*-
"""文件夹选择器的运行时探针。

真的弹一次对话框（约 1.5 秒后自动关掉），用来回答三个问题：
  1. 弹得出来吗（pwsh 参数、STA、COM 调用这条链通不通）
  2. 弹的是**现代** IFileOpenDialog 还是**老式** SHBrowseForFolder 树形框
  3. 用户点取消时，结果是否正确回到 {"path": None, "cancelled": True}

判据：现代对话框的内容区是 DirectUI（子窗口类名含 DUIViewWndClassName）；
老式 SHBrowseForFolder 的核心是一个 SysTreeView32 目录树。

如果本环境处于 session 0（服务会话），窗口不会出现在交互桌面 → 枚举不到，
那只能说明"探针环境看不到窗口"，不能判定功能失败。
"""
import ctypes
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, ".")

import folder_pick

TITLE = "GB_PICK_PROBE_9281"

user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumProc, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [wintypes.HWND, EnumProc, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]


def _text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _cls(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def find_window(title_part: str) -> int:
    hits = []

    @EnumProc
    def cb(hwnd, _l):
        if not user32.IsWindowVisible(hwnd):
            return True
        if title_part in _text(hwnd):
            hits.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return hits[0] if hits else 0


def child_classes(hwnd: int) -> list[str]:
    out: list[str] = []

    @EnumProc
    def cb(child, _l):
        out.append(_cls(child))
        return True

    user32.EnumChildWindows(hwnd, cb, 0)
    return out


def close_window(hwnd: int) -> None:
    user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE


print("=== 0) 能力探测 ===")
print("   ", folder_pick.available())

result: dict = {}
err: list = []


def worker():
    try:
        result.update(folder_pick.pick_folder(title=TITLE, initial="D:/Notely", timeout=60))
    except Exception as e:  # noqa: BLE001
        err.append(f"{type(e).__name__}: {e}")


t = threading.Thread(target=worker, daemon=True)
t.start()

hwnd = 0
try:
    for _ in range(60):  # 最多等 6 秒
        time.sleep(0.1)
        hwnd = find_window(TITLE)
        if hwnd or err:
            break

    print("\n=== 1) 对话框是否出现 ===")
    if err:
        print("   调用失败：", err[0])
    elif hwnd:
        print("   窗口已出现 ✓  hwnd =", hwnd, "| 标题 =", _text(hwnd))
        print("   顶层类名 =", _cls(hwnd))
        kids = child_classes(hwnd)
        print("   子窗口类名（前 16 个）：")
        for k in kids[:16]:
            print("      ", k)
        modern = any("DUIViewWndClassName" in k or "ShellTabWindowClass" in k for k in kids)
        legacy = any("SysTreeView32" in k for k in kids)
        print("   → DirectUI 现代内容区：", modern)
        print("   → SysTreeView32 老式目录树：", legacy)
    else:
        print("   6 秒内没枚举到窗口 —— 可能是 session 0 / 无交互桌面，不能判定功能失败")

    if hwnd:
        print("\n=== 2) 发 WM_CLOSE 模拟用户取消 ===")
        close_window(hwnd)
        t.join(timeout=20)
        print("   线程结束 =", not t.is_alive())
        if err:
            print("   调用异常：", err[0])
        else:
            print("   返回 =", result)
            ok = result.get("cancelled") is True and result.get("path") is None
            print("   → 取消路径正确：", ok)
finally:
    # 兜底：任何情况下都别把模态窗口留在用户屏幕上
    left = find_window(TITLE)
    if left:
        close_window(left)
        time.sleep(0.5)
        print("   [兜底] 关闭了残留窗口", left)

print("\n=== 3) 临时结果文件是否清干净 ===")
import tempfile

left = [p.name for p in Path(tempfile.gettempdir()).glob("gb_pick_*")]
print("   残留 =", left or "无 ✓")
