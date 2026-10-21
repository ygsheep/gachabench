# -*- coding: utf-8 -*-
"""书源引擎 —— Legado 书源规则的一个零依赖实现，**语义对齐 novel-booksource-engine**。

为什么不直接复制那套实现（docs/INTEGRATION-novel-booksource-engine.md §7 M7 / §3 C3）：
它依赖 lxml / cssselect / jsonpath-ng / quickjs / fonttools / cryptography 六件套，
整包移植会让 dist 涨 15~45MB，破坏本项目「零重型依赖」的底线。所以这里做的事是
**把它的规则语义照搬过来、把底层实现换成标准库**：

  lxml.html      → html.parser 建小 DOM（本文件 Node）
  cssselect      → 自己写的 CSS-lite（见 Simple / _compile_selector）
  jsonpath_ng    → 迷你 JSONPath（_jsonpath_all，覆盖 $ / . / .. / [n] / [*] / ['k']）
  httpx + cookie → urllib + http.cookiejar（Fetcher）
  quickjs        → ❌ 不做。需要 JS 的源一律标 unsupported 并说明原因

规则层面的每一条语义，都是从那套实现里逐条抄过来的（包括它的踩坑注释）：
正则三段式 `选择器##pattern##replacement###`、`$1` 展开 vs 整串替换、`||` 与 `&&`
都是取首个非空、`!n`（负数取末位 / 正数是**移除**下标 n）、`tag.N`（0-based 负数倒序、
冒号并列 `span.2:1:4`）、裸属性名（`href`、`content`…）、裸取值动词（`text`、`html`…）、
`@` 组合链（末段是动词则取值、是属性名则取属性、否则继续往下选）。

刻意不做的三件事（本 Sierra §7 M7 定的边界）：
  1. 字体解密 —— 砍掉它才不用 fonttools/cryptography。
  2. JS 引擎 —— 需要 @js:/<js> 的源标 unsupported，**不假装能用**。
  3. XPath —— 统计内置的 68 个源，可用的那 27 个里 **0 个**用 @XPath:，收益为零。
     （遇到了也只会导致那条规则 unsupported，不会静默出错。）
"""

from __future__ import annotations

import gzip
import http.cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from runtime import BACKEND_DIR
from textclean import clean_text

# ---------------------------------------------------------------- 常量

DEFAULT_SOURCES_PATH = BACKEND_DIR / "data" / "shareBookSource.json"
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

#: Legado 的取值动词。写在选择器末尾表示「怎么取值」。
_VERB_SET = frozenset({"text", "html", "textnodes", "textnode", "owntext", "all"})

#: 裸写的属性名 —— Legado 里 `chapterUrl: "href"`、`chapterName: "text"` 这种
#: 「整条规则就是一个取值动作」的写法极常见。当 CSS 标签选择器查会匹配不到任何东西，
#: 于是整章被静默丢弃。
_BARE_ATTR = frozenset(
    {"href", "src", "alt", "content", "value", "id", "class", "name", "style", "type", "title"}
)

#: 需要真 JS 引擎的规则标记。
_JS_MARKERS = ("<js>", "</js>", "@js:")

VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())

_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.I)

# Legado `!n` 后缀：`!-1` 取末位、`!0` 移除第 0 个。下标也可能写在取值动词之前
# （`p!-1@text`），所以动词部分要一并允许。
_BANG_SUFFIX = re.compile(r"!(-?\d+)(?:@(?:text|html|textNodes|textnodes|ownText|owntext|all))?$", re.I)
# 规则标志：`@@`（默认规则，可省）、`@css:`、`@XPath:`、`@Json:`
_RULE_MARKER = re.compile(r"^(@@|@css:|@CSS:|@xpath:|@XPath:|@json:|@Json:)\s*", re.S)
# Legado 元素索引后缀：`a.1` 取第 2 个 <a>；`:` 并列多个下标（`span.2:1:4`）
_INDEX_SUFFIX = re.compile(r"((?:[.:]-?\d+)+)$")
# 多路回退分隔符：Legado 里 `&&` 与 `||` 都是「依次尝试，取首个非空」
_ALT_RE = re.compile(r"\|\||&&")
# 纯标签名（`img` / `dl` / `a`…）：在 @ 链里出现时是「继续往里选」而不是属性名
_TAG_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")
# 属性名里不会出现的选择器元字符：用于区分 `@href`（取属性）与 `@p`（后代标签）
_CSS_META = re.compile(r"[.#>\[\]():,*\s]")
# URL 模板占位符
_TEMPLATE_RE = re.compile(r"\{\{(.*?)\}\}")
# 只允许 page + 数字的四则运算（{{page-1}} 这种写法本书源包里有 279 处）
_ARITH_OK = re.compile(r"^[\d\s+\-*/()page]*$")
# @put / @get / @cookie 规则变量
_GET_RE = re.compile(r"@get:\{([^}]*)\}")
_PUT_RE = re.compile(r"@put:\{([^}]*)\}")
_COOKIE_RE = re.compile(r"@cookie:\{([^}]*)\}")

#: 兼容旧导出的名字（早期实现里「取值方式」的枚举）。
GETTERS = _VERB_SET | _BARE_ATTR


class UnsupportedRule(Exception):
    """规则跑不了（需要 JS / XPath / 语法超出子集）。带人话原因，UI 直接显示。"""


class FetchError(Exception):
    """网络层失败。带人话原因。"""


# ---------------------------------------------------------------- DOM


class Node:
    """极简 DOM 节点。children 里混着 Node 与 str（文本片段）。"""

    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str] | None = None, parent: "Node | None" = None):
        self.tag = (tag or "").lower()
        # 🔴 属性名一律小写：HTML 属性大小写不敏感，而书源规则几乎都写小写
        self.attrs: dict[str, str] = {k.lower(): (v or "") for k, v in (attrs or {}).items()}
        self.children: list = []
        self.parent = parent

    def text(self) -> str:
        out: list[str] = []
        _collect_text(self, out)
        return "".join(out)

    def own_text(self) -> str:
        """直接子文本（不含后代元素里的字）。对应 lxml 的 element.text。"""
        return "".join(c for c in self.children if isinstance(c, str))

    def html(self) -> str:
        """内部 HTML（不含自身标签），对应 Legado 的 @html。"""
        out: list[str] = []
        for c in self.children:
            out.append(c if isinstance(c, str) else _serialize(c))
        return "".join(out)

    def outer_html(self) -> str:
        return _serialize(self)

    def attr(self, name: str) -> str:
        if name == "text":
            return self.text()
        if name == "html":
            return self.html()
        return self.attrs.get(name, "")


def _collect_text(node: Node, out: list[str]) -> None:
    for c in node.children:
        if isinstance(c, str):
            out.append(c)
        elif c.tag not in ("script", "style"):
            _collect_text(c, out)


def _serialize(node: Node) -> str:
    if isinstance(node, str):
        return node
    # 属性值按源码原样输出：Legado 的正则作用在**源码**上，转义会把特征串改掉
    attrs = "".join(f' {k}="{str(v).replace(chr(34), "&quot;")}"' for k, v in node.attrs.items())
    if node.tag in VOID_TAGS:
        return f"<{node.tag}{attrs}>"
    inner = "".join(_serialize(c) for c in node.children)
    return f"<{node.tag}{attrs}>{inner}</{node.tag}>"


class _TreeBuilder(HTMLParser):
    """html.parser → DOM。容错靠 HTMLParser 自带的宽松解析。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("[document]")
        self._stack: list[Node] = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self._stack[-1])
        self._stack[-1].children.append(node)
        if tag.lower() not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._stack[-1].children.append(Node(tag, dict(attrs), self._stack[-1]))

    def handle_endtag(self, tag):
        # HTML 里常有不成对的结束标签：只在栈里真找得到同名开标签时才退栈
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag.lower():
                del self._stack[i:]
                return

    def handle_data(self, data):
        if data:
            self._stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    p = _TreeBuilder()
    try:
        p.feed(html or "")
        p.close()
    except Exception:
        # HTMLParser 极少抛错；真抛了就给一棵空树，上层看到空结果而不是 500
        pass
    return p.root


def _walk(node: Node):
    """文档序遍历所有后代元素节点。"""
    stack = list(reversed(node.children))
    while stack:
        c = stack.pop()
        if isinstance(c, Node):
            yield c
            stack.extend(reversed(c.children))


def _children(node: Node):
    for c in node.children:
        if isinstance(c, Node):
            yield c


# ---------------------------------------------------------------- CSS-lite


@dataclass
class Simple:
    """一个复合选择器：`tag#id.c1.c2[attr=v]`。`comb` 是与前一个的关系。

    🔴 Legado 的 `class.author fl` 表示「同时含 author 和 fl 两个类」，不是
    「class 属性等于 'author fl'」—— 所以 clase 存成列表逐词比对。
    """

    tag: str = ""
    cls: list[str] = field(default_factory=list)
    id: str = ""
    attrs: list[tuple[str, str, str]] = field(default_factory=list)  # (name, op, value)
    comb: str = " "  # " "=后代，">"=直接子

    def matches(self, node: Node) -> bool:
        if not isinstance(node, Node):
            return False
        if self.tag and node.tag != self.tag:
            return False
        if self.id and node.attrs.get("id") != self.id:
            return False
        if self.cls:
            have = (node.attrs.get("class") or "").split()
            if not all(w in have for w in self.cls):
                return False
        for name, op, val in self.attrs:
            got = node.attrs.get(name)
            if op == "":
                if got is None:
                    return False
            elif got is None:
                return False
            elif op == "=":
                if got != val:
                    return False
            elif op == "*=":
                if val not in got:
                    return False
            elif op == "^=":
                if not got.startswith(val):
                    return False
            elif op == "$=":
                if not got.endswith(val):
                    return False
            elif op == "~=":
                if val not in got.split():
                    return False
        return True


_ATTR_RE = re.compile(r"\[([A-Za-z_\-][\w\-:.]*)\s*(?:([\^$*~]?=)\s*['\"]?(.*?)['\"]?)?\]")


def _parse_compound(tok: str) -> Simple | None:
    """解析一个复合选择器 token：`div#id.a.b[x=y][z*=w]` / `*` / `.cls`。"""
    tok = tok.strip()
    if not tok or tok == "*":
        return Simple()
    s = Simple()
    buf: list[str] = []
    i = 0
    while i < len(tok):
        if tok[i] == "[":
            j = tok.find("]", i)
            if j < 0:
                buf.append(tok[i:])
                break
            m = _ATTR_RE.match(tok[i : j + 1])
            if m:
                s.attrs.append((m.group(1).lower(), m.group(2) or "", (m.group(3) or "").strip()))
            i = j + 1
        else:
            buf.append(tok[i])
            i += 1
    body = "".join(buf)
    if body.startswith("*"):
        body = body[1:]
    m = re.match(r"^([A-Za-z][A-Za-z0-9\-]*)", body)
    if m:
        s.tag = m.group(1).lower()
        body = body[m.end() :]
    for part in re.findall(r"[.#][^.#]+", body):
        if part[0] == ".":
            s.cls.append(part[1:])
        else:
            s.id = part[1:]
    return s


def _split_comb(css: str) -> list[Simple]:
    """按后代/子代组合器切成一个复合选择器链。"""
    parts: list[str] = []
    combs: list[str] = []
    buf = ""
    i = 0
    while i < len(css):
        ch = css[i]
        if ch in ">+~":
            if buf.strip():
                parts.append(buf.strip())
                buf = ""
                combs.append(ch)
            i += 1
        elif ch.isspace():
            if buf.strip():
                parts.append(buf.strip())
                buf = ""
                combs.append(" ")
            i += 1
        else:
            buf += ch
            i += 1
    if buf.strip():
        parts.append(buf.strip())
    chain: list[Simple] = []
    for i, tok in enumerate(parts):
        comp = _parse_compound(tok)
        if comp is None:
            return []
        if i:
            comp.comb = combs[i - 1] if i - 1 < len(combs) else " "
        chain.append(comp)
    return chain


def _pop_index(s: str) -> tuple[str, list[int]]:
    """剥离 Legado 索引后缀，返回 (clean, indexes)。

    XPath（`//div[1]` 有原生下标）和含路径/协议的 URL 不处理，否则会把
    `http://x/1` 这类 URL 的结尾切掉。
    """
    if not s or "/" in s or "://" in s:
        return s, []
    m = _INDEX_SUFFIX.search(s)
    if not m:
        return s, []
    clean = s[: m.start()].strip()
    if not clean:
        return s, []
    # CSS 类名也可能以数字结尾（`class.1` 是合法的索引写法），照 Legado 语义裁
    return clean, [int(x) for x in re.findall(r"[.:](-?\d+)", m.group(1))]


def _apply_index(vals: list, indexes: list[int]) -> list:
    """按下标挑元素：负数从末尾算，重复下标去重，越界忽略。"""
    if not indexes:
        return vals
    n = len(vals)
    out: list = []
    seen: set[int] = set()
    for i in indexes:
        j = i + n if i < 0 else i
        if 0 <= j < n and j not in seen:
            seen.add(j)
            out.append(vals[j])
    return out


def _select_chain(scope: list, chain: list[Simple]) -> list:
    """在给定作用域里按组合器链选节点。chain 为空 = 保持原作用域。"""
    cur = [n for n in scope if isinstance(n, Node)]
    for comp in chain:
        nxt: list = []
        seen: set[int] = set()
        for root in cur:
            pool = _children(root) if comp.comb == ">" else _walk(root)
            for d in pool:
                if comp.matches(d) and id(d) not in seen:
                    seen.add(id(d))
                    nxt.append(d)
        cur = nxt
        if not cur:
            return []
    return cur


def _compile_one(tok: str) -> list[list[Simple]]:
    """编译一个（不含逗号的）选择器段。

    🔴 `class.X Y` 是 Legado 的二义写法，两种解释都得给：
      - 「class 属性同时含 X 和 Y」（`class.author fl` 是这种）
      - 「CSS 后代 `.X Y`」（`class.list p` 是这种）
    两种几乎不可能同时命中，这里按**并集**返回 —— 谁选得到算谁的。
    """
    tok = tok.strip()
    if not tok:
        return []
    for prefix in ("class.", "id.", "tag."):
        if not tok.startswith(prefix):
            continue
        words = tok[len(prefix) :].strip().split()
        if not words:
            return []
        if prefix == "id.":
            return [[Simple(id=words[0])]]
        if len(words) == 1:
            one = Simple(cls=words) if prefix == "class." else Simple(tag=words[0].lower())
            return [[one]]
        # <｜hy_place▁holder▁no▁813｜>了两个逗号不可用，多词时 CSS 链要绕过
        css = "." + words[0] if prefix == "class." else words[0].lower()
        chain = _split_comb(css + " " + " ".join(words[1:]))
        one = Simple(cls=words) if prefix == "class." else Simple(tag=words[0].lower())
        return [[one]] + ([chain] if chain else [])
    return [c for c in [_split_comb(tok)] if c]


def _compile_selector(seg: str) -> tuple[list[list[Simple]], list[int]]:
    """把一个 @ 段编译成 (若干并列的后代链, 索引)。

    三种写法都要认：
      - `class.pic fl`            → 单个 compound（类名可含空格）
      - `id.j_content` / `tag.dd` → 单个 compound
      - `.cate-list a` / `a[href]`→ CSS-lite 链，逗号可并列多条
    """
    seg = (seg or "").strip()
    if not seg:
        return [], []
    seg, indexes = _pop_index(seg)
    if "://" in seg or seg.startswith("//"):
        return [], []
    # 逗号并列：必须**先切逗号**再做前缀判断，否则 `class.list p, div`
    # 会把逗号后的部分当成类名的一部分
    chains: list[list[Simple]] = []
    for part in seg.split(","):
        if part.strip():
            chains.extend(_compile_one(part))
    return chains, indexes


def _select_nodes(scope: list, seg: str) -> list:
    """对一个 @ 段做选择 —— 逗号并列的链取并集，最后套索引。"""
    chains, indexes = _compile_selector(seg)
    if not chains:
        return []
    out: list = []
    seen: set[int] = set()
    for chain in chains:
        for n in _select_chain(scope, chain):
            if id(n) not in seen:
                seen.add(id(n))
                out.append(n)
    return _apply_index(out, indexes)


# ---------------------------------------------------------------- 迷你 JSONPath


def _deep_nodes(objs: list) -> list:
    """按文档序收集所有 dict / list 子节点，供 `$..key` 用（顺序必须是正的）。"""
    out: list = []
    stack = list(reversed(objs))
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            out.append(cur)
            stack.extend(reversed(list(cur.values())))
        elif isinstance(cur, list):
            out.append(cur)
            stack.extend(reversed(cur))
    return out


_JSON_STEP = re.compile(r"(?:\.\.|\.)([A-Za-z_][\w\-]*|\*)|\[([^\]]*)\]")


def _bracket(node: Any, inner: str) -> list:
    inner = (inner or "").strip()
    if isinstance(node, dict):
        if inner == "*":
            return list(node.values())
        m = re.fullmatch(r"['\"](.*)['\"]", inner)
        if m:
            v = node.get(m.group(1))
            return [v] if v is not None else []
        return [node[inner]] if inner in node else []
    if isinstance(node, list):
        if inner == "*":
            return list(node)
        if ":" in inner:
            try:
                a, _, b = inner.partition(":")
                lo = int(a) if a.strip() else None
                hi = int(b) if b.strip() else None
                return list(node[lo:hi])
            except (ValueError, TypeError):
                return []
        try:
            i = int(inner)
        except ValueError:
            return []
        return [node[i]] if -len(node) <= i < len(node) else []
    return []


def _jsonpath_all(doc: Any, path: str) -> list:
    """极简 JSONPath：`$` `.key` `..key` `[n]` `[-1]` `[*]` `['k']` `[a:b]`。

    覆盖了书源里 99% 的用法；`[?(...)]` 过滤器不支持（遇到就返回空，不报错）。
    """
    path = (path or "").strip()
    if not path:
        return []
    if isinstance(doc, (dict, list)) and not path.startswith(("$", "@")):
        path = "$." + path
    if not path.startswith("$"):
        return []
    steps = list(_JSON_STEP.finditer(path))
    if not steps:
        return [doc] if doc is not None else []
    cur: list = [doc]
    for st in steps:
        nxt: list = []
        if st.group(1) is not None:
            key = st.group(1)
            recursive = st.group(0).startswith("..")
            pool = _deep_nodes(cur) if recursive else cur
            for node in pool:
                if isinstance(node, dict):
                    if key == "*":
                        nxt.extend(node.values())
                    elif key in node:
                        nxt.append(node[key])
                elif isinstance(node, list) and not recursive:
                    for x in node:
                        if isinstance(x, dict):
                            if key == "*":
                                nxt.extend(x.values())
                            elif key in x:
                                nxt.append(x[key])
        else:
            for node in cur:
                nxt.extend(_bracket(node, st.group(2)))
        cur = nxt
        if not cur:
            return []
    return cur


# ---------------------------------------------------------------- 规则求值


def _needs_js(rule: str) -> bool:
    r = (rule or "").lower()
    return any(m in r for m in _JS_MARKERS)


def _require_no_js(rule: str, what: str = "规则") -> None:
    if _needs_js(rule):
        raise UnsupportedRule(f"{what}需要 JS 引擎，当前不支持")


def _norm(s: str) -> str:
    return (s or "").replace("\u0010", "").replace("\u000b", "").strip()


def _as_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, Node):
        return v.text()
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dict, list)):
        try:
            return json.dumps(v, ensure_ascii=False)
        except Exception:
            return str(v)
    return str(v)


def _split_marker(sel: str) -> tuple[str, str]:
    """剥离 Legado 规则标志 → (kind, cleaned)。kind ∈ {'', 'css','xpath','json'}。"""
    m = _RULE_MARKER.match(sel or "")
    if not m:
        return "", sel or ""
    return (m.group(1).lower().strip("@: ") or ""), sel[m.end() :]


def _split_regex_sandwich(rule: str) -> tuple[str, str, str]:
    """把 `sel##pat##repl###` 拆成 (selector, pattern, replacement)。

    两段式 `sel##X`（没收尾 `###`）在书源里表示「把 X 匹配到的内容剔掉」
    （如晋江 `$.novelIntroShort##。$`），所以 X 是 pattern、替换为空 —— 当成
    replacement 会把 `。$` 当成字面量插进去。
    """
    if "##" not in rule:
        return rule, "", ""
    head, rest = rule.split("##", 1)
    if rest.endswith("###"):
        rest = rest[:-3]
    if "##" in rest:
        pat, repl = rest.split("##", 1)
    else:
        pat, repl = rest, ""
    return head, pat, repl


def _legado_repl_to_python(repl: str) -> str:
    """`$1` / `$2` 反向引用 → Python re 的 `\\g<1>` / `\\g<2>`。"""
    return re.sub(r"\$(\d+)", r"\\g<\1>", repl)


def _compile_safe(pat: str):
    try:
        return re.compile(pat, re.S)
    except re.error:
        return re.compile(re.escape(pat), re.S)


def _apply_regex(text: str, pat_re, repl_py: str, repl_raw: str) -> str:
    """应用 `##正则##替换###`，区分两种 Legado 语义。

    - 替换串里有 `$1` 这类分组引用时，Legado 返回**展开后的匹配**（从整页 HTML
      里抠出作者/章节名，如 `##作者：</aria>([^<]+)<aria##$1###`）。
    - 否则是对整串做替换（如 `$.name##^#+###` 去掉开头的 #）。
      一律用 sub 会把整页 HTML 当成结果返回。
    """
    if repl_raw and re.search(r"\$\d", repl_raw):
        m = pat_re.search(text)
        if m:
            try:
                return m.expand(repl_py)
            except Exception:
                return text
        return ""
    return pat_re.sub(repl_py, text)


def _whole_text(doc: Any) -> str:
    """取整个作用域的**源码**，供「空选择器 + 正则」规则使用。

    🔴 必须是源码不是纯文本：起点源的 `##span>连载至([^<]+)##$1###` 依赖 `span>`
    这种标签文本参与匹配，用 text_content 会把标签吃掉、匹配不上。
    """
    if doc is None:
        return ""
    if isinstance(doc, Node):
        return doc.outer_html()
    if isinstance(doc, str):
        return doc
    if isinstance(doc, bytes):
        return doc.decode("utf-8", "replace")
    if isinstance(doc, (dict, list)):
        try:
            return json.dumps(doc, ensure_ascii=False)
        except Exception:
            return str(doc)
    return str(doc)


def _apply_bang(vals: list, bang: int) -> list:
    """Legado `!n`：n<0 取末位（`!-1`），否则**移除**下标 n 的元素。"""
    if not vals:
        return vals
    if bang < 0:
        return vals[-1:]
    return [v for i, v in enumerate(vals) if i != bang]


def _split_at_chain(sel: str) -> tuple[list[str], str | None, str | None]:
    """拆 Legado 的 `@` 组合规则 → (选择器段列表, 取值动词, 属性名)。

    `class.read-content@class.content-wrap@text` = 在前两段圈定的范围内取文本；
    `class.pic@img@data-original` = 取 img 的 data-original 属性。
    🔴 各段必须**逐段收窄**，不能拼成一个空格分隔的后代字符串 —— `class.pic fl`
    里的空格是类名的一部分（`class="pic fl"`），拼开就变成两个类了。
    """
    if "@" not in sel:
        return [sel], None, None
    parts = sel.split("@")
    tail = parts[-1].strip()
    head = [p.strip() for p in parts[:-1] if p.strip()]
    low = tail.lower()
    if low in _VERB_SET:
        return head, low, None
    # 🔴 属性名 vs 继续往里选的标签名，判据是「这段像不像一个标签」：
    # `@href` / `@content` 这类是 Legado 约定的取值属性（书源里 `@title`、`@alt`
    # 都指属性）；而 `@dl` / `@img` / `@a` 必须当成下一层选择器 —— 否则
    # `class.listboxw@dl` 会被解释成「取 dl 属性」，静默选空。
    # 含 `-` 的名字（data-original 这类自定义属性）不可能是标签，一律当属性。
    if low in _BARE_ATTR:
        return head, None, tail
    if tail and _TAG_WORD.fullmatch(tail):
        return [p.strip() for p in parts if p.strip()], None, None
    if tail and not _CSS_META.search(tail):
        return head, None, tail
    return [p.strip() for p in parts if p.strip()], None, None


def _get_value(node: Any, verb: str | None, attr: str | None, base_url: str = "") -> str:
    """从节点上按「动词 / 属性名」取值；都没给就返回节点本身（供下一层选）。"""
    if isinstance(node, str):
        return node
    low = (verb or "").lower()
    if low == "text":
        return _norm(node.text())
    if low == "html":
        # jsoup 语义：@html 是**内部** HTML（不含自身标签）。Legado 用 jsoup 实现，
        # 写成 outer 会把正文外层的壳也一并带走。
        return node.html()
    if low in ("owntext", "textnodes", "textnode", "all"):
        return _norm(node.own_text())
    name = attr or ""
    if isinstance(node, dict):
        return node.get(name, "")
    if not name:
        return node
    v = node.attrs.get(name, "") if isinstance(node, Node) else ""
    if v and name in ("href", "src") and base_url:
        return urllib.parse.urljoin(base_url, v)
    return v


def _select_html(doc: Any, sel: str, want_all: bool, base_url: str = "") -> list:
    """在 DOM 上执行一条选择器（可能带 `@` 链），返回 Node 或字符串列表。"""
    segs, verb, attr = _split_at_chain(sel)
    nodes: list = [doc] if isinstance(doc, Node) else []
    for seg in segs:
        seg = seg.strip()
        if not seg:
            continue
        nodes = _select_nodes(nodes, seg)
        if not nodes:
            return []
    if verb is None and attr is None:
        # 没到取值那一步：返回节点本身，交给后续 @ 段或 want_all 继续加工
        return nodes if want_all else nodes[:1]
    vals = []
    for n in nodes:
        v = _get_value(n, verb, attr, base_url)
        if v is not None and v != "":
            vals.append(v)
    if not want_all:
        return vals[:1]
    return vals


def _select_json(doc: Any, sel: str, want_all: bool) -> list:
    vals = _jsonpath_all(doc, sel)
    if not vals:
        return []
    if not want_all:
        return [vals[0]]
    # 命中的是数组节点本身时展开成元素，便于逐条迭代
    flat: list = []
    for v in vals:
        if isinstance(v, list):
            flat.extend(v)
        else:
            flat.append(v)
    return flat


def eval_values(doc: Any, rule: str, want_all: bool = False, base_url: str = "") -> list:
    """在 doc 上执行一条 Legado 规则，返回结果列表。

    doc 可以是 Node（HTML）或 dict/list（JSON）—— 这就是本书源包里那批
    API 站点（`api.17k.com`、`appi.kuwo.cn`…）能跑起来的前提：响应体先看是不是
    JSON，是就当 JSON 文档、规则按 JSONPath 求值。
    """
    if not rule or not rule.strip():
        return []
    rule = rule.strip()

    bang: int | None = None
    m = _BANG_SUFFIX.search(rule)
    if m:
        bang = int(m.group(1))
        rule = rule[: m.start()] + rule[m.start() + len(m.group(1)) + 1 :]

    selector, pat, repl = _split_regex_sandwich(rule)

    # `||` / `&&` 多路回退：依次尝试，取首个非空
    if _ALT_RE.search(selector) and not isinstance(doc, (dict,)):
        tail = f"##{pat}##{repl}###" if pat else ""
        vals: list = []
        for branch in _ALT_RE.split(selector):
            vals = eval_values(doc, branch + tail, want_all=want_all, base_url=base_url)
            if vals:
                break
        if bang is not None:
            vals = _apply_bang(vals, bang)
        return vals

    kind, core = _split_marker(selector)
    if kind == "xpath" or (not kind and core.strip().startswith("//")):
        raise UnsupportedRule("XPath 规则（@XPath:）暂不支持")
    _require_no_js(core)

    pat_re = _compile_safe(pat) if pat else None
    repl_py = _legado_repl_to_python(repl) if pat else ""

    is_json_doc = isinstance(doc, (dict, list))

    # 空选择器 + 正则：Legado 对整篇文档（源码）做正则
    if pat and not core.strip():
        return [_norm(_apply_regex(_whole_text(doc), pat_re, repl_py, repl))]

    low = core.strip().lower()

    # 裸写属性名（`href`、`content`…）：Legado 按取属性处理
    if low in _BARE_ATTR and not is_json_doc:
        vals = []
        if isinstance(doc, Node):
            v = doc.attrs.get(low, "")
            if low in ("href", "src") and v and base_url:
                v = urllib.parse.urljoin(base_url, v)
            if v:
                vals = [v]
        if pat_re and vals:
            vals = [_norm(_apply_regex(_as_text(x), pat_re, repl_py, repl)) for x in vals]
        if bang is not None:
            vals = _apply_bang(vals, bang)
        return vals if want_all else vals[:1]

    # 裸写取值动词（`chapterName: "text"`）：作用于作用域本身
    if low in _VERB_SET and not is_json_doc:
        vals = []
        if isinstance(doc, Node):
            v = _get_value(doc, low, None, base_url)
            if v:
                vals = [v]
        if pat_re and vals:
            vals = [_norm(_apply_regex(_as_text(x), pat_re, repl_py, repl)) for x in vals]
        if bang is not None:
            vals = _apply_bang(vals, bang)
        return vals if want_all else vals[:1]

    # 有 `!n` 时必须先取全部再按下标裁：`p!-1@text` 只取首个匹配就永远取不到末位
    want = want_all or bang is not None
    if kind == "json" or (not kind and is_json_doc):
        vals = _select_json(doc, core if kind == "json" else selector, want)
    else:
        vals = _select_html(doc, core, want, base_url)

    if pat_re and vals:
        vals = [_norm(_apply_regex(_as_text(x), pat_re, repl_py, repl)) for x in vals]
    if bang is not None:
        vals = _apply_bang(vals, bang)
    return vals


def first(doc: Any, rule: str, default: str = "") -> str:
    """取第一个匹配，返回字符串。"""
    vals = eval_values(doc, rule, want_all=False)
    return _as_text(vals[0]) if vals else default


def eval_rule(rule: str, scope: list, base_url: str = "") -> str:
    """（兼容入口）对一组作用域节点求值，拼成字符串。"""
    if not scope:
        return ""
    out: list[str] = []
    for node in scope:
        for v in eval_values(node, rule, want_all=False, base_url=base_url):
            if v is not None and _as_text(v):
                out.append(_as_text(v))
    return "\n".join(out)


def eval_list(rule: str, scope: list, base_url: str = "") -> list[Node]:
    """（兼容入口）求「列表规则」：bookList / chapterList，返回节点集合。

    `||` / `&&` 备选依然适用（Legado 语义：取首个非空的那条）。
    """
    if not rule or not rule.strip():
        return []
    _require_no_js(rule, "列表规则")
    for alt in _ALT_RE.split(rule):
        alt = (alt or "").strip()
        if not alt:
            continue
        vals: list[Node] = []
        seen: set[int] = set()
        for node in scope:
            for v in eval_values(node, alt, want_all=True, base_url=base_url):
                if isinstance(v, Node) and id(v) not in seen:
                    seen.add(id(v))
                    vals.append(v)
        if vals:
            return vals
    return []


# ---------------------------------------------------------------- HTTP


def _decode(raw: bytes, forced: str = "") -> str:
    """按 强制 > HTTP/meta > 探测 的顺序解码。中文站常见 gbk。"""
    if forced:
        try:
            return raw.decode(forced, errors="replace")
        except (LookupError, UnicodeDecodeError):
            pass
    m = _META_CHARSET_RE.search(raw[:4096])
    if m:
        enc = m.group(1).decode("ascii", errors="replace")
        try:
            return raw.decode(enc, errors="replace")
        except (LookupError, UnicodeDecodeError):
            pass
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _clean_url(url: str) -> str:
    """去掉 URL 里的控制字符。

    书源规则拼接常把换行带进 URL（模板换行、字段值含换行），发出去会直接报错。
    Legado 的 URL 处理会忽略这些字符，这里对齐它的行为。
    """
    if not url:
        return url
    if any(ch in url for ch in "\r\n\t\x00"):
        return re.sub(r"[\x00-\x1f\x7f]", "", url)
    return url


def _netloc(url: str) -> str:
    return (urllib.parse.urlparse(url).netloc or "").split(":")[0]


def _domain_match(cookie_domain: str, host: str) -> bool:
    """宽松域匹配：cookie 域与请求域相等，或互为父子域。

    标准语义（host 是 cookie 域的子域）之外，额外允许 cookie 域是 host 的子域
    （Legado 反查，起点源用 `qidian.com` 读 `read.qidian.com` 的 token 就靠它）。
    """
    cd = (cookie_domain or "").lstrip(".")
    if not cd or not host:
        return False
    return cd == host or host.endswith("." + cd) or cd.endswith("." + host)


def _safe_urljoin(base: str, href: str) -> str:
    """拼相对 URL。base 补 `/` 结尾，避免 `fixtures` 这类目录被当文件名丢掉。"""
    href = (href or "").strip()
    if not href:
        return href
    if href.startswith(("http://", "https://", "file://", "data:")):
        return href
    if href.startswith("//"):
        proto = "http:" if (base or "").startswith("http://") else "https:"
        return proto + href
    if not base:
        return href
    return urllib.parse.urljoin(base if base.endswith("/") else base + "/", href)


def abs_url(value: str, base: str) -> str:
    """相对地址补成绝对地址（封面 / 链接常用）。"""
    v = (value or "").strip()
    if not v or not base:
        return v
    if v.startswith(("http://", "https://", "//", "data:")):
        return _safe_urljoin(base, v)
    return _safe_urljoin(base, v)


# 兼容旧名字
_abs_url = abs_url


class Fetcher:
    """网络层：urllib + cookie jar，统一返回 (raw_text, doc)。

    cookie jar 按源隔离（构造时传入）—— 一批书的 session 不能串到另一本书，
    而且很多站的反爬就是「先验 cookie 再给内容」。
    """

    def __init__(self, src: "Source", timeout: int = 15, max_bytes: int = 4 * 1024 * 1024,
                 user_agent: str = ""):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.base_headers = {
            # 🔴 只声明 gzip/deflate：br 需要 brotli（非标准库），不能让源站回 br
            "Accept-Encoding": "gzip, deflate",
            "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
            "User-Agent": user_agent or DEFAULT_UA,
        }
        self.base_headers.update(src.header)

    # -- cookie 读写（对齐 Legado 的 cookie.getKey / cookie.setCookie） --

    def cookie_header(self, url: str) -> str:
        host = _netloc(url)
        if not host:
            return ""
        return "; ".join(
            f"{c.name}={c.value}" for c in self.jar if c.domain and _domain_match(c.domain, host)
        )

    def cookie_key(self, url: str, key: str) -> str:
        for part in self.cookie_header(url).split(";"):
            k, _, v = part.strip().partition("=")
            if k == key:
                return v
        # 未命中时全局同名兜底（Legado 宽容语义）
        for c in self.jar:
            if c.name == key:
                return c.value
        return ""

    def set_cookie(self, url: str, cookie_str: str) -> None:
        host = _netloc(url)
        if not host or not cookie_str:
            return
        for part in cookie_str.split(";"):
            k, _, v = part.strip().partition("=")
            if k:
                ck = http.cookiejar.Cookie(
                    version=0, name=k, value=v, port=None, port_specified=False,
                    domain=host, domain_specified=True, domain_initial_dot=False,
                    path="/", path_specified=True, secure=False, expires=None,
                    discard=True, comment=None, comment_url=None, rest={}, rfc2109=False,
                )
                self.jar.set_cookie(ck)

    # -- 请求 --

    def _open(self, url: str, headers: dict[str, str], data: bytes | None, method: str) -> bytes:
        req = urllib.request.Request(_clean_url(url), data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read(self.max_bytes + 1)
                if len(raw) > self.max_bytes:
                    raise FetchError(f"响应超过 {self.max_bytes // 1024 // 1024}MB，已放弃")
                ctype = (resp.headers.get("Content-Type") or "").lower()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                charset = ""
                if "charset=" in ctype:
                    charset = ctype.split("charset=")[-1].strip().strip('"')
        except FetchError:
            raise
        except urllib.error.HTTPError as e:
            raise FetchError(f"源站返回 {e.code}") from e
        except urllib.error.URLError as e:
            raise FetchError(f"连不上源站（{e.reason}）") from e
        except TimeoutError as e:
            raise FetchError(f"源站超时（>{self.timeout}s）") from e

        if enc == "gzip":
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass
        elif enc == "deflate":
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                try:
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                except zlib.error:
                    pass
        self._last_charset = charset
        return raw

    def request(self, method: str, url: str, *, headers: dict[str, str] | None = None,
                body: Any = None, charset: str = "") -> tuple[str, Any]:
        """发请求并解析 → (原始文本, 文档对象)。

        文档对象优先按 JSON 解析（这批源里有一半是 API 站），失败才当 HTML。
        """
        self._last_charset = ""
        if url.startswith("file://"):
            # 本地夹具 URL（离线确定性测试）：去掉查询串后直读
            raw = Path(url[len("file://") :].split("?")[0].lstrip("/")).read_bytes()
            charset = charset
            self._last_charset = ""
            text = _decode(raw, charset)
            return text, self.parse_bytes(raw, charset, text)
        h = dict(self.base_headers)
        if headers:
            h.update({str(k): str(v) for k, v in headers.items()})
        data: bytes | None = None
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        elif isinstance(body, (str, bytes)):
            # 🔴 httpx 会自动给 `data=str` 带表单头，手写 urllib 必须补上：
            # PHP 站点（歌书网、红薯这类）读不到 $_POST 就返回一个空结果页，
            # 症状是「请求成功但一条都选不中」而不是报错，极难排查。
            data = body.encode("utf-8", errors="replace") if isinstance(body, str) else body
            h.setdefault("Content-Type", "application/x-www-form-urlencoded")
        if data is not None and method.upper() == "GET":
            method = "POST"
        raw = self._open(url, h, data, method.upper())
        text = _decode(raw, charset or self._last_charset)
        return text, self.parse_bytes(raw, charset or self._last_charset, text)

    def get(self, url: str, *, headers: dict[str, str] | None = None, charset: str = "") -> tuple[str, Any]:
        return self.request("GET", url, headers=headers, charset=charset)

    def parse_bytes(self, raw: bytes, charset: str, text: str) -> Any:
        try:
            return json.loads(text)
        except Exception:
            pass
        return parse_html(text)

    def parse(self, raw: bytes, charset: str = "") -> Any:
        return self.parse_bytes(raw, charset, _decode(raw, charset))


# ---------------------------------------------------------------- 模板 / 变量


def fill(rule: str, *, key: str = "", page: int = 1, base_url: str = "",
         ctx: Any = None, charset: str = "utf-8") -> str:
    """填充 URL / 规则里的 `{{...}}` 占位符。

    - `{{key}}` / `{{searchKey}}` → 搜索词（按源站编码 percent-encode）
    - `{{page}}`                  → 页码
    - `{{page-1}}` / `{{(page-1)*10}}` → 受限算术（本书源包里有 279 处这种写法）
    - `{{baseUrl}}`               → Legado 的「当前页面 URL」
    - `{{字段名}}` / `{{$.path}}`  → 取当前条目（JSON）的字段
    其余需要 JS 求值的写法保持原样（源站不支持时那只可能是 JS 源）。
    """
    if not rule:
        return rule or ""

    def _repl(m: "re.Match[str]") -> str:
        inner = (m.group(1) or "").strip()
        if inner in ("key", "searchKey"):
            return urllib.parse.quote(key, encoding=charset or "utf-8", errors="replace")
        if inner == "page":
            return str(page)
        if inner == "baseUrl":
            return base_url
        if isinstance(ctx, dict) and inner in ctx:
            v = ctx[inner]
            return "" if v is None else str(v)
        if inner.startswith(("$", "@@", "@css:")) and ctx is not None:
            try:
                vals = eval_values(ctx, inner, want_all=False)
                return _as_text(vals[0]) if vals else ""
            except Exception:
                return ""
        if _ARITH_OK.fullmatch(inner or ""):
            try:
                return str(eval(inner, {"__builtins__": {}}, {"page": page}))  # noqa: S307
            except Exception:
                return inner
        return inner

    return _TEMPLATE_RE.sub(_repl, rule)


def apply_gets(rule: str, vars: dict) -> str:
    """`@get:{key}` → 暂存值（Legado 的规则变量，跨规则累积）。"""
    if "@get:" not in (rule or ""):
        return rule or ""
    return _GET_RE.sub(lambda m: str(vars.get(m.group(1).strip(), "")), rule)


def apply_cookies(rule: str, getter) -> str:
    if "@cookie:" not in (rule or ""):
        return rule or ""
    return _COOKIE_RE.sub(lambda m: getter(m.group(1).strip()), rule)


def apply_puts(rule: str, item: Any, eval_fn) -> tuple[str | None, dict]:
    """`@put:{bid:$.id}` → 写入暂存区，返回 (去掉 @put 后的纯规则, 本次写入)。

    整条规则就是 @put 时返回 (None, writes)，由调用方决定取哪个值。
    """
    if "@put:" not in (rule or ""):
        return rule, {}
    writes: dict = {}

    def _resolve(expr: str):
        expr = (expr or "").strip()
        if not expr:
            return ""
        if re.fullmatch(r"[A-Za-z_]\w*", expr):
            expr = "$." + expr
        try:
            vals = eval_fn(item, expr)
            return _as_text(vals[0]) if vals else ""
        except Exception:
            return ""

    def _repl(m):
        for pair in m.group(1).split(","):
            pair = pair.strip()
            if not pair:
                continue
            k, _, expr = pair.partition(":")
            if not expr:
                k = expr = pair
            writes[k.strip()] = _resolve(expr)
        return ""

    clean = _PUT_RE.sub(_repl, rule).strip()
    if not clean:
        return None, writes
    return clean, writes


# ---------------------------------------------------------------- 书源模型


@dataclass
class Source:
    name: str
    url: str
    search_url: str
    header: dict[str, str] = field(default_factory=dict)
    rule_search: dict = field(default_factory=dict)
    rule_book_info: dict = field(default_factory=dict)
    rule_toc: dict = field(default_factory=dict)
    rule_content: dict = field(default_factory=dict)
    unsupported: str = ""

    @property
    def can_search(self) -> bool:
        # 🔴 规则里带 @js: 时字段依然非空，只看字段存在性会把它判成可用
        if self.unsupported:
            return False
        rs = self.rule_search
        return bool(self.search_url) and bool(rs.get("bookList") and rs.get("name") and rs.get("bookUrl"))

    @property
    def can_toc(self) -> bool:
        if self.unsupported:
            return False
        rt = self.rule_toc
        return bool(rt.get("chapterList") and rt.get("chapterName") and rt.get("chapterUrl"))

    @property
    def can_content(self) -> bool:
        if self.unsupported:
            return False
        return bool(self.rule_content.get("content"))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "can_search": self.can_search,
            "can_toc": self.can_toc,
            "can_content": self.can_content,
            "unsupported": self.unsupported,
        }


def _why_unsupported(raw: dict) -> str:
    """为什么这个源跑不了 —— 给 UI 的人话原因，空串 = 可以试。

    🔴 只看我们真会用到的那几段。整份 JSON 扫一遍会把「只有发现页用 JS」的源也
    误杀掉 —— 那是另一个入口，搜索→目录→正文这条链根本走不到它。
    """
    used = {
        "searchUrl": raw.get("searchUrl") or "",
        "ruleSearch": raw.get("ruleSearch") or {},
        "ruleToc": raw.get("ruleToc") or {},
        "ruleContent": raw.get("ruleContent") or {},
    }
    blob = json.dumps(used, ensure_ascii=False)
    if "<js>" in blob:
        return "规则需要 JS 引擎（当前为节省体积未内置）"
    if "@js:" in blob:
        return "取值规则需要 JS 引擎"
    if "decrypt" in blob.lower():
        return "需要字体解密（明确不做）"
    rs = used["ruleSearch"]
    if not used["searchUrl"] and not rs.get("bookList"):
        return "该源没有可用的搜索规则"
    return ""


def parse_header_field(raw: str) -> dict[str, str]:
    """书源的 header 字段可能是 JSON，也可能是 `K: V` 逐行文本。"""
    raw = (raw or "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                return {str(k): str(v) for k, v in d.items()}
        except Exception:
            return {}
    out: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def load_sources(path: str | Path = "") -> tuple[list[Source], list[str]]:
    """读书源 JSON。返回 (源列表, 读取错误)。单个源坏掉不影响其余。"""
    p = Path(path) if path else DEFAULT_SOURCES_PATH
    if not p.exists():
        return [], [f"书源文件不存在：{p}"]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return [], [f"书源文件读不出来：{e}"]

    if isinstance(data, dict):
        data = data.get("bookSources") or data.get("sources") or []
    if not isinstance(data, list):
        return [], ["书源文件格式不是数组"]

    sources: list[Source] = []
    errors: list[str] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        try:
            src = Source(
                name=str(raw.get("bookSourceName") or "").strip(),
                url=str(raw.get("bookSourceUrl") or "").strip(),
                search_url=str(raw.get("searchUrl") or "").strip(),
                header=parse_header_field(str(raw.get("header") or "")),
                rule_search=raw.get("ruleSearch") or {},
                rule_book_info=raw.get("ruleBookInfo") or {},
                rule_toc=raw.get("ruleToc") or {},
                rule_content=raw.get("ruleContent") or {},
                unsupported=_why_unsupported(raw),
            )
            if src.name:
                sources.append(src)
        except Exception as e:  # 单个源字段怪异不该拖垮整份文件
            errors.append(f"{raw.get('bookSourceName', '?')}: {e}")
    return sources, errors


def find_source(sources: list[Source], name: str) -> Source | None:
    for s in sources:
        if s.name == name:
            return s
    return None


# ---------------------------------------------------------------- 兼容旧 API：build_url


def build_url(tpl: str, *, key: str = "", page: int = 1, base: str = "", charset: str = "utf-8") -> tuple[str, dict]:
    """展开书源的 URL 模板（旧接口保留，实际请求一律走 Engine._request）。

    返回 (url, options)。`,{...}` 选项段会被剥离出来。
    """
    tpl = (tpl or "").strip()
    if not tpl:
        raise UnsupportedRule("URL 模板为空")
    _require_no_js(tpl, "URL")

    opts: dict = {}
    m = re.search(r",\s*\{", tpl)
    if m:
        head, tail = tpl[: m.start()], tpl[m.start() + 1 :]
        depth = 0
        for i, ch in enumerate(tail):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        d = json.loads(tail[: i + 1])
                        if isinstance(d, dict):
                            opts = d
                    except Exception:
                        pass
                    break
        tpl = head

    relative = tpl.startswith("<")
    tpl = tpl.lstrip("<").rstrip(">").strip()
    url = fill(tpl, key=key, page=page, charset=charset)
    if relative and base:
        url = _safe_urljoin(base, url)
    return url, opts


# ---------------------------------------------------------------- Engine


#: 选择器形态 vs URL 模板：URL 字段规则 `bookUrl` 有时是选择器、有时就是个 URL。
_SELECTOR_LIKE = re.compile(r"^[A-Za-z0-9_\-\[\]@:.# >*+=~|()'\"()]+$")


def _is_selector_like(s: str) -> bool:
    """判断 {{}} 填充后的 URL 规则是否仍是选择器（需要 eval_values 求值）。

    完整 URL（http://...、/path）直接作为原文返回；`$.url`、`a@href`、`//a/@href`
    这类选择器形态则求值取第一个命中。
    """
    s = (s or "").strip()
    if not s:
        return False
    if s.startswith(("$.", "$..", "@css:", "@CSS:", "@json:", "@Json:", "@@", "//")):
        return True
    if "://" in s or s.startswith(("/", "./", "../")):
        return False
    return bool(_SELECTOR_LIKE.fullmatch(s)) and "," not in s


def _parse_request_opts(text: str) -> dict | None:
    """解析 Legado 载体里的请求选项：标准 JSON 优先，退回 JS 对象字面量。

    书源里常见 `{'method': 'POST', 'body': 'bid=523'}` 这种单引号写法
    （Legado 用 Gson 宽松解析，Python 的 json 不认），直接 json.loads 会失败
    并让 POST 静默退化成 GET —— 站点因此返回「参数缺失」而不是明确报错。
    """
    import ast

    for candidate in (text, text.replace("'", '"')):
        try:
            val = json.loads(candidate)
            if isinstance(val, dict):
                return val
        except Exception:
            pass
    s = text.strip()
    s = re.sub(r"\btrue\b", "True", s)
    s = re.sub(r"\bfalse\b", "False", s)
    s = re.sub(r"\bnull\b", "None", s)
    try:
        val = ast.literal_eval(s)
    except Exception:
        return None
    return val if isinstance(val, dict) else None


def _split_request_opts(url: str) -> tuple[str, dict]:
    """拆 Legado 载体里的 `URL,{json选项}` → (URL, options)。没有选项段时 opts 为 {}。

    🔴 从**最后一个** `,{` 处切分：URL 参数里本身可能带逗号（红薯、塔读等源的
    长 query 里有），按第一个逗号切会把查询串切断。
    """
    idx = (url or "").rfind(",{")
    if idx <= 0:
        return url or "", {}
    head, tail = url[:idx], url[idx + 1 :]
    if not tail.lstrip().startswith("{"):
        return url, {}
    opts = _parse_request_opts(tail)
    return head, opts if isinstance(opts, dict) else {}


class Engine:
    """一个书源 → 搜索/目录/正文的采集引擎（对齐原实现的 Engine 语义）。"""

    def __init__(self, src: Source, *, timeout: int = 15, max_bytes: int = 4 * 1024 * 1024,
                 user_agent: str = ""):
        self.src = src
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.fetch = Fetcher(src, timeout, max_bytes, user_agent)
        # @put/@get 暂存区（Legado 语义：单个源内跨规则累积）
        self.vars: dict[str, str] = {}
        # Legado 的 baseUrl = **当前页面 URL**（不是书源首页）：目录/章节 URL 模板
        # 普遍靠它拼接口地址，所以每跳一页都要更新。
        self.page_url = src.url

    # -- 变量 / 模板 --

    def set_page_url(self, url: str) -> None:
        self.page_url = url or self.page_url

    def _fill(self, rule: str, ctx: Any = None, key: str = "", page: int = 1, charset: str = "utf-8") -> str:
        return fill(rule, key=key, page=page, base_url=self.page_url, ctx=ctx, charset=charset)

    # -- 字段取值 --

    def field(self, rule: str, item: Any, *, single_line: bool = False) -> str:
        """文本字段规则：@get/@cookie 替换 → 取值，并处理 @put 写入。"""
        if not rule:
            return ""
        rule = apply_gets(rule, self.vars)
        rule = apply_cookies(rule, lambda k: self.fetch.cookie_key(self.page_url, k))
        clean, writes = apply_puts(
            rule, item, lambda d, r: eval_values(d, r, want_all=False, base_url=self.page_url)
        )
        if writes:
            self.vars.update(writes)
        if clean is None:
            return str(list(writes.values())[-1]) if writes else ""
        vals = eval_values(item, clean, want_all=False, base_url=self.page_url)
        raw = _as_text(vals[0]) if vals else ""
        return clean_text(raw, single_line=single_line) if single_line else raw

    def url_field(self, rule: str, item: Any, *, charset: str = "utf-8") -> str:
        """URL 字段规则：{{}}填充 → @get/@cookie → 选择器求值 or 模板原文。"""
        if not rule:
            return ""
        rule = self._fill(rule, ctx=item, charset=charset)
        rule = apply_gets(rule, self.vars)
        rule = apply_cookies(rule, lambda k: self.fetch.cookie_key(self.page_url, k))
        clean, writes = apply_puts(
            rule, item, lambda d, r: eval_values(d, r, want_all=False, base_url=self.page_url)
        )
        if writes:
            self.vars.update(writes)
        if clean is None:
            return str(list(writes.values())[-1]) if writes else ""
        _require_no_js(clean, "URL 规则")
        if _is_selector_like(clean):
            vals = eval_values(item, clean, want_all=False, base_url=self.page_url)
            if vals:
                return _as_text(vals[0])
        return clean

    # -- 请求 --

    def request(self, url: str, *, charset: str = "") -> tuple[str, Any]:
        """发请求。`http://url,{json}` 里带 body/method 视为 POST，否则按选项 GET。

        🔴 从**最后一个** `,{` 处切分：URL 参数里本身可能带逗号（红薯、塔读等源的
        长 query 里有），按第一个逗号切会把查询串切断。
        """
        url, opts = _split_request_opts(url)
        charset = charset or str(opts.get("charset") or "")
        headers = opts.get("headers") if isinstance(opts.get("headers"), dict) else None
        if "body" in opts or "method" in opts:
            body = opts.get("body", opts)
            method = str(opts.get("method", "POST")).upper()
            return self.fetch.request(method, url, headers=headers, body=body, charset=charset)
        return self.fetch.get(url, headers=headers, charset=charset)

    def _search_url(self, keyword: str, page: int, charset: str = "utf-8") -> str:
        tpl = self.src.search_url or ""
        if tpl.startswith("<"):
            # Legado：以 `<` 开头表示相对书源首页
            return _safe_urljoin(self.src.url, self._fill(tpl.lstrip("<").rstrip(">"),
                                                          key=keyword, page=page, charset=charset))
        filled = self._fill(tpl, key=keyword, page=page, charset=charset)
        return filled if filled.startswith(("http://", "https://", "file://")) else _safe_urljoin(
            self.src.url, filled
        )

    # -- 三步采集 --

    def search(self, keyword: str, page: int = 1, charset: str = "utf-8") -> list[dict]:
        if self.src.unsupported:
            raise UnsupportedRule(self.src.unsupported)
        if not self.src.can_search:
            raise UnsupportedRule("这个源缺搜索规则（bookList / name / bookUrl）")
        rs = self.src.rule_search
        _tpl, sopts = _split_request_opts(self.src.search_url)
        cset = str(sopts.get("charset") or charset)
        url = self._search_url(keyword, page, cset)
        self.set_page_url(url)
        _raw, doc = self.request(url, charset=cset)

        items = eval_values(doc, rs.get("bookList") or "", want_all=True)
        out: list[dict] = []
        for it in items:
            book_url_rule = (rs.get("bookUrl") or "").strip()
            title = self.field(rs.get("name") or "", it, single_line=True)
            if not title or not book_url_rule:
                continue
            # 按 Legado 字段顺序求值：文本字段（可能 @put 写变量）先于 bookUrl
            author = self.field(rs.get("author") or "", it, single_line=True)
            cover = self.field(rs.get("coverUrl") or "", it, single_line=True)
            intro = self.field(rs.get("intro") or "", it)
            last = self.field(rs.get("lastChapter") or "", it, single_line=True)
            kind = self.field(rs.get("kind") or "", it, single_line=True)
            words = self.field(rs.get("wordCount") or "", it, single_line=True)
            book_url = self.url_field(book_url_rule, it, charset=cset)
            out.append(
                {
                    "name": title,
                    "author": author,
                    "bookUrl": _safe_urljoin(self.page_url, book_url) if book_url else "",
                    # 封面大多是相对路径，前端拿去直接 <img src> 会 404
                    "cover": abs_url(cover, self.page_url),
                    "intro": intro,
                    "lastChapter": last,
                    "kind": kind,
                    "wordCount": words,
                }
            )
        return [b for b in out if b.get("name")]

    def toc(self, book_url: str) -> list[dict]:
        if self.src.unsupported:
            raise UnsupportedRule(self.src.unsupported)
        if not self.src.can_toc:
            raise UnsupportedRule("这个源缺目录规则（chapterList / chapterName / chapterUrl）")
        rt = self.src.rule_toc

        self.set_page_url(book_url)
        _raw, doc = self.request(book_url)

        # 详情页往往只是个壳，真正的目录在 ruleBookInfo.tocUrl（27 个可用源里 15 个有）
        toc_url = self._resolve_toc_url((self.src.rule_book_info or {}).get("tocUrl") or "", doc, book_url)
        if toc_url:
            self.set_page_url(toc_url)
            _raw, doc = self.request(toc_url)

        items = eval_values(doc, rt.get("chapterList") or "", want_all=True)
        out: list[dict] = []
        for it in items:
            title = self.field(rt.get("chapterName") or "", it, single_line=True)
            curl_rule = (rt.get("chapterUrl") or "").strip()
            if not title or not curl_rule:
                continue
            href = self.url_field(curl_rule, it)
            out.append({"title": title, "url": _safe_urljoin(self.page_url, href) if href else ""})
        return [c for c in out if c.get("title")]

    def _resolve_toc_url(self, rule: str, doc: Any, base: str) -> str:
        """求值目录 URL：{{}}填充 → @get/@cookie → 选择器求值 → 拼基址。

        `@get:{var}` 必须在这里替换：很多源在搜索阶段用 `@put:` 存下 bookId/gid，
        目录接口 URL 靠 `@get:` 取回，漏掉这步请求会打到错误的地址。
        """
        if not rule:
            return ""
        rule = self._fill(rule, ctx=doc)
        rule = apply_gets(rule, self.vars)
        rule = apply_cookies(rule, lambda k: self.fetch.cookie_key(self.page_url, k))
        _require_no_js(rule, "目录 URL 规则")
        clean, writes = apply_puts(
            rule, doc, lambda d, r: eval_values(d, r, want_all=False, base_url=self.page_url)
        )
        if writes:
            self.vars.update(writes)
        if clean is None:
            rule = str(list(writes.values())[-1]) if writes else ""
        elif _is_selector_like(clean):
            vals = eval_values(doc, clean, want_all=False, base_url=self.page_url)
            rule = _as_text(vals[0]) if vals else clean
        else:
            rule = clean
        return _safe_urljoin(base, rule) if rule and not rule.startswith(("http://", "https://")) else rule

    def content(self, chapter_url: str) -> tuple[str, str]:
        if self.src.unsupported:
            raise UnsupportedRule(self.src.unsupported)
        if not self.src.can_content:
            raise UnsupportedRule("这个源缺正文规则（content）")
        rc = self.src.rule_content

        self.set_page_url(chapter_url)
        _raw, doc = self.request(chapter_url)

        rule = rc.get("content") or ""
        rule = apply_gets(rule, self.vars)
        # 🔴 刻意偏离原实现（它用 first 只取第一个命中）：正文规则常是 `@tag.p`
        # 这种「多个节点拼起来」的写法，只取第一个会把正文截断。
        vals = eval_values(doc, rule, want_all=True, base_url=chapter_url)
        text = "\n".join(_as_text(v) for v in vals if _as_text(v))
        text = text.replace("\u00a0", " ")

        title = ""
        if rc.get("title"):
            tvals = eval_values(doc, rc.get("title") or "", want_all=False, base_url=chapter_url)
            title = clean_text(_as_text(tvals[0]), single_line=True) if tvals else ""
        # 出口统一净化：正文可能是内层 HTML（`@html` 规则），也可能被塞了
        # 「最新网址：www.xxx.info」这类站点推广。
        return title, clean_text(text, strip_ads=True)


# ---------------------------------------------------------------- 顶层便捷入口


def _engine(src: Source, cfg=None) -> Engine:
    return Engine(
        src,
        timeout=_timeout(cfg),
        max_bytes=_max_bytes(cfg),
        user_agent=getattr(cfg, "user_agent", "") if cfg is not None else "",
    )


def _timeout(cfg) -> int:
    return int(getattr(cfg, "timeout_sec", 15) or 15) if cfg is not None else 15


def _max_bytes(cfg) -> int:
    return int(getattr(cfg, "max_bytes", 4 * 1024 * 1024) or 4 * 1024 * 1024) if cfg is not None else 4 * 1024 * 1024


def search_books(src: Source, keyword: str, page: int = 1, *, cfg=None) -> list[dict]:
    """搜书名 → [{name, author, bookUrl, cover, intro, lastChapter}]。"""
    return _engine(src, cfg).search(keyword, max(1, int(page)))


def fetch_toc(src: Source, book_url: str, *, cfg=None) -> list[dict]:
    """取目录 → [{title, url}]。"""
    return _engine(src, cfg).toc(book_url)


def fetch_chapter(src: Source, url: str, *, cfg=None) -> dict:
    """取正文 → {title, text}。"""
    title, text = _engine(src, cfg).content(url)
    return {"title": title, "text": text}
