# GachaBench 打包方案

把「FastAPI 后端 + React 前端」打成一个**双击即用的 Windows 桌面应用**。

## 架构：Tauri 壳 + Python 后端 sidecar

```
双击 GachaBench.exe
   │
   ├─ Tauri 壳（Rust）
   │    ├─ 找个空闲端口
   │    ├─ spawn resources/backend/GachaBench-backend.exe  （GACHABENCH_PORT=<port>）
   │    ├─ 轮询 TCP 连得上 → lifespan 已跑完 → 窗口导航过去
   │    └─ 窗口关闭 → taskkill /T 整棵树端掉
   │
   └─ GachaBench-backend.exe（PyInstaller onedir）
        ├─ FastAPI：/api/* + StaticFiles 挂 web/dist（SPA + 媒体，同一端口同源）
        ├─ _internal/ffmpeg/  ← ffmpeg + ffprobe + 7 个 DLL（226 MB，运行机无需自备）
        └─ _internal/web/dist、tools/pick_folder.ps1、seed/config.json
```

### 为什么不让 Tauri 直接托管 web/dist

前端 `src/lib/api.ts` 全部用相对路径 `fetch("/api/…")`，而且大量 `<img>/<video>`
的 src 也是后端返回的相对 URL（`/api/media/…`、`/api/assets/file/…`）。
如果 SPA 由 `tauri://localhost` 提供，这些 URL 会全部落到 Tauri 自己的 origin 上——
要么改几十处调用点，要么加一层 URL 重写/service worker。

让后端在单端口上同时供 API + SPA + 媒体，是唯一**前端零改动**的架构。
代价是窗口有 1~2 秒的启动页（壳自己画的，不是白屏）。

### 就绪判据为什么是 TCP 而不是 HTTP

uvicorn 的启动顺序是 `lifespan.startup()` → `create_server()`。
也就是说**端口能连上时，lifespan（扫尾僵尸任务 + 一次补收）已经跑完了**，
一个 `TcpStream::connect_timeout` 就是精确的 ready 信号。
顺带免掉了跨域——探活在 Rust 侧做，不经过 webview。

## 一键构建

```bash
uv run --project backend --with pyinstaller python packaging/build.py
```

| 参数 | 作用 |
|---|---|
| `--frontend` | 强制重建 `web/dist`（默认已存在就复用） |
| `--skip-frontend` | 完全不碰前端 |
| `--skip-pyinstaller` | 只重跑 Tauri（复用 `build/GachaBench-backend/`），调 Rust 时省一分钟 |
| `--target-dir PATH` | Rust target 目录，默认 `desktop/src-tauri/target` |
| `--skip-tauri` | 只打后端 sidecar |

产物：

```
desktop/src-tauri/target/release/
├── GachaBench.exe              ← 双击这个
├── resources/
│   ├── app/                    ← 壳自己（loading.html）
│   └── backend/                ← PyInstaller onedir（含 _internal/ffmpeg）
└── bundle/nsis/GachaBench_0.1.0_x64-setup.exe   ← 安装包
```

## 环境要求（构建机）

| 依赖 | 说明 |
|---|---|
| Node ≥ 20 | 构建前端 + Tauri CLI |
| Rust | `winget install Rustlang.Rustup`，装完 `cargo` 在 `~/.cargo/bin` |
| MSVC C++ 工具链 | VS2022/2026 的「使用 C++ 的桌面开发」工作负载 |
| ffmpeg | 构建时从 PATH 自动找并内置；也可用 `GACHABENCH_FFMPEG_SRC` 指定目录 |

> **运行机不需要上面任何一样**——exe 里全带了（ffmpeg 也在内）。

## 后端为打包做的改造

新增 `backend/runtime.py`，把「源码运行」和「冻结运行」两套路径布局收拢到一处：

| | 源码运行 | 冻结运行 |
|---|---|---|
| 只读资源 | `backend/tools/…` | `sys._MEIPASS/tools/…` |
| `web/dist` | `<项目根>/web/dist` | `_MEIPASS/web/dist` |
| `config.json` | `backend/config.json` | **exe 同目录**（不可写则 `%LOCALAPPDATA%/GachaBench`） |
| ffmpeg | PATH / config 里配的 | `_MEIPASS/ffmpeg/` 优先 |

改动的文件只有四处，且**源码运行行为与改造前完全一致**：

- `backend/runtime.py`（新增）路径仲裁 + 端口探测 + 日志兜底
- `backend/config.py` `CONFIG_PATH` 改从 `runtime` 取
- `backend/folder_pick.py` ps1 路径改 `runtime.resource()`
- `backend/ffmpeg_tools.py` `find_binary()` 优先用随包的那份
- `backend/main.py` `DIST_DIR` 走 `runtime`；冻结时 `uvicorn.run(app)` 不用 `reload`

## 图标

`tools/make_icon.py` 用纯标准库生成（zlib 压 PNG + ICO 容器，不引 PIL）：

```bash
python tools/make_icon.py desktop/icons
```

图形语义跟产品设计语言对齐——无彩度、只靠形状和明度：
圆角方底 `#1E1E1E`，中间亮条 `#CFCFD4` 就是「接续轨」，上下两条暗格是废稿，
左右两枚片孔。缩到 16px 时「暗-亮-暗」的三段式依然读得出来。

## 已知取舍

- **sidecar 不弹控制台**：stdout/stderr 由壳管道接走，落到 `%TEMP%/gachabench-backend.log`。
  桌面应用不该有黑窗口；要排查问题看那个日志。
- **tkinter 被排除**：`folder_pick` 的回退实现，Windows 必有 `powershell.exe`，
  排掉省约 10 MB。真到了没有 PowerShell 的机器上，文件夹选择器会显示不可用（不会崩）。
- **UPX 关闭**：压缩率有限，被国产杀软误报的概率很高，不值。
- **`--target-dir` 这个参数不是摆设**：某些沙箱不允许执行工作区内新生成的 exe，
  Rust 的 build script 探针（`proc-macro2` / `serde_core`）会因此以
  `OS Error 5 (PermissionDenied)` 失败。把 target 目录指到工作区外即可。
