# -*- coding: utf-8 -*-
"""「通过」落点验收测试（2026-10-24 改版）。

跑法（backend 目录下）：

    PYTHONPATH=".;.venv/Lib/site-packages" \
      "C:/Users/young.YOUNG/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe" \
      -m unittest tests.test_to_final -v

覆盖重点：
- 通过 = `status=final` + 复制一份到 `{资产}/未分类/`，**原件留在草稿**；
- 文件名按模板现算（`17.1_成片.mp4`），块号从文件名 `NofM` 优先取；
- 🔴 旧缺陷回归：`chunk` 绝不再被覆写成 `"final"`；
- 算不出段号 → 409，**不猜**；同名 → `_02`，绝不覆盖。
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

import config as config_mod
import db as db_mod
from modules.assets.models import IndexAsset
from modules.assets.roles import UNCATEGORIZED_DIR
from modules.platform.db import ensure_schema, reset_engines, session_scope
from routers import cards as cards_router

WS = "WS"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 56


class ToFinalCase(unittest.TestCase):
    def setUp(self) -> None:
        self._origin = config_mod._config_cache
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-final-")).resolve()
        self.root = self.tmp / "ws"
        (self.root / "草稿").mkdir(parents=True)
        config_mod._config_cache = config_mod.Config(
            workspaces=[
                config_mod.WorkspaceEntry(name=WS, root=str(self.root), folders={})
            ],
            active=WS,
        )
        ensure_schema(self.root)
        db_mod.close_workspace(self.root)
        self.db = db_mod.get_db(self.root)

        app = FastAPI()
        app.include_router(cards_router.router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        db_mod.close_workspace(self.root)
        reset_engines()
        config_mod._config_cache = self._origin
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- 夹具 ----------

    def draft(self, name: str) -> Path:
        p = self.root / "草稿" / name
        p.write_bytes(MP4)
        return p

    def card(self, rel: str, seg: str | None = None, chunk: str | None = None) -> int:
        """直接插一行卡，绕开 ffprobe（测试用的是假 mp4 字节）。"""
        with self.db.lock:
            cur = self.db.conn.execute(
                "INSERT INTO cards (workspace, rel_path, seg, chunk, status, created_at, updated_at)"
                " VALUES (?,?,?,?,'draft','2026-10-24T00:00:00','2026-10-24T00:00:00')",
                (WS, rel, seg, chunk),
            )
            self.db.conn.commit()
            return int(cur.lastrowid)

    def pass_it(self, card_id: int, **body) -> dict:
        resp = self.client.post(f"/api/cards/{card_id}/to-final", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def delivered(self) -> list[str]:
        # 资产目录名从配置里取，别写死（2026-10-24 重排：素材 → 资产）
        d = self.root / config_mod.Config().folders.asset / UNCATEGORIZED_DIR
        return sorted(p.name for p in d.iterdir()) if d.is_dir() else []

    def row(self, card_id: int) -> dict:
        with self.db.lock:
            r = self.db.conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        return dict(r)

    # ---------- 用例 ----------

    def test_default_copies_with_template_name(self):
        self.draft("17_第17段_1of3_00001.mp4")
        cid = self.card("草稿/17_第17段_1of3_00001.mp4")
        body = self.pass_it(cid)

        self.assertEqual(self.delivered(), ["17.1_成片.mp4"])
        self.assertTrue(body["copied_to"].endswith("17.1_成片.mp4"))
        self.assertEqual(body["chunk_source"], "filename")
        self.assertFalse(body["missing_chunk"])
        # 原件还在草稿：一处真相 + 一处副本
        self.assertTrue((self.root / "草稿" / "17_第17段_1of3_00001.mp4").exists())

    def test_chunk_is_never_overwritten(self):
        # 🔴 旧缺陷：勾选复制时把 chunk 写成 "final"，把 1of3 永久丢掉。
        self.draft("17_第17段_1of3_00001.mp4")
        cid = self.card("草稿/17_第17段_1of3_00001.mp4", seg="17", chunk="1of3")
        self.pass_it(cid)
        row = self.row(cid)
        self.assertEqual(row["status"], "final")
        self.assertEqual(row["chunk"], "1of3")

    def test_missing_chunk_falls_back_to_bare_seg(self):
        self.draft("18_正片输出.mp4")
        cid = self.card("草稿/18_正片输出.mp4", seg="18")
        body = self.pass_it(cid)
        self.assertEqual(self.delivered(), ["18_成片.mp4"])
        self.assertTrue(body["missing_chunk"])
        self.assertEqual(body["chunk_source"], "none")

    def test_chunk_from_card_field(self):
        self.draft("17_output.mp4")
        cid = self.card("草稿/17_output.mp4", seg="17", chunk="2of3")
        self.pass_it(cid)
        self.assertEqual(self.delivered(), ["17.2_成片.mp4"])

    def test_no_segment_is_409_not_a_guess(self):
        self.draft("未认领_00042.mp4")
        cid = self.card("草稿/未认领_00042.mp4")
        resp = self.client.post(f"/api/cards/{cid}/to-final", json={})
        self.assertEqual(resp.status_code, 409, resp.text)
        self.assertEqual(self.delivered(), [])
        self.assertEqual(self.row(cid)["status"], "draft")

    def test_same_slot_twice_gets_seq_suffix(self):
        self.draft("17_第17段_1of3_00001.mp4")
        self.draft("17_第17段_1of3_00002.mp4")
        a = self.card("草稿/17_第17段_1of3_00001.mp4")
        b = self.card("草稿/17_第17段_1of3_00002.mp4")
        self.pass_it(a)
        self.pass_it(b)
        self.assertEqual(self.delivered(), ["17.1_成片.mp4", "17.1_成片_02.mp4"])

    def test_opt_out_marks_only(self):
        self.draft("17_第17段_1of3_00001.mp4")
        cid = self.card("草稿/17_第17段_1of3_00001.mp4")
        body = self.pass_it(cid, copy_to_asset=False)
        self.assertIsNone(body["copied_to"])
        self.assertEqual(self.delivered(), [])
        self.assertEqual(self.row(cid)["status"], "final")

    def test_legacy_field_still_works(self):
        self.draft("17_第17段_1of3_00001.mp4")
        cid = self.card("草稿/17_第17段_1of3_00001.mp4")
        body = self.pass_it(cid, copy_to_final=True)
        self.assertEqual(self.delivered(), ["17.1_成片.mp4"])
        self.assertTrue(body["copied_to"])

    def test_copy_is_registered_as_asset(self):
        self.draft("17_第17段_1of3_00001.mp4")
        cid = self.card("草稿/17_第17段_1of3_00001.mp4")
        body = self.pass_it(cid)
        self.assertTrue(body["asset_id"])
        with session_scope(self.root) as s:
            got = s.scalar(
                select(IndexAsset).where(IndexAsset.name == "17.1_成片.mp4")
            )
            self.assertIsNotNone(got)
            self.assertEqual(
                got.path,
                f"{config_mod.Config().folders.asset}/{UNCATEGORIZED_DIR}/17.1_成片.mp4",
            )
            self.assertEqual(got.role, "asset")
            # 镜头类没有主体：段号不能被当成主体名，否则资产页长出假角色 `17.1`
            self.assertEqual(got.subject, "")

    def test_template_name_draft_needs_no_rewrite(self):
        # 草稿里已经是模板名：照原名交付，不叠一层
        self.draft("19.2_成片.mp4")
        cid = self.card("草稿/19.2_成片.mp4")
        body = self.pass_it(cid)
        self.assertEqual(self.delivered(), ["19.2_成片.mp4"])
        self.assertEqual(body["chunk_source"], "name")


if __name__ == "__main__":
    unittest.main()
