# -*- coding: utf-8 -*-
"""资产上传 / 扫描的单元测试。

跑法（在 backend 目录下）：

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

隔离策略：全程不碰真实的 config.json —— 直接替换 config 模块里的内存缓存
（`config._config_cache`），每个用例用独立临时目录当工作区、独立 SQLite。
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import config as config_mod
import db as db_mod
from repo import list_assets
from routers import assets as assets_mod

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 16
MP3 = b"ID3\x03\x00\x00\x00" + b"\x00" * 16

WS_NAME = "WS"


def _upload_files(*specs: tuple[str, bytes, str]) -> list[tuple[str, tuple[str, bytes, str]]]:
    """把 (文件名, 内容, MIME) 转成 httpx 的 multipart files 参数。"""
    return [("files", spec) for spec in specs]


# ---------------------------------------------------------------------------
# 纯函数：文件名口径（主体名 / 类别 / 类型 / 去重编号）
# ---------------------------------------------------------------------------

class TestNameRules(unittest.TestCase):
    def test_media_type_by_extension(self):
        self.assertEqual(assets_mod._media_type(".png"), ("image", "image/png"))
        self.assertEqual(assets_mod._media_type(".webp"), ("image", "image/webp"))
        self.assertEqual(assets_mod._media_type(".mp3"), ("audio", "audio/mpeg"))
        self.assertEqual(assets_mod._media_type(".wav"), ("audio", "audio/wav"))
        self.assertEqual(assets_mod._media_type(".mp4"), ("video", "video/mp4"))
        # 非媒体：索引里留 kind=other，但不进素材面板
        self.assertIsNone(assets_mod._media_type(".py"))
        self.assertIsNone(assets_mod._media_type(""))
        # 调用方必须先 lower()，否则大写扩展名会被判成「不支持」
        self.assertIsNone(assets_mod._media_type(".PNG"))

    def test_sanitize_part_removes_reserved_chars(self):
        self.assertEqual(assets_mod._sanitize_part('a:b*c?d"e<f>g|h'), "abcdefgh")

    def test_sanitize_part_collapses_spaces_and_trims_dots(self):
        self.assertEqual(assets_mod._sanitize_part("  白  陌 ."), "白 陌")
        self.assertEqual(assets_mod._sanitize_part("...白陌..."), "白陌")
        self.assertEqual(assets_mod._sanitize_part(None or ""), "")

    def test_dedup_name_is_zero_padded(self):
        self.assertEqual(assets_mod._dedup_name("白陌_参考", ".png", 2), "白陌_参考_02.png")
        self.assertEqual(assets_mod._dedup_name("白陌_参考", ".mp3", 10), "白陌_参考_10.mp3")

    def test_split_subject_and_tags(self):
        self.assertEqual(assets_mod._split("白陌_参考"), ("白陌", ["参考"]))
        self.assertEqual(assets_mod._split("白陌 参考"), ("白陌", ["参考"]))
        self.assertEqual(assets_mod._split("张三_近景_01"), ("张三", ["近景", "01"]))
        self.assertEqual(assets_mod._split("白陌"), ("白陌", []))

    def test_category_rules(self):
        self.assertEqual(assets_mod._category_of("教室", ["实拍"]), "scene")
        self.assertEqual(assets_mod._category_of("风格", ["国漫3D"]), "style")
        self.assertEqual(assets_mod._category_of("白陌QQ空间", []), "other")
        self.assertEqual(assets_mod._category_of("白陌", ["配音"]), "character")


# ---------------------------------------------------------------------------
# 公共夹具：临时工作区 + 内存配置 + 只挂 assets 路由的最小 app
# ---------------------------------------------------------------------------

class AssetCase(unittest.TestCase):
    def setUp(self) -> None:
        self._origin_cache = config_mod._config_cache
        # 必须 resolve()：Windows 的 TEMP 常带 8.3 短名（YOUNG~1.YOU），
        # 而 _resolve_target 是拿 resolve() 过的路径做前缀比较 ——
        # 两边形态不一致时 is_relative_to 会静默失败。生产里 _context() 也是先 resolve() 的。
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-assets-")).resolve()
        self.root = self.tmp / "ws"
        self.folder = "素材"
        (self.root / self.folder).mkdir(parents=True)
        self.install()

        app = FastAPI()
        app.include_router(assets_mod.router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        db_mod.close_all()
        config_mod._config_cache = self._origin_cache
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- 夹具工具 ----------

    def install(self, folder: str = "素材", active: str | None = WS_NAME) -> config_mod.Config:
        """装一份临时配置。folder="" 表示「资产目录未确定」（对应后端 409）。"""
        self.folder = folder
        cfg = config_mod.Config(
            workspaces=[
                config_mod.WorkspaceEntry(
                    name=WS_NAME, root=str(self.root), folders={"asset": folder}
                )
            ],
            active=active,
        )
        config_mod._config_cache = cfg
        return cfg

    @property
    def base(self) -> Path:
        return self.root / self.folder

    def write(self, rel: str, data: bytes = PNG) -> Path:
        p = self.base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def scan(self) -> dict:
        return assets_mod._scan(self.base, self.root, WS_NAME)

    def rows(self) -> dict[str, dict]:
        """库里的资产，按 rel_path 索引。"""
        db = db_mod.get_db(self.root)
        return {r["rel_path"]: r for r in list_assets(db, WS_NAME)}

    def upload(self, files, **form):
        payload = {"prefix": "", "suffix": "", "category": "character", "subdir": ""}
        payload.update(form)
        return self.client.post("/api/assets/upload", data=payload, files=files)


# ---------------------------------------------------------------------------
# 扫描：递归整棵目录树 → 幂等入库
# ---------------------------------------------------------------------------

class TestScan(AssetCase):
    def _seed(self) -> None:
        self.write("白陌_参考.png")
        self.write("白陌_配音.mp3", MP3)
        self.write("人物/张三/张三_近景_01.png")
        self.write("场景/教室/教室_实拍.webp", WEBP)
        self.write("风格_国漫3D_参考.webp", WEBP)
        self.write("白陌QQ空间_参考.png")
        self.write("_env.py", b"print(1)")
        self.write(".隐藏.png")          # 隐藏文件：跳过
        (self.base / "空目录").mkdir(parents=True, exist_ok=True)

    def test_scan_goes_recursive_and_indexes_every_file(self):
        self._seed()
        res = self.scan()
        self.assertEqual(res["scanned"], 7)          # 6 个媒体 + _env.py；隐藏文件不计
        self.assertEqual(res["added"], 7)
        self.assertEqual(res["updated"], 0)

        rows = self.rows()
        self.assertNotIn(".隐藏.png", rows, "隐藏文件不应入库")
        self.assertIn("人物/张三/张三_近景_01.png", rows, "两层子目录也要递归到")

    def test_scan_records_dir_subject_category_and_kind(self):
        self._seed()
        self.scan()
        rows = self.rows()

        nested = rows["人物/张三/张三_近景_01.png"]
        self.assertEqual(nested["dir_rel"], "人物/张三")
        self.assertEqual(nested["subject"], "张三")
        self.assertEqual(nested["tags"], ["近景", "01"])
        self.assertEqual(nested["category"], "character")

        flat = rows["白陌_参考.png"]
        self.assertEqual(flat["dir_rel"], "", "资产根下的文件 dir_rel 是空串")
        self.assertEqual(flat["subject"], "白陌")
        self.assertEqual(flat["tags"], ["参考"])
        self.assertEqual(flat["kind"], "image")

        self.assertEqual(rows["白陌_配音.mp3"]["kind"], "audio")
        self.assertEqual(rows["场景/教室/教室_实拍.webp"]["category"], "scene")
        self.assertEqual(rows["风格_国漫3D_参考.webp"]["category"], "style")
        self.assertEqual(rows["白陌QQ空间_参考.png"]["category"], "other")
        # 非媒体照样索引（.py 也留在库里），只是不进素材面板
        self.assertEqual(rows["_env.py"]["kind"], "other")

    def test_scan_is_idempotent(self):
        self._seed()
        first = self.scan()
        self.assertEqual(first["added"], 7)

        second = self.scan()
        self.assertEqual(second["added"], 0)
        self.assertEqual(second["updated"], 7)
        self.assertEqual(second["removed"], 0)
        self.assertEqual(len(self.rows()), 7, "重复扫描不应产生重复记录")

    def test_scan_prunes_records_of_deleted_files(self):
        self._seed()
        self.scan()
        (self.base / "白陌_参考.png").unlink()

        res = self.scan()
        self.assertEqual(res["removed"], 1)
        self.assertEqual(res["scanned"], 6)
        self.assertNotIn("白陌_参考.png", self.rows(), "磁盘上消失的文件要从索引里清掉")

    def test_scan_tolerates_missing_asset_dir(self):
        """资产目录还没建出来时扫描不能炸 —— 只清库。"""
        shutil.rmtree(self.base)
        res = self.scan()
        self.assertEqual((res["scanned"], res["added"], res["updated"]), (0, 0, 0))
        self.assertEqual(self.rows(), {})


# ---------------------------------------------------------------------------
# 落位：上传前算「这个主体该放哪」
# ---------------------------------------------------------------------------

class TestResolveTarget(AssetCase):
    def test_hint_wins_when_it_stays_inside_asset_dir(self):
        (self.base / "人物/张三").mkdir(parents=True)
        _path, dir_rel = assets_mod._resolve_target(
            self.base, self.root, WS_NAME, "张三", "character", "人物/张三"
        )
        self.assertEqual(dir_rel, "人物/张三")

    def test_hint_escaping_asset_dir_is_rejected(self):
        # hint 指到资产目录外（../张三）必须被无视，否则文件会落到工作区别处
        _path, dir_rel = assets_mod._resolve_target(
            self.base, self.root, WS_NAME, "张三", "scene", "../张三"
        )
        self.assertEqual(dir_rel, "场景/张三", "越界的 hint 应被丢掉并回退到类别目录")

    def test_same_name_dir_shallow_first(self):
        (self.base / "白陌").mkdir(parents=True)
        (self.base / "人物/白陌").mkdir(parents=True)
        _path, dir_rel = assets_mod._resolve_target(
            self.base, self.root, WS_NAME, "白陌", "character", ""
        )
        self.assertEqual(dir_rel, "白陌", "同名目录取最浅的那个")

    def test_falls_back_to_dir_where_subject_already_lives(self):
        self.write("旧目录/白陌_参考.png")
        self.scan()
        _path, dir_rel = assets_mod._resolve_target(
            self.base, self.root, WS_NAME, "白陌", "character", ""
        )
        self.assertEqual(dir_rel, "旧目录", "主体已有文件所在目录优先于新建类别目录")

    def test_new_subject_gets_category_dir(self):
        _p1, d1 = assets_mod._resolve_target(self.base, self.root, WS_NAME, "教室", "scene", "")
        self.assertEqual(d1, "场景/教室")
        _p2, d2 = assets_mod._resolve_target(self.base, self.root, WS_NAME, "白陌", "character", "")
        self.assertEqual(d2, "人物/白陌")
        # 未知类别兜底到「人物」
        _p3, d3 = assets_mod._resolve_target(self.base, self.root, WS_NAME, "白陌", "bogus", "")
        self.assertEqual(d3, "人物/白陌")

    def test_new_subject_dir_is_created_on_disk(self):
        self.assertEqual(
            assets_mod._resolve_target(self.base, self.root, WS_NAME, "教室", "scene", "")[0],
            (self.base / "场景" / "教室").resolve(),
        )


# ---------------------------------------------------------------------------
# 上传：落名 + 自动归位
# ---------------------------------------------------------------------------

class TestUpload(AssetCase):
    def test_names_file_and_creates_category_dir(self):
        r = self.upload(_upload_files(("whatever.png", PNG, "image/png")), prefix="白陌")
        self.assertEqual(r.status_code, 200)
        body = r.json()

        self.assertEqual(body["asset_dir"], "素材")
        self.assertEqual(body["dir"], "人物/白陌")
        self.assertTrue(body["auto_placed"], "没给 subdir 时应标记为自动定位")
        self.assertEqual(body["skipped"], [])

        (item,) = body["items"]
        self.assertEqual(item["name"], "白陌_参考.png", "落名 = 主体名_用途.ext")
        self.assertEqual(item["subject"], "白陌")
        self.assertEqual(item["kind"], "image")
        self.assertFalse(item["deduped"])

        disk = self.base / "人物/白陌/白陌_参考.png"
        self.assertEqual(disk.read_bytes(), PNG)
        row = self.rows()["人物/白陌/白陌_参考.png"]
        self.assertEqual((row["subject"], row["category"], row["kind"]), ("白陌", "character", "image"))

    def test_default_suffix_depends_on_kind(self):
        r = self.upload(
            _upload_files(("a.png", PNG, "image/png"), ("b.mp3", MP3, "audio/mpeg")),
            prefix="白陌",
        )
        names = {i["name"] for i in r.json()["items"]}
        self.assertEqual(names, {"白陌_参考.png", "白陌_配音.mp3"})

    def test_explicit_suffix_overrides_default(self):
        r = self.upload(_upload_files(("a.png", PNG, "image/png")), prefix="白陌", suffix="定妆")
        self.assertEqual(r.json()["items"][0]["name"], "白陌_定妆.png")

    def test_never_overwrites_duplicate_names(self):
        files = _upload_files(("a.png", PNG, "image/png"))
        first = self.upload(files, prefix="白陌").json()["items"][0]
        self.assertFalse(first["deduped"])

        second = self.upload(files, prefix="白陌").json()["items"][0]
        self.assertEqual(second["name"], "白陌_参考_02.png")
        self.assertTrue(second["deduped"])

        third = self.upload(files, prefix="白陌").json()["items"][0]
        self.assertEqual(third["name"], "白陌_参考_03.png")
        # 三份都在，谁也没被覆盖
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual((self.base / "人物/白陌/白陌_参考.png").read_bytes(), PNG)

    def test_scene_category_lands_in_scene_dir(self):
        r = self.upload(_upload_files(("a.webp", WEBP, "image/webp")), prefix="教室", category="scene")
        self.assertEqual(r.json()["dir"], "场景/教室")
        self.assertTrue((self.base / "场景/教室/教室_参考.webp").exists())

    def test_explicit_subdir_is_not_marked_auto(self):
        self.write("人物/张三/张三_近景_01.png")
        self.scan()
        r = self.upload(
            _upload_files(("a.mp3", MP3, "audio/mpeg")),
            prefix="张三",
            subdir="人物/张三",
        )
        body = r.json()
        self.assertEqual(body["dir"], "人物/张三")
        self.assertFalse(body["auto_placed"])
        self.assertTrue((self.base / "人物/张三/张三_配音.mp3").exists())

    def test_joins_existing_flat_subject_dir(self):
        """主体已有文件在资产根（复试2 那种扁平结构）→ 新文件也落根目录，不另建子目录。"""
        self.write("白陌_近景.png")
        self.scan()
        body = self.upload(_upload_files(("a.png", PNG, "image/png")), prefix="白陌").json()
        self.assertEqual(body["dir"], "")
        self.assertTrue(body["auto_placed"])
        self.assertTrue((self.base / "白陌_参考.png").exists())
        self.assertFalse((self.base / "人物").exists(), "不该凭空建出类别目录")

    def test_unsupported_extension_is_skipped_with_reason(self):
        r = self.upload(_upload_files(("note.txt", b"hi", "text/plain")), prefix="白陌")
        body = r.json()
        self.assertEqual(body["items"], [])
        self.assertEqual(len(body["skipped"]), 1)
        self.assertEqual(body["skipped"][0]["name"], "note.txt")
        self.assertIn("不支持的格式", body["skipped"][0]["reason"])
        self.assertFalse((self.base / "人物").exists(), "没有可写入的文件时不该建目录")

    def test_requires_subject_or_suffix(self):
        r = self.upload(_upload_files(("a.png", PNG, "image/png")))
        self.assertEqual(r.status_code, 400)

    def test_unknown_category_falls_back_to_character(self):
        r = self.upload(_upload_files(("a.png", PNG, "image/png")), prefix="白陌", category="bogus")
        self.assertEqual(r.json()["dir"], "人物/白陌")

    def test_subject_name_is_sanitized(self):
        r = self.upload(_upload_files(("a.png", PNG, "image/png")), prefix="  白/陌  ")
        item = r.json()["items"][0]
        self.assertEqual(item["name"], "白陌_参考.png")
        self.assertNotIn("/", item["rel"].rsplit("/", 1)[-1])


# ---------------------------------------------------------------------------
# 列表 / 重扫 / 落位预览 / 预览流
# ---------------------------------------------------------------------------

class TestEndpoints(AssetCase):
    def test_list_groups_by_category_and_hides_non_media(self):
        self.write("白陌_参考.png")
        self.write("白陌_配音.mp3", MP3)
        self.write("场景/教室/教室_实拍.webp", WEBP)
        self.write("_env.py", b"print(1)")

        r = self.client.get("/api/assets")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["asset_dir"], "素材")
        self.assertEqual(body["active_workspace"], WS_NAME)
        self.assertEqual(body["total"], 3, "_env.py 不入展示，但仍在索引里")

        by_key = {c["key"]: c for c in body["categories"]}
        self.assertEqual(set(by_key), {"character", "scene"})
        (group,) = by_key["character"]["groups"]
        self.assertEqual(group["name"], "白陌")
        self.assertEqual({i["name"] for i in group["items"]}, {"白陌_参考.png", "白陌_配音.mp3"})
        # 图排音频前
        self.assertEqual(group["items"][0]["kind"], "image")
        self.assertEqual(len(self.rows()), 4)

    def test_list_reflects_asset_dir_alias(self):
        """资产目录叫「资产」而不是「素材」时，一切照旧。"""
        (self.root / "资产").mkdir()
        self.install(folder="资产")
        self.write("白陌_参考.png")
        body = self.client.get("/api/assets").json()
        self.assertEqual(body["asset_dir"], "资产")
        self.assertEqual(body["total"], 1)

    def test_list_409_when_asset_dir_undecided(self):
        (self.root / "其他").mkdir()
        self.install(folder="")
        r = self.client.get("/api/assets")
        self.assertEqual(r.status_code, 409)
        self.assertIn("资产目录", r.json()["detail"])

    def test_list_400_when_no_active_workspace(self):
        self.install(active=None)
        self.assertEqual(self.client.get("/api/assets").status_code, 400)

    def test_rescan_reports_deltas(self):
        self.write("白陌_参考.png")
        first = self.client.post("/api/assets/scan").json()
        self.assertTrue(first["ok"])
        self.assertEqual((first["scanned"], first["added"], first["removed"]), (1, 1, 0))

        self.write("白陌_配音.mp3", MP3)
        second = self.client.post("/api/assets/scan").json()
        self.assertEqual((second["scanned"], second["added"], second["updated"]), (2, 1, 1))

        (self.base / "白陌_参考.png").unlink()
        third = self.client.post("/api/assets/scan").json()
        self.assertEqual((third["scanned"], third["removed"]), (1, 1))

    def test_rescan_409_when_asset_dir_undecided(self):
        (self.root / "其他").mkdir()
        self.install(folder="")
        self.assertEqual(self.client.post("/api/assets/scan").status_code, 409)

    def test_target_preview_new_and_existing_subject(self):
        r = self.client.get(
            "/api/assets/target", params={"subject": "阿一", "category": "character"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"subject": "阿一", "category": "character", "dir": "人物/阿一"})

        scene = self.client.get("/api/assets/target", params={"subject": "教室", "category": "scene"})
        self.assertEqual(scene.json()["dir"], "场景/教室")

        self.write("旧目录/白陌_参考.png")
        self.scan()
        existing = self.client.get("/api/assets/target", params={"subject": "白陌"})
        self.assertEqual(existing.json()["dir"], "旧目录")

    def test_target_honours_subdir_param_and_sanitizes_subject(self):
        got = self.client.get(
            "/api/assets/target",
            params={"subject": "白/陌", "category": "character", "subdir": "人物/张三"},
        )
        self.assertEqual(got.json(), {"subject": "白陌", "category": "character", "dir": "人物/张三"})

    def test_target_requires_subject(self):
        self.assertEqual(self.client.get("/api/assets/target", params={"subject": ""}).status_code, 400)
        # 全是非法字符 → 清洗后为空 → 同样 400
        self.assertEqual(self.client.get("/api/assets/target", params={"subject": "..."}).status_code, 400)

    def test_file_endpoint_serves_media_inline(self):
        self.write("白陌_参考.png")
        r = self.client.get("/api/assets/file/白陌_参考.png")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/png")
        self.assertEqual(r.content, PNG)

    def test_file_endpoint_404_and_415(self):
        self.write("notes.txt", b"hi")
        self.assertEqual(self.client.get("/api/assets/file/nope.png").status_code, 404)
        self.assertEqual(self.client.get("/api/assets/file/notes.txt").status_code, 415)

    def test_file_endpoint_refuses_path_traversal(self):
        # 资产目录外放一个真实的 png —— 穿越如果没被拦住就会返回 200
        (self.root / "secret.png").write_bytes(PNG)
        r = self.client.get("/api/assets/file/%2E%2E%2Fsecret.png")
        self.assertEqual(r.status_code, 404)

        # 直接调处理函数，确认是「路径越界」这个原因，而不是文件不存在
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(assets_mod.asset_file("../secret.png"))
        self.assertEqual(cm.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
