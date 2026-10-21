# -*- mode: python ; coding: utf-8 -*-
"""GachaBench · PyInstaller 打包规格。

打包对象是 **Python 后端 sidecar**（给 Tauri 壳拉起的那个进程），不是最终 exe ——
最终产物是 `desktop/` 下的 Tauri 应用，它把这里的 onedir 整个当资源打进去。

形态固定为 onedir + 无控制台：
  - onedir：Tauri 的 `bundle.resources` 要的是一个目录，onefile 单文件反而难搬
  - 无控制台：sidecar 不该在用户面前弹一个黑窗口；stdout 由壳管道接走

请由 `python packaging/build.py` 驱动，它负责：
  ① npm run build（web/dist）
  ② 把 ffmpeg 暂存到 build/stage-ffmpeg/ 并设好 GB_FFMPEG_SRC
  ③ 跑本 spec
  ④ 把产物搬进 desktop/src-tauri/backend/ 并调 tauri build
"""
import os
from pathlib import Path

PACKAGING = Path(SPECPATH).resolve()
PROJECT = PACKAGING.parent
BACKEND = PROJECT / "backend"
WEB_DIST = PROJECT / "web" / "dist"
FFMPEG_SRC = Path(os.environ["GB_FFMPEG_SRC"]) if os.environ.get("GB_FFMPEG_SRC") else None

if not (WEB_DIST / "index.html").is_file():
    raise SystemExit(
        f"[spec] 前端产物不存在：{WEB_DIST / 'index.html'}\n"
        "      先 `cd web && npm run build`（build.py 会自动做这件事）。"
    )

# --------------------------------------------------------------------------- 随包数据
datas = [
    # 前端构建产物 —— 运行时被 StaticFiles 挂在 "/" 上，所以 dest 必须是 web/dist
    (str(WEB_DIST), "web/dist"),
    # 文件夹选择器的 PowerShell 脚本（folder_pick.py 经 runtime.resource() 取）
    (str(BACKEND / "tools" / "pick_folder.ps1"), "tools"),
    # 首次运行时播种成 <exe 同目录>/config.json，保留当前工作区配置
    (str(BACKEND / "config.json"), "seed"),
]

# ffmpeg / ffprobe 及其依赖的 DLL 一起进包，落到 _internal/ffmpeg/
# runtime.py 的 bundled_tool() 会优先用它 —— 用户机器上没装 ffmpeg 也能跑。
if FFMPEG_SRC is not None and FFMPEG_SRC.is_dir():
    datas.append((str(FFMPEG_SRC), "ffmpeg"))
    print(f"[spec] 内置 ffmpeg：{FFMPEG_SRC}")
else:
    print("[spec] 未提供 GB_FFMPEG_SRC，本包不带 ffmpeg（运行机需自备）")

# --------------------------------------------------------------------------- 隐式导入
# 本项目自己的模块：main.py 里是函数内 import，静态分析不一定认全，逐个点名更稳。
own = [
    "config", "runtime", "db", "repo", "comfy", "ingest", "reconcile",
    "splitter", "ticket", "workspaces", "prompt_locator", "ffmpeg_tools", "folder_pick",
]
own += [
    "routers." + p.stem
    for p in sorted((BACKEND / "routers").glob("*.py"))
    if p.stem != "__init__"
]

# 第三方：uvicorn 的 loop / protocol / lifespan 全是**按名字动态 import** 的，
# 漏一个就是「打包成功、启动即崩」，所以这些必须显式列出。
third = [
    "uvicorn.logging",
    "uvicorn.loops", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan", "uvicorn.lifespan.on", "uvicorn.lifespan.off",
    "anyio._backends._asyncio",
    "multipart", "python_multipart",   # FastAPI 的表单解析（运行时按需 import）
    "httptools", "websockets", "watchfiles",
]

# --------------------------------------------------------------------------- 排除
excludes = [
    "tkinter",   # 只在 folder_pick 的退路里出现，Windows 必有 powershell.exe
    "matplotlib", "numpy", "pandas", "scipy", "PIL", "IPython",
    "pytest", "uvloop",
]

a = Analysis(
    [str(BACKEND / "main.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=own + third,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# sidecar 不弹控制台窗口：stdout/stderr 由 Tauri 壳管道接走并写日志。
# 万一壳没给管道（直接手跑），runtime.fix_streams() 会把输出兜到日志文件。
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GachaBench-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # UPX 压缩极易被国产杀软误报，关掉
    console=False,
    disable_windowed_traceback=False,
    icon=str(PACKAGING / "icon.ico") if (PACKAGING / "icon.ico").is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="GachaBench-backend",
)
