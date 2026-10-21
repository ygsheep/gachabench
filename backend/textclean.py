# -*- coding: utf-8 -*-
"""文本卫生：把「外来文本」洗成可以直接用的纯文本。

来源：novel-booksource-engine 的 `engine/textclean.py`（Legado 书源引擎的正文出口）。
移植时做了两件事：

1. **拆成两层**，让通用能力可以在「不采集网文」的场合单独用：

   - `clean_basic()`：去 HTML 块 / 剥标签 / 反转义实体 / 零宽字符 / 空白折叠。
     这一步不依赖任何「这是网文」的假设，任何外来文本（粘贴、导入、摘录）都能用。
   - `clean_ads()`：去裸露 URL / 域名 / 站点推广语。这是**网文站特征**，
     只有采集来的正文才需要，剧本和提示词绝不该走这条路。

2. **保持纯函数、零第三方依赖**（只用 `re` 与 `html`），方便单测与打包。

🔴 红线（写在这里，防止后人误用）：
   本模块**禁止**出现在 `提示词/*.md` 的读取链上（`splitter.py` / `prompt_locator.py` /
   `prompt_locator` 相关的任何路由）。段 md 是唯一真相，行号是定点改动工单正确性的基础
   （`/api/ticket` 给出的「文件第 N 行」），任何清洗都会移动行号、让工单指错位置。
   它只用于**文本进入 GB 之前**的入口：导入、粘贴、摘录、采集。

「宁可少删」是总取向：只有明确命中特征才删，不做主观改写，避免把正文里的正常句子误伤。
"""
from __future__ import annotations

import html as _html
import re
from typing import Iterable

# ---- 整块丢弃 ----
_DROP_BLOCK_RE = re.compile(r"<(script|style|iframe|noscript)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)

# ---- 结构性换行 ----
_BR_RE = re.compile(r"<br\b[^>]*>", re.I)
_BLOCK_CLOSE_RE = re.compile(
    r"</(?:p|div|h[1-6]|li|tr|section|article|blockquote|ul|ol|table|figure|dd|dt)\s*>", re.I
)
_BLOCK_OPEN_RE = re.compile(
    r"<(?:p|div|h[1-6]|li|tr|section|article|blockquote|ul|ol|table|figure|hr)\b[^>]*>", re.I
)
# 🔴 剥标签时**必须放过 GB 的标签体系**：`<Picture N>` / `<Subject N>` / `<Audio N>`。
# 它们长得跟 HTML 标签一模一样（大写字母开头 + 空格 + 数字），普通标签正则会当成
# `<subject>` 之类直接吃掉 —— 而这三个标签是段 md 的唯一引用锚，H3 靠它们把参考图 /
# 参考音频接到人身上，被削掉就等于提示词指错了对象（画面照样出得来，光看成片发现不了）。
# 所以这里用否定前瞻把它们显式排除；`<picture src="1">` 这种真 HTML 标签不受影响
# （前瞻要求「关键字 + 空白 + 纯数字 + 可选斜杠 + >」才排除）。
_TAG_RE = re.compile(
    r"</?(?!(?:picture|subject|audio)\s+\d+\s*/?>)[a-zA-Z!][^>]*>",
    re.S | re.I,
)

# ---- 网址 ----
# 字符类里排除 CJK：`www.a.com正文一` 这种「网址与正文之间没有空格」的写法很常见，
# 不排除的话整个后半句会被当成 URL 一起吃掉（NBE 实测踩过这个坑）。
_URL_RE = re.compile(
    r"(?:https?://|www\.|m\.)[^\s<>\"'（）()【】\[\]，,。；;、\u4e00-\u9fff]+", re.I
)
_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9][a-z0-9-]{0,61}\.)+"
    r"(?:com|net|org|cn|cc|info|me|top|xyz|vip|io|co|la|tv|biz|site|club|online|app|"
    r"fun|live|shop|wang|ren|asia|us|uk|de|ru|jp|kr|tw|hk|pro|name|mobi)"
    r"(?:/[A-Za-z0-9._~%\-/&=?#+]*)?",
    re.I,
)

# ---- 广告特征 ----
# 强特征：命中即判定为推广语（整行都在说站点）。
# 只放「不可能出现在正文里」的组合词 —— 单字词（如「广告」「域名」）放进来会把
# 「电视里正在播广告。」这类正常句子误删，它们归到弱特征。
_HARD_AD_RE = re.compile(
    r"(最新网址|最新域名|请记住本站|记住本站|本站网址|本站域名|永久域名|防盗域名|防走失|"
    r"手机版?阅读网址|手机阅读网址|首发地址|本书首发|首发于|txt下载|全集下载|全文免费阅读|"
    r"无弹窗|笔趣阁|顶点小说|请收藏本站|加入书签|投推荐票|求推荐票|求收藏|"
    r"关注微信公众号?|微信公众号|扫码关注|百度搜索|谷歌搜索|手机用户请|电脑用户请|"
    r"更新最快|下载APP|app下载|本章未完|点击下一页|下一页继续|章节不完整|内容不完整)"
)
# 弱特征：只在「短行 + 不像正常句子」时才算广告，避免误伤正文
_SOFT_AD_WORDS = ("网址", "域名", "书签", "推荐票", "收藏", "下载", "无弹窗", "手机版", "公众号", "广告", "换源", "首发")
_SENTENCE_END = ("。", "！", "？", "”", "…", '"', "』")

# 行内推广片段：连同它前面的顿号/句号一起吃掉，避免留下孤立标点
_INLINE_AD_RE = re.compile(
    r"[，,。；;、:：]?\s*(?:最新网址|最新域名|请记住本站|记住本站|本站网址|本站域名|"
    r"永久域名|防走失|手机版?阅读网址|首发地址|本书首发|请收藏本站)\s*[:：]?\s*"
)

_ZW_RE = re.compile(r"[\u200b-\u200f\ufeff]")
_SPACE_RE = re.compile(r"[ \t\u00a0\u3000]+")


# ---------------------------------------------------------------------------
# 公共层：HTML → 纯文本
# ---------------------------------------------------------------------------

def looks_like_html(text: str) -> bool:
    """粗判是否含标签。纯文本走这条路只做很轻的处理，不做额外改写。"""
    return bool(text) and bool(_TAG_RE.search(text))


def strip_html(text: str) -> str:
    """HTML 片段 → 带换行结构的纯文本。"""
    s = _DROP_BLOCK_RE.sub("", text)
    s = _COMMENT_RE.sub("", s)
    s = _BR_RE.sub("\n", s)
    s = _BLOCK_CLOSE_RE.sub("\n", s)
    s = _BLOCK_OPEN_RE.sub("\n", s)
    s = _TAG_RE.sub("", s)
    return s


def clean_basic(text: str, single_line: bool = False) -> str:
    """通用层：HTML / 实体 / 零宽 / 空白。

    - 不删任何内容，只做「卫生」处理，所以对剧本、字幕、笔记、摘录都安全；
    - `single_line=True` 用于章节名、书名这类不该有换行的字段（换行压成空格）。

    处理顺序有讲究：实体反转义放在剥标签**之后**，否则文本里被转义的 `&lt;div&gt;`
    会被当成真标签削掉；反转义之后如果又露出真标签（有的源把整段 HTML 转义后塞进
    JSON 字段），再补洗一次。
    """
    if not text:
        return ""
    s = str(text)
    if looks_like_html(s):
        s = strip_html(s)
    if "&" in s:
        s = _html.unescape(s)
        if _BR_RE.search(s) or _BLOCK_CLOSE_RE.search(s):
            s = strip_html(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _ZW_RE.sub("", s)

    lines: list[str] = []
    for raw in s.split("\n"):
        line = _SPACE_RE.sub(" ", raw).strip()
        lines.append(line)

    if single_line:
        return " ".join(x for x in lines if x).strip()

    # 折叠连续空行：最多保留一个，用来说明「这里本来分段了」
    out: list[str] = []
    for line in lines:
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 网文层：去网址与站点推广语（只有采集来的正文才用）
# ---------------------------------------------------------------------------

def _strip_urls(line: str) -> str:
    out = _URL_RE.sub("", line)
    out = _DOMAIN_RE.sub("", out)
    return out


def _is_ad_line(line: str) -> bool:
    if not line:
        return False
    if _HARD_AD_RE.search(line) and len(line) <= 40:
        return True
    if len(line) <= 20 and not line.endswith(_SENTENCE_END) and any(w in line for w in _SOFT_AD_WORDS):
        return True
    return False


def clean_ads(text: str, single_line: bool = False) -> str:
    """网文层：在 `clean_basic` 之后去掉站点推广语与裸露 URL。

    只删「明确在说站点」的行或行内片段；删完 URL 后残留的多余空格会再压一次，
    去完只剩标点的行直接丢掉。
    """
    if not text:
        return ""
    s = clean_basic(text)
    lines: list[str] = []
    for raw in s.split("\n"):
        line = _SPACE_RE.sub(" ", raw).strip()
        before = line
        line = _strip_urls(line)
        line = _INLINE_AD_RE.sub("", line)
        # 去掉网址后常留下多余空格（"他把  记下来了"），再压一次
        line = _SPACE_RE.sub(" ", line).strip()
        # 整行本来有内容、删完之后空了 → 整行丢弃，不留空行（不是「本来分段」）
        if before and not line:
            continue
        # 去完推广后可能只剩标点，这种行没有保留价值
        if line and not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", line):
            continue
        if _is_ad_line(line):
            continue
        lines.append(line)
    if single_line:
        return " ".join(x for x in lines if x).strip()
    out: list[str] = []
    for line in lines:
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def clean_text(text: str, *, single_line: bool = False, strip_ads: bool = False) -> str:
    """统一入口。

    - `strip_ads=False`（默认）：只做通用卫生。**外来文本导入 GB 时用这个。**
    - `strip_ads=True`：额外去掉网址与站点推广语。**只有采集来的网文正文用这个。**

    默认关闭去广告是刻意的：GB 的处理对象是剧本与提示词，网文站特征词表对它们
    有害无益；真有采集需求时由调用方显式打开。
    """
    if strip_ads:
        return clean_ads(text, single_line=single_line)
    return clean_basic(text, single_line=single_line)


def clean_lines(items: Iterable, *, single_line: bool = True, strip_ads: bool = False) -> list[str]:
    """批量版，给列表字段（书名 / 作者 / 章节名）用。"""
    return [clean_text(str(x or ""), single_line=single_line, strip_ads=strip_ads) for x in items]
