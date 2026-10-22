# -*- coding: utf-8 -*-
"""P1 资产索引验收测试。

跑法（backend 目录下）：

    .venv\\Scripts\\python.exe -m unittest tests.test_asset_index -v
"""
from __future__ import annotations

import base64
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

import config as config_mod
from modules.assets import search
from modules.assets.indexer import scan_workspace
from modules.assets.models import IndexAsset
from modules.assets.service import get_scan_manager
from modules.platform.db import ensure_schema, reset_engines, session_scope
from routers import assets as legacy_assets

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO7Zx3sAAAAASUVORK5CYII="
)
WS = "WS"


class IndexCase(unittest.TestCase):
    def setUp(self) -> None:
        self._origin_cache = config_mod._config_cache
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-p1-")).resolve()
        self.root = self.tmp / "ws"
        self.root.mkdir()
        self.install()
        ensure_schema(self.root)

        from modules.assets.api import media_router, router as index_router

        app = FastAPI()
        app.include_router(legacy_assets.router)
        app.include_router(index_router)
        app.include_router(media_router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        get_scan_manager().shutdown()
        reset_engines()
        search.drop_fts_cache()
        config_mod._config_cache = self._origin_cache
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- 夹具 ----------

    def install(self, folder: str = "素材", active: str | None = WS) -> None:
        cfg = config_mod.Config(
            workspaces=[
                config_mod.WorkspaceEntry(
                    name=WS, root=str(self.root), folders={"asset": folder}
                )
            ],
            active=active,
        )
        config_mod._config_cache = cfg

    def write(self, rel: str, data: bytes = PNG_1X1) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def scan(self, force: bool = True) -> dict:
        return scan_workspace(self.root, force=force)

    def rows(self) -> dict[str, IndexAsset]:
        with session_scope(self.root) as session:
            return {a.path: a for a in session.scalars(select(IndexAsset)).all()}

    def wait_scan(self, task_id: str, timeout: float = 15.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            resp = self.client.get(f"/api/assets/scan/{task_id}")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            if body["status"] in ("done", "error"):
                return body
            time.sleep(0.05)
        self.fail("扫描任务超时")


class TestIndexer(IndexCase):
    def test_indexes_files_and_is_idempotent(self):
        for i in range(500):
            self.write(f"素材/sub{i % 5}/角色_{i:03d}_参考.png")
        first = self.scan(force=True)
        self.assertEqual(first["total"], 500)
        self.assertEqual(first["added"], 500)
        self.assertEqual(len(self.rows()), 500)

        second = self.scan(force=False)
        self.assertEqual(second["added"], 0)
        self.assertEqual(len(self.rows()), 500, "重复扫描不应新增记录")

    def test_role_aliases_map_to_asset(self):
        self.write("素材/白陌_参考.png")
        self.write("资产/张三_参考.png")
        self.write("assets/教室_参考.png")
        self.scan(force=True)
        rows = self.rows()
        self.assertEqual(rows["素材/白陌_参考.png"].role, "asset")
        self.assertEqual(rows["资产/张三_参考.png"].role, "asset")
        self.assertEqual(rows["assets/教室_参考.png"].role, "asset")

    def test_skips_system_dirs_and_keeps_other_dirs(self):
        self.write(".gachabench/secret.txt")
        self.write(".git/HEAD")
        self.write("素材/白陌_参考.png")
        self.write("草稿/01_片段.md")
        self.scan(force=True)
        rows = self.rows()
        self.assertNotIn(".gachabench/secret.txt", rows)
        self.assertNotIn(".git/HEAD", rows)
        self.assertEqual(rows["草稿/01_片段.md"].role, "draft")

    def test_delete_marks_missing_and_keeps_row(self):
        self.write("素材/白陌_参考.png")
        self.scan(force=True)
        (self.root / "素材/白陌_参考.png").unlink()

        res = self.scan(force=False)
        self.assertEqual(res["missing"], 1)
        rows = self.rows()
        row = rows["素材/白陌_参考.png"]
        self.assertEqual(row.status, "missing")
        self.assertIsNotNone(row.missing_at)

    def test_hash_is_stable_across_scans(self):
        self.write("素材/白陌_参考.png")
        self.scan(force=True)
        first = self.rows()["素材/白陌_参考.png"].sha256
        self.scan(force=True)
        second = self.rows()["素材/白陌_参考.png"].sha256
        self.assertEqual(first, second)
        self.assertEqual(len(first or ""), 64)

    def test_performance_1000_files(self):
        for i in range(1000):
            self.write(f"素材/角色_{i:04d}_参考.png")
        t0 = time.monotonic()
        first = self.scan(force=True)
        first_elapsed = time.monotonic() - t0
        self.assertEqual(first["total"], 1000)
        self.assertLess(first_elapsed, 30, "首扫 1000 文件应小于 30s")

        t1 = time.monotonic()
        second = self.scan(force=False)
        second_elapsed = time.monotonic() - t1
        self.assertEqual(second["added"], 0)
        self.assertLess(second_elapsed, 2, "无变化二次扫描应小于 2s")


class TestApi(IndexCase):
    def test_list_and_chinese_middle_substring(self):
        self.write("素材/白陌_定妆_参考.png")
        self.write("素材/张三_近景.png")
        self.scan(force=True)

        body = self.client.get("/api/assets").json()
        self.assertEqual(body["total"], 2)

        hit = self.client.get("/api/assets", params={"q": "定妆"}).json()
        self.assertEqual(hit["total"], 1)
        self.assertEqual(hit["items"][0]["name"], "白陌_定妆_参考.png")

    def test_collections_and_role_counts(self):
        self.write("素材/白陌_参考.png")
        self.write("分镜表/01_片段.md", b"# shot")
        self.scan(force=True)
        body = self.client.get("/api/assets/collections").json()
        counts = {r["key"]: r["count"] for r in body["roles"]}
        self.assertEqual(counts["asset"], 1)
        self.assertEqual(counts["storyboard"], 1)
        names = {c["id"] for c in body["collections"]}
        self.assertEqual(names, {"recent", "missing", "unreferenced", "large"})

    def test_patch_meta_persists(self):
        self.write("素材/白陌_参考.png")
        self.scan(force=True)
        asset_id = self.rows()["素材/白陌_参考.png"].id

        r = self.client.patch(
            f"/api/assets/{asset_id}",
            json={"tags": ["主角"], "aliases": ["阿陌"], "note": "定妆参考"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["tags"], ["主角"])
        self.assertEqual(body["aliases"], ["阿陌"])
        self.assertEqual(body["note"], "定妆参考")

        detail = self.client.get(f"/api/assets/{asset_id}").json()
        self.assertEqual(detail["tags"], ["主角"])

        # 主体覆盖后搜索按新主体命中
        self.client.patch(f"/api/assets/{asset_id}", json={"subject_override": "阿陌"})
        hit = self.client.get("/api/assets", params={"q": "阿陌"}).json()
        self.assertEqual(hit["total"], 1)

    def test_scan_task_and_status(self):
        self.write("素材/白陌_参考.png")
        start = self.client.post("/api/assets/scan", json={"force": True})
        self.assertEqual(start.status_code, 200)
        task = start.json()
        self.assertTrue(task["task_id"])
        body = self.wait_scan(task["task_id"])
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["stats"]["added"], 1)

    def test_media_stream_and_traversal(self):
        self.write("素材/白陌_参考.png")
        self.scan(force=True)

        ok = self.client.get("/api/media", params={"path": "素材/白陌_参考.png"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.headers["content-type"], "image/png")

        bad = self.client.get("/api/media", params={"path": "../../Windows/win.ini"})
        self.assertEqual(bad.status_code, 400)

    def test_thumb_generates_webp(self):
        self.write("素材/白陌_参考.png")
        self.scan(force=True)
        r = self.client.get("/api/thumb", params={"path": "素材/白陌_参考.png"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/webp")
        self.assertGreater(len(r.content), 0)

        txt = self.write("素材/notes.txt", b"hi")
        r2 = self.client.get("/api/thumb", params={"path": "素材/notes.txt"})
        self.assertEqual(r2.status_code, 415)

    def test_legacy_groups_still_work(self):
        self.write("素材/白陌_参考.png")
        r = self.client.get("/api/assets/groups")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["total"], 1)

        scan = self.client.post("/api/assets/groups/scan")
        self.assertEqual(scan.status_code, 200)
        self.assertTrue(scan.json()["ok"])

    def test_upload_syncs_new_index(self):
        r = self.client.post(
            "/api/assets/upload",
            data={"prefix": "白陌", "suffix": "", "category": "character", "subdir": ""},
            files=[("files", ("a.png", PNG_1X1, "image/png"))],
        )
        self.assertEqual(r.status_code, 200)
        rel = r.json()["items"][0]["rel"]
        ws_rel = f"素材/{rel}"
        rows = self.rows()
        self.assertIn(ws_rel, rows)
        self.assertEqual(rows[ws_rel].status, "active")


if __name__ == "__main__":
    unittest.main()
