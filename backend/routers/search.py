# -*- coding: utf-8 -*-
"""联网采集 API（整合项 C3 / L3）—— 前端「小说搜索」页的真实后端。

- `GET  /api/search/status`    开关状态 + 源统计 + 免责文案（未启用也返回 200）
- `GET  /api/search/sources`   书源列表（含 supported 与人话原因）
- `POST /api/search`           搜书名
- `POST /api/search/toc`       取目录
- `POST /api/search/chapter`   取正文（过 textclean 的网文档）
- `POST /api/search/import`    收进本地原著库（复用 C2 的 import_book）

🔴 **默认关闭**。除了 status，其余端点在未启用时一律 403 且带可操作说明。
这是方案 §7 M7 定的三条前置里的第一条：默认关闭 + 免责声明 + 来源标注。
启用只能由前端在用户确认免责之后 PUT /api/config 提交，后端不替用户点头。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from booksource import (
    DEFAULT_SOURCES_PATH,
    FetchError,
    UnsupportedRule,
    fetch_chapter,
    fetch_toc,
    find_source,
    load_sources,
    search_books,
)
from config import get_config

router = APIRouter(prefix="/api/search", tags=["search"])

DISCLAIMER = (
    "联网采集会访问第三方站点。请只用于你有权使用的文本，"
    "并遵守各站点的使用条款与相关法律法规。采集结果仅供个人学习研究，"
    "请勿用于任何商业用途。"
)

#: 一次最多收多少章。设上限是为了不把别人的站点当爬虫靶子。
MAX_IMPORT_CHAPTERS = 200


def _gate():
    """未启用就挡掉。返回 (cfg, sources_path)。"""
    cfg = get_config()
    s = cfg.search
    if not s.enabled or not s.disclaimer_acked:
        raise HTTPException(
            status_code=403,
            detail="联网采集未启用：请先在「小说搜索」页确认免责声明并开启",
        )
    return cfg


def _sources(cfg):
    path = (cfg.search.sources_path or "").strip() or str(DEFAULT_SOURCES_PATH)
    sources, errors = load_sources(path)
    return sources, errors, path


def _pick(cfg, name: str):
    sources, errors, _ = _sources(cfg)
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors))
    src = find_source(sources, name)
    if src is None:
        raise HTTPException(status_code=404, detail=f"找不到书源：{name}")
    return src


def _fail(e: Exception) -> HTTPException:
    """引擎层的两类错误都翻译成人话，不往外抛堆栈。"""
    if isinstance(e, UnsupportedRule):
        return HTTPException(status_code=422, detail=str(e))
    if isinstance(e, FetchError):
        return HTTPException(status_code=502, detail=str(e))
    return HTTPException(status_code=500, detail=f"采集失败：{e}")


# ---------------------------------------------------------------- status / sources


@router.get("/status")
async def status():
    """开关状态。这个端点永远 200 —— 前端要靠它决定画「未启用」还是画搜索框。"""
    cfg = get_config()
    s = cfg.search
    path = (s.sources_path or "").strip() or str(DEFAULT_SOURCES_PATH)
    total = usable = 0
    errors: list[str] = []
    if Path(path).exists():
        sources, errors = load_sources(path)
        total = len(sources)
        usable = sum(
            1 for x in sources if not x.unsupported and x.can_search and x.can_toc and x.can_content
        )
    else:
        errors = [f"书源文件不存在：{path}"]
    return {
        "enabled": bool(s.enabled and s.disclaimer_acked),
        "enabled_flag": bool(s.enabled),
        "disclaimer_acked": bool(s.disclaimer_acked),
        "disclaimer": DISCLAIMER,
        "sources_path": path,
        "sources_total": total,
        "sources_usable": usable,
        "errors": errors,
        "attribution": bool(s.attribution),
        "timeout_sec": s.timeout_sec,
    }


@router.get("/sources")
async def list_sources():
    cfg = _gate()
    sources, errors, _ = _sources(cfg)
    return {
        "sources": [s.to_dict() for s in sources],
        "errors": errors,
    }


# ---------------------------------------------------------------- 三步采集


class SearchBody(BaseModel):
    keyword: str = ""
    source: str = ""
    page: int = 1


class TocBody(BaseModel):
    source: str = ""
    book_url: str = ""


class ChapterBody(BaseModel):
    source: str = ""
    url: str = ""


@router.post("")
async def search(body: SearchBody):
    cfg = _gate()
    kw = (body.keyword or "").strip()
    if not kw:
        raise HTTPException(status_code=400, detail="关键词不能为空")
    src = _pick(cfg, body.source)
    try:
        books = search_books(src, kw, max(1, body.page), cfg=cfg.search)
    except (UnsupportedRule, FetchError) as e:
        raise _fail(e) from e
    except Exception as e:  # noqa: BLE001
        raise _fail(e) from e
    return {"keyword": kw, "source": src.name, "books": books}


@router.post("/toc")
async def toc(body: TocBody):
    cfg = _gate()
    if not (body.book_url or "").strip():
        raise HTTPException(status_code=400, detail="book_url 不能为空")
    src = _pick(cfg, body.source)
    try:
        chapters = fetch_toc(src, body.book_url.strip(), cfg=cfg.search)
    except (UnsupportedRule, FetchError) as e:
        raise _fail(e) from e
    except Exception as e:  # noqa: BLE001
        raise _fail(e) from e
    return {"source": src.name, "book_url": body.book_url, "chapters": chapters}


@router.post("/chapter")
async def chapter(body: ChapterBody):
    cfg = _gate()
    if not (body.url or "").strip():
        raise HTTPException(status_code=400, detail="url 不能为空")
    src = _pick(cfg, body.source)
    try:
        return fetch_chapter(src, body.url.strip(), cfg=cfg.search)
    except (UnsupportedRule, FetchError) as e:
        raise _fail(e) from e
    except Exception as e:  # noqa: BLE001
        raise _fail(e) from e


# ---------------------------------------------------------------- 收进原著库


class ImportBody(BaseModel):
    source: str = ""
    book_url: str = ""
    title: str = ""
    author: str = ""
    chapter_from: int = Field(default=1, ge=1)
    chapter_to: int = Field(default=0, ge=0)


@router.post("/import")
async def import_book(body: ImportBody):
    """取目录 → 逐章取正文 → 拼成一本书 → 走 C2 的 import_book 落盘建索引。

    失败的章节**保留在结果里**说明原因，不整本放弃 —— 200 章里挂 3 章很正常，
    整本失败的话用户什么都拿不到。
    """
    cfg = _gate()
    src = _pick(cfg, body.source)
    if not (body.book_url or "").strip():
        raise HTTPException(status_code=400, detail="book_url 不能为空")

    try:
        chapters = fetch_toc(src, body.book_url.strip(), cfg=cfg.search)
    except (UnsupportedRule, FetchError) as e:
        raise _fail(e) from e

    start = max(1, body.chapter_from)
    end = body.chapter_to if body.chapter_to > 0 else len(chapters)
    end = min(end, len(chapters))
    if end > start + MAX_IMPORT_CHAPTERS:
        end = start + MAX_IMPORT_CHAPTERS
    picked = chapters[start - 1 : end]
    if not picked:
        raise HTTPException(status_code=400, detail=f"这个范围里没有章节（共 {len(chapters)} 章）")

    parts: list[str] = []
    failed: list[dict] = []
    for i, ch in enumerate(picked, start=start):
        try:
            got = fetch_chapter(src, ch["url"], cfg=cfg.search)
        except Exception as e:  # noqa: BLE001 —— 单章失败不拖垮整本
            failed.append({"index": i, "title": ch.get("title", ""), "reason": str(e)})
            continue
        text = (got.get("text") or "").strip()
        if not text:
            failed.append({"index": i, "title": ch.get("title", ""), "reason": "正文为空"})
            continue
        parts.append(f"{ch.get('title', f'第{i}章')}\n\n{text}")

    if not parts:
        raise HTTPException(status_code=502, detail="一章都没取到，可能是源站改版或需要 JS")

    from config import get_config as _gc
    from library import import_book as _import

    cfg2 = _gc()
    root = cfg2.ws_root()
    if root is None or not cfg2.active:
        raise HTTPException(status_code=400, detail="未激活任何工作区")

    title = (body.title or "").strip() or f"未命名_{src.name}"
    # 合规前置第 3 条：来源标注落到书的 source 字段里，跟着工作区一起走
    attribution = f"{src.name} · {src.url}" if cfg2.search.attribution else ""
    res = _import(
        root,
        cfg2.active,
        title,
        "\n\n".join(parts),
        author=(body.author or "").strip(),
        source=attribution,
    )
    res["failed"] = failed
    res["requested"] = len(picked)
    return res
