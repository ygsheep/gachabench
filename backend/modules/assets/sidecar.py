# -*- coding: utf-8 -*-
"""素材提示词 sidecar：`X.png` 旁边放 `X.prompt.json`。

## 为什么是文件而不是库

外部 Agent 没有这个工作区的 SQLite 句柄，也不该有。它只能读写文件系统。
所以「图片 ↔ 提示词」的绑定必须落在**文件**上，库里的 `asset_prompt`
只是这条文件的缓存 —— 删库重扫能完全重建，不会丢任何信息。

## 谁写、谁读

- **写：外部 Agent（或用户手改）**。系统永不回写 sidecar。
- **读：本模块**。扫盘时摄取进 `asset_prompt`，UI 只展示。

因此这里全部是**防御性读取**：外部写入的东西可能是任何形状，一个坏文件
绝不能拖垮整轮索引 —— 宁可这一条降级成「没有提示词」，也不能让扫描 500。

## 契约（schema = 1）

```jsonc
{
  "schema": 1,                    // 可选，缺省按 1
  "subject": "白兴学",             // 可选。与文件名不一致时只记录差异，不覆盖分组
  "variant": "国漫3D_四视图",       // 可选
  "prompt": "生成这张图的提示词",    // 主字段
  "negative": "",                 // 可选
  "caption": "画面文字描述",         // 可选。给「图片识别」那一路用：外部 Agent 写，系统只读
  "params": {"model": "..."},     // 可选，任意扁平键值
  "extra": {}                     // 可选；未识别的顶层键也会被收进这里，不丢信息
}
```

## 刻意不做的事

- **不写 `windows`（适用区间）**。区间是后验、随时要改的，而系统对 sidecar
  只读 —— 若区间放文件里，「改终点」就得改文件，用户改库又会被下次扫盘覆盖。
  区间只在库（`asset_window`），见 `modules/assets/windows.py`。
- **不因 sidecar 改分组**。`index_assets.subject` 仍然只由文件名决定：
  文件名是唯一稳定、可预测、人能一眼看懂的口径。sidecar 声明的 subject
  与本模块记录一起存下来，UI 有差异时可提示，但绝不静默换组。
- **sidecar 自身不进资产索引**（`is_sidecar`），否则资产页会多出一堆 json。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select

from modules.assets.models import AssetPrompt, IndexAsset, now_iso

SIDECAR_SUFFIX = ".prompt.json"
SCHEMA_VERSION = 1

# 上限：外部 Agent 可能塞进整本书。超了截断并记问题，不拒绝整条。
MAX_PROMPT = 200_000
MAX_TEXT = 20_000

_STR_FIELDS = ("subject", "variant", "prompt", "negative", "caption")
_KNOWN = {"schema", *_STR_FIELDS, "params", "extra"}


def is_sidecar(name: str | Path) -> bool:
    """`X.prompt.json` 是不是 sidecar（小写比较，Windows 上大小写不敏感）。"""
    return Path(str(name)).name.lower().endswith(SIDECAR_SUFFIX)


def sidecar_for(name: str | Path) -> str:
    """媒体文件名 → sidecar 文件名：`a.png` → `a.prompt.json`。"""
    return f"{Path(str(name)).stem}{SIDECAR_SUFFIX}"


def media_for(name: str | Path) -> str:
    """sidecar 文件名 → 媒体文件名：`a.prompt.json` → `a`（不含扩展名）。"""
    n = Path(str(name)).name
    if not n.lower().endswith(SIDECAR_SUFFIX):
        return Path(n).stem
    return n[: -len(SIDECAR_SUFFIX)]


@dataclass
class Sidecar:
    """一条 sidecar 的规范化结果。字段永远存在，坏输入降级成空串。"""

    subject: str = ""
    variant: str = ""
    prompt: str = ""
    negative: str = ""
    caption: str = ""
    params: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """有没有任何可用内容（全空的 sidecar 不值得建缓存行）。"""
        return bool(self.prompt or self.caption or self.params or self.extra)


def _text(value: Any, limit: int, name: str, problems: list[str]) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        s = value
    elif isinstance(value, (int, float, bool)):
        s = str(value)
    else:
        problems.append(f"{name} 不是字符串，已忽略（{type(value).__name__}）")
        return ""
    s = s.strip()
    if len(s) > limit:
        problems.append(f"{name} 超过 {limit} 字符，已截断")
        s = s[:limit]
    return s


def _dict(value: Any, name: str, problems: list[str]) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    problems.append(f"{name} 不是对象，已忽略（{type(value).__name__}）")
    return {}


def parse_obj(data: Any) -> Sidecar:
    """把已解析的 JSON 对象规范成 :class:`Sidecar`。**永不抛异常。**"""
    problems: list[str] = []
    if not isinstance(data, dict):
        problems.append(f"顶层不是对象（{type(data).__name__}）")
        return Sidecar(problems=problems)

    sc = Sidecar()
    raw_schema = data.get("schema")
    if raw_schema is None:
        pass
    elif isinstance(raw_schema, int) and not isinstance(raw_schema, bool):
        sc.schema_version = raw_schema
    else:
        problems.append(f"schema 不是整数（{raw_schema!r}），按 {SCHEMA_VERSION} 处理")

    sc.subject = _text(data.get("subject"), MAX_TEXT, "subject", problems)
    sc.variant = _text(data.get("variant"), MAX_TEXT, "variant", problems)
    sc.prompt = _text(data.get("prompt"), MAX_PROMPT, "prompt", problems)
    sc.negative = _text(data.get("negative"), MAX_PROMPT, "negative", problems)
    sc.caption = _text(data.get("caption"), MAX_TEXT, "caption", problems)
    sc.params = _dict(data.get("params"), "params", problems)

    extra = _dict(data.get("extra"), "extra", problems)
    # 未识别的顶层键并进 extra：外部 Agent 可能带自己的字段，不该被丢掉
    for k, v in data.items():
        if k not in _KNOWN:
            extra.setdefault(k, v)
    sc.extra = extra
    sc.problems = problems
    return sc


def read(path: str | Path) -> tuple[Optional[Sidecar], list[str]]:
    """读一个 sidecar 文件。返回 ``(数据 or None, 问题列表)``，**永不抛异常**。

    文件不存在 / 读不了 / 不是 JSON 对象 → 返回 ``(None, [原因])``，
    调用方据此清掉缓存行（真相在文件里，文件坏了就等于没有提示词）。
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, [f"读取失败：{exc}"]
    except UnicodeDecodeError as exc:
        return None, [f"不是 UTF-8：{exc}"]
    if not raw.strip():
        return None, ["文件为空"]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [f"JSON 解析失败：第 {exc.lineno} 行第 {exc.colno} 列 {exc.msg}"]
    sc = parse_obj(data)
    return sc, sc.problems


# ---------------------------------------------------------------------------
# 摄取
# ---------------------------------------------------------------------------

def ingest(root: str | Path, session) -> dict:
    """把盘上的 sidecar 摄取进 `asset_prompt`。返回统计。

    - 没有 sidecar 的资产不建行（不占空间、不误导 UI）；
    - sidecar 消失 → **删掉缓存行**（真相在文件里，文件没了缓存就该没）；
    - mtime 未变 → 跳过解析（成本上限 = N 次 stat）；
    - 单个文件失败只记 problems，不中断整轮。
    """
    root = Path(root)
    stats = {"checked": 0, "added": 0, "updated": 0, "removed": 0, "problems": []}

    assets = session.scalars(
        select(IndexAsset).where(IndexAsset.status == "active")
    ).all()
    cached = {p.asset_id: p for p in session.scalars(select(AssetPrompt)).all()}
    alive: set[str] = set()

    for asset in assets:
        stats["checked"] += 1
        rel_dir = Path(asset.path).parent.as_posix()
        sc_name = sidecar_for(asset.name)
        rel_side = f"{rel_dir}/{sc_name}" if rel_dir not in ("", ".") else sc_name
        abs_side = root / rel_side
        try:
            st = abs_side.stat()
        except OSError:
            continue
        alive.add(asset.id)

        row = cached.get(asset.id)
        if row is not None and abs(row.sidecar_mtime - st.st_mtime) < 1e-6:
            continue
        data, problems = read(abs_side)
        if problems:
            stats["problems"].append(f"{rel_side}: {'; '.join(problems)}")
        if data is None or not data.ok:
            # 文件在但不可用：保留旧缓存没有意义（旧内容可能早已过期）
            if row is not None:
                session.delete(row)
                cached.pop(asset.id, None)
                stats["removed"] += 1
            continue

        if row is None:
            row = AssetPrompt(
                asset_id=asset.id,
                created_at=now_iso(),
                updated_at=now_iso(),
            )
            session.add(row)
            cached[asset.id] = row
            stats["added"] += 1
        else:
            stats["updated"] += 1
        row.schema_version = data.schema_version
        row.subject = data.subject
        row.variant = data.variant
        row.prompt = data.prompt
        row.negative = data.negative
        row.caption = data.caption
        row.params_json = json.dumps(data.params, ensure_ascii=False)
        row.extra_json = json.dumps(data.extra, ensure_ascii=False)
        row.sidecar_path = rel_side
        row.sidecar_mtime = st.st_mtime
        row.updated_at = now_iso()

    # 缓存里剩下的 = 现在没有 sidecar 的
    for asset_id, row in list(cached.items()):
        if asset_id not in alive:
            session.delete(row)
            stats["removed"] += 1

    session.flush()
    stats["problems"] = stats["problems"][:50]
    return stats
