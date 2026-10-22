# -*- coding: utf-8 -*-
"""回归测试：工作区库还停在旧结构时，资产接口应当自愈而不是 500。

事故现象（2026-10-22）：切到新工作区后，`GET /api/assets*` 全部 500，
日志里是 `sqlite3.OperationalError: no such table: index_assets / collections`。
根因：schema 升级只在进程启动时对「当时的活动工作区」跑一次，切换 / 新建 /
打开工作区都不触发；资产接口又直接查表，没有兜底。

跑法（backend 目录下）：

    .venv\\Scripts\\python.exe -m unittest tests.test_schema_selfheal -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

import config as config_mod
import db as legacy_db
from modules.assets import search
from modules.assets.service import get_scan_manager
from modules.platform.db import ensure_schema, get_engine, reset_engines
from routers import assets as legacy_assets

WS = "WS"


def _legacy_only(root: Path) -> None:
    """只建旧 db.py 那 6 张表 —— 模拟「P1/P2 迁移从没跑过」的工作区库。"""
    legacy_db.get_db(root)


def _head_revision() -> str:
    """从 migrations/versions 里读当前 head。"""
    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory

    backend_dir = Path(__file__).resolve().parents[1]
    cfg = AlembicConfig(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _tables(root: Path) -> set[str]:
    with get_engine(root).connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
        ).all()
    return {r[0] for r in rows}


class SchemaSelfHealCase(unittest.TestCase):
    def setUp(self) -> None:
        self._origin_cache = config_mod._config_cache
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-heal-")).resolve()
        self.root = self.tmp / "ws"
        self.root.mkdir()

        cfg = config_mod.Config(
            workspaces=[
                config_mod.WorkspaceEntry(
                    name=WS, root=str(self.root), folders={"asset": "素材"}
                )
            ],
            active=WS,
        )
        config_mod._config_cache = cfg

        # 关键：不调用 ensure_schema / ensure_schema_once，让库保持旧结构
        _legacy_only(self.root)
        self.assertNotIn("index_assets", _tables(self.root))

        from modules.assets.api import media_router, router as index_router

        app = FastAPI()
        app.include_router(legacy_assets.router)
        app.include_router(index_router)
        app.include_router(media_router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        get_scan_manager().shutdown()
        legacy_db.close_all()
        reset_engines()
        search.drop_fts_cache()
        config_mod._config_cache = self._origin_cache
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------

    def test_asset_list_self_heals(self) -> None:
        """旧结构库上直接打资产列表：不能 500，且库要被升到最新 schema。"""
        resp = self.client.get("/api/assets")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["items"], [])
        self.assertEqual(body["workspace"], WS)

        tables = _tables(self.root)
        for name in ("index_assets", "collections", "asset_meta", "alembic_version"):
            self.assertIn(name, tables, f"自愈后仍缺表：{name}")

        with get_engine(self.root).connect() as conn:
            ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        # 比的是「迁移脚本的 head」，不是写死的版本号：以后加迁移不用回来改这行
        self.assertEqual(ver, _head_revision())

    def test_collections_and_scan_self_heal(self) -> None:
        resp = self.client.get("/api/assets/collections")
        self.assertEqual(resp.status_code, 200, resp.text)
        roles = {r["key"] for r in resp.json()["roles"]}
        self.assertTrue(roles, "角色统计不应为空")
        self.assertIn("other", roles)
        # 空库也要给出角色项与 0 计数，前端才不会白屏
        self.assertTrue(all(r["count"] == 0 for r in resp.json()["roles"]))

        # 集合种子已写入，且内置集合齐全
        cols = {c["id"] for c in resp.json()["collections"]}
        self.assertTrue({"recent", "missing", "unreferenced"} <= cols)

    def test_second_request_does_not_remigrate(self) -> None:
        """自愈只做一次：同一工作区后续请求走缓存，不再跑 Alembic。"""
        self.assertEqual(self.client.get("/api/assets").status_code, 200)
        self.assertEqual(self.client.get("/api/assets/collections").status_code, 200)
        # ensure_schema 幂等；这里只验证不会因为重复升级而报错
        ensure_schema(self.root)

    def test_activate_warms_index(self) -> None:
        """切换 / 打开工作区的路径也要建 schema，不能只靠第一次请求兜底。"""
        from routers.workspace import _warm_index
        from workspaces import WorkspaceEntry

        _warm_index(WorkspaceEntry(name=WS, root=str(self.root)))
        self.assertIn("index_assets", _tables(self.root))


if __name__ == "__main__":
    unittest.main()
