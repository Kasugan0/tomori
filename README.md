# tomori

公共照明故障工单系统：面向**运维管理员 / 巡查员 / 维修人员**，完成「报障 → 分级派工 → 维修 → 验收 → 关闭或返修」的完整闭环。单体应用，单套 SPA，三角色由同一个前端的角色视图区分。

---

## 当前状态

> **阶段一（设计定稿）已完成，尚未开始编码。**

仓库当前**没有任何业务代码**——`backend/`、`web/`、`tests/`、`migrations/` 均不存在，`pyproject.toml` 的 `dependencies = []`。本仓库现阶段的交付物是**设计文档套件**，等待用户审阅裁决后方可进入阶段二实现。

仓库里仅有的可运行内容：`deploy/stack-guard.sh`（本地 PostgreSQL 栈启停与环境校验脚本，**已实测可用**）、`compose.yaml`（PostgreSQL 容器编排）、`deploy/postgres/init-dbs.sql`（创建 `tomori_dev` / `tomori_test`）。根目录的 `main.py` 是 `uv init` 留下的 hello world 占位，不是程序入口。

---

## 文档导航

| 文档 | 内容 |
|---|---|
| [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) | 3 个角色 / 9 个核心用例 / 29 条功能需求 + 29 条可验证判据 / `UC ↔ FR ↔ VC ↔ 测试层次` 追溯矩阵 / 四类「不做」清单 |
| [`docs/SCHEMA-DESIGN.md`](docs/SCHEMA-DESIGN.md) | 17 张业务表的列、约束、索引、Alembic 迁移策略（76 CHECK / 41 外键列 / 22 UNIQUE / 37 索引 / 6 部分唯一） |
| [`docs/BUSINESS-LOGIC.md`](docs/BUSINESS-LOGIC.md) | 4 套状态机（29 状态 / 36 条合法边 / 11 终态 / 0 死路）、三大非 CRUD 机制（区域派单、故障分级、返修闭环） |
| [`docs/API-CONTRACT.md`](docs/API-CONTRACT.md) | 57 个端点 / 34 个 `error.code` / 128 个 `data-testid` 锚点（121 已冻结 + 7 灰区待裁决） |
| [`docs/TECH-STACK.md`](docs/TECH-STACK.md) | 定稿技术栈与版本号、8 条前端版本陷阱、`vision/` 状态澄清、目录结构规划 |
| [`docs/TEAM-CHARTER.md`](docs/TEAM-CHARTER.md) | 9 人团队角色边界、写入域矩阵、反假绿纪律、坐标四级判据 |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | 本地 PG 栈启停、端口分配、远端 GPU 训练管线与产物回传校验 |
| [`audits/stage1-audit.md`](audits/stage1-audit.md) | 阶段一独立审计报告：6 项 P0 + 4 项 P1，已全部修正并复核 |

---

## 技术栈

**后端**　FastAPI · SQLAlchemy 2.0 Async · psycopg v3 · Alembic · PostgreSQL 17 · Python 3.12
**前端**　Vue 3 · TypeScript · Vite · Element Plus
**视觉**　Ultralytics YOLO · OpenCV（进程内 Python 模块，不做独立视觉服务）
**工具链**　`uv` · `pnpm` · Docker Compose

依赖布局为**单包**：根 `pyproject.toml` 是唯一的包定义，`backend/` 与 `vision/` 作为子目录模块；视觉依赖隔离在 `[project.optional-dependencies] vision` 中，用 `uv sync --extra vision` 安装，避免 `torch` 的 GB 级体积拖累后端开发。

---

## 快速开始（仅本地数据库栈）

```bash
./deploy/stack-guard.sh up      # 启动 PostgreSQL 容器并校验 tomori_dev / tomori_test 就绪
./deploy/stack-guard.sh status  # 容器、数据库、端口占用、E2E 租约一览
```

上述两条命令**现在就能跑**，效果是把本地 PostgreSQL 17.12 拉起来（`127.0.0.1:25432`）。

> ⚠️ **没有任何应用启动命令**。后端与前端尚未实现，本仓库**不提供**启动 Web 服务的命令；请不要按常规 FastAPI / Vue 项目惯例去猜命令行，试不通是预期结果，不是环境问题。
> 完整的环境准备、端口分配与远端 GPU 训练流程见 [`docs/RUNBOOK.md`](docs/RUNBOOK.md)。

---

## 边界

**用户明确要求的四条红线**（`docs/REQUIREMENTS.md` §5，不可扩展）：

- **不接灯控设备** — 无任何灯控协议或 IoT 客户端依赖，无远程控制端点
- **不做能耗预测** — 无时序预测库，无能耗相关表
- **不接实时监控** — 主动以 `415` 拒绝视频容器与 `rtsp://` / `m3u8` / `stream_url` 字段，而非静默忽略
- **AI 不替代电气安全检查** — 电气安全检查是维修员填写的 5 项清单，与视觉模块零耦合

**智能能力的边界**：只处理**静态巡查图片**；AI 产出的故障候选**必须经巡查员确认**才能进入派工，模型不得直接驱动业务。

**已知边界**：`vision/` 下的 8 个 `.py` 文件是**未经运行验证的预研草稿**，未纳入阶段一交付，未被任何测试覆盖。本仓库不对其可用性作任何承诺。

---

## 阶段划分

| 阶段 | 内容 | 状态 |
|---|---|---|
| 阶段一 | 需求、库表设计、业务逻辑、API 契约、技术栈、团队章程、运维手册 | ✅ 设计定稿，待用户审阅 |
| 阶段一审计 | 独立审计 6 项 P0 + 4 项 P1 | ✅ 全部修正并复核 |
| 阶段二 | 后端 / 前端 / 视觉 / 测试实现，本地编排 | ⬜ 未开始（**等用户授权**） |
