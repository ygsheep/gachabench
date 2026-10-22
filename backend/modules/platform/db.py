# -*- coding: utf-8 -*-
"""工作区级 SQLAlchemy 引擎与 schema 升级。

库位置沿用旧约定：`<工作区>/.gachabench/cards.db`。
- 按工作区根缓存 engine；
- 连接建立时设 WAL / 外键 / busy_timeout；
- 正常安装用 Alembic 升级；冻结态缺 migrations 时退回 create_all + 原生 DDL。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engines: dict[str, Engine] = {}
_factories: dict[str, sessionmaker] = {}
_ensured: set[str] = set()
_guard = threading.RLock()


def db_path(ws_root: Path) -> Path:
    return Path(ws_root).resolve() / ".gachabench" / "cards.db"


def _sqlite_url(ws_root: Path) -> str:
    # SQLite URL 用 POSIX 斜杠；Windows 盘符形如 sqlite:///D:/x/y.db
    return "sqlite:///" + db_path(ws_root).as_posix()


def get_engine(ws_root: Path) -> Engine:
    root = Path(ws_root).resolve()
    key = str(root)
    with _guard:
        engine = _engines.get(key)
        if engine is not None:
            return engine

        db_path(root).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            _sqlite_url(root),
            future=True,
            connect_args={"check_same_thread": False, "timeout": 10},
        )

        @event.listens_for(engine, "connect")
        def _on_connect(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            try:
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=5000")
            finally:
                cur.close()

        _engines[key] = engine
        return engine


def get_sessionmaker(ws_root: Path) -> sessionmaker:
    root = Path(ws_root).resolve()
    key = str(root)
    with _guard:
        factory = _factories.get(key)
        if factory is None:
            factory = sessionmaker(
                bind=get_engine(root), expire_on_commit=False, future=True
            )
            _factories[key] = factory
        return factory


@contextmanager
def session_scope(ws_root: Path) -> Iterator[Session]:
    """事务边界：正常提交，异常回滚，最后关闭。"""
    session = get_sessionmaker(ws_root)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _schema_version(engine: Engine) -> Optional[str]:
    try:
        with engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).first()
            return str(row[0]) if row else None
    except Exception:
        return None


def _has_table(engine: Engine, name: str) -> bool:
    try:
        with engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).first()
            return row is not None
    except Exception:
        return False


def ensure_schema(ws_root: Path) -> bool:
    """升级到最新 schema。返回 True 表示本次确实建过表或升过版本。"""
    root = Path(ws_root).resolve()
    engine = get_engine(root)
    before_version = _schema_version(engine)
    had_index = _has_table(engine, "index_assets")

    # Alembic：源码运行的标准路径。
    # parents[2] = backend/（modules/platform/db.py -> modules/platform -> modules -> backend）
    backend_dir = Path(__file__).resolve().parents[2]
    ini = backend_dir / "alembic.ini"
    versions = backend_dir / "migrations" / "versions"
    if ini.is_file() and versions.is_dir():
        try:
            from alembic import command
            from alembic.config import Config as AlembicConfig

            cfg = AlembicConfig(str(ini))
            cfg.set_main_option("script_location", str(backend_dir / "migrations"))
            cfg.set_main_option("sqlalchemy.url", _sqlite_url(root))
            command.upgrade(cfg, "head")
        except Exception as exc:  # noqa: BLE001
            # 迁移失败不能让窗口起不来；退回 create_all，并把原因留在日志里。
            print(f"[schema] Alembic 升级失败，退回 create_all：{exc}")
            _fallback_create(engine)
    else:
        _fallback_create(engine)

    _ensure_sqlite_extras(engine)

    after_version = _schema_version(engine)
    return (not had_index) or (before_version != after_version)


def _fallback_create(engine: Engine) -> None:
    from modules.assets import models  # noqa: F401  注册元数据

    Base.metadata.create_all(engine)


def _ensure_sqlite_extras(engine: Engine) -> None:
    """FTS5 虚拟表。trigram 不可用时由搜索模块退回 LIKE。"""
    from modules.assets.search import ensure_fts

    ensure_fts(engine)


def ensure_schema_once(ws_root: Path) -> None:
    """进程内每个工作区只升级一次；给不在 lifespan 里调用的写入路径兜底。"""
    root = Path(ws_root).resolve()
    key = str(root)
    with _guard:
        if key in _ensured:
            return
    ensure_schema(root)
    with _guard:
        _ensured.add(key)


def reset_engines() -> None:
    """测试用：释放所有连接并清空缓存。"""
    with _guard:
        for engine in _engines.values():
            try:
                engine.dispose()
            except Exception:
                pass
        _engines.clear()
        _factories.clear()
        _ensured.clear()
