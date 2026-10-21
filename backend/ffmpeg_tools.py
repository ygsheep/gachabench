# -*- coding: utf-8 -*-
"""ffmpeg_tools：探测、抽末帧、拼接。ffmpeg/ffprobe 走 subprocess，不引 wrapper。"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import runtime


def find_binary(cfg, name: str) -> Optional[str]:
    """解析优先级：随包的那份 → config 里配的 → PATH。

    随包优先是为了「打包版在没装 ffmpeg 的机器上也能跑」；源码运行时没有
    `_internal/ffmpeg/`，`bundled_tool()` 直接返回 None，行为与改造前一致。
    """
    if name in ("ffmpeg", "ffprobe"):
        bundled = runtime.bundled_tool(name)
        if bundled:
            return bundled
        configured = cfg.ffmpeg.binary if name == "ffmpeg" else cfg.ffmpeg.probe_binary
        if configured and shutil.which(configured):
            return configured
        return shutil.which(name)
    return shutil.which(name) or None


def _run_sync(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def ffprobe_available(cfg) -> bool:
    return find_binary(cfg, "ffprobe") is not None


def ffmpeg_available(cfg) -> bool:
    return find_binary(cfg, "ffmpeg") is not None


def probe_media(cfg, abs_path: Path) -> dict:
    """ffprobe 返回值：{duration, width, height, has_audio}；失败返回空 dict。"""
    probe = find_binary(cfg, "ffprobe")
    if probe is None:
        return {}
    try:
        r = _run_sync([
            probe, "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", str(abs_path),
        ])
        data = json.loads(r.stdout.decode("utf-8", "replace"))
    except Exception:
        return {}
    info: dict = {}
    try:
        dur = data.get("format", {}).get("duration")
        if dur:
            info["duration"] = float(dur)
    except Exception:
        pass
    vstream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if vstream:
        info["width"] = vstream.get("width")
        info["height"] = vstream.get("height")
    info["has_audio"] = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    return info


async def extract_last_frame(cfg, mp4: Path, out_png: Path) -> Optional[Path]:
    """抽 mp4 末帧为 PNG：ffmpeg -sseof -0.05 -frames:v 1 -q:v 2。"""
    ffmpeg = find_binary(cfg, "ffmpeg")
    if ffmpeg is None:
        return None
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-sseof", "-0.05", "-i", str(mp4),
           "-frames:v", "1", "-q:v", "2", str(out_png)]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await proc.wait()
    return out_png if out_png.exists() else None


async def concat_videos(cfg, mp4_list: list[Path], out_path: Path) -> Optional[Path]:
    """拼接：优先 concat demuxer + -c copy（同编码），否则重编码。"""
    ffmpeg = find_binary(cfg, "ffmpeg")
    if ffmpeg is None or len(mp4_list) < 2:
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = out_path.with_suffix(".txt")
    lines = [f"file '{p.resolve().as_posix()}'" for p in mp4_list if p.exists()]
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 尝试 copy 拼接
    code = _run_in_loop([
        ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", str(out_path),
    ])
    if code == 0 and out_path.exists():
        return out_path
    # 回退重编码
    code2 = _run_in_loop([
        ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(out_path),
    ])
    if code2 == 0 and out_path.exists():
        return out_path
    return None


def _run_in_loop(cmd: list[str], timeout: int = 60) -> int:
    """在事件循环内跑同步 subprocess（不抢占当前 loop）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return subprocess.run(cmd, capture_output=True, timeout=timeout).returncode
    import threading
    import time as _time

    result = {}

    def _worker():
        try:
            result["code"] = subprocess.run(cmd, capture_output=True, timeout=timeout).returncode
        except Exception as e:  # noqa: BLE001
            result["err"] = e

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout + 5)
    if "code" in result:
        return result["code"]
    return -1