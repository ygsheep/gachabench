# -*- coding: utf-8 -*-
"""生成 GachaBench 的应用图标（icon.ico + 各级 PNG）。

为什么手写而不引 PIL：这只是个一次性小工具，不值得为它往构建环境里加一个
几十 MB 的依赖。PNG 用 zlib 压、ICO 只是个容器，标准库全都能干。

图形语义跟产品设计语言严格对齐（无彩度 · 金属灰）：
  - 圆角方底 #1E1E1E（= --gb-panel）
  - 左右两列片孔（--gb-void #171717，比底更暗，靠「挖洞」而不是描边）
  - 中间三条胶片格：上下两条暗（--gb-line #3C3C3C，废稿），中间一条亮
    （#CFCFD4，= 接续轨高亮色，全页唯一的高亮）
  → ico 缩到 16px 时，「暗-亮-暗」的三段式依然读得出来。
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

BG = (0x1E, 0x1E, 0x1E, 0xFF)      # panel
DIM = (0x3C, 0x3C, 0x3C, 0xFF)     # line（废稿格）
BRIGHT = (0xCF, 0xCF, 0xD4, 0xFF)  # 接续轨高亮
SPROCKET = (0x6E, 0x6E, 0x6E, 0xFF)  # 片孔：比底亮才读得出来（暗底上画暗块等于没画）

SS = 4  # 超采样倍数：先按 4 倍画，再盒式降采样，白拿抗锯齿


def _rr(x: float, y: float, w: float, h: float, r: float) -> bool:
    """点是否落在圆角矩形内（用于超采样判定）。"""
    if x < 0 or y < 0 or x >= w or y >= h:
        return False
    r = min(r, w / 2, h / 2)
    cx = min(max(x, r), w - r)
    cy = min(max(y, r), h - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def render(size: int) -> list[list[tuple[int, int, int, int]]]:
    """画一张 size×size 的 RGBA 位图。

    构图（全部用归一化坐标表达，各级尺寸完全一致）：
        圆角方底 #1E1E1E
        中央三条横格：#3C3C3C / #CFCFD4 / #3C3C3C —— 中间那条就是「接续轨」
        亮条左右各一枚片孔 #6E6E6E，点在 16px 上是小点、在 256px 上是胶片孔
    """
    W = size * SS

    def px(v: float) -> float:
        return v * size

    # 三条格：横向占 62%，竖向集中在中央
    cell_x = px(0.19)
    cell_w = px(0.62)
    gap = px(0.055)
    h1, h2, h3 = px(0.15), px(0.21), px(0.15)
    total = h1 + h2 + h3 + 2 * gap
    top = (size - total) / 2
    y1 = top
    y2 = y1 + h1 + gap
    y3 = y2 + h2 + gap

    # 片孔：贴在亮条两端外侧
    sq = px(0.075)
    sx_l = px(0.085)
    sx_r = px(1.0) - px(0.085) - sq
    sy = y2 + (h2 - sq) / 2

    rows: list[list[tuple[int, int, int, int]]] = []
    for py in range(W):
        row: list[tuple[int, int, int, int]] = []
        for pxi in range(W):
            x, y = pxi / SS, py / SS
            if not _rr(x, y, size, size, px(0.22)):
                row.append((0, 0, 0, 0))
                continue
            color = BG
            if (sx_l <= x <= sx_l + sq) or (sx_r <= x <= sx_r + sq):
                if sy <= y <= sy + sq:
                    color = SPROCKET
            if color is BG and cell_x <= x <= cell_x + cell_w:
                if y1 <= y <= y1 + h1:
                    color = DIM
                elif y2 <= y <= y2 + h2:
                    color = BRIGHT
                elif y3 <= y <= y3 + h3:
                    color = DIM
            row.append(color)
        rows.append(row)

    # 盒式降采样
    out: list[list[tuple[int, int, int, int]]] = []
    for oy in range(size):
        orow: list[tuple[int, int, int, int]] = []
        for ox in range(size):
            acc = [0, 0, 0, 0]
            for dy in range(SS):
                for dx in range(SS):
                    p = rows[oy * SS + dy][ox * SS + dx]
                    for k in range(4):
                        acc[k] += p[k]
            orow.append(tuple(c // (SS * SS) for c in acc))  # type: ignore[return-value]
        out.append(orow)
    return out


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def to_png(bmp: list[list[tuple[int, int, int, int]]]) -> bytes:
    h = len(bmp)
    w = len(bmp[0])
    raw = b"".join(b"\x00" + b"".join(bytes(px) for px in row) for row in bmp)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def to_ico(pngs: list[tuple[int, bytes]]) -> bytes:
    """多尺寸 ICO：每级都是内嵌 PNG（Vista+ 支持，体积还小）。"""
    out = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries = b""
    body = b""
    for size, data in pngs:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        body += data
        offset += len(data)
    return out + entries + body


SIZES = [16, 24, 32, 48, 64, 128, 256]


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    out_dir.mkdir(parents=True, exist_ok=True)

    pngs: list[tuple[int, bytes]] = []
    for s in SIZES:
        data = to_png(render(s))
        pngs.append((s, data))
        (out_dir / f"icon-{s}.png").write_bytes(data)
        print(f"  icon-{s}.png  {len(data):>7} B")

    ico = to_ico(pngs)
    (out_dir / "icon.ico").write_bytes(ico)
    print(f"  icon.ico      {len(ico):>7} B  （含 {len(pngs)} 级尺寸）")


if __name__ == "__main__":
    main()
