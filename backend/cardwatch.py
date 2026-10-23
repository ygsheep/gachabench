# -*- coding: utf-8 -*-
"""卡池增量刷新：定时便宜地看一眼 草稿/ 与 生成/，只登记新片与被改过的片。

## 为什么不能「每次全量扫」

一次全量扫描会对**每一个** mp4 跑一遍 ffprobe（几十毫秒一个），30 条就是 1~2 秒，
几百条时点一下「重新扫描」能把整个工作区卡住 —— 而「有没有新片」这件事，
一次 `stat` 就够了，根本不需要 ffprobe。

所以这里分两层：

1. **指纹**：只 stat，得到 `rel_path -> (size, mtime_ns)` 快照，和上一轮比。
   只有**新增**或**变了大小/时间**的文件才值得探测入库，其余一律不碰数据库。
   （这就是「实时读盘」的成本上限：N 次 stat，几毫秒。）
2. **节流**：进程内按工作区记住「上次真正看过盘的时间」，短时间内的重复调用
   直接返回，不动盘。间隔由 `config.watch` 控制。

## 三个调用点

| 调用方 | 语义 |
| --- | --- |
| `main._watch_cards_loop` | 后台循环，每 `interval_sec` 秒跑一次；没变化就是零写入 |
| `GET /api/cards` | 页面一打开先保证新鲜，受 `request_gap_sec` 节流（切页/刷新时不重复扫） |
| `POST /api/workspaces/{name}/scan` | 手动「重新扫描」= `force=True`，全量重探（用户明确要求时才付这个代价） |

## 不做什么

- **不删卡**：文件从盘上消失时只从指纹里移除，库里那条记录保留（标签、备注、
  废稿原因都是人手写的，不能因为一次没看到文件就丢掉）。要清理由用户自己删。
- **不猜段号**：段号仍由 `repo.seg_from_filename` 按文件名解析，这里只负责「有没有」。

## 线程模型

`ensure_fresh()` 是**同步阻塞**函数（stat + sqlite + ffprobe），调用方负责扔进
`asyncio.to_thread`。同一工作区用一把进程内锁做单飞，后台循环与请求路径撞车时
后来的那个直接返回「正在扫」，不会排两条队列。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

from repo import upsert_card_by_path

# 只盯这两个目录：草稿（跑片落库）与生成（用户自己挑出来的正片）
SCAN_KEYS: tuple[str, ...] = ("draft", "final")

# 每工作区状态：{"snap": {rel: (size, mtime_ns)}, "at": 上次看盘时刻, "lock": 单飞锁}
_state: dict[str, dict] = {}
_state_guard = threading.Lock()


def _slot(root: Path) -> dict:
    key = str(Path(root).resolve())
    with _state_guard:
        slot = _state.get(key)
        if slot is None:
            slot = {"snap": None, "at": 0.0, "lock": threading.Lock()}
            _state[key] = slot
        return slot


def reset(root: Optional[Path] = None) -> None:
    """丢掉指纹（换盘 / 测试用）；root=None 清全部。"""
    with _state_guard:
        if root is None:
            _state.clear()
        else:
            _state.pop(str(Path(root).resolve()), None)


# ---------------------------------------------------------------------------
# 指纹
# ---------------------------------------------------------------------------

def _scan_roots(cfg, ws) -> list[Path]:
    """本工作区要看的两个目录（不含不存在的）。"""
    root = Path(ws.root)
    out: list[Path] = []
    for key in SCAN_KEYS:
        name = cfg.folder_name(key, ws)
        if not name:
            continue
        p = root / name
        if p.is_dir():
            out.append(p)
    return out


def snapshot(cfg, ws) -> dict[str, tuple[int, int]]:
    """只 stat，不探测：`相对路径 -> (字节数, mtime_ns)`。正斜杠，和 cards.rel_path 一致。"""
    root = Path(ws.root)
    snap: dict[str, tuple[int, int]] = {}
    for base in _scan_roots(cfg, ws):
        for mp4 in base.rglob("*.mp4"):
            try:
                st = mp4.stat()
            except OSError:
                continue  # 扫描途中被删 / 被占用：下一轮再说，不打断整轮
            rel = str(mp4.relative_to(root)).replace("\\", "/")
            snap[rel] = (st.st_size, st.st_mtime_ns)
    return snap


# ---------------------------------------------------------------------------
# 增量入库
# ---------------------------------------------------------------------------

def ensure_fresh(
    cfg,
    ws_name: str,
    db,
    root: Path,
    *,
    min_gap: float = 0.0,
    force: bool = False,
) -> dict:
    """看一眼盘，把新片 / 改过的片登记进库。返回统计（永不抛错）。

    {checked, added, updated, probed, skipped, root}

    - `min_gap > 0`：距上次看盘不足这么多秒就直接返回（`skipped="gap"`）；
    - `force=True`：忽略指纹与节流，全部重探（手动「重新扫描」用）。
    """
    stats = {"checked": 0, "added": 0, "updated": 0, "probed": 0, "skipped": None}
    root = Path(root)
    if not root.is_dir():
        stats["skipped"] = "missing"
        return stats

    slot = _slot(root)
    # 单飞：同一工作区不并发扫（后台循环和请求路径会撞车）
    if not slot["lock"].acquire(blocking=False):
        stats["skipped"] = "busy"
        return stats
    try:
        now = time.monotonic()
        if not force and min_gap > 0 and (now - slot["at"]) < min_gap:
            stats["skipped"] = "gap"
            return stats

        ws = next((w for w in cfg.workspaces if w.name == ws_name), None)
        if ws is None:
            stats["skipped"] = "no-workspace"
            return stats

        snap = snapshot(cfg, ws)
        stats["checked"] = len(snap)
        prev: Optional[dict] = slot["snap"]

        # 没变化：一个字节都不写库，只把「看过盘的时刻」往前推
        if not force and prev is not None and snap == prev:
            slot["at"] = now
            stats["skipped"] = "unchanged"
            return stats

        if force:
            todo = list(snap)
        elif prev is None:
            # 冷启动（进程刚起，还没有指纹）：只登记「库里没有的」。
            # 这里绝不能 list(snap) —— 那等于每次重启都把整个卡池重新 ffprobe 一遍，
            # 几百条片子就是十几秒，正好卡在用户点开审片台的时候。
            with db.lock:
                known = {
                    row["rel_path"]
                    for row in db.conn.execute(
                        "SELECT rel_path FROM cards WHERE workspace=?", (ws_name,)
                    )
                }
            todo = [rel for rel in snap if rel not in known]
        else:
            todo = [rel for rel, sig in snap.items() if prev.get(rel) != sig]

        for rel in todo:
            try:
                _card_id, created = upsert_card_by_path(db, ws_name, rel, cfg)
            except Exception:  # noqa: BLE001 —— 单个文件失败不能毁掉这一轮
                continue
            stats["probed"] += 1
            if created:
                stats["added"] += 1
            else:
                stats["updated"] += 1

        slot["snap"] = snap
        slot["at"] = now
        return stats
    except Exception:  # noqa: BLE001 —— 刷新失败不该让接口 500
        stats["skipped"] = "error"
        return stats
    finally:
        slot["lock"].release()


def note_root(cfg, ws) -> None:
    """外部刚写完盘（如跑片落库）时调用：把指纹刷新成当前状态，避免下一轮重复探测。"""
    try:
        slot = _slot(Path(ws.root))
        slot["snap"] = snapshot(cfg, ws)
        slot["at"] = time.monotonic()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 「文件已不在盘上」的标记
# ---------------------------------------------------------------------------

def scan_folder_names(cfg, ws) -> set[str]:
    """本工作区被盯的那两个目录名（草稿 / 生成）。"""
    out: set[str] = set()
    for key in SCAN_KEYS:
        name = cfg.folder_name(key, ws)
        if name:
            out.add(name)
    return out


def current_snap(root: Path) -> Optional[dict[str, tuple[int, int]]]:
    """上一次看盘的指纹；这个进程还没扫过 → None（此时不做任何「缺失」判断）。"""
    with _state_guard:
        slot = _state.get(str(Path(root).resolve()))
    if not slot or slot.get("snap") is None:
        return None
    return dict(slot["snap"])


def mark_missing(cards: list[dict], snap: Optional[dict], folders: set[str]) -> int:
    """给卡片打 `missing`：文件已经不在盘上（被改名 / 移动 / 删掉）。返回有几条。

    刻意**不删卡**：标签、备注、废稿原因都是人手写的，不能因为一次没 stat 到就丢。
    这里只负责让界面说实话 —— 点开是黑的，比「少了一条」更让人困惑。

    只对 `草稿/` `生成/` 这两层下的卡判断；`folders` 之外的路径一律当作「管不着」，
    免得把用户换了目录角色之后的老卡全标成缺失。
    """
    total = 0
    for c in cards:
        rel = str(c.get("rel_path") or "")
        top = rel.split("/", 1)[0]
        miss = bool(snap is not None and top in folders and rel not in snap)
        c["missing"] = miss
        total += 1 if miss else 0
    return total
