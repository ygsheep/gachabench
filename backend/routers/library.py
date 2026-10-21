# -*- coding: utf-8 -*-
"""本地原著 / 剧本库 API（整合项 C2）。

- `POST /api/library/import`             导入 txt/md（multipart 或 {path}/{text}）
- `GET  /api/library/books`              书目列表（含章节数、字数）
- `GET  /api/library/books/{id}`         单本 + 章节列表
- `GET  /api/library/chapters/{id}`      章节正文（textclean 第一层）
- `POST /api/library/chapters/{id}/excerpt`  按段号摘录（**只摘录，不改写**）
- `DELETE /api/library/books/{id}`       删索引（不动盘上的 md）

只支持 txt / md：epub 需要新依赖，会破坏「零依赖」底线，暂不做。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from config import get_config
from library import (
    decode_bytes,
    excerpt,
    get_book,
    import_book,
    list_books,
    read_chapter,
    safe_filename,
)

router = APIRouter(prefix="/api/library", tags=["library"])

MAX_UPLOAD_BYTES = 32 * 1024 * 1024
TEXT_SUFFIX = {".txt", ".md", ".markdown", ".text"}


def _context() -> tuple[Path, str]:
    cfg = get_config()
    root = cfg.ws_root()
    if root is None or not cfg.active:
        raise HTTPException(status_code=400, detail="未激活任何工作区")
    return root, cfg.active


class TextBody(BaseModel):
    title: str
    text: str = ""
    path: str = ""
    author: str = ""
    source: str = ""


class ExcerptBody(BaseModel):
    start: int = 1
    end: int = 0


@router.post("/import")
async def import_api(
    file: UploadFile | None = File(default=None),
    title: str = Form(default=""),
    author: str = Form(default=""),
    source: str = Form(default=""),
    path: str = Form(default=""),
    text: str = Form(default=""),
):
    root, ws = _context()

    if file is not None and file.filename:
        raw = await file.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")
        suffix = Path(file.filename).suffix.lower()
        if suffix not in TEXT_SUFFIX:
            raise HTTPException(status_code=415, detail=f"只支持 txt / md，收到 {suffix or '无后缀'}")
        content = decode_bytes(raw)
        book_title = title.strip() or Path(file.filename).stem
    elif path.strip():
        fp = Path(path).expanduser()
        if not fp.is_file():
            raise HTTPException(status_code=404, detail=f"文件不存在：{path}")
        if fp.suffix.lower() not in TEXT_SUFFIX:
            raise HTTPException(status_code=415, detail=f"只支持 txt / md，收到 {fp.suffix}")
        content = decode_bytes(fp.read_bytes())
        book_title = title.strip() or fp.stem
    elif text.strip():
        content = text
        book_title = title.strip() or "未命名"
    else:
        raise HTTPException(status_code=400, detail="给个文件、路径或正文，三选一")

    result = import_book(root, ws, book_title, content, author=author, source=source)
    if not result["chapters"]:
        raise HTTPException(
            status_code=400,
            detail="没切出章节：" + "；".join(result["skipped"]) or "正文是空的",
        )
    return result


@router.get("/books")
def books_api():
    root, ws = _context()
    return {"books": list_books(root, ws)}


@router.get("/books/{book_id}")
def book_api(book_id: int):
    root, ws = _context()
    book = get_book(root, ws, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="没有这本书")
    return book


@router.delete("/books/{book_id}")
def delete_book_api(book_id: int):
    """只删索引，不动盘上的 md —— 书是用户的，删错了还能从盘上找回来。"""
    root, ws = _context()
    book = get_book(root, ws, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="没有这本书")
    from db import get_db

    db = get_db(root)
    with db.lock:
        db.conn.execute("DELETE FROM chapters WHERE book_id=?", (book_id,))
        db.conn.execute("DELETE FROM books WHERE id=? AND workspace=?", (book_id, ws))
        db.conn.commit()
    return {"ok": True, "id": book_id, "kept_files": True}


@router.get("/chapters/{chapter_id}")
def chapter_api(chapter_id: int):
    root, _ = _context()
    ch = read_chapter(root, chapter_id)
    if not ch:
        raise HTTPException(status_code=404, detail="没有这一章")
    return ch


@router.post("/chapters/{chapter_id}/excerpt")
def excerpt_api(chapter_id: int, body: ExcerptBody):
    root, _ = _context()
    got = excerpt(root, chapter_id, body.start, body.end)
    if not got:
        raise HTTPException(status_code=404, detail="没有这一章")
    return got


@router.get("/target")
def target_api(title: str = ""):
    """落位预览：这本书会放进哪个目录（导入前给前端显示）。"""
    root, _ = _context()
    cfg = get_config()
    folder = cfg.folder_name("source") or "原著"
    return {"dir": f"{folder}/{safe_filename(title.strip() or '未命名')}", "root": str(root)}
