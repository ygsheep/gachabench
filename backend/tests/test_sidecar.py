# -*- coding: utf-8 -*-
"""提示词 sidecar 与适用区间的验收测试。

跑法（backend 目录下，用 3.12 基础解释器 + venv 的 site-packages）：

    PYTHONPATH=".;.venv/Lib/site-packages" \
      "C:/Users/young.YOUNG/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe" \
      -m unittest tests.test_sidecar -v

覆盖重点：
- 外部 Agent 写的 sidecar 可能是任何形状，**坏文件不拖垮整轮索引**；
- 未识别的顶层键不丢（进 extra），超长文本截断但有记录；
- sidecar 文件本身**不进资产索引**（否则资产页多出一堆 json）；
- 区间：0 对 = 隐式全局；多对 = 中途被顶替又用回来；`suggest_close` 只提建议不写库。
"""
from __future__ import annotations

import base64
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

import config as config_mod
from modules.assets import sidecar, windows
from modules.assets.indexer import scan_workspace
from modules.assets.models import AssetPrompt, IndexAsset
from modules.platform.db import ensure_schema, reset_engines, session_scope

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Zx3sAAAAASUVORK5CYII="
)


class TestSidecarNames(unittest.TestCase):
    def test_pair_names(self):
        self.assertEqual(sidecar.sidecar_for("a.png"), "a.prompt.json")
        self.assertEqual(sidecar.sidecar_for("素材/白兴学_参考.PNG"), "白兴学_参考.prompt.json")
        self.assertEqual(sidecar.media_for("a.prompt.json"), "a")

    def test_is_sidecar(self):
        self.assertTrue(sidecar.is_sidecar("a.prompt.json"))
        self.assertTrue(sidecar.is_sidecar("A.PROMPT.JSON"))
        self.assertFalse(sidecar.is_sidecar("prompt.json"))
        self.assertFalse(sidecar.is_sidecar("a.png"))


class TestParseObj(unittest.TestCase):
    def test_full_doc(self):
        sc = sidecar.parse_obj(
            {
                "schema": 1,
                "subject": "白兴学",
                "variant": "国漫3D_四视图",
                "prompt": "少年，国漫3D，四视图",
                "negative": "模糊",
                "caption": "白衣少年正面站立",
                "params": {"steps": 30},
            }
        )
        self.assertTrue(sc.ok)
        self.assertEqual(sc.subject, "白兴学")
        self.assertEqual(sc.variant, "国漫3D_四视图")
        self.assertEqual(sc.caption, "白衣少年正面站立")
        self.assertEqual(sc.params, {"steps": 30})
        self.assertEqual(sc.problems, [])

    def test_unknown_keys_go_to_extra(self):
        sc = sidecar.parse_obj({"prompt": "x", "agent": "nano-banana", "seed": 7})
        self.assertEqual(sc.extra, {"agent": "nano-banana", "seed": 7})

    def test_wrong_types_degrade_not_raise(self):
        sc = sidecar.parse_obj({"prompt": ["不是字符串"], "params": "不是对象", "caption": 123})
        self.assertEqual(sc.prompt, "")
        self.assertEqual(sc.params, {})
        self.assertEqual(sc.caption, "123")
        self.assertTrue(any("prompt" in p for p in sc.problems))
        self.assertTrue(any("params" in p for p in sc.problems))

    def test_non_dict_toplevel(self):
        sc = sidecar.parse_obj(["a", "b"])
        self.assertFalse(sc.ok)
        self.assertTrue(sc.problems)

    def test_bad_schema_is_reported_but_tolerated(self):
        sc = sidecar.parse_obj({"schema": "1", "prompt": "x"})
        self.assertEqual(sc.schema_version, 1)
        self.assertTrue(sc.problems)

    def test_long_text_is_truncated(self):
        sc = sidecar.parse_obj({"prompt": "あ" * (sidecar.MAX_PROMPT + 10)})
        self.assertEqual(len(sc.prompt), sidecar.MAX_PROMPT)
        self.assertTrue(any("截断" in p for p in sc.problems))

    def test_caption_alone_is_enough(self):
        self.assertTrue(sidecar.parse_obj({"caption": "描述"}).ok)
        self.assertFalse(sidecar.parse_obj({}).ok)


class TestRead(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="gb-side-"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def put(self, name: str, text: str) -> Path:
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_missing_file(self):
        data, problems = sidecar.read(self.dir / "nope.prompt.json")
        self.assertIsNone(data)
        self.assertTrue(problems)

    def test_broken_json_reports_position(self):
        p = self.put("a.prompt.json", '{"prompt": ')
        data, problems = sidecar.read(p)
        self.assertIsNone(data)
        self.assertIn("JSON", problems[0])

    def test_bom_and_empty(self):
        p = self.put("b.prompt.json", '\ufeff{"prompt": "x"}')
        data, _ = sidecar.read(p)
        self.assertEqual(data.prompt, "x")
        p2 = self.put("c.prompt.json", "   ")
        self.assertIsNone(sidecar.read(p2)[0])


class TestNormalizeWindows(unittest.TestCase):
    def test_accepts_all_shapes(self):
        got = windows.normalize_windows(["5-9", "10", (12, 14), {"seg_from": 20, "seg_to": None}])
        self.assertEqual(
            got,
            [
                {"seg_from": 5, "seg_to": 9},
                {"seg_from": 10, "seg_to": None},
                {"seg_from": 12, "seg_to": 14},
                {"seg_from": 20, "seg_to": None},
            ],
        )

    def test_drops_invalid(self):
        self.assertEqual(
            windows.normalize_windows(["9-5", "0-3", "abc", None, {"seg_to": 4}]),
            [],
        )

    def test_dedupes_identical_pairs_only(self):
        # `5-6` 与 `5` 是两条不同的区间（5-∞），都该留下：
        # 仲裁时 5-6 在 5~6 段胜出，7 段起由 5-∞ 接管。只去掉完全相同的对。
        got = windows.normalize_windows(["10-12", "5-6", "10-12", "5"])
        self.assertEqual(
            [(w["seg_from"], w["seg_to"]) for w in got], [(5, 6), (5, None), (10, 12)]
        )

    def test_normalizes_single_digit(self):
        self.assertEqual(windows.normalize_windows(["1-3"])[0]["seg_from"], 1)


class SidecarDbCase(unittest.TestCase):
    """真扫一个临时工作区（会跑 Alembic 0005，顺带验迁移 SQL）。"""

    def setUp(self) -> None:
        self._origin = config_mod._config_cache
        config_mod._config_cache = config_mod.Config()
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-p4-")).resolve()
        self.root = self.tmp / "ws"
        (self.root / "素材").mkdir(parents=True)
        ensure_schema(self.root)

    def tearDown(self) -> None:
        config_mod._config_cache = self._origin
        try:
            reset_engines()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel: str, data: bytes | str = PNG_1X1) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            p.write_text(data, encoding="utf-8")
        else:
            p.write_bytes(data)
        return p

    def sidecar_doc(self, **kw) -> str:
        return json.dumps(kw, ensure_ascii=False)

    # ------------------------------------------------------------------

    def test_ingest_reads_caption_and_prompt(self):
        self.write("素材/白兴学_参考.png")
        self.write(
            "素材/白兴学_参考.prompt.json",
            self.sidecar_doc(prompt="白衣少年", caption="正面全身", params={"steps": 30}),
        )
        scan_workspace(self.root, force=True)

        with session_scope(self.root) as s:
            asset = s.scalar(select(IndexAsset).where(IndexAsset.name == "白兴学_参考.png"))
            self.assertIsNotNone(asset)
            row = s.get(AssetPrompt, asset.id)
            self.assertIsNotNone(row)
            self.assertEqual(row.caption, "正面全身")
            self.assertEqual(row.prompt, "白衣少年")
            self.assertEqual(json.loads(row.params_json), {"steps": 30})
            self.assertEqual(row.sidecar_path, "素材/白兴学_参考.prompt.json")

    def test_sidecar_json_is_not_an_asset(self):
        self.write("素材/白兴学_参考.png")
        self.write("素材/白兴学_参考.prompt.json", self.sidecar_doc(prompt="x"))
        scan_workspace(self.root, force=True)
        with session_scope(self.root) as s:
            names = [a.name for a in s.scalars(select(IndexAsset)).all()]
            self.assertEqual(names, ["白兴学_参考.png"])

    def test_asset_without_sidecar_has_no_row(self):
        self.write("素材/刘强.png")
        scan_workspace(self.root, force=True)
        with session_scope(self.root) as s:
            self.assertEqual(s.scalars(select(AssetPrompt)).all(), [])

    def test_broken_sidecar_does_not_break_scan(self):
        self.write("素材/刘强.png")
        self.write("素材/刘强.prompt.json", "{ 这不是 JSON")
        stats = scan_workspace(self.root, force=True)
        self.assertEqual(stats["sidecar"]["added"], 0)
        with session_scope(self.root) as s:
            self.assertIsNotNone(
                s.scalar(select(IndexAsset).where(IndexAsset.name == "刘强.png"))
            )

    def test_sidecar_removed_drops_cache(self):
        self.write("素材/刘强.png")
        side = self.write("素材/刘强.prompt.json", self.sidecar_doc(prompt="x"))
        scan_workspace(self.root, force=True)
        with session_scope(self.root) as s:
            self.assertEqual(len(s.scalars(select(AssetPrompt)).all()), 1)
        side.unlink()
        scan_workspace(self.root, force=True)
        with session_scope(self.root) as s:
            self.assertEqual(s.scalars(select(AssetPrompt)).all(), [])

    def test_resolve_picks_latest_start(self):
        self.write("素材/教室_破坏_参考.png")
        self.write("素材/教室_修复_参考.png")
        scan_workspace(self.root, force=True)
        with session_scope(self.root) as s:
            broken = s.scalar(select(IndexAsset).where(IndexAsset.name == "教室_破坏_参考.png"))
            fixed = s.scalar(select(IndexAsset).where(IndexAsset.name == "教室_修复_参考.png"))
            windows.set_windows(s, broken.id, ["5-9"])
            windows.set_windows(s, fixed.id, ["10-"])
            s.flush()

            self.assertEqual(windows.resolve(s, "教室", 7)["asset"].id, broken.id)
            self.assertEqual(windows.resolve(s, "教室", 12)["asset"].id, fixed.id)
            self.assertIsNone(windows.resolve(s, "教室", 4))

    def test_implicit_window_is_globally_valid(self):
        # 存量素材全都没有区间 → 等价于「一直可用」，一条都不用改
        self.write("素材/白兴学_参考.png")
        scan_workspace(self.root, force=True)
        with session_scope(self.root) as s:
            hit = windows.resolve(s, "白兴学", 1)
            self.assertIsNotNone(hit)
            self.assertTrue(hit["implicit"])
            self.assertIsNotNone(windows.resolve(s, "白兴学", 99))

    def test_explicit_window_beats_implicit(self):
        self.write("素材/教室_参考.png")          # 无区间 = 一直可用
        self.write("素材/教室_破坏_参考.png")      # 5 段起
        scan_workspace(self.root, force=True)
        with session_scope(self.root) as s:
            broken = s.scalar(select(IndexAsset).where(IndexAsset.name == "教室_破坏_参考.png"))
            windows.set_windows(s, broken.id, ["5-"])
            s.flush()
            self.assertEqual(windows.resolve(s, "教室", 7)["asset"].id, broken.id)
            self.assertFalse(windows.resolve(s, "教室", 7)["implicit"])

    def test_multiple_ranges_for_one_asset(self):
        self.write("素材/教室_破坏_参考.png")
        scan_workspace(self.root, force=True)
        with session_scope(self.root) as s:
            a = s.scalar(select(IndexAsset))
            got = windows.set_windows(s, a.id, ["5-9", "16-"])
            self.assertEqual([(w.seg_from, w.seg_to) for w in got], [(5, 9), (16, None)])
            self.assertEqual(windows.resolve(s, "教室", 6)["asset"].id, a.id)
            self.assertIsNone(windows.resolve(s, "教室", 12))

    def test_empty_windows_restores_implicit(self):
        self.write("素材/教室_破坏_参考.png")
        scan_workspace(self.root, force=True)
        with session_scope(self.root) as s:
            a = s.scalar(select(IndexAsset))
            windows.set_windows(s, a.id, ["5-9"])
            windows.set_windows(s, a.id, [])
            s.flush()
            self.assertTrue(windows.resolve(s, "教室", 1)["implicit"])

    def test_suggest_close_never_writes(self):
        self.write("素材/教室_破坏_参考.png")
        self.write("素材/教室_修复_参考.png")
        scan_workspace(self.root, force=True)
        with session_scope(self.root) as s:
            broken = s.scalar(select(IndexAsset).where(IndexAsset.name == "教室_破坏_参考.png"))
            fixed = s.scalar(select(IndexAsset).where(IndexAsset.name == "教室_修复_参考.png"))
            windows.set_windows(s, broken.id, ["5-"])
            windows.set_windows(s, fixed.id, ["10-"])
            s.flush()

            tips = windows.suggest_close(s, fixed.id, 10)
            self.assertEqual(len(tips), 1)
            self.assertEqual(tips[0]["asset_id"], broken.id)
            self.assertEqual(tips[0]["suggest_seg_to"], 9)
            # 建议只是建议：破坏 的区间没有被改
            self.assertEqual(windows.windows_for(s, broken.id)[0].seg_to, None)

    def test_suggest_close_ignores_implicit_globals(self):
        # 校服/日常服是两条并行轨道，不该因为新来一个就被收口
        self.write("素材/白兴学_日常_参考.png")
        self.write("素材/白兴学_校服_参考.png")
        scan_workspace(self.root, force=True)
        with session_scope(self.root) as s:
            uniform = s.scalar(select(IndexAsset).where(IndexAsset.name == "白兴学_校服_参考.png"))
            windows.set_windows(s, uniform.id, ["10-"])
            s.flush()
            self.assertEqual(windows.suggest_close(s, uniform.id, 10), [])


if __name__ == "__main__":
    unittest.main()
