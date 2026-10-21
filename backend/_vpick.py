# -*- coding: utf-8 -*-
"""文件夹选择接口的契约验证。

弹窗本身不能自动化（要人点），所以这里把 `folder_pick.pick_folder` 换成 stub，
验端点这一层：返回结构、取消、并发拒绝、超时、能力缺失，以及
**picker 吐出来的 Windows 反斜杠路径能不能被工作区接口直接用**。
"""
import sys

sys.path.insert(0, ".")

from fastapi.testclient import TestClient

import folder_pick
from main import app

ok_n = 0
fail: list[str] = []


def check(name, got, want):
    global ok_n
    if got == want:
        ok_n += 1
        print("  ✓ %-42s %s" % (name, got))
    else:
        fail.append(name)
        print("  ✗ %-42s got=%r want=%r" % (name, got, want))


orig = folder_pick.pick_folder

with TestClient(app) as c:
    print("1) 能力探测")
    r = c.get("/api/system/folder-picker")
    check("status", r.status_code, 200)
    check("可用", r.json()["available"], True)
    check("实现 = pwsh（本机装了 PowerShell 7）", r.json()["backend"], "pwsh")

    print("\n2) 选到目录（stub 返回 Windows 反斜杠路径）")
    seen: dict = {}

    def stub(title, initial="", timeout=0):
        seen["title"] = title
        seen["initial"] = initial
        return {"path": r"D:\Notely\workspace\AIGC", "cancelled": False, "backend": "pwsh"}

    folder_pick.pick_folder = stub
    r = c.post("/api/system/pick-folder",
               json={"title": "选择父目录", "initial": "D:/Notely"})
    check("status", r.status_code, 200)
    check("path 原样返回（反斜杠不转换）", r.json()["path"], r"D:\Notely\workspace\AIGC")
    check("cancelled", r.json()["cancelled"], False)
    check("title 传到了 picker", seen["title"], "选择父目录")
    check("initial 传到了 picker", seen["initial"], "D:/Notely")

    print("\n3) 用户取消")
    folder_pick.pick_folder = lambda *a, **k: {
        "path": None, "cancelled": True, "backend": "pwsh"}
    r = c.post("/api/system/pick-folder", json={"title": "选择项目目录"})
    check("status", r.status_code, 200)
    check("path = null", r.json()["path"], None)
    check("cancelled = true", r.json()["cancelled"], True)

    print("\n4) title 缺省时给默认值（前端不传也不会空标题）")
    seen.clear()

    def stub2(title, initial="", timeout=0):
        seen["title"] = title
        return {"path": None, "cancelled": True, "backend": "pwsh"}

    folder_pick.pick_folder = stub2
    r = c.post("/api/system/pick-folder", json={})
    check("status", r.status_code, 200)
    check("默认标题非空", bool(seen["title"].strip()), True)

    print("\n5) 已经有一个对话框在等 → 409（不弹第二个模态窗）")
    folder_pick.pick_folder = orig
    folder_pick._LOCK.acquire()
    try:
        r = c.post("/api/system/pick-folder", json={})
        check("status", r.status_code, 409)
        check("文案说清楚了", "在等" in r.json()["detail"] or "文件夹选择框" in r.json()["detail"], True)
        print("       detail =", r.json()["detail"])
    finally:
        folder_pick._LOCK.release()

    print("\n6) 锁释放后又能正常调用（没有卡死）")
    folder_pick.pick_folder = stub
    r = c.post("/api/system/pick-folder", json={})
    check("status", r.status_code, 200)

    print("\n7) 超时 / 无可用实现 的映射")
    def boom_to(*a, **k):
        raise folder_pick.FolderPickerTimeout("等待超过 300 秒")

    def boom_un(*a, **k):
        raise folder_pick.FolderPickerUnavailable("本机既没有 PowerShell，也用不了 tkinter")

    folder_pick.pick_folder = boom_to
    check("超时 → 504", c.post("/api/system/pick-folder", json={}).status_code, 504)
    folder_pick.pick_folder = boom_un
    check("无实现 → 503", c.post("/api/system/pick-folder", json={}).status_code, 503)

    folder_pick.pick_folder = orig

    print("\n8) picker 吐出的反斜杠路径能被工作区接口直接用（只读 inspect）")
    r = c.post("/api/workspaces/inspect", json={"root": r"D:\Notely\workspace\AIGC"})
    check("status", r.status_code, 200)
    check("目录存在", r.json()["exists"], True)
    check("读出子目录", len(r.json()["subdirs"]) > 0, True)

    r2 = c.post("/api/workspaces/inspect", json={"root": "D:/Notely/workspace/AIGC"})
    check("正斜杠也一样认", r2.json()["exists"], True)

print("\n────────────────────────────")
print("通过 %d 项，失败 %d 项" % (ok_n, len(fail)))
if fail:
    print("失败：", fail)
sys.exit(1 if fail else 0)
