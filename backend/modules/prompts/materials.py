# -*- coding: utf-8 -*-
"""素材绑定：把分镜表里的「人 / 场景 / 道具 / 音色」对上工作区里的具体文件，并分配 `@占位`。

占位规则沿用三段式体例：`@人物1`、`@场景1`、`@道具1`、`@音色1`。
- 编号按**首次出现顺序**生成，一经生成不再重排；
- 匹配不到的保留占位，`file` 置空、`missing=True`，由人在界面上指定；
- 真实文件名只出现在最前面的素材表里，正文一律用占位。

匹配策略：先「归一化后整串包含」，再用服装/道具里的用途词加权，最后比名字长度与集号。
宁可选错让人一眼看出来，也不要为了"总能配上"去模糊匹配 —— 配错的代价是整段跑飞。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Optional

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac"}

SKIP_DIR_NAMES = {
    "生成", "草稿", "原著", "final", "draft", "source",
    ".gachabench", ".workbuddy", "node_modules", "历史版本", "导出", "视图",
}
ASSET_DIR_CANDIDATES = ("素材", "资产", "素材库", "资产库", "assets", "参考图", "refs")

SCENE_NOISE = re.compile(r"^(SC\d+\s*)?")
SCENE_WORDS = {
    "内景", "外景", "内", "外", "夜", "日", "晨", "昏", "黄昏", "白天", "晚上",
    "SC01", "SC02", "SC03",
}
NIGHT_WORDS = ("夜", "晚上", "晚间", "灯灭")
DAY_WORDS = ("白天", "日景", "清晨", "傍晚")

# 这些词出现在道具列时不算独立道具（是场景构成或角色本身）
PROP_STOPWORDS = ("小人", "Q版", "群演", "替身", "影")

# 文件名里带这些字的多半是「一个地方」，不能当道具图用
PLACE_HINT_RE = re.compile(r"屋|房|院|店|街|田|厂|摊|铺|场地|作坊|处|室内|门口|墙|路|场")


# ---------------------------------------------------------------------------
# 素材扫描
# ---------------------------------------------------------------------------

def scan_asset_files(root: Path, configured: Optional[Path] = None) -> list[Path]:
    """工作区里的参考素材（图片 + 音频），两层深，跳过成品/草稿/原著。

    同名文件只留一份：资产库和素材目录常常互为副本，留两份会让匹配随手挑中错的那个。
    """
    folders: list[Path] = []
    if configured is not None and configured.is_dir():
        folders.append(configured)
    for name in ASSET_DIR_CANDIDATES:
        p = root / name
        if p.is_dir() and p not in folders:
            folders.append(p)

    out: list[Path] = []
    seen_names: dict[str, Path] = {}
    for folder in folders:
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in IMAGE_EXT | AUDIO_EXT:
                continue
            rel_parts = path.relative_to(folder).parts
            if any(part in SKIP_DIR_NAMES for part in rel_parts[:-1]):
                continue
            key = path.name.lower()
            prev = seen_names.get(key)
            # 同名副本：留路径更短的那份（通常就是主素材目录里的原件）
            if prev is None or len(str(path)) < len(str(prev)):
                seen_names[key] = path
    out = sorted(seen_names.values(), key=lambda p: p.name)
    return out


def _norm(text: str) -> str:
    return re.sub(r"[\s_\-·（）()\[\]【】,，。.、]+", "", (text or "")).lower()


def _char_overlap(needle: str, haystack: str) -> float:
    chars = {c for c in needle if "\u4e00" <= c <= "\u9fff"}
    if not chars:
        return 0.0
    return sum(1 for c in chars if c in haystack) / len(chars)


def _tokens(text: str) -> list[str]:
    """从「白开襟睡衣·深蓝长裤·素颜」里取用途词：睡衣 / 开襟 / 长裤。"""
    out: list[str] = []
    for part in re.split(r"[·・、,，;；/\s\-—]+", text or ""):
        part = part.strip()
        if len(part) >= 2 and part not in ("素颜", "淡妆", "无妆"):
            out.append(part)
            # 长一点的词再切一刀，命中「开襟睡衣」这种组合
            if len(part) >= 4:
                out.append(part[-2:])
    return out


def best_match(
    name: str,
    files: list[Path],
    hints: Iterable[str] = (),
    ep: int = 0,
) -> Optional[Path]:
    """文件名里必须含这个人/物的名字（归一化后整串包含），再用用途词加权。"""
    key = _norm(name)
    if not key:
        return None
    cands = [p for p in files if key in _norm(p.stem)]
    if not cands:
        return None
    hint_list = [h for h in (_norm(x) for x in hints) if h]
    ep_prefix = (str(ep), f"{ep:02d}") if ep else ()

    def score(path: Path) -> tuple[float, float, int]:
        stem = _norm(path.stem)
        hint_hit = sum(1 for h in hint_list if h in stem)
        ep_hit = 1 if ep_prefix and any(stem.startswith(p) or f"-{p}" in stem for p in ep_prefix) else 0
        return (hint_hit, ep_hit, -len(stem))

    return max(cands, key=score)


# ---------------------------------------------------------------------------
# 从分镜表里抽「人名 / 地点 / 道具」
# ---------------------------------------------------------------------------

def extract_people(story: dict[str, Any]) -> list[dict[str, str]]:
    """从「服装·妆造」列与台词抽人物，返回 `[{name, hints}]`，按首次出现排序。

    服装列体例：`顾砚书-白开襟睡衣·深蓝长裤·素颜；云天娇-浅蓝碎花睡衣·素颜`
    """
    order: list[dict[str, str]] = []
    seen: dict[str, dict[str, str]] = {}

    def push(name: str, hints: str) -> None:
        name = re.sub(r"^Q\s*版", "Q版", name.strip())
        if not name or len(name) > 8:
            return
        if name in seen:
            if hints and not seen[name]["hints"]:
                seen[name]["hints"] = hints
            return
        item = {"name": name, "hints": hints}
        seen[name] = item
        order.append(item)

    for shot in story.get("shots", []):
        raw = shot.get("costume") or ""
        for part in re.split(r"[；;\n]+", raw):
            part = part.strip()
            if not part or part in ("—", "-", "无"):
                continue
            name = re.split(r"[-—:：]", part)[0].strip()
            push(name, part)
        for item in shot.get("dialogue") or []:
            if item.get("who"):
                push(item["who"], "")
    return order


def extract_scene(story: dict[str, Any]) -> dict[str, str]:
    """场次 → `{name, time}`。name 去掉 SCxx 与「内/夜」，time 取 `夜` 或 `日`。"""
    raw = ""
    for shot in story.get("shots", []):
        if shot.get("scene"):
            raw = shot["scene"]
            break
    body = SCENE_NOISE.sub("", raw).strip()
    parts = [p.strip() for p in re.split(r"[·・/｜|]", body) if p.strip()]
    keep = [p for p in parts if p not in SCENE_WORDS]
    time = ""
    if any(w in body for w in NIGHT_WORDS):
        time = "夜"
    elif any(w in body for w in DAY_WORDS):
        time = "日"
    return {"name": "·".join(keep) or body, "time": time, "raw": raw}


def extract_props(story: dict[str, Any]) -> list[str]:
    """道具列逐件抽，去掉括号说明与角色本身（Q 版小人不是道具）。"""
    order: list[str] = []
    for shot in story.get("shots", []):
        raw = shot.get("props") or ""
        if not raw or raw in ("—", "-", "无"):
            continue
        for part in re.split(r"[、，,；;/\n]+", raw):
            part = re.sub(r"（[^）]*）|\([^)]*\)", "", part).strip()
            if not part or part in ("—", "-", "无"):
                continue
            if any(w in part for w in PROP_STOPWORDS):
                continue
            if part not in order:
                order.append(part)
    return order


# ---------------------------------------------------------------------------
# 绑定
# ---------------------------------------------------------------------------

def _scene_rank(path: Path, scene_time: str) -> tuple[int, int, int]:
    """场景图排序键：主视角(1) → 反打(2) → 床区/帐内(3) → 其他(4)。

    同档里先看时间对不对（夜戏别拿白天图），再看名字长短。
    """
    stem = _norm(path.stem)
    if "反打" in stem:
        rank = 2
    elif "蚊帐" in stem or ("帐" in stem and "屋" not in stem) or ("床区" in stem):
        rank = 3
    elif any(k in stem for k in ("屋", "房间", "室内", "场景")):
        rank = 1
    else:
        rank = 4
    time_hit = 0
    if scene_time == "夜" and any(w in stem for w in ("晚上", "夜", "灯")):
        time_hit = -1
    elif scene_time == "日" and any(w in stem for w in ("白天", "日", "晨", "傍晚")):
        time_hit = -1
    return (rank, time_hit, len(stem))


def _scene_role(rank: int) -> str:
    return {1: "主视角", 2: "反打", 3: "关键场景"}.get(rank, "关键场景")


def resolve_materials(
    story: dict[str, Any],
    files: list[Path],
    overrides: Optional[dict[str, str]] = None,
    ignored: Optional[set[str]] = None,
) -> dict[str, Any]:
    """生成素材表 + 名称→占位映射。

    - `overrides`：`{占位: 文件路径}`，人工指定，优先级最高；
    - `ignored`：被人工忽略的占位集合，编号照样占位、不回收，只是不配图。

    每行都带 `source`：`manual` / `auto` / `missing` / `ignored`。
    界面靠它标「手动 / 自动 / 已忽略 / 待指定」，渲染靠它决定素材表里写什么。
    """
    overrides = overrides or {}
    ignored = ignored or set()
    images = [p for p in files if p.suffix.lower() in IMAGE_EXT]
    audios = [p for p in files if p.suffix.lower() in AUDIO_EXT]
    ep = int(story.get("ep") or 0)

    people = extract_people(story)
    scene = extract_scene(story)
    props = extract_props(story)

    rows: list[dict[str, Any]] = []
    name_to_ph: dict[str, str] = {}
    used: set[str] = set()

    def add(
        placeholder: str,
        name: str,
        path: Optional[Path],
        note: str,
        kind: str,
    ) -> None:
        source = "auto"
        if placeholder in ignored:
            path, note, source = None, "已忽略", "ignored"
        else:
            manual = overrides.get(placeholder)
            if manual:
                candidate = Path(manual)
                if candidate.is_file():
                    path, source, note = candidate, "manual", "手动指定"
                else:
                    path, note, source = None, "指定的文件不见了，请重选", "missing"
            elif path is None:
                source = "missing"
        rows.append({
            "placeholder": placeholder,
            "name": name,
            "file": path.name if path else "",
            "note": note,
            "kind": kind,
            "missing": path is None,
            "source": source,
        })
        if name:
            name_to_ph.setdefault(name, placeholder)
        if path is not None:
            used.add(path.name.lower())

    def free(pool: list[Path]) -> list[Path]:
        return [p for p in pool if p.name.lower() not in used]

    # 1) 人物：人工指定 > 名字命中 + 用途词加权
    pi = 0
    for person in people:
        pi += 1
        name = person["name"]
        path = best_match(name, free(images), _tokens(person["hints"]), ep)
        add(f"@人物{pi}", name, path, "角色三视图" if path else "未匹配，请指定", "person")

    # 2) 人脸备用图：名字带「大头/面部/脸」的挂到第一个真人后面
    main_people = [p for p in people if not any(w in p["name"] for w in ("Q版", "小人"))]
    if main_people:
        spare = next((p for p in free(images) if any(k in p.name for k in ("大头", "面部", "脸"))), None)
        if spare is not None:
            rows.append({
                "placeholder": "@人物1备用",
                "name": main_people[0]["name"],
                "file": spare.name,
                "note": "脸型漂移时启用",
                "kind": "person_backup",
                "missing": False,
                "source": "auto",
            })
            used.add(spare.name.lower())

    # 3) 场景：按「主视角 → 反打 → 床区」排，最多 3 张
    pool = free(images)
    if scene["name"]:
        hit = [p for p in pool if _char_overlap(scene["name"], _norm(p.stem)) >= 0.6]
    else:
        hit = []
    if not hit:
        hit = [p for p in pool if any(k in p.name for k in ("屋", "房间", "场景", "室内", "室外"))]
    hit.sort(key=lambda p: _scene_rank(p, scene["time"]))
    # 每个机位档先取一张（主视角 / 反打 / 床区），再按顺序补第二张。
    # 否则同档的两张主视角会把反打和床区挤出前三位，正文里就没有「照图搬用」的依据了。
    ordered: list[Path] = []
    by_rank: dict[int, list[Path]] = {}
    for path in hit:
        by_rank.setdefault(_scene_rank(path, scene["time"])[0], []).append(path)
    for rank in sorted(by_rank):
        ordered.append(by_rank[rank][0])
    for rank in sorted(by_rank):
        ordered.extend(by_rank[rank][1:])
    for i, path in enumerate(ordered[:3], start=1):
        add(f"@场景{i}", scene["name"], path, _scene_role(_scene_rank(path, scene["time"])[0]), "scene")

    # 4) 道具：名字命中文件名才算，且不能是一张「场景图」（被子 ≠ 弹棉花的作坊）。
    #    没配到图的道具（帐纱、被子这类场景自带物件）不占位，否则素材表里全是空位；
    #    真要给它单独配图，把文件名写清道具名，下次自动匹配就会认。
    di = 0
    for prop in props:
        path = best_match(prop, free(images), _tokens(prop), ep)
        if path is None or PLACE_HINT_RE.search(path.stem):
            continue
        di += 1
        add(f"@道具{di}", prop, path, "道具", "prop")

    # 5) 音色：一个真人一条，行照留（没 mp3 时可指定或忽略）
    for i, person in enumerate(people, start=1):
        path = best_match(person["name"], free(audios), (), ep)
        add(f"@音色{i}", person["name"], path, "音色参考" if path else "未匹配，可指定或忽略", "audio")

    return {
        "rows": rows,
        "name_to_placeholder": name_to_ph,
        "scene_name": scene["name"],
        "scene_time": scene["time"],
        "people": [p["name"] for p in people],
        "props": props,
        "unmatched": [r for r in rows if r["missing"] and r["source"] != "ignored"],
        "ignored": [r for r in rows if r["source"] == "ignored"],
    }
