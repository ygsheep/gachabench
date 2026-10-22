# 分阶段实现方案

本目录把 [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) 的架构决定拆成可逐段交付的工程步骤，产品口径见 [../docs/REDESIGN-PRD.md](../docs/REDESIGN-PRD.md)。

每个阶段单独成文，按「目标 → 进入条件 → 范围 → 步骤 → 数据变更 → 接口 → 前端 → 测试与验收 → 风险」的顺序组织。阶段之间可以独立发布，前一段不通过验收就不进入下一段。

## 阶段总表

| 阶段 | 主题 | 依赖 | 主要交付 | 退出条件 |
| --- | --- | --- | --- | --- |
| [P0](P0-外壳与路由.md) | 外壳与路由 | 无 | 五项导航、路由树、接口类型生成 | 五个入口直链可开，旧链接可跳转 |
| [P1](P1-资产索引.md) | 资产索引 | P0 | 全工作区索引、FTS5、增量扫描、资产页只读 | 任一文件可搜到并看到引用 |
| [P2](P2-文件操作与回收站.md) | 文件操作与回收站 | P1 | 计划/应用/回滚、引用重写、回收站 | 改名 50 个引用文件，未选区域字节不变 |
| [P3](P3-分镜解析与中间层.md) | 分镜解析与中间层 | P1、P2 | 21 字段解析、素材绑定、三栏骨架 | 样本段解析出 8 镜且缺列可降级 |
| [P4](P4-双目标渲染.md) | 双目标渲染 | P3 | 两套模板、候选差异、区间应用、往返校验 | 两目标各自落盘且不变量全过 |
| [P5](P5-Agent与Patch.md) | Agent 与 Patch | P3、P4 | Provider、暂存区、patch 生命周期 | 未选区间不落盘，冲突可复现 |
| [P6](P6-审片台.md) | 审片台 | P1、P3 | 段/块/Take、时间映射、双联播放 | 点镜号定位，双联偏差在容差内 |
| [P7](P7-制作任务台.md) | 制作任务台 | P3、P6 | 任务快照、调度器、产物回收 | 同一快照重跑请求一致，产物进卡片 |
| [P8](P8-收尾与下线.md) | 收尾与下线 | P0–P7 | 旧路由下线、性能、诊断包、打包冒烟 | 全部验收项通过，安装包可启动 |
| [V2](V2-团队协作.md) | 团队协作与局域网同步 | P1、P2 的接缝 | 账本、授权、WS 同步、块传输 | 两台机器互看互改，越权写入被拒 |

v2 不与 v1 并行开工。P1 与 P2 只需把接缝留好，具体做法见 [V2](V2-团队协作.md) 的「v1 接缝」一节。

## 代码落点

v1 在 `backend/` 下新增 `modules/` 包，旧文件保持原位，逐段搬入：

```
backend/
├── main.py                    现有入口，路由挂载处扩展
├── db.py                      旧访问层，P1 之后只读，P8 删除
├── splitter.py                逻辑保留，P3 搬到 modules/prompts
├── prompt_locator.py          同上
├── routers/                   旧路由，P8 删除
├── modules/
│   ├── platform/              db.py engine、迁移、日志、媒体探测
│   ├── workspace/             目录角色、设置
│   ├── assets/                索引、文件操作、回收站
│   ├── prompts/               解析、中间层、渲染、校验
│   ├── review/                段块 take、时间映射
│   ├── produce/               任务与产物
│   └── agent/                 Provider 与 patch
├── migrations/                Alembic
└── tests/
```

每个业务模块内部分 `api.py`（路由）、`service.py`（流程）、`repo.py`（SQL）、`models.py`（表）。模块之间只调 service，不跨模块读表。

前端新增 `src/routes/` 与 `src/features/`，旧 `src/pages/` 在对应阶段替换，P8 删除：

```
web/src/
├── app/router.tsx             路由树与 Provider
├── routes/                    页面级路由
├── features/
│   ├── assets/
│   ├── prompts/
│   ├── review/
│   ├── produce/
│   └── settings/
├── lib/client.ts              openapi-fetch 实例
└── lib/openapi.json           后端生成的契约
```

## 共同约定

一个数据库文件服务所有模块。每个工作区的库仍在 `<工作区>/.gachabench/cards.db`，P1 起由 SQLAlchemy 连接，旧代码继续用 `sqlite3` 访问旧表，两者写不同表，P8 统一。

接口一律挂在 `/api` 下，错误用统一信封 `{error:{code,message,details,traceId}}`。前端类型由 `openapi.json` 生成，不手写请求与响应类型。

每个写操作都要落一条操作记录。P1 起 `op_log` 增加 `seq`、`prev_hash`、`op_hash`、`actor` 四列，为 v2 账本留位置。

路径统一用工作区相对的 POSIX 字符串。绝对路径只在打开工作区、定位文件、系统对话框三处出现。

## 每段的验收门

进入下一段之前，四件事同时成立：

1. 后端单测全过，新增能力有对应用例；
2. 前端类型检查与构建通过；
3. 该段新增接口在浏览器里能按验收表逐条复现；
4. 打包后的安装包至少启动一次，不要求功能齐全，但不能白屏。

验收项写成可判定的结果，例如「未选中区域字节不变」「越权写入返回 403」，不写「体验良好」这类无法验证的描述。

## 常用命令

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests

cd ..\web
npm test
npm run build
```

P1 起后端逐步迁到 pytest，保留旧用例可运行：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
```

打包冒烟：

```powershell
uv run --project backend --with pyinstaller python packaging/build.py
```
