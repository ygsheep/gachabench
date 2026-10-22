# P1 资产索引 — 任务清单

前置：依赖已写入 `backend/pyproject.toml`（sqlalchemy、alembic、watchfiles、pillow、pytest）。

## T1 平台数据层

- [x] 建 `backend/modules/__init__.py`、`modules/platform/__init__.py`、`modules/platform/ids.py`（ULID）。
- [x] 建 `modules/platform/db.py`：engine 缓存、WAL/外键/busy_timeout、`session_scope`、`ensure_schema`、`reset_engines`。
- [x] 建 `backend/alembic.ini`、`migrations/env.py`、`script.py.mako`、`versions/0001_baseline.py`、`versions/0002_p1_index.py`。
- 退出条件：空临时工作区可 `ensure_schema` 成功；已有旧库升级后旧表数据不变。

## T2 模型与映射

- [x] 建 `modules/assets/models.py`（index_assets、asset_meta、asset_ref、collections、collection_items、scan_runs、index_dirs、op_log）。
- [x] 建 `modules/assets/roles.py`（角色、资产别名、类别、主体、扩展名）。
- 退出条件：模型建表与迁移结果一致；角色/类别纯函数单测通过。

## T3 索引器

- [x] 建 `modules/assets/indexer.py`：递归扫描、跳过目录、mtime 增量、sha256、upsert、missing、统计。
- [x] 建 `modules/assets/search.py`：trigram 探测、FTS 同步、LIKE 回退。
- 退出条件：500 文件用例与中文子串用例通过。

## T4 缩略图与事件

- [x] 建 `modules/assets/thumbs.py`：320px WebP 按需生成与缓存。
- [x] 建 `modules/system/events.py`：事件总线 + `GET /api/events` SSE。
- 退出条件：PNG 返回 WebP，txt 返回 415；SSE 能收到 `index.progress`。

## T5 扫描服务与监听

- [x] 建 `modules/assets/service.py`：单飞扫描、scan_runs、200 文件进度、`index_one`。
- [x] 建 `modules/assets/watcher.py`：watchfiles 300ms 去抖、忽略规则、pending 合并。
- 退出条件：扫描中再次触发只置 pending；文件变化后自动增量扫描。

## T6 新 API

- [x] 建 `modules/assets/api.py`：列表/详情/PATCH/collections/scan/状态/media/thumb，Pydantic 模型。
- [x] 路径校验：媒体越界 400、非文件 404；缩略图仅图片。
- 退出条件：接口用例全过。

## T7 旧功能兼容

- [x] `routers/assets.py`：`GET ""`→`GET /groups`，`POST /scan`→`POST /groups/scan`。
- [x] 上传后调用 `index_one` 同步新索引。
- [x] `main.py`：挂新路由、lifespan attach loop + ensure_started + watcher。
- [x] 更新 `tests/test_assets.py` 两处旧路径。
- 退出条件：旧 229 用例全过，`/groups` 与上传行为不变。

## T8 前端

- [x] `routes/assets.tsx` 扩充搜索参数并渲染 `AssetIndex`。
- [x] `lib/types.ts` 追加 P1 类型；`lib/assetsApi.ts` 封装。
- [x] `pages/AssetIndex.tsx` 三栏、虚拟网格、懒加载缩略图、多选、详情、URL 防抖搜索、进度条。
- [x] `lib/useAssetEvents.ts` SSE；`RefStrip.tsx`、`Reviewer.tsx` 改 `/groups`。
- 退出条件：`tsc -b`、`test:ui`、`test`、`build` 全过。

## T9 验收

- [x] `npm run gen:api` 重新生成类型。
- [x] 跑后端全部单测，确认 500/1000 文件、二次扫描、路径穿越、哈希一致。
- [x] 浏览器验收 `/assets`：三栏、缩略图、详情、进度条、URL 刷新保留参数、旧审片素材选择可用。
- [x] 汇总结果与回退方式。
