# P5 Agent 与 Patch

依赖：P3 中间层、P4 的区间应用与校验器。

## 目标

接一个可配置的外部模型，帮用户写初稿、查规则问题、按对话改提示词。模型只能产出结构化补丁，补丁进暂存区，看得到差异、确认后才写进 md。Provider 默认关闭，不配也能用全部其它功能。

## 进入条件

- P4 的 `apply_spans` 与六条不变量可复用。
- 现有 `routers/agent.py` 只有一个返回 501 的占位路由，P5 直接替换，不需要兼容旧行为。
- 明确本段只做“读正文、产 patch”，不做自动跑片。

## 范围

| 做 | 不做 |
| --- | --- |
| Provider 配置与连通性测试 | 不做本地模型管理 |
| 三种任务：初稿、规则诊断、对话调整 | 不做多轮自主循环 |
| Patch 生成、校验、过期、应用 | 不做自动应用 |
| 只读工具：查资产、查镜、查正文 | 不做写工具 |
| 冲突检测与单一待审 | 不做多人同时编辑（v2） |

## 步骤

1. 加依赖 `pydantic-ai`。Provider 配置存 `settings` 分组 `agent`：`provider`、`model`、`base_url`、`enabled`。密钥只进 Windows 凭据管理器，接口返回末四位。

2. 建 `backend/modules/agent/patch.py`，定义 patch 结构，字段固定：

| 字段 | 说明 |
| --- | --- |
| `id` | ULID |
| `segmentId` / `target` | 目标段与目标 |
| `scope` | `whole`、`shot:3`、`section:detailed_description` |
| `baseHash` | 生成时的正文哈希 |
| `hunks` | 每项含 `hunkId`、`start`、`end`、`before`、`after`、`reason` |
| `model` / `provider` | 来源标识 |
| `status` | `pending`、`applied`、`rejected`、`expired`、`conflict` |
| `expiresAt` | 生成后 24 小时 |

3. 暂存区：`.gachabench/staged/<patchId>.json`。生成阶段不写工作区任何文件，只写暂存区与 `patches` 表。

4. 建 `backend/modules/agent/service.py`。三种任务共用一条管线：

1) 组装上下文：段信息、镜列表、绑定素材名、当前正文、诊断规则结果；
2) 调用模型，要求输出 patch schema；
3) 校验 schema、范围、baseHash、编号规则；
4) 落暂存区与表，发 `patch.created` 事件；
5) 等待用户确认。

5. 三种任务的差异：

| 任务 | 输入 | 输出范围 |
| --- | --- | --- |
| 初稿 | 中间层与绑定，允许正文为空 | 整段 |
| 规则诊断 | 当前正文加规则表 | 命中的具体行区间，不给整段重写 |
| 对话调整 | 当前正文、用户一句话、选中的镜或 § | 选中范围 |

6. 只读工具集：

| 工具 | 作用 |
| --- | --- |
| `list_assets(subject, kind)` | 返回资产名与 id |
| `get_shot(segment, no)` | 返回单镜结构化字段 |
| `search_assets(q)` | 按名称与标签查 |
| `get_prompt(segment, target)` | 返回正文与哈希 |

工具实现直接调 `assets` 与 `prompts` 的 service，不给模型任何文件路径读写能力。

7. 校验器：

- 每个 hunk 的 `start`、`end` 必须落在生成的 `scope` 内；
- `before` 必须与当前正文该区间逐字相等；
- 多个 hunk 不允许重叠；
- 不允许改动镜号、`<Picture N>`、`<Subject N>`、`<Audio N>` 的编号；
- 违反任一条，整份 patch 废弃并记录原因，不部分接受。

8. 应用：`POST /api/agent/patches/{id}/apply` 带 `expectedSourceHash` 与 `hunkIds`。流程复用 P4：比对哈希、按区间倒序替换、反向还原校验、落盘、跑不变量、生成快照。已应用的 patch 再次应用返回 `PATCH_APPLIED`。

9. 冲突与过期：同一段同时只允许一个 `pending`，新请求把旧的置 `expired`。应用前发现文件哈希不同，置 `conflict` 并保留两侧内容供查看。

10. 接口：

| 方法与路径 | 说明 |
| --- | --- |
| `GET /api/agent/providers` | 配置与状态，密钥掩码 |
| `POST /api/agent/providers/test` | 发一条最小请求，返回耗时与错误 |
| `POST /api/agent/patches` | `{segment, target, mode, scope, instruction?}` |
| `GET /api/agent/patches?segment=` | 待审列表 |
| `GET /api/agent/patches/{id}` | 单份 patch 与差异 |
| `POST /api/agent/patches/{id}/apply` | 应用选中 hunk |
| `POST /api/agent/patches/{id}/reject` | 拒绝并保留原因 |

11. 前端：

- 设置页增加 Provider 分组，未启用时 Agent 入口置灰并说明；
- 段页右栏增加 Agent 面板：三个任务按钮、指令输入、生成进度；
- 结果以差异视图展示，按 hunk 勾选，应用前显示基准哈希与文件行号；
- 校验失败时列出具体原因，不提供“强制应用”。

## 数据变更

新增 `patches` 表，字段与 patch 结构对应；`settings` 增加 `agent` 分组。暂存区文件保留 7 天，过期清理。

## 测试与验收

| 用例 | 判定 |
| --- | --- |
| Provider 关闭 | 所有 Agent 接口返回 `PROVIDER_OFF`，其它功能不受影响 |
| 假 Provider 返回非法结构 | 丢弃并记录，不写暂存区 |
| 越界 hunk | 返回 `PATCH_SCOPE`，整份拒绝 |
| 基准哈希不符 | 返回 `PATCH_CONFLICT`，原文件不变 |
| 只应用一个 hunk | 其余位置逐字节不变 |
| 修改镜号 | 校验失败，patch 被拒 |
| 过期 | 25 小时后 apply 返回 `PATCH_EXPIRED` |
| 重复应用 | 第二次返回 `PATCH_APPLIED` |
| 日志 | 请求元信息可查，正文不进日志 |

## 风险与回退

模型输出不稳定是常态。校验器按“整份接受或整份拒绝”处理，不做局部修补，避免用户看到一半对一半错的正文。

成本与网络不可控。默认关闭，生成前显示将发送的段与镜数量；超时 30s 中止，不重试。

回退方式：Provider 关闭即回到 P4 状态，`patches` 表与暂存区可保留不影响主流程。

## 已落地（第一刀）

「规则诊断 + 可机械修的补丁」已实现，**不含模型**：

- `modules/prompts/checks.py`：12 条规则，读文本 + 可选中间层；
- `modules/prompts/patch.py`：三类自动修（正文时长、真实文件名、独白口型标注），
  复用 `rewrite.apply_spans` 的反向还原校验；
- `POST /api/prompts/segments/{seg}/check`、`/patch`；
- 前端右栏「检查」标签：分级结论 + 补丁预览 + 应用到草稿。

这样排的理由：初稿已有确定性渲染器，真正缺的是「写完挑错」；
那部分不需要模型，且比模型更准。详见 [P5/规则诊断与补丁.md](P5/规则诊断与补丁.md)。

模型那一半（Provider、暂存区、`patches` 表、只读工具集）仍按本文原计划，
等 Provider 配好再做；patch 结构与 `apply_spans` 直接复用。
