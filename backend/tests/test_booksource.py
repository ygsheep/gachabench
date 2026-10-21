# -*- coding: utf-8 -*-
"""迷你书源引擎 + 联网采集路由的单测。

🔴 全部走**本地 fixture HTTP 站点**，不碰外网：
  1. 这里的断言必须稳定，不能因为源站改版或代理拦截而红；
  2. 真实站点在本机环境里会被代理改写（实测返回 202），拿它做断言是自欺。

覆盖：DOM/选择器 → 规则求值 → URL 模板 → 书源加载与 unsupported 判定 →
      对 fixture 站点跑完 搜索/目录/正文 → 路由开关（403 门禁）→ 导入原著库。
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import booksource as bs

# ---------------------------------------------------------------- fixture 站点

SEARCH_HTML = """<html><body>
<div class="listboxw">
  <dl>
    <dd><p><a href="/book/1">凡人修仙传</a></p></dd>
    <span class="author fl">忘语</span>
    <div class="pic fl"><img src="x.jpg" data-original="/cover/1.jpg"></div>
  </dl>
  <dl>
    <dd><p><a href="/book/2">诛仙</a></p></dd>
    <span class="author fl">萧鼎</span>
    <div class="pic fl"><img src="y.jpg" data-original="/cover/2.jpg"></div>
  </dl>
</div>
</body></html>"""

TOC_HTML = """<html><body><div class="cate-list">
<a href="/c/1">第一章 山村</a>
<a href="/c/2">第二章 青牛</a>
</div></body></html>"""

CHAPTER_HTML = """<html><body>
<div id="j_content"><p>清晨，山村还很安静。</p><p>他背起行囊出了门。</p></div>
</body></html>"""

# ---- API 型站点（JSON 响应 + {{字段}} 模板 + ruleBookInfo.tocUrl）----

#: 搜索结果直接是 JSON —— 这批书源里有一半是这种 API 站
JSON_SEARCH = json.dumps(
    {
        "code": 0,
        "data": {
            "books": [
                {"id": 11, "name": "斗破苍穹", "author": "天蚕土豆",
                 "cover": "/img/11.jpg", "intro": "三十年河东"},
                {"id": 12, "name": "武动乾坤", "author": "天蚕土豆",
                 "cover": "/img/12.jpg", "intro": "百世轮回"},
            ]
        },
    },
    ensure_ascii=False,
)

#: 详情页只是个壳，真正的目录地址写在这里（书源里对应 ruleBookInfo.tocUrl）
DETAIL_HTML = """<html><body><div class="book-card">
<h1>斗破苍穹</h1><a class="to-toc" href="/catalog">查看目录</a>
</div></body></html>"""

CATALOG_JSON = json.dumps(
    {"chapters": [{"cid": 101, "title": "第一章"}, {"cid": 102, "title": "第二章"}]},
    ensure_ascii=False,
)

#: GBK 站点的搜索页
GBK_HTML = """<html><body><div class="listboxw">
<dl><dd><p><a href="/book/9">凡人修仙传</a></p></dd></dl>
</div></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def _reply(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/search":
            self._reply(SEARCH_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/search":
            self._reply(JSON_SEARCH.encode("utf-8"), "application/json; charset=utf-8")
        elif path.startswith("/book/"):
            self._reply(TOC_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/detail":
            self._reply(DETAIL_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/catalog":
            self._reply(CATALOG_JSON.encode("utf-8"), "application/json; charset=utf-8")
        elif path.startswith("/c/"):
            self._reply(CHAPTER_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/gbk":
            # charset 藏在 ,{"charset":"gbk"} 选项里 —— GBK 站要靠它才不乱码
            self._reply(GBK_HTML.encode("gbk"), "text/html; charset=gbk")
        elif path == "/empty":
            self._reply(b"<html><body></body></html>", "text/html; charset=utf-8")
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802
        """只认 `/post-search`，body 里必须有 kw= 才给结果。"""
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        if self.path.split("?")[0] == "/post-search" and "kw=" in body:
            self._reply(SEARCH_HTML.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self.send_error(400)

    def log_message(self, *a):  # 静音，别把测试输出刷满
        pass


class _FixtureSite:
    """起一个临时站点，用完即关。"""

    def __init__(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        host, port = self.srv.server_address[:2]
        return f"http://{host}:{port}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=3)


def _fixture_source(base: str) -> bs.Source:
    return bs.Source(
        name="测试源",
        url=base,
        search_url=f"{base}/search?kw={{{{key}}}}",
        rule_search={
            "bookList": "class.listboxw@dl",
            "name": "tag.dd.0@tag.p.0@tag.a.0@text",
            "bookUrl": "tag.dd.0@tag.p.0@tag.a.0@href",
            "author": "class.author fl@text",
            "coverUrl": "class.pic fl@img@data-original",
        },
        rule_toc={
            "chapterList": "class.cate-list@a",
            "chapterName": "text",
            "chapterUrl": "href",
        },
        rule_content={"content": "id.j_content@html"},
    )


# ---------------------------------------------------------------- DOM / 选择器


class DomTest(unittest.TestCase):
    def setUp(self):
        self.root = bs.parse_html(SEARCH_HTML)
        self.items = bs.eval_list("class.listboxw@dl", [self.root])

    def test_list_rule_multi_segment(self):
        """`class.listboxw@dl` 必须逐段收窄 —— 只解析第一段会静默选不中。"""
        self.assertEqual(len(self.items), 2)

    def test_nested_tag_index(self):
        self.assertEqual(bs.eval_rule("tag.dd.0@tag.p.0@tag.a.0@text", self.items[:1]), "凡人修仙传")

    def test_multi_class_selector(self):
        """`class.author fl` = 同时含两个类，不是 class 属性等于 'author fl'。"""
        self.assertEqual(bs.eval_rule("class.author fl@text", self.items[:1]), "忘语")
        self.assertEqual(bs.eval_rule("class.author fl@text", self.items[1:2]), "萧鼎")

    def test_compound_attr_getter(self):
        self.assertEqual(bs.eval_rule("class.pic fl@img@data-original", self.items[:1]), "/cover/1.jpg")

    def test_href_is_absolute(self):
        got = bs.eval_rule("tag.dd.0@tag.p.0@tag.a.0@href", self.items[:1], "http://s/")
        self.assertEqual(got, "http://s/book/1")

    def test_css_descendant_chain(self):
        self.assertEqual(len(bs.eval_list(".listboxw dl", [self.root])), 2)

    def test_alternatives_pick_first_nonempty(self):
        self.assertEqual(bs.eval_rule("class.nope||tag.dd.0@text", self.items[:1]), "凡人修仙传")

    def test_script_is_not_text(self):
        root = bs.parse_html("<div id=a>正文<script>alert(1)</script></div>")
        self.assertEqual(bs.eval_rule("id.a@text", [root]), "正文")

    def test_html_getter(self):
        root = bs.parse_html('<div id=a><p>hi</p></div>')
        self.assertEqual(bs.eval_rule("id.a@html", [root]), "<p>hi</p>")

    def test_index_out_of_range_is_empty(self):
        self.assertEqual(bs.eval_rule("tag.dd.9@text", self.items[:1]), "")


# ---------------------------------------------------------------- URL 模板


class UrlTest(unittest.TestCase):
    def test_key_is_urlencoded(self):
        url, _ = bs.build_url("http://s/q?kw={{key}}", key="凡人")
        self.assertIn("%E5%87%A1%E4%BA%BA", url)

    def test_page_substitution(self):
        url, _ = bs.build_url("http://s/q?p={{page}}", page=3)
        self.assertTrue(url.endswith("p=3"))

    def test_options_json_is_split_off(self):
        url, opts = bs.build_url('/q?kw={{key}},{"charset":"gbk"}', key="x", base="http://s/")
        self.assertEqual(opts.get("charset"), "gbk")
        self.assertNotIn("{", url)

    def test_leading_angle_bracket_is_relative(self):
        url, _ = bs.build_url("<,?page={{page}}>", page=2, base="http://s/list")
        self.assertTrue(url.startswith("http://s/"))

    def test_js_url_is_rejected(self):
        with self.assertRaises(bs.UnsupportedRule):
            bs.build_url("http://s/q,<js>java.toString()</js>")


# ---------------------------------------------------------------- 书源加载


class SourceLoadTest(unittest.TestCase):
    def _write(self, data) -> Path:
        d = Path(tempfile.mkdtemp(prefix="_srctest"))
        p = d / "s.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def test_js_source_marked_unsupported(self):
        p = self._write(
            [
                {
                    "bookSourceName": "JS 源",
                    "bookSourceUrl": "http://a/",
                    "searchUrl": "http://a/s?k={{key}}",
                    "ruleSearch": {"bookList": "class.x", "name": "@js:result", "bookUrl": "href"},
                }
            ]
        )
        srcs, errs = bs.load_sources(p)
        self.assertEqual(errs, [])
        self.assertEqual(len(srcs), 1)
        self.assertTrue(srcs[0].unsupported)
        self.assertIn("JS", srcs[0].unsupported)

    def test_js_only_in_explore_still_usable(self):
        """只在「发现页」用 JS 不该拖垮搜索链 —— 那条路根本走不到。"""
        p = self._write(
            [
                {
                    "bookSourceName": "发现页用JS",
                    "bookSourceUrl": "http://a/",
                    "searchUrl": "http://a/s?k={{key}}",
                    "ruleExplore": {"bookList": "<js>x</js>"},
                    "ruleSearch": {"bookList": "class.x", "name": "text", "bookUrl": "href"},
                    "ruleToc": {"chapterList": "class.y", "chapterName": "text", "chapterUrl": "href"},
                    "ruleContent": {"content": "id.z@html"},
                }
            ]
        )
        srcs, _ = bs.load_sources(p)
        self.assertEqual(srcs[0].unsupported, "")
        self.assertTrue(srcs[0].can_search)

    def test_broken_entry_does_not_kill_the_file(self):
        p = self._write([{"bookSourceName": "好源", "searchUrl": "http://a/"}, "不是对象"])
        srcs, _ = bs.load_sources(p)
        self.assertEqual(len(srcs), 1)

    def test_missing_file_reports_clearly(self):
        srcs, errs = bs.load_sources(Path(tempfile.gettempdir()) / "_nope_.json")
        self.assertEqual(srcs, [])
        self.assertEqual(len(errs), 1)

    def test_real_bundled_sources_load(self):
        """随仓库带的那份书源必须能读，且有一定数量的可用源。"""
        if not bs.DEFAULT_SOURCES_PATH.exists():
            self.skipTest("未随仓库附带书源文件")
        srcs, errs = bs.load_sources()
        self.assertEqual(errs, [])
        self.assertGreater(len(srcs), 50)
        usable = [s for s in srcs if not s.unsupported and s.can_search and s.can_toc and s.can_content]
        self.assertGreater(len(usable), 10)


# ---------------------------------------------------------------- 采集闭环


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = _FixtureSite()

    @classmethod
    def tearDownClass(cls):
        cls.site.close()

    def setUp(self):
        self.src = _fixture_source(self.site.base)

    def test_search(self):
        books = bs.search_books(self.src, "凡人")
        self.assertEqual(len(books), 2)
        self.assertEqual(books[0]["name"], "凡人修仙传")
        self.assertEqual(books[0]["author"], "忘语")
        self.assertTrue(books[0]["bookUrl"].endswith("/book/1"))
        self.assertEqual(books[0]["cover"], self.site.base + "/cover/1.jpg")

    def test_toc(self):
        chs = bs.fetch_toc(self.src, self.site.base + "/book/1")
        self.assertEqual([c["title"] for c in chs], ["第一章 山村", "第二章 青牛"])
        self.assertTrue(chs[0]["url"].endswith("/c/1"))

    def test_chapter(self):
        got = bs.fetch_chapter(self.src, self.site.base + "/c/1")
        self.assertIn("清晨，山村还很安静", got["text"])

    def test_empty_result_is_not_an_error(self):
        """源站改版导致选不中时，返回空列表而不是抛异常。"""
        self.src.search_url = self.site.base + "/empty?kw={{key}}"
        self.assertEqual(bs.search_books(self.src, "凡人"), [])

    def test_unsupported_source_refuses(self):
        self.src.unsupported = "需要 JS 引擎"
        with self.assertRaises(bs.UnsupportedRule):
            bs.search_books(self.src, "x")

    def test_missing_rules_refuses(self):
        self.src.rule_search = {}
        with self.assertRaises(bs.UnsupportedRule):
            bs.search_books(self.src, "x")

    def test_dead_site_is_fetch_error(self):
        src = _fixture_source("http://127.0.0.1:1")  # 没人监听
        with self.assertRaises(bs.FetchError):
            bs.search_books(src, "x", cfg=None)


# ---------------------------------------------------------------- 路由门禁


class RouterTest(unittest.TestCase):
    """走 FastAPI TestClient。配置只改内存缓存，绝不落盘。"""

    @classmethod
    def setUpClass(cls):
        cls.site = _FixtureSite()

    @classmethod
    def tearDownClass(cls):
        cls.site.close()

    def setUp(self):
        from fastapi.testclient import TestClient

        import config as config_mod
        from main import app

        self.config_mod = config_mod
        self._saved_cache = config_mod._config_cache

        cfg = config_mod.get_config().model_copy(deep=True)
        from config import WorkspaceEntry

        self.root_dir = Path(tempfile.mkdtemp(prefix="_searchtest"))
        cfg.workspaces = [
            WorkspaceEntry(name="_searchtest", root=str(self.root_dir), created_at="2026-10-18T00:00:00")
        ]
        cfg.active = "_searchtest"
        cfg.search.enabled = False
        cfg.search.disclaimer_acked = False
        # 书源指向 fixture：一份只有「测试源」的 JSON
        fj = self.root_dir / "sources.json"
        fj.write_text(
            json.dumps(
                [
                    {
                        "bookSourceName": "测试源",
                        "bookSourceUrl": self.site.base,
                        "searchUrl": self.site.base + "/search?kw={{key}}",
                        "ruleSearch": {
                            "bookList": "class.listboxw@dl",
                            "name": "tag.dd.0@tag.p.0@tag.a.0@text",
                            "bookUrl": "tag.dd.0@tag.p.0@tag.a.0@href",
                            "author": "class.author fl@text",
                        },
                        "ruleToc": {
                            "chapterList": "class.cate-list@a",
                            "chapterName": "text",
                            "chapterUrl": "href",
                        },
                        "ruleContent": {"content": "id.j_content@html"},
                    },
                    {
                        "bookSourceName": "JS 源",
                        "bookSourceUrl": "http://x/",
                        "searchUrl": "http://x/s?k={{key}}",
                        "ruleSearch": {"bookList": "@js:1", "name": "text", "bookUrl": "href"},
                    },
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        cfg.search.sources_path = str(fj)
        config_mod._config_cache = cfg
        self.cfg = cfg
        self.client = TestClient(app)

    def tearDown(self):
        self.config_mod._config_cache = self._saved_cache

    def _enable(self):
        self.cfg.search.enabled = True
        self.cfg.search.disclaimer_acked = True

    # ---- 门禁 ----

    def test_status_is_always_200(self):
        r = self.client.get("/api/search/status")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertFalse(d["enabled"])
        self.assertEqual(d["sources_total"], 2)
        self.assertEqual(d["sources_usable"], 1)
        self.assertTrue(d["disclaimer"])

    def test_gated_endpoints_403_when_disabled(self):
        for method, url in (
            ("get", "/api/search/sources"),
            ("post", "/api/search"),
            ("post", "/api/search/toc"),
            ("post", "/api/search/chapter"),
            ("post", "/api/search/import"),
        ):
            # httpx 的 GET 不接受 json 参数
            r = self.client.get(url) if method == "get" else self.client.post(url, json={})
            self.assertEqual(r.status_code, 403, f"{url} 应该被门禁挡住")
            self.assertIn("未启用", r.json()["detail"])

    def test_disclaimer_alone_is_not_enough(self):
        """只勾免责、没开开关（或反之）都不算启用 —— 两个都要。"""
        self.cfg.search.disclaimer_acked = True
        self.assertEqual(self.client.get("/api/search/sources").status_code, 403)
        self.cfg.search.disclaimer_acked = False
        self.cfg.search.enabled = True
        self.assertEqual(self.client.get("/api/search/sources").status_code, 403)

    # ---- 启用后 ----

    def test_sources_lists_unsupported_with_reason(self):
        self._enable()
        d = self.client.get("/api/search/sources").json()
        by = {s["name"]: s for s in d["sources"]}
        self.assertTrue(by["测试源"]["can_search"])
        self.assertFalse(by["JS 源"]["can_search"])
        self.assertIn("JS", by["JS 源"]["unsupported"])

    def test_search_returns_books(self):
        self._enable()
        r = self.client.post("/api/search", json={"keyword": "凡人", "source": "测试源"})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(len(d["books"]), 2)
        self.assertEqual(d["books"][0]["author"], "忘语")

    def test_empty_keyword_is_400(self):
        self._enable()
        r = self.client.post("/api/search", json={"keyword": "  ", "source": "测试源"})
        self.assertEqual(r.status_code, 400)

    def test_unknown_source_is_404(self):
        self._enable()
        r = self.client.post("/api/search", json={"keyword": "x", "source": "不存在的源"})
        self.assertEqual(r.status_code, 404)

    def test_toc_and_chapter(self):
        self._enable()
        toc = self.client.post(
            "/api/search/toc", json={"source": "测试源", "book_url": self.site.base + "/book/1"}
        ).json()
        self.assertEqual(len(toc["chapters"]), 2)
        ch = self.client.post(
            "/api/search/chapter", json={"source": "测试源", "url": toc["chapters"][0]["url"]}
        ).json()
        self.assertIn("清晨，山村还很安静", ch["text"])

    def test_import_into_library(self):
        self._enable()
        r = self.client.post(
            "/api/search/import",
            json={"source": "测试源", "book_url": self.site.base + "/book/1", "title": "采集来的书"},
        )
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["requested"], 2)
        self.assertEqual(d["failed"], [])
        self.assertEqual(len(d["chapters"]), 2)
        # 盘上真的落了 md
        book_dir = self.root_dir / "原著" / "采集来的书"
        self.assertTrue(book_dir.is_dir())
        files = sorted(p.name for p in book_dir.glob("*.md"))
        self.assertEqual(len(files), 2)
        self.assertIn("山村", (book_dir / files[0]).read_text(encoding="utf-8"))
        # 来源标注（合规第 3 条）
        self.assertIn(self.site.base, d.get("source", ""))

    def test_import_range(self):
        self._enable()
        r = self.client.post(
            "/api/search/import",
            json={
                "source": "测试源",
                "book_url": self.site.base + "/book/1",
                "title": "只要一章",
                "chapter_from": 2,
                "chapter_to": 2,
            },
        ).json()
        self.assertEqual(r["requested"], 1)
        self.assertEqual(len(r["chapters"]), 1)


# ---------------------------------------------------------------- 规则语义


class RegexTest(unittest.TestCase):
    """`##正则##替换###` —— 27 个可用源里 24 个在用，是兼容度的命脉。"""

    def setUp(self):
        self.root = bs.parse_html(
            '<div class="box"><span>作者：忘语</span><em id="n">第1章</em></div>'
        )

    def test_extract_with_group(self):
        """三段式 + $1：返回**展开后的分组**，不是整串原文。"""
        self.assertEqual(bs.first(self.root, "class.box@span@text##作者：(.*)##$1###"), "忘语")

    def test_replace_without_group(self):
        """替换串里没有 $n 时，是对整串做替换。"""
        self.assertEqual(bs.first(self.root, "class.box@span@text##作者：###"), "忘语")

    def test_two_segment_removes_match(self):
        """两段式 `##X`（无 `###` 收尾）= 把 X 匹配到的内容剔掉。"""
        self.assertEqual(bs.first(self.root, "class.box@span@text##作者："), "忘语")

    def test_empty_selector_hits_whole_source(self):
        """空选择器 + 正则：对整篇**源码**做匹配，标签文本要参与匹配。"""
        self.assertEqual(bs.first(self.root, '##<em id="n">([^<]+)</em>##$1###'), "第1章")

    def test_extract_on_attribute(self):
        root = bs.parse_html('<a href="/book/42.html">看</a>')
        self.assertEqual(bs.first(root, r"a@href##/book/(\d+)\.html##$1###"), "42")

    def test_bad_pattern_does_not_raise(self):
        """书源里偶尔有写坏的正则：编译失败就退回字面量匹配，不能 500。"""
        self.assertEqual(bs.first(self.root, "class.box@span@text##([##$1###"), "")

    def test_pattern_not_matching_returns_empty(self):
        """带 $1 的提取式没匹配上时返回空 —— 比返回整段原文更容易察觉源头改版。"""
        self.assertEqual(bs.first(self.root, "class.box@span@text##定价：([^，]+)##$1###"), "")


class JsonPathTest(unittest.TestCase):
    """JSON 文档 + JSONPath。这批书源里一半是 API 站，返回体不是 HTML。"""

    doc = {
        "code": 0,
        "data": {"total": 2, "list": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]},
    }

    def test_dot_path(self):
        self.assertEqual(bs.first(self.doc, "$.data.total"), "2")

    def test_bare_key_is_jsonpath_on_dict(self):
        """dict 文档里裸键名（书源常写 `data.list[*]`）自动按 JSONPath 处理。"""
        self.assertEqual(bs.first(self.doc, "code"), "0")

    def test_array_expands_for_iteration(self):
        items = bs.eval_values(self.doc, "$.data.list", want_all=True)
        self.assertEqual(len(items), 2)
        self.assertEqual(bs.first(items[1], "$.name"), "b")

    def test_wildcard(self):
        self.assertEqual(bs.eval_values(self.doc, "$.data.list[*].name", want_all=True), ["a", "b"])

    def test_index_and_negative_index(self):
        self.assertEqual(bs.first(self.doc, "$.data.list[1].name"), "b")
        self.assertEqual(bs.first(self.doc, "$.data.list[-1].name"), "b")

    def test_recursive_descent(self):
        self.assertEqual(bs.eval_values(self.doc, "$..name", want_all=True), ["a", "b"])

    def test_missing_path_is_empty(self):
        self.assertEqual(bs.eval_values(self.doc, "$.data.nope", want_all=True), [])


class SelectorExtraTest(unittest.TestCase):
    """Legado 语法的边角：索引、!n、&& 备选、CSS 属性选择器。"""

    def setUp(self):
        self.root = bs.parse_html(
            """<div class="list">
            <a href="/b/1.html">一</a><a href="/b/2.html">二</a><a href="/b/3.html">三</a>
            <p>首段</p><p data-x="v">中段</p><p>末段</p>
            </div>"""
        )

    def test_index_negative(self):
        self.assertEqual(bs.first(self.root, "class.list a.-1@text"), "三")

    def test_index_multiple(self):
        vals = bs.eval_values(self.root, "class.list a.1:0", want_all=True)
        self.assertEqual([bs._as_text(v) for v in vals], ["二", "一"])

    def test_bang_last_takes_the_tail(self):
        self.assertEqual(bs.first(self.root, "class.list p!-1@text"), "末段")

    def test_bang_positive_removes_that_index(self):
        """🔴 Legado 的 `!n`（n>=0）是**移除**下标 n，不是取它。"""
        vals = bs.eval_values(self.root, "class.list p!0@text", want_all=True)
        self.assertEqual(vals, ["中段", "末段"])

    def test_amp_is_also_an_alternative(self):
        self.assertIn("一二三", bs.first(self.root, "class.nope&&class.list@text"))

    def test_attr_selector_contains(self):
        self.assertEqual(bs.first(self.root, 'class.list [data-x]@data-x'), "v")

    def test_attr_selector_suffix(self):
        self.assertEqual(bs.first(self.root, 'class.list a[href$="/b/2.html"]@text'), "二")

    def test_comma_is_union(self):
        self.assertEqual(len(bs.eval_values(self.root, "class.list p, div", want_all=True)), 4)


class TemplateTest(unittest.TestCase):
    """`{{...}}` 模板填充 —— 全部 27 个源都在用。"""

    def test_key_is_encoded_and_page_substituted(self):
        got = bs.fill("http://x/s?kw={{key}}&p={{page}}", key="凡人 修", page=2)
        self.assertIn("%E5%87%A1%E4%BA%BA", got)
        self.assertTrue(got.endswith("p=2"))

    def test_arithmetic_page_expression(self):
        """`{{page-1}}` 这种写法本书源包里有 279 处，不当 JS 就不能跑。"""
        self.assertEqual(bs.fill("http://x/{{page-1}}", page=3), "http://x/2")
        self.assertEqual(bs.fill("http://x/{{(page-1)*10}}", page=2), "http://x/10")

    def test_base_url_is_current_page(self):
        self.assertEqual(bs.fill("{{baseUrl}}/toc", base_url="http://x/d"), "http://x/d/toc")

    def test_field_from_json_context(self):
        ctx = {"id": 7, "name": "斗破"}
        self.assertEqual(bs.fill("http://x/d?id={{id}}", ctx=ctx), "http://x/d?id=7")

    def test_put_and_get_vars(self):
        """@put 写、@get 读 —— 目录接口地址常常靠搜索阶段存下来的 bookId。"""
        src = bs.Source(name="", url="", search_url="")
        eng = bs.Engine(src)
        clean, writes = bs.apply_puts("@put:{bid:$.id}", {"id": 7}, bs.eval_values)
        self.assertIsNone(clean)
        self.assertEqual(writes, {"bid": "7"})
        eng.vars.update(writes)
        self.assertEqual(bs.apply_gets("/x?id=@get:{bid}", eng.vars), "/x?id=7")

    def test_unknown_template_is_left_alone(self):
        """需要 JS 求值的写法保持原样（这类源本来就被判 unsupported）。"""
        self.assertEqual(bs.fill("http://x/{{a+b()}}"), "http://x/a+b()")


# ---------------------------------------------------------------- 整管道（API 型站点）


class JsonSourceTest(unittest.TestCase):
    """对一个返回 JSON 的 fixture API 站跑完「搜索 → 目录」。"""

    @classmethod
    def setUpClass(cls):
        cls.site = _FixtureSite()

    @classmethod
    def tearDownClass(cls):
        cls.site.close()

    def setUp(self):
        base = self.site.base
        self.src = bs.Source(
            name="JSON 源",
            url=base,
            search_url=f"{base}/api/search?kw={{{{key}}}}",
            rule_search={
                "bookList": "$.data.books",
                "name": "$.name",
                "author": "$.author",
                "coverUrl": "$.cover",
                # @put 存变量，bookUrl 再用 @get 取回（Legado 书源的标准套路）
                "kind": "@put:{bid:$.id}",
                "bookUrl": "/detail?id=@get:{bid}",
            },
            rule_book_info={"tocUrl": "class.to-toc@href"},
            rule_toc={
                "chapterList": "$.chapters",
                "chapterName": "$.title",
                "chapterUrl": "/c/{{cid}}",
            },
            rule_content={"content": "id.j_content@html"},
        )

    def test_search_over_json_has_absolute_urls(self):
        books = bs.search_books(self.src, "斗破")
        self.assertEqual(len(books), 2)
        self.assertEqual(books[0]["name"], "斗破苍穹")
        self.assertEqual(books[0]["bookUrl"], self.site.base + "/detail?id=11")
        self.assertEqual(books[1]["cover"], self.site.base + "/img/12.jpg")

    def test_toc_comes_from_book_info_toc_url(self):
        """详情页只是个壳 —— 真正的目录接口在 ruleBookInfo.tocUrl。"""
        chs = bs.fetch_toc(self.src, self.site.base + "/detail?id=11")
        self.assertEqual([c["title"] for c in chs], ["第一章", "第二章"])
        self.assertTrue(chs[0]["url"].endswith("/c/101"))


class RequestOptionTest(unittest.TestCase):
    """,{"method":"POST"} / ,{"charset":"gbk"} 这类请求选项。"""

    @classmethod
    def setUpClass(cls):
        cls.site = _FixtureSite()

    @classmethod
    def tearDownClass(cls):
        cls.site.close()

    def test_post_body_is_sent(self):
        src = _fixture_source(self.site.base)
        src.search_url = self.site.base + '/post-search,{"method":"POST","body":"kw={{key}}"}'
        self.assertEqual(len(bs.search_books(src, "凡人")), 2)

    def test_charset_option_decodes_gbk(self):
        src = _fixture_source(self.site.base)
        src.search_url = self.site.base + '/gbk?kw={{key}},{"charset":"gbk"}'
        books = bs.search_books(src, "凡人")
        self.assertEqual(books[0]["name"], "凡人修仙传")

    def test_comma_in_query_is_not_mistaken_for_options(self):
        """URL 参数里本身带逗号：必须从**最后一个** `,{` 处切，不然查询串被切断。"""
        src = _fixture_source(self.site.base)
        src.search_url = self.site.base + "/search?a=1,2&kw={{key}}"
        self.assertEqual(len(bs.search_books(src, "凡人")), 2)

    def test_xpath_rule_is_reported_not_silently_empty(self):
        src = _fixture_source(self.site.base)
        src.rule_search["bookList"] = "@XPath://div"
        with self.assertRaises(bs.UnsupportedRule):
            bs.search_books(src, "凡人")


if __name__ == "__main__":
    unittest.main()
