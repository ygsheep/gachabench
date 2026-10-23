# -*- coding: utf-8 -*-
"""提示词工程服务：段列表、解析、渲染、落盘。

和旧 `splitter.compose_segment` 的分工：
- 旧的按「15s 拆 3 块」切正文，服务于跑片；
- 这里按「分镜表 → 三段式」重写一份给人看的提示词，服务于写作与定稿。
两者都只**读**源 md，谁也不改别人。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from config import Config
from modules.prompts import bindings as bindings_mod
from modules.prompts import checks, patch
from modules.prompts import materials as materials_mod
from modules.prompts import seedance, storyboard

PROMPT_SUFFIX = "_三段式.md"

_STYLE_RE = re.compile(r"【风格】\s*\n(.+?)(?=\n【|\n```|\Z)", re.DOTALL)
_SCENE_RE = re.compile(r"【场景环境】\s*\n(.+?)(?=\n【|\n```|\Z)", re.DOTALL)
_ERA_RE = re.compile(r"(\d{4}\s*年[^，。；\n的]{0,16})")


# 提示词文件名里的段号前缀。两套命名并存：
#  老式：`01_15_01_000-015s.md`  → 段号 01（前缀 `01_`）
#  新式：`07-1_打谷场_视频提示词.md` → 段号 07、子段 1
#         `08_03_镇上买烟_视频提示词.md` → 段号 08、子段 03
# 两者段号都在开头两三位数字，跟后面的内容用 `_` 或 `-` 隔开。
_SEG_PREFIX_RE = re.compile(r"^(\d{1,3})(?:[_-](\d{1,2}))?(?=[_-])")
# 文件类型：参考图提示词 / 视频提示词 / 三段式 / 其他
_REF_IMAGE_RE = re.compile(r"参考图|参考图提示词|外景参考图|SD5")
_VIDEO_RE = re.compile(r"视频提示词|视频")


def _prompt_dir(cfg: Config) -> Optional[Path]:
    p = cfg.subdir("prompt")
    return p if p is not None and p.is_dir() else None


def _prompt_file_kind(name: str) -> str:
    """按文件名判断提示词 md 的类型：`ref`（参考图）/ `video`（视频）/ `template` / `other`。"""
    stem = Path(name).stem
    if stem.startswith("提示词模板"):
        return "template"
    if _REF_IMAGE_RE.search(stem):
        return "ref"
    if _VIDEO_RE.search(stem):
        return "video"
    if stem.endswith("_三段式"):
        return "template"
    return "other"


def _prompt_seg_no(name: str) -> Optional[int]:
    """从提示词文件名里提段号（老式 `01_15_...` → 1，新式 `07-1_...` → 7）。"""
    m = _SEG_PREFIX_RE.match(Path(name).stem)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def list_segment_files(cfg: Config, seg_no: int) -> list[dict[str, Any]]:
    """某一段在 `提示词/` 目录里关联的所有 md 文件，按类型分类。

    返回 [{name, kind, sub, scene}]，`kind` ∈ ref/video/template/other。
    同一「段 + 子段 + 场景」的参考图提示词与视频提示词靠 kind 配对（前端成组展示）。
    """
    prompt_dir = _prompt_dir(cfg)
    if prompt_dir is None:
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(prompt_dir.glob("*.md")):
        stem = p.stem
        # 跳过模板与备份/临时目录里的（这里只 glob 顶层，天然不含 _backup 子目录）
        if stem.startswith("提示词模板"):
            continue
        no = _prompt_seg_no(stem)
        if no != seg_no:
            continue
        # 子段与场景：新式命名 `07-1_打谷场` / `08_03_镇上买烟`；老式没有子段/场景。
        m = _SEG_PREFIX_RE.match(stem)
        sub = m.group(2) if m and m.group(2) else ""
        rest = stem[m.end():] if m else stem
        # 场景 = 去掉类型后缀与杂项后的中间段（`打谷场`、`镇上买烟`），老式给空。
        # 按「最长优先」清：外景参考图提示词 → 参考图提示词 → 视频提示词 → SD5.0 → 三段式
        scene = rest
        for kw in (
            "外景参考图提示词", "参考图提示词", "视频提示词",
            "参考图", "SD5.0", "SD5", "三段式",
        ):
            scene = scene.replace(kw, "")
        scene = scene.strip("_- .0123456789")
        out.append({
            "name": p.name,
            "kind": _prompt_file_kind(stem),
            "sub": sub,
            "scene": scene,
        })
    return out


def _storyboard_dir(cfg: Config) -> Optional[Path]:
    return cfg.subdir("storyboard")


def list_segments(cfg: Config) -> list[dict[str, Any]]:
    """工作区里所有能解析的分镜段，按集、段排序。"""
    root = cfg.ws_root()
    if root is None:
        return []
    files = storyboard.find_storyboards(root, _storyboard_dir(cfg))
    prompt_dir = _prompt_dir(cfg)
    out: list[dict[str, Any]] = []
    for path in files:
        try:
            parsed = storyboard.read_storyboard(path, root)
        except OSError:
            continue
        no = int(parsed.get("no") or 0)
        dur = float(parsed.get("duration_target") or 0)
        prefix = f"{no:02d}_{dur:.0f}_"
        prompt_file = ""
        if prompt_dir is not None:
            hit = sorted(prompt_dir.glob(f"{prefix}*.md"))
            if hit:
                prompt_file = hit[0].name
        out.append({
            "id": parsed["id"],
            "ep": parsed.get("ep") or 0,
            "no": no,
            "title": parsed.get("title") or path.stem,
            "source_path": parsed.get("source_path") or "",
            "range": parsed.get("range"),
            "duration": dur,
            "shots": len(parsed.get("shots") or []),
            "spoken": sum(len(s.get("dialogue") or []) for s in parsed.get("shots") or []),
            "status": parsed.get("status"),
            "issues": len(parsed.get("issues") or []),
            "prompt_file": prompt_file,
            # 这一段在「提示词/」里关联的所有 md（参考图/视频/三段式/其他，按类型分类）
            "files": list_segment_files(cfg, no),
        })
    out.sort(key=lambda s: (s["ep"], s["no"], s["source_path"]))
    return out


def _find_source(cfg: Config, seg_id: str) -> Path:
    root = cfg.ws_root()
    if root is None:
        raise ValueError("未激活任何工作区")
    files = storyboard.find_storyboards(root, _storyboard_dir(cfg))
    for path in files:
        ep_m = storyboard.EP_RE.search(path.name)
        no_m = storyboard.NO_RE.search(path.name)
        if not ep_m or not no_m:
            continue
        if seg_id in (f"{int(ep_m.group(1))}-{int(no_m.group(1))}", path.stem):
            return path
    # 允许直接传段号（"04" / "4"）
    digits = re.sub(r"\D", "", seg_id)
    if digits:
        want = int(digits)
        hits = [p for p in files if (storyboard.NO_RE.search(p.name) or [None, "0"])[1] == str(want)]
        if hits:
            return hits[0]
    raise ValueError(f"没有 ID 为 {seg_id} 的分镜脚本")


def load_segment(cfg: Config, seg_id: str) -> dict[str, Any]:
    """解析 + 素材绑定。返回 storyboard、materials、可选上下文。"""
    root = cfg.ws_root()
    if root is None:
        raise ValueError("未激活任何工作区")
    path = _find_source(cfg, seg_id)
    parsed = storyboard.read_storyboard(path, root)
    text = parsed.pop("text", "")
    files = materials_mod.scan_asset_files(root, cfg.subdir("asset"))
    overrides, ignored = _manual_bindings(root, parsed["id"])
    bound = materials_mod.resolve_materials(parsed, files, overrides, ignored)
    return {
        "storyboard": parsed,
        "materials": bound,
        "context": inherit_context(cfg),
        "size": {"text": len(text), "assets": len(files)},
    }


def _manual_bindings(root: Path, segment_id: str) -> tuple[dict[str, str], set[str]]:
    """库里的人工绑定 → (占位→绝对路径, 被忽略的占位)。

    存的是工作区相对路径：工作区整体搬走后仍然有效，也不怕盘符变化。
    """
    rows = bindings_mod.load_bindings(root, segment_id)
    overrides: dict[str, str] = {}
    ignored: set[str] = set()
    for placeholder, row in rows.items():
        if row.get("ignored"):
            ignored.add(placeholder)
        elif row.get("file"):
            overrides[placeholder] = str(root / Path(row["file"]))
    return overrides, ignored


def inherit_context(cfg: Config) -> dict[str, Any]:
    """从 `提示词/` 里最近一份带【风格】的 md 继承风格与场景段。

    同一个场景连着写十几段，风格和屋里有什么每段都要重打一遍不现实。
    继承的是**文字**，不是结论：拿回来还要人看一眼再改。
    """
    prompt_dir = _prompt_dir(cfg)
    if prompt_dir is None:
        return {"style": "", "scene": "", "era_region": "", "file": ""}
    candidates = sorted(
        (p for p in prompt_dir.glob("*.md") if not p.name.startswith("提示词模板")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        style_m = _STYLE_RE.search(text)
        if not style_m:
            continue
        scene_m = _SCENE_RE.search(text)
        style = style_m.group(1).strip()
        scene = scene_m.group(1).strip() if scene_m else ""
        era = _ERA_RE.search(style)
        return {
            "style": style,
            "scene": scene,
            "era_region": era.group(1).strip() if era else "",
            "file": path.name,
        }
    return {"style": "", "scene": "", "era_region": "", "file": ""}


def render_segment(
    cfg: Config,
    seg_id: str,
    options: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    data = load_segment(cfg, seg_id)
    story = data["storyboard"]
    opts = dict(options or {})
    ctx = data["context"]
    # 没显式给风格就继承项目里已经定下来的那份，避免每段风格漂移
    if not opts.get("style") and ctx.get("style"):
        opts["style"] = ctx["style"]
    if not opts.get("scene") and ctx.get("scene"):
        opts["scene"] = ctx["scene"]
    if not opts.get("era_region") and ctx.get("era_region"):
        opts["era_region"] = ctx["era_region"]
    opts.setdefault("aspect", story.get("aspect") or "16:9")

    rendered = seedance.render_full(story, data["materials"], opts)
    rendered["source_path"] = story.get("source_path")
    rendered["context"] = ctx
    rendered["storyboard"] = story
    rendered["materials"] = data["materials"]
    return rendered


def save_prompt(
    cfg: Config,
    seg_id: str,
    content: str,
    filename: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    """把渲染结果写到 提示词/。文件名默认 `NN_时长_01_start-end s_三段式.md`。"""
    prompt_dir = _prompt_dir(cfg)
    root = cfg.ws_root()
    if prompt_dir is None or root is None:
        raise ValueError("工作区没有 提示词/ 目录")
    rendered = render_segment(cfg, seg_id)
    name = (filename or f"{rendered['file_stem']}{PROMPT_SUFFIX}").strip()
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("文件名不能带路径")
    if not name.lower().endswith(".md"):
        name += ".md"
    target = prompt_dir / name
    if target.exists() and not overwrite:
        raise FileExistsError(name)
    target.write_text(content, encoding="utf-8")
    return {
        "path": target.relative_to(root).as_posix(),
        "name": name,
        "bytes": len(content.encode("utf-8")),
    }


def list_files(cfg: Config, kind: str = "image", q: str = "") -> list[dict[str, str]]:
    """可供人工指定的素材清单（工作区相对路径）。"""
    root = cfg.ws_root()
    if root is None:
        return []
    files = materials_mod.scan_asset_files(root, cfg.subdir("asset"))
    if kind == "audio":
        want = materials_mod.AUDIO_EXT
    elif kind == "image":
        want = materials_mod.IMAGE_EXT
    else:
        want = materials_mod.IMAGE_EXT | materials_mod.AUDIO_EXT
    needle = (q or "").strip().lower()
    out: list[dict[str, str]] = []
    for path in files:
        if path.suffix.lower() not in want:
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if needle and needle not in rel.lower():
            continue
        out.append({
            "path": rel,
            "name": path.name,
            "dir": Path(rel).parent.as_posix(),
        })
    return out


def set_binding(
    cfg: Config,
    seg_id: str,
    placeholder: str,
    file: str = "",
    ignored: bool = False,
    name: str = "",
) -> dict[str, Any]:
    """人工指定 / 忽略 / 恢复自动。返回这条绑定的新状态。"""
    root = cfg.ws_root()
    if root is None:
        raise ValueError("未激活任何工作区")
    path = _find_source(cfg, seg_id)
    parsed = storyboard.read_storyboard(path, root)
    segment_id = parsed["id"]

    rel = ""
    if file:
        raw = Path(file)
        candidate = raw if raw.is_absolute() else (root / raw)
        try:
            resolved = candidate.resolve()
            rel = resolved.relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            raise ValueError("只能指定工作区内的文件")
        if not resolved.is_file():
            raise ValueError(f"文件不存在：{rel}")

    state = bindings_mod.set_binding(
        root, segment_id, placeholder, file=rel, ignored=ignored, name=name
    )
    return {
        "segment": segment_id,
        "placeholder": placeholder,
        "binding": state,
        "cleared": state is None,
    }


def check_segment(
    cfg: Config,
    seg_id: str,
    content: Optional[str] = None,
) -> dict[str, Any]:
    """诊断。`content` 为空时诊断「按当前分镜表现渲染出来的那一份」。"""
    data = load_segment(cfg, seg_id)
    if content is None:
        content = render_segment(cfg, seg_id)["full"]
        source = "render"
    else:
        source = "draft"
    findings = checks.check_prompt(content, data["storyboard"])
    return {
        "source": source,
        "chars": len(content),
        "issues": findings,
        "summary": checks.summarize(findings),
    }


def patch_segment(
    cfg: Config,
    seg_id: str,
    content: str,
    codes: Optional[list[str]] = None,
) -> dict[str, Any]:
    """由诊断结论攒补丁，并把「改完还剩哪些问题」一起算好。"""
    data = load_segment(cfg, seg_id)
    story = data["storyboard"]
    findings = checks.check_prompt(content, story)
    result = patch.build_patch(content, findings, codes)
    after = checks.check_prompt(result["patched"], story)
    result["issues_before"] = findings
    result["issues_after"] = after
    result["summary_before"] = checks.summarize(findings)
    result["summary_after"] = checks.summarize(after)
    return result
