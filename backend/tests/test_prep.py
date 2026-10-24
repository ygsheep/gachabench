# -*- coding: utf-8 -*-
"""备料区验收测试。

跑法（backend 目录下）：

    PYTHONPATH=".;.venv/Lib/site-packages" \
      "C:/Users/young.YOUNG/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe" \
      -m unittest tests.test_prep -v

覆盖重点：
- 只认素材上传表里的 `Picture N` / `Audio N`，散文里提到的文件名一律不算
  （"撤掉 `风格_国漫3D_参考.webp`" 这种句子照它备料会是灾难）；
- 备料文件名 == 提示词里写的名字（换文件不改提示词）；
- 默认不覆盖已存在的备料文件（人可能手工换过）。
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import config as config_mod
import prep

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

SAMPLE_MD = """# 第 1 段

## ② 素材上传表（**5 张图**，按此顺序）

| H3 标签 | 中文名称素材 | 用途 |
| --- | --- | --- |
| Picture 1 | `白陌_写实_四视图.png` | 人物参考·锁白陌 |
| Picture 2 | `客厅_实拍_视角1.png` | 场景参考 |
| Audio 1 | `白陌_配音.mp3` | 台词音色 |

> 🔴 撤掉 `风格_国漫3D_参考.webp` 之后画风仍成立，别再把这张塞回去。
"""


class TestRequirements(unittest.TestCase):
    def test_reads_table_rows_only(self):
        got = prep.requirements(SAMPLE_MD)
        self.assertEqual(
            [(i["n"], i["kind"], i["filename"]) for i in got],
            [
                (1, "image", "白陌_写实_四视图.png"),
                (2, "image", "客厅_实拍_视角1.png"),
                (1, "audio", "白陌_配音.mp3"),
            ],
        )

    def test_prose_mention_is_ignored(self):
        names = [i["filename"] for i in prep.requirements(SAMPLE_MD)]
        self.assertNotIn("风格_国漫3D_参考.webp", names)

    def test_empty_slots_are_skipped(self):
        text = "| Picture 3 | - | 无 |\n| Picture 4 |  | 空 |\n"
        self.assertEqual(prep.requirements(text), [])

    def test_dedupes_same_name(self):
        text = "| Picture 1 | `a.png` | x |\n| Picture 2 | `a.png` | y |\n"
        self.assertEqual(len(prep.requirements(text)), 1)

    def test_no_backticks_still_parsed(self):
        text = "| Picture 1 | 白陌.png | 人物 |\n"
        self.assertEqual(prep.requirements(text)[0]["filename"], "白陌.png")


class PrepCase(unittest.TestCase):
    def setUp(self) -> None:
        self._origin = config_mod._config_cache
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-prep-")).resolve()
        self.root = self.tmp / "ws"
        for sub in ("提示词", "资产/人物", "素材"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        (self.root / "提示词" / "01_正片_第1段_15s.md").write_text(SAMPLE_MD, encoding="utf-8")
        (self.root / "资产" / "人物" / "白陌_写实_四视图.png").write_bytes(PNG)
        self.cfg = config_mod.Config(
            workspaces=[config_mod.WorkspaceEntry(name="WS", root=str(self.root))],
            active="WS",
        )
        config_mod._config_cache = self.cfg

    def tearDown(self) -> None:
        config_mod._config_cache = self._origin
        shutil.rmtree(self.tmp, ignore_errors=True)

    def staged(self, name: str = "白陌_写实_四视图.png") -> Path:
        return self.root / "素材" / "01" / name


class TestPlanAndApply(PrepCase):
    def test_plan_separates_copyable_and_missing(self):
        plan = prep.plan_prep(self.cfg, "1")
        self.assertEqual(plan["seg"], "01")
        self.assertEqual(plan["prompt_file"], "01_正片_第1段_15s.md")
        status = {i["filename"]: i["status"] for i in plan["items"]}
        self.assertEqual(status["白陌_写实_四视图.png"], "copyable")
        self.assertEqual(status["客厅_实拍_视角1.png"], "missing")
        self.assertEqual(status["白陌_配音.mp3"], "missing")
        self.assertEqual((plan["copyable"], plan["missing"], plan["ready"]), (1, 2, 0))

    def test_apply_copies_with_prompt_filename(self):
        stats = prep.apply_prep(self.cfg, "1")
        self.assertEqual(stats["copied"], 1)
        # 备料区里的名字 == 提示词里写的名字，提示词不用改
        self.assertTrue(self.staged().is_file())
        self.assertEqual(sorted(stats["missing"]), ["客厅_实拍_视角1.png", "白陌_配音.mp3"])

    def test_second_apply_is_noop(self):
        prep.apply_prep(self.cfg, "1")
        stats = prep.apply_prep(self.cfg, "1")
        self.assertEqual(stats["copied"], 0)
        self.assertEqual(stats["skipped"], 1)

    def test_hand_replaced_file_is_not_clobbered(self):
        self.staged().parent.mkdir(parents=True)
        self.staged().write_bytes(b"HAND REPLACED")
        stats = prep.apply_prep(self.cfg, "1")
        self.assertEqual(stats["copied"], 0)
        self.assertEqual(self.staged().read_bytes(), b"HAND REPLACED")

    def test_overwrite_is_explicit(self):
        self.staged().parent.mkdir(parents=True)
        self.staged().write_bytes(b"HAND REPLACED")
        stats = prep.apply_prep(self.cfg, "1", overwrite=True)
        self.assertEqual(stats["copied"], 1)
        self.assertEqual(self.staged().read_bytes(), PNG)

    def test_only_limits_scope(self):
        stats = prep.apply_prep(self.cfg, "1", only=["客厅_实拍_视角1.png"])
        self.assertEqual(stats["copied"], 0)
        self.assertFalse(self.staged().exists())

    def test_prep_path_used_by_run(self):
        self.assertIsNone(prep.prep_path(self.cfg, "1", "白陌_写实_四视图.png"))
        prep.apply_prep(self.cfg, "1")
        self.assertIsNotNone(prep.prep_path(self.cfg, "1", "白陌_写实_四视图.png"))

    def test_asset_lookup_falls_back_to_other_ext(self):
        (self.root / "资产" / "人物" / "客厅_实拍_视角1.jpg").write_bytes(PNG)
        plan = prep.plan_prep(self.cfg, "1")
        status = {i["filename"]: i["status"] for i in plan["items"]}
        self.assertEqual(status["客厅_实拍_视角1.png"], "copyable")

    def test_missing_prompt_md_is_reported(self):
        plan = prep.plan_prep(self.cfg, "9")
        self.assertIsNone(plan["prompt_file"])
        self.assertTrue(plan["problems"])

    def test_items_carry_preview_url(self):
        plan = prep.plan_prep(self.cfg, "1")
        by_name = {i["filename"]: i for i in plan["items"]}
        got = by_name["白陌_写实_四视图.png"]
        self.assertTrue(got["preview"].startswith("/api/assets/file/"))
        self.assertIn("%E7%99%BD%E9%99%8C", got["preview"])  # 中文要 URL 编码
        self.assertIsNone(by_name["客厅_实拍_视角1.png"]["preview"])

    def test_preview_after_staging(self):
        prep.apply_prep(self.cfg, "1")
        plan = prep.plan_prep(self.cfg, "1")
        got = {i["filename"]: i for i in plan["items"]}["白陌_写实_四视图.png"]
        self.assertEqual(got["status"], "in-prep")
        self.assertIn("/%E7%B4%A0%E6%9D%90/01/", got["preview"])


if __name__ == "__main__":
    unittest.main()
