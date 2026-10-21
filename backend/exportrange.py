# -*- coding: utf-8 -*-
"""范围导出（C4）：把「第 x–y 段 / 第 x–y 章 / 某一批卡」展开成 ExportItem 列表。

移植自 novel-booksource-engine 的 `export_txt_dir` + `chapter_filename` 思路，
按 GB 的三类产物重排（见 docs/INTEGRATION-novel-booksource-engine.md §3 C4）。

## 为什么单独开一个模块

`export.py` 的 `build_items` 是「请求给什么就导什么」，一次最多 64 条（`MAX_ITEMS`）。
范围导出是**后端自己枚举**的：第 1–40 段就是 40 个条目，让调用方拼 40 个
`rel_path` 再发过来既笨又容易越界。所以展开放在后端，展开完照样走
`payload_download` / `write_to_dir` 那条链路 —— 三种形态、zip 打包、不覆盖的约定
全部复用，不另起一套。

## 三类范围

| `what`      | 来源                          | 条目摆放                     |
| ----------- | ----------------------------- | ---------------------------- |
| `prompts`   | `{prompt}/*_正片_第N段_15s.md` | `提示词/NN_正片_第N段_15s.md` |
| `chapters`  | `chapters` 表（需要 book_id）  | `{书名}/NNNN 标题.md`         |
| `cards`     | `cards` 表（status / seg 筛选）| `第N段/{块}_{文件名}`         |

摆放一律带 subdir：二十个 `0001 xxx.md` 平铺在一个目录里会互相撞名，
zip 里的去重只能把它们改成 `_02` —— 分了层才是人能用的目录结构。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from db import get_db
from export import ExportError, ExportItem
from library import safe_filename
from repo import card_file_path, list_cards

# 一次范围导出最多多少个条目：再多就该分批，也防止 from=1 to=99999 把内存吃光
MAX_RANGE = 200

_PROMPT_SEG_RE = re.compile(r"第(\d+)段")


def _seg_of(name: str) -> Optional[int]:
    m = _PROMPT_SEG_RE.search(name)
    return int(m.group(1)) if m else None


def _range(from_: Any, to_: Any) -> tuple[int, int]:
    """校验范围：正整数、from ≤ to、跨度不超过 MAX_RANGE。"""
    try:
        a = int(from_)
        b = int(to_)
    except (TypeError, ValueError):
        raise ExportError("范围必须是数字") from None
    if a < 1 or b < 1:
        raise ExportError("范围从 1 开始")
    if a > b:
        raise ExportError(f"起始（{a}）不能大于结束（{b}）")
    if b - a + 1 > MAX_RANGE:
        raise ExportError(f"一次最多导出 {MAX_RANGE} 个，请分批")
    return a, b


def range_prompts(cfg, from_seg: Any, to_seg: Any) -> list[ExportItem]:
    """段 x–y 的提示词 md，按段号升序。"""
    a, b = _range(from_seg, to_seg)
    pdir = cfg.subdir("prompt")
    if pdir is None or not pdir.is_dir():
        raise ExportError("提示词目录未确定或不存在", 404)
    picked: list[tuple[int, Path]] = []
    for md in pdir.glob("*_正片_第*段_15s.md"):
        n = _seg_of(md.name)
        if n is not None and a <= n <= b:
            picked.append((n, md))
    picked.sort(key=lambda t: t[0])
    if not picked:
        raise ExportError(f"第 {a}–{b} 段里没有提示词文件", 404)
    return [
        ExportItem(filename=md.name, mime="text/markdown; charset=utf-8", src=md, subdir="提示词")
        for _, md in picked
    ]


def range_chapters(
    cfg, from_ch: Any, to_ch: Any, book_id: Any
) -> list[ExportItem]:
    """某本书的第 x–y 章（按 chapters.idx，即导入时的章节序号）。"""
    a, b = _range(from_ch, to_ch)
    root = cfg.ws_root()
    ws = cfg.active
    if root is None or not ws:
        raise ExportError("未激活任何工作区")
    try:
        bid = int(book_id)
    except (TypeError, ValueError):
        raise ExportError("book_id 必须是数字") from None

    db = get_db(root)
    with db.lock:
        book = db.conn.execute(
            "SELECT * FROM books WHERE id=? AND workspace=?", (bid, ws)
        ).fetchone()
        if not book:
            raise ExportError(f"没有这本书：{bid}", 404)
        rows = db.conn.execute(
            "SELECT idx, title, rel_path FROM chapters WHERE book_id=? AND idx BETWEEN ? AND ?"
            " ORDER BY idx",
            (bid, a, b),
        ).fetchall()
    if not rows:
        raise ExportError(f"这本书里没有第 {a}–{b} 章", 404)

    subdir = safe_filename(book["title"] or f"book{bid}", limit=40)
    items: list[ExportItem] = []
    for r in rows:
        fp = root / r["rel_path"]
        if not fp.is_file():
            # 索引在、盘上没了：跳过而不是整个失败（用户可能手动删过一章）
            continue
        items.append(
            ExportItem(
                filename=fp.name,
                mime="text/markdown; charset=utf-8",
                src=fp,
                subdir=subdir,
            )
        )
    if not items:
        raise ExportError(f"第 {a}–{b} 章的章节文件都不在盘上了", 404)
    return items


def range_cards(cfg, status: str = "final", seg: str = "") -> list[ExportItem]:
    """按状态（+ 可选段号）导出卡的视频文件。

    这就是「这一段的正片打包带走」。文件名带上块号（`1of3_`）——同段同块可能
    有多张卡，光靠原文件名分不清谁是谁。
    """
    root = cfg.ws_root()
    ws = cfg.active
    if root is None or not ws:
        raise ExportError("未激活任何工作区")
    status = str(status or "final")
    if status not in ("final", "draft", "reject", "all"):
        raise ExportError(f"不支持的卡片状态：{status}")

    db = get_db(root)
    filters: dict[str, Any] = {"workspace": ws}
    if status != "all":
        filters["status"] = status
    if seg and str(seg).strip() and str(seg) != "all":
        filters["seg"] = str(seg).strip()
    cards = list_cards(db, filters)
    if len(cards) > MAX_RANGE:
        raise ExportError(
            f"符合条件的有 {len(cards)} 张，超过 {MAX_RANGE} 张；请缩小范围或分批"
        )

    items: list[ExportItem] = []
    for c in cards:
        fp = card_file_path(cfg, c)
        if fp is None or not fp.is_file():
            continue
        chunk = (c.get("chunk") or "").strip()
        seg_no = (c.get("seg") or "").strip()
        name = f"{chunk}_{fp.name}" if chunk else fp.name
        items.append(
            ExportItem(
                filename=name,
                mime="video/mp4",
                src=fp,
                subdir=f"第{seg_no}段" if seg_no else "卡片",
            )
        )
    if not items:
        raise ExportError("没有符合条件的卡片（或文件已经不在盘上了）", 404)
    return items


def build_range(
    cfg,
    what: str,
    from_: Any = 1,
    to_: Any = 1,
    book_id: Any = None,
    status: str = "final",
    seg: str = "",
) -> list[ExportItem]:
    """按 `what` 分发。"""
    what = str(what or "").strip()
    if what == "prompts":
        return range_prompts(cfg, from_, to_)
    if what == "chapters":
        return range_chapters(cfg, from_, to_, book_id)
    if what == "cards":
        return range_cards(cfg, status=status, seg=seg)
    raise ExportError(f"不支持的导出范围：{what}")
