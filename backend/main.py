# -*- coding: utf-8 -*-
"""GachaBench 后端入口：FastAPI + 挂路由 + 挂静态 + uvicorn 启动。"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from config import BACKEND_DIR, PROJECT_ROOT, get_config
from runtime import DIST_DIR, IS_FROZEN, CONFIG_PATH, open_browser_later, pick_port, seed_config, fix_streams

# 允许在 `python backend/main.py` 与 `uvicorn main:app` 两种方式下都能 import 模块
sys.path.insert(0, str(BACKEND_DIR))

def _sweep_stale_jobs() -> None:
    """启动扫尾：库里还挂着 queued/running 的任务都是僵尸。

    进程内的跑片协程随重启一起消失了，这些记录再也不会被更新 —— 留着会让顶部的
    任务气泡永久显示「N 个任务进行中」，还一直每 2.5 秒轮询。统一落成「已中断」。
    （ComfyUI 那边可能还在生成，但已经没有进程去收产物了，所以这里是诚实的说法。）
    """
    from db import get_db
    from repo import mark_stale_jobs

    cfg = get_config()
    n = 0
    for ws in cfg.workspaces:
        root = Path(ws.root)
        # 从没建过索引的工作区不要动它（get_db 会顺手把目录建出来）
        if not (root / cfg.index_dir / "cards.db").is_file():
            continue
        try:
            n += mark_stale_jobs(get_db(root), ws.name)
        except Exception as e:  # 扫尾失败不该拦住服务启动
            print(f"[startup] 清理「{ws.name}」的中断任务失败：{e}")
    if n:
        print(f"[startup] 已把 {n} 条中断的任务标为「已中断」")


async def _reconcile_once(tag: str) -> None:
    """补收一次：把 ComfyUI 已经跑完、本地却没入库的产物捞进草稿。

    永不抛错 —— 补收是兜底，不能反过来把服务弄挂。整体有超时，启动时也不卡太久。
    """
    import reconcile

    try:
        stats = await asyncio.wait_for(reconcile.reconcile_all(), timeout=40)
    except asyncio.TimeoutError:
        print(f"[{tag}] 补收超时（>40s），剩下的交给周期补收")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[{tag}] 补收失败：{e}")
        return
    line = reconcile.format_stats(stats)
    if line:
        print(f"[{tag}] {line}")


async def _reconcile_loop() -> None:
    """周期补收：兜住「轮询协程早没了、进程却还活着」的情况（例如轮询 60min 超时）。"""
    import reconcile

    while True:
        try:
            await asyncio.sleep(reconcile.RECONCILE_INTERVAL)
            await _reconcile_once("补收")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"[补收] 周期任务异常：{e}")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    _sweep_stale_jobs()
    # 顺序很重要：先把僵尸任务落成「已中断」，补收才知道该去查哪些 prompt_id
    await _reconcile_once("startup")
    task = asyncio.create_task(_reconcile_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


app = FastAPI(title="GachaBench", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# P0：健康检查 + 环境探测
# ---------------------------------------------------------------------------

def _ffmpeg_ok() -> bool:
    cfg = get_config()
    for cand in (cfg.ffmpeg.binary, "ffmpeg"):
        if shutil.which(cand):
            return True
    return False


def _comfy_ok() -> bool:
    """仅静态探测：配置里给了 base_url 即可（连通性由 /api/health 调用方决定）。"""
    return bool(get_config().comfyui.base_url)


@app.get("/api/health")
async def health():
    cfg = get_config()
    return JSONResponse(
        {
            "ok": True,
            "service": "gachabench-backend",
            "version": "0.1.0",
            "time": time.time(),
            "ffmpeg": _ffmpeg_ok(),
            "comfyui": {"base_url": cfg.comfyui.base_url, "configured": _comfy_ok()},
            "active_workspace": cfg.active,
        }
    )


# ---------------------------------------------------------------------------
# 路由挂载（各阶段逐步接入）
# ---------------------------------------------------------------------------

def _include_routers() -> None:
    from routers import (
        workspace, media, cards, split, generate, agent, locate, ffmpeg, workflows, assets,
        system, config_api, export, library, search,
    )

    for router in (
        workspace.router,
        media.router,
        media.input_router,
        cards.router,
        split.router,
        split.router2,
        generate.router,
        generate.jobs_router,
        agent.router,
        locate.router,
        ffmpeg.router,
        workflows.router,
        assets.router,
        system.router,
        config_api.router,
        export.router,
        library.router,
        search.router,
    ):
        app.include_router(router)


# 挂载业务路由（必须先于静态 mount，否则 '/api/*' 会被 SPA fallback 吞掉）
_include_routers()

# 静态托管 web/dist（生产模式；未构建时忽略）
_DIST = DIST_DIR
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="web")


def main() -> None:
    fix_streams()          # --noconsole 构建下先把 stdout/stderr 接上，否则 uvicorn 起不来
    seed_config()          # 冻结运行时：首次启动把随包的默认配置落到可写目录

    preferred = int(os.environ.get("GACHABENCH_PORT", "8000"))
    # 开发态锁死 8000（vite 的 proxy 写死指向它）；打包态端口被占就自动后移，
    # 双击 exe 的用户不该因为别的服务先占了 8000 就一脸黑屏。
    port = pick_port(preferred) if IS_FROZEN else preferred
    url = f"http://127.0.0.1:{port}"

    if port != preferred:
        print(f"[warn] 端口 {preferred} 已被占用，改用 {port}")
    print(f"GachaBench backend -> {url}")
    if not _DIST.is_dir():
        print("[warn] 没找到 web/dist —— 界面会 404。源码运行请先 `npm run build`。")
    if IS_FROZEN:
        print(f"配置文件 -> {CONFIG_PATH}")
        print("关闭这个窗口即退出服务。")
        open_browser_later(url)
        # 冻结环境下必须直接传 app 对象：`reload=True` 要重新导入模块，打包后必然失败。
        uvicorn.run(app, host="127.0.0.1", port=port)
    else:
        uvicorn.run("main:app", host="127.0.0.1", port=port, reload=True)


if __name__ == "__main__":
    # 打包后由 PyInstaller 的 bootloader 拉起时，任何子进程都必须走 freeze_support()
    import multiprocessing

    multiprocessing.freeze_support()
    main()
