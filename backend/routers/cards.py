# -*- coding: utf-8 -*-
"""抽卡 API：GET/PATCH cards、bulk、to-final。

「通过」（to-final）的语义 2026-10-24 改版：

- 卡片 `status = final`（库里打标，唯一真相）；
- **复制一份到 `{资产目录}/未分类/`**，文件名按命名模板现算（`01.1_成片.mp4`）；
- 原件留在草稿不动 —— 一处真相（草稿）+ 一处交付（资产副本），不再有第三份。

旧的 `生成/` 落点已废弃。`copy_to_final` 作为兼容字段保留，语义等同 `copy_to_asset`。

🔴 顺手修掉的旧缺陷：勾选复制时会把 `chunk` 覆写成字符串 `"final"`，把 `1of3`
这种块号永久丢掉。现在块号只**读**不写，卡片的 `chunk` 保持原值。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from config import get_config
from db import get_db
from naming import compose_shot, resolve_chunk, seg2, seg_from_path
from repo import (
    bulk_update_status,
    card_file_path,
    get_card,
    list_cards,
    update_card,
)

log = logging.getLogger("gachabench.cards")

router = APIRouter(prefix="/api/cards", tags=["cards"])


REJECT_TAGS = [
    "人物崩", "口型不准", "串词", "道具复读", "运镜糊", "画风漂",
    "时长不符", "手部穿模", "闪黑异常", "其他",
]


def _active_db():
    cfg = get_config()
    root = cfg.ws_root()
    if root is None:
        raise HTTPException(status_code=400, detail="未激活任何工作区")
    return cfg, get_db(root)


@router.get("")
async def cards_list(
    status: str = Query("all"),
    seg: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    workspace: Optional[str] = Query(None),
):
    cfg, db = _active_db()
    owner = workspace or cfg.active

    # 实时读盘：列卡之前先按节流看一眼 草稿/ 与 生成/。
    # 只 stat 文件指纹，没新片就一个字节都不写库；间隔由 config.watch 控制，
    # 所以「来回切页 / 重复刷新」不会变成反复扫盘。失败只是这一轮不新鲜，不该 500。
    if owner == cfg.active and cfg.watch.enabled:
        try:
            import cardwatch

            await asyncio.to_thread(
                cardwatch.ensure_fresh,
                cfg,
                owner,
                db,
                Path(cfg.ws_root()),
                min_gap=float(cfg.watch.request_gap_sec),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[cards] 刷新卡池失败（返回上次的库内容）：%s", e)

    rows = list_cards(
        db,
        {"workspace": owner, "status": status, "seg": seg, "tag": tag, "q": q},
    )
    _annotate_missing(cfg, owner, rows)
    return {"cards": rows, "reject_tags": REJECT_TAGS}


def _annotate_missing(cfg, owner: str, rows: list[dict]) -> None:
    """标出「文件已不在盘上」的卡（改名 / 移动 / 删掉）。

    复用卡池刷新刚算好的指纹，不额外 stat 一遍；拿不到指纹（还没扫过）就一律不标，
    宁可晚一轮也不给用户看假警报。
    """
    try:
        import cardwatch

        ws = next((w for w in cfg.workspaces if w.name == owner), None)
        if ws is None:
            return
        snap = cardwatch.current_snap(Path(ws.root))
        cardwatch.mark_missing(rows, snap, cardwatch.scan_folder_names(cfg, ws))
    except Exception as e:  # noqa: BLE001 —— 标记失败不该让列卡 500
        log.warning("[cards] 缺失标记失败：%s", e)


@router.get("/{card_id}")
async def cards_detail(card_id: int):
    cfg, db = _active_db()
    card = get_card(db, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="卡片不存在")
    _annotate_missing(cfg, card["workspace"], [card])
    return card


class PatchBody(BaseModel):
    status: Optional[str] = None
    reject_tags: Optional[list[str]] = None
    reject_note: Optional[str] = None
    note: Optional[str] = None
    seg: Optional[str] = None
    tags: Optional[list[str]] = None
    chunk: Optional[str] = None
    prompt_md: Optional[str] = None


@router.patch("/{card_id}")
async def cards_patch(card_id: int, body: PatchBody):
    cfg, db = _active_db()
    card = update_card(db, card_id, body.model_dump(exclude_none=True))
    if card is None:
        raise HTTPException(status_code=404, detail="卡片不存在")
    return card


class BulkBody(BaseModel):
    ids: list[int]
    status: str


@router.post("/bulk")
async def cards_bulk(body: BulkBody):
    cfg, db = _active_db()
    n = bulk_update_status(db, body.ids, body.status)
    return {"updated": n}


class ToFinalBody(BaseModel):
    """两个字段同义；谁先出现用谁。都不给 = **默认复制**。

    默认复制是有意的：点「通过」的结果必须是「产出了一份能用的东西」，
    而不是「库里多了个标记、文件还在草稿堆里自己找」。
    """

    copy_to_asset: Optional[bool] = None
    copy_to_final: Optional[bool] = None

    @property
    def should_copy(self) -> bool:
        for value in (self.copy_to_asset, self.copy_to_final):
            if value is not None:
                return bool(value)
        return True


def _deliver_to_uncategorized(cfg, card: dict, src: Path) -> dict:
    """把通过的那一份复制进 `{资产目录}/未分类/`，并登记成资产。

    同步阻塞（复制 + sha256），调用方负责扔进线程。返回
    ``{"path", "name", "asset_id", "chunk_source", "problems"}``。

    🔴 段号或文件名算不出来就**报错**，不猜：猜错会把片子归到别一段，
    而画面照样出得来，没人会发现。
    """
    from modules.assets.roles import UNCATEGORIZED_DIR

    root = cfg.ws_root()
    asset_dir = cfg.subdir("asset")
    if root is None or asset_dir is None:
        raise HTTPException(status_code=400, detail="未激活工作区，或资产目录未配置")

    name = Path(card["rel_path"]).name
    seg = seg_from_path(name) or seg2(card.get("seg"))
    if not seg:
        raise HTTPException(
            status_code=409,
            detail=f"这条片子没有段号，无法按模板命名：{name}。请先在抽卡台给它指定段号。",
        )
    chunk, chunk_source = resolve_chunk(name, card.get("chunk"))
    ext = src.suffix or ".mp4"

    dst_dir = asset_dir / UNCATEGORIZED_DIR
    dst_dir.mkdir(parents=True, exist_ok=True)

    dst = dst_dir / compose_shot(seg, chunk, "成片", ext)
    if dst.exists() and dst.resolve() != src.resolve():
        for seq in range(2, 100):
            cand = dst_dir / compose_shot(seg, chunk, "成片", ext, seq=seq)
            if not cand.exists():
                dst = cand
                break
        else:
            raise HTTPException(
                status_code=409, detail=f"{dst.name} 的重名副本太多（已到 _99），请先清理"
            )

    if dst.resolve() != src.resolve():
        shutil.copy2(src, dst)

    problems: list[str] = []
    asset_id: Optional[str] = None
    try:
        from modules.assets.indexer import index_single

        asset_id = index_single(root, dst.relative_to(root).as_posix())
    except Exception as exc:  # noqa: BLE001 —— 复制成功但登记失败，不能把已交付的文件藏起来
        log.warning("[to-final] 资产登记失败：%s", exc)
        problems.append(f"资产登记失败：{exc}")

    return {
        "path": str(dst),
        "name": dst.name,
        "asset_id": asset_id,
        "chunk_source": chunk_source,
        "problems": problems,
    }


@router.post("/{card_id}/to-final")
async def cards_to_final(card_id: int, body: ToFinalBody):
    cfg, db = _active_db()
    card = get_card(db, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="卡片不存在")

    src = card_file_path(cfg, card)
    if src is None or not src.exists():
        raise HTTPException(status_code=404, detail=f"卡片视频不存在：{card['rel_path']}")

    delivered = None
    if body.should_copy:
        # 复制 + sha256 会占几十毫秒到几百毫秒，别卡住事件循环（健康检查与任务轮询都在这上面）
        delivered = await asyncio.to_thread(_deliver_to_uncategorized, cfg, card, src)

    update_card(db, card_id, {"status": "final"})

    return {
        "card": get_card(db, card_id),
        "copied_to": delivered["path"] if delivered else None,
        "asset_id": delivered["asset_id"] if delivered else None,
        "chunk_source": delivered["chunk_source"] if delivered else None,
        "missing_chunk": bool(delivered and delivered["chunk_source"] == "none"),
        "problems": delivered["problems"] if delivered else [],
    }