# -*- coding: utf-8 -*-
"""配置 API 的单元测试（GET/PUT /api/config）。

跑法（在 backend 目录下）：

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

⚠️ 沙箱里 `.venv/Scripts/python.exe` 是 uv trampoline 会报 permission denied，改用：

    UVPY="C:/Users/young.YOUNG/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe"
    PYTHONPATH="../backend;../backend/.venv/Lib/site-packages" "$UVPY" -m unittest discover -s tests -v

隔离策略：PUT 会把配置写进 config.json —— 这里把 `config_api.set_config` 换成
「只更新内存缓存、不落盘」的替身，跑完不碰用户真实的 config.json。
"""
from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

import config as config_mod
import routers.config_api as config_api_mod
from config import Config
from routers.config_api import router


def _memory_only_set(cfg: Config) -> None:
    """替身：只更新进程内缓存，不写盘（避免污染用户真实的 config.json）。"""
    with config_mod._config_cache_lock:
        config_mod._config_cache = cfg


def _seed(**overrides) -> Config:
    base = {
        "comfyui": {"base_url": "http://127.0.0.1:8188", "poll_interval_sec": 5, "timeout_min": 60},
        "folders": {"draft": "草稿", "final": "生成", "prompt": "提示词",
                    "storyboard": "分镜表", "asset": "素材"},
        "workflow": {"json_path": "{prompt}/a.json", "nodes": {"prompt": "664"}},
        "workspaces": [{"name": "WS", "root": "D:/tmp/ws", "folders": {"asset": "资产"}}],
        "active": "WS",
    }
    base.update(overrides)
    return Config.model_validate(base)


class ConfigApiTest(unittest.TestCase):
    def setUp(self):
        self._orig_set = config_api_mod.set_config
        self._orig_cache = config_mod._config_cache
        config_api_mod.set_config = _memory_only_set
        config_mod._config_cache = _seed()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        config_api_mod.set_config = self._orig_set
        config_mod._config_cache = self._orig_cache

    # ---- 读 ----
    def test_get_returns_full_config(self):
        r = self.client.get("/api/config")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["comfyui"]["base_url"], "http://127.0.0.1:8188")
        self.assertEqual(body["active"], "WS")
        self.assertEqual(body["workflow"]["nodes"]["prompt"], "664")

    # ---- 写：合并语义 ----
    def test_put_is_merge_not_replace(self):
        r = self.client.put("/api/config", json={"comfyui": {"poll_interval_sec": 10}})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # 提交的键生效
        self.assertEqual(body["comfyui"]["poll_interval_sec"], 10)
        # 没提交的一个不动
        self.assertEqual(body["comfyui"]["timeout_min"], 60)
        self.assertEqual(body["comfyui"]["base_url"], "http://127.0.0.1:8188")
        self.assertEqual(body["folders"]["draft"], "草稿")
        self.assertEqual(body["workflow"]["nodes"]["prompt"], "664")
        self.assertEqual(body["active"], "WS")

    def test_put_clamps_out_of_range(self):
        r = self.client.put(
            "/api/config",
            json={"comfyui": {"poll_interval_sec": 9999, "timeout_min": -5}},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["comfyui"]["poll_interval_sec"], 60)   # 上限
        self.assertEqual(body["comfyui"]["timeout_min"], 1)          # 下限

    def test_put_garbage_number_falls_back_to_default(self):
        r = self.client.put("/api/config", json={"comfyui": {"poll_interval_sec": "abc"}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["comfyui"]["poll_interval_sec"], 5)

    def test_put_rejects_bad_base_url(self):
        r = self.client.put("/api/config", json={"comfyui": {"base_url": "127.0.0.1:8188"}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("http", r.json()["detail"])

    def test_put_rejects_folder_name_with_path(self):
        r = self.client.put("/api/config", json={"folders": {"draft": "../草稿"}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("单个文件夹名", r.json()["detail"])

    def test_put_ignores_workflow_nodes(self):
        # 节点映射不开放写：改错会产出废片且画面看不出来
        r = self.client.put(
            "/api/config",
            json={"workflow": {"json_path": "{prompt}/b.json", "nodes": {"prompt": "999"},
                               "ref_image_size": "512"}},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["workflow"]["json_path"], "{prompt}/b.json")
        self.assertEqual(body["workflow"]["nodes"]["prompt"], "664")  # 未被改动
        self.assertEqual(body["workflow"]["ref_image_size"], "max")   # 未被改动

    def test_put_rejects_empty_required_text(self):
        r = self.client.put("/api/config", json={"comfyui": {"input_dir": "   "}})
        self.assertEqual(r.status_code, 400)

    def test_put_ffmpeg_binary(self):
        r = self.client.put("/api/config", json={"ffmpeg": {"binary": "D:/ffmpeg/bin/ffmpeg.exe"}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["ffmpeg"]["binary"], "D:/ffmpeg/bin/ffmpeg.exe")


if __name__ == "__main__":
    unittest.main()
