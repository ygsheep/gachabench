# -*- coding: utf-8 -*-
"""素材的适用区间（时间线）：这素材从第几段到第几段有效。

## 为什么区间不进文件名

提示词里 `<Picture N>` 直接写素材文件名，这是死约束。区间若写进名字，
「改终点」就得改名 → 所有引用它的段提示词同时失效，而且**失效是静默的**
（跑片时只表现为参考图缺失）。名字回答「是什么」，本模块回答「什么时候能用」。

## 模型

一个资产拥有 **0..N 对 (seg_from, seg_to)**：

- `seg_to` 为 ``None`` = 一直有效；
- 0 对 = 隐式全局有效（``seg_from=1``, ``seg_to=None``）。
  存量素材全都没有区间，一条都不用改，行为等价于「一直可用」；
- N 对 = 中间被别的版本顶替过，后来又用回来（破坏 5-9 / 修复 10-15 / 破坏 16-∞）。

## 为什么不做「自动接替」

上一轮设计里写过「新状态从第 10 段开始时，系统自动把上一条收到第 9 段」。
实现前否掉了：**「同一轨道」这件事无法从文件名推导**。

`教室_破坏` → `教室_修复` 是同一轨道的接替；但 `白兴学_校服` → `白兴学_日常`
是两条并行轨道。两者在数据上长得一模一样（同主体、不同变体名），
自动收口会把「日常服」在第 10 段就关掉 —— 而它本该一直有效。

所以：**只提建议，不自动写**（`suggest_close`）。选择正确性不依赖收口 ——
`naming.pick_for_seg` 取「`seg_from` 最大者」，第 12 段天然命中第 10 段生效的版本，
不闭环也不会选错。收口只影响 UI 上时间线好不好看，那是人一眼就能判的事。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import select

from modules.assets.models import AssetPrompt, AssetWindow, IndexAsset, now_iso
from modules.platform.ids import ulid
from naming import pick_for_seg, seg2

# 无区间资产的隐式起点：第 1 段。用 1 而不是 0，让「显式区间」永远排在它前面。
IMPLICIT_FROM = 1


# ---------------------------------------------------------------------------
# 规范化
# ---------------------------------------------------------------------------

def _one(raw: Any) -> Optional[dict]:
    """把一条区间输入规范化成 ``{"seg_from": int, "seg_to": int|None}``。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip().replace("—", "-").replace("~", "-")
        if not s:
            return None
        if "-" in s:
            a, _, b = s.partition("-")
            frm, to = seg2(a), seg2(b)
        else:
            frm, to = seg2(s), None
        if frm is None:
            return None
        return {"seg_from": int(frm), "seg_to": int(to) if to else None}
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        frm, to = seg2(raw[0]), seg2(raw[1]) if raw[1] is not None else None
        if frm is None:
            return None
        return {"seg_from": int(frm), "seg_to": int(to) if to else None}
    if isinstance(raw, dict):
        frm = seg2(raw.get("seg_from"))
        if frm is None:
            return None
        to_raw = raw.get("seg_to")
        to = seg2(to_raw) if to_raw not in (None, "") else None
        return {"seg_from": int(frm), "seg_to": int(to) if to else None}
    return None


def normalize_windows(raw: Iterable[Any] | None) -> list[dict]:
    """校验 + 排序 + 去重。**非法的整条丢弃**（不带进半截数据）。

    非法包括：起点解析不出、终点 < 起点。起点为 0 也当非法 ——
    段号从 1 开始，``0`` 通常是「没填」的误写。
    """
    out: list[dict] = []
    seen: set[tuple[int, Optional[int]]] = set()
    for item in raw or []:
        got = _one(item)
        if got is None:
            continue
        if got["seg_from"] < 1:
            continue
        if got["seg_to"] is not None and got["seg_to"] < got["seg_from"]:
            continue
        key = (got["seg_from"], got["seg_to"])
        if key in seen:
            continue
        seen.add(key)
        out.append(got)
    out.sort(key=lambda w: (w["seg_from"], w["seg_to"] if w["seg_to"] is not None else 10 ** 6))
    return out


def windows_for(session, asset_id: str) -> list[AssetWindow]:
    return list(
        session.scalars(
            select(AssetWindow)
            .where(AssetWindow.asset_id == asset_id)
            .order_by(AssetWindow.seg_from)
        ).all()
    )


def set_windows(
    session,
    asset_id: str,
    raw: Iterable[Any] | None,
    *,
    source: str = "manual",
    note: str = "",
) -> list[AssetWindow]:
    """**全量替换**某个资产的区间。传 ``[]`` = 回到隐式全局有效。

    全量替换而不是增量，是因为 UI 上它是一条可拖的时间线：人改了哪一段
    就该整体生效，不该出现「旧行还在、只多了一行」的幽灵。
    """
    wanted = normalize_windows(raw)
    existing = {w.id: w for w in windows_for(session, asset_id)}
    keep: set[str] = set()

    for w in wanted:
        hit = next(
            (
                row
                for row in existing.values()
                if row.id not in keep
                and row.seg_from == w["seg_from"]
                and row.seg_to == w["seg_to"]
            ),
            None,
        )
        if hit is not None:
            keep.add(hit.id)
            hit.source = source
            if note:
                hit.note = note
            hit.updated_at = now_iso()
            continue
        row = AssetWindow(
            id=ulid(),
            asset_id=asset_id,
            seg_from=w["seg_from"],
            seg_to=w["seg_to"],
            source=source,
            note=note,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        session.add(row)
        keep.add(row.id)

    for rid, row in existing.items():
        if rid not in keep:
            session.delete(row)
    session.flush()
    return windows_for(session, asset_id)


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------

def _candidates(session, subject: str) -> list[dict]:
    """某主体下所有可用资产的候选行（含隐式全局行），喂给 `naming.pick_for_seg`。"""
    assets = session.scalars(
        select(IndexAsset).where(
            IndexAsset.subject == subject, IndexAsset.status == "active"
        )
    ).all()
    if not assets:
        return []
    ids = [a.id for a in assets]
    rows = session.scalars(
        select(AssetWindow).where(AssetWindow.asset_id.in_(ids))
    ).all()
    by_asset: dict[str, list[AssetWindow]] = {}
    for w in rows:
        by_asset.setdefault(w.asset_id, []).append(w)

    items: list[dict] = []
    for a in assets:
        wins = by_asset.get(a.id) or []
        if not wins:
            items.append(
                {
                    "asset": a,
                    "window": None,
                    "implicit": True,
                    "seg_from": IMPLICIT_FROM,
                    "seg_to": None,
                    "created_at": "",
                }
            )
            continue
        for w in wins:
            items.append(
                {
                    "asset": a,
                    "window": w,
                    "implicit": False,
                    "seg_from": w.seg_from,
                    "seg_to": w.seg_to,
                    "created_at": w.created_at or "",
                }
            )
    return items


def resolve(session, subject: str, seg: Any) -> Optional[dict]:
    """给定主体与段号，返回该用的那一条素材（含区间与提示词）。

    返回 ``{"asset", "window", "implicit", "seg", "prompt"}``；
    该主体下没有任何素材时返回 ``None``。
    """
    items = _candidates(session, subject)
    if not items:
        return None
    won = pick_for_seg(items, seg)
    if won is None:
        return None
    prompt = session.get(AssetPrompt, won["asset"].id)
    return {
        "asset": won["asset"],
        "window": won["window"],
        "implicit": won["implicit"],
        "seg": seg2(seg),
        "prompt": prompt,
    }


def resolve_many(session, subjects: Sequence[str], seg: Any) -> dict[str, dict]:
    """批量：``{主体名: 命中条目}``，命不中的主体不出现在结果里。"""
    out: dict[str, dict] = {}
    for s in subjects:
        hit = resolve(session, s, seg)
        if hit is not None:
            out[s] = hit
    return out


# ---------------------------------------------------------------------------
# 只提建议，不落库
# ---------------------------------------------------------------------------

def suggest_close(
    session, asset_id: str, seg_from: Any, *, subject: Optional[str] = None
) -> list[dict]:
    """新版本从 ``seg_from`` 生效时，同主体下哪些**未闭环**的旧区间「可能」该收到前面。

    只返回建议，**绝不写库**。理由见模块顶部「为什么不做自动接替」：
    同主体不等于同轨道（校服 / 日常服是并行的），自动收口会误伤。
    """
    n = seg2(seg_from)
    if n is None:
        return []
    target = int(n)
    if subject is None:
        asset = session.get(IndexAsset, asset_id)
        if asset is None:
            return []
        subject = asset.subject
    picks = [
        it
        for it in _candidates(session, subject)
        if not it["implicit"]
        and it["asset"].id != asset_id
        and it["seg_to"] is None
        and int(it["seg_from"]) < target
    ]
    return [
        {
            "asset_id": it["asset"].id,
            "name": it["asset"].name,
            "suggest_seg_to": target - 1,
        }
        for it in picks
    ]
