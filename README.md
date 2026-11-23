# tomori

公共照明故障工单系统：面向**运维管理员 / 巡查员 / 维修人员**，完成「报障 → 分级派工 → 维修 → 验收 → 关闭或返修」的完整闭环。单体应用，单套 SPA，三角色由同一个前端的角色视图区分。

---

## 当前状态

> **阶段一定稿已完成，全部 40 条裁决已收口落定（`20 ✅ / 20 🟢`，零待决项），待用户最终放行阶段二实现。**

仓库当前**没有任何业务代码**——`backend/`、`web/`、`tests/`、`migrations/` 均不存在，`pyproject.toml` 的 `dependencies = []`。本仓库现阶段的交付物是精简合并后的**设计文档套件**（`docs/` 根下仅 5 份核心文档：`DECISION.md`、`REQUIREMENTS.md`、`DESIGN.md`、`API.md`、`RUNBOOK.md`，外加 `docs/team/`），等待用户最终放行后方可进入阶段二实现。

仓库里仅有的可运行内容：`deploy/stack-guard.sh`（本地 PostgreSQL 栈启停与环境校验脚本，**已实测可用**）、`compose.yaml`（PostgreSQL 容器编排）、`deploy/postgres/init-dbs.sql`（创建 `tomori_dev` / `tomori_test`）。根目录的 `main.py` 是 `uv init` 留下的 hello world 占位，不是程序入口。

---

## 文档导航

> `docs/` 根下仅保留以下 **5 份核心文档**（`DECISION.md`、`REQUIREMENTS.md`、`DESIGN.md`、`API.md`、`RUNBOOK.md`），外加 `docs/team/` 团队方法论与 `audits/` 历史快照。

| 文档 | 内容 |
|---|---|
| **[`docs/DECISION.md`](docs/DECISION.md)** | ⭐ **全项目裁决事项清单：40 条裁决已全部收口（`20 ✅ / 20 🟢`，按四档组织）—— 阶段一验收与定案口径的唯一入口** |
| [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) | 3 个角色 / 9 个核心用例 / 29 条功能需求 + 29 条可验证判据 / `UC ↔ FR ↔ VC ↔ 测试层次` 追溯矩阵 / 五项核心红线（N1–N5）与郑州 48h 法定基线 |
| [`docs/DESIGN.md`](docs/DESIGN.md) | **系统设计与技术定案（合并原技术栈 / 业务逻辑 / Schema 三份文档）**：系统架构与技术栈（含 8 条前端版本陷阱）、4 套状态机（29 态 / 36 边 / 11 终态 / 0 死路）、三大核心机制与郑州 48h SLA、17 张业务表（76 CHECK / 41 FK / 22 UNIQUE / 37 索引 / 6 部分唯一）、YOLO26 视觉管线（`yolo26s.pt @ 1024`）与 27 行（30 实验）变异基线 |
| [`docs/API.md`](docs/API.md) | 57 条路由行（展开为 60 个 (method, path) 端点） / 34 个 `error.code` / 12 个必需幂等端点 / 128 个已冻结 `data-testid` 锚点（121 + 第 J 组 7 项，`D-04` 已定案） |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | 本地 PG 栈启停、端口分配、远端 GPU 训练管线与产物回传校验 |
| [`docs/team/TEAM.md`](docs/team/TEAM.md) | 9 人团队角色边界、写入域矩阵、协作机制、反假绿纪律、坐标四级判据、提交协议 |
| [`docs/team/LEAD.md`](docs/team/LEAD.md) | Lead 方法论：角色边界、裁决四段模板、审计四问、7 次判据错误诚实记录、继任者检查清单 |
| [`audits/stage1-audit.md`](audits/stage1-audit.md) | 阶段一独立审计报告：6 项 P0 + 4 项 P1，已全部修正并复核（**历史快照，坐标以当时版本为准**） |

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

**用户明确要求的五项核心红线（`docs/REQUIREMENTS.md` §5，`N1`–`N5`，不可扩展）：**

- **N1 不接实时监控** — 无 RTSP/流媒体依赖，主动以 `415 MEDIA_VIDEO_STREAM_FORBIDDEN` 拒绝视频容器（MP4/MKV/AVI/FLV）、`video/*` MIME 与 `rtsp://` / `rtmp://` / `m3u8` / `stream_url` / `camera_*` 字段，而非静默忽略
- **N2 不做远程灯控** — 无任何灯控协议或 IoT 客户端依赖，无开关灯/调光/继电器/PLC 端点与设备指令表
- **N3 不做能耗/电量预测** — 无时序预测库，无能耗相关表与端点，`facility_assets.rated_power_w` 等静态参数不作预测输入
- **N4 不跳过巡查员确认（`V1`）** — AI 候选必须经巡查员确认后方可派工（`requires_inspector_confirmation` 恒 `true`；前端禁用 + `422 AI_CANDIDATE_NOT_CONFIRMED` + DB 三态串行化 `CHECK` 三层强制）
- **N5 不跳过现场电气安全检查（`V2`）** — 电气安全检查是维修员填写的「总开关 + 5 项清单 + 检查说明」，与视觉模块零耦合（`replaces_electrical_safety_check` 恒 `false`；前端 disabled + `422 ELECTRICAL_SAFETY_CHECK_REQUIRED` + DB `CHECK` 三层兜底）

**智能能力的边界**：只处理**静态巡查图片**；AI 产出的故障候选**必须经巡查员确认**才能进入派工，模型不得直接驱动业务。

**已知边界**：`vision/` 下的 8 个 `.py` 文件是**未经运行验证的预研草稿**，未纳入阶段一交付，未被任何测试覆盖。本仓库不对其可用性作任何承诺。

---

## 阶段划分

| 阶段 | 内容 | 状态 |
|---|---|---|
| 阶段一 | 需求规格、系统设计（架构/状态机/机制/Schema/视觉）、API 契约、裁决清单、团队章程、运维手册 | ✅ 设计定稿（40 条裁决已收口，待放行阶段二） |
| 阶段一审计 | 独立审计 6 项 P0 + 4 项 P1 | ✅ 全部修正并复核 |
| 阶段二 | 后端 / 前端 / 视觉 / 测试实现，本地编排 | ⬜ 未开始（**等用户授权**） |
