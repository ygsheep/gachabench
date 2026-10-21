# -*- coding: utf-8 -*-
"""textclean 的单元测试。

跑法（在 backend 目录下）：

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

⚠️ 沙箱里 `.venv/Scripts/python.exe` 是 uv trampoline，会报
`permission denied (os error 5)`。绕过办法（用 uv 的基础解释器 + venv 的
site-packages，效果等同）：

    UVPY="C:/Users/young.YOUNG/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe"
    PYTHONPATH="../backend;../backend/.venv/Lib/site-packages" "$UVPY" -m unittest discover -s tests -v

覆盖重点：
- 通用层（clean_basic）只做卫生、不改内容；
- 网文层（clean_ads）只删「明确在说站点」的东西，不误伤正文；
- 域名后紧跟中文不会被 URL 正则一起吃掉（NBE 实测踩过的坑）；
- 默认 `strip_ads=False`，保证剧本 / 提示词类文本不会被广告词表碰。
"""
from __future__ import annotations

import unittest

import textclean


class TestCleanBasic(unittest.TestCase):
    def test_strip_html_keeps_paragraph_structure(self):
        raw = "<div class='page-content'>第一段<br>第二段<p>第三段</p></div>"
        # strip_html 是「原始替换」，标签位置会留下换行；折叠空行是 clean_basic 的活
        self.assertEqual(textclean.strip_html(raw).strip("\n"), "第一段\n第二段\n第三段")

    def test_drop_script_style_blocks_with_content(self):
        raw = "<style>.a{color:red}</style>正文<script>alert(1)</script>尾巴"
        self.assertEqual(textclean.strip_html(raw), "正文尾巴")

    def test_comments_are_removed(self):
        self.assertEqual(textclean.strip_html("甲<!-- 注释 -->乙"), "甲乙")

    def test_entity_unescape_after_tag_strip(self):
        # 设计如此：反转义后露出的真标签会补洗一次（NBE 原有行为，防「整段转义塞进 JSON」）
        self.assertEqual(textclean.clean_basic("&lt;div&gt;不是标签&lt;/div&gt;"), "不是标签")
        self.assertEqual(textclean.clean_basic("甲&nbsp;乙"), "甲 乙")

    def test_double_escaped_html_is_cleaned_twice(self):
        raw = "&lt;p&gt;第一段&lt;br&gt;第二段&lt;/p&gt;"
        self.assertEqual(textclean.clean_basic(raw), "第一段\n第二段")

    def test_zero_width_chars_removed(self):
        self.assertEqual(textclean.clean_basic("甲\u200b乙\ufeff丙"), "甲乙丙")

    def test_whitespace_collapsed_and_blank_lines_folded(self):
        raw = "甲　 乙 \n\n\n丙\t丁"
        self.assertEqual(textclean.clean_basic(raw), "甲 乙\n\n丙 丁")

    def test_crlf_normalized(self):
        self.assertEqual(textclean.clean_basic("甲\r\n乙"), "甲\n乙")

    def test_single_line_collapses_newlines(self):
        self.assertEqual(textclean.clean_basic("第一章  觉醒\n副标题", single_line=True), "第一章 觉醒 副标题")

    def test_empty_input(self):
        self.assertEqual(textclean.clean_basic(""), "")
        self.assertIsNone(textclean.clean_basic(None) or None)


class TestCleanAds(unittest.TestCase):
    def test_url_after_chinese_is_not_eaten(self):
        # 关键坑：URL 正则的字符类排除了 CJK，否则后半句会被一起吃掉
        self.assertEqual(textclean._strip_urls("www.a.com正文一"), "正文一")

    def test_bare_url_line_is_dropped(self):
        self.assertEqual(textclean.clean_ads("正文\nhttp://example.com/x\n尾巴", single_line=False).split("\n"),
                         ["正文", "尾巴"])

    def test_hard_ad_line_removed(self):
        out = textclean.clean_ads("正文\n最新网址：www.xxx.info\n尾巴")
        self.assertNotIn("最新网址", out)
        self.assertIn("正文", out)
        self.assertIn("尾巴", out)

    def test_normal_sentence_with_ad_word_is_kept(self):
        # 「电视里正在播广告。」是正常句子：句尾是句号，不该被弱特征误删
        out = textclean.clean_ads("电视里正在播广告。")
        self.assertEqual(out, "电视里正在播广告。")

    def test_short_line_with_soft_word_is_removed(self):
        out = textclean.clean_ads("正文\n下载本书\n尾巴")
        self.assertNotIn("下载本书", out)
        self.assertIn("正文", out)

    def test_inline_ad_fragment_eaten_with_punctuation(self):
        out = textclean.clean_ads("他把书放下了，最新网址：www.x.com，然后出门。")
        self.assertIn("他把书放下了", out)
        self.assertNotIn("最新网址", out)

    def test_line_with_only_punctuation_is_dropped(self):
        out = textclean.clean_ads("正文\n——\n尾巴")
        self.assertEqual(out.split("\n"), ["正文", "尾巴"])


class TestCleanTextEntry(unittest.TestCase):
    def test_default_does_not_strip_ads(self):
        # 红线：默认走通用层。剧本/提示词类文本一个字都不该被广告词表碰。
        raw = "最新网址：www.xxx.info 请记住本站"
        self.assertEqual(textclean.clean_text(raw), raw.strip())

    def test_strip_ads_opt_in(self):
        self.assertNotIn("最新网址", textclean.clean_text("最新网址：www.x.com", strip_ads=True))

    def test_prompt_like_text_is_untouched(self):
        # 段 md 的 §④ 内容必须原样通过：这是定点工单行号可信度的前提
        block = (
            "[Shot 2] At 00:02.500, INS. 特写位（俯拍茶几）\n"
            "<Subject 1> is 白兴学，…\n"
            "<Audio 1> 白兴学_配音.mp3"
        )
        self.assertEqual(textclean.clean_text(block), block)
        # 行数不变：任何清洗都不能移动行号
        self.assertEqual(len(textclean.clean_text(block).split("\n")), 3)

    def test_clean_lines(self):
        self.assertEqual(
            textclean.clean_lines(["第一章  觉醒", "<b>第二章 闭关</b>", None]),
            ["第一章 觉醒", "第二章 闭关", ""],
        )


class TestLabelInvariant(unittest.TestCase):
    """🔴 标签不变式：`<Picture N>` / `<Subject N>` / `<Audio N>` 必须原样穿过清洗。

    这三个标签是段 md 的引用锚，H3 靠它们把参考图 / 参考音频接到人身上。
    一旦被当 HTML 标签削掉，提示词就指错了对象 —— 而画面照样出得来，
    光看片子发现不了。这里每一层都要守。
    """

    PROMPT_BLOCK = (
        "[Shot 2] At 00:02.500, INS. 特写位（俯拍茶几）\n"
        "<Subject 1> is 白兴学，…\n"
        "<Picture 2> is 上一段的末帧…\n"
        "<Audio 1> 白兴学_配音.mp3"
    )

    def test_labels_survive_clean_basic(self):
        out = textclean.clean_basic(self.PROMPT_BLOCK)
        for tag in ("<Subject 1>", "<Picture 2>", "<Audio 1>"):
            self.assertIn(tag, out)

    def test_labels_survive_clean_ads(self):
        # 连去广告层也不能碰（万一哪天有人给 md 开了 strip_ads）
        out = textclean.clean_ads(self.PROMPT_BLOCK)
        for tag in ("<Subject 1>", "<Picture 2>", "<Audio 1>"):
            self.assertIn(tag, out)

    def test_multi_digit_labels(self):
        out = textclean.clean_basic("<Subject 12> 与 <Audio 3> 与 <Picture 10>")
        self.assertIn("<Subject 12>", out)
        self.assertIn("<Audio 3>", out)
        self.assertIn("<Picture 10>", out)

    def test_lowercase_labels_also_protected(self):
        self.assertIn("<subject 1>", textclean.clean_basic("<subject 1> 小写也要活"))

    def test_real_html_tags_still_stripped(self):
        # 前瞻只排除「关键字 + 空白 + 纯数字」形态，真 HTML 标签照剥
        self.assertNotIn("<div", textclean.clean_basic("<div>正文</div>"))
        self.assertNotIn("<picture", textclean.clean_basic('<picture src="1">正文</picture>'))
        self.assertNotIn("<subject", textclean.clean_basic("<subject class=x>正文</subject>"))

    def test_prompt_block_line_count_unchanged(self):
        # 行号是定点工单的地基：清洗不能移动行
        before = len(self.PROMPT_BLOCK.split("\n"))
        self.assertEqual(len(textclean.clean_basic(self.PROMPT_BLOCK).split("\n")), before)
        self.assertEqual(len(textclean.clean_ads(self.PROMPT_BLOCK).split("\n")), before)


if __name__ == "__main__":
    unittest.main()
