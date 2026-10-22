# -*- coding: utf-8 -*-
"""资产库 API（SPEC §273）。

- 目录口径：`{asset}` 目录（工作区级，目录名可自动匹配「素材/资产/assets…」）
- `GET  /api/assets`         递归扫描 → 落库 → 从库读取，按主体分组返回
- `POST /api/assets/scan`    只重扫入库，返回增删计数
- `GET  /api/assets/target`  上传前的「落位预览」：算出一个主体该放哪个目录
- `POST /api/assets/upload`  上传并按 `主体名_用途` 落名；自动定位到该主体已有目录
- `GET  /api/assets/file/{rel}`  预览流（图片/音频/视频，严格防路径穿越）

分组口径：文件名按 `_` 与空格切分，第一段 = 主体名（人物名或场景名）。
- 主体名含「实拍」标签 → 场景
- 主体名为「风格」 → 风格参考
- 主体名含「QQ空间」 → 其他
- 其余（含配音）→ 人物（一个人物 = 一套：四视图 / 近景 / Q版 / 配音 …）
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from config import get_config
from db import get_db
from repo import asset_dirs_for_subject, list_assets, prune_assets, upsert_asset

router = APIRouter(prefix="/api/assets", tags=["assets"])

IMAGE_MEDIA = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".bmp": "image/bmp", ".gif": "image/gif",
}
AUDIO_MEDIA = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".flac": "audio/flac", ".ogg": "audio/ogg",
}
VIDEO_MEDIA = {
    ".mp4": "video/mp4", ".mov": "video/quicktime",
    ".webm": "video/webm", ".mkv": "video/x-matroska",
}

# 类别展示顺序（key, 标签）
CATEGORIES = [
    ("character", "人物"),
    ("scene", "场景"),
    ("style", "风格参考"),
    ("other", "其他"),
]

# 新建主体时，按类别建同名目录（上传接口的 category 字段）
CATEGORY_LABEL = {k: label for k, label in CATEGORIES}

_SPLIT_RE = re.compile(r"[_\s]+")
_KIND_RANK = {"image": 0, "video": 1, "audio": 2, "other": 3}

# 文件名里不允许出现的字符（Windows 保留字符 + 控制字符）
_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# 用途（后缀）留空时的默认值，对齐库里已有命名：
#   图/视频 → 参考（风格_国漫3D_参考.webp）；音频 → 配音（白陌_配音.mp3）
DEFAULT_SUFFIX = {"image": "参考", "audio": "配音", "video": "参考"}


def _dedup_name(stem: str, ext: str, i: int) -> str:
    """重名追加的编号格式，对齐库里已有的 风格_国漫3D_参考_02.webp"""
    return "%s_%02d%s" % (stem, i, ext)


def _media_type(ext: str) -> tuple[str, str] | None:
    if ext in IMAGE_MEDIA:
        return "image", IMAGE_MEDIA[ext]
    if ext in AUDIO_MEDIA:
        return "audio", AUDIO_MEDIA[ext]
    if ext in VIDEO_MEDIA:
        return "video", VIDEO_MEDIA[ext]
    return None


def _context():
    """返回 (cfg, ws_root, ws_name, asset_base)。资产目录未确定时给 409。

    base 一律 resolve()：`_resolve_target` 要拿它跟 resolve() 过的目标做前缀比较，
    两边形态不一致时 `is_relative_to` 会静默失败（文件悄悄落到资产根而不是类别目录）。
    实测触发条件：工作区路径里含 8.3 短名（TEMP 常见的 YOUNG~1.YOU）、符号链接或映射盘。
    """
    cfg = get_config()
    root = cfg.ws_root()
    if root is None or not cfg.active:
        raise HTTPException(status_code=400, detail="未激活任何工作区")
    base = cfg.subdir("asset")
    if base is None:
        raise HTTPException(
            status_code=409,
            detail="当前工作区还没指定资产目录，请在「新建 / 打开项目」里选择",
        )
    return cfg, root, cfg.active, base.resolve()


def _split(stem: str) -> tuple[str, list[str]]:
    parts = [p for p in _SPLIT_RE.split(stem) if p]
    if not parts:
        return stem, []
    return parts[0], parts[1:]


def _category_of(subject: str, tags: list[str]) -> str:
    if any("实拍" in t for t in tags):
        return "scene"
    if subject == "风格":
        return "style"
    if "QQ空间" in subject:
        return "other"
    return "character"


def _scan(base: Path, root: Path, ws_name: str) -> dict:
    """递归扫描 {asset} 目录 → 幂等写入 assets 表 → 清掉已消失的记录。"""
    db = get_db(root)
    keep: set[str] = set()
    added = updated = 0
    base = base.resolve()
    if base.is_dir():
        for p in base.rglob("*"):
            if not p.is_file() or p.name.startswith("."):
                continue
            rel = p.relative_to(base).as_posix()
            keep.add(rel)
            ext = p.suffix.lower()
            mt = _media_type(ext)
            subject, tags = _split(p.stem)
            parent = p.parent.relative_to(base).as_posix()
            dir_rel = "" if parent == "." else parent
            try:
                st = p.stat()
            except OSError:
                continue
            created = upsert_asset(db, ws_name, {
                "rel_path": rel,
                "name": p.name,
                "dir_rel": dir_rel,
                "subject": subject,
                "tags": tags,
                "category": _category_of(subject, tags),
                "kind": mt[0] if mt else "other",
                "ext": ext,
                "size": st.st_size,
                "mtime": st.st_mtime,
            })
            added += int(created)
            updated += int(not created)
    removed = prune_assets(db, ws_name, keep)
    return {"scanned": len(keep), "added": added, "updated": updated, "removed": removed}


def _resolve_target(
    base: Path, root: Path, ws_name: str, subject: str, category: str, hint: str = ""
) -> tuple[Path, str]:
    """算出某个主体该落在哪个目录（相对 base）。

    顺序：① 前端给的 hint（attach 到某套已有目录）② 同名目录（浅→深）
    ③ 该主体已有文件所在目录（浅→深）④ 新主体 → `类别/主体`。
    """
    if hint:
        cand = (base / hint).resolve()
        if cand.is_relative_to(base):
            return cand, hint

    named = sorted(
        (p for p in base.rglob("*") if p.is_dir() and p.name == subject and not p.name.startswith(".")),
        key=lambda x: (len(x.relative_to(base).parts), str(x).lower()),
    ) if base.is_dir() else []
    if named:
        p = named[0]
        return p, p.relative_to(base).as_posix()

    dirs = asset_dirs_for_subject(get_db(root), ws_name, subject)
    if dirs:
        d = dirs[0]
        return (base / d).resolve() if d else base, d

    label = CATEGORY_LABEL.get(category, "人物")
    target = (base / label / subject).resolve()
    if target.is_relative_to(base):
        return target, f"{label}/{subject}"
    return base, ""


@router.get("/groups")
async def list_assets_api():
    """旧分组视图。新索引的分页列表在 `GET /api/assets`。"""
    cfg, root, ws_name, base = _context()
    _scan(base, root, ws_name)  # 每次都同步磁盘 → 库，保证列表不落灰

    db = get_db(root)
    buckets: dict[str, list] = {k: [] for k, _ in CATEGORIES}
    groups: dict[str, dict] = {}
    total = 0
    for a in list_assets(db, ws_name):
        if a["kind"] not in ("image", "video", "audio"):
            continue  # 非媒体文件（如 _env.py）不进展示，但索引仍在库里
        total += 1
        subject = a["subject"] or a["name"]
        g = groups.setdefault(subject, {
            "subject": subject, "items": [], "tags": set(), "category": a["category"],
        })
        g["tags"].update(a["tags"])
        g["items"].append({
            "rel": a["rel_path"],
            "name": a["name"],
            "dir": a["dir_rel"],
            "kind": a["kind"],
            "tags": a["tags"],
            "size": a["size"],
            "url": f"/api/assets/file/{quote(a['rel_path'])}",
        })

    for g in groups.values():
        g["items"].sort(key=lambda it: (_KIND_RANK.get(it["kind"], 9), it["name"]))
        buckets[g["category"]].append({
            "name": g["subject"],
            "tags": sorted(g["tags"]),
            "items": g["items"],
        })

    categories = []
    for key, label in CATEGORIES:
        gs = sorted(buckets[key], key=lambda x: x["name"])
        if gs:
            categories.append({"key": key, "label": label, "groups": gs})

    return {
        "root": str(base),
        "asset_dir": base.name,
        "total": total,
        "active_workspace": ws_name,
        "categories": categories,
    }


@router.post("/groups/scan")
async def rescan_assets():
    """旧同步重扫。新索引的异步扫描在 `POST /api/assets/scan`。"""
    _cfg, root, ws_name, base = _context()
    return {"ok": True, "asset_dir": base.name, **_scan(base, root, ws_name)}


@router.get("/target")
async def asset_target(
    subject: str = Query(""), category: str = Query("character"), subdir: str = Query("")
):
    """上传前的落位预览：这个主体会被放到哪个目录。"""
    subject = _sanitize_part(subject)
    if not subject:
        raise HTTPException(status_code=400, detail="主体名不能为空")
    _cfg, root, ws_name, base = _context()
    _target_dir, dir_rel = _resolve_target(base, root, ws_name, subject, category, subdir.strip())
    return {"subject": subject, "category": category, "dir": dir_rel}


def _sanitize_part(part: str) -> str:
    """清洗文件名片段：去掉保留字符、把连续空白并成一个空格、去掉首尾的点与空白。"""
    s = _ILLEGAL_RE.sub("", part or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" .")


@router.post("/upload")
async def upload_assets(
    files: list[UploadFile] = File(...),
    prefix: str = Form(""),
    suffix: str = Form(""),
    category: str = Form("character"),
    subdir: str = Form(""),
):
    """上传素材（图片 / 音频 / 视频），按 `{主体名}_{用途}{ext}` 自动落名。

    - 主体名（prefix）= 用户输入，决定归到哪一套资产
    - 用途（suffix）= 用户输入；留空按类型取默认值（图/视频 → 参考，音频 → 配音）
    - category = 人物 / 场景 / 风格参考 / 其他 —— 新主体建目录时用
    - subdir = 可选提示；留空则由后端**自动定位**该主体已有目录，否则新建 `类别/主体`
    - 从不覆盖：目标名被占用时追加 `_02`、`_03` …
    """
    _cfg, root, ws_name, base = _context()
    prefix = _sanitize_part(prefix)
    suffix = _sanitize_part(suffix)
    if not prefix and not suffix:
        raise HTTPException(status_code=400, detail="主体名不能为空")
    if category not in CATEGORY_LABEL:
        category = "character"

    # 自动定位目标目录（新建主体时先不落盘，等确认有可写入的文件再建）
    target, dir_rel = _resolve_target(base, root, ws_name, prefix or suffix, category, subdir.strip())

    items: list[dict] = []
    skipped: list[dict] = []
    for f in files:
        raw_name = Path(f.filename or "").name
        ext = Path(raw_name).suffix.lower()
        mt = _media_type(ext)
        if mt is None:
            skipped.append({
                "name": raw_name or "(无名)",
                "reason": "不支持的格式 %s" % (ext or "(无扩展名)"),
            })
            continue
        kind, _media = mt
        sfx = suffix or DEFAULT_SUFFIX[kind]
        stem = "%s_%s" % (prefix, sfx) if prefix else sfx

        dest = target / (stem + ext)
        deduped = False
        if dest.exists():
            deduped = True
            for i in range(2, 1000):
                cand = target / _dedup_name(stem, ext, i)
                if not cand.exists():
                    dest = cand
                    break
            else:
                skipped.append({"name": raw_name, "reason": "同名文件过多，请换个用途"})
                continue

        try:
            target.mkdir(parents=True, exist_ok=True)
            await f.seek(0)
            with dest.open("wb") as out:
                while True:
                    chunk = await f.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
        except OSError as exc:
            skipped.append({"name": raw_name, "reason": "写入失败：%s" % exc})
            continue

        rel = dest.relative_to(base).as_posix()
        subject, tags = _split(dest.stem)
        st = dest.stat()
        upsert_asset(get_db(root), ws_name, {
            "rel_path": rel,
            "name": dest.name,
            "dir_rel": dest.parent.relative_to(base).as_posix()
            if dest.parent != base else "",
            "subject": subject,
            "tags": tags,
            "category": _category_of(subject, tags),
            "kind": kind,
            "ext": ext,
            "size": st.st_size,
            "mtime": st.st_mtime,
        })
        items.append({
            "rel": rel,
            "name": dest.name,
            "kind": kind,
            "tags": tags,
            "subject": subject,
            "dir": dir_rel,
            "size": st.st_size,
            "deduped": deduped,
            "url": "/api/assets/file/%s" % quote(rel),
        })

        # 旧表照旧写；同时补一条新索引，资产页上传后立即可见。
        try:
            from modules.assets.service import get_scan_manager

            ws_rel = dest.relative_to(Path(root).resolve()).as_posix()
            get_scan_manager().index_one(root, ws_rel)
        except Exception as exc:  # noqa: BLE001
            print(f"[assets] 新索引同步失败（旧上传仍成功）：{exc}")

    return {
        "ok": True,
        "asset_dir": base.name,
        "dir": dir_rel,
        "auto_placed": not subdir.strip(),
        "items": items,
        "skipped": skipped,
    }


@router.get("/file/{rel:path}")
async def asset_file(rel: str):
    """提供 {asset} 目录下的素材预览（图片/音频/视频），严格限制在目录内。"""
    _cfg, _root, _ws, base = _context()
    base = base.resolve()
    path = (base / rel).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(status_code=404, detail="素材不存在")
    mt = _media_type(path.suffix.lower())
    if mt is None:
        raise HTTPException(status_code=415, detail="不支持预览的文件类型")
    _kind, media = mt
    return FileResponse(
        path, media_type=media, filename=path.name, content_disposition_type="inline"
    )
