# GachaBench

通用 ComfyUI 视频抽卡工作台：把「15s 一次性生成」拆成「3×5s 分块生成」，把抽出来的片子归为草稿 / 正片 / 废稿，并对分镜做定点定位与改动工单。

FastAPI + SQLite + React 单机应用，也可打包成双击即用的 Windows 桌面程序。

## 功能

| 模块 | 能力 |
| --- | --- |
| **工作区** | 选目录建档，自动创建 `草稿 / 生成 / 提示词 / 分镜表 / 素材 / 原著` 六个目录和 `.gachabench/cards.db`；工作区可整体拷走，抽卡记录跟着走 |
| **抽卡台** | 扫描 `草稿/` 与 `生成/` 的视频入库；卡片墙筛选、悬停预览、详情播放（帧步进 / ±0.1s / 循环）；标记正片或废稿（标签 + 备注） |
| **拆分** | 读提示词 md 的 §④ 代码块，按镜组切分 15s → 3 块；**甲**：按镜组自然边界取整；**乙**：定长 5s 等比缩放。只裁剪 / 重编号 / 时间码重基，正文字词一字不改 |
| **跑片** | 提交本地 ComfyUI；参考图 / 参考音频接线；可继承上一块末帧；任务进度轮询、中断、启动补收 |
| **定位与工单** | `SH402`、`#13-#14` 等分镜标签 → `[Shot N]` 块与文件绝对行号；生成可一键复制的定点改动工单 |
| **素材库** | 递归扫描 `素材/`，按人物 / 场景 / 风格 / 其他分组；上传自动按「主体名_用途」落位；为跑片挑选参考图与音频 |
| **原著库** | 导入 txt / md，自动识别 GBK、切章、净化，落盘 `原著/<书名>/0001 第一章 xxx.md`；摘录片段供拆分引用 |
| **联网采集** | 零依赖实现 Legado 书源规则子集；内置 68 个书源。**默认关闭**，需确认免责声明后启用，采集只到文本 |
| **导出** | 统一三态契约：浏览器下载 / 单文件直写 / 目录写入；多条目自动打 zip；支持按段、按书章节、按卡状态做范围导出 |
| **设置** | 连接、跑片参数、工作流节点映射、目录角色、界面偏好；参数越界后端钳制 |

## 技术栈

- **后端**：Python 3.12、FastAPI、uvicorn、httpx、pydantic v2、stdlib `sqlite3`（无 ORM）、stdlib `html.parser`（书源解析）、ffmpeg / ffprobe 子进程
- **前端**：React 19、TypeScript 6、Vite 8、Tailwind CSS 4、shadcn/ui、TanStack Query 5、zustand 5
- **桌面**：Tauri v2 壳 + PyInstaller onedir sidecar（含 ffmpeg，运行机无需另装）
- 后端直接依赖仅 6 个，保持「零重型依赖」；不引入 lxml / QuickJS / 字体解密库

## 快速开始

### 环境要求

- Python 3.12 + [uv](https://docs.astral.sh/uv/)
- Node.js ≥ 20
- ffmpeg / ffprobe（在 PATH，或在配置里指定绝对路径）
- 本地 ComfyUI（默认 `http://127.0.0.1:8188`，可选）

### 开发模式

后端（`http://127.0.0.1:8000`）：

```powershell
cd backend
uv sync
uv run uvicorn main:app --port 8000
```

也可以用 `python backend/main.py` 启动，自带 `--reload`。

前端（`http://127.0.0.1:5173`，Vite 把 `/api` 代理到 8000）：

```powershell
cd web
npm install
npm run dev
```

### 生产模式（单端口）

```powershell
cd web
npm run build

cd ..\backend
uv run uvicorn main:app --port 8000
```

`web/dist` 由 FastAPI 的 `StaticFiles` 托管，API、SPA、媒体全部同源。

### 首次启动

`backend/config.json` 不在版本库中，首次启动会自动生成默认配置。至少确认这两项：

- `comfyui.comfy_output_dir`、`comfyui.input_dir`：ComfyUI 的输出 / 输入目录
- `workflow.json_path`：工作流 JSON 路径，`{prompt}` 会替换成当前工作区的 `提示词/` 目录

工作流节点号优先按 `class_type` 自动探测，探测失败才回退到 `config.workflow.nodes`。换工作流通常只需改这一处配置，不需要改代码。

## 目录结构

```
GachaBench/
├─ backend/                  FastAPI 后端
│  ├─ main.py                入口：挂路由、托管 web/dist、uvicorn
│  ├─ config.py              config.json 读写（文件锁 + 原子写）
│  ├─ runtime.py             源码 / 冻结两套路径布局、端口探测
│  ├─ workspaces.py          工作区建档、扫描、切换、目录角色
│  ├─ comfy.py               ComfyUI 客户端：接线、提交、轮询、下载
│  ├─ splitter.py            15s → 3×5s（甲 / 乙）
│  ├─ prompt_locator.py      分镜标签 → [Shot N] 与行号区间
│  ├─ ticket.py              定点改动工单
│  ├─ ingest.py / reconcile.py  产物入库与补收
│  ├─ ffmpeg_tools.py        抽末帧、拼接、ffprobe
│  ├─ library.py             本地原著库（导入、切章、摘录）
│  ├─ booksource.py          书源引擎（stdlib 实现）
│  ├─ export.py / exportrange.py  导出通道与范围导出
│  ├─ textclean.py           外来文本净化
│  ├─ db.py / repo.py        SQLite 建表与仓储
│  ├─ routers/              workspace / media / cards / split / generate / locate
│  │                        / assets / library / search / export / system / config_api
│  │                        / workflows / ffmpeg / agent
│  └─ tests/                 unittest 单测
├─ web/                      React 前端
│  └─ src/
│     ├─ App.tsx             hash 视图切换
│     ├─ pages/              GachaBoard / SplitPanel / ShotLocator / AssetLibrary
│     │                      LibraryPage / NovelSearch / SettingsPage / WorkspaceGate
│     ├─ components/         Rail / TaskBar / Reviewer / RefStrip / FilmCell …
│     └─ lib/                api / health / prefs / swrCache / download / types
├─ desktop/                  Tauri v2 桌面壳（只负责拉起后端并导航）
├─ packaging/                一键打包脚本、PyInstaller spec、图标
├─ prototype/                设计原型留档
├─ docs/                     整合设计文档
└─ SPEC.md                   最初的设计定稿与验收标准
```

前端共 7 个视图，用 hash 同步，可直接分享单屏链接：

`#/board` 抽卡台 · `#/split` 拆分跑片 · `#/locator` 定位工单 · `#/assets` 素材库 · `#/library` 原著库 · `#/search` 小说搜索 · `#/settings` 设置

## API 概览

所有接口挂在 `/api` 下，媒体流支持 HTTP Range。

| 组 | 主要接口 |
| --- | --- |
| 健康与配置 | `GET /api/health`、`GET/PUT /api/config` |
| 工作区 | `GET/POST /api/workspaces`、`/inspect`、`/open`、`/{name}/activate`、`/{name}/folders`、`/{name}/scan` |
| 卡片 | `GET /api/cards`、`/{id}`、`PATCH /{id}`、`/bulk`、`/{id}/to-final`、`GET /api/media/{id}` |
| 拆分 | `GET /api/segments`、`/{seg}/prompt`、`/{seg}/shots`、`/{seg}/chunks`、`POST /{seg}/split` |
| 跑片 | `POST /api/split/{plan_id}/generate`、`/{plan_id}/preview`、`/{plan_id}/refs/*`、`GET/POST /api/jobs` |
| 定位 | `GET /api/segments/{seg}/locate`、`POST /api/ticket` |
| 素材 | `GET /api/assets`、`/scan`、`/target`、`POST /upload`、`GET /file/{rel}` |
| 原著库 | `POST /api/library/import`、`/books`、`/chapters/{id}`、`/chapters/{id}/excerpt` |
| 联网采集 | `GET /api/search/status`、`/sources`、`POST /api/search`、`/toc`、`/chapter`、`/import` |
| 导出 | `POST /api/export`、`POST /api/export/range` |
| 系统 | `GET /api/system/folder-picker`、`POST /api/system/pick-folder` |
| 预留 | `POST /api/agent/apply`（501，v1 不做 LLM 自动改提示词） |

## 测试

```powershell
# 后端
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests

# 前端
cd web
npm test
npm run lint
```

## 打包为桌面应用

```powershell
uv run --project backend --with pyinstaller python packaging/build.py
```

流水线：前端构建 → 暂存 ffmpeg → PyInstaller onedir → 搬入 Tauri resources → `tauri build`。产物在 `desktop/src-tauri/target/release/`。

构建机需要 Node、Rust、MSVC C++ 工具链与 ffmpeg；运行机不需要任何一样。详见 [packaging/README.md](packaging/README.md)。

## 设计约束

这几条是后续所有改动都必须服从的：

1. **标签体系不动**：`<Picture N>` / `<Subject N>` / `<Audio N>` 的编号与顺序绝不改动；拆分只在块内重编号 `[Shot N]`。
2. **工作区自包含**：所有索引与记录都在 `<工作区>/.gachabench/cards.db`，目录拷走即可继续用。
3. **正文字词一字不改**：拆分只做裁剪、重编号、时间码重基，保证工单行号可信。
4. **零彩度界面**：全站 R=G=B，状态只靠形状与明度表达。
5. **联网采集默认关闭**：只用于有权的文本，只到「文本」为止，不做字体解密，也不承诺「一键出剧本」。

## 文档

- [SPEC.md](SPEC.md)：原始设计定稿、数据模型、实施阶段与验收标准
- [docs/REDESIGN-PRD.md](docs/REDESIGN-PRD.md)：产品重设后的信息架构、页面模块与验收标准
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：重构后的运行时形态、模块边界、数据与接口设计、库选型
- [docs/INTEGRATION-novel-booksource-engine.md](docs/INTEGRATION-novel-booksource-engine.md)：原著库 / 书源采集 / 导出 / 设置等整合项的设计与落地记录
- [packaging/README.md](packaging/README.md)：桌面打包架构与构建参数
- [desktop/README.md](desktop/README.md)：Tauri 壳的设计取舍

## License

[MIT](LICENSE)
