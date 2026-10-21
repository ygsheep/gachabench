# GachaBench × novel-booksource-engine 整合设计方案

> 状态：**方案待拍板**，不含代码实现。  
> 盘点时间：2026-10-17。基于两个仓库当前工作副本（GachaBench 后端 5,826 行；NBE 引擎 + 服务 2,400 行 / 前端 2,300 行）。  
> 命名约定：**GB** = GachaBench，**NBE** = novel-booksource-engine。

---

## 0. 结论先行

**两个项目的业务重叠度接近于零**：GB 是「把剧本变成片子」的生产端，NBE 是「把网文变成纯文本」的采集端。所以「整合功能」这件事得拆成两种含义：

| 含义                | 具体内容                                       | 判断                                      |
| ----------------- | ------------------------------------------ | --------------------------------------- |
| **A. 整合能力（能力复用）** | 把 NBE 的工程件（导出通道、缓存策略、设置模型、文本卫生、并发调度）搬进 GB  | ✅ **值得做 5 项，其中 3 项零新依赖**                |
| **B. 整合业务（链路打通）** | 用 NBE 的书源采集，为 GB 补上「原著 → 剧本 → 提示词 md」的上游输入 | ⚠️ **只能补到「文本」这一步**；真正缺的「改编」不在 NBE 能力范围内 |

三条必须先说清的判断：

1. **GB 的真实缺口不是「找不到小说」，是「输入侧没有入口」。** 现在 `提示词/NN_正片_第N段_15s.md` 这种结构化 md 全部靠外部手工/AI 产出，GB 只从「md 已存在」往下做。这个缺口 NBE 只能填一半（给文本），另一半是「文本 → §①–§⑥ 结构」的改编，而 GB v1 明确不做 LLM（`/api/agent/*` 返回 501）。**所以联网采集的性价比远低于直觉。**
2. **真正高性价比的整合在「基建」而不是「业务」。** NBE 已经踩完了一整套「FastAPI + React + Tauri 双形态」的坑：前端自主托管 + IPC 取后端地址（GB 的白屏遗留项正是缺这个）、web/desktop 双形态导出契约、localStorage 版本化设置、stale-while-revalidate 首屏缓存。这四件搬过来，**新增第三方依赖为 0**。
3. **UI 一个组件都搬不动。** NBE 是 React 18 + JSX + Vite 5 + 铜金双主题；GB 是 React 19 + TS 6 + Vite 8 + 零彩度金属灰 `--gb-*` 设计系统。只能借「交互模式」，样式必须重写。

**如果只做三件事**：`C1 文本卫生` → `O2 首屏缓存` → `P2 设置视图`。三件都是零新依赖、边界清晰、用完就能看见。

---

## 1. 当前项目（GachaBench）结构盘点

### 1.1 定位与不变式

通用 ComfyUI 视频抽卡工作台：把「15s 一次性生成」拆成「3×5s 分块生成」，把抽出来的片子归为草稿 / 正片 / 废稿，并对分镜做定点定位与改动工单。

三条不能破的东西（后续任何整合都必须服从）：

| 不变式            | 内容                                              | 整合时的约束                     |
| -------------- | ----------------------------------------------- | -------------------------- |
| **I-1 标签体系不动** | `<Picture N>/<Subject N>/<Audio N>` 编号与顺序绝不改动   | 任何「自动改写提示词」的能力（含脚本沙箱）都与之冲突 |
| **I-2 工作区自包含** | 整个工作区目录拷走，抽卡记录跟着走（`<工作区>/.gachabench/cards.db`） | 新数据（书库）必须能随工作区走，不能只存全局     |
| **I-3 零彩度**    | 全站 R=G=B，无彩色；状态只靠形状 + 明度                        | 外来 UI 一律重写，不许带主题色          |

### 1.2 模块地图

```
GachaBench/
├─ backend/                        5,826 行，FastAPI + sqlite3(stdlib) + ffmpeg 子进程
│  ├─ main.py          218   入口：路由挂载 → 静态托管 web/dist → uvicorn；lifespan 扫僵尸任务
│  ├─ config.py        228   config.json 读写（文件锁 + 原子写）；{prompt}/{draft} 占位符解析
│  ├─ runtime.py       186   冻结态路径（_MEIPASS/web/dist）、端口探测、stdout 兜底
│  ├─ workspaces.py    314   建档（5 文件夹 + .gachabench）、扫描、切换、重命名
│  ├─ comfy.py         783   ComfyUI 客户端：build/接线/提交/node_errors/轮询/下载（最大单文件）
│  ├─ splitter.py      342   15s → 3×5s（甲：按镜组取整；乙：定长 5s 等比缩放）
│  ├─ prompt_locator.py 191  SH402 / #13-#14 → [Shot N] 块 + 绝对行号区间
│  ├─ ticket.py         62   定点改动工单（纯文本，供外部 Agent 消费）
│  ├─ ingest.py        125   产物归档入库
│  ├─ reconcile.py     277   补收：ComfyUI 已跑完但本地未入库的产物（启动 + 周期）
│  ├─ ffmpeg_tools.py  136   探测 / 抽末帧 / 拼接 / ffprobe
│  ├─ db.py 136 / repo.py 432 / folder_pick.py 160
│  └─ routers/         ~2,000  workspace / media(+comfy-input) / cards / split / generate(+jobs)
│                              / locate / ffmpeg / workflows / assets / agent(501) / system
├─ web/                            React 19.2 + Vite 8 + TS 6 + Tailwind 4.3 + shadcn + TanStack Query 5 + zustand 5
│  └─ src/{App.tsx, components/, pages/, lib/{api,assetUpload,types,query,format}, store/useApp.ts}
│     components: FilmCell / StitchRail / Rail / Reviewer / TaskBar / RefStrip / AssetGroupCard
│                 / AssetUploadDialog / AssetDirSetter
│     pages: GachaBoard / SplitPanel / ShotLocator / AssetLibrary / WorkspaceGate
├─ desktop/                        Tauri 2.12 壳：frontendDist=../shell（仅 loading.html）→ navigate 到后端 URL
├─ packaging/build.py              五步流水线：前端 → ffmpeg → PyInstaller → resources → tauri build
├─ prototype/index.html            设计原型留档（同源同语言）
└─ SPEC.md                         施工依据
```

### 1.3 技术栈与关键约束

| 层  | 选型                                                                                     | 整合相关注意                                                     |           |                           |                        |
| -- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------- | --------- | ------------------------- | ---------------------- |
| 后端 | FastAPI ≥0.143 / httpx / pydantic v2 + settings / python-multipart / uvicorn[standard] | **仅 6 个直接依赖，全为纯 Python 或有 wheel**；「零重型依赖」是明确设计取向           |           |                           |                        |
| 存储 | `sqlite3` stdlib（无 ORM）+ 磁盘目录为真相源                                                      | 表：`cards / split_plans / jobs / assets`；无 CHECK 约束，加状态不需迁移 |           |                           |                        |
| 媒体 | ffmpeg / ffprobe 走 `asyncio.create_subprocess_exec`                                    | 不用 wrapper 库                                               |           |                           |                        |
| 前端 | React 19 + TS 6 + Vite 8 + Tailwind 4                                                  | ⚠️ `tsconfig.app.json` 的 `baseUrl` 已删（TS 6 废弃）             |           |                           |                        |
| 状态 | TanStack Query（服务端态）+ zustand（UI 态，仅内存）                                                | 刷新即丢，UI 偏好无持久化                                             |           |                           |                        |
| 路由 | 无 router，\`#/board                                                                     | #/split                                                    | #/locator | #/assets\` 与 zustand 双向同步 | 与 NBE 自写 hash 路由是同一套思路 |
| 打包 | Tauri 壳 spawn sidecar（PyInstaller 单 exe）+ Job Object 防孤儿                               | Python 固定 **3.12**（`backend/.python-version`），uv 管理        |           |                           |                        |

### 1.4 现有能力缺口（本次整合要打的靶子）

| #   | 缺口                                | 证据                                                             | NBE 是否已有对应件                                       |                          |
| --- | --------------------------------- | -------------------------------------------------------------- | ------------------------------------------------- | ------------------------ |
| G-1 | **输入侧没有入口**：结构化提示词 md 只能外部产出      | `splitter` 只从「md 已存在」开始；无新建/导入提示词的路由或页面                        | ⚠️ 部分（有文本采集，无结构化改编）                               |                          |
| G-2 | **无设置界面**：跑片参数只能手改 `config.json`  | 视图只有 board/split/locator/assets；\`GET                          | PUT /api/config\` 无消费者                            | ✅ 有（五组设置页 + 参数下发 + 越界钳制） |
| G-3 | **无统一导出通道**：拼接只落 `草稿/`；工单只走剪贴板    | `/api/ffmpeg/concat` 写死落盘；`/api/ticket` 返回文本                   | ✅ 有（`download/dir/file` 三态契约 + `save_export` IPC） |                          |
| G-4 | **首屏扫盘即等**：卡池 / 素材库每次都先扫盘再读库      | `GET /api/assets` 先扫盘入库再读库                                     | ✅ 有（stale-while-revalidate + 缓存徽标）                |                          |
| G-5 | **壳层靠 navigate 而不是前端自主托管**（白屏遗留项） | `frontendDist=../shell` 只有 loading.html，`win.navigate(后端 URL)` | ✅ 有（`frontendDist` = 真前端 + `get_backend_url` IPC） |                          |
| G-6 | **无文本卫生层**：外来文本（小说/字幕/剪贴板）直接进 md  | 无任何 HTML/实体/推广语清洗                                              | ✅ 有（`textclean.py` 158 行，零依赖纯函数）                  |                          |
| G-7 | **跑片只有单端点**                       | `config.comfyui.base_url` 单值                                   | ✅ 有（多源聚合 + 自动切换的骨架）                               |                          |
| G-8 | **健康状态只在启动页**                     | `/api/health` 只被 App 的 Splash 消费                               | ✅ 有（侧栏底部常驻连接状态）                                   |                          |

---

## 2. 被评估项目（NBE）功能盘点

### 2.1 定位

基于「开源阅读（Legado）书源 DSL」的规则执行引擎：消费社区维护的 `shareBookSource.json`（68 源 / 67 平台），统一采集番茄/七猫/起点/晋江等章节正文，落到阅读器与 EPUB/MD/TXT 导出。个人离线阅读 + 技术研究定位。

### 2.2 能力矩阵

| 里程碑    | 能力                                                                     | 技术含量             | 与 GB 的相关性           |
| ------ | ---------------------------------------------------------------------- | ---------------- | ------------------- |
| M1     | 选择器求值（CSS / XPath / JSONPath）+ 正则三段式 + URL 模板                          | 高（DSL 解释器）       | 无                   |
| M2     | QuickJS 沙箱 + `java.*` 桥（12 个工具函数）                                      | 高（且重）            | 低（与 I-1 冲突）         |
| M3     | `@put/@get` 规则变量 + 级联 `选择器@js:` + 裸键 JSONPath                          | 高                | 无                   |
| M4     | cookie 会话（宽松域匹配）/ `replaceFont` 字体解密 / `!n` 后缀 / charset / 三格式导出 / GUI | 高                | 导出通道有用，字体解密无        |
| M5     | 多源并行搜索 + 书名作者去重 + 目录/正文自动换源                                            | **中高（控制流模式最值钱）** | 中（可映射到多端点跑片）        |
| GUI v2 | 单页三视图 + hash 路由 + 侧栏（历史/最近/连接状态）+ 设置五组（localStorage 版本化）+ 双主题          | 中                | **高（模式）**           |
| GUI v3 | 详情页 / 按章节范围导出 / 正文净化 / 相邻预载 / 搜索结果 SWR 缓存                              | 中                | **高（模式 + 一个纯函数模块）** |

### 2.3 技术形态与规模

```
engine/  ≈2,400 行 / 14 模块
  evaluator 411 · pipeline 455 · js_sandbox 388 · aggregator 358 · export 236
  http_client 190 · textclean 158 · templates 126 · jsutil 119 · font_utils 102
  rule_vars 70 · models 56 · sources 32
app.py   418 行   /api/{sources,search,toc,content,search_all,toc_all,content_all,export}
frontend ≈2,300 行  React 18.3 + JSX + Vite 5.4 + Tailwind 4（无 TS、无 Query、无组件库）
  视图 Search(473) / Settings(455) / Detail(373) / Reader(226) + App(943) 编排
  state settings(188) / searchCache(177) / library(179) + lib storage(64)/api(158)/backend(28)
依赖   httpx lxml cssselect jsonpath-ng quickjs fonttools cryptography fastapi uvicorn pydantic
桌面   Tauri 2：frontendDist=../dist（真前端）+ resources=../dist-backend（后端 exe 独立目录）
       invoke get_backend_url / save_export；web 模式导出走 JSON+base64，desktop 模式直接写盘
```

> ⚠️ 已记录的关键教训（对 GB 有直接价值）：**后端产物绝不能输出到 `dist/` 下**——Tauri 会把 `frontendDist` 整个目录嵌进 exe，798 文件 / 100MB 被塞进去后，exe 从 8MB 涨到 49MB，启动要解压上百 MB 资源，表现为**黑屏**。GB 目前 `frontendDist=../shell`（3 个文件）没有这个问题，但一旦改成年「前端自主托管」，这条纪律必须写进 build 脚本。

### 2.4 同构点与差异点

| 维度    | 同构 | 说明                                                           |
| ----- | -- | ------------------------------------------------------------ |
| 形态    | ✅  | 都是 FastAPI + React 单页 + Tauri 桌面壳，同一台机器同一套打包哲学               |
| 路由    | ✅  | 都自写 hash 路由，不引 router 库                                      |
| 外链    | ✅  | 都主张零外链（GB 已本地化字体；NBE 是白屏对照样本）                                |
| 后端地址  | ❌  | GB 靠 navigate 到后端；NBE 靠前端自主 + IPC                            |
| 数据模型  | ❌  | GB 工作区 + sqlite 为真相源；NBE 无 DB、无工作区、localStorage 存偏好与历史       |
| 语言/代际 | ❌  | JSX/React18/Vite5 **vs** TS/React19/Vite8/TS6 → npm 包与组件不可共享 |
| 设计语言  | ❌  | 铜金 + 双主题 + 圆角 **vs** 零彩度金属灰 + 形状语言                           |
| 领域    | ❌  | 视频生成 vs 网文采集                                                 |

---

## 3. 可整合功能总表

> 分类：**I**=工程基建 · **C**=文本内容 · **O**=调度并发 · **P**=UI 模式 · **R**=明确不做  
> 「方式」：`移植`=代码可直接搬 · `改写`=逻辑搬、语言/样式重写 · `借鉴`=只取控制流或交互模型 · `新建`=借需求不借代码

| ID     | 功能                                | 来源                                                   | 目标落点                                  | 方式                         | 价值                                | 成本                             | 新依赖    | 优先级          |
| ------ | --------------------------------- | ---------------------------------------------------- | ------------------------------------- | -------------------------- | --------------------------------- | ------------------------------ | ------ | ------------ |
| **C1** | 文本卫生 `clean_text`                 | `engine/textclean.py`                                | `backend/textclean.py` + 导入接口         | **移植**                     | 高（补 G-6）                          | 极低（158 行纯函数）                   | 0      | **P0**       |
| **O2** | 首屏 SWR 缓存                         | `state/searchCache.jsx` + SearchView 徽标              | 卡池 / 素材库 query                        | 借鉴 + 新建 hook               | 高（补 G-4）                          | 低                              | 0      | **P0**       |
| **P2** | 设置视图（五组 + 实时预览 + 参数下发 + clamp）    | `SettingsView.jsx` + `state/settings.jsx` + `RANGES` | 新视图 `#/settings` + `PUT /api/config`  | 改写                         | **很高**（补 G-2，唯一能直接消除「手改 json」的整合） | 中（400–500 行新 UI + 校验层）         | 0      | **P1**       |
| **I2** | 导出通道三态契约                          | `_download_payload` + `save_export` IPC              | `/api/export` + 拼接/工单/清单              | 移植（契约）+ 改写（IPC）            | 高（补 G-3）                          | 中（涉及壳层 command）                | 0      | **P1**       |
| **I3** | UI 偏好持久化（版本化 key + 深合并 + clamp）   | `lib/storage.js` + `RANGES`                          | `web/src/lib/prefs.ts` + `gb.ui.v1`   | 移植                         | 中（刷新不丢视图/筛选/密度）                   | 低（≈80 行）                       | 0      | **P1**       |
| **P1** | 阅读面板（排版 CSS 变量 + ←→ 翻镜 + 目录抽屉）    | `ReaderView.jsx` + `TocDrawer`                       | ShotLocator 正文区                       | 改写（**必须保行号**）              | 中高（定位之后缺「读」）                      | 中高（要与高亮/行号共存）                  | 0      | **P2**       |
| **O1** | 候选源调度骨架（按源锁 / 超时放弃 / 候选列表 / 自动切换） | `engine/aggregator.py`                               | 多 ComfyUI 端点 / 多工作流回退                 | 借鉴（**控制流**，线程池改 asyncio）   | 中高（补 G-7，跑片韧性）                    | 高（要动 comfy.py + config + 前端状态） | 0      | **P2**       |
| **I4** | API 客户端错误分型 + 退避 + abort          | `lib/api.js`                                         | `lib/api.ts`                          | 改写                         | 中                                 | 低                              | 0      | **P2**       |
| **I5** | 侧栏底部常驻健康状态                        | `Sidebar.jsx`                                        | `Rail.tsx` 底部                         | 改写（形状+明度表达）                | 中（补 G-8）                          | 低                              | 0      | **P2**       |
| **C2** | 本地原著/剧本库（手动导入 → 章节 md → 索引）       | `textclean` + `safe_filename` + 章节切分思路               | `{工作区}/原著/` + sqlite `books/chapters` | 新建（借 `export.py` 的命名/切章规则） | **高（补 G-1 的一半）**                  | 中高                             | 0      | **P2**       |
| **C4** | 范围导出（第 x–y 章）+ 每章一文件打 zip         | `export_txt_dir` / `chapter_filename`                | 提示词 / 工单批量导出                          | 移植                         | 中                                 | 低                              | 0      | **P3**       |
| **P3** | 历史与最近（搜索历史 chips / 最近阅读）          | `state/library.jsx`                                  | 最近打开的段 / 卡                            | 借鉴                         | 低中                                | 低                              | 0      | **P3**       |
| **C3** | 联网书源采集（68 源）                      | `evaluator/pipeline/js_sandbox/aggregator`           | 独立可选模块                                | 移植整包                       | ⚠️ 中（但需先解题 5/6 的合规与改编缺口）          | **极高**                         | +6     | **P4（暂缓）**   |
| **I1** | 壳层「前端自主托管 + IPC 取地址」              | `src-tauri/src/lib.rs` + `lib/backend.js`            | `desktop/` + `lib/api.ts`             | 改写                         | 高（补 G-5，白屏遗留项）                    | **高**（14 文件 47 处 + 后端回传 URL）   | +1 前端包 | **P1（独立立项）** |
| **R1** | QuickJS 沙箱 + `java.*` 桥           | `js_sandbox.py` 388 行                                | —                                     | 不做                         | —                                 | —                              | —      | **不做**       |
| **R2** | 字体解密 `replaceFont`                | `font_utils.py`                                      | —                                     | 不做                         | —                                 | —                              | —      | **不做**       |
| **R3** | 书源 DSL 全量解释器                      | `evaluator/templates/pipeline/http_client` ≈1,300 行  | —                                     | 不做（C3 才需要）                 | —                                 | —                              | —      | **不做**       |
| **R4** | EPUB3 导出                          | `export.py`                                          | —                                     | 不做（GB 产物是 mp4）             | —                                 | —                              | —      | **不做**       |
| **R5** | 起第二个 sidecar 进程                   | `nbe-backend.exe` 模式                                 | —                                     | 不做                         | —                                 | —                              | —      | **不做**       |
| **R6** | 直接复用 NBE 的 JSX 组件                 | `frontend/src/**`                                    | —                                     | 不做                         | —                                 | —                              | —      | **不做**       |



---

## 4. 整合方案明细

### 4.1 分档策略（三层，可独立落地）

```
┌─ 第 0 层：壳与通道（不影响业务逻辑）──────────────────────────────┐
│  I1 前端自主托管 + IPC      I2 导出三态契约      I3 偏好持久化    │
└──────────────────────────────────────────────────────────────────┘
┌─ 第 1 层：文本入口（补齐上游，不碰既有不变式）────────────────────┐
│  C1 文本卫生   C2 本地书库（原著/）   C4 范围导出                 │
└──────────────────────────────────────────────────────────────────┘
┌─ 第 2 层：体验与控制流（要动既有页面）────────────────────────────┐
│  P2 设置视图   P1 阅读面板   O2 首屏缓存   O1 多端点调度   I4 I5  │
└──────────────────────────────────────────────────────────────────┘
┌─ 第 3 层：联网采集（可选插件，默认关闭）──────────────────────────┐
│  C3 书源引擎整包（+6 依赖 / 合规面 / 与 v1「不做 LLM」相邻）      │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 C1 · 文本卫生（P0，先做）

**做什么**：把 `clean_text()` 搬到 `backend/textclean.py`（保持纯函数、零依赖），但**限定使用范围**。

**关键边界（红线级）**：

- ✅ 允许：外来文本导入入口（粘贴/上传/摘录）、原著章节正文、任何「进入 GB 之前」的文本。
- 🔴 禁止：出现在 `提示词/*.md` 的读取链上。`splitter.py` / `prompt_locator.py` 解析的 md 是**唯一真相**，行号是工单正确性的基础，任何清洗都会移动行号。这条要写成代码注释 + SPEC 注记。

**取舍**：NBE 的广告词表（`_HARD_AD_RE` / `_SOFT_AD_WORDS`）是网文站特征，对剧本 md 有害无益。移植时建议**拆两层**：`strip_html / unescape / 零宽清理 / 空白折叠`（通用，无条件用）与 `strip_ads / strip_urls`（只在「网文采集」路径用）。这样第 1 层的能力可以在 `C3` 缺席时单独存在。

**验收**：给定一段含 `<div><br>`、`&nbsp;`、`www.x.com正文`、零宽字符的脏文本，输出为段落规整的纯文本，且「WWW 域名后面的中文」不被吞掉（NBE 已踩过这个坑，正则字符类里排除了 CJK）。

### 4.3 O2 · 首屏 SWR 缓存（P0）

**做什么**：卡池与素材库改为「先用上次结果渲染，后台扫盘完成后替换」。

**为什么是这里**：GB 的 `GET /api/assets` 是「先扫盘入库再读库」，卡池扫描同理——首屏体感完全取决于磁盘。NBE 的 `searchCache` 正是解决同类问题（搜索要等 68 个源）。

**怎么落（不改后端）**：

- 新增 `web/src/lib/swrCache.ts`：`read(key)` / `write(key, payload, {maxAge})`，键 = 工作区签名 + 视图 + 筛选条件。参考 NBE 的容量上限与过期策略（12 条查询 / 60 条结果 / 14 天）。
- TanStack Query 侧用 `placeholderData` 注入缓存，`isFetching && data` 时在标题旁挂「旧结果 · 更新中」徽标。
- 徽标样式用既有语言：**虚线框 = 未确认**（正好对齐「轮廓语言」），不引入彩色。

**冲突预案**：GB 的数据是磁盘真相，缓存**必须可被识别为旧**，且「扫描」按钮要能强制绕过缓存（`refetch({cancelRefetch:true})`）。绝不能出现「删了文件但列表还在」且无提示的情况。

### 4.4 P2 · 设置视图（P1，用户可感知度最高）

**为什么优先级高**：GB 现在跑片参数、工作流节点映射、poll 间隔、超时全靠手改 `config.json`。这是**真实的生产阻塞**，而 NBE 的设置页已经把这套「五组 + 实时预览 + 参数下发 + 后端钳制」跑通过。

**落点**：新视图 `#/settings`，`Rail` 增一项。

**结构（借 NBE 分组，换 GB 内容）**：

| 组 | GB 的实际内容 | 交互 |
| --- | --- | --- |
| 连接 | `comfyui.base_url` / `input_dir` / `comfy_output_dir` / ffmpeg 路径 | 探测按钮（复用 `/api/health`） |
| 跑片 | `poll_interval_sec` / `timeout_min` / 默认秒数 / megapixels / seed 策略 | 滑杆 + 越界钳制 |
| 工作流 | `workflow.json_path` / `nodes.*` 映射 / `load_image_ids` | 只读展示 + 从 workflows 路由选择 |
| 文件夹 | 本工作区的 5（+1）个目录名 | ⚠️ 必须区分「全局」与「本工作区」 |
| 界面 | 卡片密度 / 审片器分隔条高度 / 上次视图 | 写 localStorage（`I3`） |

**必须做对的一件事**：GB 的 config 是**两层**（全局 `config.json` + 每工作区 `WorkspaceEntry.folders` 覆盖）。设置页若不区分层级，会写出跨工作区污染（历史上踩过「污染用户环境」）。设计上：全局项改动 → `PUT /api/config`；工作区项改动 → `PATCH /api/workspaces/{name}/folders`，UI 上用**实线/虚线**区分「已确认（全局）」与「本工作区覆盖（未固化）」。

**钳制**：前端滑杆区间常量与后端 pydantic 校验共用一份取值（NBE 的 `RANGES` 模式），越界值后端回落默认而不是 500。

### 4.5 I2 · 导出通道三态契约（P1）

**现状缺口**：`/api/ffmpeg/concat` 把结果写死落到 `草稿/`；工单只在剪贴板。用户想「把这一段的三个块 + 清单导到 U 盘/交付目录」没有路径。

**契约（直接沿用 NBE 已验证的形状）**：

```jsonc
// web 模式：JSON + base64（浏览器对 zip/epub 这类响应体会接管为原生下载，fetch 只能拿到空 204）
{ "kind": "download", "filename": "...", "mime": "...", "size": 12345, "base64": "..." }
// desktop 模式：直接写盘
{ "kind": "file", "path": "D:/out/x.mp4", "filename": "x.mp4" }
{ "kind": "dir",  "path": "D:/out/第4段", "count": 3 }
```

**两侧落地**：

- 后端 `backend/export.py`（新建）：`payload_download()` / `write_to_dir()` 两个函数，路由只负责选形态。
- 壳层加 `save_export` command（NBE `main.rs` 已有实现可对照），桌面模式弹目录选择复用 GB 现有的 `folder_pick.py` 机制。
- 前端 `lib/download.ts`：统一解码 base64 → Blob → `<a download>`；桌面模式走 invoke。

**收益外溢**：工单批量导出、拆分清单导出、成片交付都能复用这一条通道，不用各写一套。

### 4.6 P1 · 阅读面板（P2）

**现状**：`ShotLocator` 是「输标签 → 高亮行号区间 → 写意见 → 出工单」。缺的是「连续读这一段正文」的能力，而读的时候正是发现问题的时机。

**借 NBE 的**：`fontSize / lineHeight / letterSpacing / paragraphGap / contentWidth` 五个 CSS 变量驱动排版；`←/→` 跳镜；右侧滑出「镜表目录」（等价于 NBE 的 TocDrawer，数据源换成 `§③bis` 镜表）。

**绝对不能丢的**：绝对行号的可见性。NBE 的阅读器是「好看优先」，GB 这里必须「行号优先」——工单正文的可信度建立在「第 134 行」这个数字上。方案：行号沟槽常驻（等宽 `IBM Plex Mono`），排版变量只作用于正文列宽与行距，**不改行高基准**，否则行号与视觉行的对应会漂。

**冲突**：GB 现在用 `<pre>` + 行号；改成富文本渲染会破坏「复制原文」的保真度。**建议保留 `<pre>`**，只叠加排版变量与目录抽屉，不引入 markdown 渲染。

### 4.7 O1 · 候选源调度骨架（P2）

**借什么**：不是借书源，是借 `Aggregator` 的**控制流**——

1. 候选列表（`candidates: [{source, book_url}]`）→ 映射成 `endpoints: [{name, base_url, workflow}]`
2. 每个候选带独立的锁 → 同一端点串行、不同端点并行
3. 到点未完成就放弃，**不影响整体返回**（`shutdown(wait=False)` 的等价物）
4. 首选失败 → 按序换下一个；正文阶段换源要「重抓目录 + 按标题匹配章节」→ 映射成「换端点后重提交同一 chunk + 沿用同 seed」

**必须改的一点**：NBE 用 `ThreadPoolExecutor` + 全局锁，因为 QuickJS Context 不能跨线程。GB 是 asyncio，**不要照搬线程池**——改成 `asyncio.gather` + `Semaphore` + `asyncio.wait(timeout=)`，否则会把 `comfy.py` 的异步链切断（在有 event loop 的线程里跑同步阻塞调用，会卡死轮询）。

**成本警告**：这是本次清单里唯一要动 `comfy.py`（783 行，最复杂模块）+ `generate.py`（840 行）的方案。建议**先只做「配置层多端点 + 手动切换」，不做自动 failover**，把自动切换留到有真实双机环境时再上。

### 4.8 C2 · 本地原著/剧本库（P2，补 G-1）

**做什么**：给 GB 一个「外来文本入库」的入口——不联网，手动导入 txt/md（或从剪贴板粘），按章节切分、净化、落盘、建索引。

**为什么要做**：这是 G-1 的一半，且**零新依赖**。真正的「改编成 §①–§⑥」仍靠外部 Agent，但至少「文本在工作区里、可检索、可引用」这件事能自己做。

**落盘（服从 I-2 工作区自包含）**：

```
{工作区}/原著/<书名>/0001 第一章 xxx.md     ← 与 NBE 的 chapter_filename 规则一致（补零 4 位）
{工作区}/.gachabench/cards.db
  + books(id, workspace, title, author, source, created_at)
  + chapters(id, book_id, idx, title, rel_path, word_count)
```

**为什么不做全局库**：全局库会让「目录拷走记录跟着走」失效。跨工作区复用改用「导入时选择：复制 / 引用」——默认复制。

**目录名要新增一项**：`folders.source = "原著"`。这会牵动 `workspaces.py` 的建档逻辑（现在固定建 5 个）与 `WorkspaceEntry.folders` 覆盖机制；已有工作区**不能强行重建**，要在下次扫描时按需 backfill（缺失则补建，已有则跳过）。

**接口草案（形状，非实现）**：

```
POST /api/library/import      multipart 或 {path} → {book_id, chapters:[…], skipped:[…]}
GET  /api/library/books       → 书目列表（含章节数、字数、最后导入时间）
GET  /api/library/books/{id}  → 章节列表
GET  /api/library/chapters/{id} → 净化后正文（textclean 第一层）
POST /api/library/chapters/{id}/excerpt → {text, words} 供 SplitPanel 引用为段落素材
```

**与 SplitPanel 的关系**：只做「引用片段」，**不做「生成提示词 md」**——那一步是 LLM 的活，属于 v1 之外。UI 上要把这个边界说清（例如摘录按钮旁注明「仅摘录，不改写」），否则用户会以为点了就能出 §④。

### 4.9 I1 · 壳层方案（P1，但建议单独立项）

**问题**：GB 现在 `frontendDist = ../shell`（只有 loading.html），启动后 `win.navigate("http://127.0.0.1:{port}/")`。这意味着**页面完全依赖后端**：后端慢/失败 → 白屏，连「正在启动」都看不到。

**NBE 的做法**：`frontendDist = ../dist`（真前端）+ 壳层 `manage(BackendPort)` + `#[command] get_backend_url`，前端 `invoke` 取地址。页面先起来，后端连不上时显示「重连」而不是白屏。

**成本（已量化）**：

- 前端 14 个文件、**47 处** `/api/...` 字面量（`grep -rn '/api/' web/src`）。
- 2 处**前端拼**的媒体 URL：`FilmCell.tsx:81`、`Reviewer.tsx:370` 的 `` `/api/media/${card.id}` ``。
- 若干**后端回传**的相对 URL：`assets.py` 的 `url` 字段、`generate.py` 的 `_assets_file_url` / `_comfy_input_url`。这些如果前端不做统一前缀，图片/音频预览会 404——这是最容易漏的一处。
- 加前端依赖 `@tauri-apps/api`。

**结论**：技术上完全可行，但它是**基建重构**而非「功能整合」，会横穿整个前端。建议：**只有当白屏问题仍然复现时才做**；如果当前 navigate 方案在目标机器上稳定，把它降为 P3，不要和功能整合混在一个迭代里。

---

## 5. 冲突点与红线

| # | 冲突 | 表现 | 对策 |
| --- | --- | --- | --- |
| **X-1** | **设计系统** | NBE 铜金强调色 + 双主题 + 圆角卡片；GB 零彩度 + 形状语言 | 只借模式不借样式。所有颜色走 `--gb-*`，状态用实线/虚线/斜纹表达。**一个 JSX 都不复制。** |
| **X-2** | **框架代际** | JSX / React 18 / Vite 5 / 无 TS / 无组件库 **vs** TS / React 19 / Vite 8 / shadcn / TanStack | 不共享 npm 包、不共享组件。NBE 的 `useState` 编排要改写成 Query + zustand 分工。 |
| **X-3** | **数据模型** | NBE 无工作区、无 DB、偏好与历史全在 localStorage | 书库必须进 `<工作区>/.gachabench/cards.db`（服从 I-2）；只有 UI 偏好进 localStorage。 |
| **X-4** | **v1 边界** | SPEC §1 明确「不做 LLM 自动改提示词」，`/api/agent/*` 返回 501 | 采集/导入只到「文本」。**不承诺「一键出剧本」**。若哪天要做改编，那是新立项，不是本次整合。 |
| **X-5** | **铁律 I-1 + 行号可信度** | 任何自动改写都会破坏 `<Subject N>` 引用与工单行号 | `textclean` **禁止**出现在 `提示词/*.md` 读取链（`splitter` / `prompt_locator`）。只做导入入口。 |
| **X-6** | **网络与合规** | 抓取第三方站点、cookie 对抗、`replaceFont` 字体解密（规避技术措施）、版权 | 默认**只做本地导入**；联网采集作为独立可选模块、默认关闭、带免责与来源标注。**字体解密不做**（R2）。 |
| **X-7** | **依赖与打包** | +6 个后端依赖（含 lxml / cryptography 两个大件）；PyInstaller 需补 hiddenimports；dist 体积上涨；**火绒 HIPS 对新 hash 的 exe 会重新拦截** | 见 §6。轻档方案（C1/C2/O2/UI）**零新增依赖**，这是首选路径。 |
| **X-8** | **并发模型** | NBE：ThreadPool + 全局串行锁（QuickJS 线程独占）；GB：asyncio | 调度器必须改 `asyncio.Semaphore`，**不要在线程池里调 `comfy.py`**。 |
| **X-9** | **进程模型** | NBE 是独立 sidecar exe | **同进程挂 router**（`routers/library.py`），不起第二个服务、不引入第二套端口与生命周期。 |
| **X-10** | **设置双层** | GB config 有全局层 + 每工作区层 | 设置页必须显式分层，否则跨工作区污染（历史教训）。 |

---

## 6. 依赖与打包影响

### 6.1 分档依赖账

| 档位 | 新增后端依赖 | 新增前端依赖 | dist 体积 | 打包风险 |
| --- | --- | --- | --- | --- |
| **轻档**（C1 C2 C4 O2 P1 P2 I2 I3 I4 I5） | **0** | **0** | ~0 | 无 |
| **中档**（+ I1 壳层） | 0 | `@tauri-apps/api`（~0.1MB） | ~0 | 前端产物位置纪律（X-7 黑屏坑） |
| **重档**（+ C3 联网采集） | lxml / cssselect / jsonpath-ng / quickjs / fonttools / cryptography | 0 | **+25~45MB** | hiddenimports、构建时间、**火绒重拦** |

### 6.2 重档的逐项依赖评估

| 包 | 类型 | 说明 |
| --- | --- | --- |
| `lxml` | C 扩展（有 wheel） | 最大的一个，约 11MB；PyInstaller 需要 collect 子模块 |
| `cssselect` | 纯 Python | 小 |
| `jsonpath-ng` (+`ply`) | 纯 Python | 小；ply 会生成表，冻结时要注意 `hiddenimports` |
| `quickjs` | C 扩展 | ⚠️ **仅 cp312 有 wheel**（NBE README 明确：cp313 无 wheel 且本机无编译器）。GB 后端已固定 **3.12**（`backend/.python-version`）→ **无版本冲突**，这是可以做的关键前提 |
| `fonttools` | 纯 Python（体积大） | 约 12MB；只为字体解密用 → 不做 R2 时**不需要** |
| `cryptography` | C+Rust 扩展 | 约 10MB；同样只为特定书源 → 按需 |

> **重要**：R2（字体解密）不做的话，`fonttools` + `cryptography` 这两个大件可以直接砍掉，重档体积从 +45MB 降到 **+15MB 左右**。建议如果真要做 C3，先只支持「不需要字体解密」的书源子集。

### 6.3 进程与端口

不起第二个 sidecar。`main.py` 的 `_include_routers()` 加一个 `library.router` 即可；SQLite 复用 `db.py` 的连接工厂（同一个 `cards.db`，新增表不需要迁移框架——GB 表上无 CHECK 约束，`db.py` 有轻量迁移）。

---

## 7. 优先级与里程碑

| 阶段 | 内容 | 验收标准 | 改动面 |
| --- | --- | --- | --- |
| **M1（P0）** | C1 文本卫生 + O2 首屏缓存 | ① 脏文本输入 → 规整纯文本，域名后中文不丢；② 二次进入卡池先出缓存、后台刷新、有「旧结果」徽标；③ `提示词/*.md` 读取链**零改动**（grep 证明） | 新 2 文件 + 2 处 query 接入 |
| **M2（P1）** | P2 设置视图 + I3 偏好持久化 | ① 跑片参数可在界面改并落 `config.json`；② 越界值被钳制不 500；③ 工作区级目录改动只影响本工作区；④ 刷新后视图/筛选不丢 | 新视图 + `PUT /api/config` 校验层 + prefs |
| **M3（P1）** | I2 导出通道 | web/desktop 双形态都能导出，`kind` 三态齐全；拼接连带导出不覆盖原产物 | 新 `export.py` + 壳层 command + `lib/download.ts` |

> **M3 已落地（2026-10-17）**，与方案有两处偏离，都是刻意往低风险方向偏：
>
> 1. **没加壳层 `save_export` command**。桌面形态改走「前端问目录 → 后端直接写盘」：
>    目录选择复用已有的 `POST /api/system/pick-folder`（pwsh `IFileOpenDialog`），
>    后端本来就跑在本机、和用户同一个桌面会话。这条路比「前端拿 base64 → invoke 壳层
>    command 写盘」更短，也不用让一个尚无法在本机编译验证的 Rust 文件进发布包。
>    `kind: "file"` 保留在契约里（单文件直写的载荷），当前路由只产出 `download` / `dir`。
> 2. **多条目 download 暂不支持**，要求选目录。`zip` 打包留给 M6，与方案一致。
>
> 另外补了一条方案里没写明的安全边界：`items[].rel_path` 来自 sqlite，解析后必须
> `relative_to(工作区根)`，否则拒绝 —— 否则一个构造出来的 `../../x` 就能把工作区外的
> 文件复制到用户选的地方。
| **M4（P2）** | P1 阅读面板 / C2 本地书库 / I5 健康灯 / I4 API 强化 | 阅读面板行号与 `<pre>` 保真；书库导入后随工作区拷走仍可用 | ShotLocator 改造 / 新 router + 2 表 / Rail 底部 |

> **M4 已落地（2026-10-18）**。四项全绿，验证方式：后端 121/121（新增 21 条）、
> 前端 CDP 端到端 I5 5/5 · I4 5/5 · P1 6/6 · C2 8/8、`web` 单测 12/12（新增 `npm test`）。
>
> - **I5 健康灯**（`lib/health.ts` + `Rail.tsx` + `App.tsx`）：`/api/health` 从此常驻轮询
>   （在线 20s / 掉线 5s / 聚焦即探），Rail 底部三行：后端（实心亮点＝通、虚线环＋暗芯＝断、
>   可点击重检）＋ ffmpeg ＋ ComfyUI，掉线时后两项压暗表示「旧数据」。
>   🔴 顺带改掉一个真问题：原先 `health.isError` 就整屏 Splash，导致「连上之后后端再掉线」
>   时健康灯永远没机会亮 —— 现在只有**从来没连上过**才占满屏，之后交给健康灯说话。
> - **I4 API 客户端**（`lib/api.ts` 重写）：错误分型 `network / timeout / http / server /
>   aborted / parse`，中文人话消息；超时按方法分档（GET 30s / 写 60s / 上传 5min，
>   `timeoutMs: 0` = 永不超时给「选文件夹」这类等用户操作的请求）；退避重试**只给 GET**
>   （写操作重复提交会出两份任务，一次都不重试）；`signal` 接进了 `useSwrQuery`，查询被
>   取消时在飞的请求真的被掐掉。⚠️ 保留了一条老契约：无 body 的写操作照旧发 `{}`，
>   免得哪个端点声明了必填 body 就一片 422。
> - **P1 阅读面板**（`ShotLocator.tsx`）：保留 `<pre>`（复制保真）＋ 行号沟槽常驻；五个排版
>   变量（字号 / 行距 / 字距 / 段距 / 列宽）落在 `gb.ui.v1` 的 `reader` 组里会记住；
>   ←/→ 跳镜（输入框与审片器开着时不抢方向键）；右侧滑出「镜表目录」抽屉（数据源是 §④
>   代码块里的 `[Shot N]`，不是独立的 §③bis 表——实际数据里没有那一节）。
>   **行号不漂的验收**：改完字号/行距后行数不变、每行等高（没有折行）。
> - **C2 本地原著库**（`library.py` + `routers/library.py` + `books/chapters` 两表 +
>   `pages/LibraryPage.tsx`）：`{工作区}/原著/<书名>/0001 第一章 xxx.md`；切章认
>   `第N章` / `Chapter N` / md 标题 / 无标题；GBK 自动识别；重复导入幂等且清掉旧章节文件
>   （只删 `NNNN *.md`，用户自己放的 md 不动）；删索引不动盘。
>   **`folders.source = "原著"`**：加进了 `FOLDER_KEYS`（新工作区自动建），已有工作区
>   **不重建骨架**，导入时按需补建 —— 对应 §9 待决策第 3 条的保守答案。
>
> 两处刻意偏离方案：① 镜表目录的数据源换成 §④ 的 `[Shot N]`（实测提示词 md 里没有独立的
> §③bis 镜表节）；② C2 导入格式**只做 txt/md**（§9 第 2 条按「零依赖」底线先这么定），
> epub 需要新依赖，等真有人拿 epub 来再说。
| **M5（P2，需双机）** | O1 多端点调度 | 手动切换端点跑片；自动 failover 单独评审 | `comfy.py` + `generate.py` + config |
| **M6（P3）** | C4 范围导出 / P3 历史 | 按范围导出 + 每章一文件打 zip | 复用 M3 通道 |

> **M6 已落地（2026-10-18）**。后端 151/151（新增 30 条）、前端 `npm test` 19/19、
> CDP 端到端 6/6。
>
> - **M6a zip 打包**（`export.py`）：补上 M3 留下的缺口 —— web 形态多条目以前直接
>   400，现在打成一个 zip（标准库 `zipfile`，**不加依赖**）。单条目仍直出原文件
>   （审片器「导出这一条」要的就是原 mp4，套一层 zip 是添乱）。
>   🔴 两条实测出来的坑：① zip 里**同名成员不报错、后写静默覆盖先写** —— 必须主动
>   改名成 `_02`，否则用户拿到手才发现少了一半；② `zipfile` 对非 ASCII 文件名会自动
>   打 UTF-8 flag（`flag_bits & 0x800`），中文名实测不乱码，不用手工设。
> - **M6b 范围导出**（`exportrange.py` + `POST /api/export/range`）：三类范围
>   —— `prompts`（段 x–y 的提示词 md）/ `chapters`（某本书第 x–y 章）/ `cards`
>   （按 status + seg 的卡）。展开放在**后端**（第 1–40 段让前端拼 40 个 `rel_path`
>   既笨又撞 `MAX_ITEMS`），展开完照样走 M3 那条链路，zip / 不覆盖 / 三态契约全复用。
>   条目一律带 `subdir`（`提示词/` · `{书名}/` · `第N段/`）：二十个 `0001 xxx.md`
>   平铺在一个目录里会互相撞名。
>   🔴 `subdir` 是安全面：它决定文件落在目标目录哪一层，带 `..` 就能逃逸 —— 遇到
>   `..` 直接报错，**不静默剥掉**（剥掉会改变调用方语义）。体积闸门按**打包前的原始
>   字节**判定（视频压不动，拿压缩后体积判会漏）。
> - **M6c 最近打开**（P3）：`prefs.recent.segs`，上限 8、去重、新的排最前，
>   定位工单页段选择器下方出 chips。🔴 **历史记录不受 `keepOnReload` 管** ——
>   那个开关说的是「刷新后要不要回到上次的位置」，跟「最近看过哪些段」是两件事。
>
> 一处刻意偏离：没做「按段范围批量导出工单 txt」。工单是**定点改动**工具（针对具体
> 镜号），批量导出没有使用场景，做出来只是占地方 —— 范围导出换成了 `cards`（按段
> 打包正片），这是真实痛点。
| **M7（P4，独立评审）** | C3 联网采集 | 默认关闭；免责声明；来源标注；只支持无字体解密源 | 整包移植 + 6 依赖 |

> **M7 已落地（2026-10-18），且刻意没走「整包移植 + 6 依赖」这条路。**
>
> 方案里 C3 的成本栏写的是「+6 依赖 / dist +15~45MB / 火绒重拦」，那是按
> 「把 NBE 的 `evaluator/templates/pipeline/http_client` 整包搬过来」估算的。
> 实际动手前重新读了一遍 Legado 的规则语法，发现一个关键事实：
> **Legado 的默认规则语言不是 XPath，而是 `class.x` / `id.x` / `tag.x` 的自有语法**
> （XPath 与 JS 都只是它的可选前缀 `@XPath:` / `@js:`）。于是改成
> `html.parser` 自建小 DOM + 一个几百行的选择器，**零新增依赖**。
>
> - 新增 `backend/booksource.py`（引擎）+ `backend/routers/search.py`（6 个端点）；
>   书源随仓库带一份 `backend/data/shareBookSource.json`（1.2MB）。
> - 实测 68 个源里 **27 个**搜索 / 目录 / 正文三段全通；其余被**明确标注原因**
>   （需要 JS 引擎 / 字体解密），不假装能用。
> - 三条前置全部实现：`search.enabled` + `search.disclaimer_acked` 双开关默认关闭、
>   来源写进 `books.source`、导入只到文本。
>
> **代价（如实记）**：需要 JS 引擎的源用不了。要补齐就真得引 `quickjs`，
> 那时再按原方案做完整的合规与体积评审。
| **单独立项** | I1 壳层前端自主托管 | 后端挂掉时页面仍能显示「重连」而非白屏 | 14 文件 47 处 + 后端 URL |

---

## 8. 明确不做（及理由）

| 不做 | 理由 |
| --- | --- |
| **R1 QuickJS 沙箱 + `java.*` 桥** | 唯一能想到的用途是「提示词变换脚本」，而 GB 的铁律是「正文字词一字不改」（I-1）。引入脚本执行能力 = 给铁律开一个后门，且需要 cp312 的 C 扩展与跨线程安全设计。**能力与约束方向相反。** |
| **R2 字体解密** | 纯反爬对抗技术，GB 无此场景；且属「规避技术措施」，合规上最不该带进来的一项。 |
| **R3 书源 DSL 全量解释器** | 1,300 行 + 6 依赖，只有在做 C3 时才需要。轻档方案里它是纯负担。 |
| **R4 EPUB3 导出** | GB 产物是 mp4 / txt 工单，EPUB 无消费者。 |
| **R5 第二个 sidecar 进程** | 双进程意味着双端口、双日志、双生命周期、双份杀软误报面。同进程挂 router 完全够。 |
| **R6 直接复用 JSX 组件** | 代际 + 语言 + 设计系统三重不兼容（X-1/X-2），复制过来等于重写两遍。 |
| **全局书库（跨工作区共享数据库）** | 破坏 I-2「工作区自包含」。要复用就在导入时选择复制。 |

---

## 9. 待决策（需要拍板才能进实现）

1. ~~**C3 联网采集到底做不做？**~~
   → **已定（M7，2026-10-18）**：做，但**只做零依赖子集**。原文「不建议做」的前提是
   「整包移植 + 6 依赖」那个成本；实际改用 stdlib 迷你引擎后成本降到近乎为零，
   收益（省掉手动找书 + 复制粘贴 + 切章）重新变得划算。
   仍未解决的是「改编那一步谁来做」——本模块**只到文本**，§①–§⑥ 提示词生成依然在外面。
2. ~~**C2 的导入格式**：只支持 `txt/md`（成本最低）还是要 `epub`（需要新依赖，会破坏轻档「零依赖」）？~~
   → **已定（M4）**：只做 `txt/md`。epub 要新依赖，会破坏「零依赖」底线；等真有人拿 epub
   来再单独立项。非 txt/md 上传返回 415，接口层已经挡住。
3. ~~**`folders.source` 的默认名**：`原著` / `剧本` / `小说`？以及是否要让已有工作区自动 backfill（会动到 `workspaces.py` 与用户目录）。~~
   → **已定（M4）**：`原著`。已加进 `FOLDER_KEYS`（新工作区建档时自动建）；**已有工作区不
   backfill 骨架**，只在导入时按需补建这一个目录，不动 `build_workspace_dirs` 的既有行为。
4. **I1 是否现在就做**：当前 navigate 方案在目标机器上是否还会白屏？如果稳定，建议整体推后，别和功能整合混在一个迭代。
5. **设置视图的权限边界**：`workflow.nodes` 这种「改错就出废片」的映射，界面上是**只读展示**还是可编辑？（建议只读 + 从 workflows 路由切换，避免手抖改坏节点号）

---

## 10. 一页纸总结

- **能整合的是「能力」，不是「业务」**：5 项基建/模式（文本卫生、首屏缓存、设置模型、导出契约、偏好持久化）零新依赖、风险可控。
- **业务链路上 NBE 只能补一半**：它给文本，GB 缺的是「文本 → 结构化提示词」的改编，而那条线在 v1 边界之外（`/api/agent/*` 501）。
- **联网采集是性价比最低的一项**：+6 依赖 / +体积 / 合规面 / 与铁律相邻，建议做成默认关闭的独立可选模块，且砍掉字体解密。
- **UI 一个组件都别搬**：零彩度设计系统 + React 19/TS 6 与 NBE 的 React 18/JSX 不兼容，只借交互模式。
- **先做 C1 + O2 + P2**：三件事都在 GB 自己的痛点上（无文本卫生、首屏扫盘即等、无设置界面），且不碰任何既有不变式。
