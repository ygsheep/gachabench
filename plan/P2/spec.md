# P2 文件操作与回收站 Spec

## Why

P1 的资产库是只读的：能看、能搜、能筛，但改名、移动、删除都要离开应用手动做，而正文里写的是文件名，手改必然漏引用。P2 要把这些操作收进应用，并在动手前把影响面算清楚、可回滚。

## What Changes

- 新增 `operation_plans`、`trash_entries` 两张表（迁移 `0003`），计划 30 分钟过期。
- 新增引用扫描：把提示词上传表/正文、分镜素材列、workflow inputs 里的完整文件名写进 `asset_ref`；计划阶段重新读文件确认行号与区间。
- 新增区间替换器：只替换命中文件名的字符区间，写前做反向还原校验。
- 新增计划 / 应用 / 回滚三态接口，应用前校验哈希与 mtime，失败按 journal 逆序还原。
- 删除改为进 `.gachabench/trash/`，支持恢复（撞名加 `_02`）与彻底清除。
- 回收站、重复、未引用作为内置集合/视图复用现有资产网格。
- 前端多选出现操作条：改名、移动、删除；改名弹窗先出计划再应用，逐文件显示差异。

## Impact

- 受影响能力：资产索引（只读 → 可操作）、引用表、集合。
- 主要代码：
  - `backend/modules/assets/{plans,refs,trash}.py`（新）、`prompts/rewrite.py`（新）
  - `backend/modules/assets/{models,api}.py`、`backend/migrations/versions/0003_p2_ops.py`
  - `web/src/pages/AssetIndex.tsx`、`web/src/lib/{assetsApi,types}.ts`
- 不改旧 `assets` 表与旧上传/预览接口；旧资产库功能继续可用。

## 约束与决策

- 改名/移动只接受工作区内的相对路径；`..`、绝对路径、盘符一律 `PATH_INVALID`。
- 匹配文件名用转义后的完整名，前后不得紧邻字母、数字、下划线或汉字，避免 `A.png` 命中 `AA.png`。
- 应用顺序：校验 → 备份 → journal → 临时名 + `os.replace` → 改写引用 → 更新 DB → 删 journal。
- 只改大小写：先改 `.tmp-<uuid>` 再改目标名，避免 Windows 同名比较。
- 删除不改写引用；引用页显示「引用的文件已删除，可恢复」，恢复后自动有效。
- `.gachabench/` 已被索引跳过，备份、journal、回收站不会进资产列表。
- 失败即中止并回滚，不对 `PermissionError` 做重试循环。

## ADDED Requirements

### Requirement: 操作计划

系统 SHALL 提供 `POST /api/assets/operations/plan`，按 `kind`（`rename` / `move` / `delete`）计算影响面，返回受影响文件、引用、逐文件 before/after 与 blocked 项，不落盘。

#### Scenario: 改名计划
- **WHEN** 对 `白陌_写实_四视图.png` 提交改名 `白陌_写实_四视图_v2.png`
- **THEN** 返回所有引用该文件名的 md / json，含 `file_path`、`line`、`kind`、`before`、`after`
- **AND** 目标名已存在时该资产进入 `blocked`，原因为 `NAME_CONFLICT`，不可勾选

#### Scenario: 计划过期
- **WHEN** 计划创建 31 分钟后调用 apply
- **THEN** 返回 409 `PLAN_STALE`，不做任何文件改动

### Requirement: 应用与回滚

系统 SHALL 在 apply 前校验计划有效期、文件哈希与 mtime；成功后可 rollback 还原。

#### Scenario: 应用改名
- **WHEN** 应用一份未被改动的改名计划
- **THEN** 目标文件改名，所有命中引用同步改写，其余字节不变，`assets.path` 更新
- **AND** 备份保留在 `.gachabench/backups/<planId>/`

#### Scenario: 文件被占用
- **WHEN** 改名时抛出 `PermissionError`
- **THEN** 返回 `FILE_BUSY`，已完成的步骤按 journal 回滚，计划状态 `failed`

#### Scenario: 回滚
- **WHEN** 对已应用的计划调用 rollback
- **THEN** 文件路径与内容哈希恢复到应用前，引用文本还原，计划状态 `rolled_back`

### Requirement: 回收站

系统 SHALL 把删除操作落到 `.gachabench/trash/<YYYYMMDD>/<assetId>__<原名>`，而不是直接删除。

#### Scenario: 恢复
- **WHEN** 恢复一个回收站条目
- **THEN** 文件回到原路径，资产状态恢复 `active`，引用自动有效

#### Scenario: 恢复撞名
- **WHEN** 原路径已存在同名文件
- **THEN** 落到 `_02` 后缀，不覆盖已有文件，新路径写回资产记录

#### Scenario: 彻底清除
- **WHEN** 确认彻底清除
- **THEN** 删除回收站文件与资产行、关联 `asset_ref`，其他文件不动

### Requirement: 重复与未引用

系统 SHALL 提供 `GET /api/assets/duplicates`（按 sha256 分组，`hash_status="quick"` 不参与）与 `GET /api/assets/unreferenced`（无 `asset_ref` 且不在回收站）。

## MODIFIED Requirements

### Requirement: 资产集合

`GET /api/assets` 的 `collection` SHALL 额外支持 `trashed`（状态 `trashed`），与 `missing` 等内置集合共用同一列表与网格。

### Requirement: 资产详情引用

资产详情的 `refs` SHALL 来自 P2 引用扫描，包含 `kind`（`prompt_filename` / `prompt_body` / `storyboard_material` / `workflow_input`）与行号。

## 验收

见 `tasks.md` 的 T9 与 `plan/P2-文件操作与回收站.md` 的测试表。
