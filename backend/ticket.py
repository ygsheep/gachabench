# -*- coding: utf-8 -*-
"""ticket：定点改动工单生成（SPEC §7.6，方案 1）。输出纯文本一键复制。"""
from __future__ import annotations

from config import get_config


def _tc(sec: float) -> str:
    """00:02.500 格式。"""
    ms = max(0, int(round(sec * 1000)))
    s = ms // 1000
    r = ms % 1000
    return f"00:{s:02d}.{r:03d}"


def _shot_end(block: dict, next_block) -> str:
    dur = block.get("duration")
    at = block.get("at_sec")
    if at is None:
        at = 0.0
    if dur:
        return _tc(at + dur)
    if next_block and next_block.get("at_sec") is not None:
        return _tc(next_block["at_sec"])
    return _tc(at)


def build_ticket(cfg, locate_result: dict, comment: str = "") -> str:
    """locate_result 来自 prompt_locator.locate_segment。hits 已按镜号升序。"""
    ws = cfg.active or "（未激活工作区）"
    file_abs = locate_result["file_abs"]
    seg = locate_result["seg"]
    hits = locate_result.get("hits", [])

    # 时间码区间：末镜用下一块不存在时取时长（或 at + 默认 0.8）
    lines = [
        "【GachaBench 定点改动工单】",
        f"工作区：{ws}",
        f"文件：{file_abs}",
    ]

    for i, b in enumerate(hits):
        nxt = hits[i + 1] if i + 1 < len(hits) else None
        at_sec = b.get("at_sec") if b.get("at_sec") is not None else 0.0
        tc = f"{_tc(at_sec)}–{_shot_end(b, nxt)}"
        dur = b.get("duration")
        dur_s = f"{dur:g}s" if dur else "—"
        framing = b.get("framing") or "—"
        camera = b.get("camera") or "—"
        lines += [
            "",
            f"位置：§④ 代码块 · [Shot {b['index']}]（文件第 {b['start_line']} 行）",
            f"镜号：SH{int(seg)}{b['index']:02d} ｜ 时间码 {tc} ｜ 时长 {dur_s} ｜ 景别 {framing} ｜ 机位 {camera}",
            "原文：",
            b["raw"],
        ]

    lines += [
        f"你的意见：{comment.strip() or '（请补充）'}",
        "要求：只改这一段；保持 <Picture N>/<Subject N>/<Audio N> 标签体系与编号顺序不变；不改其它镜；改完备份原文件。",
    ]
    return "\n".join(lines)
