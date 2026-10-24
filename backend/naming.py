# -*- coding: utf-8 -*-
"""命名语法：镜头类 / 素材类 的唯一解析与组合入口。

## 语法（语法硬编码，词表可配）

分隔符恒为 ``_``；段/块复合键内部用 ``.``。

    镜头类（属于某一段的产物）   01.1_成片.mp4
    素材类（全局复用的锚）       白兴学_国漫3D_四视图.png
                                教室_破坏_参考.png
                                白兴学_参考_02.png        ← 末位两位数是重名序号

判定规则：``split("_")`` 之后首段形如 ``NN`` 或 ``NN.N`` → 镜头类，否则素材类。
这条判据不依赖字段个数，永不受后续加字段影响。

## 为什么段/块用点

``_`` 只能有一个含义：字段分隔符。若写成 ``01_1_SC01``，``split("_")``
之后无法区分「段/块层级」与「字段边界」，只能靠位置猜——一旦某个文件少一个
字段（或有人手改），整条会**静默错位**：片子被归到错的段里，而且不报警。
用点之后 ``split("_")`` 即得字段，无需任何启发式。

## 为什么没有镜号槽

一块（1of3）= 一镜 = 一次生成，``SC01`` 恒等于 1。恒常字段不是信息，是噪声；
更糟的是它会诱导手改（``SC02`` / ``SC1`` / ``sc01``），解析一旦认错就归错镜头。

## 为什么镜头类不带主体名

一条提示词里常有多个角色（``<Subject 1>`` ``<Subject 2>``），文件名只放得下
一个，群戏必然选错。镜头的位置（段.块）已经唯一确定它，主体进库、进 UI。

## 为什么范围不进文件名

适用区间（这素材从第几段到第几段有效）是**后验、可随时改**的；而提示词里
``<Picture 1>`` 直接写素材文件名，这是死约束。把区间写进名字 → 一改区间就得
改名 → 所有引用它的段提示词同时失效，且失效静默（只表现为跑片时参考图缺失）。
区间只存索引。名字回答「是什么」，索引回答「什么时候能用」。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

SEP = "_"
CHUNK_SEP = "."

# 用途词表：**可在 config 里加值**，但语法（分隔符、槽位顺序、复合键）不可配。
# 加一个词不会破坏任何历史文件的解析，这才是「可配」该有的边界。
USE_SHOT: tuple[str, ...] = ("成片", "参考", "分镜", "配音")
USE_ASSET: tuple[str, ...] = ("参考", "配音", "分镜")
ALL_USES: tuple[str, ...] = tuple(dict.fromkeys(USE_SHOT + USE_ASSET))

# `01` / `01.1`
_SHOT_KEY_RE = re.compile(r"^(\d{1,3})(?:\.(\d{1,2}))?$")
# 字段切分：`_` 或空白。空白也算分隔符是**为兼容存量** —— 见 _fields 的注释。
_FIELD_SPLIT_RE = re.compile(r"[_\s]+")
# 重名序号：末位 2~3 位纯数字（`_02`）
_SEQ_RE = re.compile(r"^\d{2,3}$")
# ComfyUI 落盘名里的块号：`1of3`
_N_OF_M_RE = re.compile(r"(?<!\d)(\d{1,2})of(\d{1,2})(?!\d)", re.IGNORECASE)
_SEG_IN_NAME_RE = re.compile(r"第\s*(\d{1,3})\s*段")


# ---------------------------------------------------------------------------
# 值对象
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShotName:
    """镜头类：``01.1_成片.mp4``。"""

    seg: str                              # 两位段号，'01'
    chunk: Optional[int] = None           # 1 基块号；None = 整段一气生成
    use: Optional[str] = None             # 成片 / 参考 / 分镜 / 配音
    seq: Optional[int] = None             # 重名序号

    @property
    def key(self) -> str:
        """定位键：``01`` 或 ``01.1``。"""
        return f"{self.seg}{CHUNK_SEP}{self.chunk}" if self.chunk else self.seg


@dataclass(frozen=True)
class AssetName:
    """素材类：``白兴学_国漫3D_四视图.png``。"""

    subject: str                          # 主体名（第一段）
    variant: Optional[str] = None         # 自由描述，可含 `_`（内部原样）
    use: Optional[str] = None             # 参考 / 配音 / 分镜
    seq: Optional[int] = None


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def stem_of(name: str | Path) -> str:
    """取文件名去扩展名；传进来带目录也行。"""
    return Path(str(name)).stem


def seg2(value: object) -> Optional[str]:
    """段号归一成两位：``1`` → ``'01'``，``17`` → ``'17'``，``104`` → ``'104'``。

    与 ``repo.seg_from_filename`` 保持同一口径（<100 补零），否则同一段会出现
    ``'01'`` 与 ``'1'`` 两种键，卡片对不上。
    """
    if value is None:
        return None
    m = re.search(r"\d{1,3}", str(value))
    if not m:
        return None
    n = int(m.group(0))
    return f"{n:02d}" if n < 100 else str(n)


def _clean_part(part: str) -> str:
    """单段字段的卫生：去空白、把分隔符换成连字符。

    🔴 主体/变体里绝不能出现 ``_``：那是字段分隔符，混进去等于造了一个解析歧义。
    同理空格也要换掉——现有 ``刘强 Q版.png`` 靠「按 `_`/空白切」判断主体，
    空格会让规则变成不确定的。
    """
    s = str(part or "").strip()
    s = re.sub(r"\s+", "-", s)
    return s.replace(SEP, "-")


def _fields(stem: str) -> list[str]:
    """字段切分：``_`` 或空白。

    🔴 空白也算分隔符是**为兼容存量**：真实素材目录里有 ``刘强 Q版.png``、
    ``同学甲 Q版.png`` 这类名字，旧的「按 `_`/空白切、第一段=主体」正是这么分的。
    这里改成只认 ``_``，会让这 5 个文件在改造后归到别的主体名下 —— 而且不报警。
    组装方向相反：``compose_*`` 只输出 ``_``，并把字段内的空白换成 ``-``，
    于是新文件不再产生空格，存量照旧能解析。
    """
    return [f for f in _FIELD_SPLIT_RE.split(stem) if f]


def _split_seq(fields: list[str]) -> Optional[int]:
    """从末位摘掉重名序号（原地修改），返回序号。"""
    if len(fields) > 1 and _SEQ_RE.match(fields[-1]):
        return int(fields.pop())
    return None


# ---------------------------------------------------------------------------
# 镜头类
# ---------------------------------------------------------------------------

def parse_shot(name: str | Path) -> Optional[ShotName]:
    """按**模板**解析镜头类；不符合模板一律返回 None（不做部分猜测）。

    ``01.1_成片.mp4``        → seg=01 chunk=1 use=成片
    ``17_成片_02.mp4``       → seg=17 chunk=None use=成片 seq=2
    ``17_第17段_1of3_00001.mp4`` → None（这是 ComfyUI 落盘名，走 chunk_from_filename）
    """
    fields = _fields(stem_of(name))
    if not fields:
        return None
    m = _SHOT_KEY_RE.match(fields[0])
    if not m:
        return None
    seg = seg2(m.group(1))
    chunk = int(m.group(2)) if m.group(2) else None
    if seg is None:
        return None

    rest = fields[1:]
    seq = _split_seq(rest)
    use = None
    if rest and rest[-1] in USE_SHOT:
        use = rest.pop()
    # 还有剩 = 不是我们的模板（例如 legacy 落盘名），绝不硬解释
    if rest:
        return None
    return ShotName(seg=seg, chunk=chunk, use=use, seq=seq)


def compose_shot(
    seg: object,
    chunk: Optional[int] = None,
    use: Optional[str] = "成片",
    ext: str = ".mp4",
    seq: Optional[int] = None,
) -> str:
    """组装镜头类文件名。段号归一、扩展名带点。"""
    s = seg2(seg)
    if s is None:
        raise ValueError(f"段号不可用：{seg!r}")
    parts = [f"{s}{CHUNK_SEP}{int(chunk)}" if chunk else s]
    if use:
        parts.append(_clean_part(use))
    if seq:
        parts.append(f"{int(seq):02d}")
    return SEP.join(parts) + _ext(ext)


# ---------------------------------------------------------------------------
# 素材类
# ---------------------------------------------------------------------------

def parse_asset(name: str | Path) -> AssetName:
    """解析素材类。**永不返回 None**——认不出就整串当主体名（宁可归错组，不可丢文件）。

    ``白兴学_国漫3D_四视图.png`` → subject=白兴学 variant=国漫3D_四视图 use=None
    ``教室_破坏_参考.png``       → subject=教室   variant=破坏        use=参考
    ``刘强 Q版.png``             → subject=刘强-Q版
    """
    fields = _fields(stem_of(name))
    seq = _split_seq(fields)
    use = None
    if len(fields) > 1 and fields[-1] in USE_ASSET:
        use = fields.pop()
    subject = _clean_part(fields[0]) if fields else ""
    variant = SEP.join(fields[1:]) or None
    return AssetName(subject=subject, variant=variant, use=use, seq=seq)


def compose_asset(
    subject: str,
    variant: Optional[str] = None,
    use: Optional[str] = "参考",
    ext: str = ".png",
    seq: Optional[int] = None,
) -> str:
    """组装素材类文件名。变体内部的 ``_`` 保留（它就是自由描述）。"""
    sub = _clean_part(subject)
    if not sub:
        raise ValueError("主体名不能为空")
    parts = [sub]
    if variant:
        parts.append(str(variant).strip())
    if use:
        parts.append(_clean_part(use))
    if seq:
        parts.append(f"{int(seq):02d}")
    return SEP.join(parts) + _ext(ext)


# ---------------------------------------------------------------------------
# 分类
# ---------------------------------------------------------------------------

def classify(name: str | Path) -> str:
    """``'shot'`` / ``'asset'`` / ``'unknown'``。

    unknown = 首段像段号（纯数字）但整体不符合模板 —— 例如 ComfyUI 的落盘名
    ``17_第17段_1of3_00001.mp4``。这类文件**归不了类，但也不该被当成素材**：
    当成素材会让 `17` 变成一个主体名的开头。
    """
    if parse_shot(name) is not None:
        return "shot"
    fields = _fields(stem_of(name))
    if fields and _SHOT_KEY_RE.match(fields[0]):
        return "unknown"
    return "asset"


# ---------------------------------------------------------------------------
# 块号回退（「通过」时用）
# ---------------------------------------------------------------------------

def chunk_from_filename(name: str | Path) -> Optional[int]:
    """从落盘名读块号：``17_第17段_1of3_00001.mp4`` → 1。

    比读 ``cards.chunk`` 可靠：文件名是物理事实，而那一列历史数据大多是 null。
    """
    m = _N_OF_M_RE.search(stem_of(name))
    return int(m.group(1)) if m else None


def chunk_from_card(value: object) -> Optional[int]:
    """从卡片 ``chunk`` 字段读块号：``'1of3'`` / ``'2'`` / ``2`` → 1 / 2 / 2。"""
    if value is None:
        return None
    m = _N_OF_M_RE.search(str(value))
    if m:
        return int(m.group(1))
    m = re.search(r"\d{1,2}", str(value))
    return int(m.group(0)) if m else None


def resolve_chunk(filename: str | Path, card_chunk: object = None) -> tuple[Optional[int], str]:
    """「通过」时取块号，三级回退。返回 ``(chunk, source)``。

    ``source`` ∈ ``'name'``（模板名里已带） / ``'filename'``（``NofM``） /
    ``'card'`` / ``'none'``。

    🔴 拿不到就返回 ``(None, 'none')``，由调用方只写段号并标「缺块号」。
    绝不猜——猜错的代价是把片子归到别一段，而画面照样出得来，没人会发现。
    """
    shot = parse_shot(filename)
    if shot is not None and shot.chunk:
        return shot.chunk, "name"
    got = chunk_from_filename(filename)
    if got:
        return got, "filename"
    got = chunk_from_card(card_chunk)
    if got:
        return got, "card"
    return None, "none"


def seg_from_path(name: str | Path) -> Optional[str]:
    """段号：先认模板名首段，再认 ``第N段``。与 ``repo.seg_from_filename`` 口径一致。"""
    stem = stem_of(name)
    fields = _fields(stem)
    if fields and _SHOT_KEY_RE.match(fields[0]):
        return seg2(fields[0].split(CHUNK_SEP)[0])
    m = _SEG_IN_NAME_RE.search(stem)
    return seg2(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# 适用区间（索引层，不在文件名里）
# ---------------------------------------------------------------------------

def pick_for_seg(
    items: Iterable[dict],
    seg: object,
    *,
    key_from: str = "seg_from",
    key_to: str = "seg_to",
    key_at: str = "created_at",
) -> Optional[dict]:
    """给定段号，在候选区间里挑出该用哪一条。``seg_to`` 为 None = 一直有效。

    规则（顺序即优先级）：

    1. 只保留覆盖该段的（``seg_from <= seg`` 且 ``seg_to`` 为 None 或 ``>= seg``）；
    2. 取 ``seg_from`` 最大的 —— 「最近生效的那版」；
    3. 起点相同时取终点更近的 —— 更精确的排在前，``None``（∞）算最宽；
    4. 仍并列取 ``created_at`` 更晚的（同起点同终点时，后导入的胜）。
    """
    target = seg2(seg)
    if target is None:
        return None
    n = int(target)
    cands: list[tuple[tuple, dict]] = []
    for it in items:
        frm = seg2(it.get(key_from))
        if frm is None or int(frm) > n:
            continue
        to = it.get(key_to)
        to_n = int(seg2(to)) if to not in (None, "") else None
        if to_n is not None and to_n < n:
            continue
        rank = (int(frm), -(to_n if to_n is not None else 10 ** 6), str(it.get(key_at) or ""))
        cands.append((rank, it))
    if not cands:
        return None
    return max(cands, key=lambda kv: kv[0])[1]


def succession_to(new_from: object) -> Optional[int]:
    """新状态从 ``new_from`` 生效时，上一条的终点应该收到哪。返回 ``new_from - 1``。

    这只是**建议值**：自动接替是为了省事，用户可以随手改回去，也能给同一条素材
    再开第二个区间（修好了又坏了）。区间是一对多，不是一条素材一个区间。
    """
    s = seg2(new_from)
    return int(s) - 1 if s is not None else None


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _ext(ext: str) -> str:
    """归一扩展名：``.MP4`` / ``mp4`` / ``a.mp4`` 都认，输出带点的低位形式。

    认整文件名是为了 ``guess_kind("白兴学_参考.PNG")`` 这种用法——
    调用方手里常常是一个文件名字符串，不是单独的扩展名。
    """
    s = str(ext or "").strip().lower()
    if not s:
        return ""
    suf = Path(s).suffix
    if suf and suf != s:
        return suf
    return s if s.startswith(".") else "." + s


def guess_kind(ext: str) -> str:
    """媒体大类：``video`` / ``image`` / ``audio`` / ``other``。扩展名已经表达类型，
    所以类型不进文件名。"""
    e = _ext(ext)
    if e in (".mp4", ".webm", ".mkv", ".mov"):
        return "video"
    if e in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"):
        return "image"
    if e in (".mp3", ".wav", ".flac", ".m4a", ".ogg"):
        return "audio"
    return "other"


def unique(fields: Sequence[str]) -> list[str]:
    """保序去重（词表合并时用）。"""
    seen: set[str] = set()
    out: list[str] = []
    for f in fields:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out
