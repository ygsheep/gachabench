# -*- coding: utf-8 -*-
"""分镜脚本解析：把「21 字段主表」读成结构化中间层。

三条原则：
1. 只读源 md，一个字节都不改 —— md 是真相，中间层只是投影。
2. 按列名建映射，不按列序猜。列名带空格、带 `(s)`、多一列未知列都不算错。
3. 解析不全就降级，不整段报错：缺列进 `issues`，其余字段照常可用。

`分镜脚本_*.md` 的体例见工作区 `分镜/第55集_第4段_分镜脚本_60-90s.md`。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Optional

# 字段 → 列名候选（全部按「去空格、去括号说明」后比较）
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "shot_no": ("镜号", "镜头号", "编号"),
    "scene": ("场次", "场景"),
    "framing": ("景别",),
    "camera": ("摄法",),
    "position": ("机位",),
    "movement": ("运镜",),
    "focal": ("焦段",),
    "duration": ("时长", "时长(s)", "时长s", "秒数"),
    "content": ("画面内容", "画面"),
    "action": ("人物动作", "动作"),
    "dialogue": ("台词·声音", "台词声音", "台词", "对白"),
    "sound": ("音效·bgm", "音效bgm", "音效", "声音"),
    "transition": ("转场",),
    "light": ("光线",),
    "color": ("色彩·氛围", "色彩氛围", "色彩"),
    "props": ("道具",),
    "costume": ("服装·妆造", "服装妆造", "服装"),
    "fx": ("特效·后期", "特效后期", "特效"),
    "beat": ("节拍·功能", "节拍功能", "节拍"),
    "risk": ("风险·备注", "风险备注", "风险"),
    "material": ("素材编号", "素材", "素材号"),
}

# 缺任一列 → PARTIAL
REQUIRED: tuple[str, ...] = ("shot_no", "duration", "framing", "content", "material")

# 段号/集号/区间
EP_RE = re.compile(r"第\s*(\d+)\s*集")
NO_RE = re.compile(r"第\s*(\d+)\s*段")
RANGE_RES = (
    re.compile(r"(\d+(?:\.\d+)?)\s*[–—\-~～]\s*(\d+(?:\.\d+)?)\s*s"),
    re.compile(r"(\d+(?:\.\d+)?)\s*[–—\-~～]\s*(\d+(?:\.\d+)?)"),
)
DUR_TAIL_RE = re.compile(r"｜\s*(\d+(?:\.\d+)?)\s*s\s*/\s*(\d+)\s*镜")
DUR_META_RE = re.compile(r"本段[：:]\s*(\d+(?:\.\d+)?)\s*s")
ASPECT_RE = re.compile(r"(\d+\s*[:：]\s*\d+)")
RES_RE = re.compile(r"(\d{3,4})\s*[×x]\s*(\d{3,4})")
CARRY_RE = re.compile(r"承接[^：:（(\n]*[：:]?\s*([^\n｜|]+)")


# ---------------------------------------------------------------------------
# markdown 表格
# ---------------------------------------------------------------------------

def normalize_head(text: str) -> str:
    """列名归一：去空格、去 id 里的 `(s)`/`（秒）` 这类单位说明，小写英文。"""
    s = (text or "").strip().replace(" ", "").replace("\u3000", "")
    s = s.replace("（", "(").replace("）", ")")
    # 去掉尾部括号说明：时长(s) → 时长
    s = re.sub(r"\([^)]*\)$", "", s)
    return s.lower()


def split_row(line: str) -> list[str]:
    """切一行表格。去掉首尾竖线后按 `|` 切，单元格内不处理转义（体例里不出现）。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_separator(cells: Iterable[str]) -> bool:
    cells = list(cells)
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in cells if c.strip() != "")


def parse_tables(text: str) -> list[dict[str, Any]]:
    """抽出全部 markdown 表格：`{"header": [...], "rows": [[...]]}`。"""
    tables: list[dict[str, Any]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|") and line.count("|") >= 2:
            block: list[str] = []
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                block.append(lines[j])
                j += 1
            if len(block) >= 2:
                header = split_row(block[0])
                body: list[list[str]] = []
                started = False
                for row_line in block[1:]:
                    cells = split_row(row_line)
                    if not started and _is_separator(cells):
                        started = True
                        continue
                    if not started:
                        # 没有分隔行：第一行当表头，这里已经取了
                        continue
                    if all(c == "" for c in cells):
                        continue
                    body.append(cells)
                tables.append({"header": header, "rows": body})
            i = j
        else:
            i += 1
    return tables


def map_columns(header: list[str]) -> dict[str, int]:
    """列名 → 下标。同名列只认第一个；认不出的列直接忽略。"""
    mapped: dict[str, int] = {}
    normalized = [normalize_head(h) for h in header]
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            key = normalize_head(alias)
            if key in normalized:
                mapped[field] = normalized.index(key)
                break
        if field in mapped:
            continue
        # 兜底：前缀命中（如「时长(秒)」被 normalize 掉括号后已相等，这里防「素材编号A」）
        for idx, head in enumerate(normalized):
            if any(head.startswith(normalize_head(a)) and len(head) <= len(normalize_head(a)) + 2
                   for a in aliases):
                mapped[field] = idx
                break
    return mapped


def pick_main_table(tables: list[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], dict[str, int]]:
    """挑 21 字段主表：同时含镜号与时长，且命中列最多。风险表/道具表会输。"""
    best: Optional[dict[str, Any]] = None
    best_map: dict[str, int] = {}
    for table in tables:
        mapped = map_columns(table["header"])
        if "shot_no" not in mapped:
            continue
        score = len(mapped)
        if "duration" not in mapped or "content" not in mapped:
            continue
        if score > len(best_map):
            best, best_map = table, mapped
    return best, best_map


def cell(row: list[str], mapped: dict[str, int], field: str) -> str:
    idx = mapped.get(field)
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip()


# ---------------------------------------------------------------------------
# 台词解析
# ---------------------------------------------------------------------------

LIP_OFF = "无口型"
LIP_ON = "有"

_SAY_RE = re.compile(r"^\s*([^\s：:，,。]{1,12}?)\s*(?:说|道|问|答|喊|叫)[：:]?\s*[“\"](.+?)[”\"]")
_QUOTE_RE = re.compile(r"^\s*([^\s：:，,。]{1,12}?)\s*[：:]\s*[“\"](.+?)[”\"]")
_MONO_RE = re.compile(r"^\s*(?:【内心】|\[内心\])?\s*([^\s：:，,。]{1,12}?)\s*(?:内心独白)?\s*[：:]\s*(.+)$")
_INNER_FLAG = re.compile(r"^\s*(?:【内心】|\[内心\]|（内心）|\(内心\))")
_SOUNDISH = re.compile(r"^(SFX|AMB|BGM|MUSIC)[·\s]")


def parse_dialogue(cell_text: str) -> list[dict[str, str]]:
    """把一个单元格里的话拆成发声单元。

    三种体例：
    - `云天娇说：“干嘛？”`            → 有口型
    - `顾砚书内心独白：要是她当初…`   → 无口型（画外音）
    - `—` / 空                        → 无
    一格多句用 `；` 或换行分隔。
    """
    text = (cell_text or "").strip()
    if not text or text in ("—", "-", "无", "无台词"):
        return []
    out: list[dict[str, str]] = []
    for part in re.split(r"[；;\n]+", text):
        part = part.strip()
        if not part or part in ("—", "-", "无"):
            continue
        m = _MONO_RE.match(part)
        if m and (("内心独白" in part) or _INNER_FLAG.match(part)):
            who = m.group(1)
            line = re.sub(r"（[^）]*）$", "", m.group(2)).strip()
            out.append({"who": who, "line": line, "lip": LIP_OFF, "kind": "独白"})
            continue
        m = _SAY_RE.match(part) or _QUOTE_RE.match(part)
        if m:
            out.append({
                "who": m.group(1),
                "line": m.group(2).strip(),
                "lip": LIP_ON,
                "kind": "台词",
            })
            continue
        # 无引号体例：`顾砚书：休息吧，你也累一天了。`
        m = re.match(r"^\s*([^\s：:，,。]{1,8})\s*(?:（内心）|\(内心\)|（画外）|\(画外\))?\s*[：:]\s*(.+)$", part)
        if m and not _SOUNDISH.match(m.group(2)):
            head = part[: m.start(2)]
            inner = any(w in head for w in ("内心", "画外"))
            who = re.sub(r"[（(](?:内心|画外)[）)]", "", m.group(1)).strip()
            out.append({
                "who": who,
                "line": m.group(2).strip(),
                "lip": LIP_OFF if inner else LIP_ON,
                "kind": "独白" if inner else "台词",
            })
            continue
        # 认不出说话人但确实有引号 → 记一条待人工确认，不丢内容
        m = re.search(r"[“\"](.+?)[”\"]", part)
        if m:
            out.append({"who": "", "line": m.group(1), "lip": LIP_ON, "kind": "台词"})
    return out


# ---------------------------------------------------------------------------
# 中间层
# ---------------------------------------------------------------------------

def _float(text: str) -> float:
    m = re.search(r"\d+(?:\.\d+)?", text or "")
    return float(m.group(0)) if m else 0.0


def _shot_seq(no: str) -> int:
    """`SC01-SH025` → 25；`SH025` → 25；纯数字 → 自身。"""
    m = re.search(r"SH\s*(\d+)", no or "", re.IGNORECASE)
    if m:
        return int(m.group(1)) % 100
    m = re.search(r"(\d+)", no or "")
    return int(m.group(1)) if m else 0


def _head_meta(text: str) -> dict[str, Any]:
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    head_block = "\n".join(text.splitlines()[:40])

    ep_m = EP_RE.search(first)
    no_m = NO_RE.search(first)

    rng: tuple[float, float] | None = None
    for rx in RANGE_RES:
        m = rx.search(first)
        if m:
            rng = (float(m.group(1)), float(m.group(2)))
            break
    if rng is None:
        for rx in RANGE_RES:
            m = rx.search(head_block)
            if m:
                rng = (float(m.group(1)), float(m.group(2)))
                break

    dur = 0.0
    m = DUR_TAIL_RE.search(first)
    if m:
        dur = float(m.group(1))
    if not dur:
        m = DUR_META_RE.search(head_block)
        if m:
            dur = float(m.group(1))
    if not dur and rng:
        dur = round(rng[1] - rng[0], 3)

    aspect = ""
    m = ASPECT_RE.search(head_block)
    if m:
        aspect = m.group(1).replace("：", ":").replace(" ", "")

    resolution = ""
    m = RES_RE.search(head_block)
    if m:
        resolution = f"{m.group(1)}×{m.group(2)}"

    carry = ""
    m = CARRY_RE.search(head_block)
    if m:
        carry = m.group(1).strip().strip("（）()").strip()

    return {
        "title": first.lstrip("# ").strip(),
        "ep": int(ep_m.group(1)) if ep_m else 0,
        "no": int(no_m.group(1)) if no_m else 0,
        "range": {"start": rng[0], "end": rng[1]} if rng else None,
        "duration_target": dur,
        "aspect": aspect or "16:9",
        "resolution": resolution,
        "carry_in": carry,
    }


def parse_storyboard(text: str, source_path: str = "", seg_id: str = "") -> dict[str, Any]:
    """整份分镜脚本 → 中间层。永不抛错，最差返回 status=NO_TABLE。"""
    meta = _head_meta(text)
    tables = parse_tables(text)
    main, mapped = pick_main_table(tables)

    issues: list[dict[str, str]] = []
    shots: list[dict[str, Any]] = []

    if main is None:
        issues.append({"kind": "NO_TABLE", "field": "", "detail": "没有找到含「镜号 / 时长 / 画面内容」的分镜主表"})
    else:
        missing = [f for f in REQUIRED if f not in mapped]
        for f in missing:
            label = COLUMN_ALIASES[f][0]
            issues.append({"kind": "MISSING_COLUMN", "field": f, "detail": f"缺「{label}」列"})

        offset = 0.0
        for row in main["rows"]:
            no = cell(row, mapped, "shot_no")
            if not no or no in ("—", "-"):
                continue
            raw_dialogue = cell(row, mapped, "dialogue")
            dlg = parse_dialogue(raw_dialogue)
            dur = _float(cell(row, mapped, "duration"))
            shots.append({
                "no": no,
                "seq": _shot_seq(no),
                "scene": cell(row, mapped, "scene"),
                "framing": cell(row, mapped, "framing"),
                "camera": cell(row, mapped, "camera"),
                "position": cell(row, mapped, "position"),
                "movement": cell(row, mapped, "movement"),
                "focal": cell(row, mapped, "focal"),
                "duration": dur,
                "start": round(offset, 3),
                "end": round(offset + dur, 3),
                "content": cell(row, mapped, "content"),
                "action": cell(row, mapped, "action"),
                "dialogue_raw": raw_dialogue,
                "dialogue": dlg,
                "sound": cell(row, mapped, "sound"),
                "transition": cell(row, mapped, "transition"),
                "light": cell(row, mapped, "light"),
                "color": cell(row, mapped, "color"),
                "props": cell(row, mapped, "props"),
                "costume": cell(row, mapped, "costume"),
                "fx": cell(row, mapped, "fx"),
                "beat": cell(row, mapped, "beat"),
                "risk": cell(row, mapped, "risk"),
                "material": cell(row, mapped, "material"),
            })
            offset += dur

        # 重复镜号：列出来，不静默合并
        seen: dict[str, int] = {}
        for s in shots:
            seen[s["no"]] = seen.get(s["no"], 0) + 1
        dups = [k for k, v in seen.items() if v > 1]
        if dups:
            issues.append({"kind": "DUPLICATE_SHOT", "field": "shot_no", "detail": f"重复镜号：{'、'.join(dups)}"})

        total = round(sum(s["duration"] for s in shots), 3)
        target = meta["duration_target"] or total
        if target and abs(total - target) > 0.1:
            issues.append({
                "kind": "DURATION_DRIFT",
                "field": "duration",
                "detail": f"各镜合计 {total:.1f}s，抬头 {target:.1f}s，偏差 {abs(total - target):.1f}s",
            })

        meta["shot_sum_sec"] = total

    props_tracking = _parse_props_tracking(tables, mapped is not None and main is not None and main in tables)
    risks = _parse_risks(tables)
    if not shots:
        status = "NO_TABLE"
    elif any(i["kind"] in ("MISSING_COLUMN", "DUPLICATE_SHOT", "NO_TABLE") for i in issues):
        status = "PARTIAL"
    else:
        status = "READY"

    ep = meta["ep"]
    no = meta["no"]
    return {
        "id": seg_id or (f"{ep}-{no}" if ep and no else source_path),
        "source_path": source_path,
        "status": status,
        "issues": issues,
        "shots": shots,
        "props_tracking": props_tracking,
        "risks": risks,
        **meta,
    }


def _parse_props_tracking(tables: list[dict[str, Any]], _skip: bool = False) -> list[dict[str, str]]:
    """道具追踪表（可选）。认「道具」「首次出现」「状态变化链」三列。"""
    out: list[dict[str, str]] = []
    for table in tables:
        heads = [normalize_head(h) for h in table["header"]]
        if "道具" not in heads or "状态变化链" not in heads:
            continue
        i_prop = heads.index("道具")
        i_first = heads.index("首次出现") if "首次出现" in heads else None
        i_chain = heads.index("状态变化链")
        i_end = heads.index("归宿") if "归宿" in heads else None
        for row in table["rows"]:
            if i_prop >= len(row):
                continue
            out.append({
                "prop": row[i_prop].strip(),
                "first": row[i_first].strip() if i_first is not None and i_first < len(row) else "",
                "chain": row[i_chain].strip() if i_chain < len(row) else "",
                "end": row[i_end].strip() if i_end is not None and i_end < len(row) else "",
            })
    return out


def _parse_risks(tables: list[dict[str, Any]]) -> list[dict[str, str]]:
    """风险登记表（可选）。列名有两种：`风险` 或 `描述`，其余固定。"""
    out: list[dict[str, str]] = []
    for table in tables:
        heads = [normalize_head(h) for h in table["header"]]
        if "等级" not in heads or "镜号" not in heads:
            continue
        if not any(h in heads for h in ("风险", "描述", "风险类型")):
            continue
        if "景别" in heads:  # 主表，跳过
            continue

        def idx(*names: str) -> Optional[int]:
            for n in names:
                if n in heads:
                    return heads.index(n)
            return None

        i_shot = idx("镜号")
        i_kind = idx("风险类型", "类型")
        i_text = idx("风险", "描述")
        i_level = idx("等级")
        i_fix = idx("处置", "预案")
        for row in table["rows"]:
            get = lambda i: row[i].strip() if i is not None and i < len(row) else ""  # noqa: E731
            out.append({
                "shots": get(i_shot),
                "kind": get(i_kind),
                "text": get(i_text),
                "level": get(i_level),
                "fix": get(i_fix),
            })
    return out


# ---------------------------------------------------------------------------
# 文件扫描
# ---------------------------------------------------------------------------

STORYBOARD_DIR_CANDIDATES = ("分镜表", "分镜", "storyboard")
_VARIANT_HINTS = ("快节奏", "加密", "补剧情", "备份", "old", "旧")


def storyboard_dirs(root: Path, configured: Optional[Path] = None) -> list[Path]:
    """分镜目录：配置的优先，其余按候选名兜底（各人叫法不同）。"""
    out: list[Path] = []
    if configured is not None and configured.is_dir():
        out.append(configured)
    for name in STORYBOARD_DIR_CANDIDATES:
        p = root / name
        if p.is_dir() and p not in out:
            out.append(p)
    return out


def _file_score(path: Path) -> tuple[int, int]:
    """同名多版本时挑「正稿」：含变体词的往后排，名字短的优先。"""
    penalty = 1 if any(h in path.stem for h in _VARIANT_HINTS) else 0
    return (penalty, len(path.stem))


def find_storyboards(root: Path, configured: Optional[Path] = None) -> list[Path]:
    """所有分镜脚本 md，按 (集, 段) 去重，每段只留一份正稿。"""
    best: dict[tuple[int, int], Path] = {}
    others: list[Path] = []
    for folder in storyboard_dirs(root, configured):
        for md in sorted(folder.glob("*.md")):
            name = md.name
            if "分镜" not in name and "脚本" not in name:
                continue
            ep_m = EP_RE.search(name)
            no_m = NO_RE.search(name)
            if not ep_m or not no_m:
                continue
            key = (int(ep_m.group(1)), int(no_m.group(1)))
            prev = best.get(key)
            if prev is None or _file_score(md) < _file_score(prev):
                if prev is not None:
                    others.append(prev)
                best[key] = md
            else:
                others.append(md)
    return sorted(best.values(), key=lambda p: _file_score(p)[1]) or []


def read_storyboard(path: Path, root: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
    parsed = parse_storyboard(text, source_path=rel)
    if not parsed["id"] or "-" not in str(parsed["id"]):
        parsed["id"] = f"{parsed['ep']}-{parsed['no']}"
    parsed["text"] = text
    return parsed
