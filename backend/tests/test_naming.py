# -*- coding: utf-8 -*-
"""naming 的单元测试。

跑法（在 backend 目录下）：

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

⚠️ 沙箱里 `.venv/Scripts/python.exe` 是 uv trampoline，会报
`permission denied (os error 5)`。绕过办法（用 uv 的基础解释器 + venv 的
site-packages，效果等同）：

    UVPY="C:/Users/young.YOUNG/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe"
    PYTHONPATH=".;.venv/Lib/site-packages" "$UVPY" -m unittest discover -s tests -v

覆盖重点：
- ``01_1`` 那种写法不可解析（`_` 不能双语义），`01.1` 才解析得出来；
- 不符合模板的名字一律返回 None，**不做部分猜测**（猜错不报警，最危险）；
- 通过时块号三级回退，拿不到就 (None,'none')，绝不拿 0 或 1 顶上；
- 区间仲裁：最近生效的胜；同起点取更精确的；一条素材可有多个区间。
"""
from __future__ import annotations

import unittest

import naming


class TestShotParse(unittest.TestCase):
    def test_compound_key_with_dot(self):
        got = naming.parse_shot("01.1_成片.mp4")
        self.assertEqual((got.seg, got.chunk, got.use, got.seq), ("01", 1, "成片", None))
        self.assertEqual(got.key, "01.1")

    def test_whole_segment_without_chunk(self):
        got = naming.parse_shot("17_成片.mp4")
        self.assertEqual((got.seg, got.chunk), ("17", None))
        self.assertEqual(got.key, "17")

    def test_seq_suffix_is_not_use(self):
        got = naming.parse_shot("01.1_成片_02.mp4")
        self.assertEqual(got.seq, 2)
        self.assertEqual(got.use, "成片")

    def test_one_digit_seg_is_normalized(self):
        self.assertEqual(naming.parse_shot("1_成片.mp4").seg, "01")
        self.assertEqual(naming.parse_shot("1.2_成片.mp4").key, "01.2")

    def test_underscore_compound_is_rejected(self):
        # 01_1_SC01 —— 用户原案的写法。`_` 若同时当层级连接符和字段分隔符，
        # split 之后无法判断哪一个是「段/块」的层级，只能靠位置猜。这里锁死为不可解析。
        self.assertIsNone(naming.parse_shot("01_1_成片.mp4"))

    def test_legacy_comfy_output_is_not_a_shot_name(self):
        self.assertIsNone(naming.parse_shot("17_第17段_1of3_00001.mp4"))

    def test_asset_name_is_not_a_shot(self):
        self.assertIsNone(naming.parse_shot("白兴学_国漫3D_四视图.png"))


class TestShotCompose(unittest.TestCase):
    def test_roundtrip(self):
        name = naming.compose_shot("1", 1, "成片", ".mp4")
        self.assertEqual(name, "01.1_成片.mp4")
        self.assertEqual(naming.parse_shot(name).key, "01.1")

    def test_roundtrip_with_seq(self):
        name = naming.compose_shot(17, 2, "成片", "mp4", seq=2)
        self.assertEqual(name, "17.2_成片_02.mp4")
        got = naming.parse_shot(name)
        self.assertEqual((got.seg, got.chunk, got.seq), ("17", 2, 2))

    def test_ext_normalized(self):
        self.assertEqual(naming.compose_shot(3, None, "分镜", "PNG"), "03_分镜.png")

    def test_bad_seg_raises(self):
        with self.assertRaises(ValueError):
            naming.compose_shot("草稿", None)


class TestAssetParse(unittest.TestCase):
    def test_real_workspace_names(self):
        # 这些是 D:/Notely/workspace/AIGC/复试2/素材 里真实存在的文件名
        got = naming.parse_asset("白兴学_国漫3D_四视图.png")
        self.assertEqual(got.subject, "白兴学")
        self.assertEqual(got.variant, "国漫3D_四视图")
        self.assertIsNone(got.use)

        got = naming.parse_asset("白兴学_配音.mp3")
        self.assertEqual((got.subject, got.variant, got.use), ("白兴学", None, "配音"))

        got = naming.parse_asset("客厅_实拍_视角1.png")
        self.assertEqual((got.subject, got.variant), ("客厅", "实拍_视角1"))

    def test_state_variant(self):
        got = naming.parse_asset("教室_破坏_参考.png")
        self.assertEqual((got.subject, got.variant, got.use), ("教室", "破坏", "参考"))
        got = naming.parse_asset("教室_修复_参考.png")
        self.assertEqual(got.variant, "修复")

    def test_space_splits_like_the_legacy_rule(self):
        # 真实存在 `刘强 Q版.png`。旧的「按 _/空白切、第一段=主体」把它归到 `刘强`
        # 名下；解析器必须给出同样的结果，否则改造后这 5 个文件会换分组（无声回归）。
        got = naming.parse_asset("刘强 Q版.png")
        self.assertEqual((got.subject, got.variant), ("刘强", "Q版"))

    def test_seq_is_stripped(self):
        got = naming.parse_asset("白兴学_参考_02.png")
        self.assertEqual((got.use, got.seq), ("参考", 2))

    def test_lone_use_word_stays_subject(self):
        # 只有一个字段时不能把 `参考` 当用途吃掉，否则主体变空
        self.assertEqual(naming.parse_asset("参考.png").subject, "参考")

    def test_never_returns_none(self):
        self.assertEqual(naming.parse_asset("weird (1).png").subject, "weird")


class TestAssetCompose(unittest.TestCase):
    def test_roundtrip(self):
        name = naming.compose_asset("教室", "破坏", "参考", ".png")
        self.assertEqual(name, "教室_破坏_参考.png")
        got = naming.parse_asset(name)
        self.assertEqual((got.subject, got.variant, got.use), ("教室", "破坏", "参考"))

    def test_underscore_in_subject_is_demoted(self):
        # 主体里出现 `_` 会被当成字段分隔符 → 必须挡在组装这一层
        self.assertEqual(naming.compose_asset("白_兴学", None, "参考"), "白-兴学_参考.png")

    def test_variant_keeps_underscore(self):
        self.assertEqual(
            naming.compose_asset("白兴学", "国漫3D_四视图", None), "白兴学_国漫3D_四视图.png"
        )

    def test_roundtrip_with_seq(self):
        name = naming.compose_asset("白兴学", None, "参考", ".png", seq=2)
        self.assertEqual(name, "白兴学_参考_02.png")
        self.assertEqual(naming.parse_asset(name).seq, 2)


class TestClassify(unittest.TestCase):
    def test_shot(self):
        self.assertEqual(naming.classify("01.1_成片.mp4"), "shot")
        self.assertEqual(naming.classify("17_成片.mp4"), "shot")

    def test_asset(self):
        self.assertEqual(naming.classify("白兴学_参考.png"), "asset")

    def test_unknown_for_legacy_dump(self):
        # 像段号却不符合模板：归不了类，但也绝不能当素材（否则 `17` 变成主体名开头）
        self.assertEqual(naming.classify("17_第17段_1of3_00001.mp4"), "unknown")


class TestChunkFallback(unittest.TestCase):
    def test_from_template_name(self):
        self.assertEqual(naming.resolve_chunk("01.1_成片.mp4"), (1, "name"))

    def test_from_comfy_filename(self):
        self.assertEqual(
            naming.resolve_chunk("17_第17段_1of3_00001.mp4"), (1, "filename")
        )
        self.assertEqual(
            naming.resolve_chunk("17_第17段_3of3_00001.mp4"), (3, "filename")
        )

    def test_falls_back_to_card(self):
        self.assertEqual(
            naming.resolve_chunk("output_00042.mp4", "2of3"), (2, "card")
        )
        self.assertEqual(naming.resolve_chunk("output_00042.mp4", 2), (2, "card"))

    def test_never_guesses(self):
        # 🔴 拿不到就是 none，由调用方标「缺块号」。猜错的代价是归错段，且没人会发现。
        self.assertEqual(naming.resolve_chunk("output_00042.mp4"), (None, "none"))
        self.assertEqual(naming.resolve_chunk("17_成片.mp4", None), (None, "none"))

    def test_whole_segment_name_does_not_invent_chunk(self):
        self.assertEqual(naming.resolve_chunk("17_成片.mp4", "1of3"), (1, "card"))


class TestPickForSeg(unittest.TestCase):
    def test_latest_start_wins(self):
        items = [
            {"id": "破坏", "seg_from": 5, "seg_to": None},
            {"id": "修复", "seg_from": 10, "seg_to": None},
        ]
        self.assertEqual(naming.pick_for_seg(items, 7)["id"], "破坏")
        self.assertEqual(naming.pick_for_seg(items, 12)["id"], "修复")
        self.assertIsNone(naming.pick_for_seg(items, 4))

    def test_closed_range_is_respected(self):
        items = [
            {"id": "破坏", "seg_from": 5, "seg_to": 9},
            {"id": "修复", "seg_from": 10, "seg_to": None},
        ]
        self.assertEqual(naming.pick_for_seg(items, 9)["id"], "破坏")
        self.assertEqual(naming.pick_for_seg(items, 10)["id"], "修复")

    def test_same_start_prefers_narrower(self):
        items = [
            {"id": "宽", "seg_from": 5, "seg_to": None},
            {"id": "窄", "seg_from": 5, "seg_to": 9},
        ]
        self.assertEqual(naming.pick_for_seg(items, 7)["id"], "窄")

    def test_multiple_ranges_for_same_asset(self):
        # 破坏 5-9 → 修复 10-15 → 又用回 破坏 16 起（同一条素材两个区间）
        items = [
            {"id": "破坏", "seg_from": 5, "seg_to": 9},
            {"id": "修复", "seg_from": 10, "seg_to": 15},
            {"id": "破坏", "seg_from": 16, "seg_to": None},
        ]
        self.assertEqual(naming.pick_for_seg(items, 6)["id"], "破坏")
        self.assertEqual(naming.pick_for_seg(items, 12)["id"], "修复")
        self.assertEqual(naming.pick_for_seg(items, 20)["id"], "破坏")

    def test_string_seg_numbers_from_db(self):
        items = [{"id": "修复", "seg_from": "09", "seg_to": "10"}]
        self.assertEqual(naming.pick_for_seg(items, "10")["id"], "修复")

    def test_bad_segment_returns_none(self):
        self.assertIsNone(naming.pick_for_seg([], "第几段"))


class TestSuccession(unittest.TestCase):
    def test_previous_closes_before_new_start(self):
        self.assertEqual(naming.succession_to(10), 9)
        self.assertEqual(naming.succession_to("10"), 9)
        self.assertIsNone(naming.succession_to(None))


class TestMisc(unittest.TestCase):
    def test_seg_from_path(self):
        self.assertEqual(naming.seg_from_path("01.1_成片.mp4"), "01")
        self.assertEqual(naming.seg_from_path("17_第17段_1of3_00001.mp4"), "17")
        self.assertIsNone(naming.seg_from_path("白兴学_参考.png"))

    def test_guess_kind(self):
        self.assertEqual(naming.guess_kind(".MP4"), "video")
        self.assertEqual(naming.guess_kind("png"), "image")
        self.assertEqual(naming.guess_kind(".mp3"), "audio")
        self.assertEqual(naming.guess_kind(".flac"), "audio")
        self.assertEqual(naming.guess_kind(".psd"), "other")

    def test_guess_kind_accepts_whole_filename(self):
        self.assertEqual(naming.guess_kind("白兴学_国漫3D_四视图.PNG"), "image")
        self.assertEqual(naming.guess_kind("01.1_成片.mp4"), "video")
        self.assertEqual(naming.guess_kind("_env.py"), "other")

    def test_unique_keeps_order(self):
        self.assertEqual(naming.unique(["a", "b", "a"]), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
