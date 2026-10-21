# GachaBench 实施文档

> 通用 ComfyUI 视频抽卡工作台。把「15s 一次性生成」拆成「3×5s 分块生成」，把抽出来的片子归类（草稿 → 正片 / 废稿），并对分镜做**定点定位 + 改动工单**。
>
> 状态：**设计已定稿**（经 grill 逐分支确认）。本文档是唯一施工依据。
> 项目路径：`d:\Notely\workspace\AIGC\GachaBench\`

---

## 1. 目标与范围

|  | 内容 |
| --- | --- |
| **要做的** | ① 工作区管理（选目录 → 自动建 5 个文件夹）② 15s→3×5s 拆分（甲/乙双策略）③ 抽卡台（草稿/正片/废稿 + 废稿记录）④ 分镜标签定位 + 改动工单 |
| **通用性** | 不绑定任何具体剧本。`复试2` 只是被注册的**其中一个工作区** |
| **v1 不做** | LLM 自动改提示词（只留 `/api/agent/*` 接口形状，返回 501）；多人协作；云端部署；数据库以外的分布式存储 |

---

## 2. 目录结构

```
GachaBench/
├─ backend/
│  ├─ main.py              FastAPI 入口：挂路由 + 挂静态 + uvicorn 启动
│  ├─ config.py            pydantic-settings；读写 config.json（带文件锁 + 原子写）
│  ├─ config.json          运行时配置（见 §4）
│  ├─ models.py            pydantic 模型：Card / SplitPlan / Job / Workspace / Config
│  ├─ db.py                sqlite3 连接、建表、PRAGMA、轻量迁移
│  ├─ repo.py              cards / split_plans / jobs 的仓储（纯 SQL，无 ORM）
│  ├─ workspaces.py        工作区创建 / 扫描 / 切换 / 重命名
│  ├─ comfy.py             ComfyUI 客户端：提交 / 轮询 / node_errors / 下载产物
│  ├─ splitter.py          15s → 3×5s 拆分（甲 / 乙）
│  ├─ ffmpeg_tools.py      ffmpeg 探测、抽末帧、拼接
│  ├─ prompt_locator.py    分镜标签 → [Shot N] 块定位 + 行号区间
│  ├─ ticket.py            改动工单生成
│  └─ routers/
│     ├─ workspace.py  media.py  cards.py  split.py  generate.py  agent.py
└─ web/
   ├─ package.json  vite.config.ts  tailwind.config.js  tsconfig.json  index.html
   └─ src/
      ├─ main.tsx  App.tsx
      ├─ lib/api.ts          fetch 封装（统一错误处理）
      ├─ lib/query.ts        TanStack QueryClient
      ├─ store/useApp.ts     zustand：当前工作区 / 选中卡 / 筛选
      ├─ components/ui/*     shadcn/ui 组件
      └─ pages/
         ├─ WorkspaceGate.tsx   无工作区时：选父目录 → 创建
         ├─ GachaBoard.tsx      卡片墙 + 筛选
         ├─ CardDetail.tsx      播放器 + 三面板 + 状态操作
         ├─ SplitPanel.tsx      选段 → 甲/乙 → 预览 → 生成 → 跑片
         └─ ShotLocator.tsx     镜号定位 → 高亮 → 工单 → 复制
```

**运行方式**：开发 `python backend/main.py`（:8000）+ `npm run dev`（:5173，Vite proxy `/api`→:8000）；生产 `npm run build` 后由 FastAPI `StaticFiles` 托管 `web/dist`。

---

## 3. 技术栈

**后端**：`fastapi` + `uvicorn[standard]`；`pydantic` v2 + `pydantic-settings`；`httpx`（async，调 ComfyUI / 下载）；**ffmpeg 走 `asyncio.create_subprocess_exec`**（不引 wrapper）；**SQLite 用 stdlib `sqlite3`**（无 ORM）；`StaticFiles` + `CORSMiddleware`。**零第三方重型依赖**。可选 `websockets`（实时进度）。MCP SDK 后置。

**前端**：React 18 + Vite + TypeScript；**shadcn/ui + Tailwind**；TanStack Query（列表 + 跑片轮询）；Zustand；原生 `<video>` + 自写帧步进（±1 帧 / ±0.1s）；`sonner` 做 toast。**不用 react-router**（单页 + 顶栏切工作区）。

**外部依赖**：ffmpeg（PATH 或配置指定）；本地 ComfyUI（默认 `http://127.0.0.1:8188`）。

---

## 4. config.json

```json
{
  "comfyui": {
    "base_url": "http://127.0.0.1:8188",
    "poll_interval_sec": 5,
    "timeout_min": 60,
    "comfy_output_dir": "R:/workspace/A_ComfyUI/ComfyUI/output",
    "input_dir": "R:/workspace/A_ComfyUI/ComfyUI/input"
  },
  "ffmpeg": { "binary": "ffmpeg" },
  "folders": {
    "draft": "草稿", "final": "生成", "prompt": "提示词",
    "storyboard": "分镜表", "asset": "素材"
  },
  "index_dir": ".gachabench",
  "workflow": {
    "json_path": "{prompt}/minimax_h3_多图参考生视频V8 .json",
    "nodes": {
      "prompt": "664", "unet": "620", "ref_root": "136",
      "seconds": "132", "megapixels": "3881",
      "output_prefix": "732", "seed": "142",
      "load_image_ids": ["137", "139", "141"]
    },
    "ref_image_size": "max",
    "models": {
      "hybrid": "DasiwaMinimaxH3_dasiwaHybridV4_int8.safetensors",
      "ref2va": "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    }
  },
  "workspaces": [
    { "name": "复试2", "root": "D:/Notely/workspace/AIGC/复试2", "created_at": "2026-10-15T00:00:00" }
  ],
  "active": "复试2"
}
```

- `{prompt}` / `{final}` / `{draft}` 等占位符在运行时替换为 `active` 工作区的对应子目录。
- `workflow.nodes` 是**关键通用化开关**：不同工作流的节点号不同，改这里即可适配，绝不硬编码。默认值对应 `复试2` 的 `minimax_h3_多图参考生视频V8 .json`（与 `工具_跑草稿.py` 一致）。
- `folders` 默认即「草稿/生成/提示词/分镜表/素材」五个中文名，可整组改名。

---

## 5. 数据模型（SQLite，存于 `<工作区>/.gachabench/cards.db`）

工作区自包含：整个工作区目录拷走，抽卡记录跟着走。

```sql
CREATE TABLE cards (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace    TEXT NOT NULL,
  rel_path     TEXT NOT NULL,          -- 相对工作区根，如 草稿/xxx-audio.mp4
  seg          TEXT,                    -- 段号，如 "04"（可空）
  chunk        TEXT,                    -- "1of3" / "2of3" / "3of3" / "full"（可空）
  status       TEXT NOT NULL DEFAULT 'draft',   -- draft | final | reject
  reject_tags  TEXT DEFAULT '[]',       -- JSON 数组
  reject_note  TEXT DEFAULT '',
  tags         TEXT DEFAULT '[]',       -- 分镜标签，如 ["SH402","SH403"]
  note         TEXT DEFAULT '',
  prompt_md    TEXT,                    -- 来源提示词 md 相对路径
  split_plan_id INTEGER,
  duration     REAL, width INTEGER, height INTEGER, has_audio INTEGER,
  created_at   TEXT, updated_at TEXT,
  UNIQUE(workspace, rel_path)
);

CREATE TABLE split_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace TEXT, seg TEXT, strategy TEXT,   -- "jia" | "yi"
  source_md TEXT, manifest_json TEXT, created_at TEXT
);

CREATE TABLE jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace TEXT, seg TEXT, chunk TEXT,
  prompt_file TEXT, comfy_prompt_id TEXT,
  status TEXT,          -- queued | running | done | error
  log_tail TEXT, created_at TEXT, updated_at TEXT
);
```

**媒体探测**（时长/分辨率/是否有音轨）走 `ffprobe`，入库时一次性写入。

---

## 6. API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 + 上报 ffmpeg / ComfyUI 可用性 |
| GET/PUT | `/api/config` | 读写配置 |
| GET | `/api/workspaces` | 列表 + active |
| POST | `/api/workspaces` | body `{parent_dir, name}` → 建 5 文件夹 + `.gachabench/` + 注册 |
| POST | `/api/workspaces/{name}/activate` | 切换 |
| PATCH | `/api/workspaces/{name}` | 重命名 |
| DELETE | `/api/workspaces/{name}` | 仅从列表移除（不删盘） |
| POST | `/api/workspaces/{name}/scan` | 遍历 草稿/ + 生成/ 登记新卡（幂等 upsert） |
| GET | `/api/cards` | 筛选：`status/seg/tag/q` |
| GET | `/api/cards/{id}` | 详情 |
| PATCH | `/api/cards/{id}` | 改 `status/reject_tags/reject_note/note/seg/tags` |
| POST | `/api/cards/bulk` | 批量改状态 |
| POST | `/api/cards/{id}/to-final` | body `{copy_to_final: bool}`；可选复制成 `生成/NN.mp4` |
| GET | `/api/media/{card_id}` | 视频流，**支持 Range**（Starlette FileResponse） |
| GET | `/api/segments` | 扫描工作区可用段号（从提示词 md / 卡池推断） |
| GET | `/api/segments/{seg}/prompt` | 读 md 的 §④ ```text 块 + 各分节 |
| GET | `/api/segments/{seg}/shots` | 解析 `[Shot N]` → 镜号/时间码/行号区间/摘要 |
| POST | `/api/segments/{seg}/split` | body `{strategy}` → 生成 3 txt + manifest |
| POST | `/api/split/{plan_id}/generate` | body `{chunk, carry_last_frame, prev_card_id}` → 提交本地跑片 |
| GET | `/api/jobs/{id}` | 进度轮询 |
| POST | `/api/ffmpeg/last-frame` | body `{card_id}` → 抽末帧 PNG |
| POST | `/api/ffmpeg/concat` | body `{card_ids:[...]}` → 拼接 |
| POST | `/api/ticket` | body `{seg, shots:[], comment}` → 工单文本 |
| POST | `/api/agent/apply` | **预留**，返回 501 |

---

## 7. 核心模块

### 7.1 splitter —— 15s → 3×5s

**输入**：`{prompt}/NN_正片_第N段_15s.md` 的 §④ ```text 代码块（见 `04_正片_第4段_15s.md`）。

**步骤**
1. **切节**：把块切成 `subject_definitions / summary / retention_analysis / detailed_description / overall_soundscape / non_diegetic_music` 六节。
2. **提镜**：从 `detailed_description` 按行提取 `[Shot N]` 块（起始行正则 `^\[Shot (\d+)\]`，首镜无 `At`，其余含 `At 00:0X.XXX,`）。
3. **取镜长**：从 md 的 §③bis 镜表解析每镜时长与景别（用于切分与 manifest）。
4. **算切点（按镜组、不截断整镜）**：
   - **甲**：镜组自然边界累计 → 每块请求时长 = **ceil(组时长)**（≥1）。`04` → 5.4/5.0/4.6 → **6/5/5 ＝ 16s**。**不裁尾，由用户自理。**
   - **乙**：每块**定长 5s**，段内各镜时长**等比缩放**使和为 5.0。`04` → **5/5/5 ＝ 15s**，拼回严丝合缝。
5. **生成每块 txt**（关键：**只裁剪 + 重编号 + 时间码重基 + 改抬头，正文用词一字不改**）：
   - `subject_definitions` / `overall_soundscape` / `non_diegetic_music`：**原样保留**（画风/人物/场景锚不能丢）
   - `summary`：按本块镜内容重写一句 + 保留 `[reference generation ...]` 抬头口径
   - `retention_analysis`：只留本块出现的 `<Subject N>`，`[Shot k]` 重编号 1..k
   - `detailed_description`：只留本块镜块；`At 00:0X.XXX` **重基到 0**（乙再按比例缩放）
   - 抬头：`时长 X 秒｜k 镜｜带音频`
6. **落盘**：`{prompt}/拆分/NN_第N段/NN_第N段_1of3.txt|2of3.txt|3of3.txt` + `NN_第N段_拆分清单.json`（manifest：策略、每块镜号列表、原/新时长、txt 路径、接续关系）。
7. 记 `split_plans`。

**不变式**：`<Picture N>` / `<Subject N>` / `<Audio N>` 标签体系与编号顺序**绝不改动**；只在块内重编号 `[Shot N]`。否则 H3 的标签引用会断。

### 7.2 连续性（继承上一段末帧）

`carry_last_frame=true` 时：
1. 取上一块卡（`prev_card_id`）的 mp4 → `ffmpeg -sseof -0.05 -frames:v 1` 抽末帧 → 存 `{draft}/_carry_{seg}_{chunk}.png`；
2. 该 PNG 作为**额外参考图**接到 `ref_root.ref_images.ref_image_{N}`（N = 现有图数量）；
3. 提交的提示词里同步声明 `<Picture {N+1}> is ... the exact last frame of the preceding segment ...` 并加一句接续描述（如 *"this shot continues seamlessly from the previous segment's final frame"*）——**这一步由 splitter 的 manifest 记录，跑片前自动补进 txt**。
4. 兜底：若提示词里未声明该 `<Picture>`，则只接图不加句（不强改正文）。

### 7.3 comfy.py —— 客户端

复用 `工具_跑草稿.py` 的修补逻辑，但**节点号全部读 `config.workflow.nodes`**：
- 写入 prompt 到 `nodes.prompt`；设定 `nodes.seconds`（整秒）、`nodes.megapixels`、`nodes.output_prefix`（`草稿/<tag>`）、`nodes.seed`（随机 1..1125899906842624）
- 参考图接线：`nodes.load_image_ids` 前若干位复用，超出则动态新建节点；`ref_root.ref_images.ref_image_{i}`
- 参考音频（可选）：`LoadAudio` → `ref_root.ref_audios.ref_audio_{i}`，**先清空既有 `ref_audios.*` 与 `ref_video_audios.*`**
- 提交前**强制查 `node_errors`**；非空即中止并回传错误
- 轮询 `GET /history/{pid}` + `/queue`；完成后从 `comfy_output_dir` 复制产物到 `{draft}/`
- **`.gachabench` 口径**：会同时产出「纯视频」与「`-audio.mp4`」两个文件，抽卡台默认用**带 `-audio` 的**（有音轨）

### 7.4 抽卡（cards）

- **扫描**：遍历 `{draft}` 与 `{final}` 的 `.mp4`，按 `rel_path` 幂等 upsert。
- **段号解析三路兜底**：① 跑片时写入的 `seg/chunk/prompt_md`（最准）；② 文件名前缀正则 `^(\d{2})[_\-]` 或 `第(\d+)段`；③ 界面**手动指派**。
- **正片**：`PATCH status=final`；勾选 `copy_to_final` 时复制成 `{final}/NN.mp4`（**原件留在 `草稿/`，不搬不删**）。
- **废稿**：`status=reject` + `reject_tags` + `reject_note`。
- **预置废稿标签**（按 `04` 词表，可改）：`人物崩 / 口型不准 / 串词 / 道具复读 / 运镜糊 / 画风漂 / 时长不符 / 手部穿模 / 闪黑异常 / 其他`。

### 7.5 prompt_locator.py —— 分镜标签定位

1. 读 `NN_正片_第N段_15s.md` 原文，定位 §④ ```text 代码块起止行；
2. 块内正则匹配 `^\[Shot N\]` → 记录每个镜块的**文件绝对行号区间**；
3. 标签解析：
   - `SH401`（剧镜号）↔ `[Shot 1]`：`SH` + 段号后两位 + 镜序；
   - `#13-#14`（台词编号）：从 §⑤ / §⑥ 表格解析后映射到镜块；
4. 支持混写（`SH402,#13`），逐个解析并返回命中集合。

### 7.6 ticket.py —— 改动工单（方案 1）

输出纯文本，一键复制给外部 Agent：

```
【GachaBench 定点改动工单】
工作区：复试2
文件：D:/Notely/workspace/AIGC/复试2/提示词/04_正片_第4段_15s.md
位置：§④ 代码块 · [Shot 2]（文件第 134 行）
镜号：SH402 ｜ 时间码 00:02.500–00:05.400 ｜ 时长 2.9s ｜ 景别 INS ｜ 机位 特写位（俯拍茶几）
原文：
<[Shot 2] 整段原文>
你的意见：<用户输入>
要求：只改这一段；保持 <Subject N>/<Audio N> 标签体系与前后镜衔接不变；不改其它镜；改完备份原文件。
```

### 7.7 ffmpeg_tools.py

- 启动时探测 ffmpeg/ffprobe（`config.ffmpeg.binary` 或 PATH），不可用则 `/api/health` 报警；
- **抽末帧**：`-sseof -0.05 -frames:v 1 -q:v 2`；
- **拼接**：优先 concat demuxer + `-c copy`（同编码），否则重编码；
- **媒体探测**：`ffprobe -show_streams -of json` → 时长/宽高/音轨。

---

## 8. 前端

- **WorkspaceGate**：无工作区时让用户选父目录 → `POST /api/workspaces` → 建 5 文件夹 → 进主界面。
- **GachaBoard**：卡片墙。筛选项＝状态（草稿/正片/废稿）、段号、分镜标签、关键词。卡片上 `<video muted preload="metadata">` 悬停预览，点击进详情。
- **CardDetail**：
  - 大播放器：`<video>` + 帧步进（`currentTime ± 1/25`）+ ±0.1s + 循环 + 音量；
  - **三面板 Tabs**：`提示词`（读 md 的 §④/§⑤ 摘录）、`素材`（人物 · 场景 · 音频 三组，读 `{asset}` 目录）、`分镜`（读 `分镜脚本_第12集第N段15s.md` 的镜表）；
  - 状态操作：`正片`（含「复制到 生成/」勾选）/ `废稿`（标签 chips + 文本）；
  - **分镜标签输入框**：写 `SH402` / `#13-#14` → 打 tag（A）→ 可跳转定位（B）。
- **SplitPanel**：选段 → 选 **甲/乙** → 预览 3 块切分表（镜号 / 原时长 / 目标秒数）→ 生成 txt → 逐块跑片（含「继承上一段末帧」开关 + 选上一块卡）。
- **ShotLocator**：输入镜号 → 高亮原文 + 行号 → 写意见 → 生成工单 → 一键复制。
- 轮询：TanStack Query `refetchInterval` 拉 `/api/jobs/{id}`。

---

## 9. 实施步骤

| 阶段 | 交付 | 验收 |
| --- | --- | --- |
| **P0 骨架** | backend `main.py` + `/api/health`；web Vite + shadcn + Tailwind 壳 | `python backend/main.py` 与 `npm run dev` 均能起 |
| **P1 工作区** | config 读写 + 建 5 文件夹 + 注册/切换；WorkspaceGate、GachaBoard 空态 | 选一个目录能生成 5 个文件夹并出现在列表 |
| **P2 卡片 + 媒体** | 扫描 + cards CRUD + `/api/media`（Range）+ ffprobe 探测；卡片墙、详情、正片/废稿（标签 + note） | 在 `复试2` 扫描后卡池有片；状态写入库、mp4 未移动 |
| **P3 拆分** | prompt 解析 + splitter（甲/乙）+ 产物 txt/manifest；SplitPanel 切分预览 | 对 `04`：甲得 6/5/5 三 txt、乙得 5/5/5 三 txt，均含 `subject_definitions` |
| **P4 跑片** | comfy 客户端 + jobs + 进度轮询 + 产物自动入库；「继承上一段末帧」 | 提交 `127.0.0.1:8188` 能产出并自动成卡 |
| **P5 定位 + 工单** | prompt_locator + ticket；ShotLocator + 一键复制 | `SH402` 能给出 §④ 中 `[Shot 2]` 的准确行号区间与原文 |
| **P6 收尾** | 拼接按钮；统计（可选）；`/api/agent/*` 占位 501 | 三段能拼回；占位接口可访问 |

---

## 10. 验收标准（总）

1. 在 `复试2` 上：`草稿/` + `生成/` 的视频全部出现在卡池，段号自动识别；
2. 点「正片」/「废稿」→ 状态正确落库，**mp4 位置不变**；勾选复制时 `生成/NN.mp4` 出现且原件保留；
3. 对 `04` 段：**甲** 产出 `6/5/5`（拼出 16s，不裁尾）；**乙** 产出 `5/5/5`（拼回 15s）；
4. `SH402` 定位：能给出 `[Shot 2]` 的**准确行号区间与整段原文**，工单可一键复制；
5. 跑片：提交本地 ComfyUI 能产出、自动入库为「草稿」卡，并优先取 `-audio.mp4`。

---

## 11. 风险与降级

| 风险 | 降级 |
| --- | --- |
| 拆分重写破坏 H3 对 `[Shot N]` / `<Subject N>` 的依赖 | **铁律**：正文字词一字不改，只裁剪/重编号/时间码重基；标签编号顺序不动 |
| 工作流节点号硬编码 | 全部走 `config.workflow.nodes`，换工作流只改配置 |
| ffmpeg 未安装 | 启动探测 + `/api/health` 报警，抽帧/拼接功能禁用而非崩溃 |
| 大文件播放卡 | `FileResponse` 天然支持 Range；必要时加 `preload="metadata"` |
| 甲乙混用时拼接时长不一致 | manifest 记录每块真实时长；拼接页给出总时长提示，不自动裁 |

---

## 12. 待定项

- **废稿预置标签**：已按 `04` 词表预置 10 条，可按需增删（存 `config.json` 或常量）。
- **云端口径**：v1 只做**本地** ComfyUI；云端 API（`工具_跑API云端.py` 的 U30）留作后续 worker 类型。
