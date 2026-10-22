# -*- coding: utf-8 -*-
"""FTS5 搜索，trigram 不可用时退回 LIKE。

trigram 分词器对 >=3 字符的子串最有效；中文两字查询（验收要求）走 LIKE，
功能不缺失，只是慢一点。
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

from sqlalchemy import bindparam, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from modules.assets.models import AssetMeta, IndexAsset

_fts_ready: dict[str, bool] = {}


def _engine_key(engine: Engine) -> str:
    return str(engine.url)


def ensure_fts(engine: Engine) -> bool:
    """幂等建 FTS 虚拟表；trigram 不支持时不建，返回 False。"""
    key = _engine_key(engine)
    if key in _fts_ready:
        return _fts_ready[key]
    ready = False
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql(
                "CREATE VIRTUAL TABLE IF NOT EXISTS asset_fts USING fts5("
                "asset_id UNINDEXED, name, subject, tags, aliases, tokenize='trigram')"
            )
            conn.commit()
        ready = True
    except Exception as exc:  # noqa: BLE001
        print(f"[search] FTS5 trigram 不可用，退回 LIKE：{exc}")
    _fts_ready[key] = ready
    return ready


def fts_ready(session: Session) -> bool:
    engine = session.get_bind()
    if not isinstance(engine, Engine):  # 极端情况下拿到 Connection
        engine = engine.engine  # type: ignore[attr-defined]
    return ensure_fts(engine)


def _join_tags(raw: Optional[str]) -> str:
    if not raw:
        return ""
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return " ".join(str(x) for x in data if x)
    except Exception:
        pass
    return str(raw)


REBUILD_DELETE_BATCH = 200


def _clear_fts(session: Session) -> None:
    """分批清空。

    某些 SQLite 构建在 WAL 下对 trigram 表做整表 DELETE 会抛
    ``unable to open database file``（行数够多时稳定复现），分批删除可绕开。
    """
    ids = [
        r[0]
        for r in session.execute(text("SELECT asset_id FROM asset_fts")).all()
    ]
    for start in range(0, len(ids), REBUILD_DELETE_BATCH):
        chunk = ids[start : start + REBUILD_DELETE_BATCH]
        session.execute(
            text("DELETE FROM asset_fts WHERE asset_id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": chunk},
        )


def _mark_unavailable(session: Session) -> None:
    bind = session.get_bind()
    engine = bind if isinstance(bind, Engine) else getattr(bind, "engine", None)
    if engine is not None:
        _fts_ready[_engine_key(engine)] = False


def rebuild_fts(session: Session) -> None:
    """整表重建。重扫结束时调用一次，避免逐条同步的开销。

    FTS 只是检索加速；出错时标记不可用退回 LIKE，不让整次扫描失败。
    """
    if not fts_ready(session):
        return
    try:
        _clear_fts(session)
        rows = session.execute(_select_fts_source()).all()
        if rows:
            session.execute(
                text(
                    "INSERT INTO asset_fts(asset_id, name, subject, tags, aliases) "
                    "VALUES (:asset_id, :name, :subject, :tags, :aliases)"
                ),
                [
                    {
                        "asset_id": r[0],
                        "name": r[1] or "",
                        "subject": r[5] or r[2] or "",
                        "tags": _join_tags(r[3]),
                        "aliases": _join_tags(r[4]),
                    }
                    for r in rows
                ],
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[search] FTS 重建失败，退回 LIKE：{exc}")
        _mark_unavailable(session)


def sync_fts(session: Session, asset_id: str) -> None:
    if not fts_ready(session):
        return
    session.execute(
        text("DELETE FROM asset_fts WHERE asset_id = :a"), {"a": asset_id}
    )
    row = session.execute(
        _select_fts_source().where(IndexAsset.id == asset_id)
    ).first()
    if row is None:
        return
    session.execute(
        text(
            "INSERT INTO asset_fts(asset_id, name, subject, tags, aliases) "
            "VALUES (:asset_id, :name, :subject, :tags, :aliases)"
        ),
        {
            "asset_id": row[0],
            "name": row[1] or "",
            "subject": row[5] or row[2] or "",
            "tags": _join_tags(row[3]),
            "aliases": _join_tags(row[4]),
        },
    )


def _select_fts_source():
    return (
        select(
            IndexAsset.id,
            IndexAsset.name,
            IndexAsset.subject,
            AssetMeta.tags_json,
            AssetMeta.aliases_json,
            AssetMeta.subject_override,
        )
        .select_from(
            IndexAsset.__table__.outerjoin(
                AssetMeta.__table__, AssetMeta.asset_id == IndexAsset.id
            )
        )
    )


def _like_ids(session: Session, q: str, limit: int) -> list[str]:
    needle = f"%{q}%"
    rows = session.execute(
        text(
            "SELECT a.id FROM index_assets a "
            "LEFT JOIN asset_meta m ON m.asset_id = a.id "
            "WHERE a.path LIKE :q OR a.name LIKE :q OR a.subject LIKE :q "
            "   OR COALESCE(m.tags_json,'') LIKE :q OR COALESCE(m.aliases_json,'') LIKE :q "
            "LIMIT :n"
        ),
        {"q": needle, "n": limit},
    ).all()
    return [r[0] for r in rows]


def search_ids(session: Session, q: str, limit: int = 5000) -> list[str]:
    q = (q or "").strip()
    if not q:
        return []
    if len(q) >= 3 and fts_ready(session):
        match = '"%s"' % q.replace('"', '""')
        try:
            rows = session.execute(
                text(
                    "SELECT asset_id FROM asset_fts WHERE asset_fts MATCH :m LIMIT :n"
                ),
                {"m": match, "n": limit},
            ).all()
            return [r[0] for r in rows]
        except Exception as exc:  # noqa: BLE001
            print(f"[search] FTS 查询失败，退回 LIKE：{exc}")
    return _like_ids(session, q, limit)


def drop_fts_cache() -> None:
    _fts_ready.clear()


def iter_all_ids(session: Session) -> Iterable[str]:
    return (r[0] for r in session.execute(text("SELECT asset_id FROM asset_fts")).all())
