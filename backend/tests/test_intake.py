# -*- coding: utf-8 -*-
"""收编器验收测试。

跑法（backend 目录下）：

    PYTHONPATH=".;.venv/Lib/site-packages" \
      "C:/Users/young.YOUNG/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe" \
      -m unittest tests.test_intake -v

覆盖重点：
- 只改**能证实**的名字（`17_第17段_1of3_00001.mp4` → `17.1_成片.mp4`），
  解析不出段号就原样保留，绝不猜；
- 收编 = **复制**，源文件不动；
- 指纹让同一份文件不会被收两次；源删掉后指纹自清；
- 重名不覆盖，依次 `_02`；
- 监听工作区自身 → 直接拒绝（否则无限增殖）；
- 非媒体文件（`.prompt.json` / `.md`）不被当成产物。
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import config as config_mod
import intake
import workspaces

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 56
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class IntakeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-intake-")).resolve()
        self.root = self.tmp / "ws"
        self.draft = self.root / "草稿"
        self.idx = self.root / ".gachabench"
        self.src = self.tmp / "外部目录"
        self.draft.mkdir(parents=True)
        self.src.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, name: str, data: bytes = MP4, where: Path | None = None) -> Path:
        p = (where or self.src) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def run_intake(self, dirs: list[str] | None = None) -> dict:
        return intake.ingest_dirs(
            dirs if dirs is not None else [str(self.src)],
            self.draft,
            self.idx,
            workspace_root=self.root,
        )

    def names(self) -> list[str]:
        return sorted(p.name for p in self.draft.iterdir())


class TestPlanName(unittest.TestCase):
    def test_comfy_dump_is_renamed(self):
        self.assertEqual(
            intake.plan_name("17_第17段_1of3_00001.mp4"), ("17.1_成片.mp4", "legacy")
        )

    def test_seg_without_chunk_gets_bare_name(self):
        self.assertEqual(
            intake.plan_name("18_正片输出.mp4"), ("18_成片.mp4", "legacy")
        )

    def test_template_name_is_kept(self):
        self.assertEqual(intake.plan_name("19.2_成片.mp4"), ("19.2_成片.mp4", "template"))

    def test_asset_name_is_kept(self):
        self.assertEqual(
            intake.plan_name("白兴学_参考.png"), ("白兴学_参考.png", "asset")
        )

    def test_unparseable_seg_is_kept_verbatim(self):
        # 🔴 不猜：没有段号线索，就让它带着原名躺进草稿，由人指定
        self.assertEqual(
            intake.plan_name("output_00042.mp4"), ("output_00042.mp4", "asset")
        )


class TestIngest(IntakeCase):
    def test_copies_and_renames(self):
        src = self.put("17_第17段_1of3_00001.mp4")
        stats = self.run_intake()
        self.assertEqual(self.names(), ["17.1_成片.mp4"])
        self.assertEqual(stats["copied"], 1)
        self.assertEqual(stats["renamed"], 1)
        self.assertTrue(src.exists(), "收编绝不能动源文件")

    def test_second_run_is_noop(self):
        self.put("17_第17段_1of3_00001.mp4")
        self.run_intake()
        stats = self.run_intake()
        self.assertEqual(stats["copied"], 0)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(self.names(), ["17.1_成片.mp4"])

    def test_changed_file_is_reingested(self):
        p = self.put("17_第17段_1of3_00001.mp4")
        self.run_intake()
        p.write_bytes(MP4 + b"more")
        stats = self.run_intake()
        self.assertEqual(stats["copied"], 1)
        self.assertEqual(self.names(), ["17.1_成片.mp4", "17.1_成片_02.mp4"])

    def test_state_file_is_written_and_pruned(self):
        p = self.put("17_第17段_1of3_00001.mp4")
        self.run_intake()
        state = intake.load_state(self.idx)
        self.assertEqual(len(state), 1)
        self.assertEqual(next(iter(state.values()))["dst"], "草稿/17.1_成片.mp4")
        p.unlink()
        self.run_intake()
        self.assertEqual(intake.load_state(self.idx), {})

    def test_same_name_twice_does_not_overwrite(self):
        self.put("01.1_成片.mp4")
        self.run_intake()
        other = self.tmp / "另一个目录"
        other.mkdir()
        self.put("01.1_成片.mp4", where=other)
        self.run_intake([str(other)])
        self.assertEqual(self.names(), ["01.1_成片.mp4", "01.1_成片_02.mp4"])

    def test_non_media_files_ignored(self):
        self.put("17_第17段_1of3_00001.prompt.json", b"{}")
        self.put("说明.md", b"# hi")
        stats = self.run_intake()
        self.assertEqual(stats["copied"], 0)
        self.assertEqual(self.names(), [])

    def test_recurses_into_subfolders(self):
        self.put("a/b/17_第17段_2of3_00001.mp4")
        self.run_intake()
        self.assertEqual(self.names(), ["17.2_成片.mp4"])

    def test_refuses_watch_dir_inside_workspace(self):
        # 监听工作区自己 = 把 草稿/ 里的片子再收一遍，下一轮再收一遍，无限增殖
        self.put("x.mp4", where=self.draft)
        stats = self.run_intake([str(self.draft)])
        self.assertEqual(stats["copied"], 0)
        self.assertEqual(stats["dirs"], 0)
        self.assertTrue(any("工作区内部" in p for p in stats["problems"]))
        self.assertEqual(self.names(), ["x.mp4"])

    def test_missing_dir_is_reported(self):
        stats = self.run_intake([str(self.tmp / "不存在")])
        self.assertEqual(stats["scanned"], 0)
        self.assertTrue(any("不存在" in p for p in stats["problems"]))

    def test_unclaimed_counts_only_media_without_seg(self):
        self.put("output_00042.mp4")
        self.put("白兴学_参考.png", PNG)
        stats = self.run_intake()
        self.assertEqual(stats["unclaimed"], 1)
        self.assertEqual(sorted(self.names()), ["output_00042.mp4", "白兴学_参考.png"])

    def test_no_dirs_is_free(self):
        stats = intake.ingest_dirs([], self.draft, self.idx, workspace_root=self.root)
        self.assertEqual(stats["scanned"], 0)
        self.assertFalse(intake.state_path(self.idx).exists())


class TestIntakeWorkspace(IntakeCase):
    def test_requires_draft_folder(self):
        import config as config_mod

        ws = config_mod.WorkspaceEntry(name="WS", root=str(self.root), watch_dirs=[str(self.src)])
        cfg = config_mod.Config(workspaces=[ws], active="WS")
        self.put("17_第17段_1of3_00001.mp4")
        stats = intake.intake_workspace(cfg, ws)
        self.assertEqual(stats["copied"], 1)
        self.assertTrue((self.draft / "17.1_成片.mp4").exists())

    def test_no_watch_dirs_returns_early(self):
        import config as config_mod

        ws = config_mod.WorkspaceEntry(name="WS", root=str(self.root))
        cfg = config_mod.Config(workspaces=[ws], active="WS")
        stats = intake.intake_workspace(cfg, ws)
        self.assertEqual(stats["dirs"], 0)
        self.assertFalse(intake.state_path(self.idx).exists())


class TestSetWatchDirs(unittest.TestCase):
    """配置写入必须隔离：`set_config` 换成「只更内存、不落盘」的替身，
    否则会写进用户真实的 backend/config.json。"""

    def setUp(self) -> None:
        self._origin_cache = config_mod._config_cache
        self._origin_set = workspaces.set_config
        workspaces.set_config = lambda cfg: setattr(config_mod, "_config_cache", cfg)
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-watch-")).resolve()
        self.a = self.tmp / "a"
        self.b = self.tmp / "b"
        self.a.mkdir()
        self.b.mkdir()
        config_mod._config_cache = config_mod.Config(
            workspaces=[
                config_mod.WorkspaceEntry(name="WS", root=str(self.tmp / "ws"))
            ],
            active="WS",
        )

    def tearDown(self) -> None:
        workspaces.set_config = self._origin_set
        config_mod._config_cache = self._origin_cache
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_normalizes_to_absolute(self):
        ws = workspaces.set_watch_dirs("WS", [str(self.a), str(self.b)])
        self.assertEqual(ws.watch_dirs, [str(self.a), str(self.b)])

    def test_rejects_relative_path(self):
        with self.assertRaises(workspaces.WorkSpaceError):
            workspaces.set_watch_dirs("WS", ["某个相对目录"])

    def test_rejects_missing_dir(self):
        with self.assertRaises(workspaces.WorkSpaceError):
            workspaces.set_watch_dirs("WS", [str(self.tmp / "不存在")])

    def test_dedupes(self):
        ws = workspaces.set_watch_dirs("WS", [str(self.a), str(self.a) + "/", ""])
        self.assertEqual(ws.watch_dirs, [str(self.a)])

    def test_caps_count(self):
        dirs = []
        for i in range(25):
            d = self.tmp / f"d{i:02d}"
            d.mkdir()
            dirs.append(str(d))
        with self.assertRaises(workspaces.WorkSpaceError):
            workspaces.set_watch_dirs("WS", dirs)

    def test_empty_clears(self):
        workspaces.set_watch_dirs("WS", [str(self.a)])
        ws = workspaces.set_watch_dirs("WS", [])
        self.assertEqual(ws.watch_dirs, [])

    def test_unknown_workspace(self):
        with self.assertRaises(workspaces.WorkSpaceError):
            workspaces.set_watch_dirs("NOPE", [])


if __name__ == "__main__":
    unittest.main()
