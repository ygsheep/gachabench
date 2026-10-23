# -*- coding: utf-8 -*-
"""工作区「显示名（label）」与「安全改名」的单元测试。

跑法（在 backend 目录下）：

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

隔离策略：`workspaces.set_config` 换成「只更新内存缓存、不落盘」的替身，
所有目录都建在临时目录里、跑完删掉 —— 不碰用户真实的 config.json 与工作区。
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import config as config_mod
import db as db_mod
import workspaces as ws_mod
from config import Config


class WorkspaceLabelTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="gb_ws_"))
        self.root = self._tmp / "Day01"
        (self.root / ".gachabench").mkdir(parents=True, exist_ok=True)

        cfg = Config.model_validate(
            {
                "workspaces": [{"name": "Day01", "root": str(self.root), "label": "", "folders": {}}],
                "active": "Day01",
            }
        )
        config_mod._config_cache = cfg
        self._orig_set = ws_mod.set_config
        ws_mod.set_config = lambda c: setattr(config_mod, "_config_cache", c)

    def tearDown(self) -> None:
        ws_mod.set_config = self._orig_set
        for p in (self.root, self._tmp / "第一天"):
            try:
                db_mod.close_workspace(p)
            except Exception:  # noqa: BLE001
                pass
        config_mod._config_cache = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ---------- label ----------

    def test_set_label_only_touches_config(self) -> None:
        """改显示名：目录原地不动、库不动。"""
        db = db_mod.get_db(self.root)
        with db.lock:
            db.conn.execute(
                "INSERT INTO cards (workspace, rel_path, status) VALUES ('Day01','草稿/a.mp4','draft')"
            )
            db.conn.commit()

        ws = ws_mod.set_label("Day01", "  第一天  ")

        self.assertEqual(ws.label, "第一天")
        self.assertEqual(ws.name, "Day01")  # name 是库键，绝不能变
        self.assertEqual(ws.title, "第一天")
        self.assertTrue(self.root.is_dir())
        with db.lock:
            n = db.conn.execute("SELECT COUNT(*) c FROM cards WHERE workspace='Day01'").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_label_blank_falls_back_to_dirname(self) -> None:
        ws = ws_mod.set_label("Day01", "第一天")
        self.assertEqual(ws.title, "第一天")
        ws = ws_mod.set_label("Day01", "   ")
        self.assertEqual(ws.label, "")
        self.assertEqual(ws.title, "Day01")

    def test_label_too_long_rejected(self) -> None:
        with self.assertRaises(ws_mod.WorkSpaceError):
            ws_mod.set_label("Day01", "x" * (ws_mod.LABEL_MAX + 1))

    def test_label_on_missing_workspace(self) -> None:
        with self.assertRaises(ws_mod.WorkSpaceError):
            ws_mod.set_label("不存在", "随便")

    # ---------- 危险路径：真改目录名 ----------

    def test_rename_moves_dir_and_rekeys_cards(self) -> None:
        """改目录名必须：目录跟着改、cards.workspace 跟着改、新路径能重开库。

        这条链上任何一环漏掉，卡池就会在改名后瞬间变空（历史上正是这么坏的）。
        """
        db = db_mod.get_db(self.root)
        with db.lock:
            db.conn.execute(
                "INSERT INTO cards (workspace, rel_path, status) VALUES ('Day01','草稿/a.mp4','draft')"
            )
            db.conn.commit()

        ws = ws_mod.rename("Day01", "第一天")

        new_root = self._tmp / "第一天"
        self.assertFalse(self.root.exists(), "旧目录应该已经不在")
        self.assertTrue((new_root / ".gachabench" / "cards.db").is_file())
        self.assertEqual(ws.name, "第一天")
        self.assertEqual(ws.root, str(new_root))
        self.assertEqual(ws.title, "第一天")  # 没单独设过 label，跟着目录名走
        self.assertEqual(config_mod.get_config().active, "第一天")

        newdb = db_mod.get_db(new_root)
        with newdb.lock:
            rows = newdb.conn.execute("SELECT workspace, rel_path FROM cards").fetchall()
        self.assertEqual([(r["workspace"], r["rel_path"]) for r in rows], [("第一天", "草稿/a.mp4")])

    def test_rename_keeps_custom_label(self) -> None:
        ws_mod.set_label("Day01", "我自己起的名")
        ws = ws_mod.rename("Day01", "Day01_new")
        self.assertEqual(ws.label, "我自己起的名")

    def test_rename_refuses_existing_target(self) -> None:
        (self._tmp / "占用中").mkdir()
        with self.assertRaises(ws_mod.WorkSpaceError):
            ws_mod.rename("Day01", "占用中")
        self.assertTrue(self.root.is_dir(), "失败时不该动原目录")


if __name__ == "__main__":
    unittest.main()
