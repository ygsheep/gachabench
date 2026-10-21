# -*- coding: utf-8 -*-
"""prompt_locator：分镜标签 → [Shot N] 块定位 + 行号区间。SPEC §7.5。

标签解析：
- `SH402`（剧镜号）↔ `[Shot 2]`：SH + 段号后两位 + 镜序两位；
- `#13` / `#13-#14`（剧本台词编号）：从 §⑥ 表格解析后映射到镜块；
- 支持混写（`SH402,#13-#14`），逗号 / 顿号 / 空格分隔。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from splitter import find_segment_md, parse_shot_blocks, _at_to_sec

SHOT_RE = re.compile(r"^\s*\[Shot (\d+)\]")
AT_RE = re.compile(r"At 00:(\d+)\.(\d+)")
# §③bis 镜表行：| SH401 | 2.5s | MS | 过肩位… | 主体 | … | 台词 |
TABLE_ROW_RE = re.compile(
    r"^\|\s*(SH\d{3})\s*\|\s*([\d.]+)s\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|",
    re.MULTILINE,
)
# §⑥ 台词处理行：| 台词 | 原文 | 拆为 SH401 + SH402 两镜… | 原因 |
SPLIT_ROW_RE = re.compile(r"^\|\s*台词\s*\|", re.MULTILINE)
SPLIT_SHOTS_RE = re.compile(r"SH(\d{3})\s*\+\s*SH(\d{3})")
# §⑥ 台词范围行：| 台词范围 | 原剧本 #13–#28 … | 本段只取 **#13–#14** … |
DIALOG_RANGE_RE = re.compile(r"#\s*(\d+)\s*[–—-]\s*#?\s*(\d+)")


# ---------------------------------------------------------------------------
# 行号与镜块定位
# ---------------------------------------------------------------------------

def locate_shot_blocks(text: str, seg_no: int) -> list[dict]:
    """返回 §④ ```text 内 [Shot N] 块的绝对行号区间（1-based）。

    block = {index, start_line, end_line, raw, head, at_sec, duration, framing, camera}
    """
    lines = text.splitlines()

    # 1. 定位 §④ 代码块起止（第一个 ```text）
    fence_start = None
    fence_end = None
    for i, line in enumerate(lines):
        if "```text" in line and fence_start is None:
            fence_start = i + 1  # 内容从下一行开始
            continue
        if fence_start is not None and line.strip().startswith("```"):
            fence_end = i  # 内容到这一行前结束
            break
    if fence_start is None or fence_end is None:
        return []

    # 2. §③bis 镜表：时长 / 景别 / 机位
    table: dict[int, dict] = {}
    for m in TABLE_ROW_RE.finditer(text):
        sh = int(m.group(1)[2:]) % 100  # SH401 → 01 → 1
        table[sh] = {
            "duration": float(m.group(2)),
            "framing": m.group(3).strip(),
            "camera": m.group(4).strip(),
        }

    # 3. 在代码块内切 [Shot N] 块
    blocks: list[dict] = []
    starts: list[tuple[int, int, int]] = []  # (绝对行号, 镜号, 时间毫秒)
    for i in range(fence_start, fence_end):
        m = SHOT_RE.match(lines[i])
        if m:
            n = int(m.group(1))
            at_m = AT_RE.search(lines[i])
            at_sec = _at_to_sec(at_m.group(1), at_m.group(2)) if at_m else None
            starts.append((i, n, at_sec))
    for k, (ln, n, at_sec) in enumerate(starts):
        end_ln = starts[k + 1][0] if k + 1 < len(starts) else fence_end
        raw = "\n".join(lines[ln:end_ln]).rstrip()
        meta = table.get(n, {})
        blocks.append({
            "index": n,
            "start_line": ln,       # 1-based 绝对行号
            "end_line": end_ln - 1 if end_ln - 1 >= ln else ln,
            "raw": raw,
            "head": lines[ln].strip(),
            "at_sec": at_sec,
            "duration": meta.get("duration"),
            "framing": meta.get("framing", ""),
            "camera": meta.get("camera", ""),
        })
    return blocks


# ---------------------------------------------------------------------------
# 台词编号 → 镜块映射（§⑥）
# ---------------------------------------------------------------------------

def parse_dialogue_shot_map(text: str) -> dict[int, list[int]]:
    """#台词编号 → [Shot N...]。取 §⑥ 「拆为 SH.. + SH..」行，按序配 #A–#B 递增。

    例：本段只取 #13–#14；两行拆镜 → {13: [1,2], 14: [3,4]}。
    """
    # 找台词范围起始
    span = DIALOG_RANGE_RE.search(text)
    if not span:
        return {}
    start_no = int(span.group(1))

    # §⑥ 表格中「拆为 SH.. + SH..」的台词行，按出现顺序
    mapping: dict[int, list[int]] = {}
    n = start_no
    for m in SPLIT_SHOTS_RE.finditer(text):
        row_start = max(0, text.rfind("\n", 0, m.start()) + 1)
        row = text[row_start:m.end()].strip()
        if not row.startswith("| 台词"):
            continue  # 只认「台词」行（排除「台词范围」行）
        shots = [int(m.group(1)[1:]) % 100, int(m.group(2)[1:]) % 100]
        mapping[n] = shots
        n += 1
    return mapping


# ---------------------------------------------------------------------------
# 标签解析
# ---------------------------------------------------------------------------

SH_TAG_RE = re.compile(r"SH(\d{3,4})", re.IGNORECASE)
DIALOG_TAG_RE = re.compile(r"#(\d+)(?:\s*[–—-]\s*#?\s*(\d+))?")


def parse_tags(tags: str) -> dict:
    """解析逗号/顿号/空格分隔的标签列表，返回 {shots:[镜号], dialogue:[台词编号], raw:[未识别]}。"""
    result = {"shots": [], "dialogue": [], "raw": []}
    if not tags:
        return result
    parts = re.split(r"[,，、;\s]+", tags.strip())
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = SH_TAG_RE.fullmatch(p)
        if m:
            result["shots"].append(int(m.group(1)[1:]) % 100)  # SH402 → 02 → 2
            continue
        m = DIALOG_TAG_RE.fullmatch(p)
        if m:
            lo = int(m.group(1))
            hi = int(m.group(2)) if m.group(2) else lo
            result["dialogue"].extend(range(lo, hi + 1))
            continue
        result["raw"].append(p)
    return result


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def locate_segment(cfg, seg: str, tags: str) -> dict:
    """定位入口：返回命中镜块（含行号区间与原文）与标签解析结果。"""
    seg_no = int(seg)
    md = find_segment_md(cfg, seg)
    if md is None:
        raise ValueError(f"未找到 {seg} 段提示词 md")
    text = md.read_text(encoding="utf-8")
    blocks = locate_shot_blocks(text, seg_no)
    if not blocks:
        raise ValueError(f"{md.name} 的 §④ 代码块中未找到 [Shot N] 块")

    parsed = parse_tags(tags)
    # SH 镜号
    shot_hits = {b["index"]: b for b in blocks}
    # 台词编号 → 镜
    dmap = parse_dialogue_shot_map(text)
    for d in parsed["dialogue"]:
        for s in dmap.get(d, []):
            if s in shot_hits:
                parsed["shots"].append(s)
    parsed["shots"] = sorted(set(parsed["shots"]))

    hits = [shot_hits[s] for s in parsed["shots"] if s in shot_hits]
    missing = [s for s in parsed["shots"] if s not in shot_hits]

    return {
        "seg": seg,
        "file": str(md.relative_to(cfg.ws_root())),
        "file_abs": str(md),
        "parsed": parsed,
        "dialogue_map": {str(k): v for k, v in dmap.items()},
        "hits": hits,
        "missing": missing,
    }
