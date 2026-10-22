# -*- coding: utf-8 -*-
"""补丁：把「能机械修的问题」攒成一组字符区间，预览后一次性应用。

刻意不做整段重写：整段重写没法让人看出改了什么，也容易顺手改掉台词。
这里只允许两种动作 —— 删一小段（正文里的时长）、换一小段（文件名 → 占位）、
在行尾补一小段（独白的无口型标注）。

应用沿用 `rewrite.apply_spans`：倒序替换 + 反向还原校验，
证明「除了这些区间，原文一个字节都没动」。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from modules.prompts.rewrite import RewriteError, apply_spans


def _context(text: str, start: int, end: int, pad: int = 36) -> str:
    left = max(0, start - pad)
    right = min(len(text), end + pad)
    head = "…" if left > 0 else ""
    tail = "…" if right < len(text) else ""
    return head + text[left:start] + "[[" + text[start:end] + "]]" + text[end:right] + tail


def build_patch(
    text: str,
    findings: Iterable[dict[str, Any]],
    codes: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """从诊断结论生成补丁。返回 hunks、应用后的全文、以及没被采纳的项。"""
    wanted = set(codes or [])
    picked: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for f in findings:
        if not f.get("fixable") or not f.get("fix"):
            continue
        if wanted and f["code"] not in wanted:
            continue
        fix = f["fix"]
        start, end = int(fix["start"]), int(fix["end"])
        if start < 0 or end < start or end > len(text):
            skipped.append({**f, "skip_reason": "区间越界，文本可能已经被改过"})
            continue
        # 区间重叠：同一个位置只改一次，后面的按序跳过
        if any(not (end <= h["start"] or start >= h["end"]) for h in picked):
            skipped.append({**f, "skip_reason": "与已选中的改动重叠"})
            continue
        picked.append({
            "code": f["code"],
            "title": f["title"],
            "reason": f["detail"],
            "shot": f.get("shot") or 0,
            "line": f.get("line") or 0,
            "start": start,
            "end": end,
            "before": text[start:end],
            "after": fix["after"],
            "context": _context(text, start, end),
        })

    picked.sort(key=lambda h: h["start"])

    patched = text
    records: list[dict[str, Any]] = []
    if picked:
        try:
            patched, records = apply_spans(
                text, [(h["start"], h["end"], h["after"]) for h in picked]
            )
        except RewriteError as exc:
            # 校验没过就一个都不改：宁可让人手工来，也不能写出一份不可复原的文本
            return {
                "hunks": [],
                "patched": text,
                "applied": 0,
                "skipped": [*skipped, {"code": "REWRITE_FAILED", "title": "补丁校验失败", "detail": str(exc)}],
            }

    return {
        "hunks": picked,
        "patched": patched,
        "applied": len(picked),
        "skipped": skipped,
    }
