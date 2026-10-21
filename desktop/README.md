# GachaBench 桌面壳（Tauri v2）

这个壳只干一件事：**把 Python 后端拉起来，等它 ready，然后把窗口导航过去**。
界面本身完全由后端提供（FastAPI + StaticFiles 挂 web/dist），所以前端一行都不用改 ——
`/api/*`、媒体 URL、SPA 全是同源的相对路径。

## 为什么不让 Tauri 直接托管 web/dist

前端 `src/lib/api.ts` 用的是相对路径 `fetch("/api/…")`，而且大量 `<img>/<video>`
的 src 也是后端返回的相对 URL。如果 SPA 由 `tauri://localhost` 提供，这些 URL 会全部
落到 Tauri 自己的 origin 上，要么全改要么加一层 URL 重写。
让后端在单端口上同时供 API + SPA + 媒体，是唯一「前端零改动」的架构。

## 就绪判据 = TCP 能连上

uvicorn 的顺序是 `lifespan.startup()` → `create_server()`，也就是说
**端口能连上时，lifespan（扫尾僵尸任务 + 补收）已经跑完了**。
所以一个 TCP connect 就是精确的 ready 信号，不需要 HTTP 探活（也就没有跨域问题）。
