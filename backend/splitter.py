# -*- coding: utf-8 -*-
"""splitter：15s → 3×5s 拆分（甲/乙）。SPEC §7.1 落地实现。

铁律：正文字词一字不改，只裁剪/重编号 [Shot N]/时间码重基/改抬头；
      <Picture N>/<Subject N>/<Audio N> 标签体系与编号顺序绝不动。
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from db import get_db

# 六节头
SECTION_HEADS = [
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
]

SHOT_RE = re.compile(r"^\s*\[Shot (\d+)\]")
AT_RE = re.compile(r"At 00:(\d+)\.(\d+)")
DUR_RE = re.compile(r"(\d+(?:\.\d+)?)s")


def find_segment_md(cfg, seg: str) -> Optional[Path]:
    """在 {prompt} 下找 NN_正片_第N段_15s.md。"""
    prompt_dir = cfg.subdir("prompt")
    if prompt_dir is None:
        return None
    # 文件名：04_正片_第4段_15s.md
    for md in prompt_dir.glob("*_正片_第*段_15s.md"):
        m = re.search(r"第(\d+)段", md.name)
        if m and m.group(1).zfill(2) == seg.zfill(2):
            return md
    # 兜底：NN 前缀
    for md in prompt_dir.glob(f"{seg.zfill(2)}_*"):
        if md.suffix.lower() == ".md":
            return md
    return None


# ---------------------------------------------------------------------------
# §④ 代码块解析
# ---------------------------------------------------------------------------

def _extract_fence(text: str) -> Optional[str]:
    """取 md 里第一个 ```text 代码块内容。"""
    m = re.search(r"```text\s*\n(.*?)```", text, re.DOTALL)
    return m.group(1) if m else None


def parse_codeblock_sections(text: str) -> dict[str, str]:
    """把代码块切成六节。返回 {head: content}，head 为六节名。"""
    block = _extract_fence(text)
    if block is None:
        return {}
    # 定位各节头行
    heads = []
    for i, line in enumerate(block.splitlines()):
        s = line.strip().rstrip(":")
        if s in SECTION_HEADS:
            heads.append((i, s))
    if not heads:
        return {}
    sections: dict[str, str] = {}
    lines = block.splitlines()
    for idx, (li, name) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        content = "\n".join(lines[li + 1:end]).rstrip("\n")
        sections[name] = content
    return sections


def parse_shot_blocks(text: str) -> list[dict]:
    """从 detailed_description 提取 [Shot N] 块，返回每镜信息。"""
    sections = parse_codeblock_sections(text)
    desc = sections.get("detailed_description", "")
    lines = desc.splitlines()
    blocks: list[dict] = []
    start = None
    for i, line in enumerate(lines):
        m = SHOT_RE.match(line)
        if m:
            if start is not None:
                blocks.append(_shot_block(lines[start:i]))
            start = i
    if start is not None:
        blocks.append(_shot_block(lines[start:]))
    return blocks


def _shot_block(lines: list[str]) -> dict:
    raw = "\n".join(lines)
    first = lines[0]
    n = SHOT_RE.match(first).group(1)  # type: ignore[union-attr]
    at = AT_RE.search(first)
    return {
        "index": int(n),
        "at_sec": _at_to_sec(at.group(1), at.group(2)) if at else 0.0,
        "has_at": at is not None,
        "raw": raw,
        "head": first,
        "body": "\n".join(lines[1:]),
    }


def _at_to_sec(mm: str, ss: str) -> float:
    return int(mm) + int(ss) / 1000.0


def _format_at(sec: float) -> str:
    """H3 时间码格式：At 00:{整数秒}.{毫秒}。如 At 00:02.500。"""
    total_ms = max(0, int(round(sec * 1000)))
    secs = total_ms // 1000
    ms = total_ms % 1000
    return f"At 00:{secs:02d}.{ms:03d}"


# ---------------------------------------------------------------------------
# §③bis 镜表解析（时长）
# ---------------------------------------------------------------------------

def parse_shot_table_durations(text: str) -> dict[int, float]:
    """解析 §③bis 表格：| SH401 | 2.5s | ... | → {1: 2.5}。"""
    result: dict[int, float] = {}
    for m in re.finditer(r"\|\s*(SH\d{3})\s*\|\s*(\d+(?:\.\d+)?)s", text):
        shot_no = int(m.group(1)[2:]) % 100  # SH401 → 01 → 1
        result[shot_no] = float(m.group(2))
    return result


# ---------------------------------------------------------------------------
# 切分主逻辑
# ---------------------------------------------------------------------------

def _group_shots(durations: list[float]) -> list[list[int]]:
    """按镜组自然边界累计，每累计满 5s 切一组；末组收尾（不裁尾）。04 → [1,2]/[3,4]/[5..8]。"""
    groups: list[list[int]] = []
    cur: list[int] = []
    cur_sum = 0.0
    for i, d in enumerate(durations):
        cur.append(i)
        cur_sum += d
        if cur_sum >= 5.0:
            groups.append(cur)
            cur = []
            cur_sum = 0.0
    if cur:
        groups.append(cur)
    return groups


def _rebuild_chunk(
    sections: dict[str, str],
    shots: list[dict],
    group: list[int],
    strategy: str,
    target_sec: float,
) -> str:
    """生成单块 txt。铁律：正文一字不改；只裁剪 + 重编号 + 时间码重基 + 改抬头。"""
    selected = [shots[i] for i in group]
    # 每块 [Shot k] 重新从 1 编号
    renum = {sel["index"]: k + 1 for k, sel in enumerate(selected)}

    # detailed_description：只留本块镜块 + 重编号 + 时间码重基
    desc_lines = []
    for k, sel in enumerate(selected):
        new_no = k + 1
        head = sel["head"]
        if sel["has_at"]:
            # 重基：把 At 时间码减去第一镜起始时间（乙再按比例缩放）
            rel = sel["at_sec"] - selected[0]["at_sec"]
            if strategy == "yi" and target_sec > 0:
                src_total = sum(
                    _shot_table_duration_for(shots, i) for i in group
                )
                ratio = target_sec / src_total if src_total > 0 else 1.0
                rel *= ratio
            new_head = re.sub(AT_RE, lambda m: _format_at(rel), head)
            new_head = SHOT_RE.sub(f"[Shot {new_no}]", new_head)
            desc_lines.append(new_head + "\n" + sel["body"])
        else:
            new_head = SHOT_RE.sub(f"[Shot {new_no}]", head)
            desc_lines.append(new_head + "\n" + sel["body"])
    desc = "\n\n".join(desc_lines)

    # retention_analysis：只留本块出现的 <Subject N> / <Audio N> 行，[Shot N] 重编号
    ret_lines = []
    for line in sections.get("retention_analysis", "").splitlines():
        if "<Subject" not in line and "<Audio" not in line:
            continue
        # 本镜组涉及的行才保留：按 [Shot N] 引用判断
        refs = re.findall(r"\[Shot (\d+)\]", line)
        if any(int(r) in renum for r in refs) or (not refs and "<Subject" in line):
            new_line = line
            for old, new in renum.items():
                new_line = re.sub(
                    rf"\[Shot {old}\]", f"[Shot {new}]", new_line
                )
            ret_lines.append(new_line)
    ret = "\n".join(ret_lines)

    # subject_definitions / overall_soundscape / non_diegetic_music：原样保留
    subj = sections.get("subject_definitions", "")
    snd = sections.get("overall_soundscape", "")
    music = sections.get("non_diegetic_music", "")

    # summary：按本块镜内容重写一句 + 保留抬头口径
    total_sec = target_sec if strategy == "yi" else sum(
        _shot_table_duration_for(shots, i) for i in group
    )
    summary = (
        f"Summary: This block contains {len(selected)} shot(s) "
        f"([Shot 1] through [Shot {len(selected)}]) with a total runtime of "
        f"{total_sec:.1f} seconds, presenting the {strategy}-split segment "
        f"{len(group)} of the original 15-second prompt. "
        "[reference generation] following the same characters, room, style "
        "and audio timbre as the source prompt, with the label numbering of "
        "<Picture>/<Subject>/<Audio> unchanged."
    )

    # 抬头（元信息行，不属于原文正文）：镜号用源 md 里的编号，别拿数组下标糊弄人
    shot_nos = [shots[i]["index"] for i in group]
    header = (
        f"时长 {total_sec:.1f} 秒｜{len(selected)} 镜｜带音频\n\n"
        f"（GachaBench 拆分 · 策略{'甲' if strategy == 'jia' else '乙'} · "
        f"镜 {'、'.join(map(str, shot_nos))} ｜ 由源 15s 提示词裁剪，正文用词一字未改）"
    )

    parts = [
        header,
        f"subject_definitions:\n{subj}" if subj else "",
        f"summary:\n{summary}" if summary else "",
        f"retention_analysis:\n{ret}" if ret else "",
        f"detailed_description:\n{desc}" if desc else "",
        f"overall_soundscape:\n{snd}" if snd else "",
        f"non_diegetic_music:\n{music}" if music else "",
    ]
    return "\n\n".join(p for p in parts if p) + "\n"


def _shot_table_duration_for(shots: list[dict], i: int) -> float:
    """回退：用第一镜与下一镜的 At 差估算镜长。"""
    cur = shots[i]
    if i + 1 < len(shots):
        nxt = shots[i + 1]
        return max(0.1, nxt["at_sec"] - cur["at_sec"])
    return 1.0


def compose_segment(cfg, seg: str, strategy: str = "jia") -> dict:
    """按**当前源 md** 实时切块 —— 全流程唯一的切块入口，内容只活在内存里。

    这里一个 txt 都不写。源 md 是唯一真相源，改完立刻在预览与跑片里生效。
    以前的做法是把块正文切出来落成 txt、跑片再去读那个 txt，等于把提示词复制了一份：
    改 md 不会回流，屏幕上展示新版、实际跑旧版，只能靠人记着「改完要重新拆」。

    返回 {"seg","strategy","source_md","source_total_sec","chunks":[
        {"chunk","shots","src_dur","target_sec","len_shots","text"}]}
    """
    if strategy not in ("jia", "yi"):
        raise ValueError("strategy 必须为 jia 或 yi")
    md = find_segment_md(cfg, seg)
    if md is None:
        raise ValueError(f"未找到 {seg} 段提示词 md")
    text = md.read_text(encoding="utf-8")
    sections = parse_codeblock_sections(text)
    if not sections:
        raise ValueError(f"{md.name} 中未找到 §④ 代码块")
    shots = parse_shot_blocks(text)
    if len(shots) < 3:
        raise ValueError(f"{md.name} 中 [Shot] 块不足 3 个")

    # 时长表（§③bis）；没有镜表就按 At 差估算
    table_dur = parse_shot_table_durations(text)
    durations = [table_dur.get(s["index"], 0.0) for s in shots]
    if all(d == 0.0 for d in durations):
        durations = [_shot_table_duration_for(shots, i) for i in range(len(shots))]
    total = sum(durations)

    # 切组（甲乙同一套切组逻辑，只有目标时长策略不同）
    groups = _group_shots(durations)
    chunks: list[dict] = []
    for gi, g in enumerate(groups, start=1):
        grp_dur = sum(durations[i] for i in g)
        # 甲：请求时长 = ceil(组时长)；乙：定长 5s（组内等比缩放）
        target = float(max(1, math.ceil(grp_dur))) if strategy == "jia" else 5.0
        chunks.append({
            "chunk": f"{gi}of{len(groups)}",
            "shots": [shots[i]["index"] for i in g],
            "src_dur": round(grp_dur, 3),
            "target_sec": target,
            "len_shots": len(g),
            "text": _rebuild_chunk(sections, shots, g, strategy, target),
        })

    return {
        "seg": seg,
        "strategy": strategy,
        "source_md": str(md.relative_to(cfg.ws_root())),
        "source_total_sec": round(total, 3),
        "chunks": chunks,
    }


def find_chunk(composed: dict, chunk: str) -> Optional[dict]:
    """在实时切块结果里取一块；取不到说明镜结构变了（块名对不上）。"""
    return next((c for c in composed["chunks"] if c["chunk"] == chunk), None)


def ensure_plan(cfg, seg: str, strategy: str = "jia") -> dict:
    """确认「这段用这个策略」，返回 plan_id + manifest（不含正文）。

    计划只记策略与源 md：块内容每次现算。因此同一段反复确认不会堆一串计划，
    也不会留下和源 md 脱节的旧块。
    """
    composed = compose_segment(cfg, seg, strategy)
    root = cfg.ws_root()
    db = get_db(root)
    manifest = {
        "seg": seg,
        "strategy": strategy,
        "source_md": composed["source_md"],
        "source_total_sec": composed["source_total_sec"],
        # 快照仅供补收/历史查看；跑片与预览一律以 compose_segment 现算为准
        "chunks": [
            {k: v for k, v in c.items() if k != "text"} for c in composed["chunks"]
        ],
        "continuity": {"carry_last_frame": False},
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    payload = json.dumps(manifest, ensure_ascii=False)
    with db.lock:
        row = db.conn.execute(
            "SELECT id FROM split_plans WHERE workspace=? AND seg=? AND strategy=?"
            " ORDER BY id DESC LIMIT 1",
            (cfg.active, seg, strategy),
        ).fetchone()
        if row is not None:
            plan_id = row["id"]
            db.conn.execute(
                "UPDATE split_plans SET manifest_json=?, source_md=? WHERE id=?",
                (payload, composed["source_md"], plan_id),
            )
        else:
            cur = db.conn.execute(
                "INSERT INTO split_plans (workspace, seg, strategy, source_md, manifest_json, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (cfg.active, seg, strategy, composed["source_md"], payload,
                 manifest["created_at"]),
            )
            plan_id = cur.lastrowid
        db.conn.commit()
    return {"plan_id": plan_id, "manifest": manifest}


# 旧名字保留：老调用方/脚本仍可能用这个入口
split_segment = ensure_plan