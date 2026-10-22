# P1 资产索引 — 实现规格

来源：`plan/P1-资产索引.md`。本文件把计划落成可执行的工程决策，不改变计划范围。

## 1. 现状与冲突结论

- 旧 `db.py` 已在 `<工作区>/.gachabench/cards.db` 建了 legacy `assets` 表（INTEGER id、workspace + rel_path），旧 `routers/assets.py` 占用 `GET /api/assets`、`POST /api/assets/scan`、`GET /api/assets/target`、`POST /api/assets/upload`、`GET /api/assets/file/{rel}`。
- P1 需要稳定字符串 ID、跨角色索引、元信息与集合。因此**新主表命名为 `index_assets`**，其余沿用计划名：`asset_meta`、`asset_ref`、`collections`、`collection_items`、`scan_runs`；另加 `index_dirs` 存目录 mtime。
- 旧功能继续读写旧 `assets` 表，新索引独立写 `index_assets`。两者不互相同步历史数据，只在新上传时同步 upsert 一条新索引。
- 旧 `op_log` 在当前代码库中不存在。P1 新建 `op_log`，直接包含 P1 所需四列（`seq`、`prev_hash`、`op_hash`、`actor`），`actor` 写 `local`，其余留空。

## 2. HTTP 路由裁决

| 路径 | 归宿 |
| --- | --- |
| `GET /api/assets` | 新索引分页列表 |
| `GET /api/assets/groups` | 旧分组列表（原 `GET /api/assets` 逻辑） |
| `POST /api/assets/scan` | 新索引异步扫描，body `{force: bool}`，返回 task id |
| `POST /api/assets/groups/scan` | 旧同步重扫（原 `POST /api/assets/scan` 逻辑） |
| `POST /api/assets/upload` | 保留旧逻辑；写盘后同步 upsert 新索引 |
| `GET /api/assets/target` | 保留旧逻辑 |
| `GET /api/assets/file/{rel}` | 保留旧逻辑 |
| `GET /api/assets/{asset_id}` | 新索引详情 |
| `PATCH /api/assets/{asset_id}` | 只改 `asset_meta` |
| `GET /api/assets/collections` | 新索引角色树 + 内置集合 |
| `GET /api/assets/scan/{task_id}` | 扫描任务状态 |
| `GET /api/media?path=` | 新索引媒体流（Range + 工作区越界校验） |
| `GET /api/thumb?path=` | 新索引缩略图 |
| `GET /api/events` | SSE |

注册顺序：旧 `routers.assets` 先挂，新 `modules.assets.api` 后挂，保证 `upload/target/file/groups` 不被 `{asset_id}` 吞掉。

旧前端消费者 `RefStrip.tsx`、`Reviewer.tsx` 改读 `/api/assets/groups`；旧 `AssetLibrary.tsx` 从 `/assets` 路由撤下但文件保留，便于回退。

## 3. 后端文件与职责

```
backend/modules/__init__.py
backend/modules/platform/__init__.py
backend/modules/platform/db.py        # engine 缓存、WAL/外键/busy_timeout、session_scope、schema 升级
backend/modules/platform/ids.py       # ULID（无第三方依赖）
backend/modules/assets/__init__.py
backend/modules/assets/models.py      # SQLAlchemy 声明式模型
backend/modules/assets/roles.py       # 角色/类别/主体/扩展名映射
backend/modules/assets/indexer.py     # 全量+增量扫描、哈希、missing
backend/modules/assets/search.py      # FTS5 trigram 探测、同步、检索、LIKE 回退
backend/modules/assets/thumbs.py      # 320px WebP 缩略图
backend/modules/assets/service.py     # 单飞扫描任务、scan_runs 状态
backend/modules/assets/watcher.py     # watchfiles 300ms 去抖
backend/modules/assets/api.py         # 新路由与 Pydantic 响应模型
backend/modules/system/__init__.py
backend/modules/system/events.py      # SSE 事件总线与 /api/events
backend/migrations/env.py
backend/migrations/script.py.mako
backend/migrations/versions/0001_baseline.py
backend/migrations/versions/0002_p1_index.py
backend/alembic.ini
```

### 3.1 `platform/db.py`

- `get_engine(root)`：按 `str(root.resolve())` 缓存；`create_engine("sqlite:///<root>/.gachabench/cards.db")`。
- 连接事件：`journal_mode=WAL`、`foreign_keys=ON`、`busy_timeout=5000`。
- `session_scope(root)`：`sessionmaker(bind=engine, expire_on_commit=False)` 上下文管理器，异常回滚。
- `ensure_schema(root) -> bool`：优先 Alembic `upgrade head`；`migrations/` 不存在（冻结态）时退回 `Base.metadata.create_all` + 原生 FTS/op_log DDL。返回 `True` 表示本次新建或升级过 schema。
- `reset_engines()`：测试用，释放并清空缓存。
- Alembic 调用使用 `alembic.config.Config` + `command.upgrade`，`sqlalchemy.url` 指向当前工作区库；0001 与 0002 全部使用 `IF NOT EXISTS`/PRAGMA 判存在的幂等 DDL，已有库无需手工 stamp。

### 3.2 `assets/models.py`

| 表 | 关键字段 |
| --- | --- |
| `index_assets` | `id TEXT PK`、`path TEXT UNIQUE`、`name`、`dir_path`、`role`、`subject`、`category`、`ext`、`size`、`mtime`、`sha256`、`hash_status`、`media_kind`、`width`、`height`、`duration`、`fps`、`status`、`missing_at`、`created_at`、`updated_at` |
| `asset_meta` | `asset_id PK`、`tags_json`、`aliases_json`、`note`、`subject_override`、`updated_at` |
| `asset_ref` | `id`、`asset_id`、`file_path`、`line`、`col`、`kind`、`segment`、`created_at` |
| `collections` | `id`、`name`、`kind`、`filter_json`、`builtin`、`created_at` |
| `collection_items` | `collection_id`、`asset_id` 复合主键 |
| `scan_runs` | `id`、`workspace`、`started_at`、`finished_at`、`status`、`stats_json`、`force` |
| `index_dirs` | `path PK`（相对 POSIX，根为 `""`）、`mtime_ns`、`scanned_at` |
| `op_log` | `id`、`kind`、`plan_id`、`payload_json`、`result`、`created_at`、`seq`、`prev_hash`、`op_hash`、`actor` |

索引：`path`、`role`、`category`、`subject`、`status`、`dir_path`、`sha256`。

### 3.3 `assets/roles.py`

- 角色：`draft/final/prompt/storyboard/asset/source/other`。
- 一级目录名与 `config.folder_names_for(ws)` 匹配（大小写不敏感）取该角色。
- 资产角色额外接受 `DEFAULT_ASSET_DIR_CANDIDATES`，保证 `素材`、`资产`、`assets` 都映射 `asset`。
- 类别按扩展名：`image/video/audio/text/archive/other`。
- `subject`：文件名去扩展名后按 `[_\s]+` 切分取第一段；空串保留空。
- 跳过目录：`.gachabench`、`.git`、`node_modules`、`__pycache__`、`.tmp-*`，不跟随符号链接。

### 3.4 `assets/indexer.py`

- `scan_workspace(root, force, progress_cb, changed_cb) -> stats`。
- 递归 `os.scandir`；目录 mtime_ns 未变且非 `force` 且有 `index_dirs` 记录时跳过该目录（其下记录视为仍有效）。
- 文件按 `size + mtime` 判断是否变化；未变化且已有 sha256 时跳过重算。
- sha256：1 MiB 分块；`size > 2GB` 时只记 `size + mtime`，`hash_status="quick"`。
- upsert 以 `path` 为键，保留已有 `id` 与 `asset_meta`。
- 已消失文件标 `status="missing"`、写 `missing_at`，不删除。
- 写入每 500 条提交一次；单次扫描并发上限 2（线程池读取哈希）。
- 统计：`total/added/updated/missing/skipped/elapsed_ms`。
- 结束时重建 FTS 并回传本次变化路径。

### 3.5 `assets/search.py`

- 启动时探测 `CREATE VIRTUAL TABLE ... fts5(tokenize='trigram')`，成功才建 `asset_fts(asset_id UNINDEXED, name, subject, tags, aliases)`。
- upsert 时同步：先按 `asset_id` 删除再插入。
- 检索：`q` 长度 >= 3 且 FTS 可用时 `MATCH`；否则 `LIKE '%q%'` 覆盖 `path/name/subject/tags/aliases`。中文 2 字查询走 LIKE。
- 重扫后整表重建。

### 3.6 `assets/service.py` 与 `watcher.py`

- `ScanManager` 按工作区单飞；运行中再次请求只置 `pending`。
- `start(root, force=True)` 生成 ULID task id，插 `scan_runs(status="running")`，后台线程执行。
- 每 200 文件发 `index.progress`；完成发 `file.changed`，更新 `scan_runs`。
- `ensure_started(root)`：schema 新建/升级或 `index_assets` 为空时触发一次全量扫描。
- `watcher.py`：`watchfiles.awatch(root, debounce=300, stop_event=...)`；忽略 `.gachabenb/.git/node_modules/__pycache__/.tmp-*` 与临时文件；变化时 `manager.start(force=False)` 或置 pending。

### 3.7 API 响应模型

- `AssetItem`：`id/path/name/dir_path/role/subject/category/ext/size/mtime/sha256/hash_status/media_kind/width/height/status/tags/aliases/thumb_url/media_url`。
- `GET /api/assets` → `{items, next_cursor, total, root, workspace}`；`cursor` 为 `updated_at|id` 的 base64；`limit` 默认 80、上限 500。
- 详情额外返回 `note`、`subject_override`、`refs`。
- `PATCH` 请求 `{tags?, aliases?, note?, subject_override?}`，只写 `asset_meta`。
- `GET /api/assets/collections` → `{roles:[{key,label,count}], collections:[{id,name,kind,builtin,count}]}`。
- 内置集合：`recent`（7 天内修改）、`missing`、`unreferenced`、`large`（>50MB）。
- `POST /api/assets/scan` → `{task_id, status}`；`GET /api/assets/scan/{id}` → `{id,status,started_at,finished_at,stats}`。
- `GET /api/media?path=`：拒绝绝对路径、盘符、含 `..` 的路径、解析后越出工作区 → 400；非文件 → 404；`FileResponse` 自带 Range。
- `GET /api/thumb?path=`：仅图片；缓存 `.gachabench/thumbs/<sha 前两位>/<sha>.webp`；按需生成，宽 320，WebP quality 82；失败 415。

### 3.8 旧路由改造

- `routers/assets.py`：`GET ""` → `GET "/groups"`；`POST "/scan"` → `POST "/groups/scan"`。
- 上传成功后对每个落盘文件调用 `modules.assets.service.index_one(...)`，新索引立即可见。
- `RefStrip.tsx`、`Reviewer.tsx` 的 `/api/assets` 改 `/api/assets/groups`。

### 3.9 `main.py`

- lifespan 中：`event_bus.attach_loop(asyncio.get_running_loop())`；为 active 工作区 `ensure_schema` + `ensure_started`；启动 watcher 任务。
- 关闭时取消 watcher、`ScanManager.shutdown()`。
- `_include_routers()` 在旧 `assets.router` 之后追加 `modules.assets.api.router`、`modules.system.events.router`。

## 4. 前端文件

```
web/src/routes/assets.tsx             # search 参数：role/category/collection/q/asset（保留 panel）
web/src/pages/AssetIndex.tsx          # 新三栏页
web/src/lib/types.ts                  # 追加 P1 类型，保留旧类型
web/src/lib/assetsApi.ts              # 新索引 API 封装 + URL 拼装
web/src/lib/useAssetEvents.ts         # SSE 订阅
```

- 左栏：角色树（资产、分镜、提示词、成片、草稿、原著、其他，按后端返回）+ 内置集合与数量。
- 中栏：虚拟网格（按行窗口化，无新依赖），缩略图 `loading="lazy"`，多选仅选中态；点击单项写 `asset` 并加载详情。
- 右栏：路径、大小、哈希、主体、类别、角色、标签、别名、备注、引用列表。
- 顶部搜索：本地输入 250ms 防抖后写 URL；`role/category/collection` 直接写 URL。
- 底部：扫描进度条（SSE `index.progress`），完成后隐藏；`file.changed` 只失效当前资产查询。
- 素材/资产/提示词等非图片显示类型占位图；不做上传、拖放、改名、删除。

## 5. 测试与验收

新增 `backend/tests/test_asset_index.py`（unittest + TestClient + 临时工作区），并在测试 setUp/tearDown 调 `reset_engines()`。

| 用例 | 判定 |
| --- | --- |
| 500 文件 | 记录数 = 文件数；二次扫描不新增 |
| 中文中间子串 | `白陌_定妆_参考.png` 搜 `定妆` 命中 |
| 目录角色 | `素材`、`资产`、`assets` 均 `role=asset` |
| 跳过目录 | `.gachabench`、`.git` 内文件不入库 |
| 删除文件 | 重扫后 `status=missing` 且记录仍在 |
| 路径穿越 | `/api/media?path=../../Windows/win.ini` → 400 |
| 哈希一致 | 同一文件两次索引 sha256 相同 |
| 首扫性能 | 1000 个小文件 < 30s |
| 二次扫描 | 无变化 < 2s |
| 扫描任务 | `POST /scan` 返回 task_id，轮询到 done |
| PATCH | 标签/备注持久化，详情可见 |
| 旧兼容 | `/api/assets/groups`、`/groups/scan`、上传、旧 229 用例全过 |

验证命令：
- `backend\.venv\Scripts\python.exe -m unittest discover -s tests -v`
- `cd web && npm run gen:api && npm run test:ui && npm test && npx tsc -b && npm run build`
- 浏览器：`/assets?role=asset&q=定妆`，检查三栏、缩略图、详情、进度条、刷新保留参数；确认 `/prompts`、`/review` 的素材选择仍可用。

## 6. 风险与回退

- 首次全量扫描慢：后台线程 + 500 条批量提交 + 目录 mtime 增量；缩略图按需生成，不在扫描时生成。
- FTS trigram 不可用：启动探测一次，检索退回 LIKE。
- Alembic 在冻结构建中不可用：`ensure_schema` 退回 `create_all` + 原生 DDL，功能不回退。
- 路由冲突：旧分组接口迁到 `/groups`；新路由后挂。
- 回退：撤掉 `main.py` 中 watcher/新路由挂载即可，旧表与新表互不依赖，旧页面照常打开。

## 7. 待确认决策（本规格已选定）

1. 旧列表/扫描迁到 `/groups`、`/groups/scan`，同步更新旧测试路径。
2. 新表主名 `index_assets`，避免与 legacy `assets` 冲突。
3. 缩略图按需生成，不在扫描时预热。
4. 视频缩略图 P1 不生成，前端用类型占位图。
5. `op_log` 当前不存在，P1 直接新建含四列的表。
