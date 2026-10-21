# -*- coding: utf-8 -*-
"""运行时配置：读写 config.json（带文件锁 + 原子写）。"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

import runtime

# 路径统一由 runtime 解析（源码运行 / 打包成 exe 两套布局）。
# 源码运行时这些值与改造前完全一致：BACKEND_DIR = backend/，CONFIG_PATH = backend/config.json。
BACKEND_DIR = runtime.BACKEND_DIR
PROJECT_ROOT = runtime.PROJECT_ROOT
CONFIG_PATH = runtime.CONFIG_PATH

# 文件锁：同一进程内对 config.json 的读写互斥；跨进程由「原子写」保证（写临时文件再 rename）
_config_lock = threading.RLock()


class ComfyUIConfig(BaseModel):
    base_url: str = "http://127.0.0.1:8188"
    poll_interval_sec: int = 5
    timeout_min: int = 60
    comfy_output_dir: str = "R:/workspace/A_ComfyUI/ComfyUI/output"
    input_dir: str = "R:/workspace/A_ComfyUI/ComfyUI/input"


class FFmpegConfig(BaseModel):
    binary: str = "ffmpeg"
    probe_binary: str = "ffprobe"


class FoldersConfig(BaseModel):
    draft: str = "草稿"
    final: str = "生成"
    prompt: str = "提示词"
    storyboard: str = "分镜表"
    asset: str = "素材"
    # 本地原著 / 剧本库（C2）。已有工作区不重建骨架，导入时按需补建。
    source: str = "原著"


# 六个内容角色目录
FOLDER_KEYS: tuple[str, ...] = ("draft", "final", "prompt", "storyboard", "asset", "source")

# 资产目录自动匹配的候选名（按顺序优先命中，大小写不敏感）。
# 每个人习惯不同：有人叫「素材」，有人叫「资产」，也有人用英文 assets/media。
DEFAULT_ASSET_DIR_CANDIDATES: list[str] = [
    "素材", "资产", "素材库", "资产库", "素材文件", "资产文件", "参考图", "参考",
    "assets", "asset", "materials", "material", "media", "resources", "resource",
    "references", "reference", "refs", "images", "img",
]


class WorkflowNodesConfig(BaseModel):
    """工作流节点号兜底值 —— 优先由 comfy.detect_nodes() 按 class_type 自动探测；
    此处仅在探测失败时兜底。默认值 = 复试2 的 minimax_h3_多图参考生视频V8 .json。
    空字符串表示「无此角色」（如 V8 无 BasicScheduler，steps 留空）。"""
    prompt: str = "664"
    unet: str = "620"
    ref_root: str = "136"
    seconds: str = "132"
    megapixels: str = "3881"
    output_prefix: str = "732"
    seed: str = "142"
    lora: str = "710"
    sampler: str = "725"
    sigmas: str = "3864"
    attention: str = "748"
    load_image_ids: list[str] = Field(default_factory=lambda: ["137", "139", "141"])
    # 音频参考用的 LoadAudio 节点（顺序 == <Audio 1/2/3>）。留空则自动复用工作流里
    # 已经接在参考音频槽上的 LoadAudio，再不够就新建 300+i。
    load_audio_ids: list[str] = Field(default_factory=lambda: ["3878", "3879", "3880"])
    # 音频 VAE 加载节点号。空 = 按 vae_name 含 "audio" 自动探测；"-" = 不接。
    # 必须接上，否则 ComfyUI 侧「参考音频只条件化文本编码器」（节点 tooltip 原文），
    # 音色参考不会进入音频潜空间。
    audio_vae: str = ""


class WorkflowConfig(BaseModel):
    json_path: str = "{prompt}/minimax_h3_多图参考生视频V8 .json"
    nodes: WorkflowNodesConfig = Field(default_factory=WorkflowNodesConfig)
    ref_image_size: str = "max"


class SearchConfig(BaseModel):
    """联网采集（整合方案 L3 / C3）的开关。见 docs/INTEGRATION-novel-booksource-engine.md §7 M7。

    🔴 默认关闭：这一层会去访问第三方站点，方案里定的三条前置是
    「默认关闭 + 免责声明 + 来源标注」。采集只到「文本」为止，不做字体解密。
    """

    enabled: bool = False
    """用户已在 UI 上确认过免责声明。enabled 由它 + 上面开关共同决定。"""
    disclaimer_acked: bool = False
    """书源 JSON 的路径（Legado 书源格式）。空 = 用自带的 shareBookSource.json。"""
    sources_path: str = ""
    timeout_sec: int = 15
    """单个响应体上限：正文再长也不会到 10MB，超了直接断掉。"""
    max_bytes: int = 4 * 1024 * 1024
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    """导入原著库时写进 chapters 的来源标注（合规前置第 3 条）。"""
    attribution: bool = True


class WorkspaceEntry(BaseModel):
    name: str
    root: str
    created_at: str = ""
    # 每工作区的目录名覆盖（键取自 FOLDER_KEYS）；缺省键回退全局 folders。
    # 值为空串表示「该角色目录尚未确定」（打开项目时未匹配到资产目录名，
    # 需前端手动选择）。资产目录尤其常见：有人叫「素材」有人叫「资产」。
    folders: dict[str, str] = Field(default_factory=dict)


class Config(BaseModel):
    comfyui: ComfyUIConfig = Field(default_factory=ComfyUIConfig)
    ffmpeg: FFmpegConfig = Field(default_factory=FFmpegConfig)
    folders: FoldersConfig = Field(default_factory=FoldersConfig)
    # 资产目录自动匹配候选名（可在 config.json 里改）
    asset_dir_candidates: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ASSET_DIR_CANDIDATES)
    )
    index_dir: str = ".gachabench"
    workflow: WorkflowConfig = Field(default_factory=WorkflowConfig)
    workspaces: list[WorkspaceEntry] = Field(default_factory=list)
    active: Optional[str] = None
    search: SearchConfig = Field(default_factory=SearchConfig)

    # ---------- 运行期派生（不入库） ----------
    def active_ws(self) -> Optional[WorkspaceEntry]:
        if not self.active:
            return None
        for ws in self.workspaces:
            if ws.name == self.active:
                return ws
        return None

    def ws_root(self) -> Optional[Path]:
        ws = self.active_ws()
        return Path(ws.root) if ws else None

    def folder_name(self, key: str, ws: Optional[WorkspaceEntry] = None) -> Optional[str]:
        """解析目录名：给了 ws 就走它的覆盖，否则回退全局 folders。
        返回 None 表示「该目录未确定」（工作区里显式标记为空串也走这里）。"""
        if ws is not None and key in ws.folders:
            return ws.folders[key] or None
        return getattr(self.folders, key, None) or None

    def folder_names_for(self, ws: Optional[WorkspaceEntry]) -> dict[str, str]:
        """某工作区的五个目录名（缺省键用全局默认值填充）。ws=None = 纯全局默认。"""
        return {k: self.folder_name(k, ws) or "" for k in FOLDER_KEYS}

    def subdir(self, key: str) -> Optional[Path]:
        """返回 active 工作区下 key 对应子目录（draft/final/prompt/storyboard/asset）。"""
        root = self.ws_root()
        if root is None:
            return None
        folder = self.folder_name(key, self.active_ws())
        return root / folder if folder else None

    def index_path(self) -> Optional[Path]:
        root = self.ws_root()
        return root / self.index_dir if root else None

    def resolve_placeholder(self, template: str) -> str:
        """把 {prompt}/{final}/{draft}/{storyboard}/{asset} 占位符替换为 active 工作区子目录。"""
        for key in FOLDER_KEYS:
            sub = self.subdir(key)
            if sub is not None:
                template = template.replace("{%s}" % key, str(sub))
        return template


# ---------------------------------------------------------------------------
# 读写（文件锁 + 原子写）
# ---------------------------------------------------------------------------

def _default_config() -> Config:
    # 默认工作区：复试2（对齐 SPEC §4）
    return Config(
        workspaces=[
            WorkspaceEntry(
                name="复试2",
                root="D:/Notely/workspace/AIGC/复试2",
                created_at="2026-10-15T00:00:00",
            )
        ],
        active="复试2",
    )


def load_config() -> Config:
    """读 config.json；不存在则写默认值。"""
    with _config_lock:
        if not CONFIG_PATH.exists():
            cfg = _default_config()
            _write(cfg)
            return cfg
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return Config.model_validate(data)
        except Exception:
            # 配置损坏时回退默认并备份坏文件
            bad = CONFIG_PATH.with_suffix(".json.bak")
            try:
                os.replace(CONFIG_PATH, bad)
            except OSError:
                pass
            cfg = _default_config()
            _write(cfg)
            return cfg


def save_config(cfg: Config) -> None:
    with _config_lock:
        _write(cfg)


def _write(cfg: Config) -> None:
    """原子写：写临时文件再 os.replace。"""
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        cfg.model_dump_json(indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, CONFIG_PATH)


# 模块级缓存：进程内只读一次文件，修改走 save_config 后同步内存值
_config_cache: Optional[Config] = None
_config_cache_lock = threading.Lock()


def get_config() -> Config:
    global _config_cache
    with _config_cache_lock:
        if _config_cache is None:
            _config_cache = load_config()
        return _config_cache


def set_config(cfg: Config) -> None:
    global _config_cache
    save_config(cfg)
    with _config_cache_lock:
        _config_cache = cfg
