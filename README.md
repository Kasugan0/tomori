# tomori

公共照明故障工单系统：面向**运维管理员 / 巡查员 / 维修人员**，完成「报障 → 分级派工 → 维修 → 验收 → 关闭或返修」的完整闭环。单体应用，单套 SPA，三角色由同一个前端的角色视图区分。

---

## 当前状态

> **阶段一 + 阶段二均已完成**：冻结基线 `42eb94e`（设计文档套件）+ `origin/main` 最新 `50df47e`（`39` 条严格线性提交、全 `G` 签名），`43` 条裁决已收口落定（`23 ✅ / 20 🟢`，`D-41`/`D-42`/`D-43` 为 `nc=3`/`occluded` 弃用与 `merged_v6`/`merged_v7` 清洗管线新裁决，零待决项）。**阶段二已完结**：`60` 个业务端点 + Vue 3 SPA + `src/tomori/` 单包实现 + `streetlight_v3`（`nc=3`/`merged_v7`）训练完结并验收；`118` 单测+集成 + `31` E2E 全绿。

**阶段一冻结基线（`42eb94e`）时仓库没有任何应用代码**：无 `src/tomori/` 业务实现、无 `web/`、无 `tests/`、无 `migrations/`，`pyproject.toml` 的 `dependencies = []`。该基线当时唯一的交付物是精简合并后的**设计文档套件**（`docs/` 根下仅 5 份核心文档：`DECISION.md`、`REQUIREMENTS.md`、`DESIGN.md`、`API.md`、`RUNBOOK.md`；`docs/team/` 属本地过程产物，已 `.gitignore` 不入库）。

**阶段二已完结（`src layout`）**：Python 源码统一位于 **`src/tomori/`** 单包（所有 import 为 `tomori.*`），`alembic.ini` 与 `migrations/`（`0001..0004`）位于**仓库根**，前端 `web/`（Vue 3 SPA），测试 `tests/`（`unit`/`integration`/`e2e`），`60` 个业务端点 + `34` 个 `error.code` + `12` 个幂等端点全部落地；根级 `vision/` 仅存运行时产物（`weights/`、`reports/`，gitignored——`streetlight_v3` 自定义权重 `best_e2e.onnx` `nc=3` 已回传并经 `sha256sum -c` 校验），视觉源码位于 `src/tomori/vision/`。布局权威定义见 `docs/DESIGN.md` §1.1。

仓库里可运行的运维内容：`deploy/stack-guard.sh`（本地 PostgreSQL 栈启停与环境校验脚本，**已实测可用**）、`compose.yaml`（PostgreSQL 容器编排）、`deploy/postgres/init-dbs.sql`（创建 `tomori_dev` / `tomori_test`）。

---

## 文档导航

> `docs/` 根下现包含以下 **6 份核心文档**入库（阶段一定稿 5 份 `DECISION.md`、`REQUIREMENTS.md`、`DESIGN.md`、`API.md`、`RUNBOOK.md` + 阶段二新增用户请求的模型训练方案 **`TRAIN.md`**）；`docs/team/` 与 `audits/` 属**本地过程产物**（已 `.gitignore`，不入库）。

| 文档 | 内容 |
|---|---|
| **[`docs/DECISION.md`](docs/DECISION.md)** | ⭐ **全项目裁决事项清单：43 条裁决已全部收口（`23 ✅ / 20 🟢`，按四档组织，`D-41`/`D-42`/`D-43` 含 `occluded` 弃用 `nc=3` + `merged_v6`/`merged_v7` 清洗管线 + `data.yaml` 权威源）—— 阶段一验收与定案口径的唯一入口** |
| [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) | 3 个角色 / 9 个核心用例 / 29 条功能需求 + 29 条可验证判据 / `UC ↔ FR ↔ VC ↔ 测试层次` 追溯矩阵 / 五项核心红线（N1–N5）与郑州 48h 法定基线 |
| [`docs/DESIGN.md`](docs/DESIGN.md) | **系统设计与技术定案（合并原技术栈 / 业务逻辑 / Schema 三份文档）**：系统架构与技术栈（含 8 条前端版本陷阱）、4 套状态机（29 态 / `36` 转移规则条目·`47` `(from,to)` 图边 / 11 终态 / 0 死路）、三大核心机制与郑州 48h SLA、17 张业务表（76 CHECK / 41 FK / 22 UNIQUE / 37 索引 / 6 部分唯一）、YOLO26 视觉管线（`yolo26s.pt @ 1280`）与 27 行（30 实验）变异基线 |
| [`docs/API.md`](docs/API.md) | 57 条路由行（展开为 60 个 (method, path) 端点） / 34 个 `error.code` / 12 个必需幂等端点 / 128 个已冻结 `data-testid` 锚点（121 + 第 J 组 7 项，`D-04` 已定案） |
| [`docs/TRAIN.md`](docs/TRAIN.md) | **模型训练方案、数据集手册与实测记录**：`yolo26s.pt @ 1280` 架构、8 源多源精筛 + `merged_v6`/`merged_v7` 清洗管线（`mean_v>100` 全 `on` 删帧 + `occluded` 自证 + `damaged` 全保 + `--drop-occluded` `nc=3`）、`1024 vs 1280` 分辨率与算力对比、Union-Find / 64-bit pHash 防泄漏、`trainer.py` vs `ultralytics==8.4.216` 超参数全表（`epochs=500 + patience=20` 早停源码分析）、`[1,300,6]` ONNX 导出、`streetlight_v1`（`nc=4`）与 **`streetlight_v3`（`nc=3`，`val mAP50=0.607`/`test 0.662`）** 双实测回填与 `D-30`/`damaged` 验收 |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | 本地 PG 栈启停、端口分配、远端 GPU 训练管线与产物回传校验 |

> **本地过程产物（不入库）**：`docs/team/TEAM.md`（9 人角色边界/协作/反假绿/提交协议）、`docs/team/LEAD.md`（Lead 方法论）与 `audits/` 独立审计报告（如 `audits/stage1-audit.md` 等）属本地过程产物（已 `.gitignore`，不入库，仅本地工作树保留参考，不设仓库内可点击链接）。

---

## 技术栈

**后端**　FastAPI · SQLAlchemy 2.0 Async · psycopg v3 · Alembic · PostgreSQL 17 · Python 3.12
**前端**　Vue 3 · TypeScript · Vite · Element Plus
**视觉**　Ultralytics YOLO · OpenCV（进程内 Python 模块，不做独立视觉服务）；自定义权重 `yolo26s @1280` **`nc=3`**（`streetlight_on`/`streetlight_off`/`streetlight_damaged`），`streetlight_v3` 已训成产出（`vision/weights/` gitignored，`baseline_pretrained_opencv` 兜底降级）
**工具链**　`uv` · `pnpm` · Docker Compose

依赖布局为**单包 `src layout`**：根 `pyproject.toml` 是唯一的包定义（`[tool.uv.build-backend]` 的 `module-root` 为 `src`，包名 `tomori`），源码全部位于 `src/tomori/`；视觉依赖隔离在 `[project.optional-dependencies] vision` 中，用 `uv sync --extra vision` 安装，避免 `torch` 的 GB 级体积拖累后端开发。目录树与门禁命令见 `docs/DESIGN.md` §1.1 与 §6.1。

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
pnpm --dir web dev
```

> 完整的环境变量说明、静态/测试门禁（`uv run ruff check src tests`、`uv run ruff format --check src tests`、`uv run mypy src/tomori`、`uv run pytest tests/unit`、`pnpm --dir web typecheck`）、E2E 隔离端口与远端 GPU 训练流程见 [`docs/RUNBOOK.md`](docs/RUNBOOK.md)。

---

## 边界

**用户明确要求的五项核心红线（`docs/REQUIREMENTS.md` §5，`N1`–`N5`，不可扩展）：**

- **N1 不接实时监控** — 无 RTSP/流媒体依赖，主动以 `415 MEDIA_VIDEO_STREAM_FORBIDDEN` 拒绝视频容器（MP4/MKV/AVI/FLV）、`video/*` MIME 与 `rtsp://` / `rtmp://` / `m3u8` / `stream_url` / `camera_*` 字段，而非静默忽略
- **N2 不做远程灯控** — 无任何灯控协议或 IoT 客户端依赖，无开关灯/调光/继电器/PLC 端点与设备指令表
- **N3 不做能耗/电量预测** — 无时序预测库，无能耗相关表与端点，`facility_assets.rated_power_w` 等静态参数不作预测输入
- **N4 不跳过巡查员确认（`V1`）** — AI 候选必须经巡查员确认后方可派工（`requires_inspector_confirmation` 恒 `true`；前端禁用 + `422 AI_CANDIDATE_NOT_CONFIRMED` + DB 三态串行化 `CHECK` 三层强制）
- **N5 不跳过现场电气安全检查（`V2`）** — 电气安全检查是维修员填写的「总开关 + 5 项清单 + 检查说明」，与视觉模块零耦合（`replaces_electrical_safety_check` 恒 `false`；前端 disabled + `422 ELECTRICAL_SAFETY_CHECK_REQUIRED` + DB `CHECK` 三层兜底）

**智能能力的边界**：只处理**静态巡查图片**；AI 产出的故障候选**必须经巡查员确认**才能进入派工，模型不得直接驱动业务。

**已知边界**：`streetlight_v3`/`nc=3` 自定义权重已训成产出（`val mAP50=0.607`/`test 0.662`，`on 0.939`/`off 0.785`/`damaged 0.304`）并回传经 `sha256sum -c` 校验；视觉模块自定义模式已实跑（`D-30` 夜间亮灯参照 + `damaged` 类候选全通）；`baseline_pretrained_opencv` 兜底降级链保留（置信度受 DB `CHECK` 封顶 `0.65`），`strict_custom` 模式在权重缺失时返回 `503 VISION_DISABLED`。`streetlight_occluded` 已弃用（`d1` 独源 `227` 框 `~95%` 错标，`API` `fault_type=OCCLUDED` 保留手工报障）。

---

## 阶段划分

| 阶段 | 内容 | 状态 |
|---|---|---|
| 阶段一 | 需求规格、系统设计（架构/状态机/机制/Schema/视觉）、API 契约、裁决清单、运维手册 | ✅ 设计定稿（`42eb94e` 签名落盘，43 条裁决全部收口） |
| 阶段一审计 | 独立审计 6 项 P0 + 4 项 P1 | ✅ 全部修正并复核 |
| 阶段二 | `src/tomori/`（含 `vision/`）、`migrations/`（`0001..0004`）、`web/`、`tests/` 实现 + `streetlight_v3`/`nc=3` 训练完结 + `docs/TRAIN.md` 实测回填 | ✅ **已完结**（`60` 端点 + `118` 单测/集成 + `31` E2E 全绿；`merged_v7`/`nc=3` `streetlight_v3` `val mAP50=0.607`/`test 0.662`，`D-30`+`damaged` 验收通过，`origin/main = 50df47e`） |
