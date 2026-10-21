# -*- coding: utf-8 -*-
"""本地原著 / 剧本库（整合项 C2，补 G-1 的一半）。

**不联网**：手动导入 txt / md（或从剪贴板粘），按章节切分、净化、落盘、建索引。
真正的「改编成 §①–§⑥」仍靠外部 Agent —— 这里只负责让文本「在工作区里、
可检索、可引用」。

落盘服从 I-2（工作区自包含）::

    {工作区}/原著/<书名>/0001 第一章 xxx.md      ← 与 NBE 的 chapter_filename 一致（补零 4 位）
    {工作区}/.gachabench/cards.db → books / chapters 两表

**为什么不做全局库**：全局库会让「目录拷走记录跟着走」失效。跨工作区复用改用
「导入时选择复制 / 引用」，默认复制（整本书都在工作区里）。

**导入格式只支持 txt / md**：epub 需要新依赖，会破坏 GB 轻档「零依赖」的底线，
暂不做（见设计文档 §10 遗留问题）。

目录「按需补建」：已有工作区不强行重建骨架，导入时缺哪个补哪个。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from db import get_db
from textclean import clean_text

SOURCE_DIR = "原著"

# 章节标题：`第N章 xxx` / `第N节 xxx` / `Chapter 3 xxx` / md 的 `# xxx`
CN_CHAPTER_RE = re.compile(
    r"^(?:#{1,6}\s*)?第\s*([0-9零一二三四五六七八九十百千两]+)\s*[章节回]\s*[·\.、:：\-—]?\s*(.*)$"
)
EN_CHAPTER_RE = re.compile(r"^(?:#{1,6}\s*)?chapter\s+([0-9ivxIVX]+)\s*[·\.、:：\-—]?\s*(.*)$", re.I)
MD_HEAD_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")

# 文件名里不能出现的字符（Windows）
_BAD_CHARS = re.compile(r"[\\/:*?\"<>|\r\n\t]")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")


def safe_filename(name: str, limit: int = 60) -> str:
    """文件名安全化：去非法字符、压空白、限长。空结果给「未命名」。"""
    s = _BAD_CHARS.sub("", (name or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    if not s:
        s = "未命名"
    return s[:limit]


def chapter_filename(idx: int, title: str) -> str:
    """`0001 第一章 xxx.md` —— 与 NBE 的 chapter_filename 规则一致（补零 4 位）。"""
    return f"{idx:04d} {safe_filename(title, 48)}.md"


def count_words(text: str) -> int:
    """中文字数 + 英文词数（混排文本的合理口径）。"""
    return len(_CJK.findall(text)) + len(_WORD.findall(text))


def decode_bytes(raw: bytes) -> str:
    """中文 txt 常见 GBK，别一上来就 utf-8 硬解然后满屏问号。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _norm(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def split_chapters(text: str) -> list[tuple[str, str]]:
    """切章。返回 [(标题, 正文)]。

    优先级：`第N章` → `Chapter N` → md 的 `# 标题` → 整篇当一章。
    第一章标题之前的序言**丢弃**（那里通常是书名 / 作者 / 广告，不是正文）。
    章节编号按出现顺序从 1 起，跟标题里写的数字无关（md 标题可能没数字）。
    """
    lines = _norm(text).split("\n")
    marks: list[tuple[int, str]] = []

    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        m = CN_CHAPTER_RE.match(s) or EN_CHAPTER_RE.match(s)
        if m:
            title = (m.group(2) or "").strip()
            if not title:
                title = s[:40]
            marks.append((i, title[:80]))
    if not marks:
        for i, line in enumerate(lines):
            m = MD_HEAD_RE.match(line.strip())
            if m:
                marks.append((i, m.group(2).strip()[:80]))

    if not marks:
        body = _norm(text).strip()
        return [("", body)] if body else []

    out: list[tuple[str, str]] = []
    for k, (start, title) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
        body = "\n".join(lines[start + 1 : end]).strip("\n")
        # 标题行本身也留着：md 里它就是要看的
        out.append((title, body))
    return out


def _source_root(root: Path) -> Path:
    """原著目录：缺了就补建（已有工作区不重建整个骨架，只补这一个）。"""
    p = root / SOURCE_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def import_book(
    root: Path,
    ws_name: str,
    title: str,
    text: str,
    *,
    author: str = "",
    source: str = "",
) -> dict:
    """导入一本书：切章 → 落盘 → 建索引。同名书复用同一条 book 记录（幂等）。

    返回 {book_id, title, dir_rel, chapters:[…], skipped:[…]}。
    """
    root = root.resolve()
    safe_title = safe_filename(title.strip() or "未命名")
    source_root = _source_root(root)
    book_dir = source_root / safe_title
    book_dir.mkdir(parents=True, exist_ok=True)
    dir_rel = f"{SOURCE_DIR}/{safe_title}"

    chapters = split_chapters(text)
    if not chapters:
        return {"book_id": None, "title": safe_title, "dir_rel": dir_rel, "chapters": [], "skipped": ["没有切出任何章节"]}

    db = get_db(root)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    skipped: list[str] = []

    with db.lock:
        cur = db.conn.execute(
            "SELECT id FROM books WHERE workspace=? AND dir_rel=?", (ws_name, dir_rel)
        ).fetchone()
        if cur:
            book_id = int(cur["id"])
            db.conn.execute(
                "UPDATE books SET title=?, author=?, source=?, created_at=? WHERE id=?",
                (safe_title, author, source, now, book_id),
            )
            db.conn.execute("DELETE FROM chapters WHERE book_id=?", (book_id,))
        else:
            cur = db.conn.execute(
                "INSERT INTO books(workspace, title, author, source, dir_rel, created_at)"
                " VALUES(?,?,?,?,?,?)",
                (ws_name, safe_title, author, source, dir_rel, now),
            )
            book_id = int(cur.lastrowid)

        keep: set[str] = set()
        for i, (ctitle, body) in enumerate(chapters, start=1):
            fname = chapter_filename(i, ctitle or f"第{i}章")
            rel_path = f"{dir_rel}/{fname}"
            keep.add(fname)
            try:
                (book_dir / fname).write_text(f"# {ctitle}\n\n{body}\n", encoding="utf-8")
            except OSError as e:
                skipped.append(f"{fname}：{e}")
                continue
            words = count_words(body)
            cur = db.conn.execute(
                "INSERT INTO chapters(book_id, idx, title, rel_path, word_count) VALUES(?,?,?,?,?)",
                (book_id, i, ctitle, rel_path, words),
            )
            rows.append(
                {
                    "id": int(cur.lastrowid),
                    "idx": i,
                    "title": ctitle,
                    "rel_path": rel_path,
                    "word_count": words,
                }
            )

        # 清掉上一版留下的旧章节文件：只删长得像章节名的（NNNN xxx.md），
        # 用户自己放进去的别的 md 不动。
        for p in book_dir.glob("[0-9][0-9][0-9][0-9] *.md"):
            if p.name not in keep:
                try:
                    p.unlink()
                except OSError:
                    pass

        db.conn.commit()

    # source 一并回传：联网采集拿它做来源标注，UI 要显示「这书从哪来的」
    return {
        "book_id": book_id,
        "title": safe_title,
        "dir_rel": dir_rel,
        "source": source,
        "chapters": rows,
        "skipped": skipped,
    }


def list_books(root: Path, ws_name: str) -> list[dict]:
    """书目列表（含章节数、总字数）。"""
    db = get_db(root)
    with db.lock:
        books = db.conn.execute(
            "SELECT * FROM books WHERE workspace=? ORDER BY id DESC", (ws_name,)
        ).fetchall()
        out = []
        for b in books:
            agg = db.conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(word_count),0) AS w FROM chapters WHERE book_id=?",
                (b["id"],),
            ).fetchone()
            out.append(
                {
                    "id": int(b["id"]),
                    "title": b["title"],
                    "author": b["author"] or "",
                    "source": b["source"] or "",
                    "dir_rel": b["dir_rel"],
                    "created_at": b["created_at"],
                    "chapters": int(agg["n"]),
                    "words": int(agg["w"]),
                }
            )
        return out


def get_book(root: Path, ws_name: str, book_id: int) -> dict | None:
    db = get_db(root)
    with db.lock:
        b = db.conn.execute(
            "SELECT * FROM books WHERE id=? AND workspace=?", (book_id, ws_name)
        ).fetchone()
        if not b:
            return None
        rows = db.conn.execute(
            "SELECT id, idx, title, rel_path, word_count FROM chapters WHERE book_id=? ORDER BY idx",
            (book_id,),
        ).fetchall()
    return {
        "id": int(b["id"]),
        "title": b["title"],
        "author": b["author"] or "",
        "source": b["source"] or "",
        "dir_rel": b["dir_rel"],
        "created_at": b["created_at"],
        "chapters": [
            {
                "id": int(r["id"]),
                "idx": int(r["idx"]),
                "title": r["title"],
                "rel_path": r["rel_path"],
                "word_count": int(r["word_count"]),
            }
            for r in rows
        ],
    }


def _chapter_row(root: Path, chapter_id: int) -> dict | None:
    db = get_db(root)
    with db.lock:
        r = db.conn.execute(
            "SELECT c.*, b.dir_rel, b.title AS book_title FROM chapters c"
            " JOIN books b ON b.id=c.book_id WHERE c.id=?",
            (chapter_id,),
        ).fetchone()
    return dict(r) if r else None


def read_chapter(root: Path, chapter_id: int) -> dict | None:
    """章节正文（textclean 第一层：净化但不改写）。"""
    row = _chapter_row(root, chapter_id)
    if not row:
        return None
    fp = root / row["rel_path"]
    text = fp.read_text(encoding="utf-8", errors="replace") if fp.is_file() else ""
    return {
        "id": int(row["id"]),
        "book_id": int(row["book_id"]),
        "book_title": row["book_title"],
        "idx": int(row["idx"]),
        "title": row["title"],
        "rel_path": row["rel_path"],
        "text": clean_text(text),
        "words": int(row["word_count"]),
    }


def excerpt(root: Path, chapter_id: int, start: int, end: int) -> dict | None:
    """按段号取片段（1-based，闭区间），供 SplitPanel 当段落素材引用。

    🔴 只摘录，不改写 —— 生成 §④ 提示词是 LLM 的活，不在这里做。
    """
    ch = read_chapter(root, chapter_id)
    if not ch:
        return None
    paras = [p for p in re.split(r"\n\s*\n", ch["text"]) if p.strip()]
    # 落盘时给每章加的 `# 标题` 是元数据，不算正文段落 —— 摘录从第 1 段正文开始
    if paras and paras[0].lstrip().startswith("# "):
        paras = paras[1:]
    if not paras:
        return {"text": "", "words": 0, "from": 0, "to": 0}
    s = max(1, min(start if start > 0 else 1, len(paras)))
    e = max(s, min(end if end > 0 else len(paras), len(paras)))
    text = "\n\n".join(paras[s - 1 : e]).strip()
    return {"text": text, "words": count_words(text), "from": s, "to": e}
