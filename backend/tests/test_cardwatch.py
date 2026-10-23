# -*- coding: utf-8 -*-
"""卡池增量刷新（cardwatch）的单元测试。

要守住的三件事：
1. 没有变化时**零写入**（不重复探测、不重复 UPDATE）；
2. 新片 / 改过的片能被认出来；
3. `min_gap` 真的能挡住高频调用（这是「减少占用」的关键）。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import cardwatch
import db as db_mod
from config import Config


class CardWatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="gb_watch_"))
        self.root = self._tmp / "WS"
        self.draft = self.root / "草稿"
        self.final = self.root / "生成"
        self.draft.mkdir(parents=True)
        self.final.mkdir(parents=True)

        self.cfg = Config.model_validate(
            {
                "workspaces": [{"name": "WS", "root": str(self.root), "label": "", "folders": {}}],
                "active": "WS",
            }
        )
        self.ws = self.cfg.workspaces[0]
        self.db = db_mod.get_db(self.root)
        cardwatch.reset()

    def tearDown(self) -> None:
        cardwatch.reset()
        try:
            db_mod.close_workspace(self.root)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ---------- helpers ----------

    def _put(self, rel: str, size: int = 1024) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\0" * size)
        return p

    def _fresh(self, **kw):
        return cardwatch.ensure_fresh(self.cfg, "WS", self.db, self.root, **kw)

    def _count(self) -> int:
        with self.db.lock:
            return self.db.conn.execute("SELECT COUNT(*) c FROM cards").fetchone()["c"]

    # ---------- cases ----------

    def test_registers_new_and_then_goes_quiet(self) -> None:
        self._put("草稿/a.mp4")
        r1 = self._fresh()
        self.assertEqual((r1["added"], r1["probed"]), (1, 1))
        self.assertEqual(self._count(), 1)

        r2 = self._fresh()
        self.assertEqual(r2["skipped"], "unchanged")
        self.assertEqual((r2["added"], r2["probed"]), (0, 0))

        # 再跑十轮也不该有任何写入（这才是「实时读盘但不占资源」）
        for _ in range(10):
            self.assertEqual(self._fresh()["skipped"], "unchanged")
        self.assertEqual(self._count(), 1)

    def test_new_file_after_first_pass_is_found(self) -> None:
        self._put("草稿/a.mp4")
        self._fresh()
        self._put("生成/b.mp4")
        r = self._fresh()
        self.assertEqual(r["added"], 1)
        self.assertEqual(r["probed"], 1)  # 只探新片，不重探 a.mp4
        self.assertEqual(self._count(), 2)

    def test_changed_file_is_reprobed(self) -> None:
        p = self._put("草稿/a.mp4", 1024)
        self._fresh()
        p.write_bytes(b"\0" * 4096)  # 大小变了 = 同一路径被重跑覆盖
        r = self._fresh()
        self.assertEqual((r["added"], r["updated"], r["probed"]), (0, 1, 1))

    def test_min_gap_blocks_back_to_back_calls(self) -> None:
        self._put("草稿/a.mp4")
        self._fresh()
        self._put("草稿/b.mp4")  # 盘上确实变了
        r = self._fresh(min_gap=30)
        self.assertEqual(r["skipped"], "gap")
        self.assertEqual(r["added"], 0)
        r2 = self._fresh(min_gap=0)  # 放行后立刻能看见
        self.assertEqual(r2["added"], 1)

    def test_force_reprobes_everything(self) -> None:
        self._put("草稿/a.mp4")
        self._put("生成/b.mp4")
        self._fresh()
        r = self._fresh(force=True)
        self.assertEqual(r["probed"], 2)  # 手动「重新扫描」= 连旧片也重探
        self.assertEqual(r["added"], 0)

    def test_missing_dir_is_not_an_error(self) -> None:
        shutil.rmtree(self.final)
        self._put("草稿/a.mp4")
        self.assertEqual(self._fresh()["added"], 1)

    def test_deleted_file_keeps_card(self) -> None:
        """文件从盘上消失（被移走 / 改名）：库里那条卡不删 —— 备注和废稿原因是人手写的。"""
        p = self._put("草稿/a.mp4")
        self._fresh()
        os.remove(p)
        self._fresh()
        self.assertEqual(self._count(), 1)

    def test_non_mp4_ignored(self) -> None:
        self._put("草稿/cover.png")
        r = self._fresh()
        self.assertEqual((r["checked"], r["added"]), (0, 0))

    def test_subdirs_are_scanned(self) -> None:
        self._put("草稿/第1集/c.mp4")
        self.assertEqual(self._fresh()["added"], 1)

    def test_note_root_skips_next_probe(self) -> None:
        """跑片刚落库时先 note_root，后台循环就不该把同一批片子再探一遍。"""
        self._put("草稿/a.mp4")
        cardwatch.note_root(self.cfg, self.ws)
        time.sleep(0.01)
        self.assertEqual(self._fresh()["skipped"], "unchanged")

    def test_cold_start_does_not_reprobe_known_cards(self) -> None:
        """重启后第一次刷新：库里已有的卡片不再探测（否则每次启动都全量 ffprobe）。"""
        self._put("草稿/a.mp4")
        self._fresh()
        cardwatch.reset()  # 模拟进程重启，指纹没了
        r = self._fresh()
        self.assertEqual((r["probed"], r["added"]), (0, 0))
        self.assertEqual(self._count(), 1)

        # 冷启动时新增的片仍然要被发现
        self._put("草稿/b.mp4")
        cardwatch.reset()
        r2 = self._fresh()
        self.assertEqual((r2["probed"], r2["added"]), (1, 1))
        self.assertEqual(self._count(), 2)

    # ---------- 「文件已不在盘上」 ----------

    def test_mark_missing_flags_only_vanished_files(self) -> None:
        cards = [
            {"rel_path": "草稿/a.mp4"},
            {"rel_path": "草稿/gone.mp4"},
            {"rel_path": "别处/x.mp4"},  # 不归我们管的目录，不标
        ]
        n = cardwatch.mark_missing(cards, {"草稿/a.mp4": (1, 2)}, {"草稿", "生成"})
        self.assertEqual(n, 1)
        self.assertEqual([c["missing"] for c in cards], [False, True, False])

    def test_mark_missing_without_snapshot_is_silent(self) -> None:
        cards = [{"rel_path": "草稿/a.mp4"}]
        self.assertEqual(cardwatch.mark_missing(cards, None, {"草稿"}), 0)
        self.assertFalse(cards[0]["missing"])

    def test_current_snap_exposes_last_pass(self) -> None:
        self.assertIsNone(cardwatch.current_snap(self.root))  # 还没扫过
        self._put("草稿/a.mp4")
        self._fresh()
        snap = cardwatch.current_snap(self.root)
        self.assertIsNotNone(snap)
        self.assertIn("草稿/a.mp4", snap)

    def test_deleted_file_becomes_missing(self) -> None:
        """整条链路：删文件 → 下一轮指纹更新 → 这张卡被标成 missing（但不删）。"""
        p = self._put("草稿/a.mp4")
        self._fresh()
        os.remove(p)
        self._fresh()
        snap = cardwatch.current_snap(self.root)
        cards = [{"rel_path": "草稿/a.mp4"}]
        self.assertEqual(cardwatch.mark_missing(cards, snap, {"草稿", "生成"}), 1)
        self.assertEqual(self._count(), 1)


if __name__ == "__main__":
    unittest.main()
