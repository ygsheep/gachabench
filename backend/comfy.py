# -*- coding: utf-8 -*-
"""ComfyUI 客户端：读工作流 JSON → 修补 → 提交 → 轮询 → 收产物。SPEC §7.3。

节点号不再写死：`detect_nodes()` 按 class_type 自动探测各角色节点（每个 class_type
在单个工作流 JSON 内唯一），探测不到时回退 config.workflow.nodes 的兜底值。这样
同一套 build/params 逻辑对 V8 与 U30 两套结构不同的工作流都生效。

- 参考图接线：LoadImage 节点按 id 升序复用，超出则动态新建；
  ref_image_N 顺序 == 提示词里 <Picture N+1> 的顺序。
- 参考音频（可选）：LoadAudio → ref_audios.ref_audio_i（顺序 == 提示词里 <Audio i+1>，
  从 0 起连续不跳号）。优先复用工作流里已经接在参考音频槽上的那批 LoadAudio，不够才
  新建 300+i；同时清空既有的 ref_audios.* 与 ref_video_audios.*（后者是「同号参考视频
  的音轨」，工作流里没接 ref_videos 时残留条目会让节点校验失败）。接了参考音频就必须
  同时接 ref_root 的 audio_vae，否则按节点说明「参考音频只条件化文本编码器」。
- 可调参数：model / lora / lora_strength / sampler / sigmas / steps /
  aspect_ratio / mp / duration / attention / seed，全部经节点号写入并做范围校验。
- 提交前强制查 node_errors；非空即中止。
- 进度：用 ComfyUI 的 WebSocket（同一 client_id）实时回传采样进度百分比。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from pathlib import Path
from typing import Callable, Optional

import httpx

SEED_MAX = 1125899906842624  # easy seed 上限，超了会被静默拒收

CLIENT_ID = "gachabench"

# 角色 → 候选 class_type（按优先级）；单个 JSON 内这些 class_type 唯一
ROLE_CLASS: dict[str, tuple[str, ...]] = {
    "prompt": ("CR Prompt Text", "PrimitiveStringMultiline", "PrimitiveString"),
    "unet": ("UNETLoader",),
    "ref_root": ("MiniMaxH3ReferenceToVideo",),
    "seconds": ("PrimitiveFloat",),
    "megapixels": ("ResolutionSelector",),
    "output_prefix": ("VHS_VideoCombine",),
    "seed": ("easy seed", "RandomNoise"),
    "lora": ("LoraLoaderModelOnly", "Lora Loader Stack (rgthree)"),
    "sampler": ("KSamplerSelect",),
    "sigmas": ("ManualSigmas",),
    "attention": ("ModelAttentionBackend",),
}

# 角色 → 该节点上承载该值的字段候选名（按优先级）
ROLE_FIELDS: dict[str, tuple[str, ...]] = {
    "prompt": ("prompt", "value"),
    "unet": ("unet_name",),
    "seconds": ("value",),
    "megapixels": ("megapixels", "aspect_ratio"),
    "output_prefix": ("filename_prefix",),
    "seed": ("seed", "noise_seed"),
    "lora": ("lora_name", "lora_02", "lora_01", "lora_03", "lora_04"),
    "sampler": ("sampler_name",),
    "sigmas": ("sigmas",),
    "attention": ("attention",),
}

# ComfyUI 未提供枚举时的兜底候选项
FALLBACK_OPTIONS: dict[str, list[str]] = {
    "ResolutionSelector.aspect_ratio": [
        "16:9 (Widescreen)",
        "9:16 (Portrait)",
        "1:1 (Square)",
        "4:3 (Standard)",
        "3:4 (Portrait)",
        "21:9 (Ultrawide)",
    ],
}


def _http(cfg) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=cfg.comfyui.base_url, timeout=30)


def _ws_url(cfg, client_id: str) -> str:
    base = cfg.comfyui.base_url.rstrip("/")
    return base.replace("https://", "wss://").replace("http://", "ws://") + f"/ws?clientId={client_id}"


# ---------------------------------------------------------------------------
# 节点探测
# ---------------------------------------------------------------------------

def detect_nodes(wf: dict, fallback=None) -> dict:
    """按 class_type 探测各角色节点。返回 {role: {node, field, class_type}}。

    探测不到的角色回退 fallback（config.workflow.nodes）里的节点号（若该节点存在）。
    另附 load_image_ids / load_audio_ids（按数字 id 升序）。
    """
    nodes: dict[str, dict] = {}
    for role, classes in ROLE_CLASS.items():
        chosen = None
        for cls in classes:
            for nid, nd in wf.items():
                if nd.get("class_type") == cls:
                    chosen = nid
                    break
            if chosen:
                break
        if chosen is None and fallback is not None:
            fid = (getattr(fallback, role, "") or "").strip()
            if fid and fid in wf:
                chosen = fid
        if chosen and chosen in wf:
            inp = wf[chosen].get("inputs", {})
            fields = ROLE_FIELDS.get(role, ())
            field = next((f for f in fields if f in inp), fields[0] if fields else None)
            nodes[role] = {
                "node": chosen,
                "field": field,
                "class_type": wf[chosen].get("class_type"),
            }
    lora = nodes.get("lora")
    if lora and lora.get("field"):
        f = lora["field"]
        m = re.match(r"^lora_(\d+)$", f)
        lora["strength_field"] = ("strength_" + m.group(1)) if m else "strength_model"

    def _ids(cls: str) -> list[str]:
        return sorted(
            [nid for nid, nd in wf.items() if nd.get("class_type") == cls],
            key=lambda x: int(x) if str(x).isdigit() else 1 << 30,
        )

    nodes["load_image_ids"] = _ids("LoadImage")  # type: ignore[assignment]
    nodes["load_audio_ids"] = _ids("LoadAudio")  # type: ignore[assignment]
    return nodes


def _role_inputs(wf: dict, nodes: dict, role: str):
    """返回 (inputs dict, field) 或 (None, None)。"""
    info = nodes.get(role)
    if not info:
        return None, None
    nd = wf.get(info["node"])
    if not nd:
        return None, None
    return nd.setdefault("inputs", {}), info.get("field")


def _write(wf: dict, nodes: dict, role: str, value) -> bool:
    inp, field = _role_inputs(wf, nodes, role)
    if inp is None or not field:
        return False
    inp[field] = value
    return True


# ---------------------------------------------------------------------------
# 构造提交用工作流
# ---------------------------------------------------------------------------

def _audio_vae_node(wf: dict, override: str = "") -> Optional[str]:
    """找音频 VAE 加载节点。

    优先级：config 指定 → `vae_name` 含 "audio" 的 VAELoader（id 升序取第一个）。
    `override` 传 "-" 表示显式不接（返回 None）。
    """
    ov = (override or "").strip()
    if ov == "-":
        return None
    if ov:
        nd = wf.get(ov)
        if isinstance(nd, dict) and nd.get("class_type") == "VAELoader":
            return ov
    cands = [
        nid
        for nid, nd in wf.items()
        if isinstance(nd, dict)
        and nd.get("class_type") == "VAELoader"
        and "audio" in str((nd.get("inputs") or {}).get("vae_name", "")).lower()
    ]
    cands.sort(key=lambda x: int(x) if str(x).isdigit() else 1 << 30)
    return cands[0] if cands else None


def _audio_load_nodes(wf: dict, ri: dict, fallback: Optional[list[str]] = None) -> list[str]:
    """工作流里已经接在参考音频槽上的 LoadAudio 节点，按槽位号升序。

    在 ComfyUI 里手工调工作流时，配音常常被挂在 `ref_video_audios.ref_video_audio_N`
    上 —— 那是「同号参考视频的音轨」，语义不对，但节点本身是好的。直接搬过来复用到
    `ref_audios`，免得每次跑片都凭空多出一批 LoadAudio。
    """
    found: list[tuple[int, str]] = []
    for key, val in ri.items():
        if not isinstance(val, list) or not val:
            continue
        if not (key.startswith("ref_audios.") or key.startswith("ref_video_audios.")):
            continue
        m = re.search(r"_(\d+)$", key)
        if not m:
            continue
        nid = str(val[0])
        if isinstance(wf.get(nid), dict) and wf[nid].get("class_type") == "LoadAudio":
            found.append((int(m.group(1)), nid))
    out: list[str] = []
    for _, nid in sorted(found):
        if nid not in out:
            out.append(nid)
    for nid in fallback or []:
        sid = str(nid)
        if sid and sid not in out and isinstance(wf.get(sid), dict):
            out.append(sid)
    return out


def build(
    cfg,
    prompt_text: str,
    refs: list[str],
    seconds: float,
    mp: float,
    tag: str,
    audios: Optional[list[str]] = None,
    seed: Optional[int] = None,
    *,
    workflow_path: Optional[str] = None,
    model: Optional[str] = None,
    lora: Optional[str] = None,
    lora_strength: Optional[float] = None,
    sampler: Optional[str] = None,
    sigmas: Optional[str] = None,
    multiple: Optional[int] = None,
    aspect_ratio: Optional[str] = None,
    attention: Optional[str] = None,
    duration: Optional[float] = None,
) -> dict:
    """构造提交用工作流 dict（未提供的参数一律保持 JSON 原值）。

    refs: 参考图文件名列表（须存在于 ComfyUI input 目录），顺序 == <Picture N+1>。
    audios: 参考音频文件名列表（须存在于 ComfyUI input 目录），顺序 == <Audio N+1>；最多 3 条。
    tag: ComfyUI 输出前缀，如 `草稿/04_第4段_1of3`。
    multiple: ResolutionSelector 的对齐倍数（用户口径的「步数」，常用 8/16/32/64）。
    """
    wf_path = Path(workflow_path or cfg.resolve_placeholder(cfg.workflow.json_path))
    if not wf_path.exists():
        raise FileNotFoundError(f"工作流 JSON 不存在：{wf_path}")
    wf = json.loads(wf_path.read_text(encoding="utf-8"))
    nodes = detect_nodes(wf, cfg.workflow.nodes)

    if not nodes.get("ref_root"):
        raise RuntimeError(f"{wf_path.name} 中未探测到 MiniMaxH3ReferenceToVideo 节点")

    # ① 提示词 + 参数覆盖（先校验后写入，避免半改）
    if duration is not None:
        _check_range("时长 duration", duration, 0.5, 60.0)
    _check_range("画质 megapixels", mp, 0.1, 1.0)
    if lora_strength is not None:
        _check_range("LoRA 强度", lora_strength, 0.0, 2.0)
    if multiple is not None:
        _check_int("倍数 multiple", multiple, 8, 128)
    if seed is not None:
        _check_range("种子 seed", seed, 1, SEED_MAX)

    _write(wf, nodes, "prompt", prompt_text.rstrip("\n"))
    if model:
        _write(wf, nodes, "unet", model)
    _write(wf, nodes, "seconds", float(duration if duration is not None else seconds))
    _write(wf, nodes, "megapixels", float(mp))
    if nodes.get("megapixels") and nodes["megapixels"]["node"] in wf:
        mg = wf[nodes["megapixels"]["node"]]["inputs"]
        if aspect_ratio:
            mg["aspect_ratio"] = aspect_ratio
        if multiple is not None:
            mg["multiple"] = int(multiple)
    _write(wf, nodes, "output_prefix", tag)
    _write(wf, nodes, "seed", int(seed) if seed else random.randint(1, SEED_MAX))
    if lora:
        _write(wf, nodes, "lora", lora)
        if lora_strength is not None:
            info = nodes.get("lora") or {}
            nd = wf.get(info.get("node"))
            sf = info.get("strength_field")
            if nd and sf:
                nd.setdefault("inputs", {})[sf] = float(lora_strength)
    if sampler:
        _write(wf, nodes, "sampler", sampler)
    if sigmas is not None and str(sigmas).strip():
        _write(wf, nodes, "sigmas", str(sigmas).strip())
    if attention:
        _write(wf, nodes, "attention", attention)

    # ② 参考图接线
    load_ids = list(nodes.get("load_image_ids") or [])
    nids: list[str] = []
    for i, img in enumerate(refs):
        nid = load_ids[i] if i < len(load_ids) else str(220 + i)
        if nid in wf:
            wf[nid]["inputs"]["image"] = img
        else:
            wf[nid] = {
                "inputs": {"image": img},
                "class_type": "LoadImage",
                "_meta": {"title": "加载图像%d" % (i + 1)},
            }
        nids.append(nid)
    ri = wf[nodes["ref_root"]["node"]]["inputs"]
    for k in [k for k in ri if k.startswith("ref_images.")]:
        del ri[k]
    for i, nid in enumerate(nids):
        ri["ref_images.ref_image_%d" % i] = [nid, 0]
    ri["ref_image_size"] = cfg.workflow.ref_image_size

    # ③ 参考音频接线（可选；ref_audio_N 顺序 == <Audio N+1>）
    #
    # 清空只发生在这个内存里的 wf 上，不写回工作流 JSON 文件。
    audio_load = _audio_load_nodes(wf, ri, list(nodes.get("load_audio_ids") or []))
    for k in [k for k in ri if k.startswith("ref_audios.")]:
        del ri[k]
    for k in [k for k in ri if k.startswith("ref_video_audios.")]:
        del ri[k]

    used_audio: list[str] = []
    for i, aud in enumerate((audios or [])[:3]):
        if not aud:
            continue  # 槽位留空但保持编号（AUTOGROW 要求从 0 起连续、不跳号）
        nid = audio_load[i] if i < len(audio_load) else str(300 + i)
        if nid in wf:
            wf[nid]["inputs"]["audio"] = aud
        else:
            wf[nid] = {
                "inputs": {"audio": aud},
                "class_type": "LoadAudio",
                "_meta": {"title": "加载音频%d" % (i + 1)},
            }
        ri["ref_audios.ref_audio_%d" % i] = [nid, 0]
        used_audio.append(nid)

    # 接了参考音频就必须给 audio_vae（节点 tooltip：Without it reference audio only
    # conditions the text encoder）—— 不接等于音色参考根本没进音频潜空间。
    if used_audio:
        av = _audio_vae_node(wf, getattr(cfg.workflow.nodes, "audio_vae", ""))
        if av:
            ri["audio_vae"] = [av, 0]

    return wf


def _check_range(name: str, value, lo, hi) -> None:
    if value is None:
        return
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是数字，收到 {value!r}")
    if not (lo <= v <= hi):
        raise ValueError(f"{name} 必须在 {lo}–{hi} 之间，收到 {value}")


def _check_int(name: str, value, lo: int, hi: int) -> None:
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是整数，收到 {value!r}")
    if not (lo <= v <= hi):
        raise ValueError(f"{name} 必须在 {lo}–{hi} 之间，收到 {value}")


# ---------------------------------------------------------------------------
# 工作流发现 + 参数元数据
# ---------------------------------------------------------------------------

def list_workflow_files(cfg) -> list[dict]:
    """列出 {prompt} 目录下的工作流 JSON。"""
    prompt_dir = cfg.subdir("prompt")
    if prompt_dir is None or not prompt_dir.is_dir():
        return []
    out = []
    for p in sorted(prompt_dir.glob("*.json")):
        out.append({
            "name": p.name,
            "path": str(p),
            "rel": str(p.relative_to(cfg.ws_root())).replace("\\", "/"),
        })
    return out


def _cur(wf: dict, nodes: dict, role: str):
    inp, field = _role_inputs(wf, nodes, role)
    if inp is None or not field:
        return None
    return inp.get(field)


def describe_workflow(cfg, wf_path: str) -> dict:
    """解析单个工作流 → 可调参数元数据（枚举候选项由调用方异步补齐）。"""
    p = Path(wf_path)
    wf = json.loads(p.read_text(encoding="utf-8"))
    nodes = detect_nodes(wf, cfg.workflow.nodes)

    node_ids = {k: v["node"] for k, v in nodes.items() if isinstance(v, dict)}
    params: list[dict] = []

    def add(key, label, role, type_, value, **extra):
        info = nodes.get(role)
        if not info:
            return
        params.append({
            "key": key, "label": label, "role": role, "node": info["node"],
            "field": extra.pop("field", info.get("field")),
            "class_type": info.get("class_type"),
            "type": type_, "value": value, **extra,
        })

    add("model", "视频模型（UNET）", "unet", "select", _cur(wf, nodes, "unet"),
        option_source={"class_type": "UNETLoader", "field": "unet_name"})
    add("lora", "LoRA", "lora", "select", _cur(wf, nodes, "lora"),
        option_source={"class_type": nodes.get("lora", {}).get("class_type"),
                       "field": (nodes.get("lora") or {}).get("field")})
    if nodes.get("lora"):
        info = nodes["lora"]
        nd = wf.get(info["node"]) or {}
        sf = info.get("strength_field")
        add("lora_strength", "LoRA 强度", "lora", "number",
            (nd.get("inputs") or {}).get(sf) if sf else None,
            field=sf, min=0.0, max=2.0, step=0.05)
    add("megapixels", "画质（MP）", "megapixels", "number", _cur(wf, nodes, "megapixels"),
        field="megapixels", min=0.1, max=1.0, step=0.05)
    if nodes.get("megapixels") and nodes["megapixels"]["node"] in wf:
        mg = wf[nodes["megapixels"]["node"]]["inputs"]
        params.append({
            "key": "multiple", "label": "倍数 multiple", "role": "megapixels",
            "node": nodes["megapixels"]["node"], "field": "multiple",
            "class_type": "ResolutionSelector", "type": "number",
            "value": mg.get("multiple"), "options": [8, 16, 32, 64],
            "min": 8, "max": 128, "step": 4,
            "option_source": {"class_type": "ResolutionSelector", "field": "multiple"},
        })
        params.append({
            "key": "aspect_ratio", "label": "画幅", "role": "megapixels",
            "node": nodes["megapixels"]["node"], "field": "aspect_ratio",
            "class_type": "ResolutionSelector", "type": "select", "value": mg.get("aspect_ratio"),
            "option_source": {"class_type": "ResolutionSelector", "field": "aspect_ratio"},
        })
    if nodes.get("sigmas"):
        cur_sig = _cur(wf, nodes, "sigmas") or ""
        n = len([x for x in str(cur_sig).split(",") if x.strip()])
        add("sigmas", f"采样 Sigma 序列（高级，当前 {max(0, n - 1)} 步）", "sigmas", "text", cur_sig)
    add("sampler", "采样器", "sampler", "select", _cur(wf, nodes, "sampler"),
        option_source={"class_type": "KSamplerSelect", "field": "sampler_name"})
    add("attention", "注意力后端", "attention", "select", _cur(wf, nodes, "attention"),
        option_source={"class_type": "ModelAttentionBackend", "field": "attention"})
    add("duration", "时长（秒）", "seconds", "number", _cur(wf, nodes, "seconds"),
        min=0.5, max=60.0, step=0.5)
    add("seed", "随机种子（0=随机）", "seed", "number",
        _cur(wf, nodes, "seed") or 0, min=0, max=SEED_MAX, step=1)

    return {
        "name": p.name,
        "rel": str(p.relative_to(cfg.ws_root())).replace("\\", "/"),
        "nodes": node_ids,
        "params": params,
    }


async def fetch_spec(cfg, class_type: Optional[str], field: Optional[str]) -> Optional[dict]:
    """查 ComfyUI /object_info/{class_type} 取该字段规格：options / min / max / step。

    兼容两种序列化：COMBO 为 [ [选项...], {...} ] 或 [ "COMBO", {options:[...]} ]。
    """
    if not class_type or not field:
        return None
    try:
        async with _http(cfg) as client:
            r = await client.get(f"/object_info/{class_type}")
            if r.status_code != 200:
                return None
            info = r.json().get(class_type) or {}
    except Exception:
        return None
    for bucket in ("required", "optional"):
        spec = (info.get("input", {}) or {}).get(bucket, {}) or {}
        entry = spec.get(field)
        if not entry or not isinstance(entry, list):
            continue
        t = entry[0]
        meta = entry[1] if len(entry) > 1 and isinstance(entry[1], dict) else {}
        options = None
        if isinstance(t, list):
            options = [x for x in t if isinstance(x, str)]
        elif isinstance(t, str) and t == "COMBO":
            opts = meta.get("options")
            if isinstance(opts, list):
                options = [x for x in opts if isinstance(x, str)]
        return {
            "options": options,
            "min": meta.get("min"),
            "max": meta.get("max"),
            "step": meta.get("step"),
            "type": t if isinstance(t, str) else "COMBO",
        }
    return None


async def enrich_params(cfg, params: list[dict]) -> list[dict]:
    """给参数补候选/范围：select 补 options（ComfyUI 枚举优先，回退内置表）；
    number 缺 min/max/step 时按 ComfyUI 规格补齐。"""
    for p in params:
        src = p.get("option_source") or {}
        spec = await fetch_spec(cfg, src.get("class_type"), src.get("field")) if src else None
        if p.get("type") == "select":
            opts = (spec or {}).get("options")
            if opts is None:
                key = f"{p.get('class_type')}.{p.get('field')}"
                opts = FALLBACK_OPTIONS.get(key)
            if opts is None:
                opts = [p["value"]] if p.get("value") is not None else []
            if p.get("options"):
                opts = list(dict.fromkeys([*p["options"], *opts]))
            if p.get("value") is not None and p["value"] not in opts:
                opts = [p["value"], *opts]
            p["options"] = opts
        elif p.get("type") == "number" and spec:
            for k in ("min", "max", "step"):
                if p.get(k) is None and spec.get(k) is not None:
                    p[k] = spec[k]
        p.pop("option_source", None)
    return params


# ---------------------------------------------------------------------------
# 提交 / 轮询 / 收产物
# ---------------------------------------------------------------------------

async def submit(cfg, wf: dict, client_id: str = CLIENT_ID) -> str:
    """POST /prompt；node_errors 非空即抛错。返回 prompt_id。"""
    async with _http(cfg) as client:
        r = await client.post(
            "/prompt",
            json={"prompt": wf, "client_id": client_id},
        )
        r.raise_for_status()
        resp = r.json()
    errs = resp.get("node_errors") or {}
    if errs:
        raise RuntimeError(
            "节点校验未通过，ComfyUI 会收下 prompt_id 但什么都不执行：\n"
            + json.dumps(errs, ensure_ascii=False)[:3000]
        )
    if "prompt_id" not in resp:
        raise RuntimeError("提交失败：%s" % json.dumps(resp, ensure_ascii=False)[:1500])
    return resp["prompt_id"]


async def alive(cfg, timeout: float = 3.0) -> bool:
    """ComfyUI 是否可达（/system_stats 有响应即算）。

    补收要能区分「历史里没有这条记录」和「服务器根本没连上」——
    后者只能让路，绝不能把任务判成「没有产物」。
    """
    try:
        async with httpx.AsyncClient(
            base_url=cfg.comfyui.base_url, timeout=timeout
        ) as client:
            r = await client.get("/system_stats")
            return r.status_code < 500
    except Exception:
        return False


async def history(cfg, pid: Optional[str], timeout: float = 8.0) -> Optional[dict]:
    """查一次 /history/{pid}：拿到就返回 history entry，查不到（未完成/已被清理）返回 None。

    不抛错：补收是「尽力而为」，ComfyUI 没开也不该拦住后端起来或接口返回。
    """
    if not pid:
        return None
    try:
        async with httpx.AsyncClient(
            base_url=cfg.comfyui.base_url, timeout=timeout
        ) as client:
            r = await client.get(f"/history/{pid}")
            r.raise_for_status()
            hist = r.json()
    except Exception:
        return None
    entry = hist.get(pid) if isinstance(hist, dict) else None
    return entry if isinstance(entry, dict) else None


async def is_queued(cfg, pid: Optional[str], timeout: float = 5.0) -> bool:
    """pid 是否还在 ComfyUI 队列里（running/pending）。查不到一律当 False。"""
    if not pid:
        return False
    try:
        async with httpx.AsyncClient(
            base_url=cfg.comfyui.base_url, timeout=timeout
        ) as client:
            q = (await client.get("/queue")).json()
    except Exception:
        return False
    for key in ("queue_running", "queue_pending"):
        for it in q.get(key) or []:
            if isinstance(it, list) and len(it) > 1 and it[1] == pid:
                return True
    return False


def succeeded(entry: Optional[dict]) -> bool:
    """history entry 是否真的跑完了（与 wait() 里同一套判断口径）。"""
    if not entry:
        return False
    st = entry.get("status") or {}
    return bool(st.get("completed") or st.get("status_str") == "success")


async def cancel(cfg, pid: Optional[str]) -> str:
    """尽力取消一个已提交的 prompt：在跑的打断，在排队的出队。

    只删/打断指定的 pid —— 不碰别的任务（多人同时跑片时不能无差别 interrupt）。
    任一步失败都不抛错，返回一段人话说明，直接写进 job 日志。
    """
    if not pid:
        return "没有 prompt_id，只在本地停止"
    notes: list[str] = []
    async with _http(cfg) as client:
        try:
            q = (await client.get("/queue")).json()
            running = [
                it for it in q.get("queue_running", [])
                if isinstance(it, list) and len(it) > 1 and it[1] == pid
            ]
            if running:
                await client.post("/interrupt")
                notes.append("已打断正在执行的这次采样")
        except Exception as e:
            notes.append("读队列失败：%s" % str(e)[:80])
        try:
            await client.post("/queue", json={"delete": [pid]})
            notes.append("已从 ComfyUI 队列移除")
        except Exception as e:
            notes.append("出队失败：%s" % str(e)[:80])
    return "；".join(notes)


ProgressCb = Callable[..., None]


def _emit(cb: Optional[ProgressCb], msg: str, pct: Optional[int] = None) -> None:
    if not cb:
        return
    try:
        cb(msg, pct)
    except TypeError:
        cb(msg)  # 兼容只接受单参的回调


async def _ws_listen(cfg, client_id: str, state: dict, on_progress: Optional[ProgressCb]):
    """旁听 ComfyUI WebSocket，把采样进度写进 state 并回调。"""
    try:
        import websockets
    except ImportError:
        return
    try:
        async with websockets.connect(_ws_url(cfg, client_id), max_size=None) as ws:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                t = msg.get("type")
                data = msg.get("data") or {}
                if t == "progress":
                    v, mx = data.get("value"), data.get("max")
                    if mx:
                        pct = int(v * 100 / mx)
                        if pct != state.get("pct"):
                            state["pct"] = pct
                            state["stage"] = "采样中"
                            _emit(on_progress, f"采样进度 {pct}%（{v}/{mx}）", pct)
                elif t == "executing":
                    node = data.get("node")
                    if node:
                        state["node"] = node
    except Exception:
        return  # ws 不可用不影响主轮询


async def wait(
    cfg,
    pid: str,
    on_progress: Optional[ProgressCb] = None,
    client_id: str = CLIENT_ID,
) -> dict:
    """轮询 /history/{pid} + /queue 直到完成/出错，同时经 WebSocket 回报采样进度。

    on_progress(msg: str, pct: int|None)。返回 history entry。
    """
    interval = cfg.comfyui.poll_interval_sec
    timeout = cfg.comfyui.timeout_min * 60
    t0 = time.time()
    last = ""
    state: dict = {"pct": 0, "stage": "排队中", "node": None}
    ws_task = asyncio.create_task(_ws_listen(cfg, client_id, state, on_progress))
    try:
        while True:
            await asyncio.sleep(interval)
            el = int(time.time() - t0)
            if el > timeout:
                raise RuntimeError(f"轮询超时（>{cfg.comfyui.timeout_min}min），pid={pid}")
            try:
                async with _http(cfg) as client:
                    h = await client.get(f"/history/{pid}")
                    h.raise_for_status()
                    hist = h.json()
                    q = await client.get("/queue")
                    q.raise_for_status()
                    queue = q.json()
            except Exception as e:
                _emit(on_progress, f"[{el:4d}s] 轮询异常 {e}", state.get("pct"))
                continue
            if pid in hist:
                entry = hist[pid]
                st = entry.get("status", {})
                if st.get("completed") or st.get("status_str") == "success":
                    if el < 10 and not (entry.get("outputs") or {}):
                        raise RuntimeError(
                            f"{el}s 就报完成且无产物 —— 典型症状是节点校验被拒（prompt_id 已发但未真正执行）"
                        )
                    _emit(on_progress, f"[{el:4d}s] 完成", 100)
                    return entry
                if st.get("status_str") == "error":
                    raise RuntimeError(
                        "执行报错：%s" % json.dumps(st, ensure_ascii=False)[:2000]
                    )
            running = len(queue.get("queue_running", []))
            pending = len(queue.get("queue_pending", []))
            if running == 0 and pending > 0 and state.get("stage") == "排队中":
                pass
            elif running > 0 and state.get("stage") == "排队中":
                state["stage"] = "运行中"
            now = f"{state.get('stage')} · running={running} pending={pending}"
            if now != last:
                _emit(on_progress, f"[{el:4d}s] {now}", state.get("pct"))
                last = now
    finally:
        ws_task.cancel()


def collect(cfg, entry: dict, tag: str) -> list[Path]:
    """从 comfy_output_dir 收集产物到返回的路径列表。

    偏好 `-audio.mp4`（带音轨）；找不到则回退任意视频。
    """
    out_root = Path(cfg.comfyui.comfy_output_dir)
    files: list[Path] = []
    for _, o in (entry.get("outputs") or {}).items():
        for key in ("gifs", "videos", "images"):
            for it in o.get(key, []) or []:
                fn = it.get("filename", "")
                sub = it.get("subfolder", "")
                if fn.lower().endswith((".mp4", ".webm", ".mkv")):
                    files.append(out_root / sub / fn)

    # 首选：history 里列出的、磁盘上真的在的文件
    found = [p for p in files if p.exists()]

    # 兜底：history 的 subfolder 与实际落盘位置对不上时（例如 history 记 subfolder=""
    # 但文件落在草稿/ 下，或反过来），按文件名在输出目录里递归找一遍。
    # 没有这一步，产物明明写出来了却会被判成「未找到产物文件」。
    if not found:
        missing = [p for p in files if p.name]
        for p in missing:
            try:
                hit = next((q for q in out_root.rglob(p.name) if q.is_file()), None)
            except OSError:
                hit = None
            if hit is not None and hit not in found:
                found.append(hit)

    # 按修改时间排序，-audio 后缀优先；只剩一个候选时不必排序（也避免 stat 失败）
    audio = [p for p in found if "-audio" in p.stem]
    picked = audio or found
    if len(picked) > 1:
        picked = sorted(picked, key=lambda p: p.stat().st_mtime, reverse=True)
    return picked
