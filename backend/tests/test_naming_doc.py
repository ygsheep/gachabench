# -*- coding: utf-8 -*-
"""自动生成的命名规范 README 的验收测试。

跑法（backend 目录下）：

    PYTHONPATH=".;.venv/Lib/site-packages" \
      "C:/Users/young.YOUNG/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe" \
      -m unittest tests.test_naming_doc -v

覆盖重点：
- 没有就写、一样就不动、旧了就更新；
- 🔴 **用户自己写的 README 一个字节都不碰**（没有标记行 = 不是我们的）；
- 资产目录没配好时不代建目录；
- 文档内容必须真的说清「镜头类/素材类、sidecar、区间不进名」。
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import config as config_mod
import naming_doc


class DocCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-doc-")).resolve()
        self.asset = self.tmp / "素材"
        self.asset.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def readme(self) -> Path:
        return self.asset / naming_doc.README_NAME


class TestEnsureReadme(DocCase):
    def test_creates_when_missing(self):
        got = naming_doc.ensure_readme(self.asset)
        self.assertEqual(got["reason"], "created")
        self.assertTrue(got["written"])
        text = self.readme().read_text(encoding="utf-8")
        self.assertTrue(text.startswith(naming_doc.MARKER_PREFIX))
        self.assertIn("01.1_成片.mp4", text)
        self.assertIn(".prompt.json", text)

    def test_second_run_is_noop(self):
        naming_doc.ensure_readme(self.asset)
        before = self.readme().stat().st_mtime_ns
        got = naming_doc.ensure_readme(self.asset)
        self.assertEqual(got["reason"], "current")
        self.assertFalse(got["written"])
        self.assertEqual(self.readme().stat().st_mtime_ns, before)

    def test_stale_version_is_updated(self):
        self.readme().write_text(
            "<!-- gachabench:naming-doc sha=deadbeef -->\n\n旧内容\n", encoding="utf-8"
        )
        got = naming_doc.ensure_readme(self.asset)
        self.assertEqual(got["reason"], "updated")
        self.assertIn("01.1_成片.mp4", self.readme().read_text(encoding="utf-8"))

    def test_foreign_readme_is_never_touched(self):
        mine = "# 我自己的说明\n\n别动我。\n"
        self.readme().write_text(mine, encoding="utf-8")
        got = naming_doc.ensure_readme(self.asset)
        self.assertEqual(got["reason"], "foreign")
        self.assertFalse(got["written"])
        self.assertEqual(self.readme().read_text(encoding="utf-8"), mine)

    def test_missing_dir_is_not_created(self):
        target = self.tmp / "还不存在"
        got = naming_doc.ensure_readme(target)
        self.assertEqual(got["reason"], "missing-dir")
        self.assertFalse(target.exists())

    def test_bom_file_with_marker_is_handled(self):
        body = naming_doc.render()
        self.readme().write_text("\ufeff" + body, encoding="utf-8")
        got = naming_doc.ensure_readme(self.asset)
        self.assertEqual(got["reason"], "current")


class TestContent(unittest.TestCase):
    def test_marker_carries_content_hash(self):
        text = naming_doc.render()
        sha = naming_doc._marker_sha(text)
        self.assertEqual(len(sha or ""), 8)

    def test_covers_the_rules_agents_need(self):
        text = naming_doc.render()
        for needle in [
            "01.1_成片.mp4",          # 镜头类模板
            "白兴学_国漫3D_四视图.png",  # 素材类模板
            "不要写 `01_1`",           # 最容易犯的错
            ".prompt.json",            # sidecar 载体
            "永不回写",
            "未分类",                   # 交付落点
            "不写进文件名",             # 区间
        ]:
            self.assertIn(needle, text, f"README 缺少关键说明：{needle}")


class TestEnsureForWorkspace(DocCase):
    def setUp(self) -> None:
        super().setUp()
        self._origin = config_mod._config_cache

    def tearDown(self) -> None:
        config_mod._config_cache = self._origin
        super().tearDown()

    def ws(self, folders: dict | None = None):
        entry = config_mod.WorkspaceEntry(
            name="WS", root=str(self.tmp), folders=folders or {}
        )
        cfg = config_mod.Config(workspaces=[entry], active="WS")
        config_mod._config_cache = cfg
        return cfg, entry

    def test_writes_into_configured_asset_dir(self):
        cfg, entry = self.ws({"asset": "素材"})
        got = naming_doc.ensure_for_workspace(cfg, entry)
        self.assertTrue(got["written"])
        self.assertTrue(self.readme().exists())

    def test_skips_when_asset_dir_undetermined(self):
        cfg, entry = self.ws({"asset": ""})
        self.assertIsNone(naming_doc.ensure_for_workspace(cfg, entry))

    def test_skips_without_workspace(self):
        cfg, _ = self.ws()
        self.assertIsNone(naming_doc.ensure_for_workspace(cfg, None))


if __name__ == "__main__":
    unittest.main()
