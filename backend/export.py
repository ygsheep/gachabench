# -*- coding: utf-8 -*-
"""导出通道：三态契约（download / file / dir）。

移植自 novel-booksource-engine 的 `_download_payload` + 桌面导出目录写法，
按 GB 的实际出口重排（见 docs/INTEGRATION-novel-booksource-engine.md §4.5）。

## 为什么是「JSON + base64」而不是文件流

浏览器对「不可渲染」的响应体（`application/zip`、`video/mp4` 这类）会**接管为
原生下载**，于是前端的 `fetch` 只能拿到一个空的 204 响应 —— 表现是下载下来一个
0 字节文件，而代码还以为成功了。实测 Chrome 一定这样。
所以 web 形态统一用 JSON 传字节，前端自己构造 Blob；顺带多一层「文件到底有没有
内容」的校验。

## 三种形态

```jsonc
{ "kind": "download", "filename": "...", "mime": "...", "size": 123, "base64": "..." }
{ "kind": "file",     "path": "D:/out/x.mp4", "filename": "x.mp4", "size": 123 }
{ "kind": "dir",      "path": "D:/out/第4段", "count": 3 }
```

`file` 与 `dir` 是桌面形态（目标目录由用户在系统对话框里选，见 `/api/system/pick-folder`）。
桌面形态**不经过 WebView 的下载管理器**，所以不需要壳层 `save_export` command ——
后端就写在本机，直接写盘更可靠，也少一个要随 Tauri 一起编译发布的部件。

## 多条目：打 zip（M6）

web 形态一次只能回一个文件，所以多条目一律打成一个 zip（标准库 `zipfile`，
不引新依赖）。zip 内的中文名由 `zipfile` 自动打 UTF-8 flag，Windows 资源管理器
与 macOS 归档工具都不会乱码 —— 这条实测过（`flag_bits & 0x800`）。
条目可以带 `subdir`，zip 内与写目录时都按它分层：范围导出「第 1–20 章」如果不
分目录，二十个 `0001 xxx.md` 会互相撞名。

## 安全边界

`dest_dir` 是用户自己选的目录，但 `items[].rel_path` 来自卡片库 —— 必须校验它
**确实落在当前工作区内**，否则一个构造出来的 `rel_path`（`../../etc/x`）就能把
工作区外的文件复制到用户选的地方。`subdir` 同理：它决定文件落在目标目录的哪一层，
带 `..` 就能逃逸出去。
"""
from __future__ import annotations

import base64
import io
import mimetypes
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# 单个 download 载荷的上限：base64 会让字节数涨 1/3，再套一层 JSON，
# 太大的东西走 download 会把浏览器内存吃光。超限直接报错让用户改走目录。
# 按**打包前的原始总字节**判定 —— 视频基本压不动，拿压缩后的体积判会漏。
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024

# 一次请求最多带多少个条目：再多就该分批或走范围导出
MAX_ITEMS = 64


class ExportError(Exception):
    """带 HTTP 语义的导出失败（message 直接给用户看）。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def mime_for(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def safe_rel(root: Path, rel_path: str) -> Path:
    """把卡片库里的 rel_path 解析成绝对路径，并确认它真的在工作区内。

    🔴 这条是导出通道的安全边界：卡片 rel_path 是从 sqlite 读的，理论上可能被
    外部改脏。解析后必须 `relative_to(root)`，否则拒绝。
    """
    if rel_path is None or not str(rel_path).strip():
        raise ExportError("rel_path 不能为空")
    rel = str(rel_path).strip().replace("\\", "/").lstrip("/")
    try:
        target = (root / rel).resolve()
        target.relative_to(root.resolve())
    except (ValueError, OSError) as e:
        raise ExportError(f"路径越出工作区：{rel_path}") from e
    if not target.is_file():
        raise ExportError(f"文件不存在：{rel_path}", 404)
    return target


def resolve_dest(dest_dir: str) -> Path:
    """校验用户选的目标目录：必须已存在（对话框选出来的）、且是目录。"""
    if not dest_dir or not str(dest_dir).strip():
        raise ExportError("dest_dir 不能为空")
    p = Path(str(dest_dir).strip())
    if not p.is_absolute():
        raise ExportError("dest_dir 必须是绝对路径")
    if not p.is_dir():
        raise ExportError(f"目标目录不存在：{dest_dir}", 404)
    return p


def _safe_filename(filename: str) -> str:
    """文件名只能是单个名字 —— 带路径就能把文件写到目标目录的别处去。"""
    name = str(filename or "").strip()
    if not name:
        raise ExportError("filename 不能为空")
    if any(c in name for c in "/\\") or name in (".", ".."):
        raise ExportError(f"filename 只能是单个文件名：{name}")
    return name


def _safe_subdir(subdir: Any) -> str:
    """子目录：允许 `a/b` 这种相对分层，但绝不允许 `..` 或盘符（否则能逃逸）。

    🔴 遇到 `..` 直接报错，不静默剥掉 —— 剥掉会改变调用方的语义，
    让「我以为放进子目录、其实被提到根目录」变成静默行为。
    """
    s = str(subdir or "").strip().replace("\\", "/")
    if not s:
        return ""
    s = s.strip("/")
    if not s:
        return ""
    parts = [p for p in s.split("/") if p != ""]
    for p in parts:
        if p in (".", ".."):
            raise ExportError(f"subdir 不允许 . 或 ..：{subdir}")
        if ":" in p:
            raise ExportError(f"subdir 不允许盘符：{subdir}")
    return "/".join(parts)


def _dedupe(dest_dir: Path, filename: str) -> Path:
    """不覆盖既有产物：重名追加 `_02` / `_03`（与素材库落名同一套约定）。"""
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    k = 2
    while True:
        candidate = dest_dir / f"{stem}_{k:02d}{suffix}"
        if not candidate.exists():
            return candidate
        k += 1


class ExportItem:
    """一个待导出条目：要么指向工作区里的既有文件，要么就是一段内存字节。

    `subdir` 决定它在 zip 内 / 目标目录里的层级，只影响摆放位置，不影响文件名。
    """

    __slots__ = ("filename", "mime", "src", "data", "subdir")

    def __init__(
        self,
        filename: str,
        mime: str,
        src: Optional[Path] = None,
        data: Optional[bytes] = None,
        subdir: str = "",
    ):
        self.filename = _safe_filename(filename)
        self.mime = mime
        self.src = src
        self.data = data
        self.subdir = _safe_subdir(subdir)

    @property
    def arcname(self) -> str:
        """zip 内 / 目标目录里的相对位置。"""
        return f"{self.subdir}/{self.filename}" if self.subdir else self.filename

    def read(self) -> bytes:
        if self.data is not None:
            return self.data
        assert self.src is not None
        try:
            return self.src.read_bytes()
        except OSError as e:
            raise ExportError(f"读不出来：{self.src.name}（{e}）", 500) from e

    @property
    def size(self) -> int:
        if self.data is not None:
            return len(self.data)
        assert self.src is not None
        try:
            return self.src.stat().st_size
        except OSError:
            return 0


def build_items(root: Path, raw_items: list) -> list[ExportItem]:
    """把请求里的 items 归一成 ExportItem 列表。

    两种条目形状：
    - `{"rel_path": "草稿/x.mp4"}`                —— 工作区内既有文件
    - `{"text": "...", "filename": "工单.txt"}`   —— 内存文本（工单 / 清单）

    两者都可以带 `subdir`（相对分层）与 `mime`。
    """
    if not raw_items:
        raise ExportError("没有可导出的条目")
    if not isinstance(raw_items, list):
        raise ExportError("items 必须是数组")
    if len(raw_items) > MAX_ITEMS:
        raise ExportError(f"一次最多导出 {MAX_ITEMS} 个条目")

    out: list[ExportItem] = []
    for i, raw in enumerate(raw_items):
        pos = i + 1
        if not isinstance(raw, dict):
            raise ExportError(f"第 {pos} 个条目格式不对")
        rel = raw.get("rel_path")
        text = raw.get("text")
        subdir = _safe_subdir(raw.get("subdir"))
        if rel is not None and text is not None:
            raise ExportError(f"第 {pos} 个条目同时给了 rel_path 和 text，只能给一个")
        if rel is not None:
            src = safe_rel(root, str(rel))
            filename = _safe_filename(str(raw.get("filename") or src.name))
            mime = str(raw.get("mime") or mime_for(src))
            out.append(ExportItem(filename=filename, mime=mime, src=src, subdir=subdir))
        elif text is not None:
            filename = _safe_filename(str(raw.get("filename") or ""))
            body = str(text)
            if not body:
                raise ExportError(f"第 {pos} 个文本条目是空的")
            out.append(
                ExportItem(
                    filename=filename,
                    mime=str(raw.get("mime") or "text/plain; charset=utf-8"),
                    data=body.encode("utf-8"),
                    subdir=subdir,
                )
            )
        else:
            raise ExportError(f"第 {pos} 个条目既没有 rel_path 也没有 text")
    return out


def default_zip_name(items: list[ExportItem]) -> str:
    stamp = datetime.now().strftime("%m%d-%H%M")
    if len(items) == 1 and items[0].subdir:
        return f"{items[0].subdir}_{stamp}.zip"
    return f"导出_{len(items)}项_{stamp}.zip"


def unique_arcnames(items: list[ExportItem]) -> list[str]:
    """给 zip 成员名去重：`0021 xxx.md` 撞了就变 `0021 xxx_02.md`。

    zip 里同名成员不会报错，后写的静默覆盖先写的 —— 用户拿到手才发现少了一半。
    """
    used: set[str] = set()
    out: list[str] = []
    for it in items:
        name = it.arcname
        if name not in used:
            used.add(name)
            out.append(name)
            continue
        stem = Path(it.filename).stem
        suffix = Path(it.filename).suffix
        k = 2
        while True:
            cand_name = f"{stem}_{k:02d}{suffix}"
            cand = f"{it.subdir}/{cand_name}" if it.subdir else cand_name
            if cand not in used:
                used.add(cand)
                out.append(cand)
                break
            k += 1
    return out


def zip_items(items: list[ExportItem], zip_name: str = "") -> ExportItem:
    """把多个条目打成一个 zip，返回单个 ExportItem（data 就是 zip 字节）。"""
    names = unique_arcnames(items)
    buf = io.BytesIO()
    # ZIP_DEFLATED：md / txt 能压到十几分之一；mp4 基本压不动，但 zip 只是容器
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item, name in zip(items, names):
            zf.writestr(name, item.read())
    data = buf.getvalue()
    name = str(zip_name or "").strip() or default_zip_name(items)
    if not name.lower().endswith(".zip"):
        name += ".zip"
    return ExportItem(filename=_safe_filename(name), mime="application/zip", data=data)


def _check_budget(items: list[ExportItem]) -> int:
    """下载形态的体积闸门（按原始字节算）。返回总大小。"""
    total = sum(it.size for it in items)
    if total <= 0:
        raise ExportError("要导出的文件都是空的")
    if total > MAX_DOWNLOAD_BYTES:
        raise ExportError(
            f"总共 {total / 1024 / 1024:.0f} MB，浏览器下载扛不住；请选择目标目录"
        )
    return total


def payload_download(items: list[ExportItem], zip_name: str = "") -> dict[str, Any]:
    """web 形态：JSON + base64。多条目自动打成一个 zip（M6）。

    单条目直出原文件（审片器「导出这一条」要的就是原 mp4，套一层 zip 是添乱）。
    """
    _check_budget(items)
    if len(items) == 1:
        item = items[0]
        raw = item.read()
        if not raw:
            raise ExportError(f"文件是空的：{item.filename}")
        return {
            "kind": "download",
            "filename": item.filename,
            "mime": item.mime,
            "size": len(raw),
            "base64": base64.b64encode(raw).decode("ascii"),
        }
    packed = zip_items(items, zip_name)
    # 打完包再量一次：base64 吃的是 zip 后的体积，这里才是真正过 JSON 的量
    if len(packed.data) > MAX_DOWNLOAD_BYTES:
        raise ExportError(
            f"打包后 {len(packed.data) / 1024 / 1024:.0f} MB，浏览器下载扛不住；请选择目标目录"
        )
    return {
        "kind": "download",
        "filename": packed.filename,
        "mime": packed.mime,
        "size": len(packed.data),
        "base64": base64.b64encode(packed.data).decode("ascii"),
        "packed": len(items),
    }


def write_to_dir(items: list[ExportItem], dest_dir: Path) -> dict[str, Any]:
    """桌面形态：把条目写进用户选的目录，返回 `dir` 载荷。

    🔴 绝不覆盖：同名文件追加 `_02` / `_03`。拼接产物、工单、清单都可能被导出两次，
    静默覆盖等于把用户第一次导的东西吃掉。
    """
    written: list[Path] = []
    for item in items:
        target_dir = dest_dir / item.subdir if item.subdir else dest_dir
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ExportError(f"建不了子目录：{item.subdir}（{e}）", 500) from e
        out = _dedupe(target_dir, item.filename)
        if item.src is not None:
            try:
                shutil.copyfile(item.src, out)
            except OSError as e:
                raise ExportError(f"写不进去：{out.name}（{e}）", 500) from e
        else:
            try:
                out.write_bytes(item.read())
            except OSError as e:
                raise ExportError(f"写不进去：{out.name}（{e}）", 500) from e
        written.append(out)
    return {"kind": "dir", "path": str(dest_dir), "count": len(written)}


def payload_file(path: Path) -> dict[str, Any]:
    """单文件直写后的 `file` 载荷。"""
    return {
        "kind": "file",
        "path": str(path),
        "filename": path.name,
        "size": path.stat().st_size,
    }
