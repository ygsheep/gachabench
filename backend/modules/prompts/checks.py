# -*- coding: utf-8 -*-
"""规则诊断：不看模型，先把模板的硬规矩逐条量一遍。

为什么值得单独写：模型生成的稿、手改过的稿、从别人那儿抄来的稿，
错的往往是同几处 —— 正文写了时长、正文里漏了真实文件名、内心独白没标无口型、
素材表列了却没用上。这些都能用字符串判断，不该等一次模型调用。

每条结论要么只是提醒，要么带一个**可直接应用的字符区间**（`fix`）。
带 fix 的结论能攒成一个补丁，由前端预览后应用；不带的只报不改。
"""
from __future__ import annotations

import re
from typing import Any, Optional

SECTION_HEADS = {
    "prefix": "## 前缀参考素材+风格+环境",
    "body": "## 正文",
    "tail": "## 尾部模板",
}

REQUIRED_TAGS = (
    "【风格】",
    "【主体描述】",
    "【场景环境】",
    "【时间轴】",
    "【镜头语言】",
    "【光影氛围】",
    "【声音设计】",
    "【AI防崩约束词】",
    "【合规约束词】",
    "【参考】",
)

SHOT_LINE_RE = re.compile(r"\[镜(\d+)\]")
DURATION_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:s|秒)(?![a-zA-Z])")
PAREN_DURATION_RE = re.compile(r"[（(][^）)]{0,12}\d+(?:\.\d+)?\s*(?:s|秒)[^）)]{0,12}[）)]")
MEDIA_FILE_RE = re.compile(
    r"[\u4e00-\u9fa5A-Za-z0-9_\-（）()·\. ]{1,48}?\.(?:png|jpe?g|webp|mp3|wav|m4a|mp4)",
    re.IGNORECASE,
)
PLACEHOLDER_RE = re.compile(r"@(?:人物|场景|道具|音色|Picture|Subject|Audio)[0-9备用]*")
TABLE_ROW_RE = re.compile(r"^\|\s*(@[^|]+?)\s*\|\s*([^|]*?)\s*\|", re.MULTILINE)
NUMBERED_RE = re.compile(r"^\s*\d+[.、]\s+", re.MULTILINE)

LEVEL_ORDER = {"error": 0, "warn": 1, "info": 2}


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _block(text: str, heading: str) -> Optional[tuple[int, int]]:
    """取某一节 ``` 代码块的内容区间（不含围栏本身）。"""
    at = text.find(heading)
    if at < 0:
        return None
    fence = text.find("```", at)
    if fence < 0:
        return None
    body_start = text.find("\n", fence)
    if body_start < 0:
        return None
    body_start += 1
    end = text.find("```", body_start)
    if end < 0:
        return None
    return body_start, end


def _iter_lines(text: str, start: int, end: int):
    """(行首偏移, 行文本) —— 保留偏移，才能把结论定位回字符区间。"""
    pos = start
    for line in text[start:end].splitlines(keepends=True):
        yield pos, line.rstrip("\n")
        pos += len(line)


def parse_materials(text: str) -> dict[str, str]:
    """素材表：{占位: 文件名}。只认表格里的 `| @xx | file |`。"""
    rows: dict[str, str] = {}
    head = text.find("## 素材表")
    if head < 0:
        return rows
    tail = text.find("##", head + 3)
    chunk = text[head: tail if tail > 0 else len(text)]
    for m in TABLE_ROW_RE.finditer(chunk):
        placeholder, name = m.group(1).strip(), m.group(2).strip()
        if placeholder.startswith("@") and name and not name.startswith("---"):
            rows[placeholder] = name
    return rows


def _finding(
    code: str,
    level: str,
    title: str,
    detail: str,
    *,
    scope: str = "",
    line: int = 0,
    shot: int = 0,
    fix: Optional[tuple[int, int, str]] = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "level": level,
        "title": title,
        "detail": detail,
        "scope": scope,
        "line": line,
        "shot": shot,
        "fixable": fix is not None,
        "fix": {"start": fix[0], "end": fix[1], "after": fix[2]} if fix else None,
    }


def check_prompt(text: str, story: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """诊断一份三段式提示词。返回按严重度排好的结论列表。"""
    text = text or ""
    out: list[dict[str, Any]] = []
    materials = parse_materials(text)
    bodies = {key: _block(text, head) for key, head in SECTION_HEADS.items()}

    # 1) 三节齐不齐
    for key, head in SECTION_HEADS.items():
        if head not in text:
            out.append(_finding(
                "MISSING_SECTION", "error", f"缺「{head.lstrip('# ')}」整节",
                "三段式少了这一节，模型读不到那部分约束。", scope=key,
                line=_line_of(text, len(text)),
            ))
    # 2) 十个小标题齐不齐
    for tag in REQUIRED_TAGS:
        if tag not in text:
            out.append(_finding(
                "MISSING_TAG", "warn", f"缺 {tag}",
                "模板要求这一块必须在文件里出现，哪怕只有一句。",
            ))

    prefix = bodies.get("prefix")
    body = bodies.get("body")
    tail = bodies.get("tail")

    # 3) 正文不要写时长
    if body:
        for pos, line in _iter_lines(text, *body):
            m = SHOT_LINE_RE.search(line)
            if not m:
                continue
            shot_no = int(m.group(1))
            paren = PAREN_DURATION_RE.search(line)
            token = DURATION_RE.search(line)
            if not paren and not token:
                continue
            if paren:
                start, end = pos + paren.start(), pos + paren.end()
                after = ""
            else:
                assert token is not None
                start, end = pos + token.start(), pos + token.end()
                after = ""
            out.append(_finding(
                "BODY_HAS_DURATION", "warn", "正文写了时长",
                f"第 {shot_no} 镜的镜头里有「{line[paren.start():paren.end()] if paren else token.group(0)}」；"
                "时长只进「分镜时间动作表」，正文写它会让模型把镜头当成固定时长。",
                scope=f"shot:{shot_no}", line=_line_of(text, start), shot=shot_no,
                fix=(start, end, after),
            ))

    # 4) 正文 / 前缀里的真实文件名。
    #    先拿素材表里的真文件名去原文里找（文件名可能带空格、括号，正则很难写准），
    #    找不到的再用通用媒体后缀兜一遍，那种只报不改。
    known = {name: ph for ph, name in materials.items() if name}
    for key in ("prefix", "body"):
        span = bodies.get(key)
        if not span:
            continue
        for pos, line in _iter_lines(text, *span):
            if line.lstrip().startswith("|"):
                continue
            hit_spans: list[tuple[int, int]] = []
            for name, placeholder in known.items():
                at = line.find(name)
                while at >= 0:
                    start = pos + at
                    end = start + len(name)
                    hit_spans.append((start, end))
                    out.append(_finding(
                        "REAL_FILENAME_IN_TEXT", "error", "正文写了真实文件名",
                        f"正文里直接写了文件名「{name}」。素材表用占位、正文引用占位，"
                        f"文件名只该出现在素材表里；这里可以换成 {placeholder}。",
                        scope=key, line=_line_of(text, start),
                        fix=(start, end, placeholder),
                    ))
                    at = line.find(name, at + len(name))
            for m in MEDIA_FILE_RE.finditer(line):
                raw = m.group(0)
                start = pos + m.start()
                end = start + len(raw)
                if any(s0 <= start and end <= e0 for s0, e0 in hit_spans):
                    continue
                if any(name and (name in raw) for name in known):
                    continue
                out.append(_finding(
                    "REAL_FILENAME_IN_TEXT", "error", "正文写了真实文件名",
                    f"正文里出现了文件名「{raw.strip()}」，但素材表里没有它。"
                    "要么先绑素材再引用占位，要么把这句删掉。",
                    scope=key, line=_line_of(text, start),
                ))

    # 5) 素材表 ↔ 全文 互相点名。
    #    只看前缀与正文会误报音色：音色本来就只出现在尾部【参考】里。
    used_text = text
    mat_head = text.find("## 素材表")
    if mat_head >= 0:
        mat_end = text.find("##", mat_head + 3)
        used_text = text[:mat_head] + text[mat_end if mat_end > 0 else len(text):]
    used = set(PLACEHOLDER_RE.findall(used_text))
    for placeholder in materials:
        if placeholder not in used and placeholder != "@人物1备用":
            out.append(_finding(
                "UNUSED_MATERIAL", "warn", f"{placeholder} 没被引用",
                f"素材表里列了 {placeholder}（{materials[placeholder]}），但前缀与正文里一次都没提到它，"
                "白传一张图。",
            ))
    for placeholder in sorted(used):
        if materials and placeholder not in materials:
            out.append(_finding(
                "MISSING_MATERIAL_ROW", "error", f"{placeholder} 不在素材表",
                "正文用了这个占位，但素材表里没有对应的文件名，传到平台时会对不上。",
            ))

    # 6) 台词一字不改 / 镜数对得上
    if story and story.get("shots"):
        shots = story["shots"]
        body_text = text[body[0]:body[1]] if body else ""
        body_shots = {int(m.group(1)) for m in SHOT_LINE_RE.finditer(body_text)}
        if body_shots and len(body_shots) != len(shots):
            out.append(_finding(
                "SHOT_COUNT_MISMATCH", "error", "镜数对不上",
                f"正文写了 {len(body_shots)} 镜，分镜表有 {len(shots)} 镜。",
            ))
        for idx, shot in enumerate(shots, start=1):
            for item in shot.get("dialogue") or []:
                line = (item.get("line") or "").strip()
                if line and line not in text:
                    out.append(_finding(
                        "DIALOGUE_NOT_VERBATIM", "error", "台词与分镜表不一致",
                        f"第 {idx} 镜（{shot.get('no')}）的台词「{line}」在提示词里找不到原文。"
                        "铁律是台词一字不改。",
                        scope=f"shot:{idx}", shot=idx,
                    ))

    # 7) 内心独白要标无口型
    for key in ("body", "tail"):
        span = bodies.get(key)
        if not span:
            continue
        for pos, line in _iter_lines(text, *span):
            if "内心独白" not in line or "无口型" in line:
                continue
            out.append(_finding(
                "MONOLOGUE_NO_LIP", "warn", "内心独白没标无口型",
                "独白是画外音，必须写明「无口型，是画外音，嘴部无任何开口动作」，"
                "否则模型会给人物排口型。",
                scope=key, line=_line_of(text, pos),
                fix=(pos + len(line), pos + len(line), "（内心独白无口型，是画外音，嘴部无任何开口动作）"),
            ))

    # 8) 声音三连
    for word in ("无BGM", "无字幕", "无水印"):
        if word not in text:
            out.append(_finding(
                "MISSING_SOUND_RULE", "warn", f"没写「{word}」",
                "模板要求声音设计里一句话把这三条都写死，漏一条模型就可能自己加。",
            ))

    # 9) 防崩约束条数
    if tail:
        tail_text = text[tail[0]:tail[1]]
        count = len(NUMBERED_RE.findall(tail_text))
        if count and count < 8:
            out.append(_finding(
                "FEW_CONSTRAINTS", "info", f"防崩约束只有 {count} 条",
                "模板是 14 条、每条只堵一个失败点。条数太少，模型放飞的概率会明显上升。",
            ))

    # 10) 抬头承接
    head_part = text[: bodies["prefix"][0]] if bodies.get("prefix") else text[:400]
    if "承接" not in head_part:
        out.append(_finding(
            "MISSING_CARRY", "info", "抬头没写承接",
            "「承接上一段末镜」这一行能显著减少接不上戏，建议补一句。",
        ))

    out.sort(key=lambda f: (LEVEL_ORDER.get(f["level"], 9), f["line"]))
    return out


def summarize(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"error": 0, "warn": 0, "info": 0, "fixable": 0}
    for f in findings:
        counts[f["level"]] = counts.get(f["level"], 0) + 1
        if f["fixable"]:
            counts["fixable"] += 1
    return counts
