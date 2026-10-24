# -*- coding: utf-8 -*-
"""资产条目序列化的验收测试（`_to_item` 的 `seg` 字段）。

跑法（backend 目录下）：

    PYTHONPATH=".;.venv/Lib/site-packages" \
      "C:/Users/young.YOUNG/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe" \
      -m unittest tests.test_asset_item -v

为什么值得单独测：资产页的分组规则是「有主体 → 按主体；没主体但有段 → 按段」。
镜头类（`17.1_成片.mp4`）没有主体，如果后端不把段号带出来，它们会全部倒进
「未归组」——比现在的空态还难用。
"""
from __future__ import annotations

import unittest

from modules.assets.api import _to_item
from modules.assets.models import IndexAsset, now_iso


def asset(name: str, subject: str = "") -> IndexAsset:
    return IndexAsset(
        id="a1",
        path=f"资产/未分类/{name}",
        name=name,
        dir_path="资产/未分类",
        role="asset",
        subject=subject,
        category="video",
        ext=".mp4",
        size=1,
        mtime=0.0,
        sha256=None,
        hash_status="full",
        media_kind="video",
        status="active",
        created_at=now_iso(),
        updated_at=now_iso(),
    )


class TestSegField(unittest.TestCase):
    def test_shot_name_carries_seg(self):
        got = _to_item(asset("17.1_成片.mp4"), None)
        self.assertEqual(got.seg, "17")
        self.assertEqual(got.subject, "")

    def test_whole_segment_name(self):
        self.assertEqual(_to_item(asset("18_成片.mp4"), None).seg, "18")

    def test_asset_name_has_no_seg(self):
        got = _to_item(asset("白兴学_国漫3D_四视图.png", subject="白兴学"), None)
        self.assertIsNone(got.seg)
        self.assertEqual(got.subject, "白兴学")

    def test_subject_override_wins(self):
        from modules.assets.models import AssetMeta

        meta = AssetMeta(asset_id="a1", subject_override="白兴学")
        got = _to_item(asset("17.1_成片.mp4"), meta)
        self.assertEqual(got.subject, "白兴学")
        self.assertIsNone(got.seg, "有人工主体时不该再按段分组")


if __name__ == "__main__":
    unittest.main()
