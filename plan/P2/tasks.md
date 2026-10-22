# P2 Tasks

- [x] T1 数据层与迁移：新增 `OperationPlan`、`TrashEntry` 模型与 `0003_p2_ops.py`（幂等建表 + 索引）。
- [x] T2 引用扫描：`modules/assets/refs.py` 扫描 md/json，按文件名边界匹配，写入/刷新 `asset_ref`；扫描流程结束时增量刷新。
- [x] T3 区间替换：`modules/prompts/rewrite.py` 的 `apply_spans`，倒序替换 + 反向还原校验。
- [x] T4 计划引擎：`modules/assets/plans.py` 实现 rename/move/delete 的计划生成，含 `NAME_CONFLICT`、`PATH_INVALID`、`blocked`、`stats`、30 分钟过期。
- [x] T5 应用与回滚：备份、journal、临时名 + `os.replace`、引用改写、DB 事务、失败逆序还原；只改大小写走 tmp 中转。
- [x] T6 回收站：删除入 trash、列表、恢复（撞名 `_02`）、彻底清除。
- [x] T7 接口：operations plan/apply/rollback/detail、trash、duplicates、unreferenced；`trashed` 集合。
- [x] T8 前端：多选操作条、改名计划弹窗与逐文件 diff、移动/删除确认、回收站集合、恢复与彻底清除。
- [x] T9 验收：后端单测覆盖 P2 计划表的 11 条用例；前端 tsc / vitest / build 通过；浏览器走通改名、删除、恢复。

# Task Dependencies

- T4 依赖 T2、T3
- T5 依赖 T4
- T6 依赖 T4
- T7 依赖 T4、T5、T6
- T8 依赖 T7
- T9 依赖 T1–T8
