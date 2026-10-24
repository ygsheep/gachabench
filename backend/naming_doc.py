# -*- coding: utf-8 -*-
"""自动生成 `{资产目录}/README.md`：给外部 Agent 看的命名规范。

## 为什么要有这份文件

外部 Agent 没有这个项目的代码，也只有文件系统。它要往 `素材/` 里放图、
写提示词 sidecar，就得知道「名字长什么样、sidecar 放哪、什么不能写进名字」。
把规范写在代码注释里等于没写 —— 它读不到。

## 为什么不干脆人工维护

规范会变（这次就改了三版）。人工维护的文档必然落后于代码，而**落后的规范
比没有规范更糟**：Agent 会照着旧规则生成一批解析不了的文件。
所以文档内容由代码生成，跟着代码一起变。

## 覆盖策略（绝不闷头覆盖用户的文件）

- 文件不存在 → 写
- 文件存在、**首行有我们的标记**、且内容摘要与当前版本不一致 → 重写
- 文件存在、首行有标记、摘要一致 → 不动（幂等，不刷新 mtime）
- 文件存在、**没有标记** → 一个字节都不碰（那是用户自己写的 README）

要改成自己的版本，删掉首行标记即可，系统从此不再管它。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

README_NAME = "README.md"
MARKER_PREFIX = "<!-- gachabench:naming-doc"

_TEMPLATE = """{marker}

# 命名规范（外部 Agent 读这份）

> 本文件由 GachaBench 自动生成并维护，随代码更新。想自己改写，删掉上面那行标记，
> 系统就再也不会碰它。

## 一句话

**名字回答「这是什么」，索引回答「什么时候能用」。适用区间绝不写进文件名。**

## 两类东西

| 类别 | 形态 | 例子 |
| --- | --- | --- |
| 镜头类（属于某一段的产物） | `段[.块]_用途.ext` | `01.1_成片.mp4` |
| 素材类（全局复用的锚） | `主体_自由描述_用途.ext` | `白兴学_国漫3D_四视图.png`、`教室_破坏_参考.png` |

判定规则：按 `_` 切分后，第一个字段是纯数字 `NN` 或 `NN.N` → 镜头类；否则素材类。

## 硬规则

1. **分隔符只有 `_`**。段与块的层级用**点**：`01.1`。
   不要写 `01_1` —— 那会让 `_` 同时表示「层级」和「字段边界」，解析只能靠猜。
2. **镜头类不要放主体名**。一条提示词常有多个角色（`<Subject 1>` `<Subject 2>`），
   名字里只放得下一个，群戏必然错。镜头的位置已经唯一确定它是谁。
3. **不要写镜号**。一块（1of3）= 一镜 = 一次生成。
4. **媒体类型不进名**，`.mp4` / `.png` 已经说了。用途词表：`参考` / `成片` / `配音` / `分镜`。
5. **重名用 `_02`、`_03`**（末尾两位数字）。系统按序号读，不覆盖已有文件。
6. **主体名和字段内部不要出现 `_` 或空格**；需要空格请用 `-`（`刘强-Q版.png`）。
7. **不要改已有素材的文件名**。提示词里的 `<Picture N>` 直接写文件名，改名会让
   引用它的段提示词**静默失效**（跑片时只表现为参考图缺失，你不会知道是改名引起的）。

## 提示词 sidecar（外部 Agent 写，系统只读）

每张素材旁边放一个**同名** JSON：

~~~
资产/白兴学_国漫3D_四视图.png
资产/白兴学_国漫3D_四视图.prompt.json
~~~

结构（`schema = 1`）：

~~~jsonc
{
  "schema": 1,
  "subject": "白兴学",             // 可选
  "variant": "国漫3D_四视图",       // 可选
  "prompt": "生成这张图的提示词",    // 主字段
  "negative": "",                  // 可选
  "caption": "白衣少年正面站立",     // 可选，供视觉匹配/以图搜图
  "params": { "model": "...", "steps": 30 },
  "extra": {}                      // 可选；未识别的顶层键也会被收进这里
}
~~~

规则：

- 系统**永不回写** sidecar。删库重扫能整表重建，文件是唯一真相。
- 未识别的顶层键不丢，会存进 `extra`。
- 坏 JSON / 空文件只降级成「这张图没有提示词」，不会让扫描失败。
- `caption` 是「图片识别」那一路用的：不靠文字命中也行，按语义匹配。
- **不要在 sidecar 里写适用区间**（下面解释）。

## 适用区间：只在索引里，不在文件里

同一个主体在不同段落可能要不同版本，例如：

- `教室_破坏` 用于第 5–9 段
- `教室_修复` 用于第 10 段起

跑到第 12 段时，系统自动选 `教室_修复`，不需要人工指。

规则：

- 区间是 **一对多** 的：同一条素材可以有多段（修好了又坏了可以再开一段）。
- **终点可随时改**，因为事前没人知道它什么时候失效。
- 区间**不写进文件名**：一改区间就得改名，所有引用它的提示词同时失效。
- 外部 Agent 读不到、也写不了区间。要改请走界面或接口。

## 目录结构

~~~
资产/                      ← **所有素材的总库**（长期、全量、可复用）
  人物/ 场景/ 风格参考/ 其他/   ← 按 类别/主体 归档
  未分类/                   ← 「通过」的成片复制到这里，等你归类
  README.md                 ← 本文件

素材/                      ← **当前片段要用的备料区**，按段分目录
  01/ 02/ 03/ …
    白陌_写实_四视图.png     ← 文件名 == 提示词里 <Picture N> 写的名字
                           ← 想换素材直接换这个文件，提示词一个字都不用改
~~~

备料区是那一段的**快照**，不是真相：改它不伤资产库。跑片时优先取备料区里
同名的那一份，取不到才回到资产库里模糊匹配。

## 一句话给 Agent

往 `资产/` 放图时：**文件名用 `主体_描述_用途.ext`，旁边配一个同名 `.prompt.json`，
不要动已有文件的文件名，不要在名字里写第几段。**
不要往 `素材/<段>/` 里放新素材 —— 那是跑片前的备料位，由系统或人按提示词填。
"""


def render() -> str:
    """当前规范全文（含标记行）。内容由代码生成，改内容就改这里。"""
    # 先算正文摘要，再把摘要写进标记行：标记必须能反映「正文是不是这一版」
    body = _TEMPLATE.replace("{marker}", "@@MARKER@@")
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()[:8]
    marker = f"{MARKER_PREFIX} sha={sha} -->"
    return body.replace("@@MARKER@@", marker)


def _marker_sha(text: str) -> Optional[str]:
    """读出文件首行标记里的摘要；没有标记返回 None。"""
    first = text.splitlines()[0] if text else ""
    if not first.startswith(MARKER_PREFIX):
        return None
    tail = first[len(MARKER_PREFIX):].strip().rstrip("-->").strip()
    return tail.split("=", 1)[-1].strip() or None


def ensure_readme(asset_dir: str | Path) -> dict:
    """在资产目录写/更新 README.md。返回 `{path, written, reason}`，**永不抛异常**。

    reason ∈ created / updated / current / foreign / missing-dir / error
    """
    try:
        d = Path(asset_dir)
    except (TypeError, ValueError):
        return {"path": None, "written": False, "reason": "error"}
    if not d.is_dir():
        # 不代建目录：资产目录还没配好时，写个 README 也没人用得上
        return {"path": None, "written": False, "reason": "missing-dir"}

    target = d / README_NAME
    content = render()
    sha = _marker_sha(content) or ""

    if target.exists():
        try:
            old = target.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            return {"path": str(target), "written": False, "reason": f"error: {exc}"}
        old_sha = _marker_sha(old)
        if old_sha is None:
            # 用户自己写的 README：一个字节都不碰
            return {"path": str(target), "written": False, "reason": "foreign"}
        if old_sha == sha:
            return {"path": str(target), "written": False, "reason": "current"}
        reason = "updated"
    else:
        reason = "created"

    try:
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return {"path": str(target), "written": False, "reason": f"error: {exc}"}
    return {"path": str(target), "written": True, "reason": reason}


def ensure_for_workspace(cfg, ws) -> Optional[dict]:
    """按工作区配置写 README。资产目录未确定时返回 None（不猜、不代建目录）。"""
    if ws is None:
        return None
    name = cfg.folder_name("asset", ws)
    if not name:
        return None
    return ensure_readme(Path(ws.root) / name)
