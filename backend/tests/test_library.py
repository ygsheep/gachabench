# -*- coding: utf-8 -*-
"""本地原著 / 剧本库（C2）的单测。

跑法（uv 的 trampoline 在沙箱里起不来，用 uv 的基础解释器 + venv site-packages）：

    PYTHONPATH="backend;backend/.venv/Lib/site-packages" \
      "C:/Users/young.YOUNG/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe" \
      -m unittest discover -s tests

覆盖重点：
1. **切章口径** —— 第N章 / Chapter N / md 标题 / 无标题，四种都要能切；编号从 1 起。
2. **落盘形状** —— `0001 第一章 xxx.md`，跟着工作区走（I-2 自包含）。
3. **重复导入幂等** —— 同一本书再导一次不产生第二本，旧章节文件被清掉。
4. **摘录只摘录** —— 段号闭区间，超界钳制，不改写正文。
5. **HTTP 层** —— 只收 txt/md、413、按工作区隔离。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import config as config_mod
from config import get_config
from library import (
    chapter_filename,
    count_words,
    decode_bytes,
    excerpt,
    get_book,
    import_book,
    list_books,
    read_chapter,
    safe_filename,
    split_chapters,
)
from main import app

NOVEL = """某某小说
作者：某人

第一章 开场
这里是正文第一段。

这里是第二段。

第二章 转折
转折的正文。

第三章 收尾
收尾的正文。
"""

MD_BOOK = """# 序章
序章正文。

## 第一幕
第一幕正文。
"""

EN_BOOK = """Chapter 1 The Beginning
hello world

Chapter 2 The End
goodbye
"""


def _memory_only_set(cfg):
    """只更新进程内缓存，不写盘（避免污染用户真实的 config.json）。"""
    with config_mod._config_cache_lock:
        config_mod._config_cache = cfg


def _memory_config(root: Path):
    cfg = get_config().model_copy(deep=True)
    cfg.active = None
    cfg.workspaces = []
    from config import WorkspaceEntry

    cfg.workspaces = [WorkspaceEntry(name="_libtest", root=str(root), created_at="2026-10-18T00:00:00")]
    cfg.active = "_libtest"
    return cfg


class SplitTest(unittest.TestCase):
    def test_cn_chapters(self):
        out = split_chapters(NOVEL)
        self.assertEqual([t for t, _ in out], ["开场", "转折", "收尾"])
        self.assertIn("正文第一段", out[0][1])
        # 序言（书名 + 作者）不该混进第一章
        self.assertNotIn("作者：某人", out[0][1])

    def test_md_headings(self):
        out = split_chapters(MD_BOOK)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0][0], "序章")

    def test_en_chapters(self):
        out = split_chapters(EN_BOOK)
        self.assertEqual([t for t, _ in out], ["The Beginning", "The End"])

    def test_no_heading_is_one_chapter(self):
        out = split_chapters("就这么一段话，没有标题。")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0], "")

    def test_empty(self):
        self.assertEqual(split_chapters("   \n\n  "), [])

    def test_filename_and_words(self):
        self.assertEqual(chapter_filename(1, "第一章 开场"), "0001 第一章 开场.md")
        self.assertEqual(chapter_filename(12, "a/b*c"), "0012 abc.md")
        self.assertEqual(count_words("中文六个字啊对"), 7)
        self.assertEqual(count_words("hello world"), 2)
        self.assertEqual(safe_filename("  "), "未命名")

    def test_decode_gbk(self):
        raw = "第一章".encode("gb18030")
        self.assertEqual(decode_bytes(raw), "第一章")


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb_lib_"))
        self.root = self.tmp / "ws"
        self.root.mkdir(parents=True)
        self.ws = "_libtest"

    def _files(self):
        d = self.root / "原著" / "某某小说"
        return sorted(p.name for p in d.glob("*.md")) if d.is_dir() else []

    def test_import_lands_on_disk(self):
        r = import_book(self.root, self.ws, "某某小说", NOVEL)
        self.assertEqual(len(r["chapters"]), 3)
        self.assertEqual(
            self._files(),
            ["0001 开场.md", "0002 转折.md", "0003 收尾.md"],
        )
        # 标题进了 md，正文也在
        body = (self.root / "原著" / "某某小说" / "0001 开场.md").read_text(encoding="utf-8")
        self.assertIn("# 开场", body)
        self.assertIn("正文第一段", body)

    def test_reimport_is_idempotent(self):
        a = import_book(self.root, self.ws, "某某小说", NOVEL)
        b = import_book(self.root, self.ws, "某某小说", NOVEL)
        self.assertEqual(a["book_id"], b["book_id"])
        self.assertEqual(len(list_books(self.root, self.ws)), 1)
        self.assertEqual(len(self._files()), 3)

    def test_reimport_cleans_stale_chapter_files(self):
        import_book(self.root, self.ws, "某某小说", NOVEL)
        # 少一章再导一次：旧的 0003 必须消失，否则盘上会留着已经不属于这本书的内容
        import_book(self.root, self.ws, "某某小说", "第一章 开场\n正文。\n\n第二章 转折\n正文。\n")
        self.assertEqual(self._files(), ["0001 开场.md", "0002 转折.md"])
        book = get_book(self.root, self.ws, list_books(self.root, self.ws)[0]["id"])
        self.assertEqual(len(book["chapters"]), 2)

    def test_user_files_are_kept(self):
        """不是章节名的 md（用户自己放的）不能被动。"""
        import_book(self.root, self.ws, "某某小说", NOVEL)
        d = self.root / "原著" / "某某小说"
        (d / "笔记.md").write_text("我的笔记", encoding="utf-8")
        import_book(self.root, self.ws, "某某小说", NOVEL)
        self.assertIn("笔记.md", self._files())

    def test_read_chapter_cleans(self):
        r = import_book(self.root, self.ws, "某某小说", NOVEL)
        cid = r["chapters"][0]["id"]
        ch = read_chapter(self.root, cid)
        self.assertEqual(ch["idx"], 1)
        self.assertIn("正文第一段", ch["text"])
        self.assertGreater(ch["words"], 0)

    def test_excerpt_range_and_clamp(self):
        r = import_book(self.root, self.ws, "某某小说", NOVEL)
        cid = r["chapters"][0]["id"]
        got = excerpt(self.root, cid, 1, 1)
        self.assertIn("正文第一段", got["text"])
        self.assertNotIn("第二段", got["text"])
        self.assertEqual((got["from"], got["to"]), (1, 1))
        # 超界钳制：不会抛，也不会返回空
        wide = excerpt(self.root, cid, 1, 999)
        self.assertEqual(wide["to"], 2)
        # 反着给（end < start）也要能出东西，别静默空
        self.assertGreater(excerpt(self.root, cid, 2, 1)["words"], 0)

    def test_books_are_scoped_to_workspace(self):
        import_book(self.root, self.ws, "某某小说", NOVEL)
        other = self.tmp / "ws2"
        other.mkdir()
        self.assertEqual(list_books(other, "_other"), [])
        self.assertEqual(len(list_books(self.root, self.ws)), 1)

    def test_no_chapters_is_reported(self):
        r = import_book(self.root, self.ws, "空书", "   \n  \n")
        self.assertIsNone(r["book_id"])
        self.assertTrue(r["skipped"])


class LibraryApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb_libapi_"))
        self.root = self.tmp / "ws"
        self.root.mkdir(parents=True)
        self._saved = get_config().model_copy(deep=True)
        _memory_only_set(_memory_config(self.root))
        self.client = TestClient(app)

    def tearDown(self):
        _memory_only_set(self._saved)

    def test_import_by_text(self):
        r = self.client.post(
            "/api/library/import", data={"title": "接口导入的书", "text": NOVEL}
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(len(body["chapters"]), 3)
        self.assertTrue((self.root / "原著" / "接口导入的书" / "0001 开场.md").is_file())

    def test_reject_non_text(self):
        r = self.client.post(
            "/api/library/import",
            files={"file": ("a.epub", b"PK\x03\x04fake", "application/octet-stream")},
        )
        self.assertEqual(r.status_code, 415)

    def test_books_and_chapter_and_excerpt(self):
        self.client.post("/api/library/import", data={"title": "书A", "text": NOVEL})
        books = self.client.get("/api/library/books").json()["books"]
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["chapters"], 3)
        self.assertGreater(books[0]["words"], 0)

        book = self.client.get(f"/api/library/books/{books[0]['id']}").json()
        cid = book["chapters"][0]["id"]
        ch = self.client.get(f"/api/library/chapters/{cid}").json()
        self.assertIn("正文第一段", ch["text"])

        ex = self.client.post(
            f"/api/library/chapters/{cid}/excerpt", json={"start": 1, "end": 1}
        ).json()
        self.assertIn("正文第一段", ex["text"])
        self.assertNotIn("第二段", ex["text"])

    def test_delete_keeps_files(self):
        self.client.post("/api/library/import", data={"title": "书B", "text": NOVEL})
        bid = self.client.get("/api/library/books").json()["books"][0]["id"]
        r = self.client.delete(f"/api/library/books/{bid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/library/books").json()["books"], [])
        # 只删索引：盘上的 md 还在
        self.assertTrue((self.root / "原著" / "书B" / "0001 开场.md").is_file())

    def test_404(self):
        self.assertEqual(self.client.get("/api/library/books/99999").status_code, 404)
        self.assertEqual(self.client.get("/api/library/chapters/99999").status_code, 404)

    def test_target_preview(self):
        r = self.client.get("/api/library/target", params={"title": "书名/带斜杠"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("原著/", r.json()["dir"])
        self.assertNotIn("/", r.json()["dir"].split("原著/", 1)[1])


if __name__ == "__main__":
    unittest.main()
