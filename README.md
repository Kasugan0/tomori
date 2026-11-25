# tomori

公共照明故障工单系统：面向**运维管理员 / 巡查员 / 维修人员**，完成「报障 → 分级派工 → 维修 → 验收 → 关闭或返修」的完整闭环。单体应用，单套 SPA，三角色由同一个前端的角色视图区分。

---

## 当前状态

> **阶段一 + 阶段二均已完成**：冻结基线 `42eb94e`（设计文档套件）+ `origin/main` 最新 `50df47e`（`39` 条严格线性提交、全 `G` 签名），`43` 条裁决已收口落定（`23 ✅ / 20 🟢`，`D-41`/`D-42`/`D-43` 为 `nc=3`/`occluded` 弃用与 `merged_v6`/`merged_v7` 清洗管线新裁决，零待决项）。**阶段二已完结**：`60` 个业务端点 + Vue 3 SPA + `src/backend/tomori/` 单包实现 + `streetlight_v3`（`nc=3`/`merged_v7`）训练完结并验收；`118` 单测+集成 + `31` E2E 全绿。

**阶段一冻结基线（`42eb94e`）时仓库没有任何应用代码**：无 `src/` 业务实现、无 `src/frontend/`、无 `tests/`、无 `migrations/`，`pyproject.toml` 的 `dependencies = []`。该基线当时唯一的交付物是精简合并后的**设计文档套件**（`docs/` 根下仅 5 份核心文档：`DECISION.md`、`REQUIREMENTS.md`、`DESIGN.md`、`API.md`、`RUNBOOK.md`；`docs/team/` 属本地过程产物，已 `.gitignore` 不入库）。

**阶段二已完结（`src` 三层布局）**：Python 源码统一位于 **`src/backend/tomori/`** 单包（所有 import 为 `tomori.*`，`module-name="tomori"`/`module-root="src/backend"`），`alembic.ini` 与 `migrations/`（`0001..0004`）位于**仓库根**，前端 `src/frontend/`（Vue 3 SPA，`pnpm`），测试 `tests/`（`unit`/`integration`/`e2e`），数据 DDL `src/database/{schema.sql,seed.sql}`，`60` 个业务端点 + `34` 个 `error.code` + `12` 个幂等端点全部落地；根级 `vision/` 仅存运行时产物（`weights/`、`reports/`，gitignored——`streetlight_v3` 自定义权重 `best_e2e.onnx` `nc=3` 已回传并经 `sha256sum -c` 校验），视觉源码位于 `src/backend/tomori/vision/`。

仓库里可运行的运维内容：`deploy/stack-guard.sh`（本地 PostgreSQL 栈启停与环境校验脚本，**已实测可用**）、`compose.yaml`（PostgreSQL 容器编排）、`deploy/postgres/init-dbs.sql`（创建 `tomori_dev` / `tomori_test`）。

---

## 文档导航

> `docs/` 根下现包含 **`docs/README.md` + `01`–`09` 编号文档 + `diagrams/` + `images/`**；旧版 `DECISION`/`REQUIREMENTS`/`DESIGN`/`API`/`RUNBOOK`/`TRAIN` 已并入 **`docs/local/`**（`gitignored` 不入库，仅追溯参考）。详细阅读顺序见 [`docs/README.md`](docs/README.md)。

| 文档 | 内容 |
|---|---|
| **[`docs/README.md`](docs/README.md)** | **文档导航**——`01`–`09` 阅读顺序、配套 `diagrams/`/`images/`、`docs/local/` 归档与 `F14` 一致性原则 |
| [`docs/01-案例说明.md`](docs/01-案例说明.md) | 背景与目标、`3` 角色/`17` 表/`60` 端点/`YOLO`/`nc=3`/术语 |
| [`docs/02-需求规格说明.md`](docs/02-需求规格说明.md) | `3` 角色/`FR-01..29`/`UC-01..09`/`N1-N5`/`business_rules`/`grading`/`sla`/`idempotency`/`audit_logs`/非功能需求 |
| [`docs/03-概要设计.md`](docs/03-概要设计.md) | `FastAPI`+`Vue3`+`PG17` 架构、`src/{backend,frontend,database}` 布局、模块划分与依赖红线 |
| [`docs/04-详细设计.md`](docs/04-详细设计.md) | `4` 状态机 `29` 态/`36` 规则条目·`47` `(from,to)` 图边/`transition_*`/`34` 错误码/`INVALID_STATE_TRANSITION`/`MANUAL_FALLBACK`/`DEAD_LETTER` |
| [`docs/05-数据设计.md`](docs/05-数据设计.md) | `17` 表/`76` CHECK/`41` FK/`37` 索引/`6` 部分唯一/`alembic` `0001..0004`/`seed_demo_data`/`business_rules` `5` 默认 |
| [`docs/06-接口设计.md`](docs/06-接口设计.md) | `61` 端点清单 + `health`/`ai-candidates`/`whitelist`/`mapping`/`VisionClient`/`error.code` `34`/`HTTP_STATUS_BY_CODE`/`fallback`/`MANUAL_FALLBACK`/`requires_inspector_confirmation`/`ai_is_baseline_mode`/`vision_ready` |
| [`docs/07-测试说明.md`](docs/07-测试说明.md) | `118` 单测+集成 + `31` E2E + `128` testid + `19` 变异杀灭 + `nc=3` 断言 + `69%` 覆盖率 + `PASS`/`SKIP`/`0` mock |
| [`docs/08-部署与使用说明.md`](docs/08-部署与使用说明.md) | `PG`/`uv`/`pnpm`/`uv sync`/`alembic`/`compose`/`env.sh`/`remote GPU`/`SSH`/`.env`/`application.yml`/`uvicorn`/`vite`/`worker`/`trainer`/`dataset_prep`/`rsync`/`sha256sum`/`admin`/`inspector`/`technician`/`health`/`ai-candidates`/`tickets`/`dispatches` |
| [`docs/09-需求追踪表.md`](docs/09-需求追踪表.md) | `requirement-traceability.md` `15` 格矩阵 + `FR`→端点→测试 `29` 条映射 |

> **本地过程产物（不入库）**：`docs/local/`（旧版 `DECISION`/`REQUIREMENTS`/`DESIGN`/`API`/`RUNBOOK`/`TRAIN` 六文档快照）、`docs/team/`（`TEAM.md`/`LEAD.md`）与 `audits/` 独立审计报告属本地过程产物（已 `.gitignore`，不入库，仅本地工作树保留参考）。

---

## 技术栈

**后端**　FastAPI · SQLAlchemy 2.0 Async · psycopg v3 · Alembic · PostgreSQL 17 · Python 3.12
**前端**　Vue 3 · TypeScript · Vite · Element Plus
**视觉**　Ultralytics YOLO `8.4.216`（`==` 精确 pin）· OpenCV（进程内 Python 模块，不做独立视觉服务）；自定义权重 `yolo26s.pt @ 1280` **`nc=3`**（`streetlight_on`/`streetlight_off`/`streetlight_damaged`），`streetlight_v3` 已训成产出（`vision/weights/` gitignored，`baseline_pretrained_opencv` 兜底降级）；训练口径 `epochs=500 + patience=20` + `data.yaml.names` 权威源
**工具链**　`uv` · `pnpm` · Docker Compose

依赖布局为**单包 `src/backend` 内 `tomori` 包**：根 `pyproject.toml` 是唯一的包定义（`[tool.uv.build-backend]` 的 `module-name="tomori"`、`module-root="src/backend"`），源码全部位于 `src/backend/tomori/`；视觉依赖隔离在 `[project.optional-dependencies] vision` 中，用 `uv sync --extra vision` 安装，避免 `torch` 的 GB 级体积拖累后端开发。目录树与门禁命令见 `docs/03-概要设计.md` 与 `docs/08-部署与使用说明.md`。

---

## 快速开始（本地数据库栈 + 阶段二应用）

```bash
# 1. 拉起本地 PostgreSQL 17.12（127.0.0.1:25432）并校验 tomori_dev / tomori_test 就绪
./deploy/stack-guard.sh up
./deploy/stack-guard.sh status

# 2. 同步 Python 依赖并执行 Alembic 异步迁移（需先按 .env.example 配置 .env）
uv sync
uv run alembic -c alembic.ini upgrade head

# 3. 启动后端 FastAPI（127.0.0.1:18141）与前端 Vite SPA（127.0.0.1:15173）
uv run uvicorn tomori.main:app --host 127.0.0.1 --port 18141
pnpm --dir src/frontend dev
```

> 完整的环境变量说明、静态/测试门禁（`uv run ruff check src tests`、`uv run ruff format --check src tests`、`uv run mypy src/backend/tomori`、`uv run pytest tests/unit`、`pnpm --dir src/frontend typecheck`）、E2E 隔离端口与远端 GPU 训练流程见 [`docs/08-部署与使用说明.md`](docs/08-部署与使用说明.md)。

---

## 边界

**用户明确要求的五项核心红线（`docs/02-需求规格说明.md` §4，`N1`–`N5`，不可扩展）：**

- **N1 不接实时监控** — 无 RTSP/流媒体依赖，主动以 `415 MEDIA_VIDEO_STREAM_FORBIDDEN` 拒绝视频容器（MP4/MKV/AVI/FLV）、`video/*` MIME 与 `rtsp://` / `rtmp://` / `m3u8` / `stream_url` / `camera_*` 字段，而非静默忽略
- **N2 不做远程灯控** — 无任何灯控协议或 IoT 客户端依赖，无开关灯/调光/继电器/PLC 端点与设备指令表
- **N3 不做能耗/电量预测** — 无时序预测库，无能耗相关表与端点，`facility_assets.rated_power_w` 等静态参数不作预测输入
- **N4 不跳过巡查员确认（`V1`）** — AI 候选必须经巡查员确认后方可派工（`requires_inspector_confirmation` 恒 `true`；前端禁用 + `422 AI_CANDIDATE_NOT_CONFIRMED` + DB 三态串行化 `CHECK` 三层强制）
- **N5 不跳过现场电气安全检查（`V2`）** — 电气安全检查是维修员填写的「总开关 + 5 项清单 + 检查说明」，与视觉模块零耦合（`replaces_electrical_safety_check` 恒 `false`；前端 disabled + `422 ELECTRICAL_SAFETY_CHECK_REQUIRED` + DB `CHECK` 三层兜底）

**智能能力的边界**：只处理**静态巡查图片**；AI 产出的故障候选**必须经巡查员确认**才能进入派工，模型不得直接驱动业务。

**已知边界**：`streetlight_v3`/`nc=3` 自定义权重已训成产出（`val mAP50=0.607`/`test 0.662`，`on 0.939`/`off 0.785`/`damaged 0.304`）并回传经 `sha256sum -c` 校验；视觉模块自定义模式已实跑（`D-30` 夜间亮灯参照 + `damaged` 类候选全通）；`baseline_pretrained_opencv` 兜底降级链保留（置信度受 DB `CHECK` 封顶 `0.65`），`strict_custom` 模式在权重缺失时返回 `503 VISION_DISABLED`。`streetlight_occluded` 已弃用（`d1` 源 `235` 框/`merged_v5` 合并 `227` 框 `~95%` 错标，`API` `fault_type=OCCLUDED` 保留手工报障）。

---

## 阶段划分

| 阶段 | 内容 | 状态 |
|---|---|---|
| 阶段一 | 需求规格、系统设计（架构/状态机/机制/Schema/视觉）、API 契约、裁决清单、运维手册 | ✅ 设计定稿（`42eb94e` 签名落盘，43 条裁决全部收口） |
| 阶段一审计 | 独立审计 6 项 P0 + 4 项 P1 | ✅ 全部修正并复核 |
| 阶段二 | `src/backend/tomori/`（含 `vision/`）、`src/frontend/`、`migrations/`（`0001..0004`）、`tests/` 实现 + `streetlight_v3`/`nc=3` 训练完结 + `docs/` `01`–`09` 实测回填 | ✅ **已完结**（`60` 端点 + `118` 单测/集成 + `31` E2E 全绿；`merged_v7`/`nc=3` `streetlight_v3` `val mAP50=0.607`/`test 0.662`，`D-30`+`damaged` 验收通过，`origin/main = 50df47e`） |
