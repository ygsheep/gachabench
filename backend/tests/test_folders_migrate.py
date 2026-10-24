# -*- coding: utf-8 -*-
"""目录职责重排迁移的验收测试。

跑法（backend 目录下）：

    PYTHONPATH=".;.venv/Lib/site-packages" \
      "C:/Users/young.YOUNG/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe" \
      -m unittest tests.test_folders_migrate -v

覆盖重点：
- 只迁「库还叫素材」的旧布局；已经是 `资产/` 的不动；
- `资产/` 已存在时 **blocked，绝不合并**；
- 迁移后索引路径前缀要跟着改，否则整库会变成一片 missing + 一片新增的幽灵；
- 索引改写失败要**回滚目录改名**（不能留半迁移状态）。
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

import config as config_mod
import folders_migrate as fm
from modules.assets.models import IndexAsset
from modules.platform.db import ensure_schema, reset_engines, session_scope

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class MigrateCase(unittest.TestCase):
    def setUp(self) -> None:
        self._origin_cache = config_mod._config_cache
        self._origin_set = fm.set_config
        fm.set_config = lambda cfg: setattr(config_mod, "_config_cache", cfg)

        self.tmp = Path(tempfile.mkdtemp(prefix="gb-mig-")).resolve()
        self.root = self.tmp / "ws"
        (self.root / "素材" / "人物").mkdir(parents=True)
        (self.root / "素材" / "人物" / "白陌_参考.png").write_bytes(PNG)
        self.entry = config_mod.WorkspaceEntry(
            name="WS", root=str(self.root), folders={"asset": "素材"}
        )
        config_mod._config_cache = config_mod.Config(
            workspaces=[self.entry], active="WS"
        )
        ensure_schema(self.root)

    def tearDown(self) -> None:
        fm.set_config = self._origin_set
        config_mod._config_cache = self._origin_cache
        reset_engines()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------

    def test_plan_pending_on_legacy_layout(self):
        plan = fm.plan_migration("WS")
        self.assertEqual(plan["status"], "pending")
        self.assertEqual(plan["from"], "素材")
        self.assertEqual(plan["to"], "资产")
        self.assertEqual(len(plan["steps"]), 4)

    def test_apply_moves_dir_and_creates_material(self):
        got = fm.apply_migration("WS")
        self.assertEqual(got["status"], "done")
        self.assertTrue((self.root / "资产" / "人物" / "白陌_参考.png").is_file())
        self.assertTrue((self.root / "素材").is_dir())
        self.assertEqual(list((self.root / "素材").iterdir()), [])
        self.assertEqual(self.entry.folders.get("asset"), "资产")
        self.assertEqual(self.entry.folders.get("material"), "素材")

    def test_apply_is_idempotent(self):
        fm.apply_migration("WS")
        again = fm.plan_migration("WS")
        self.assertEqual(again["status"], "already")

    def test_already_when_asset_is_new_name(self):
        self.entry.folders["asset"] = "资产"
        self.assertEqual(fm.plan_migration("WS")["status"], "already")

    def test_nothing_when_no_legacy_dir(self):
        shutil.rmtree(self.root / "素材")
        self.assertEqual(fm.plan_migration("WS")["status"], "nothing")

    def test_blocked_when_target_exists(self):
        (self.root / "资产").mkdir()
        plan = fm.plan_migration("WS")
        self.assertEqual(plan["status"], "blocked")
        # blocked 时不执行 → 磁盘原样
        self.assertEqual(fm.apply_migration("WS")["status"], "blocked")
        self.assertTrue((self.root / "素材" / "人物" / "白陌_参考.png").is_file())

    def test_index_paths_are_rewritten(self):
        with session_scope(self.root) as s:
            s.add(
                IndexAsset(
                    id="a1",
                    path="素材/人物/白陌_参考.png",
                    name="白陌_参考.png",
                    dir_path="素材/人物",
                    role="asset",
                    subject="白陌",
                    category="image",
                    ext=".png",
                    size=len(PNG),
                    mtime=0.0,
                    media_kind="image",
                    status="active",
                    created_at="2026-10-24T00:00:00",
                    updated_at="2026-10-24T00:00:00",
                )
            )
        got = fm.apply_migration("WS")
        self.assertEqual(got["rewritten"]["assets"], 1)
        with session_scope(self.root) as s:
            row = s.scalar(select(IndexAsset).where(IndexAsset.id == "a1"))
            self.assertEqual(row.path, "资产/人物/白陌_参考.png")
            self.assertEqual(row.dir_path, "资产/人物")

    def test_index_failure_rolls_back_rename(self):
        original = fm._rewrite_index
        fm._rewrite_index = lambda root: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            got = fm.apply_migration("WS")
        finally:
            fm._rewrite_index = original
        self.assertEqual(got["status"], "blocked")
        self.assertIn("回滚", got["reason"])
        self.assertTrue((self.root / "素材" / "人物" / "白陌_参考.png").is_file())
        self.assertFalse((self.root / "资产").exists())

    def test_auto_migrate_only_when_pending(self):
        self.assertIsNotNone(fm.auto_migrate_if_needed("WS"))
        self.assertIsNone(fm.auto_migrate_if_needed("WS"))

    def test_unknown_workspace(self):
        with self.assertRaises(Exception):
            fm.plan_migration("NOPE")


if __name__ == "__main__":
    unittest.main()
