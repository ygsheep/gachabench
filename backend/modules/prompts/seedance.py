# -*- coding: utf-8 -*-
"""Seedance 三段式渲染：分镜中间层 + 素材绑定 → 一份可直接复制的提示词 md。

体例见工作区 `提示词/提示词模板_三段式.md`：

    速览三表（素材 / 时间动作 / 台词）
    → 前缀（风格 + 主体描述 + 场景环境）
    → 正文（逐镜【时间轴】）
    → 尾部（镜头语言 / 光影氛围 / 声音设计 / 防崩 / 合规 / 参考）

渲染是**纯函数**：同样输入永远得到同样输出，不读时钟、不读文件。
需要「继承上一段的风格与场景」这类上下文时，由 service 传进来（options）。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable, Optional

# 景别缩写 → 中文。模型对中文景别更敏感，英文缩写只留在分镜表里。
FRAMING_ZH = {
    "ECU": "大特写",
    "BCU": "大特写",
    "CU": "特写",
    "MCU": "中近景",
    "MS": "中景",
    "MLS": "中全景",
    "LS": "全景",
    "WS": "全景",
    "FS": "远景",
    "ELS": "远景",
    "OTS": "过肩",
    "POV": "主观镜头",
    "2S": "双人镜头",
    "INS": "插入镜头",
}

# 节拍代码 → 中文。分镜表里 HK/ES/TW… 是编剧的节拍记号，提示词里要落到人话。
BEAT_ZH = {
    "HK": "钩子",
    "ES": "关系建立",
    "TW": "反转/反弹",
    "CF": "冲突",
    "SF": "给出台阶",
    "PT": "交代",
    "EM": "落空",
    "CL": "收束/卡点",
    "Q": "问",
    "A": "答",
}

DEFAULT_STYLE = (
    "16:9横屏，{era}的年代剧，高品质国产3D动画CG，UE5电影级渲染，半写实国漫风格，"
    "超高精度角色建模，真实自然光影，PBR真实材质，电影级构图与景深。"
    "无字幕、无BGM、无水印。画面清晰稳定，不发糊，不过度柔焦，"
    "人物面部细微情绪、手部动作、视线变化都要清楚可见。"
)

DEFAULT_COMPLIANCE = (
    "本段无暴力、无血腥、无暴露、无越界亲密接触，符合年代剧的表达边界；"
    "所有身体接触隔着衣料完成，不出现皮肤特写。"
)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def dedupe(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for it in items:
        it = (it or "").strip()
        if it and it not in out and it not in ("—", "-", "无"):
            out.append(it)
    return out


def one_line(text: str, limit: int = 48) -> str:
    s = re.sub(r"\s+", "", (text or "").strip())
    if len(s) <= limit:
        return s
    cut = s[:limit]
    for sep in ("，", "。", "；", "、", "→"):
        idx = cut.rfind(sep)
        if idx >= limit // 2:
            return cut[:idx]
    return cut + "…"


def expand_framing(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return "中景"
    key = raw.upper().replace(" ", "")
    if key in FRAMING_ZH:
        return FRAMING_ZH[key]
    return raw


def beat_zh(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    key = raw.split("·")[0].strip().upper()
    return BEAT_ZH.get(key, raw)


def _sound_brief(text: str) -> str:
    """声音列去掉括号备注再截断：`声音先导·纸页停住的静（切点前 0.2s 起）` → 前半句。"""
    s = re.sub(r"[（(][^）)]*[）)]", "", (text or "").strip())
    return one_line(s, 20)


def _fmt_sec(v: float) -> str:
    """0.0 / 2.4 / 15.0 —— 一位小数够了，三位小数是给人看的噪声。"""
    return f"{v:.1f}"


def _num(text: str) -> int:
    m = re.search(r"\d+", text or "")
    return int(m.group(0)) if m else 0


# ---------------------------------------------------------------------------
# 速览三表
# ---------------------------------------------------------------------------

def render_materials_table(materials: dict[str, Any]) -> str:
    """只列**真会传到平台上**的图与音频。

    没绑上 / 被忽略的占位不写进来：素材表是给上传用的清单，
    列一堆「待指定」只会让人以为漏了东西；那些在界面上单独处理。
    """
    lines = ["| 占位 | 文件名 |", "| --- | --- |"]
    for row in materials.get("rows", []):
        if not row.get("file"):
            continue
        lines.append(f"| {row['placeholder']} | {row['file']} |")
    if len(lines) == 2:
        lines.append("| — | 本段还没有可用的素材，先绑定再渲染 |")
    return "\n".join(lines)


def render_timeline_table(story: dict[str, Any]) -> str:
    lines = ["| 镜号 | 时间 | 动作 |", "| --- | --- | --- |"]
    for shot in story.get("shots", []):
        dur = shot.get("duration") or 0.0
        start = shot.get("start") or 0.0
        action = one_line(shot.get("action") or shot.get("content") or "", 34)
        lines.append(f"| {shot['no']} | {_fmt_sec(start)}–{_fmt_sec(start + dur)}s | {action} |")
    return "\n".join(lines)


def render_dialogue_table(story: dict[str, Any]) -> str:
    lines = ["| 镜号 | 谁 | 台词 | 口型 |", "| --- | --- | --- | --- |"]
    rows = 0
    for shot in story.get("shots", []):
        for item in shot.get("dialogue") or []:
            rows += 1
            who = item.get("who") or "（未标）"
            if item.get("kind") == "独白":
                who = f"{who}（内心）"
            lip = "无口型" if item.get("lip") == "无口型" else "有"
            lines.append(f"| {shot['no']} | {who} | {item['line']} | {lip} |")
    if not rows:
        lines.append("| — | — | 本段无台词，全部为环境音与动作音 | — |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 前缀
# ---------------------------------------------------------------------------

def _person_brief(story: dict[str, Any], name: str) -> tuple[str, str]:
    """返回 (服装造型, 本段状态)。状态取她/他第一次出现在画面内容里的那一句。"""
    costume = ""
    state = ""
    for shot in story.get("shots", []):
        raw = shot.get("costume") or ""
        for part in re.split(r"[；;\n]+", raw):
            part = part.strip()
            if part.startswith(name) and "-" in part and not costume:
                costume = part.split("-", 1)[1].strip()
        if not state and name in (shot.get("content") or ""):
            state = one_line(shot.get("content") or "", 34)
        if not state:
            for item in shot.get("dialogue") or []:
                if item.get("who") == name:
                    state = f"本段开口「{one_line(item.get('line') or '', 16)}」"
                    break
        if state:
            break
    return costume, state


def _space_relation(story: dict[str, Any], people: list[str]) -> str:
    shots = story.get("shots", [])
    duo = 0
    for shot in shots:
        haystack = " ".join([
            shot.get("content") or "",
            shot.get("action") or "",
            shot.get("costume") or "",
            shot.get("dialogue_raw") or "",
        ])
        if sum(1 for p in people if p in haystack) >= 2:
            duo += 1
    scene = (story.get("scene") or "").strip()
    last = shots[-1] if shots else {}
    tail = one_line(last.get("action") or last.get("content") or "", 30)
    carry = (story.get("carry_in") or "").strip()
    parts = [
        f"本段全在{scene or '同一场景'}内，真人只有{'、'.join(people) or '主角'}，"
        f"两人同框 {duo} 镜。",
    ]
    if carry:
        parts.append(f"承接上一段：{carry}。")
    if tail:
        parts.append(f"结尾停在这一瞬：{tail}，不许多走一步。")
    return "".join(parts)


def render_prefix(
    story: dict[str, Any],
    materials: dict[str, Any],
    options: dict[str, Any],
) -> str:
    style_body = (options.get("style") or "").strip()
    if not style_body:
        era = (options.get("era_region") or "").strip() or "（补年代与地域）"
        style_body = DEFAULT_STYLE.format(era=era)
    aspect = options.get("aspect") or story.get("aspect") or "16:9"
    if "16:9" in style_body:
        style_body = style_body.replace("16:9横屏", f"{aspect}横屏", 1)
    else:
        style_body = f"{aspect}横屏，" + style_body

    name_to_ph = materials.get("name_to_placeholder") or {}
    people = [p for p in (materials.get("people") or []) if p]
    real_people = [p for p in people if not any(w in p for w in ("Q版", "小人"))]
    q_people = [p for p in people if any(w in p for w in ("Q版", "小人"))]

    person_lines: list[str] = []
    for name in people:
        ph = name_to_ph.get(name, "")
        for row in materials.get("rows", []):
            if row.get("name") == name and row.get("kind") == "person":
                ph = row["placeholder"]
        person_lines.append(f"{name} {ph}".strip())

    brief_lines: list[str] = []
    for name in people:
        costume, state = _person_brief(story, name)
        seg = f"{name}：{costume or '造型见参考图'}。"
        if state:
            seg += f"本段状态：{state}。"
        brief_lines.append(seg)

    crowd = f"全段真人 {len(real_people)} 个（{'、'.join(real_people) or '—'}），无群演、无替身。"
    if q_people:
        crowd += f"另有 {'、'.join(q_people)}，只有观众看得见，不占真人数量。"

    body = [
        "【风格】",
        style_body,
        "",
        "【主体描述】",
        "人物：",
        "；".join(person_lines) if person_lines else "（本段未识别到人物，请手动补）",
        crowd,
        "",
        *brief_lines,
        "",
        _space_relation(story, real_people),
    ]

    # ---- 场景环境 ----
    scene_body = (options.get("scene") or "").strip()
    scene_refs = [r for r in materials.get("rows", []) if r.get("kind") == "scene" and r.get("file")]
    scene_names = "".join(f"{r['placeholder']}（{r['note']}）" for r in scene_refs)

    env = ["【场景环境】"]
    if scene_body:
        env.append(scene_body)
    else:
        env.append(
            f"{materials.get('scene_name') or '本段场景'}，"
            f"{'夜' if materials.get('scene_time') == '夜' else '日'}，室内。"
            "房间的墙体、地面、门窗、梁顶、灯具、家具与摆设，其外形、材质、颜色、位置"
            "全部以下场景参考图为准，照图搬用，不新增、不改动、不替换。"
        )
    if scene_names:
        env.append(f"场景参考图：{scene_names}")
        if len(scene_refs) >= 2:
            env.append("多张是同一间屋的不同机位，不是不同场景；图上缺什么是被画幅裁掉的，不是不存在。")

    prop_refs = [r for r in materials.get("rows", []) if r.get("kind") == "prop" and r.get("file")]
    prop_text = "、".join(f"{r['name']} {r['placeholder']}" for r in prop_refs) or "无独立道具，全部物件在场景内"
    env.append(f"道具：{prop_text}；其余物件均在场景内，不新增。")

    lights = dedupe(shot.get("light") or "" for shot in story.get("shots", []))
    colors = dedupe(shot.get("color") or "" for shot in story.get("shots", []))
    if lights:
        env.append(f"光线：{'；'.join(lights[:3])}。")
    if colors:
        env.append(f"色彩基调：{'；'.join(colors[:3])}。")

    sounds = dedupe(_sound_brief(shot.get("sound") or "") for shot in story.get("shots", []))
    if sounds:
        env.append(f"环境声：{'、'.join(sounds[:3])}。")

    return "\n".join(body + [""] + env)


# ---------------------------------------------------------------------------
# 正文
# ---------------------------------------------------------------------------

def _dialogue_line(item: dict[str, str]) -> str:
    who = item.get("who") or "画外"
    line = item.get("line") or ""
    if item.get("kind") == "独白":
        return f"{who}内心独白：{line}（内心独白无口型，是画外音，嘴部无任何开口动作）"
    return f"{who}说：{line}"


def render_body(story: dict[str, Any], options: dict[str, Any]) -> str:
    lines: list[str] = ["【时间轴】", ""]
    shots = story.get("shots", [])
    for i, shot in enumerate(shots, start=1):
        tech = "，".join(
            x for x in (
                expand_framing(shot.get("framing") or ""),
                shot.get("camera") or "",
                shot.get("position") or "",
                shot.get("movement") or "",
                shot.get("focal") or "",
            ) if x
        )
        lines.append(f"[镜{i}]{tech}。")
        lines.append(shot.get("content") or "")
        if shot.get("action"):
            lines.append(f"角色动作：{shot['action']}")
        mood = beat_zh(shot.get("beat") or "")
        mood_line = f"情绪：{mood}。" if mood else ""
        if shot.get("color"):
            tone = shot["color"].split("·")[-1]
            mood_line = f"色彩情绪：{tone}。" + (f"节拍：{mood}。" if mood else "")
        if mood_line:
            lines.append(mood_line)
        dlg = shot.get("dialogue") or []
        if dlg:
            lines.extend(_dialogue_line(d) for d in dlg)
        else:
            lines.append("无台词。")
        tail = f"声音：{shot.get('sound') or '环境音'}。"
        if shot.get("transition"):
            tail += f"转场：{shot['transition']}。"
        lines.append(f"（{tail}）")
        lines.append("")
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# 尾部
# ---------------------------------------------------------------------------

def _claps(shots: list[dict[str, Any]], target: int = 3) -> list[dict[str, Any]]:
    """按镜数均分「拍」：12 镜 → 3 拍。拍是节奏单位，不是节拍码单位。

    直接按节拍码切会切出七八拍（HK/ES/TW/CF/SF…十几镜全是不同码），
    读起来像分镜表复述，不像镜头语言。这里按镜数分档，每拍只标它覆盖的节拍码。
    """
    n = len(shots)
    if n == 0:
        return []
    k = 1 if n < 4 else (2 if n < 6 else target)
    bounds = [round(n * i / k) for i in range(k + 1)]
    out: list[dict[str, Any]] = []
    for i in range(k):
        a, b = bounds[i], bounds[i + 1]
        if a >= b:
            continue
        codes = dedupe(beat_zh(shot.get("beat") or "") for shot in shots[a:b])
        out.append({
            "from": a + 1,
            "to": b,
            "label": "→".join(codes[:2]) if codes else "推进",
            "acts": [one_line(shot.get("action") or shot.get("content") or "", 13) for shot in shots[a:b]],
        })
    return out


def render_tail(
    story: dict[str, Any],
    materials: dict[str, Any],
    options: dict[str, Any],
) -> str:
    shots = story.get("shots", [])
    n = len(shots)
    out: list[str] = ["【镜头语言】"]

    claps = _claps(shots)
    out.append(f"本段 {n} 镜，讲的是「{one_line(shots[0].get('content') if shots else '', 18)}"
               f"…{one_line(shots[-1].get('content') if shots else '', 14)}」，分 {len(claps)} 拍：")
    ordinal = ["第一拍", "第二拍", "第三拍", "第四拍", "第五拍"]
    for i, g in enumerate(claps):
        label = ordinal[i] if i < len(ordinal) else f"第{i + 1}拍"
        acts = "；".join(g["acts"][:3]) + ("…" if len(g["acts"]) > 3 else "")
        out.append(f"{label}（镜{g['from']}–{g['to']}，{g['label']}）：{acts}")
    moving = [i for i, s in enumerate(shots, start=1) if (s.get("movement") or "").strip() not in ("固定", "", "—")]
    closeups = [i for i, s in enumerate(shots, start=1)
                if expand_framing(s.get("framing") or "") in ("大特写", "特写")]
    if moving:
        rhythm = f"全段 {n} 镜，{len(moving)} 镜有运动（镜{'、'.join(map(str, moving))}），其余全部固定"
    else:
        rhythm = f"全段 {n} 镜全部固定机位，不推不摇不跟"
    if closeups:
        rhythm += f"；特写只给 {'、'.join(f'镜{i}' for i in closeups)}，不再多给"
    out.append(rhythm + "。所有镜头都必须看清五官与手部，不许糊、不许过度虚化。")

    # ---- 光影氛围 ----
    out.append("")
    out.append("【光影氛围】")
    lights = dedupe(s.get("light") or "" for s in shots)
    colors = dedupe((s.get("color") or "").split("·")[0] for s in shots)
    if lights:
        out.append("；".join(lights[:3]) + "。")
    if colors:
        out.append(f"全段基调：{'、'.join(colors[:3])}。光比控制在 4:1–6:1 之间。")
    out.append("不许戏剧化强打光，不许整体柔化提亮，不许把脸拍成一片平光。")

    # ---- 声音设计 ----
    out.append("")
    out.append("【声音设计】")
    out.append("无BGM、无字幕、无水印。")
    said = 0
    for shot in shots:
        for item in shot.get("dialogue") or []:
            said += 1
            if item.get("kind") == "独白":
                out.append(f"镜内 {shot['no']}：{_dialogue_line(item)}")
    if not said:
        out.append("本段无一句台词，全靠环境音与动作音撑住。")
    sounds = dedupe(_sound_brief(s.get("sound") or "") for s in shots)
    if sounds:
        out.append(f"环境音 / 动作音：{'、'.join(sounds[:4])}。")
    out.append("节奏上先给静、再给响：切点前留 0.2s 的空气，声音不许压过表演。")

    # ---- 防崩 ----
    out.append("")
    out.append("【AI防崩约束词】")
    people = [p for p in (materials.get("people") or []) if not any(w in p for w in ("Q版", "小人"))]
    q_people = [p for p in (materials.get("people") or []) if any(w in p for w in ("Q版", "小人"))]
    constraints: list[str] = [
        f"真人共 {len(people)} 个（{'、'.join(people) or '—'}），不得增加；"
        + (f"{'、'.join(q_people)}只有观众看得见，不占真人数量。" if q_people else "无 Q 版小人。"),
        "场景物件只列上面那些，不得新增；图上没画出来的东西不许凭常识补。",
        "脸、发型、服装与参考图一致，不换脸、不换衣、不换配色。",
        "人物不畸形，手不粘连、不多指、不穿模。",
        "台词一字不改，没有台词的镜闭嘴无口型；内心独白是画外音，嘴部不动。",
    ]
    prop_refs = [r for r in materials.get("rows", []) if r.get("kind") == "prop" and r.get("file")]
    if prop_refs:
        constraints.append(
            f"道具只此几件：{'、'.join(r['name'] for r in prop_refs)}，每件只出现一次，不出现第二件。"
        )
    # 分镜表自己的风险登记，按「高」优先塞进约束
    risks = [r for r in story.get("risks", []) if r.get("text")]
    risks.sort(key=lambda r: 0 if r.get("level") == "高" else 1)
    for r in risks[:4]:
        fix = r.get("fix") or ""
        constraints.append(f"{r['shots']}：{r['text']}。处置：{fix}" if fix else f"{r['shots']}：{r['text']}。")
    if shots:
        last = one_line(shots[-1].get("action") or shots[-1].get("content") or "", 30)
        constraints.append(f"结尾停在这一瞬：{last}，不许多走一步，不许多加一个反应。")

    out.extend(f"{i}. {text}" for i, text in enumerate(constraints, start=1))

    # ---- 合规 ----
    out.append("")
    out.append("【合规约束词】")
    compliance = [r for r in story.get("risks", []) if "合规" in (r.get("kind") or "")]
    if compliance:
        out.append("；".join(f"{r['shots']}：{r['fix'] or r['text']}" for r in compliance) + "。")
    out.append(DEFAULT_COMPLIANCE)

    # ---- 参考 ----
    out.append("")
    out.append("【参考】")
    visual = [r for r in materials.get("rows", []) if r.get("kind") != "audio" and r.get("file")]
    out.append(
        f"视觉素材（共 {len(visual)} 张，上文已逐个引用，此处只列用途）："
        + "；".join(f"{r['placeholder']}={r['note']}" for r in visual)
    )
    audio = [r for r in materials.get("rows", []) if r.get("kind") == "audio" and r.get("file")]
    if audio:
        out.append("音频素材：")
        out.extend(f"{r['name']}音色参考 {r['placeholder']}" for r in audio)
    else:
        out.append("音频素材：本段无音色参考。")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------

def build_header(story: dict[str, Any], options: dict[str, Any]) -> dict[str, str]:
    shots = story.get("shots", [])
    dur = float(story.get("duration_target") or sum(s.get("duration") or 0 for s in shots))
    rng = story.get("range") or {}
    start = rng.get("start")
    end = rng.get("end")
    if start is None and shots:
        start = shots[0].get("start")
        end = shots[-1].get("end")

    said = [it for s in shots for it in (s.get("dialogue") or [])]
    chars = sum(len(it.get("line") or "") for it in said)

    ep = int(story.get("ep") or 0)
    no = int(story.get("no") or 0)
    chunk = int(options.get("chunk_index") or 1)
    seg_name = f"{no:02d}_{dur:.0f}_{chunk:02d}"

    tail = one_line(shots[-1].get("action") if shots else "", 26)
    version_date = options.get("date") or date.today().isoformat()

    return {
        "seg_name": seg_name,
        "file_stem": f"{seg_name}_{int(start or 0):03d}-{int(end or 0):03d}s",
        "title": f"# {seg_name} · {_fmt_sec(start or 0)}–{_fmt_sec(end or 0)}s（Seedance 三段式）",
        "meta": "\n".join([
            "> 依据：三段式模板——前缀参考素材+风格+环境 → 正文 → 尾部模板",
            f"> 本段：{dur:.1f}s / **{len(shots)} 镜** / **{len(said)} 处发声**（{chars}字，一字未改）",
            f"> 集段：第 {ep} 集 第 {no} 段",
            f"> 承接：{story.get('carry_in') or '未在分镜表里写明'}",
            f"> 收在：{tail or '未在分镜表里写明'}",
            "> 内容属性：AI 辅助生成的虚构创作，仅供拍摄参考",
            "> AIGC: true",
            f"> 版本：v1（{version_date}）",
        ]),
    }


def render_full(
    story: dict[str, Any],
    materials: dict[str, Any],
    options: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """渲染整份三段式，返回分块结果（界面按块切换、按块复制）。"""
    options = dict(options or {})
    head = build_header(story, options)
    prefix = render_prefix(story, materials, options)
    body = render_body(story, options)
    tail = render_tail(story, materials, options)

    tables = {
        "materials": render_materials_table(materials),
        "timeline": render_timeline_table(story),
        "dialogue": render_dialogue_table(story),
    }

    parts = [
        head["title"],
        "",
        head["meta"],
        "",
        "---",
        "",
        "## 素材表",
        "",
        tables["materials"],
        "",
        "## 分镜时间动作表",
        "",
        tables["timeline"],
        "",
        "## 台词表",
        "",
        tables["dialogue"],
        "",
        "---",
        "",
        "## 前缀参考素材+风格+环境",
        "",
        "```",
        prefix,
        "```",
        "",
        "---",
        "",
        "## 正文",
        "",
        "```",
        body,
        "```",
        "",
        "---",
        "",
        "## 尾部模板",
        "",
        "```",
        tail,
        "```",
        "",
    ]

    issues = list(story.get("issues") or [])
    # 音色没配不算问题：本段可以没有音色参考
    unmatched = [
        r for r in materials.get("rows", [])
        if r.get("missing") and r.get("source") != "ignored" and r.get("kind") != "audio"
    ]
    for row in unmatched:
        issues.append({
            "kind": "MATERIAL_UNBOUND",
            "field": row["placeholder"],
            "detail": f"{row['name']} 没有匹配到文件，素材表里留空",
        })
    if story.get("status") == "NO_TABLE":
        issues.append({"kind": "NO_TABLE", "field": "", "detail": "分镜表没解析出来，先补主表"})

    return {
        "id": story.get("id"),
        "file_stem": head["file_stem"],
        "seg_name": head["seg_name"],
        "title": head["title"].lstrip("# ").strip(),
        "meta": head["meta"],
        "tables": tables,
        "prefix": prefix,
        "body": body,
        "tail": tail,
        "full": "\n".join(parts),
        "issues": issues,
        "stats": {
            "shots": len(story.get("shots") or []),
            "duration": float(story.get("duration_target") or 0),
            "spoken": sum(len(s.get("dialogue") or []) for s in story.get("shots") or []),
            "materials": len(materials.get("rows") or []),
            "unbound": len(unmatched),
        },
    }
