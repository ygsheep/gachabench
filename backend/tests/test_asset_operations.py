# -*- coding: utf-8 -*-
"""P2 文件操作与回收站验收测试。

跑法（backend 目录下）：

    .venv\\Scripts\\python.exe -m unittest tests.test_asset_operations -v
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

import config as config_mod
from modules.assets import plans, refs, search
from modules.assets.indexer import scan_workspace
from modules.assets.models import AssetRef, IndexAsset, OperationPlan
from modules.assets.service import get_scan_manager
from modules.platform.db import ensure_schema, reset_engines, session_scope
from modules.prompts import rewrite

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO7Zx3sAAAAASUVORK5CYII="
)
WS = "WS"
NAME = "白陌_写实_四视图.png"


class OpsCase(unittest.TestCase):
    def setUp(self) -> None:
        self._origin_cache = config_mod._config_cache
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-p2-")).resolve()
        self.root = self.tmp / "ws"
        self.root.mkdir()
        self.install()
        ensure_schema(self.root)

        from modules.assets.api import media_router, router as index_router
        from modules.assets.ops_api import router as ops_router

        app = FastAPI()
        # 操作路由先挂，避免 /api/assets/{asset_id} 吞掉 duplicates / operations
        app.include_router(ops_router)
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

    def write_text(self, rel: str, text: str, encoding: str = "utf-8") -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode(encoding))
        return path

    def scan(self) -> dict:
        return scan_workspace(self.root, force=True)

    def rows(self) -> dict[str, IndexAsset]:
        with session_scope(self.root) as session:
            return {a.path: a for a in session.scalars(select(IndexAsset)).all()}

    def asset_id(self, rel: str) -> str:
        return self.rows()[rel].id

    def ref_rows(self, asset_id: str) -> list[AssetRef]:
        with session_scope(self.root) as session:
            return list(
                session.scalars(
                    select(AssetRef).where(AssetRef.asset_id == asset_id)
                ).all()
            )

    def plan(self, kind: str, **payload) -> dict:
        resp = self.client.post(
            "/api/assets/operations/plan", json={"kind": kind, **payload}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def apply(self, plan_id: str) -> dict:
        resp = self.client.post(f"/api/assets/operations/{plan_id}/apply")
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def detail(self, plan_id: str) -> dict:
        resp = self.client.get(f"/api/assets/operations/{plan_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def err_code(self, resp) -> str:
        body = resp.json()
        detail = body.get("detail")
        if isinstance(detail, dict):
            return detail.get("code", "")
        return ""


class TestRewriteSpans(unittest.TestCase):
    def test_applies_descending_with_records(self):
        text = "aXbXc"
        new_text, records = rewrite.apply_spans(
            text, [(1, 2, "Y"), (3, 4, "ZZ")]
        )
        self.assertEqual(new_text, "aYbZZc")
        self.assertEqual([r["before"] for r in records], ["X", "X"])
        self.assertEqual([r["after"] for r in records], ["Y", "ZZ"])

    def test_rejects_out_of_bounds(self):
        with self.assertRaises(rewrite.RewriteError):
            rewrite.apply_spans("abc", [(0, 4, "x")])

    def test_rejects_overlap(self):
        with self.assertRaises(rewrite.RewriteError):
            rewrite.apply_spans("abcdef", [(1, 4, "x"), (3, 5, "y")])

    def test_no_spans_returns_original(self):
        text = "abc"
        new_text, records = rewrite.apply_spans(text, [])
        self.assertEqual(new_text, text)
        self.assertEqual(records, [])


class TestRefs(OpsCase):
    def test_full_name_boundary_does_not_match_longer_name(self):
        self.write("素材/A.png")
        self.write("素材/AA.png")
        self.write_text("提示词/refs.md", "AA.png\nA.png\n")
        self.scan()

        a_id = self.asset_id("素材/A.png")
        aa_id = self.asset_id("素材/AA.png")
        self.assertEqual(len(self.ref_rows(a_id)), 1, "A.png 不应命中 AA.png")
        self.assertEqual(len(self.ref_rows(aa_id)), 1)

    def test_kind_detection(self):
        self.write(f"素材/{NAME}")
        self.write_text(
            "提示词/01.md",
            f"| 1 | {NAME} |\n正文提到 {NAME}\n",
        )
        self.write_text("分镜表/01.md", f"| 镜头 | 素材 |\n| 1 | {NAME} |\n")
        self.write_text(
            "工作流/flow.json",
            json.dumps({"inputs": {"image": NAME}}, ensure_ascii=False),
        )
        self.scan()
        rows = self.ref_rows(self.asset_id(f"素材/{NAME}"))
        kinds = {r.kind for r in rows}
        self.assertIn("prompt_filename", kinds)
        self.assertIn("prompt_body", kinds)
        self.assertIn("storyboard_material", kinds)
        self.assertIn("workflow_input", kinds)


class TestRename(OpsCase):
    def test_rename_updates_all_refs_byte_exact(self):
        self.write(f"素材/{NAME}")
        prompt_lines = [f"| {i} | {NAME} |" for i in range(50)]
        prompt_text = "# 角色\r\n\r\n" + "\r\n".join(prompt_lines) + f"\r\n正文：{NAME}\r\n"
        self.write_text("提示词/01_角色.md", prompt_text)
        self.write_text("分镜表/01_分镜.md", f"| 1 | {NAME} |\n")
        self.write_text(
            "工作流/flow.json",
            json.dumps({"inputs": {"image": NAME}}, ensure_ascii=False),
        )
        self.scan()

        asset_id = self.asset_id(f"素材/{NAME}")
        before_refs = self.ref_rows(asset_id)
        self.assertGreaterEqual(len(before_refs), 52)

        new_name = "白陌_写实_四视图_v2.png"
        plan = self.plan("rename", asset_id=asset_id, new_name=new_name)
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["blocked"], [])
        self.assertGreaterEqual(plan["stats"]["refs"], 52)
        self.assertEqual(plan["stats"]["files"], 3)

        applied = self.apply(plan["id"])
        self.assertEqual(applied["status"], "applied")
        self.assertFalse((self.root / f"素材/{NAME}").exists())
        self.assertTrue((self.root / f"素材/{new_name}").exists())

        # 逐文件字节校验：只有目标文件名变了，其余字节完全一致。
        expected_prompt = prompt_text.replace(NAME, new_name)
        actual_prompt = (self.root / "提示词/01_角色.md").read_bytes()
        self.assertEqual(actual_prompt, expected_prompt.encode("utf-8"))
        self.assertIn(b"\r\n", actual_prompt, "CRLF 换行必须保留")

        expected_story = f"| 1 | {new_name} |\n".encode("utf-8")
        self.assertEqual((self.root / "分镜表/01_分镜.md").read_bytes(), expected_story)

        expected_json = json.dumps(
            {"inputs": {"image": new_name}}, ensure_ascii=False
        ).encode("utf-8")
        self.assertEqual((self.root / "工作流/flow.json").read_bytes(), expected_json)

        after_refs = self.ref_rows(asset_id)
        self.assertEqual(len(after_refs), len(before_refs))
        self.assertTrue(all(r.file_path for r in after_refs))

        detail = self.client.get(f"/api/assets/{asset_id}").json()
        self.assertEqual(detail["path"], f"素材/{new_name}")
        self.assertGreaterEqual(len(detail["refs"]), 52)

        backup = self.root / ".gachabench" / "backups" / plan["id"]
        self.assertTrue((backup / "提示词/01_角色.md").is_file())
        self.assertTrue((backup / f"素材/{NAME}").is_file())

    def test_rollback_restores_hash_and_path(self):
        self.write(f"素材/{NAME}")
        prompt_text = f"参考：{NAME}\n"
        self.write_text("提示词/01.md", prompt_text)
        self.scan()
        asset_id = self.asset_id(f"素材/{NAME}")
        old_hash = self.rows()[f"素材/{NAME}"].sha256

        plan = self.plan("rename", asset_id=asset_id, new_name="改名以后.png")
        self.apply(plan["id"])
        self.assertTrue((self.root / "素材/改名以后.png").is_file())

        resp = self.client.post(f"/api/assets/operations/{plan['id']}/rollback")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "rolled_back")

        self.assertTrue((self.root / f"素材/{NAME}").is_file())
        self.assertFalse((self.root / "素材/改名以后.png").exists())
        self.assertEqual((self.root / "提示词/01.md").read_text("utf-8"), prompt_text)
        row = self.rows()[f"素材/{NAME}"]
        self.assertEqual(row.id, asset_id)
        self.assertEqual(row.sha256, old_hash)
        self.assertGreaterEqual(len(self.ref_rows(asset_id)), 1)

    def test_case_only_rename(self):
        self.write("素材/A.png")
        self.write_text("提示词/01.md", "参考：A.png\n")
        self.scan()
        asset_id = self.asset_id("素材/A.png")

        plan = self.plan("rename", asset_id=asset_id, new_name="a.png")
        self.assertEqual(plan["blocked"], [])
        self.apply(plan["id"])

        names = os.listdir(self.root / "素材")
        self.assertIn("a.png", names)
        self.assertNotIn("A.png", names)
        self.assertEqual(
            (self.root / "提示词/01.md").read_text("utf-8"), "参考：a.png\n"
        )
        self.assertGreaterEqual(len(self.ref_rows(asset_id)), 1)

    def test_name_conflict_is_blocked(self):
        self.write("素材/A.png")
        self.write("素材/B.png")
        self.scan()
        asset_id = self.asset_id("素材/A.png")

        plan = self.plan("rename", asset_id=asset_id, new_name="B.png")
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["files"], [])
        self.assertEqual(plan["blocked"][0]["reason"], "NAME_CONFLICT")
        self.assertTrue((self.root / "素材/A.png").is_file())
        self.assertTrue((self.root / "素材/B.png").is_file())

    def test_permission_error_returns_file_busy_and_rolls_back(self):
        self.write(f"素材/{NAME}")
        self.write_text("提示词/01.md", f"参考：{NAME}\n")
        self.scan()
        asset_id = self.asset_id(f"素材/{NAME}")
        before = (self.root / f"素材/{NAME}").read_bytes()

        plan = self.plan("rename", asset_id=asset_id, new_name="占用测试.png")
        with mock.patch.object(
            plans.os, "replace", side_effect=PermissionError("file is busy")
        ):
            resp = self.client.post(f"/api/assets/operations/{plan['id']}/apply")
        self.assertEqual(resp.status_code, 409, resp.text)
        self.assertEqual(self.err_code(resp), "FILE_BUSY")

        self.assertTrue((self.root / f"素材/{NAME}").is_file())
        self.assertFalse((self.root / "素材/占用测试.png").exists())
        self.assertEqual((self.root / f"素材/{NAME}").read_bytes(), before)
        self.assertEqual(
            (self.root / "提示词/01.md").read_text("utf-8"), f"参考：{NAME}\n"
        )
        self.assertEqual(self.detail(plan["id"])["status"], "failed")

    def test_expired_plan_returns_plan_stale(self):
        self.write(f"素材/{NAME}")
        self.write_text("提示词/01.md", f"参考：{NAME}\n")
        self.scan()
        asset_id = self.asset_id(f"素材/{NAME}")

        plan = self.plan("rename", asset_id=asset_id, new_name="过期.png")
        past = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat(
            timespec="seconds"
        )
        with session_scope(self.root) as session:
            row = session.get(OperationPlan, plan["id"])
            row.expires_at = past

        resp = self.client.post(f"/api/assets/operations/{plan['id']}/apply")
        self.assertEqual(resp.status_code, 409, resp.text)
        self.assertEqual(self.err_code(resp), "PLAN_STALE")
        self.assertTrue((self.root / f"素材/{NAME}").is_file())
        self.assertEqual((self.root / "提示词/01.md").read_text("utf-8"), f"参考：{NAME}\n")


class TestMove(OpsCase):
    def test_move_outside_workspace_is_path_invalid(self):
        self.write(f"素材/{NAME}")
        self.scan()
        asset_id = self.asset_id(f"素材/{NAME}")

        resp = self.client.post(
            "/api/assets/operations/plan",
            json={
                "kind": "move",
                "asset_ids": [asset_id],
                "dest_dir": "../outside",
            },
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertEqual(self.err_code(resp), "PATH_INVALID")

        resp2 = self.client.post(
            "/api/assets/operations/plan",
            json={
                "kind": "move",
                "asset_ids": [asset_id],
                "dest_dir": "C:/Windows",
            },
        )
        self.assertEqual(resp2.status_code, 400, resp2.text)
        self.assertEqual(self.err_code(resp2), "PATH_INVALID")

    def test_move_keeps_refs_valid(self):
        self.write(f"素材/子目录/{NAME}")
        self.write_text("提示词/01.md", f"参考：{NAME}\n")
        self.scan()
        asset_id = self.asset_id(f"素材/子目录/{NAME}")

        plan = self.plan("move", asset_ids=[asset_id], dest_dir="归档")
        self.apply(plan["id"])
        self.assertTrue((self.root / f"归档/{NAME}").is_file())
        row = self.rows()[f"归档/{NAME}"]
        self.assertEqual(row.id, asset_id)
        self.assertGreaterEqual(len(self.ref_rows(asset_id)), 1)


class TestDeleteAndTrash(OpsCase):
    def test_delete_plan_counts_refs_and_restore(self):
        self.write(f"素材/{NAME}")
        self.write_text("提示词/01.md", f"参考：{NAME}\n")
        self.write_text("分镜表/01.md", f"| 1 | {NAME} |\n")
        self.scan()
        asset_id = self.asset_id(f"素材/{NAME}")

        plan = self.plan("delete", asset_ids=[asset_id])
        self.assertEqual(plan["stats"]["assets"], 1)
        self.assertEqual(plan["stats"]["refs"], 2)
        self.assertEqual(plan["files"], [])

        self.apply(plan["id"])
        self.assertFalse((self.root / f"素材/{NAME}").exists())

        row = self.rows()[f"素材/{NAME}"]
        self.assertEqual(row.status, "trashed")
        detail = self.client.get(f"/api/assets/{asset_id}").json()
        self.assertEqual(detail["status"], "trashed")
        self.assertEqual(len(detail["refs"]), 2, "删除不改写引用")

        trashed = self.client.get("/api/assets", params={"collection": "trashed"})
        self.assertEqual(trashed.status_code, 200)
        self.assertEqual(trashed.json()["total"], 1)

        listing = self.client.get("/api/trash").json()
        self.assertEqual(listing["total"], 1)
        entry = listing["items"][0]
        self.assertTrue(entry["trash_path"].startswith(".gachabench/trash/"))
        self.assertEqual(entry["original_path"], f"素材/{NAME}")

        resp = self.client.post(f"/api/trash/{entry['id']}/restore")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["path"], f"素材/{NAME}")
        self.assertTrue((self.root / f"素材/{NAME}").is_file())

        restored = self.client.get(f"/api/assets/{asset_id}").json()
        self.assertEqual(restored["status"], "active")
        self.assertGreaterEqual(len(restored["refs"]), 2)

    def test_restore_name_collision_uses_02(self):
        self.write(f"素材/{NAME}")
        self.scan()
        asset_id = self.asset_id(f"素材/{NAME}")
        plan = self.plan("delete", asset_ids=[asset_id])
        self.apply(plan["id"])

        # 原路径被新文件占用（不重扫，模拟用户手动放回一个同名文件）
        collision = b"new file occupying the original name"
        self.write(f"素材/{NAME}", collision)

        entry = self.client.get("/api/trash").json()["items"][0]
        resp = self.client.post(f"/api/trash/{entry['id']}/restore")
        self.assertEqual(resp.status_code, 200, resp.text)
        new_path = resp.json()["path"]
        self.assertTrue(new_path.endswith("_02.png"), new_path)
        self.assertEqual((self.root / f"素材/{NAME}").read_bytes(), collision)
        self.assertTrue((self.root / new_path).is_file())
        self.assertEqual(self.rows()[new_path].status, "active")

    def test_backups_and_trash_do_not_enter_asset_list(self):
        self.write(f"素材/{NAME}")
        self.write_text("提示词/01.md", f"参考：{NAME}\n")
        self.scan()
        asset_id = self.asset_id(f"素材/{NAME}")
        plan = self.plan("rename", asset_id=asset_id, new_name="备份测试.png")
        self.apply(plan["id"])
        self.scan()

        rows = self.rows()
        self.assertTrue(any(p.startswith(".gachabench") for p in [
            x.relative_to(self.root).as_posix()
            for x in (self.root / ".gachabench").rglob("*")
            if x.is_file()
        ]))
        self.assertFalse(any(p.startswith(".gachabench") for p in rows))
        listed = self.client.get("/api/assets", params={"limit": 500}).json()
        self.assertFalse(
            any(item["path"].startswith(".gachabench") for item in listed["items"])
        )


class TestDuplicatesAndUnreferenced(OpsCase):
    def test_duplicates_and_unreferenced(self):
        self.write("素材/重复_A.png", PNG_1X1)
        self.write("素材/重复_B.png", PNG_1X1)
        self.write("素材/被引用.png", b"unique-bytes")
        self.write_text("提示词/01.md", "参考：被引用.png\n")
        self.scan()

        dup = self.client.get("/api/assets/duplicates")
        self.assertEqual(dup.status_code, 200, dup.text)
        body = dup.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["groups"][0]["count"], 2)
        names = {item["name"] for item in body["groups"][0]["items"]}
        self.assertEqual(names, {"重复_A.png", "重复_B.png"})

        unref = self.client.get("/api/assets/unreferenced")
        self.assertEqual(unref.status_code, 200, unref.text)
        names = {item["name"] for item in unref.json()["items"]}
        self.assertIn("重复_A.png", names)
        self.assertIn("重复_B.png", names)
        self.assertNotIn("被引用.png", names)


if __name__ == "__main__":
    unittest.main()
