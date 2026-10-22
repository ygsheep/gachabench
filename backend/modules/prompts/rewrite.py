# -*- coding: utf-8 -*-
"""提示词正文的区间替换器。

只做一件事：把命中文件名的那一小段字符替换掉，并在写盘前用反向还原校验
证明「除了这些区间之外，原文一个字节都没动」。
"""
from __future__ import annotations

from typing import Iterable, Sequence

Span = tuple[int, int, str]


class RewriteError(ValueError):
    """区间非法或反向还原校验失败。"""


def _normalize(spans: Iterable[Span], text_len: int) -> list[tuple[int, int, str]]:
    normalized: list[tuple[int, int, str]] = []
    for raw in spans:
        if not isinstance(raw, (tuple, list)) or len(raw) != 3:
            raise RewriteError("span 必须是 (start, end, replacement)")
        start, end, replacement = raw
        if not isinstance(start, int) or not isinstance(end, int):
            raise RewriteError("span 偏移必须是整数")
        if not isinstance(replacement, str):
            raise RewriteError("span 替换值必须是字符串")
        if start < 0 or end < start or end > text_len:
            raise RewriteError(f"span 越界：({start}, {end})，文本长度 {text_len}")
        normalized.append((start, end, replacement))

    normalized.sort(key=lambda s: (s[0], s[1]))
    prev_end = -1
    for start, end, _ in normalized:
        if start < prev_end:
            raise RewriteError("span 之间发生重叠")
        prev_end = end
    return normalized


def _restore(text: str, records: Sequence[dict]) -> str:
    """按记录把改写后的文本反向还原成原文。

    记录按原文偏移升序；改写后文本里的位置 = 原偏移 + 之前所有替换的长度差。
    """
    pieces: list[str] = []
    cursor = 0
    delta = 0
    for record in records:
        target = record["start"] + delta
        after = record["after"]
        before = record["before"]
        if text[target : target + len(after)] != after:
            raise RewriteError("反向还原校验失败")
        pieces.append(text[cursor:target])
        pieces.append(before)
        cursor = target + len(after)
        delta += len(after) - len(before)
    pieces.append(text[cursor:])
    return "".join(pieces)


def apply_spans(text: str, spans: Iterable[Span]) -> tuple[str, list[dict]]:
    """按偏移倒序替换，返回 (新文本, 替换记录)。

    记录含 start/end/before/after；写盘前必须用记录反向还原，结果与原文
    逐字符相等，否则抛 `RewriteError`，调用方不得落盘。
    """
    if not isinstance(text, str):
        raise RewriteError("text 必须是字符串")
    normalized = _normalize(spans, len(text))
    if not normalized:
        return text, []

    records: list[dict] = []
    for start, end, replacement in normalized:
        records.append(
            {
                "start": start,
                "end": end,
                "before": text[start:end],
                "after": replacement,
            }
        )

    new_text = text
    for start, end, replacement in reversed(normalized):
        new_text = new_text[:start] + replacement + new_text[end:]

    if _restore(new_text, records) != text:
        raise RewriteError("反向还原校验失败，拒绝写入")
    return new_text, records
