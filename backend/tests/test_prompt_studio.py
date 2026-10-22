# -*- coding: utf-8 -*-
"""P3 提示词工程（三段式）验收测试。

跑法（backend 目录下）：

    .venv\\Scripts\\python.exe -m unittest tests.test_prompt_studio -v
"""
from __future__ import annotations

import base64
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import config as config_mod
from modules.platform.db import ensure_schema, reset_engines
from modules.prompts import checks, patch, seedance, service, storyboard

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO7Zx3sAAAAASUVORK5CYII="
)
WS = "WS"

STORY = """# 第7集 第2段 分镜脚本（15.0–30.0s）｜15s / 3 镜

> 本段：15.0s ｜ **3 镜**
> 画幅：16:9 横屏，1920×1080
> 承接第1段落点（她推门进来）

---

## 二、分镜主表（21 字段）

| 镜号 | 场次 | 景别 | 摄法 | 机位 | 运镜 | 焦段 | 时长(s) | 画面内容 | 人物动作 | 台词·声音 | 音效·BGM | 转场 | 光线 | 色彩·氛围 | 道具 | 服装·妆造 | 特效·后期 | 节拍·功能 | 风险·备注 | 素材编号 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SC02-SH021 | SC02 云家西屋·内·夜 | MCU | 平视 | 主机位 | 固定 | 长焦 | 5.0 | 她站在门口，身后是放下的帐纱 | 推门→迈一步→停住 | 云天娇说：“我回来了” | AMB·夜虫鸣 | 硬切 | 帐内暖光，光比4:1 | 暖黄低饱和·试探 | 兽医书 | 云天娇-浅蓝碎花睡衣·素颜 | — | HK·钩子 | 不许露皮肤 | A021 |
| SC02-SH022 | SC02 云家西屋·内·夜 | CU | 平视 | 反打位 | 固定 | 长焦 | 5.0 | 他抬头看她，眼镜反着灯光 | 抬眼→喉结动一次 | 顾砚书内心独白：她怎么回来了 | AMB·夜虫鸣压低 | 硬切 | 侧光，眼底一点眼神光 | 暖黄低饱和·意外 | — | 顾砚书-白开襟睡衣·细框眼镜 | — | ES·关系建立 | 眼镜不得反白 | A022 |
| SC02-SH023 | SC02 云家西屋·内·夜 | MS | 平视 | 侧位 | 跟·缓推 | 标准 | 5.0 | 两人隔着那盏灯对望 | 他起身→一步→站直 | — | SFX·布鞋一声 | 硬切 | 同前 | 暖黄低饱和·靠近 | 帐纱 | 云天娇-浅蓝碎花睡衣·素颜；顾砚书-白开襟睡衣·细框眼镜 | — | CL·段末卡点 | 只走一步 | A023 |

---

## 四、风险登记表

| 镜号 | 类型 | 风险 | 等级 | 处置 |
| --- | --- | --- | --- | --- |
| SH021 | 合规 | 门口逆光易露轮廓 | 高 | 锁"隔着衣料、不露皮肤" |
"""


class PromptStudioCase(unittest.TestCase):
    def setUp(self) -> None:
        self._origin = config_mod._config_cache
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-p3-")).resolve()
        self.root = self.tmp / "ws"
        self.root.mkdir()
        (self.root / "分镜").mkdir()
        (self.root / "提示词").mkdir()
        (self.root / "素材").mkdir()
        (self.root / "分镜" / "第7集_第2段_分镜脚本_15-30s.md").write_text(STORY, encoding="utf-8")
        for name in ("45云天娇睡衣 羽.png", "45顾砚书睡衣.png", "云家西院屋晚上-ya.png",
                     "云家西院屋晚上反打-ya.png", "兽医书.png"):
            (self.root / "素材" / name).write_bytes(PNG)
        (self.root / "素材" / "顾砚书.mp3").write_bytes(b"ID3fake")

        self.install()
        ensure_schema(self.root)

        from modules.prompts.api import router

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        reset_engines()
        config_mod._config_cache = self._origin
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install(self) -> None:
        cfg = config_mod.Config(
            workspaces=[config_mod.WorkspaceEntry(
                name=WS, root=str(self.root), folders={"asset": "素材", "storyboard": "分镜"}
            )],
            active=WS,
        )
        config_mod._config_cache = cfg


class TestParser(PromptStudioCase):
    def parse(self, text: str = STORY) -> dict:
        return storyboard.parse_storyboard(text, source_path="分镜/x.md")

    def test_reads_21_field_table(self):
        story = self.parse()
        self.assertEqual(story["status"], "READY")
        self.assertEqual(story["ep"], 7)
        self.assertEqual(story["no"], 2)
        self.assertEqual(len(story["shots"]), 3)
        self.assertEqual(story["range"], {"start": 15.0, "end": 30.0})
        first = story["shots"][0]
        self.assertEqual(first["no"], "SC02-SH021")
        self.assertEqual(first["seq"], 21)
        self.assertEqual(first["duration"], 5.0)
        self.assertEqual(first["start"], 0.0)
        self.assertEqual(story["shots"][2]["end"], 15.0)
        self.assertEqual(story["aspect"], "16:9")
        self.assertEqual(story["resolution"], "1920×1080")

    def test_dialogue_three_styles(self):
        story = self.parse()
        said = [d for s in story["shots"] for d in s["dialogue"]]
        self.assertEqual(len(said), 2)
        self.assertEqual(said[0]["who"], "云天娇")
        self.assertEqual(said[0]["line"], "我回来了")
        self.assertEqual(said[0]["lip"], "有")
        self.assertEqual(said[1]["kind"], "独白")
        self.assertEqual(said[1]["lip"], "无口型")

    def test_missing_column_degrades(self):
        broken = STORY.replace("| 素材编号 |", "| 备注 |")
        story = self.parse(broken)
        self.assertEqual(story["status"], "PARTIAL")
        self.assertIn("MISSING_COLUMN", [i["kind"] for i in story["issues"]])
        self.assertEqual(len(story["shots"]), 3, "缺列不影响其它列")

    def test_no_table(self):
        story = self.parse("# 只有标题\n\n没有任何表格。\n")
        self.assertEqual(story["status"], "NO_TABLE")
        self.assertEqual(story["shots"], [])

    def test_duplicate_shot_number(self):
        dup = STORY.replace("SC02-SH022", "SC02-SH021")
        story = self.parse(dup)
        self.assertIn("DUPLICATE_SHOT", [i["kind"] for i in story["issues"]])

    def test_duration_drift_warns_but_keeps_shots(self):
        drift = STORY.replace("| 5.0 | 她站在门口", "| 9.0 | 她站在门口")
        story = self.parse(drift)
        self.assertIn("DURATION_DRIFT", [i["kind"] for i in story["issues"]])
        self.assertEqual(len(story["shots"]), 3)


class TestRender(PromptStudioCase):
    def test_render_has_three_parts(self):
        out = service.render_segment(config_mod.get_config(), "7-2")
        self.assertIn("## 素材表", out["full"])
        self.assertIn("## 前缀参考素材+风格+环境", out["full"])
        self.assertIn("## 正文", out["full"])
        self.assertIn("## 尾部模板", out["full"])
        self.assertIn("【时间轴】", out["body"])
        self.assertIn("【AI防崩约束词】", out["tail"])
        self.assertIn("[镜1]", out["body"])
        self.assertEqual(out["stats"]["shots"], 3)

    def test_materials_bound_by_name_and_usage(self):
        out = service.render_segment(config_mod.get_config(), "7-2")
        rows = {r["placeholder"]: r for r in out["materials"]["rows"]}
        self.assertEqual(rows["@人物1"]["file"], "45云天娇睡衣 羽.png")
        self.assertEqual(rows["@人物2"]["file"], "45顾砚书睡衣.png")
        self.assertIn("@场景1", rows)
        self.assertIn("@道具1", rows)
        self.assertEqual(rows["@道具1"]["file"], "兽医书.png")
        self.assertTrue(any(r["kind"] == "audio" for r in out["materials"]["rows"]))

    def test_placeholder_written_into_prefix_not_filename(self):
        out = service.render_segment(config_mod.get_config(), "7-2")
        self.assertIn("@人物1", out["prefix"])
        self.assertNotIn("45云天娇睡衣", out["prefix"], "正文只用占位，真实文件名只在素材表")

    def test_unbound_material_reported(self):
        (self.root / "素材" / "45顾砚书睡衣.png").unlink()
        out = service.render_segment(config_mod.get_config(), "7-2")
        kinds = [i["kind"] for i in out["issues"]]
        self.assertIn("MATERIAL_UNBOUND", kinds)

    def test_render_is_deterministic(self):
        cfg = config_mod.get_config()
        a = seedance.render_full(*_story_and_materials(self.root), options={"date": "2026-01-01"})
        b = seedance.render_full(*_story_and_materials(self.root), options={"date": "2026-01-01"})
        self.assertEqual(a["full"], b["full"])
        self.assertTrue(cfg.ws_root())


DIRTY = """# 07_15_01 · 15.0–30.0s（Seedance 三段式）

## 素材表

| 占位 | 文件名 |
| --- | --- |
| @人物1 | 45顾砚书睡衣.png |
| @道具1 | 兽医书.png |

## 分镜时间动作表

| 镜号 | 时间 | 动作 |
| --- | --- | --- |

## 台词表

| 镜号 | 谁 | 台词 | 口型 |
| --- | --- | --- | --- |

## 前缀参考素材+风格+环境

```
【风格】
16:9横屏。
参考 45顾砚书睡衣.png 这张图。
```

## 正文

```
【时间轴】

[镜1]（约0.0秒—5.0秒）中近景，平视，主机位，固定，长焦。
她站在门口，身后是帐纱。
顾砚书内心独白：她怎么回来了
（声音：虫鸣。）
```

## 尾部模板

```
【镜头语言】
【光影氛围】
【声音设计】
无BGM、无字幕、无水印。
【AI防崩约束词】
1. 真人两个。
【合规约束词】
无。
【参考】
视觉素材：@道具1
```
"""


class TestChecks(PromptStudioCase):
    """规则诊断与可应用补丁。"""

    def test_finds_mechanical_problems(self):
        found = checks.check_prompt(DIRTY)
        codes = {f["code"] for f in found}
        self.assertIn("BODY_HAS_DURATION", codes)
        self.assertIn("REAL_FILENAME_IN_TEXT", codes)
        self.assertIn("MONOLOGUE_NO_LIP", codes)
        self.assertIn("MISSING_TAG", codes, "缺【主体描述】/【场景环境】要报出来")
        self.assertTrue(all(f["level"] in ("error", "warn", "info") for f in found))

    def test_fixable_flags_match_fixes(self):
        for f in checks.check_prompt(DIRTY):
            self.assertEqual(f["fixable"], f["fix"] is not None)
            if f["fix"]:
                self.assertLessEqual(f["fix"]["start"], f["fix"]["end"])

    def test_patch_removes_fixable_problems(self):
        found = checks.check_prompt(DIRTY)
        result = patch.build_patch(DIRTY, found)
        self.assertEqual(result["applied"], len(result["hunks"]))
        self.assertGreaterEqual(result["applied"], 3)
        self.assertNotIn("（约0.0秒—5.0秒）", result["patched"])
        self.assertIn("@人物1 这张图", result["patched"])
        self.assertIn("顾砚书内心独白：她怎么回来了（内心独白无口型", result["patched"])
        # 改完不该再报这三类
        left = {f["code"] for f in checks.check_prompt(result["patched"])}
        self.assertNotIn("BODY_HAS_DURATION", left)
        self.assertNotIn("REAL_FILENAME_IN_TEXT", left)
        self.assertNotIn("MONOLOGUE_NO_LIP", left)

    def test_patch_only_touches_selected_codes(self):
        result = patch.build_patch(DIRTY, checks.check_prompt(DIRTY), ["BODY_HAS_DURATION"])
        self.assertEqual([h["code"] for h in result["hunks"]], ["BODY_HAS_DURATION"])
        self.assertIn("45顾砚书睡衣.png", result["patched"], "没选中的问题不许动")

    def test_patch_is_reversible(self):
        """补丁只改命中区间：把 hunk 反着换回去，必须逐字节回到原文。"""
        result = patch.build_patch(DIRTY, checks.check_prompt(DIRTY))
        restored = result["patched"]
        for h in reversed(result["hunks"]):
            at = restored.find(h["after"]) if h["after"] else -1
            self.assertGreaterEqual(at, 0 if h["after"] else -1)
        # 用 rewrite 的校验路径反向还原
        from modules.prompts.rewrite import apply_spans

        spans = []
        delta = 0
        for h in result["hunks"]:
            spans.append((h["start"] + delta, h["start"] + delta + len(h["after"]), h["before"]))
            delta += len(h["after"]) - len(h["before"])
        back, _ = apply_spans(result["patched"], spans)
        self.assertEqual(back, DIRTY)

    def test_story_rules(self):
        story = storyboard.parse_storyboard(STORY, source_path="分镜/x.md")
        found = checks.check_prompt(DIRTY, story)
        codes = {f["code"] for f in found}
        self.assertIn("SHOT_COUNT_MISMATCH", codes, "正文 1 镜 vs 分镜表 3 镜")
        self.assertIn("DIALOGUE_NOT_VERBATIM", codes, "分镜表的台词没进正文")

    def test_api_check_and_patch(self):
        resp = self.client.post("/api/prompts/segments/7-2/check", json={"content": DIRTY})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("summary", resp.json())
        self.assertGreater(resp.json()["summary"]["fixable"], 0)

        patched = self.client.post(
            "/api/prompts/segments/7-2/patch", json={"content": DIRTY}
        )
        self.assertEqual(patched.status_code, 200)
        body = patched.json()
        self.assertGreater(body["applied"], 0)
        self.assertLess(
            body["summary_after"]["error"] + body["summary_after"]["warn"],
            body["summary_before"]["error"] + body["summary_before"]["warn"],
        )

    def test_check_without_content_uses_render(self):
        resp = self.client.post("/api/prompts/segments/7-2/check", json={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["source"], "render")


class TestBindings(PromptStudioCase):
    """人工指定 / 忽略 / 恢复自动这一圈。"""

    def put(self, payload: dict) -> dict:
        resp = self.client.put("/api/prompts/segments/7-2/bindings", json=payload)
        return {"status": resp.status_code, "body": resp.json() if resp.content else None}

    def row(self, placeholder: str) -> dict:
        detail = self.client.get("/api/prompts/segments/7-2").json()
        return next(r for r in detail["materials"]["rows"] if r["placeholder"] == placeholder)

    def test_manual_bind_wins_over_auto(self):
        self.assertEqual(self.row("@人物1")["file"], "45云天娇睡衣 羽.png")
        got = self.put({"placeholder": "@人物1", "file": "素材/兽医书.png", "name": "云天娇"})
        self.assertEqual(got["status"], 200)
        row = self.row("@人物1")
        self.assertEqual(row["source"], "manual")
        self.assertEqual(row["file"], "兽医书.png")
        self.assertEqual(row["note"], "手动指定")
        rendered = self.client.post("/api/prompts/segments/7-2/render", json={}).json()
        self.assertIn("| @人物1 | 兽医书.png |", rendered["tables"]["materials"])

    def test_ignore_keeps_placeholder_and_drops_from_table(self):
        got = self.put({"placeholder": "@音色1", "ignored": True, "name": "云天娇"})
        self.assertEqual(got["status"], 200)
        row = self.row("@音色1")
        self.assertEqual(row["source"], "ignored")
        self.assertEqual(row["file"], "")
        rendered = self.client.post("/api/prompts/segments/7-2/render", json={}).json()
        self.assertNotIn("@音色1", rendered["tables"]["materials"])
        self.assertEqual([i for i in rendered["issues"] if i["field"] == "@音色1"], [])

    def test_clear_returns_to_auto(self):
        self.put({"placeholder": "@人物1", "file": "素材/兽医书.png"})
        self.assertEqual(self.row("@人物1")["source"], "manual")
        got = self.put({"placeholder": "@人物1"})
        self.assertTrue(got["body"]["cleared"])
        row = self.row("@人物1")
        self.assertEqual(row["source"], "auto")
        self.assertEqual(row["file"], "45云天娇睡衣 羽.png")

    def test_path_escape_rejected(self):
        got = self.put({"placeholder": "@人物1", "file": "../../etc/passwd"})
        self.assertEqual(got["status"], 400)

    def test_placeholder_required(self):
        got = self.put({"placeholder": "人物1", "file": "素材/兽医书.png"})
        self.assertEqual(got["status"], 400)

    def test_files_endpoint_filters(self):
        audio = self.client.get("/api/prompts/files?kind=audio").json()["files"]
        self.assertTrue(audio)
        self.assertTrue(all(f["name"].endswith(".mp3") for f in audio))
        images = self.client.get("/api/prompts/files?kind=image").json()["files"]
        self.assertTrue(all(not f["name"].endswith(".mp3") for f in images))
        hit = self.client.get("/api/prompts/files?kind=image&q=兽医").json()["files"]
        self.assertEqual([f["name"] for f in hit], ["兽医书.png"])


class TestApi(PromptStudioCase):
    def test_list_segments(self):
        resp = self.client.get("/api/prompts/segments")
        self.assertEqual(resp.status_code, 200)
        segs = resp.json()["segments"]
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["id"], "7-2")
        self.assertEqual(segs[0]["shots"], 3)
        self.assertEqual(segs[0]["status"], "READY")

    def test_render_endpoint(self):
        resp = self.client.post("/api/prompts/segments/7-2/render", json={})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["stats"]["shots"], 3)
        self.assertIn("【风格】", body["prefix"])

    def test_unknown_segment_404(self):
        resp = self.client.get("/api/prompts/segments/999-9")
        self.assertEqual(resp.status_code, 404)

    def test_save_then_conflict(self):
        rendered = self.client.post("/api/prompts/segments/7-2/render", json={}).json()
        first = self.client.post(
            "/api/prompts/segments/7-2/save",
            json={"content": rendered["full"]},
        )
        self.assertEqual(first.status_code, 200)
        path = self.root / first.json()["path"]
        self.assertTrue(path.is_file())
        self.assertIn("【时间轴】", path.read_text(encoding="utf-8"))

        again = self.client.post(
            "/api/prompts/segments/7-2/save",
            json={"content": rendered["full"]},
        )
        self.assertEqual(again.status_code, 409, "同名文件不覆盖，先问一句")

        forced = self.client.post(
            "/api/prompts/segments/7-2/save",
            json={"content": rendered["full"], "overwrite": True},
        )
        self.assertEqual(forced.status_code, 200)

    def test_filename_cannot_escape_workspace(self):
        bad = self.client.post(
            "/api/prompts/segments/7-2/save",
            json={"content": "x", "filename": "../escape.md"},
        )
        self.assertEqual(bad.status_code, 400)

    def test_context_inherits_style(self):
        (self.root / "提示词" / "01_15_01_000-015s_参考体例版.md").write_text(
            "```\n【风格】\n16:9横屏，1985年中国苏北农村的年代剧，UE5。\n\n"
            "【场景环境】\n1985年，苏北农村一间住房的夜，室内。\n```\n",
            encoding="utf-8",
        )
        ctx = self.client.get("/api/prompts/context").json()
        self.assertIn("1985年中国苏北农村", ctx["style"])
        self.assertEqual(ctx["era_region"], "1985年中国苏北农村")
        self.assertIn("苏北农村", ctx["scene"])


def _story_and_materials(root: Path):
    parsed = storyboard.read_storyboard(root / "分镜" / "第7集_第2段_分镜脚本_15-30s.md", root)
    parsed.pop("text", None)
    files = [p for p in (root / "素材").iterdir() if p.is_file()]
    bound = __import__("modules.prompts.materials", fromlist=["x"]).resolve_materials(parsed, files)
    return parsed, bound


if __name__ == "__main__":
    unittest.main()
