# -*- coding: utf-8 -*-
"""export.py + POST /api/export 的单测。

跑法（uv 的 trampoline 在沙箱里起不来，用 uv 的基础解释器 + venv site-packages）：

    PYTHONPATH="backend;backend/.venv/Lib/site-packages" \
      "C:/Users/young.YOUNG/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe" \
      -m unittest discover -s tests

覆盖重点：
1. **路径越界必须拒绝** —— rel_path 是 sqlite 里来的，`../../x` 不能放行。
2. **绝不覆盖** —— 同名导出追加 `_02`，这是「拼接连带导出不覆盖原产物」的验收点。
3. **三态契约形状** —— download 是 JSON+base64，dir 带 count，都不许出现 0 字节成功。
"""
from __future__ import annotations

import base64
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

import export as export_mod
import config as config_mod
from config import get_config
from main import app


def _memory_only_set(cfg):
    """替身：只更新进程内缓存，不写盘（避免污染用户真实的 config.json）。

    `config.set_config` 一定会 `save_config()` 落盘，而 HTTP 层测试要反复改配置，
    所以这里照 test_config_api.py 的老办法换个不落盘的替身。
    """
    with config_mod._config_cache_lock:
        config_mod._config_cache = cfg


def _memory_config(root: Path):
    """把内存配置指向临时工作区，避免碰真实 config.json。"""
    cfg = get_config().model_copy(deep=True)
    cfg.active = None
    cfg.workspaces = []
    from config import WorkspaceEntry

    cfg.workspaces = [WorkspaceEntry(name="_exporttest", root=str(root), created_at="2026-10-17T00:00:00")]
    cfg.active = "_exporttest"
    return cfg


class ExportItemsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb_export_"))
        self.root = self.tmp / "ws"
        (self.root / "草稿").mkdir(parents=True)
        self.video = self.root / "草稿" / "seg004_concat.mp4"
        self.video.write_bytes(b"\x00\x01\x02fake-mp4")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- items 归一 ----
    def test_rel_path_ok(self):
        items = export_mod.build_items(self.root, [{"rel_path": "草稿/seg004_concat.mp4"}])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].filename, "seg004_concat.mp4")
        self.assertEqual(items[0].size, 11)

    def test_rel_path_backslash_ok(self):
        items = export_mod.build_items(self.root, [{"rel_path": "草稿\\seg004_concat.mp4"}])
        self.assertEqual(items[0].src, self.video.resolve())

    def test_rel_path_escape_rejected(self):
        outside = self.tmp / "outside.txt"
        outside.write_text("x", encoding="utf-8")
        # `..` 真的能跳出工作区 —— 必须拒绝
        for bad in ("../outside.txt", "..\\outside.txt", "草稿/../../outside.txt"):
            with self.subTest(bad=bad):
                with self.assertRaises(export_mod.ExportError) as cm:
                    export_mod.build_items(self.root, [{"rel_path": bad}])
                self.assertIn("越出工作区", str(cm.exception))
        # 绝对路径会被 lstrip 成工作区内的相对路径，落不到工作区外，报「不存在」即可
        with self.assertRaises(export_mod.ExportError):
            export_mod.build_items(self.root, [{"rel_path": "/etc/passwd"}])

    def test_rel_path_missing_404(self):
        with self.assertRaises(export_mod.ExportError) as cm:
            export_mod.build_items(self.root, [{"rel_path": "草稿/nope.mp4"}])
        self.assertEqual(cm.exception.status, 404)

    def test_rel_path_empty_rejected(self):
        with self.assertRaises(export_mod.ExportError):
            export_mod.build_items(self.root, [{"rel_path": "   "}])

    def test_text_item(self):
        items = export_mod.build_items(
            self.root, [{"text": "工单正文", "filename": "工单.txt"}]
        )
        self.assertEqual(items[0].read(), "工单正文".encode("utf-8"))
        self.assertEqual(items[0].mime, "text/plain; charset=utf-8")

    def test_text_item_needs_filename(self):
        with self.assertRaises(export_mod.ExportError):
            export_mod.build_items(self.root, [{"text": "x"}])

    def test_filename_with_path_rejected(self):
        with self.assertRaises(export_mod.ExportError) as cm:
            export_mod.build_items(
                self.root, [{"text": "x", "filename": "sub/工单.txt"}]
            )
        self.assertIn("只能是单个文件名", str(cm.exception))

    def test_both_rel_and_text_rejected(self):
        with self.assertRaises(export_mod.ExportError):
            export_mod.build_items(
                self.root, [{"rel_path": "草稿/seg004_concat.mp4", "text": "x"}]
            )

    def test_empty_items_rejected(self):
        with self.assertRaises(export_mod.ExportError):
            export_mod.build_items(self.root, [])

    # ---- download 载荷 ----
    def test_payload_download_shape(self):
        items = export_mod.build_items(self.root, [{"rel_path": "草稿/seg004_concat.mp4"}])
        p = export_mod.payload_download(items)
        self.assertEqual(p["kind"], "download")
        self.assertEqual(p["filename"], "seg004_concat.mp4")
        self.assertEqual(p["size"], 11)
        self.assertEqual(base64.b64decode(p["base64"]), b"\x00\x01\x02fake-mp4")
        self.assertTrue(p["mime"].startswith("video/"))

    def test_payload_download_multi_zips(self):
        """M6 之前多条目直接 400，现在打成一个 zip（web 形态一次只能回一个文件）。"""
        items = export_mod.build_items(
            self.root,
            [{"rel_path": "草稿/seg004_concat.mp4"}, {"text": "x", "filename": "a.txt"}],
        )
        p = export_mod.payload_download(items)
        self.assertEqual(p["kind"], "download")
        self.assertTrue(p["filename"].endswith(".zip"))
        self.assertEqual(p["packed"], 2)
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(p["base64"]))) as zf:
            self.assertEqual(sorted(zf.namelist()), ["a.txt", "seg004_concat.mp4"])

    def test_payload_download_empty_file_rejected(self):
        empty = self.root / "草稿" / "empty.mp4"
        empty.write_bytes(b"")
        items = export_mod.build_items(self.root, [{"rel_path": "草稿/empty.mp4"}])
        with self.assertRaises(export_mod.ExportError):
            export_mod.payload_download(items)

    # ---- 写目录 ----
    def test_write_to_dir_no_overwrite(self):
        items = export_mod.build_items(self.root, [{"rel_path": "草稿/seg004_concat.mp4"}])
        out = self.tmp / "out"
        out.mkdir()
        r1 = export_mod.write_to_dir(items, out)
        self.assertEqual(r1["kind"], "dir")
        self.assertEqual(r1["count"], 1)
        first = out / "seg004_concat.mp4"
        self.assertTrue(first.is_file())

        # 第二次导出同一个文件：必须另起名，不许覆盖
        r2 = export_mod.write_to_dir(items, out)
        self.assertEqual(r2["count"], 1)
        self.assertTrue((out / "seg004_concat_02.mp4").is_file())
        self.assertEqual(first.read_bytes(), b"\x00\x01\x02fake-mp4")

        r3 = export_mod.write_to_dir(items, out)
        self.assertTrue((out / "seg004_concat_03.mp4").is_file())
        self.assertEqual(r3["count"], 1)

    def test_write_to_dir_text_item(self):
        items = export_mod.build_items(self.root, [{"text": "工单", "filename": "工单.txt"}])
        out = self.tmp / "out"
        out.mkdir()
        r = export_mod.write_to_dir(items, out)
        self.assertEqual(r["count"], 1)
        self.assertEqual((out / "工单.txt").read_text(encoding="utf-8"), "工单")

    def test_write_to_dir_multi(self):
        items = export_mod.build_items(
            self.root,
            [
                {"rel_path": "草稿/seg004_concat.mp4"},
                {"text": "清单", "filename": "清单.txt"},
            ],
        )
        out = self.tmp / "out"
        out.mkdir()
        r = export_mod.write_to_dir(items, out)
        self.assertEqual(r["count"], 2)

    # ---- 目标目录校验 ----
    def test_resolve_dest(self):
        with self.assertRaises(export_mod.ExportError):
            export_mod.resolve_dest("")
        with self.assertRaises(export_mod.ExportError):
            export_mod.resolve_dest("relative/path")
        with self.assertRaises(export_mod.ExportError) as cm:
            export_mod.resolve_dest(str(self.tmp / "nope"))
        self.assertEqual(cm.exception.status, 404)
        self.assertEqual(export_mod.resolve_dest(str(self.tmp)), self.tmp)


class ExportApiTest(unittest.TestCase):
    """HTTP 层：只测契约形状与错误码，不碰真实 config.json。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb_exportapi_"))
        self.root = self.tmp / "ws"
        (self.root / "草稿").mkdir(parents=True)
        (self.root / "草稿" / "x.mp4").write_bytes(b"abc")
        self.out = self.tmp / "out"
        self.out.mkdir()

        self._orig_cache = config_mod._config_cache
        self._cfg = _memory_config(self.root)
        config_mod._config_cache = self._cfg
        self.client = TestClient(app)

    def tearDown(self):
        config_mod._config_cache = self._orig_cache
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_active_workspace(self):
        self._cfg.active = None
        config_mod._config_cache = self._cfg
        r = self.client.post("/api/export", json={"items": [{"rel_path": "草稿/x.mp4"}]})
        self.assertEqual(r.status_code, 400, r.text)

    def test_download_mode(self):
        r = self.client.post("/api/export", json={"items": [{"rel_path": "草稿/x.mp4"}]})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["kind"], "download")
        self.assertEqual(base64.b64decode(body["base64"]), b"abc")

    def test_dir_mode(self):
        r = self.client.post(
            "/api/export",
            json={
                "items": [{"rel_path": "草稿/x.mp4"}, {"text": "清单", "filename": "清单.txt"}],
                "dest_dir": str(self.out),
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["kind"], "dir")
        self.assertEqual(body["count"], 2)
        self.assertTrue((self.out / "x.mp4").is_file())

    def test_dir_mode_no_overwrite_over_http(self):
        payload = {"items": [{"rel_path": "草稿/x.mp4"}], "dest_dir": str(self.out)}
        self.assertEqual(self.client.post("/api/export", json=payload).status_code, 200)
        self.assertEqual(self.client.post("/api/export", json=payload).status_code, 200)
        self.assertTrue((self.out / "x_02.mp4").is_file())

    def test_escape_rejected_http(self):
        r = self.client.post("/api/export", json={"items": [{"rel_path": "../secret.txt"}]})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("越出工作区", r.json()["detail"])

    def test_bad_url_like_dest(self):
        r = self.client.post(
            "/api/export",
            json={"items": [{"rel_path": "草稿/x.mp4"}], "dest_dir": "out/relative"},
        )
        self.assertEqual(r.status_code, 400, r.text)

    def test_empty_items_http(self):
        r = self.client.post("/api/export", json={"items": []})
        self.assertEqual(r.status_code, 400, r.text)

    def test_no_active_workspace(self):
        self._cfg.active = None
        config_mod._config_cache = self._cfg
        r = self.client.post("/api/export", json={"items": [{"rel_path": "草稿/x.mp4"}]})
        self.assertEqual(r.status_code, 400, r.text)


if __name__ == "__main__":
    unittest.main()
