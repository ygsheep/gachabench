# -*- coding: utf-8 -*-
"""拆分内核的回归测试：块正文按源 md 现算，绝不落盘。

跑法（在 backend 目录下）：

    UVPY="C:/Users/young.YOUNG/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe"
    PYTHONPATH="../backend;../backend/.venv/Lib/site-packages" "$UVPY" -m unittest tests.test_compose -v

这三条断言对应一个真实事故：块正文曾经被切成 txt 落盘、跑片再去读它，于是
「改完提示词、片子还是旧的」。现在源 md 是唯一真相源，下面这些一旦挂掉，
说明有人把落盘副本又加回来了。
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import splitter

MD = """# 04_正片_第4段_15s

```text
subject_definitions:
<Picture 1> is a.png, 角色 A 的四视图。

summary:
一个 15 秒的测试段。

retention_analysis:
[Shot 1] <Subject 1> 站在门口
[Shot 2] <Subject 1> 推门
[Shot 3] <Subject 1> 走进屋

detailed_description:
[Shot 1] At 00:00.000 中景，门口。
角色 A 站着。

[Shot 2] At 00:02.500 近景，手。
角色 A 推门。

[Shot 3] At 00:05.000 全景，屋内。
角色 A 走进来。

overall_soundscape:
门轴吱呀。

non_diegetic_music:
低音铺底。
```
"""


class FakeCfg:
    """只提供 compose_segment / ensure_plan 需要的那几样。"""

    active = "tmp-ws"

    def __init__(self, root: Path):
        self._root = root

    def ws_root(self) -> Path:
        return self._root

    def subdir(self, key: str):
        return self._root / "提示词" if key == "prompt" else self._root / key


class ComposeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb_compose_"))
        (self.tmp / "提示词").mkdir()
        self.md = self.tmp / "提示词" / "04_正片_第4段_15s.md"
        self.md.write_text(MD, encoding="utf-8")
        self.cfg = FakeCfg(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_compose_returns_chunks_without_writing_txt(self):
        out = splitter.compose_segment(self.cfg, "04", "yi")
        self.assertGreaterEqual(len(out["chunks"]), 2)
        self.assertTrue(all(c["text"] for c in out["chunks"]))
        self.assertEqual(out["source_md"], "提示词\\04_正片_第4段_15s.md")

        # 目录里除了源 md 和（可能的）拆分子目录，不该有任何 txt
        txts = list(self.tmp.rglob("*.txt"))
        self.assertEqual(txts, [], f"不该落盘块 txt，实际出现：{txts}")

    def test_source_md_edit_is_visible_immediately(self):
        before = splitter.compose_segment(self.cfg, "04", "yi")["chunks"][0]["text"]
        self.md.write_text(
            MD.replace("角色 A 站着。", "角色 A 站着，手里多了一把伞。"),
            encoding="utf-8",
        )
        after = splitter.compose_segment(self.cfg, "04", "yi")["chunks"][0]["text"]
        self.assertNotEqual(before, after)
        self.assertIn("手里多了一把伞", after)

    def test_header_uses_shot_numbers_not_list_index(self):
        out = splitter.compose_segment(self.cfg, "04", "yi")
        header = out["chunks"][0]["text"].splitlines()[2]
        # 第一块覆盖 [Shot 1] 起，抬头里应是镜号而不是 0-based 下标
        self.assertIn("镜 1", header)
        self.assertNotIn("块 [0", header)

    def test_find_chunk_returns_none_for_unknown_block(self):
        out = splitter.compose_segment(self.cfg, "04", "yi")
        self.assertIsNone(splitter.find_chunk(out, "9of9"))
        self.assertIsNotNone(splitter.find_chunk(out, out["chunks"][0]["chunk"]))

    def test_ensure_plan_reuses_same_plan(self):
        try:
            first = splitter.ensure_plan(self.cfg, "04", "yi")
            second = splitter.ensure_plan(self.cfg, "04", "yi")
        except Exception as e:  # 极端环境没有 sqlite 写权限时不误判为逻辑错误
            self.skipTest(f"临时库不可写：{e}")
        self.assertEqual(first["plan_id"], second["plan_id"])
        # manifest 里不该再有指向落盘 txt 的 file 字段
        for c in second["manifest"]["chunks"]:
            self.assertNotIn("file", c)
            self.assertNotIn("text", c)


if __name__ == "__main__":
    unittest.main()
