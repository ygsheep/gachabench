# -*- coding: utf-8 -*-
"""导出通道的 zip 打包（M6a）与范围导出（M6b / C4）单测。

跑法（uv 的 trampoline 在沙箱里起不来，用 uv 的基础解释器 + venv site-packages）：

    PYTHONPATH="backend;backend/.venv/Lib/site-packages" \
      "C:/Users/young.YOUNG/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe" \
      -m unittest discover -s tests

覆盖重点：
1. **zip 打包** —— 多条目 web 下载打成一个 zip；单条目直出不套壳。
2. **中文名不乱码** —— zipfile 必须自动打 UTF-8 flag（实测 0x800）。
3. **zip 内不互相覆盖** —— 同名成员只能改名为 `_02`，不能静默丢文件。
4. **subdir 是摆放不是逃逸** —— `..` / 盘符一律拒绝。
5. **范围展开** —— 段 / 章 / 卡三类，越界与空结果都有话说。
6. **HTTP 层** —— `/api/export/range` 三态齐全，`from` 别名能收。
"""
from __future__ import annotations

import base64
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

import config as config_mod
from config import get_config
from db import get_db
from export import (
    ExportError,
    ExportItem,
    build_items,
    payload_download,
    unique_arcnames,
    write_to_dir,
    zip_items,
)
from exportrange import build_range, range_cards, range_chapters, range_prompts
from main import app


def _memory_only_set(cfg):
    """只更新进程内缓存，不写盘（避免污染用户真实的 config.json）。"""
    with config_mod._config_cache_lock:
        config_mod._config_cache = cfg


def _memory_config(root: Path):
    cfg = get_config().model_copy(deep=True)
    cfg.active = None
    cfg.workspaces = []
    from config import WorkspaceEntry

    cfg.workspaces = [
        WorkspaceEntry(name="_rangetest", root=str(root), created_at="2026-10-18T00:00:00")
    ]
    cfg.active = "_rangetest"
    return cfg


def _item(name: str, body: str, subdir: str = "") -> ExportItem:
    return ExportItem(
        filename=name,
        mime="text/plain; charset=utf-8",
        data=body.encode("utf-8"),
        subdir=subdir,
    )


# --------------------------------------------------------------------------
# zip 打包
# --------------------------------------------------------------------------
class ZipTest(unittest.TestCase):
    def test_single_item_stays_raw(self):
        """单条目直出原文件：审片器「导出这一条」要的就是原 mp4，套 zip 是添乱。"""
        p = payload_download([_item("a.mp4", "video")])
        self.assertEqual(p["kind"], "download")
        self.assertEqual(p["filename"], "a.mp4")
        self.assertNotIn("packed", p)
        self.assertEqual(base64.b64decode(p["base64"]), b"video")

    def test_multi_items_zipped(self):
        p = payload_download([_item("a.md", "甲"), _item("b.md", "乙")])
        self.assertTrue(p["filename"].endswith(".zip"))
        self.assertEqual(p["mime"], "application/zip")
        self.assertEqual(p["packed"], 2)
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(p["base64"]))) as zf:
            self.assertEqual(sorted(zf.namelist()), ["a.md", "b.md"])
            self.assertEqual(zf.read("a.md").decode("utf-8"), "甲")

    def test_cjk_name_keeps_utf8_flag(self):
        """中文名必须带 UTF-8 flag（0x800），否则 Windows 解压出来是乱码。"""
        z = zip_items([_item("第17段_提示词.md", "正文")], "打包.zip")
        with zipfile.ZipFile(io.BytesIO(z.data)) as zf:
            info = zf.getinfo("第17段_提示词.md")
            self.assertTrue(info.flag_bits & 0x800, "zipfile 没打 UTF-8 flag")

    def test_zip_name_honored(self):
        self.assertEqual(zip_items([_item("a", "x")], "我的导出").filename, "我的导出.zip")
        self.assertTrue(zip_items([_item("a", "x")]).filename.endswith(".zip"))

    def test_no_silent_overlap_in_zip(self):
        """同名成员只能改名：zip 里后写覆盖先写是不报错的，用户拿到手才发现少文件。"""
        items = [_item("001.md", "一", "s"), _item("001.md", "二", "s"), _item("001.md", "三", "s")]
        names = unique_arcnames(items)
        self.assertEqual(names, ["s/001.md", "s/001_02.md", "s/001_03.md"])
        with zipfile.ZipFile(io.BytesIO(zip_items(items).data)) as zf:
            self.assertEqual(len(zf.namelist()), 3)

    def test_subdir_layout_in_zip(self):
        with zipfile.ZipFile(io.BytesIO(zip_items([_item("01.md", "x", "提示词")]).data)) as zf:
            self.assertEqual(zf.namelist(), ["提示词/01.md"])

    def test_subdir_escape_rejected(self):
        for bad in ("../out", "a/../../b", "C:/x", ".."):
            with self.assertRaises(ExportError, msg=bad):
                _item("x.md", "y", bad)

    def test_zero_byte_rejected(self):
        with self.assertRaises(ExportError):
            payload_download([_item("a.md", "")])

    def test_budget_gate(self):
        import export as export_mod

        old = export_mod.MAX_DOWNLOAD_BYTES
        export_mod.MAX_DOWNLOAD_BYTES = 10
        try:
            with self.assertRaises(ExportError):
                payload_download([_item("a.md", "x" * 40)])
        finally:
            export_mod.MAX_DOWNLOAD_BYTES = old


# --------------------------------------------------------------------------
# dir 形态：subdir + 绝不覆盖
# --------------------------------------------------------------------------
class DirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb_dir_"))
        self.dest = self.tmp / "out"
        self.dest.mkdir()

    def test_subdir_created(self):
        r = write_to_dir([_item("01.md", "正文", "提示词")], self.dest)
        self.assertEqual(r["kind"], "dir")
        self.assertEqual(r["count"], 1)
        self.assertTrue((self.dest / "提示词" / "01.md").is_file())

    def test_no_overwrite(self):
        write_to_dir([_item("a.md", "第一版")], self.dest)
        write_to_dir([_item("a.md", "第二版")], self.dest)
        self.assertEqual((self.dest / "a.md").read_text(encoding="utf-8"), "第一版")
        self.assertEqual((self.dest / "a_02.md").read_text(encoding="utf-8"), "第二版")

    def test_no_overwrite_inside_subdir(self):
        write_to_dir([_item("a.md", "一", "s")], self.dest)
        write_to_dir([_item("a.md", "二", "s")], self.dest)
        self.assertEqual((self.dest / "s" / "a.md").read_text(encoding="utf-8"), "一")
        self.assertTrue((self.dest / "s" / "a_02.md").is_file())

    def test_build_items_accepts_subdir(self):
        root = self.tmp / "ws"
        (root / "草稿").mkdir(parents=True)
        (root / "草稿" / "x.mp4").write_bytes(b"v")
        items = build_items(root, [{"rel_path": "草稿/x.mp4", "subdir": "第4段"}])
        self.assertEqual(items[0].arcname, "第4段/x.mp4")


# --------------------------------------------------------------------------
# 范围展开（需要工作区 + 真实 db）
# --------------------------------------------------------------------------
class RangeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb_range_"))
        self.root = self.tmp / "ws"
        self.root.mkdir(parents=True)
        self._saved = get_config().model_copy(deep=True)
        self.cfg = _memory_config(self.root)
        _memory_only_set(self.cfg)
        self.client = TestClient(app)

    def tearDown(self):
        _memory_only_set(self._saved)

    def _seed_prompts(self, segs=(1, 2, 3, 17)):
        d = self.root / "提示词"
        d.mkdir(parents=True, exist_ok=True)
        for n in segs:
            (d / f"{n:02d}_正片_第{n}段_15s.md").write_text(
                f"# 第{n}段\n正文", encoding="utf-8"
            )

    def test_prompts_range_sorted(self):
        self._seed_prompts()
        items = range_prompts(self.cfg, 2, 17)
        self.assertEqual([i.filename for i in items], ["02_正片_第2段_15s.md", "03_正片_第3段_15s.md", "17_正片_第17段_15s.md"])
        self.assertTrue(all(i.subdir == "提示词" for i in items))

    def test_prompts_empty_range(self):
        self._seed_prompts()
        with self.assertRaises(ExportError) as cm:
            range_prompts(self.cfg, 40, 50)
        self.assertEqual(cm.exception.status, 404)

    def test_prompts_bad_range(self):
        self._seed_prompts()
        with self.assertRaises(ExportError):
            range_prompts(self.cfg, 5, 2)
        with self.assertRaises(ExportError):
            range_prompts(self.cfg, 0, 5)
        with self.assertRaises(ExportError):
            range_prompts(self.cfg, 1, 99999)  # 超过 MAX_RANGE

    def test_chapters_range(self):
        from library import import_book

        import_book(self.root, "_rangetest", "测试书", "第一章 甲\n甲正文。\n\n第二章 乙\n乙正文。\n\n第三章 丙\n丙正文。\n")
        db = get_db(self.root)
        with db.lock:
            bid = db.conn.execute("SELECT id FROM books LIMIT 1").fetchone()["id"]
        items = range_chapters(self.cfg, 2, 3, bid)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].subdir, "测试书")
        self.assertTrue(items[0].filename.startswith("0002"))

    def test_chapters_unknown_book(self):
        with self.assertRaises(ExportError) as cm:
            range_chapters(self.cfg, 1, 2, 99999)
        self.assertEqual(cm.exception.status, 404)

    def test_cards_by_status(self):
        db = get_db(self.root)
        seg_dir = self.root / "生成"
        seg_dir.mkdir(parents=True, exist_ok=True)
        rows = [
            ("生成/seg4_1of3_a.mp4", "4", "1of3", "final"),
            ("生成/seg4_1of3_b.mp4", "4", "1of3", "draft"),
            ("生成/seg9_1of3_c.mp4", "9", "1of3", "final"),
        ]
        for rel, seg, chunk, st in rows:
            (self.root / rel).write_bytes(b"v")
        with db.lock:
            for rel, seg, chunk, st in rows:
                db.conn.execute(
                    "INSERT INTO cards (workspace, rel_path, seg, chunk, status, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    ("_rangetest", rel, seg, chunk, st, "2026-10-18", "2026-10-18"),
                )
            db.conn.commit()

        finals = range_cards(self.cfg, status="final")
        self.assertEqual(len(finals), 2)
        self.assertEqual(sorted(i.subdir for i in finals), ["第4段", "第9段"])
        self.assertTrue(finals[0].filename.startswith("1of3_"))

        only4 = range_cards(self.cfg, status="final", seg="4")
        self.assertEqual(len(only4), 1)

        with self.assertRaises(ExportError):
            range_cards(self.cfg, status="nonsense")

    def test_build_range_dispatch(self):
        self._seed_prompts()
        self.assertEqual(len(build_range(self.cfg, "prompts", 1, 3)), 3)
        with self.assertRaises(ExportError):
            build_range(self.cfg, "wat", 1, 2)


# --------------------------------------------------------------------------
# HTTP 层
# --------------------------------------------------------------------------
class RangeApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb_rangeapi_"))
        self.root = self.tmp / "ws"
        self.root.mkdir(parents=True)
        self.dest = self.tmp / "out"
        self.dest.mkdir()
        self._saved = get_config().model_copy(deep=True)
        _memory_only_set(_memory_config(self.root))
        self.client = TestClient(app)
        d = self.root / "提示词"
        d.mkdir(parents=True, exist_ok=True)
        for n in (1, 2, 3):
            (d / f"{n:02d}_正片_第{n}段_15s.md").write_text(f"# 第{n}段\n正文", encoding="utf-8")

    def tearDown(self):
        _memory_only_set(self._saved)

    def test_download_zip(self):
        r = self.client.post(
            "/api/export/range", json={"what": "prompts", "from": 1, "to": 3}
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["kind"], "download")
        self.assertEqual(body["count"], 3)
        self.assertTrue(body["filename"].endswith(".zip"))
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(body["base64"]))) as zf:
            self.assertEqual(len(zf.namelist()), 3)
            self.assertTrue(all(n.startswith("提示词/") for n in zf.namelist()))

    def test_dir_form_keeps_layout(self):
        r = self.client.post(
            "/api/export/range",
            json={"what": "prompts", "from": 1, "to": 2, "dest_dir": str(self.dest)},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["kind"], "dir")
        self.assertEqual((self.dest / "提示词" / "01_正片_第1段_15s.md").read_text(encoding="utf-8").splitlines()[0], "# 第1段")

    def test_zip_name_override(self):
        r = self.client.post(
            "/api/export/range",
            json={"what": "prompts", "from": 1, "to": 2, "zip_name": "前两段"},
        )
        self.assertEqual(r.json()["filename"], "前两段.zip")

    def test_unknown_what_is_400(self):
        r = self.client.post("/api/export/range", json={"what": "nope", "from": 1, "to": 2})
        self.assertEqual(r.status_code, 400)

    def test_multi_item_export_also_zips(self):
        """老端点多条目之前直接 400，现在也走 zip。"""
        r = self.client.post(
            "/api/export",
            json={
                "items": [
                    {"rel_path": "提示词/01_正片_第1段_15s.md"},
                    {"rel_path": "提示词/02_正片_第2段_15s.md"},
                ]
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["filename"].endswith(".zip"))


if __name__ == "__main__":
    unittest.main()
