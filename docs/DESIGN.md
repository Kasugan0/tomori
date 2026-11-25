> 状态：阶段二已放行（已按用户 `src layout` 裁决同步目录与门禁口径；核心契约沿用阶段一定稿）

# 系统架构与详细设计（DESIGN）

> **文档归属**：`docs-writer`｜**本文件写入域**：`docs/DESIGN.md`
> **合并来源（曾用名）**：本文档于 `task-44` 由原 `docs/TECH-STACK.md`、`docs/BUSINESS-LOGIC.md`、`docs/SCHEMA.md` 合并精简重写而成（原 `docs/SCHEMA.md` 曾用名 `docs/SCHEMA-DESIGN.md`）。历史审计报告中的旧文件坐标按此映射关系追溯。
> **读者**：`backend-dev`、`vision-dev`、`frontend-dev`、`tester`、`dev-ops`、`auditor`
> **数字与证据口径**：凡出现版本号、数量，均附采样依据或「分子 / 分母 / 排除项」；凡工程判断均标注 `🟡 工程判断`，一手实测标注 `🟢`，未实测/不可用通道标注 `🔴`。

---

## 0. 统计口径分层声明（`docs/team/TEAM.md` §5.3 硬要求）

**任何数字在本文件出现，必须连同「分子 / 分母 / 排除项」三层一并引用。**

| 项 | 分子 | 分母 | 排除项 |
|---|---:|---|---|
| **业务表** | **17** | 本文 §4 逐表定义（①–⑰） | **`alembic_version` 不计入**（Alembic 迁移元数据表）；4 张被合并候选表不单建 |
| **CHECK 约束** | **76** | 17 表 DDL 显式声明的具名 `CHECK` | `NOT NULL`（PG `attnotnull`，不进 `table_constraints`） |
| **外键列数** | **41** | 17 表所有引用列 | **≠ 外键约束数**（迁移后以 `pg_constraint` `contype='f'` 实测对账并解释差异） |
| **UNIQUE 约束/唯一索引** | **22** | 具名唯一约束 + 唯一索引（**含 6 个部分唯一索引**） | PG 主键隐式 `*_pkey` 索引；不重复计列内联隐式索引 |
| **普通（非唯一）索引** | **37** | 手写声明的非唯一索引（含 FK 支撑索引与 **12 个非唯一部分索引**） | 唯一索引（归上行 22）与主键索引 |
| **部分唯一索引** | **6** | 带 `WHERE` 的唯一索引（属 UNIQUE 22 子集：**4 个业务防重 + 2 个配置态唯一**） | 12 个带 `WHERE` 的非唯一部分索引（只缩扫描面，不保唯一） |
| **非唯一部分索引** | **12** | 带 `WHERE` 的非唯一索引（属普通索引 37 子集） | 部分唯一索引 6 个 |
| **状态机套数** | **4** | 本文 §2（A 故障单 / B 派工 / C 返修 / D 通知） | `vision_analyses` 等一次性记录状态不构成业务状态机 |
| **状态总数** | **29** | A 9 + B 7 + C 8 + D 5 | — |
| **合法转移边** | **36** 个转移规则条目 = A 15 + B 6 + C 8 + D 7；按 `(from,to)` 字面图边展开为 **47** = A 16 + B 8 + C 14 + D 9（两口径，见 §2 口径说明） | 本文 §2 各转移表（编号行 `42` = `36` 规则条目 + `6` 条建单/初始入口行） | 初始创建入口行（`— → 初始态`，共 6 行）与非法黑名单边 |
| **终态总数** | **11** | A 2 + B 5 + C 3 + D 1（含通知 `SENT`） | — |
| **死路（非终态无出边）** | **0** | 29 − 11 = 18 个非终态逐一验证 | — |
| **跨表不变量** | **8**（`I1`–`I8`） | 本文 §3.1 | 单表列级 `CHECK`（归 76） |

> **口径补充**：
> - 非 CRUD 核心业务机制共 **3** 项（区域授权派单 / 表驱动确定性故障分级 + 郑州 48h SLA / 返修闭环与超阈值复核，见 §3.2–§3.4）；幂等与审计属横切基础设施（§3.5），不计入该数。
> - 终态构成逐机核对：A 机 2（`CLOSED` / `CANCELLED`）+ B 机 5（`REJECTED` / `COMPLETED` / `TIMEOUT_REASSIGNED` / `ESCALATED` / `CANCELLED`）+ C 机 3（`PASSED` / `CLOSED_BY_REVIEW` / `CANCELLED`）+ D 机 1（`SENT`，`DEAD_LETTER` 有 D7/D8 出口不属终态）= **11**。
> - 阶段二虽已放行，但 `migrations/` 尚未创建、迁移未在真库执行，故 `17 / 76 / 41 / 22 / 37 / 6 / 12` 均仍是 **DDL 设计文本的可复算计数**，并非 `information_schema` 实测值；阶段二迁移落地后必须运行 §4.1 对账 SQL 核实。

---

## 1. 系统架构与技术栈定案

### 1.1 单应用三角色架构、目录布局与端口分配

**一句话架构**：单应用单体架构 —— 后端 FastAPI（异步）+ PostgreSQL 17（`17.12`）+ SQLAlchemy 2.0 Async + `psycopg` v3；前端 Vue 3 + TypeScript + Vite + Element Plus；视觉 Ultralytics YOLO26s + OpenCV，**以进程内 `tomori.vision` 子包嵌入后端**（`D-37`），不拆独立视觉微服务。

#### 单应用三角色前端形态（非多 SPA）

| 项 | 决定 |
|---|---|
| 应用形态 | **单个 Vue 3 SPA**，一套构建产物（`web/`） |
| 三角色 | `ADMIN` / `INSPECTOR` / `TECHNICIAN` 共用同一入口；不拆三套前端 |
| 权限渲染 | `GET /auth/me` 返回 `role_code` + `scope_authorizations[]` → 前端据此渲染菜单与门禁按钮 |
| 路由守卫 | **全局守卫**按 `to.meta.roles` + 登录态判定 |
| 移动端 | `nav-mobile-*` 响应式适配（非独立应用） |

#### 规划目录布局与单包依赖落点（`D-32`）

**包组织裁决（`D-32` + `src layout`）**：采用**单根 `pyproject.toml`（`src layout`：`[tool.uv.build-backend]` 的 `module-root` 为 `src`——即实际默认值，包名 `tomori`）+ `[project.optional-dependencies] vision`**。全部应用与视觉源码均位于 `src/tomori/`，所有 import 统一为 `tomori.*`（如 `from tomori.settings import Settings`、`from tomori.models import FaultTicket`），**不另设子包 `pyproject.toml`**，全仓唯一 `uv.lock`。纯后端开发 `uv sync`（不拉取 GB 级 `torch`）；视觉开发/训练/全量 E2E 用 `uv sync --extra vision`。

> ✅ **阶段二已放行**（用户原话：「现在放行代码实现。注意我们的代码布局，使用的是 src layout」），以下目录树为唯一权威布局（`pyproject.toml`、`migrations/versions/0001..0004` 与 `src/tomori/vision/` 11 个模块〔含新增只读 EDA 工具 `eda.py`，另有 `dataset_prep.py`、`trainer.py`，共 13 个 `.py` 文件〕均已落盘；原根级 `vision/*.py` 已移除，仅留根级 `vision/` 空目录供运行时 `weights/`/`reports/` 产物）：

```text
tomori/                                 # 仓库根
├── pyproject.toml                      # ★ 唯一 Python 包定义（src layout 单包，module-root="src"，包名 tomori）
│                                       #   [project.optional-dependencies] vision = [...]
├── uv.lock                             # 全仓唯一 Python lockfile
├── alembic.ini                         # 根级；sqlalchemy.url 留空，由 migrations/env.py 从 Settings 注入
├── migrations/                         # 根级 Alembic 异步迁移目录
│   ├── env.py                          # 异步迁移模板（import 全部 17 个 tomori.models）
│   ├── script.py.mako
│   └── versions/
│       ├── 0001_initial_schema.py
│       ├── 0002_partial_unique_indexes.py
│       ├── 0003_circular_fk_scheme_b.py  # 显式 op.create_foreign_key（alembic_version.version_num 上限 VARCHAR(32)，缩短至 25 字符）
│       └── 0004_business_rules_seed.py
├── src/
│   └── tomori/                         # ★ 唯一 Python 包本体（所有 import 均为 tomori.*）
│       ├── __init__.py
│       ├── main.py                     # create_app() + lifespan + 统一异常处理器
│       ├── settings.py                 # pydantic-settings Settings（读 .env）
│       ├── db/{base.py,session.py,types.py}
│       ├── models/                     # 17 张业务表，models/__init__.py 汇总
│       ├── schemas/                    # Pydantic 请求/响应模型，与 models 一一对应
│       ├── api/{deps.py,errors.py,v1/}
│       ├── services/                   # ★ 唯一允许改状态的地方（state_machine.py）
│       ├── workers/{sla_scanner.py,notifier.py}
│       └── vision/                     # 已迁入落盘 11 个模块（原 8 份草稿迁入修复 + client.py/mapping.py + 新增只读 EDA 工具 eda.py；另有 dataset_prep.py、trainer.py，共 13 个 .py 文件；重依赖在函数内延迟 import，D-37 + D1 隔离）
├── vision/                             # 运行时产物目录：weights/、reports/，gitignored，不在 src 内（源码已迁出）
├── web/                                # 前端 Vue 3 + TypeScript + Vite（pnpm 管理）
└── tests/{conftest.py,unit/,integration/,e2e/}
```

> **`alembic.ini` 与 `prepend_sys_path` 说明**：`src layout` 下 `uv run` 已将可编辑安装的 `src/tomori` 放入 `sys.path`，`alembic.ini` 无需额外配置 `prepend_sys_path = src`；若脱离 `uv` 直接运行 `alembic`，可在 `alembic.ini` 设置 `prepend_sys_path = src`（Alembic 官方 1.5.5+ 支持）。

#### 代码分层硬纪律与唯一状态变更入口

| 层 | 允许 | 禁止 |
|---|---|---|
| `api/v1/*` | 解析请求、调用 `services/`、序列化响应 | **不写业务规则**；不直接 `session.execute(update(...).values(status=...))` |
| `services/*` | 业务规则、状态机、事务编排 | 不 import `fastapi.Request/Response`（纯函数必须可独立单测） |
| `models/*` | 表结构、约束、关系 | 不写业务逻辑 |
| `db/*` | 引擎/会话/元数据 | 不 import `models`（方向恒为 `models → db/base.py`，防循环依赖） |

> **唯一状态变更入口（防绕过硬纪律）**：`fault_tickets` / `dispatch_records` / `rework_orders` 的 `status` 更新**只允许**经由 `src/tomori/services/state_machine.py` 的 `transition_*()` 函数。实现期任何 `session.execute(update(...).values(status=...))` 出现在 `state_machine.py` 之外，一律视为缺陷并要求改回，以确保不变量 `I7`（审计）与 `I8`（通知）物理受控。

#### 端口分配（五方一致）与宿主机实测基线

> 本项目自定工程要求「五方一致」：`docs/DESIGN.md`（本表）/ `docs/RUNBOOK.md` / `compose.yaml` / `.env.example` / `deploy/stack-guard.sh`。端口数字与运维操作细节以 `docs/RUNBOOK.md`（`dev-ops` 独占）为最终权威，本文只做一致性同步，不复制容器/卷/网络操作指令。

| 服务 | 端口 | 绑定 | 配置项 | 说明 |
|---|---|---|---|---|
| PostgreSQL 容器 | **`25432`** → `5432/tcp` | `127.0.0.1:25432` | `TOMORI_PG_PORT=25432` | 避开宿主机占用的 `15432` 与 `5432` |
| 后端 API（开发） | **`18141`** | `127.0.0.1:18141` | `BACKEND_PORT=18141` | FastAPI / uvicorn |
| Vite 前端（开发） | **`15173`** | `127.0.0.1:15173` | `FRONTEND_PORT=15173` | 开发热更新 |
| 后端 API（E2E） | **`18142`** | `127.0.0.1:18142` | `E2E_BACKEND_PORT=18142` | 仅 `claim-e2e` 独占锁窗口内使用 |
| Vite 前端（E2E） | **`15174`** | `127.0.0.1:15174` | `E2E_FRONTEND_PORT=15174` | 同上（避免与开发页抢同进程/同库状态） |

| 组件（本地宿主机实测） | 实测版本 | 备注 |
|---|---|---|
| PostgreSQL 服务端 | **`17.12`** | 镜像 `postgres:17.12-alpine` digest 钉版，禁 `:latest`（`D-10` 不兼容 16） |
| `psql` / `pg_isready` 客户端 | `18.7` | 宿主客户端高于服务端属正常 |
| Python（`.venv`） | **`3.12.11`** | 与 `.python-version=3.12`、`requires-python=">=3.12"` 一致 |
| `uv`（本地 / 远端） | `0.8.14` / `/root/.local/bin/uv 0.13.7` | 远端见 `docs/RUNBOOK.md` §6.1.1 |
| Node / `pnpm` | `v26.13.1` / `11.1.1` | — |
| Docker / Compose | `29.10.0` / `5.8.0` | — |

#### 配置项与密钥纪律

| 项 | 纪律 |
|---|---|
| 配置文件 | `.env`（从 `.env.example` 复制）；`.env` **不进版本库**（`.env.example` 归 `dev-ops` 独占） |
| 密钥 | `JWT_SECRET_KEY` **无默认值，缺失即启动失败**，杜绝默认开发密钥泄漏至生产 |
| 数据库 URL | `postgresql+psycopg://...`；`alembic.ini` 的 `sqlalchemy.url` **留空**，由 `migrations/env.py` 从 `Settings` 注入 |
| 上传与像素上限 | `MAX_UPLOAD_SIZE_MB=15`（= `15,728,640` B，`D-02`）；像素上限 `40_000_000`（代码常量，与 `Image.MAX_IMAGE_PIXELS` 同值）；单次请求文件数 `1..10`（`D-03`） |
| 视觉配置 | `YOLO_INFERENCE_ENABLED`（`false` → AI 端点返回 `503 VISION_DISABLED`，人工报障不受影响）；`YOLO_INFERENCE_MODE`（`auto\|strict_custom`）；`YOLO_IMG_SIZE=1280`（`D-28`）。单次推理超时由后端代码常量 `VISION_TIMEOUT_SECONDS = 60` 固定（**不入 `.env`**，`.env.example` 无此键） |

---

### 1.2 后端技术栈与版本锁死

#### 数据库不可替代性：为什么必须是 PostgreSQL 17（`17.12`，`D-10`）

| 本项目依赖的特性 | PostgreSQL 17 | MySQL 8 | SQLite |
|---|---|---|---|
| **部分唯一索引**（`WHERE status NOT IN (...)`） | ✅ | ❌ | ⚠️ 无真实并发，且行为差异需防假绿 |
| `jsonb` + `->>` 路径求值（`CHECK` 内反向校验） | ✅ | ⚠️ 表达受限 | ⚠️ |
| 复杂跨列 `CHECK` / 正则 `~` / 枚举集合 | ✅ | ⚠️ 8.0.16+ | ⚠️ |
| `text[]` 数组与 `@>` 包含判断 | ✅ | ❌ | ❌ |
| `FOR UPDATE SKIP LOCKED` 队列抢占 | ✅ | ✅ | ❌ |
| `timestamptz` 时区感知存储与比较 | ✅ | ⚠️ | ❌ |

> **决定性排他理由**：核心业务防重「同一点位同类型只允许一条活跃故障单」依赖部分唯一索引 `uq_fault_tickets_active_point_type ... WHERE status NOT IN ('CLOSED','CANCELLED')`；集成测试亦**严禁使用 SQLite**（否则退化为全表唯一且丢失 `timestamptz` / `numeric` 语义）。

#### 后端与视觉/测试依赖精确版本表（🟢 2026-11-21 PyPI JSON API 实测）

| 包名 | 锁定 / 实测版本 | `requires_python` | 作用与关键约束 |
|---|---|---|---|
| Python | **`>=3.12`**（`.venv 3.12.11`） | — | 与 `.python-version` 一致 |
| `fastapi[standard]` | **`0.165.0`**（2026-11-18） | `>=3.11` | 异步 Web 框架；已依赖 `starlette>=1.6.0`（1.x API）、`pydantic>=2.9.0`；`[standard]` 附带 `uvicorn` / `python-multipart` |
| `sqlalchemy[asyncio]` | **`>=2.0.58,<2.1`**（✅ **`D-05` 定案**） | `>=3.7` | `2.0.58` 与 `2.1.9` 同于 2026-11-16 发布（2.0 仍活跃维护）；知道 2.1 存在并主动锁 2.0 线，原因见下方决策块；**强制带 `[asyncio]` extra** |
| `psycopg[binary]` | **`3.3.9`**（2026-11-12） | `>=3.10` | PG v3 驱动，统一同步/异步接口；免容器编译；唯一合法 scheme 为 `postgresql+psycopg://` |
| `alembic` | **`1.20.1`**（2026-10-22） | `>=3.10` | 异步迁移模板（`alembic init -t async`）；`SQLAlchemy>=2.0` 宽松兼容 |
| `pydantic` | **`2.14.4`**（2026-11-19） | `>=3.10` | v2 校验与 OpenAPI 序列化（`model_config` / `field_validator` / `model_validator`） |
| `pydantic-settings` | **`2.16.3`**（2026-11-13） | `>=3.11` | `BaseSettings` 从 `.env` 读配置（已从 pydantic 主包迁出） |
| `uvicorn[standard]` | **`0.54.0`**（2026-09-25） | `>=3.10` | ASGI 服务入口；`[standard]` 附带 `uvloop`/`httptools`/`watchfiles`/`websockets`（生产与 dev 同口径，`task-63` 实测落地） |
| `httpx` | **`0.28.1`**（约束 `>=0.28.1,<1.0.0`） | `>=3.8` | 主依赖显式声明——`respx 0.23.1` 仅支持 `httpx` 做 HTTP mock；ASGI 生命周期测试另用 `httpx2` |
| `pwdlib[argon2]` | **`0.4.2`** | — | Argon2 密码哈希（FastAPI 官方 JWT 教程已从 `passlib` 迁至 `pwdlib`） |
| `pyjwt` | **`2.15.1`** | — | `HS256` JWT 签发与校验（载荷含 `sub`/`tv`/`role`/`exp`） |
| `pillow` | **`>=11.0.0`**（视觉侧记 `13.0.0`） | — | **服务端**独立安全边界：魔数、单帧检查、`Image.MAX_IMAGE_PIXELS=40_000_000` 防解压炸弹（禁止设为 `None`） |
| `python-multipart` | `>=0.0.18`（PyPI 配套） | — | `multipart/form-data` 解析（缺它报 500） |
| `tzdata` | **`2026.6`** | — | Alpine 容器缺系统 tzdata，`ZoneInfo("Asia/Shanghai")` 依赖它 |
| `pytest` / `pytest-asyncio` / `pytest-cov` | **`9.2.0`** / **`1.5.0`** / **`7.1.0`** | `>=3.10` | `asyncio_mode="auto"`（1.x 已取消自定义 `event_loop` fixture） |
| `httpx2` / `asgi-lifespan` / `respx` | **`2.16.2`** / **`>=2.1.0`** / **`0.23.1`** | `>=3.11` / — / `>=3.8` | Starlette 官方 `TestClient` 已基于 `httpx2`（`httpx 0.28.1` deprecated）；`ASGITransport` 不触发 lifespan，需 `LifespanManager`；`respx` 仅 mock `httpx` |
| `ruff` / `mypy` / `pytest-playwright` | **`0.17.6`** / **`>=2.3`**（实测解析 `2.4.1`） / **`>=0.10.0`** | — | `ruff` lint+format；`mypy`（`D-08` 工程定案，**下限收紧至 `>=2.3`**——`pydantic 2.14.4` 已停测更旧 mypy）；`pytest-playwright` 统一 E2E（支撑合法种子与异步 DB 断言） |
| `ultralytics` / `opencv-python-headless` / `onnxruntime` / `onnx` / `onnxslim` / `numpy` | **`8.4.216`** / **`5.1.0.95`** / `>=1.20.0,<2.0.0` / **`1.23.2`**（约束 `>=1.16.0,<2.0.0`） / **`0.1.99`**（约束 `>=0.1.82`） / `>=2.0.0,<3.0.0` | `>=3.8` / `>=3.6` / — / `>=3.10` / `>=3.8` / `>=3.12` | `[project.optional-dependencies] vision` 分组隔离 `torch`/`torchvision`；本地推理默认加载 ONNX CPU `CPUExecutionProvider`；`onnx`/`onnxslim` 为 `simplify=True` 导出所需；`numpy` 显式声明以免被 `opencv`/`torch` 传递依赖拉到非预期版本（见下方 `vision` extra 说明） |

> **SQLAlchemy 版本决策记录（`D-05`：知道 `2.1.9` 已 GA，主动锁定 2.0 线）**：① 用户原话契约为 `SQLAlchemy 2.0 Async`；② `2.0.58` 与 `2.1.9` 同日发版证明 2.0 线仍有安全与缺陷维护通道；③ 阶段一全套 API 断言取自 `rel_2_0_*` 文档且 Context7 MCP 通道在本环境**间歇性不稳定**（§1.4，不可作为唯一取证通道），不应在 4 周交付期叠加大版本迁移；④ 阶段三（如有）再评估升 2.1（重点核查 `AsyncAttrs`、`get_async_bind()` 与 `greenlet`）。

#### 根 `pyproject.toml` 完整依赖骨架（✅ `task-63` 已实测落盘，与仓库根实际文件一致）

```toml
[build-system]
requires = ["uv_build>=0.8.14,<0.14"]
build-backend = "uv_build"

[project]
name = "tomori"
version = "0.1.0"
description = "Public lighting fault inspection and work-order system (#141)"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
  "fastapi[standard]>=0.165.0",
  "uvicorn[standard]>=0.54.0",
  "sqlalchemy[asyncio]>=2.0.58,<2.1",
  "psycopg[binary]>=3.3.9",
  "alembic>=1.20.1",
  "pydantic>=2.14.4",
  "pydantic-settings>=2.16.3",
  "pwdlib[argon2]>=0.4.2",
  "pyjwt>=2.15.1",
  "pillow>=11.0.0",
  "python-multipart>=0.0.18",
  "tzdata>=2026.6",
  "httpx>=0.28.1,<1.0.0",
]

[project.optional-dependencies]
vision = [
  "ultralytics==8.4.216",
  "opencv-python-headless>=5.1.0,<6",  # 2026-11-21 PyPI 实测 5.1.0.95，精确版本锁入 uv.lock
  "onnxruntime>=1.20.0,<2.0.0",
  "onnx>=1.16.0,<2.0.0",
  "onnxslim>=0.1.82",
  "numpy>=2.0.0,<3.0.0",
]

[dependency-groups]
dev = [
  "pytest>=9.2.0",
  "pytest-asyncio>=1.5.0",
  "pytest-cov>=7.1.0",
  "pytest-playwright>=0.10.0",
  "httpx2>=2.16.2",
  "asgi-lifespan>=2.1.0",
  "respx>=0.23.1",
  "ruff>=0.17.6",
  "mypy>=2.3",
]

[tool.uv]
# ultralytics==8.4.216 无条件请求 GUI 版 opencv-python（依赖 libGL.so.1），
# 用永假 marker 覆盖，确保只有 opencv-python-headless 提供 cv2（uv 0.8.14+ 兼容）。
override-dependencies = ["opencv-python; python_version < '0'"]

[tool.uv.build-backend]
module-name = "tomori"
module-root = "src"

[tool.mypy]
python_version = "3.12"
mypy_path = "$MYPY_CONFIG_FILE_DIR/src"
files = ["src", "tests"]
strict = true
plugins = ["pydantic.mypy"]
```

> **`[tool.uv] override-dependencies` 是必须项（`task-63` 实测结论）**：`ultralytics==8.4.216` 无条件请求 **GUI 版 `opencv-python`**（依赖 `libGL.so.1`），会与 `opencv-python-headless` 争抢同一 `cv2` 命名空间并可能静默覆盖。用永假 marker `"opencv-python; python_version < '0'"` 覆盖其依赖声明，确保**只有** `opencv-python-headless` 提供 `cv2`；该写法同时兼容本机 `uv 0.8.14` 与远端 `uv 0.13.7`。
>
> **`[build-system]` 的 `uv_build` 版本区间**：`requires = ["uv_build>=0.8.14,<0.14"]` —— 下限对齐本机 `uv 0.8.14`（内置后端版本与 uv 同号），上限 `<0.14` 对齐远端 `uv 0.13.7`，保证两端均可解析；`[tool.uv.build-backend]` 显式写 `module-name = "tomori"` / `module-root = "src"` 而非依赖默认值，使 src layout 契约显式可审。

> **`vision` extra 必须显式声明 `onnx>=1.16.0,<2.0.0` 与 `onnxslim>=0.1.82` 的理由**：生产/容器环境设 `YOLO_AUTOINSTALL=False`（禁止 `ultralytics` 运行期静默装包），而 Ultralytics 默认 `simplify=True` 导出 ONNX 时不仅依赖其 `[export-base]` extra 中的 `onnx` 与 `onnxslim`，更须对齐 `ultralytics==8.4.216` `exporter.py:1207`（`requirements = ["onnx>=1.16.1,<1.19.0" if rknn else "onnx>=1.12.0,<2.0.0"]`，`rknn` 特例 `>=1.16.1`）与 `:1217`（`onnxslim>=0.1.82`）的 `check_requirements` 硬要求；本项目声明 `onnx>=1.16.0,<2.0.0` 属**主动收紧**（比上游 `>=1.12.0` 更严），若不在此显式声明并收紧下限，`model.export(format="onnx", simplify=True)` 会在 `YOLO_AUTOINSTALL=False` 下直接报错，且可防止 `0.1.31..0.1.81` 在该限制下被拒导（见 `docs/RUNBOOK.md` §6 权重/导出门禁）。`ultralytics==8.4.216` 的 `==` 精确 pin 与 `docs/RUNBOOK.md` 锁定口径完全一致（`>=` 会解析到后续补丁版，与「锁定 `8.4.216`」意图不符）。

#### SQLAlchemy 2.0 强制异步用法与常见陷阱

| # | 强制用法 / 陷阱 | 为什么必须 |
|---:|---|---|
| 1 | **`sqlalchemy[asyncio]` extra 强制保留** | 2.0.x 在 Apple Silicon (`arm64`) 等平台不默认装 `greenlet`，2.1.x 全平台取消默认装；完全未装 `greenlet` 时 `create_async_engine()` 即抛 `ValueError: the greenlet library is required...`（`https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html`），而已装 `greenlet` 却在同步上下文触发隐式异步 IO 时抛 `MissingGreenlet` |
| 2 | 连接串 scheme 必须 **`postgresql+psycopg://`** | 同一个 `psycopg`（v3）方言自动按 `create_engine` / `create_async_engine` 分流 sync/async（无需 `+psycopg_async`，见 `https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg`）；误写 `+psycopg2` 会 `ModuleNotFoundError` 或切回同步驱动 |
| 3 | `create_async_engine(url, echo=False, pool_pre_ping=True)` | 异步引擎入口；`pool_pre_ping` 在连接检出前测活防容器重启死连接（事务中途断连仍需应用层重试；集成测试用 `poolclass=NullPool` 防跨 event loop 报错） |
| 4 | `async_sessionmaker(engine, expire_on_commit=False)` | 默认 `True` 会在 `commit()` 后使 ORM 属性过期，FastAPI 同步序列化响应时触发 `MissingGreenlet` |
| 5 | `class Base(AsyncAttrs, DeclarativeBase)` + `MetaData(naming_convention=...)` | `AsyncAttrs` 提供 `await obj.awaitable_attrs.rel`；命名约定使 `ck_*`/`fk_*`/`uq_*`/`ix_*` 确定可断言（`23505`/`23514`），并保证 `drop_all()` 自动拓扑解环不报 `CircularDependencyError` |
| 6 | Alembic `env.py` 硬纪律（`alembic init -t async`） | `target_metadata` 必须导入全部 17 个 models；`async_engine_from_config(..., poolclass=NullPool)` + `await connection.run_sync(do_run_migrations)` + `await connectable.dispose()`（`https://alembic.sqlalchemy.org/en/latest/cookbook.html`）+ `compare_type=True` + `compare_server_default=True` |

#### 明确**不引入**的依赖清单及理由

| 不引入 | 替代方案 | 理由 |
|---|---|---|
| `celery` / `arq` / `rq` | `asyncio.Task` + `notification_tasks` 表 + 单进程 worker | 引入 Redis/RabbitMQ 会多 3 个容器；DB 表驱动重试可持久化且跨重启恢复 |
| `redis` | `audit_logs.idempotency_key` + 部分唯一索引 | 无需外部 KV 存储 |
| `MinIO` 等对象存储服务 | 本地 `UPLOAD_DIR=./uploads/` 磁盘目录 + `sha256` 去重落盘 | 个人单机单体项目，鉴权流经 `GET /evidence/{id}/raw`，无需额外对象存储容器 |
| `postgis` | `boundary_geojson jsonb` | 派单判定仅用 `region_code` 相等，不用不可复现的几何相交 |
| `asyncpg` | `psycopg[binary]` v3 | 用户裁决采用 v3 统一同步 Alembic 与异步运行时，不混用两套 PG 驱动 |
| `passlib` | `pwdlib[argon2]` | 长期无更新，bcrypt 4.x 存在兼容告警 |
| `structlog` | 标准 `logging` + `request_id` 过滤器 | 日志量级小，`request_id` 由中间件注入 |
| `sqlmodel` | 原生 SQLAlchemy 2.0 声明式 | SQLModel 无法表达本库 76 条 `CHECK` 与 6 个部分唯一索引 |
| `fastapi-users` | 手写 `auth` 模块 | 自带 `users` 表会破坏 17 表口径 |
| `orjson` / `tenacity` | 标准 `json` / DB 持久化 `attempt_count`+`next_retry_at` | 少 C 扩展；内存装饰器无法跨重启保留通知重试状态 |
| `@playwright/test`（Runner） | Python `pytest-playwright` | 保持全栈测试单 Runner，支持直接 `import` 生产服务做合法播种与同循环异步 DB 断言 |
| MQTT / IoT / RTSP / 时序预测库 | 无 | 硬红线：不接灯控设备、不接实时监控、不做能耗预测 |

---

### 1.3 前端技术栈与 8 条版本陷阱

#### 前端架构与精确版本表（🟢 2026-11-21 npm registry 实测，`D-23` `^` + `pnpm-lock.yaml`）

| 依赖 | 最新稳定版 | 本项目采用 | 关键字段（逐字实测） |
|---|---|---|---|
| `vue` | **3.5.46** | `^3.5.46` | `engines` 字段不存在（`jq 'has("engines")'` → `false`）；禁 3.6.0-rc |
| `element-plus` | **2.14.8** | `^2.14.8` | `peerDependencies: {"vue":"^3.3.7"}`；依赖含 `"@element-plus/icons-vue":"^2.3.2"` |
| `@element-plus/icons-vue` | **2.3.2** | `^2.3.2`（**显式声明**） | `deprecated` 字段不存在；pnpm 严格提升下禁作幽灵依赖 |
| `vite` | **8.3.8** | `^8.3.8` | `engines:{"node":"^20.19.0 \|\| >=22.12.0"}` |
| `@vitejs/plugin-vue` | **6.0.11** | `^6.0.11` | `peerDependencies:{"vite":"^5 \|\| ^6 \|\| ^7 \|\| ^8"}` → 兼容 Vite 8 ✅ |
| `pinia` | **4.1.0**（latest） | 🔴 **禁 v4，用 `^3.0.4`** | `peerDependencies:{"vue":"^3.5.11"}`（setup store 风格） |
| `vue-router` | **5.5.1**（latest） | 🔴 **禁 v5，用 `^4.6.5`** | dist-tags `"v4-latest":"4.6.5"`；v5 peer 要 `vue:^3.5.34` |
| `typescript` | **7.0.2**（latest） | 🔴 **禁 7.x，锁 `6.0.3`** | TS7 导致 `vue-tsc` 直接崩溃（严格 TS，禁止 `any`） |
| `vue-tsc` | **3.3.14** | `^3.3.14` | `peerDependencies:{"typescript":">=5.0.0"}`（该上界实为假） |
| `@volar/typescript`（传递依赖） | 2.4.31 | （由 `vue-tsc` 锁 `2.4.30`） | `peerDependencies:{"typescript":"<7"}` ← 真实上界 |
| `pnpm` | **12.21.0**（latest） | `11.1.1`（实测环境） | dist-tags `"latest-11":"11.28.12"` |
| `@playwright/test` | **1.65.1** | 仅 npm 版本记录 | 本项目 E2E 统一使用 Python `pytest-playwright`（见 §6.1） |

#### 8 条已核实前端版本陷阱

| # | 陷阱 | 后果 | 规避 | 等级 |
|---:|---|---|---|---|
| **1** | 🔴 `typescript@latest` = `7.0.2` 让 `vue-tsc` 崩溃 | 报错 `ERR_PACKAGE_PATH_NOT_EXPORTED: './lib/tsc'`（TS7 删除该导出） | **锁 `typescript@6.0.3`** | 🟢 实测崩溃 |
| **2** | 🔴 Element Plus `data-testid` 透传分**三类**（非两类） | C 类**静默丢弃**，Vue/TS/`vue-tsc` 全不报错，E2E 运行时才炸 | 见下方摘要与 `docs/API.md` §8.4 | 🟢 源码 + jsdom |
| **3** | `element-plus` 的 `peer vue: ^3.3.7` 是假下限 | 按 `^3.3.7` 装会与 pinia / vue-router 冲突 | 真实交集下限 **`>=3.5.34`** | 🟢 实测 peer |
| **4** | `vue-router` `latest` 是 5.x 不是 4.x | 直接 `pnpm add` 装到 v5，大版本 API 差异 | 显式 `^4.6.5` | 🟢 实测 dist-tags |
| **5** | `pinia` `latest` 是 4.x | 误装到 v4 | 显式 `^3.0.4` | 🟢 实测 |
| **6** | `@element-plus/icons-vue` 15 个月未发新版 | 若靠传递依赖引入，pnpm 严格提升下成幽灵依赖 | 显式写入 `dependencies` | 🟢 实测 |
| **7** | `vue` 包无 `engines` 字段 | npm 元数据查不到 Node 下限 | 以 Vite 与 `create-vue@3.24.0` 要求为准：`^22.18.0 \|\| >=24.12.0` | 🟢 实测 |
| **8** | `vue` 3.6.0-rc.16 已存在但 `latest` 为 3.5.46 | 误装 rc 版引入不稳定行为 | 禁用 rc，锁 `3.5.46` 稳定线 | 🟢 实测 |

#### Element Plus `data-testid` 三类透传行为摘要（详见 `docs/API.md` §8.4）

- **A 类（落内层原生控件）**：`el-input`、`el-autocomplete`、`el-button` —— 经 `element-plus@2.14.8` `input.vue` L240/L36 + `use-attrs/index.ts` L12 源码级与 jsdom 运行时确证（`D-34` 推翻原误判），Python `pytest-playwright` **直接 `page.get_by_test_id("x").fill(...)`（JS 对应 `getByTestId('x').fill()`），禁止加 `.locator("input")`**。
- **B 类（落根包装元素）**：`el-checkbox`/`el-radio`→`label`；`el-select`/`el-table`/`el-pagination`/`el-dialog`/`el-form-item`/`el-switch`/`el-input-number`/`el-tabs`/`el-dropdown`/`el-color-picker`→`div` —— 需 `.locator("input")` 下钻；对经 Vue 原生 `<teleport>`（Element Plus `2.14.0+` PR `#23219` 已移除 `el-teleport`，`2.14.8` `tooltip/src/content.vue` 改用 `<teleport :disabled="!teleported" :to="appendTo">`）默认挂载至 `document.body`（`#el-popper-container-*`）的下拉/浮层，`page.get_by_test_id(...)` / `page.get_by_role(...)` 扫描整页 DOM 可直接穿透命中，仅当使用父容器链式 `container.locator(...)` 作用域定位时才受子树限制需配 `:teleported="false"`（注：门禁复选框按约定直接用原生 `<input type="checkbox">`）。
- **C 类（静默丢弃，0 命中）**：`el-tooltip`、`el-popover`、`el-date-picker`、`el-time-picker`、`el-cascader` —— `popper.vue` 全文仅 `<slot />` + `inheritAttrs: false`，**必须外包 `<div data-testid>`**。
- **源码补核与首日实测项**：`element-plus@2.14.8` `select/src/option.vue` 经核实为单根 `<li role="option">`（未设 `inheritAttrs: false`），按 Vue 3 规则可承接 `data-testid`；但真实 Chromium 下的 `fill()`/`check()`/`Teleport` 交互及 `el-option`/`el-upload` 仍列为阶段二首日必验项（`D-20`/`D-21`/`D-40`，见 §6.1）。

---

### 1.4 取证通道与诚实记录

> 🟡/🔴 **Context7 MCP 在本环境间歇性不稳定（不可作为唯一取证通道，两类异常实况并列留存）**：
> - **间歇性网络故障与同库调用波动**：此前 `docs-writer` 与 `task-44` 联网核验曾多次连续遭遇 `resolve-library-id` / `query-docs` 返回 `Error: Error searching libraries: TypeError: fetch failed`（含连续 4 次与 6 次批次，以及 `task-52` 复测 `PostgreSQL` 再次报错），但在 `task-51` 审计抽验中观测到 `6/8` 个库查询成功、且同一库不同次调用成败不一（`6` 成功 / `2` 失败）；
> - **局部索引陈旧**（`task-13` 独立复核 2 次）：`resolve-library-id("Vue")` 仅返回 `/vuejs/vue`（Versions 止于 `v2.7.16`，无 Vue 3）并输出 `"Vue 2 has reached End of Life"`。
> 因此定性为**间歇性不稳定，严禁作为唯一取证源，必须与 Exa 官方直取 / 锁定版本源码交叉核验**；`task-44` 与 `task-52` 交叉检验均以 Exa 官方文档直取及锁定版本源码原文为权威依据，逐条记录于 `.team/notes/research/merge-online-cross-check.md`。

全部技术版本、源码行为与法规原文均通过以下一手通道取证并标明出处（遵守 `F14`）：
- **官方文档直取（`task-44` 交叉核验）**：FastAPI（`fastapi.tiangolo.com`）、SQLAlchemy 2.0（`docs.sqlalchemy.org/en/20/`）、psycopg 3（`psycopg.org/psycopg3/docs/`）、Alembic（`alembic.sqlalchemy.org`）、PostgreSQL 17（`postgresql.org/docs/17/` §5.5.1/§5.5.3/§5.5.5/`indexes-partial`）、Ultralytics（`docs.ultralytics.com/models/yolo26/`）、ONNX Runtime（`onnxruntime.ai/docs/`）、OpenCV（`docs.opencv.org`）、Vue 3.5（`vuejs.org`）、Playwright Python（`playwright.dev/python/docs/locators`）
- **PyPI JSON API / npm Registry**：`curl https://pypi.org/pypi/<pkg>/json`、`curl https://registry.npmjs.org/<pkg>/latest`
- **GitHub raw 源码 / Releases**：Element Plus `2.14.8` 源码、Ultralytics `v8.4.216` tag 与 `v8.4.0` 权重资产、Alembic issues #326/#387/#626
- **政府规章与官方标准原文**：郑州市人民政府规章库《郑州市城市照明管理办法》全文网页、住房和城乡建设部规章原文、`T/CMEA 21—2021` 标准 PDF

---

## 2. 状态机设计（4 套状态机 / 29 状态 / 36 转移规则条目·按 `(from,to)` 图边计 `47` / 11 终态 / 0 死路）

> **口径说明（边数两口径，与 `src/tomori/services/state_machine.py` 一致）**：本节各转移表按**转移规则条目**编号计数（`A15 + B6 + C8 + D7 = 36`——每条「守卫 + 触发者 + 审计 `action`」为一个编号逻辑规则）；同一代码映射按 `(from, to)` 字面图边展开为 **47**（`A16 + B8 + C14 + D9`），因部分规则条目覆盖多条字面边——A8「拒单或累计阈值转复核」为 `DISPATCHED → {PENDING_DISPATCH, MANUAL_REVIEW}` `2` 条边（`A 15 → 16`）；`PENDING_ACCEPT → {ACCEPTED, REJECTED, TIMEOUT_REASSIGNED, ESCALATED, CANCELLED}` + `ACCEPTED → {COMPLETED, ESCALATED, CANCELLED}` 合计 `8` 边（`B 6 → 8`）；C 各守卫覆盖 `14` 边（`C 8 → 14`）；`PROCESSING → {SENT, FAILED, PENDING, DEAD_LETTER}` 等合计 `9` 边（`D 7 → 9`）。各表编号行合计 `42` = `36` 规则条目 + `6` 条初始创建入口行（`— → 初始态`：A `2` 行 + B `1` 行 + C `2` 行 + D `1` 行）。下文各机器标题同时标注两口径。

### 2.1 状态机 A：故障单主状态机（`fault_tickets.status`，9 态 / `15` 转移规则条目·`16` `(from,to)` 图边 / 2 终态）

#### 状态全集与终态

- **$S = 9$ 个状态**：`AI_CANDIDATE_PENDING` / `PENDING_DISPATCH` / `DISPATCHED` / `IN_REPAIR` / `PENDING_ACCEPTANCE` / `REWORK_PENDING_DISPATCH` / `MANUAL_REVIEW` / `CLOSED` / `CANCELLED`
- **$T$（终态）= 2 个**：`CLOSED` / `CANCELLED`。**终态不可复活**，再修必须另开新单（旧单永久留档）。

#### 全量转移边 $E$（`17` 行编号 = `2` 条入口行 + `15` 个转移规则条目；按字面 `(from,to)` 展开 `16` 图边——`A8` 单条规则覆盖 `DISPATCHED → {PENDING_DISPATCH, MANUAL_REVIEW}` 两条边）

| # | from → to | 触发者 | 端点 | 守卫（不满足即拒绝） | 审计 `action` |
|---:|---|---|---|---|---|
| **A1** | （无）→ `AI_CANDIDATE_PENDING` | INSPECTOR / ADMIN | `POST /tickets/ai-candidates` | 静态图片四层白名单通过；视觉返回 ≥1 个 `is_fault_candidate=true`；同点位同类无活跃单（否则 `409 DUPLICATE_ACTIVE_TICKET`） | `AI_CANDIDATE_GENERATED` |
| **A2** | （无）→ `PENDING_DISPATCH` | INSPECTOR / ADMIN | `POST /tickets` | 纯人工报障，`confirmed_by_inspector=true` 直接成立；同事务按 `grading_rules` 分级并写入 `repair_deadline_at`（+48h）；同点位同类无活跃单 | `TICKET_CREATED` + `TICKET_GRADED` |
| **A3** | `AI_CANDIDATE_PENDING` → `PENDING_DISPATCH` | INSPECTOR（区域 `INSPECT`） | `POST /tickets/{id}/confirm` | 可改 `fault_type`/`priority`/标题/`safety_flag`；**确认后按 `grading_rules` 自动分级**写 `priority` + `grading_snapshot_json`（`grading_score=NULL`）+ 法定 48h `repair_deadline_at`（`response_deadline_at` 默认 `NULL`）；改为视觉不产出的 `FLICKERING`/`ELECTRIC_HAZARD` **必须带 `manual_note`** | `TICKET_CONFIRMED` + `TICKET_GRADED` |
| **A4** | `AI_CANDIDATE_PENDING` → `CANCELLED` | INSPECTOR（区域 `INSPECT`） | `POST /tickets/{id}/cancel` | 巡查员否定 AI 候选（误报）；`cancel_reason` 必填 | `TICKET_CANCELLED` |
| **A5** | `PENDING_DISPATCH` → `CANCELLED` | INSPECTOR（本人报障）/ ADMIN | `POST /tickets/{id}/cancel` | **仅派工前可撤销**；`cancel_reason` 必填；撤销后释放点位防重锁 | `TICKET_CANCELLED` |
| **A6** | `PENDING_DISPATCH` → `DISPATCHED` | ADMIN（区域 `MANAGE`） | `POST /tickets/{id}/dispatch` | ① `confirmed_by_inspector=true`（否则 `422 AI_CANDIDATE_NOT_CONFIRMED`）；② 目标维修员持区域有效 `REPAIR` 授权且账号 `ACTIVE`、角色 `TECHNICIAN`（否则 `422 DISPATCH_TECHNICIAN_NOT_ELIGIBLE`）；③ 在途数 < `max_active_dispatch`（否则 `422 TECHNICIAN_OVERLOADED`）；④ 同票无活跃派工（部分唯一索引兜底）；同事务插入 `dispatch_records` | `TICKET_DISPATCHED` |
| **A7** | `DISPATCHED` → `IN_REPAIR` | TECHNICIAN（受派人） | `POST /dispatches/{id}/accept` | 派工记录 `status=PENDING_ACCEPT` 且未过 `accept_deadline_at`（过期走 SLA 扫描）；`accepted_at=now()` | `DISPATCH_ACCEPTED` |
| **A8** | `DISPATCHED` → `PENDING_DISPATCH` | TECHNICIAN（受派人） | `POST /dispatches/{id}/reject` | `reject_reason` 必填；`dispatch_reject_count += 1`；派工置 `REJECTED`（退出活跃索引）；**若累计拒单 ≥ 阈值（默认 3）→ 反而转 `MANUAL_REVIEW`** | `DISPATCH_REJECTED` |
| **A9** | `IN_REPAIR` → `PENDING_ACCEPTANCE` | TECHNICIAN（受派人） | `POST /dispatches/{id}/complete` | ① `electrical_safety_checked=true` 且 `safety_checklist_json` 5 项全 `true` + `safety_note` 非空（否则 `422 ELECTRICAL_SAFETY_CHECK_REQUIRED`）；② `repair_summary` 非空；③ 同事务写 `repair_processes` + `material_usages[]` | `REPAIR_COMPLETED` |
| **A10** | `IN_REPAIR` → `MANUAL_REVIEW` | TECHNICIAN（受派人） | `POST /dispatches/{id}/escalate` | `escalate_reason` 必填（无法安全作业 / 缺件 / 超出技能）；派工置 `ESCALATED` | `DISPATCH_ESCALATED` |
| **A11** | `PENDING_ACCEPTANCE` → `CLOSED` | INSPECTOR（区域 `INSPECT`） | `POST /tickets/{id}/acceptance`（`decision=APPROVED`） | 写 `acceptance_records(APPROVED)`；`closed_at=now()`；若有进行中返修单则同事务置 `PASSED`；发 `TICKET_CLOSED` 通知 | `ACCEPTANCE_APPROVED` |
| **A12** | `PENDING_ACCEPTANCE` → `REWORK_PENDING_DISPATCH` | INSPECTOR（区域 `INSPECT`） | `POST /tickets/{id}/acceptance`（`decision=REJECTED`） | ① `comment` 必填；② **单事务四件事**：写 `acceptance_records(REJECTED)` + `rework_count += 1` + 插入 `rework_orders(round_no=rework_count)` + 置票状态；③ `round_no <= max_rework_rounds`（默认 2）走本边，**超阈值走 A13** | `ACCEPTANCE_REJECTED` + `REWORK_CREATED` |
| **A13** | `PENDING_ACCEPTANCE` → `MANUAL_REVIEW` | INSPECTOR（区域 `INSPECT`） | 同上，`decision=REJECTED` | `round_no > max_rework_rounds`：返修单置 `requires_manual_review=true`、`status=IN_MANUAL_REVIEW`；票置 `MANUAL_REVIEW` + `manual_review_reason`（DB `CHECK` 强制非空）；通知全部 ADMIN | `ACCEPTANCE_REJECTED` + `REWORK_CREATED` + `REWORK_MANUAL_REVIEW_REQUIRED` |
| **A14** | `REWORK_PENDING_DISPATCH` → `DISPATCHED` | ADMIN（区域 `MANAGE`） | `POST /tickets/{id}/dispatch` | 同 A6 守卫，另需：存在该票活跃 `rework_orders`（`uq_rework_orders_active_ticket`）且 `requires_manual_review=false`；新 `dispatch_records.rework_order_id` 指向它 | `TICKET_DISPATCHED` |
| **A15** | `MANUAL_REVIEW` → `PENDING_DISPATCH` | ADMIN | `POST /tickets/{id}/review-resolve`（`resolution=APPROVE_REASSIGN`） | `reason` 必填；若存在 `IN_MANUAL_REVIEW` 返修单（经 `A13` 进入），按 `C9` 将其置终态 `CLOSED_BY_REVIEW` + `manual_review_decision=APPROVE_REASSIGN`（`decided_by/at`、`closed_at` 必填）；因该返修单已进入终态并退出活跃索引（`A14` 所需活跃 `requires_manual_review=false` 返修单不存在），票统一回落 `PENDING_DISPATCH`（由 `A6` 重新派工）并清空 `manual_review_reason` | `MANUAL_REVIEW_RESOLVED` |
| **A16** | `MANUAL_REVIEW` → `CLOSED` | ADMIN | 同上（`resolution=SPECIAL_CLOSE`） | `reason` 必填且长度 ≥ 10（第三方已处置 / 点位已撤除 / 督促留证闭环）；若存在 `IN_MANUAL_REVIEW` 返修单则置 `CLOSED_BY_REVIEW`（`manual_review_decision=SPECIAL_CLOSE`）；`closed_at=now()` | `MANUAL_REVIEW_RESOLVED` |
| **A17** | `MANUAL_REVIEW` → `CANCELLED` | ADMIN | 同上（`resolution=TERMINATE_CANCEL`） | `reason` 必填（写入 `cancel_reason`）；返修单（若存在活跃返修单）置 `CANCELLED`；释放点位防重锁 | `MANUAL_REVIEW_RESOLVED` + `TICKET_CANCELLED` |

#### 9 态无死路证明

| 状态 | 出边 | 可达终态 | 自动路径 | 人工兜底入口 |
|---|---|---|---|---|
| `AI_CANDIDATE_PENDING` | A3 / A4 | ✅ 直连 | TTL 清理任务（`ai_candidate_ttl_hours`，`D-15` 默认 72h）自动置 `CANCELLED` + `AI_CANDIDATE_EXPIRED` 通知 | 巡查员 `cancel` |
| `PENDING_DISPATCH` | A5 / A6 | ✅ 直连 | SLA 扫描：满法定 48h（`repair_deadline_at < now()`）标记 `is_sla_overdue=true` + 通知（**不改状态**） | 管理员 `dispatch` / `cancel` |
| `DISPATCHED` | A7 / A8 | ✅ 经 A7→A9→A11 | SLA 扫描：① 派工接单超时（`accept_deadline_at < now()`，工程防死锁）→ 派工 `TIMEOUT_REASSIGNED` + 票回 `PENDING_DISPATCH`；② 满法定 48h → 标记超时 + 通知 | 管理员 `reassign` |
| `IN_REPAIR` | A9 / A10 | ✅ | SLA 扫描：满法定 48h 标记 `overdue_stage='REPAIR'` + 通知（**不改状态**） | 维修员 `escalate`；管理员 `reassign` |
| `PENDING_ACCEPTANCE` | A11 / A12 / A13 | ✅ 直连 | 无自动（红线：机器不得替代巡查员验收） | 巡查员 `acceptance` |
| `REWORK_PENDING_DISPATCH` | A14 | ✅ 经 A14→A7→A9→A11 | SLA 扫描：满法定 48h 标记 `overdue_stage='REPAIR'` + 通知 | 管理员 `dispatch` |
| `MANUAL_REVIEW` | A15 / A16 / A17 | ✅ 两直连终态 | 24h 未处置发催办通知（最多 3 次进死信 → ADMIN 可见）；满法定 48h 同样标记超时 | 管理员 `review-resolve` |
| `CLOSED` | — 终态 | — | — | 只读 |
| `CANCELLED` | — 终态 | — | — | 只读 |

#### 明确禁止转移黑名单（越界即 `422 INVALID_STATE_TRANSITION`）

```text
PENDING_DISPATCH   → CLOSED                 （禁跳过维修与验收直接关单）
PENDING_DISPATCH   → PENDING_ACCEPTANCE     （禁跳过维修）
DISPATCHED         → PENDING_ACCEPTANCE     （禁跳过接单）
DISPATCHED         → CLOSED
IN_REPAIR          → CLOSED
IN_REPAIR          → REWORK_PENDING_DISPATCH （★ MUT-09：返修只能由验收驳回触发）
PENDING_ACCEPTANCE → PENDING_DISPATCH
CLOSED             → *                      （终态不可复活）
CANCELLED          → *                      （终态不可复活）
*                  → AI_CANDIDATE_PENDING   （只能作为 AI 候选初始态）
```

> **`IN_REPAIR → REWORK_PENDING_DISPATCH` 禁令理由（`MUT-09` 守卫）**：若允许维修员直接将自己推进返修，`rework_count` 就失去「验收被驳回次数」的唯一语义，导致超阈值强复核机制立刻失效；维修员修不好的唯一合法退出口是 `escalate`（A10）。

---

### 2.2 状态机 B：派工记录（`dispatch_records.status`，7 态 / `6` 转移规则条目·`8` `(from,to)` 图边 / 5 终态）

#### 转移边（`7` 行编号 = `1` 条入口行 + `6` 个转移规则条目；按字面 `(from,to)` 展开 `8` 图边——`PENDING_ACCEPT` 出 `5` 边 + `ACCEPTED` 出 `3` 边）

| # | from → to | 触发者 | 端点 | 守卫 |
|---:|---|---|---|---|
| **B1** | （无）→ `PENDING_ACCEPT` | ADMIN | `/tickets/{id}/dispatch`、`/reassign` | 见 A6/A14；同票无活跃派工（`uq_dispatch_records_active_ticket`） |
| **B2** | `PENDING_ACCEPT` → `ACCEPTED` | TECHNICIAN（受派人） | `/dispatches/{id}/accept` | 未过 `accept_deadline_at`；`accepted_at=now()` |
| **B3** | `PENDING_ACCEPT` → `REJECTED` | TECHNICIAN（受派人） | `/dispatches/{id}/reject` | `reject_reason` 必填；`rejected_at=now()` |
| **B4** | `PENDING_ACCEPT` → `TIMEOUT_REASSIGNED` | **系统**（SLA 扫描） | `POST /sla/scan` | `now() > accept_deadline_at`；`escalated_at=now()` |
| **B5** | `ACCEPTED` → `COMPLETED` | TECHNICIAN（受派人） | `/dispatches/{id}/complete` | 见 A9 三项守卫；`completed_at=now()` |
| **B6** | `ACCEPTED` → `ESCALATED` | TECHNICIAN（受派人） | `/dispatches/{id}/escalate` | `escalate_reason` 必填；`escalated_at=now()` |
| **B7** | `PENDING_ACCEPT` / `ACCEPTED` → `CANCELLED` | ADMIN | `/reassign` 内部；`/tickets/{id}/cancel` | 改派时旧派工置 `CANCELLED`（`cancelled_at=now()`）再插新派工；票撤销时无派工则跳过 |

- **终态 5 个**：`REJECTED` / `COMPLETED` / `TIMEOUT_REASSIGNED` / `ESCALATED` / `CANCELLED`（均不可复活，再派工新插一行）。
- **活跃态 2 个**：`PENDING_ACCEPT` / `ACCEPTED`（`uq_dispatch_records_active_ticket` 精确约束此 2 态）。
- **无死路证明**：`PENDING_ACCEPT` 有自动定时 B4 保证不卡死；`ACCEPTED` 有 B5/B6 与管理员 `reassign`（B7）兜底；5 个终态只读。独立状态机保留「谁拒的、为何拒、拒几次」的完整审计链。

---

### 2.3 状态机 C：返修单（`rework_orders.status`，8 态 / `8` 转移规则条目·`14` `(from,to)` 图边 / 3 终态）

#### 转移边（`10` 行编号 = `2` 条入口行 + `8` 个转移规则条目；按字面 `(from,to)` 展开 `14` 图边——`DISPATCHED`/`IN_REPAIR`/`PENDING_ACCEPTANCE` 各含终止/升级多目标边）

| # | from → to | 触发者 | 守卫 |
|---:|---|---|---|
| **C1** | （无）→ `PENDING_DISPATCH` | INSPECTOR（验收驳回） | `round_no = rework_count(new) <= max_rework_rounds`；`reject_reason` 快照自 `acceptance_records.comment`；同票无活跃返修单 |
| **C2** | （无）→ `IN_MANUAL_REVIEW` | INSPECTOR（验收驳回，超阈值） | `round_no > max_rework_rounds`；`requires_manual_review=true`；票同事务置 `MANUAL_REVIEW` |
| **C3** | `PENDING_DISPATCH` → `DISPATCHED` | ADMIN | 新 `dispatch_records.rework_order_id` 指向本单 |
| **C4** | `DISPATCHED` → `IN_REPAIR` | TECHNICIAN | 同 B2 |
| **C5** | `IN_REPAIR` → `PENDING_ACCEPTANCE` | TECHNICIAN | 同 B5（含电气安全检查硬门禁） |
| **C6** | `PENDING_ACCEPTANCE` → `PASSED` | INSPECTOR（验收通过） | 同 A11；`closed_at=now()` |
| **C7** | `PENDING_ACCEPTANCE` → `IN_MANUAL_REVIEW` | INSPECTOR（再次驳回且超阈值） | `requires_manual_review=true` |
| **C8** | `PENDING_ACCEPTANCE` → `CANCELLED` | INSPECTOR（再次驳回，未超阈值） | 本单让位给下一轮新返修单（C1 插入 `round_no+1`）；`closed_at=now()` |
| **C9** | `IN_MANUAL_REVIEW` → `CLOSED_BY_REVIEW` | ADMIN | `manual_review_decision ∈ {APPROVE_REASSIGN, SPECIAL_CLOSE}`；`decided_by/at` 必填 |
| **C10** | `IN_MANUAL_REVIEW` → `CANCELLED` | ADMIN | `manual_review_decision = TERMINATE_CANCEL` |

- **终态 3 个**：`PASSED` / `CLOSED_BY_REVIEW` / `CANCELLED`
- **活跃态 5 个**：`PENDING_DISPATCH` / `IN_MANUAL_REVIEW` / `DISPATCHED` / `IN_REPAIR` / `PENDING_ACCEPTANCE`（`uq_rework_orders_active_ticket` 约束此 5 态）。
- **无死路证明**：`PENDING_DISPATCH` 由管理员派工（C3）推进；`DISPATCHED` / `IN_REPAIR` 与状态机 B 同步推进；`IN_MANUAL_REVIEW` 有 C9/C10 人工出口及 24h 催办提醒。

#### 「三轮返修」完整时序（超阈值确切行为）

```text
round=1：维修完成 → 巡查员驳回
         rework_orders#1 PENDING_DISPATCH，票 REWORK_PENDING_DISPATCH   （1 <= 2，正常返修）
round=2：派工 → 维修完成 → 巡查员驳回
         rework_orders#1 CANCELLED（转下一轮）
         rework_orders#2 PENDING_DISPATCH，票 REWORK_PENDING_DISPATCH   （2 <= 2，仍正常）
round=3：派工 → 维修完成 → 巡查员驳回（round=3 > max_rework_rounds(2)）
         rework_orders#2 CANCELLED
         rework_orders#3 IN_MANUAL_REVIEW + requires_manual_review=true
         票 MANUAL_REVIEW + manual_review_reason='rework_threshold_exceeded'
         通知全部 ADMIN（event_type=REWORK_MANUAL_REVIEW_REQUIRED）
```

> **设计要害**：超阈值时**仍然生成第 3 张返修单**（一出生即为 `IN_MANUAL_REVIEW`），保证 `rework_orders` 行数与 `fault_tickets.rework_count` 恒等（迁移期断言 4 守卫），驳回次数无需靠冗余列做唯一审计真相。

---

### 2.4 状态机 D：通知任务（`notification_tasks.status`，5 态 / `7` 转移规则条目·`9` `(from,to)` 图边 / 1 终态）

#### 转移边（`8` 行编号 = `1` 条入口行 + `7` 个转移规则条目；按字面 `(from,to)` 展开 `9` 图边——`PROCESSING` 出 `4` 边含自愈回退 `PENDING`）

| # | from → to | 触发者 | 守卫 |
|---:|---|---|---|
| **D1** | （无）→ `PENDING` | 业务事务内 | **与业务事件同事务插入**（禁止事后补救，防静默漏发） |
| **D2** | `PENDING` → `PROCESSING` | worker | `next_retry_at IS NULL OR next_retry_at <= now()`；`UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED)` 抢占 |
| **D3** | `PROCESSING` → `SENT` | worker | 投递成功；`sent_at=now()` |
| **D4** | `PROCESSING` → `FAILED` | worker | 投递失败；`attempt_count += 1`；`next_retry_at = now() + backoff(attempt_count)`（1m/5m/15m）；写 `last_error` |
| **D5** | `FAILED` → `PENDING` | worker（重试） | `attempt_count < max_attempts` |
| **D6** | `FAILED` → `DEAD_LETTER` | worker | `attempt_count >= max_attempts`；**同时给全部 ADMIN 发 `NOTIFICATION_DEAD_LETTER`** |
| **D7** | `DEAD_LETTER` → `PENDING` | ADMIN | `POST /notifications/{id}/retry`：`attempt_count=0`、清 `last_error`（死信自动重试恢复出口） |
| **D8** | `DEAD_LETTER` → `SENT` | ADMIN | `POST /notifications/{id}/acknowledge`：填 `acknowledged_by/at`（人工确认知晓；不改历史失败事实） |

#### `PROCESSING` 10 分钟卡死自愈规则（防 worker 崩溃死路）

```sql
-- updated_at 超过 10 分钟仍是 PROCESSING → 视为 worker 已死，退回 PENDING 并计一次 attempt
UPDATE notification_tasks
SET status = 'PENDING', attempt_count = attempt_count + 1, next_retry_at = now()
WHERE status = 'PROCESSING'
  AND updated_at < now() - interval '10 minutes'
  AND attempt_count < max_attempts;
```

#### 无死路证明与为什么 `SENT` 必须是终态

| 状态 | 出边 | 可达终态 | 自动路径 | 人工兜底入口 |
|---|---|---|---|---|
| `PENDING` | D2 `→ PROCESSING` | ✅ `SENT` | worker 轮询 | — |
| `PROCESSING` | D3 `→ SENT` / D4 `→ FAILED` | ✅ `SENT` | 10 分钟卡死自愈扫描 | — |
| `SENT` | **— 终态（吸收态）** | — | — | **只读** |
| `FAILED` | D5 `→ PENDING` / D6 `→ DEAD_LETTER` | ✅ `SENT`（经 D5） | 退避重试 1m / 5m / 15m | — |
| `DEAD_LETTER` | D7 `→ PENDING` / D8 `→ SENT` | ✅ `SENT` | 无自动路径（D6 已告警 ADMIN） | **D7 `retry` / D8 `acknowledge`** |

- **`SENT` 为什么必须是终态**：允许 `SENT → PENDING/FAILED` 会使同一事件重复通知、覆盖 `sent_at` 并导致 `audit_logs` 与投递事实矛盾。若业务需重发，应**新插一行** `notification_tasks`（原则与「终态不可复活，已发生事实只追加不改写」一致）。

---

## 3. 核心业务机制、跨表不变量与算法

### 3.1 跨表不变量（I1–I8）与防绕过实现纪律

| # | 不变量 | 违反后果 | 守它的机制 |
|---:|---|---|---|
| **I1** | `fault_tickets.status ∈ {DISPATCHED, IN_REPAIR}` ⟺ 恰有 **1** 条 `dispatch_records.status ∈ {PENDING_ACCEPT, ACCEPTED}` | 双人接同一单 / 无人接单却显示已派 | `uq_dispatch_records_active_ticket`（DB）+ 服务层同事务 |
| **I2** | `fault_tickets.status = PENDING_ACCEPTANCE` ⟺ 恰有 **1** 条 `repair_processes` 且 `electrical_safety_checked = true` | 跳过电气安全检查进验收 | `ck_repair_processes_safety_checklist_complete` + 服务层 `422` |
| **I3** | `fault_tickets.rework_count` = `count(rework_orders WHERE ticket_id=…)` | 返修阈值失效 | 同事务 `+1` + 迁移期断言 4 |
| **I4** | `fault_tickets.status = MANUAL_REVIEW` ⟹ `manual_review_reason IS NOT NULL` | 挂起原因丢失，无法处置 | `ck_fault_tickets_manual_review_reason_required` |
| **I5** | `dispatch_records.region_id = fault_tickets.region_id = lighting_points.region_id` | 区域统计与越权判定错位 | 服务层赋值 + 迁移期断言 1/2 |
| **I6** | 非终态票最多 **1** 条活跃返修单 | 多轮返修并发，轮次号错乱 | `uq_rework_orders_active_ticket` |
| **I7** | 每个状态变更必须有且仅有 **1** 条 `audit_logs`（`action` 与转移表一一对应） | 「谁改的」不可追溯 | `state_machine.py` `transition_*()` 统一强制写入 |
| **I8** | 每个状态变更必须产生对应 `notification_tasks`（`event_type` 19 种） | 当事人在系统外才得知派单 | `state_machine.py` 同事务 fan-out |

> **PG 跨表守卫边界与不用触发器原因**：PostgreSQL 官方文档明确 `CHECK` 只能引用当前行（不可含子查询/跨表引用）。本项目采用**双轨兜底**：① 单行内约束由 76 条 `CHECK` + 6 个部分唯一索引强制；② 跨表不变量 `I1`–`I8` 由 `src/tomori/services/state_machine.py` 的 `transition_*()` 配合 `SELECT ... FOR UPDATE` 串行化维护，配以事后验证查询（不用难以单测追踪的数据库触发器）。

---

### 3.2 机制一：区域授权派单、并发锁与排序权重

#### RBAC + `scope_authorizations` 双层模型与三处强校验

| 层 | 判定 | 失败码 |
|---|---|---|
| 第 1 层 账号状态 | `accounts.status == 'ACTIVE'` | `403 ACCOUNT_DISABLED` |
| 第 2 层 RBAC | 角色（`ADMIN` / `INSPECTOR` / `TECHNICIAN`）是否允许动作 | `403 FORBIDDEN_ROLE` |
| 第 3 层 区域授权 | `scope_authorizations`（`scope_type` ∈ `INSPECT`/`REPAIR`/`MANAGE`，`is_active=true`，`valid_from <= now()`，`valid_to IS NULL OR valid_to > now()`） | `403 FORBIDDEN_REGION_SCOPE` |

**三处强校验（缺一即为假门禁）**：
1. **推荐列表** `GET /tickets/{id}/dispatch-candidates`：五道硬过滤（区域匹配 + `REPAIR` 授权 + 有效期 + `ACTIVE` 账号 + `TECHNICIAN` 角色）+ 超载剔除；不合格者进入 `excluded[]` 并附 `reason`。
2. **手动指派** `POST /tickets/{id}/dispatch`：后端不信任前端传入列表，服务层重新校验全部条件，并用 `FOR UPDATE` 锁定工单行 + `uq_dispatch_records_active_ticket` 防止并发双派。
3. **接单** `POST /dispatches/{id}/accept`：接单时再次校验授权（防授权在派工后被吊销或过期）。

#### `403` vs `422` 判定分工与 `excluded[].reason` 五值枚举

| 情形 | HTTP | `error.code` | 不合格主体 |
|---|---:|---|---|
| 操作人无角色 / 无该区域授权 / 账号被停用 | `403` | `FORBIDDEN_ROLE` / `FORBIDDEN_REGION_SCOPE` / `ACCOUNT_DISABLED` | 请求者本人 |
| 目标维修员不合格（跨区/过期/停用/错角） | `422` | `DISPATCH_TECHNICIAN_NOT_ELIGIBLE`（`details.reason` 区分五因） | 被指派目标维修员 |
| 目标维修员在途工单已满（`active_count >= max_active_dispatch`） | `422` | `TECHNICIAN_OVERLOADED`（推荐列表 `excluded[].reason='OVERLOADED'`） | 被指派目标维修员 |

| `reason` 五值枚举 | 含义 | 下一步动作 |
|---|---|---|
| `NO_REGION_SCOPE` | 该维修人员未获此区域的 `REPAIR` 授权 | 补授权或换人 |
| `SCOPE_EXPIRED` | 授权存在但不在有效期内 | 延期授权或换人 |
| `ACCOUNT_INACTIVE` | 账号非 `ACTIVE` | 恢复账号或换人 |
| `WRONG_ROLE` | 账号不具备 `TECHNICIAN` 角色 | 调整角色或换人 |
| `OVERLOADED` | 在途工单数 ≥ `max_active_dispatch`（`D-16` 默认 **3**，DB 范围 `1..20`） | 换人或调高上限 |

#### 内部启发式推荐排序算法与并发锁

- **并发锁**：派工与改派事务内对 `fault_tickets` 加 `SELECT ... FOR UPDATE`，校验 `status IN ('PENDING_DISPATCH','REWORK_PENDING_DISPATCH')`（派工）或 `DISPATCHED`/`IN_REPAIR`（改派），并由 `uq_dispatch_records_active_ticket` 兜底「同票最多一条活跃派工」。
- **负载与技能**：
  - `active_count`：统计 `dispatch_records` 中 `status IN ('PENDING_ACCEPT','ACCEPTED')` 行数；`active_count >= max_active_dispatch` 硬剔除。
  - `workload_score = 1 - active_count / max_active_dispatch ∈ (0, 1]`。
  - `skill_score = 1.0 if fault_type ∈ skills else 0.0`（SQL：`a.skills @> ARRAY[:fault_type]::text[]`）。
- **历史准时率（相对法定 48h `repair_deadline_at`，无历史取中性 `0.5`）**：
  ```sql
  SELECT coalesce(
    1.0 - (count(*) FILTER (WHERE d.completed_at > t.repair_deadline_at))::numeric
          / nullif(count(*), 0),
    0.5
  ) AS on_time_rate
  FROM dispatch_records d JOIN fault_tickets t ON t.id = d.ticket_id
  WHERE d.technician_account_id = :technician_account_id AND d.status = 'COMPLETED';
  ```
  > 无历史时取 `0.5`（非 `1.0`），避免把所有新单推给零记录新人；超期分母采用法定 48h `repair_deadline_at`（不编造 2h/8h/24h/72h）。
- **加权公式与并列确定性兜底**：
  - `total = w_workload * workload_score + w_skill * skill_score + w_perf * on_time_rate ∈ [0, 1]`
  - 默认 `dispatch_scoring_weights = (0.40, 0.35, 0.25)`，`max_recommendations = 10`（服务端封顶），三权重之和必须为 `1.0`（Pydantic 校验，否则 `422 INVALID_RULE_PAYLOAD`）。
  - **性质边界**：本公式仅用于内部候选人排序推荐（管理员可在合格池内任选），不影响法定 48h 时限，也不是 `T/CMEA 21—2021` 附录 B/D 考核权重。
  - **三级并列裁决**（保证同输入同输出）：`active_count` 更少优先 → `MAX(dispatched_at)` 更早优先（无记录最早）→ `accounts.id` 更小优先。
- **`accept_deadline_at` 防死锁默认取值**：在 `sla_policy.response_minutes=null` 下，**默认取所属票 `fault_tickets.repair_deadline_at`**（不另造无依据分钟数；若日后接入正式响应时限规范再取 `min(dispatched_at + response_minutes, ticket.repair_deadline_at)`）。派工时必须把返回的 `scope_authorization_id` 写入 `dispatch_records`，并把完整候选快照写入 `candidate_snapshot_json`。

---

### 3.3 机制二：表驱动确定性故障分级（`grading_rules`）与郑州 48h 法定 SLA

#### 唯一法源定案（`D-11` / `D-12` / `D-13`）

- **唯一强制适用的郑州市法规**：《郑州市城市照明管理办法》（2006 年市政府令第 152 号公布；2018 年第 229 号、2020 年第 237 号第二次修正，现行文本；出处：<https://public.zhengzhou.gov.cn/guizhang/6085704.jhtml>）：
  - **第 19 条第 3 款**：「市城市照明管理机构在对城市照明设施**日常监督检查中发现**城市照明设施损坏、缺失或需要维修养护的，应当在 **48 小时之内维修养护或督促有关单位维修养护**。」→ **唯一法定时限 = `48` 小时（`2880` 分钟）**。
  - **第 24 条第 2 款 & 住建部令第 4 号第 29 条**：严重危及安全运行时应当**立即保护现场、采取紧急措施处理**，并在 **24 小时**内告知相关部门补办手续（**24h 是行政手续义务，不是修复时限**）→ 对 `fault_type == 'ELECTRIC_HAZARD'` 或 `safety_flag == true` 的票置 `"emergency_action_required": true`，不编造分钟级响应时限。
  - **第 22 条**（不低于 97% 亮灯率）：属行政考核口径，`D-17` 已裁定不入本系统。
- **行业背景对照（不采用其数字）**：`T/CMEA 21—2021` §4.4.5（24h/2h+48h/5天）与附录 B/D（整体考核评分权重）、深圳市政府令第 309 号、`DB11/T 1876-2021` 仅作行业背景对照——郑州管理处虽系参编单位但未检索到正式采纳文件，**严禁将团体标准或外地数字充当郑州强制规定，严禁将考核权重搬用作工单分级权重**。
- **`level_code` 与 `display_name` 必须分离（`D-12`）**：`priority`（`P1`/`P2`/`P3`/`P4`）借用国际 IT/运维实践命名，作为全库枚举、`business_rules` 键名与前端 `data-priority` 的不可改契约；`display_name` 终局定稿为 **`特急` / `加急` / `普通` / `观察`**，管理员可在业务规则页调整显示文案而不影响代码或 E2E 断言。`P1`–`P4` 仅决定派工队列先后顺序，**`P4` 观察同样受法定 48h 义务约束，不得免除**（`MUT-17`）。

#### `sla_policy` 法定配置载荷（已废除四档虚构分钟数）

```jsonc
// business_rules['sla_policy'] 出厂默认载荷（D-11 锁死）
{
  "statutory_deadline_hours": 48,
  "response_minutes": null,
  "emergency_notice_hours": 24,
  "applies_to_priorities": ["P1", "P2", "P3", "P4"]
}
```

#### 表驱动确定性规则分级（`grading_rules`，废除魔数权重与 `100×100` Bug）

- **四输入属性**（全部来自已落库字段）：`fault_type`（6 值）、`road_level`（3 值：`EXPRESS`/`MAIN`/`BRANCH`）、`asset_importance`（4 值：`CRITICAL`/`HIGH`/`NORMAL`/**`null`** —— `facility_asset_id IS NULL` 时即为 `null`，不虚构 `35` 分）、`safety_flag`（`bool`，2 值）。
- **求值规则（`R1`–`R4`，从高到低依次求值，命中首个分支即返回）**：

| 顺序 | `rule_id` | 命中条件（`any` 阵内为 `OR`，同对象内多字段为 `AND`） | 输出优先级 | 业务含义 |
|---:|---|---|---|---|
| 1 | `R1_SAFETY_OR_EXPRESS_CRITICAL` | `fault_type == 'ELECTRIC_HAZARD'` **OR** `safety_flag == true` **OR** (`road_level == 'EXPRESS'` **AND** `asset_importance IN ('CRITICAL','HIGH')`) | **`P1`**（特急） | 电气/公共安全风险或快速路重点资产故障（安全分支置 `emergency_action_required=true`） |
| 2 | `R2_EXPRESS_OR_MAIN_CORE_OR_CRITICAL_ASSET` | `road_level == 'EXPRESS'` **OR** (`road_level == 'MAIN'` **AND** `fault_type IN ('FIXTURE_DAMAGED','LAMP_OUT')`) **OR** `asset_importance IN ('CRITICAL','HIGH')` | **`P2`**（加急） | 快速路其余故障、主干道破损/灭灯、其余关键/重要资产故障 |
| 3 | `R3_BRANCH_OR_MAIN_MINOR` | (`road_level == 'BRANCH'` **AND** `fault_type IN ('LAMP_OUT','OCCLUDED','FLICKERING','FIXTURE_DAMAGED')`) **OR** (`road_level == 'MAIN'` **AND** `fault_type IN ('OCCLUDED','FLICKERING','OTHER')`) | **`P3`**（普通） | 支路常规功能故障或主干道非紧急遮挡/频闪/其他故障 |
| 4 | `R4_FALLBACK_OBSERVATION` | `default_priority: "P4"` 兜底（其余情形，如支路 `OTHER` + `NORMAL`/`null` + `safety_flag=false`） | **`P4`**（观察） | 其余轻微待观察异常（仅排派靠后，仍受 48h 约束） |

- **数学完备性与变异守卫**：4 因子共有 $6 \times 3 \times 4 \times 2 = 144$ 种组合，`R1`–`R4` 自上而下依次覆盖 **`94 + 34 + 14 + 2 = 144`**（0 条死规则、0 个未命中空洞）。`MUT-05a`（抓 `return "P3"` 硬编码与 144 真值表）和 `MUT-05b`（抓规则倒序与 `OR→AND` 算子篡改）专门守卫。
- **落库结果**：`fault_tickets.priority` 写命中值；`fault_tickets.grading_score` 恒写 **`NULL`**（兼容保留列，不算分）；`grading_snapshot_json` 完整保存命中快照：

```json
{
  "rule_code": "grading_rules",
  "rule_version": 1,
  "matched_rule_id": "R2_EXPRESS_OR_MAIN_CORE_OR_CRITICAL_ASSET",
  "priority": "P2",
  "inputs": {
    "fault_type": "LAMP_OUT",
    "road_level": "MAIN",
    "asset_importance": null,
    "safety_flag": false
  },
  "emergency_action_required": false
}
```

#### `POST /business-rules` 5 个合法 `rule_code` 的 Pydantic 校验（非法 → `422 INVALID_RULE_PAYLOAD`）

| `rule_code` | Pydantic 强制校验规则 |
|---|---|
| `grading_rules` | `rules` 列表非空且 `rule_id` 全局唯一；每条 `priority ∈ {'P1','P2','P3','P4'}`；条件键仅限白名单（`fault_type`/`fault_type_in`/`road_level`/`road_level_in`/`asset_importance_in`/`safety_flag`）及合法枚举；**必须显式存在 `default_priority == 'P4'` 与 `default_rule_id` 兜底**（若含 `display_names` 须覆盖 `P1`–`P4`） |
| `sla_policy` | `statutory_deadline_hours` 为正整数（默认 `48`）；`response_minutes` 为 `null` 或正整数（默认 `null`）；`emergency_notice_hours` 为正整数（默认 `24`）；`applies_to_priorities` 覆盖 `P1`–`P4` |
| `dispatch_scoring_weights` | `weights`（`workload`、`skill`、`on_time_rate`）非负且总和严格等于 `1.0`；`max_recommendations` ∈ `[1, 50]` |
| `rework_policy` | `max_rework_rounds` 为正整数（`D-14` 默认 `2`）；`on_exceed_action == "FORCE_ADMIN_REVIEW"` |
| `ai_candidate_ttl_hours` | `ttl_hours` 为正整数（`D-15` 默认 `72`） |

> `PATCH /business-rules/{id}` 仅可改 `is_active`/`description`，**禁止原地修改 `rule_value`**（`422 RULE_VALUE_IMMUTABLE`），保障历史快照可追溯。

#### 3 个触发时机、郑州 48h SLA 公式与 `POST /sla/scan` 三段 SQL

- **触发时机（只有 3 个）**：① 人工报障 `POST /tickets`（`confirmed_at = created_at`，立即分级写 deadline）；② AI 候选确认 `POST /tickets/{id}/confirm`（`AI_CANDIDATE_PENDING` 期间 `priority` 与 `repair_deadline_at` 均为 `NULL`，确认后写入）；③ 管理员手工改级 `POST /tickets/{id}/regrade`（`reason` 必填，在 `grading_snapshot_json` 记 `manual_override: true` 并在审计保存旧快照）。
- **唯一 deadline 公式**：
  ```text
  T_discover = 发现并确认时刻（fault_tickets.confirmed_at；人工建单时等于 created_at）

  repair_deadline_at   = T_discover + sla_policy.statutory_deadline_hours * 60 分钟  （默认 +48h = +2880m）
  response_deadline_at = NULL  （除非未来正式地方规范配入正整数 response_minutes）
  ```
  > **`regrade` 与改派均不重置/不顺延 `repair_deadline_at`**：法定 48h 自「日常监督检查中发现」固定起算；若从接单时刻起算会纵容拖延派工吃穿 48h（`MUT-17` 守卫）。

- **`POST /sla/scan` 三段完整 SQL（只标记与派工防死锁回收，不自动关单）**：

```sql
-- ① 响应阶段超时（现行郑州基线下 response_deadline_at IS NULL，本段零命中；保留兼容）
UPDATE fault_tickets
SET is_sla_overdue = true, overdue_stage = 'RESPONSE', overdue_marked_at = now()
WHERE status IN ('PENDING_DISPATCH','REWORK_PENDING_DISPATCH','DISPATCHED')
  AND response_deadline_at IS NOT NULL AND response_deadline_at < now()
  AND is_sla_overdue = false;
-- 对每行：向区域 ADMIN 发 DISPATCH_RESPONSE_TIMEOUT 通知

-- ② 法定 48 小时维修养护/督促超时：覆盖含 MANUAL_REVIEW 在内的未完成处置活跃态（含 P4 观察）
--    ★ MUT-15 守卫：WHERE ... AND is_sla_overdue = false 幂等守卫严禁删除
UPDATE fault_tickets
SET is_sla_overdue = true, overdue_stage = 'REPAIR', overdue_marked_at = now()
WHERE status IN ('PENDING_DISPATCH','REWORK_PENDING_DISPATCH','DISPATCHED','IN_REPAIR','MANUAL_REVIEW')
  AND repair_deadline_at IS NOT NULL AND repair_deadline_at < now()
  AND is_sla_overdue = false;
-- 对每行：向区域 ADMIN 与当前受派维修员（若有）发 REPAIR_SLA_TIMEOUT 通知

-- ③ 派工接单超时防死锁回收（内部工程防卡死机制，非郑州市法定时限）
UPDATE dispatch_records
SET status = 'TIMEOUT_REASSIGNED', escalated_at = now()
WHERE status = 'PENDING_ACCEPT' AND accept_deadline_at < now();
-- 再通过 state_machine.transition_ticket 将对应票从 DISPATCHED 推回 PENDING_DISPATCH
```

- **第 19 条第 3 款「维修养护或督促」双分支合规闭环**：
  - **市/区管理机构直接维护设施**：48h 内完成维修并进入 `PENDING_ACCEPTANCE`（现场维修完成）→ 验收 `CLOSED`；
  - **社会单位投资建设/管理的设施**：管理员下达督促并留存**督促时间、受督促单位与书面/照片凭证**，经 `MANUAL_REVIEW` 的 `SPECIAL_CLOSE`（`len(reason) >= 10`）留档 `audit_logs` 合规闭环（禁止无凭证口头督促）。

---

### 3.4 机制三：返修闭环与超阈值强制人工复核

- **配置参数（`D-14`）**：`business_rules['rework_policy'] = {"max_rework_rounds": 2, "on_exceed_action": "FORCE_ADMIN_REVIEW"}`。
- **验收驳回 7 步原子事务（`MUT-06a` / `MUT-06b` 守卫）**：

```python
async with session.begin():  # 单事务，缺一即整笔回滚
    # 1. 行锁工单（防并发驳回算出重复 round_no）
    ticket = await session.scalar(
        select(FaultTicket).where(FaultTicket.id == ticket_id).with_for_update()
    )
    assert ticket.status == "PENDING_ACCEPTANCE"

    # 2. 写验收驳回记录（comment 必填）
    acceptance = AcceptanceRecord(
        ticket_id=ticket.id, repair_process_id=repair_process_id,
        inspector_account_id=actor.id, decision="REJECTED",
        comment=payload.comment, accepted_at=now,
    )
    session.add(acceptance)
    await session.flush()

    # 3. 递增返修次数
    ticket.rework_count += 1
    round_no = ticket.rework_count

    # 4. 关闭上一张活跃返修单（C8：先关旧单再开新单，否则撞 uq_rework_orders_active_ticket）
    prev = await session.scalar(
        select(ReworkOrder).where(
            ReworkOrder.ticket_id == ticket.id,
            ReworkOrder.status.notin_(("PASSED", "CLOSED_BY_REVIEW", "CANCELLED")),
        )
    )
    if prev:
        prev.status = "CANCELLED"
        prev.closed_at = now

    # 5. 生成新返修单（严格大于 max_rework_rounds 才超阈值 —— MUT-06b）
    exceed = round_no > max_rework_rounds
    rework = ReworkOrder(
        rework_no=await next_rework_no(session),
        ticket_id=ticket.id, source_acceptance_id=acceptance.id,
        round_no=round_no, region_id=ticket.region_id,
        status="IN_MANUAL_REVIEW" if exceed else "PENDING_DISPATCH",
        reject_reason=payload.comment, requires_manual_review=exceed,
    )
    session.add(rework)
    await session.flush()

    # 6. 回填循环外键（acceptance_records.rework_order_id）
    acceptance.rework_order_id = rework.id

    # 7. 统一状态机推进票状态 + 审计 + 通知
    await transition_ticket(
        session, ticket,
        to_status="MANUAL_REVIEW" if exceed else "REWORK_PENDING_DISPATCH",
        manual_review_reason="rework_threshold_exceeded" if exceed else None,
        action="ACCEPTANCE_REJECTED",
        notify=[
            ("ACCEPTANCE_REJECTED", [ticket_technician_id, *region_admin_ids]),
            *([("REWORK_MANUAL_REVIEW_REQUIRED", all_admin_ids)] if exceed else []),
        ],
    )
```

> **`MUT-06a` 单事务四件事**：写 `acceptance_records(REJECTED)` + `rework_count += 1` + 插 `rework_orders` + 置票态（`REWORK_PENDING_DISPATCH` 或 `MANUAL_REVIEW`），缺一不可；`ck_acceptance_records_rework_linked_on_reject` 在 DB 层同步兜底。
> **`POST /tickets/{id}/review-resolve` 三出口**：`APPROVE_REASSIGN`（若存在 `IN_MANUAL_REVIEW` 返修单则按 `C9` 置终态 `CLOSED_BY_REVIEW` + `manual_review_decision='APPROVE_REASSIGN'`，票统一回 `PENDING_DISPATCH` 由 `A6` 重新派工——不进需活跃返修单的 `REWORK_PENDING_DISPATCH`，人工裁决后允许再派一轮）；`SPECIAL_CLOSE`（票 `CLOSED`，若存在 `IN_MANUAL_REVIEW` 返修单则置 `CLOSED_BY_REVIEW`，`len(reason) >= 10`）；`TERMINATE_CANCEL`（票置 `CANCELLED`，若存在活跃返修单亦置 `CANCELLED`）。（注：经 `A8` 累计拒单或 `A10` 升级进入 `MANUAL_REVIEW` 时可能无 `rework_orders` 行。）

#### 三大机制共同实现纪律（6 条）

1. **参数全部来自 `business_rules`**，代码零魔法数字；
2. **每次计算写入规则快照**（`grading_snapshot_json` / `candidate_snapshot_json` / 规则版本号），历史可复算；
3. **同输入同输出**（含三级确定性并列裁决）；
4. **纯函数化**（`evaluate_grading_rules()` / `score_candidates()` / `compute_deadlines()` 不触碰 DB，供 L1 穷举单测）；
5. **零随机性**（不用 `random` / `uuid4()` 参与打分或排序）；
6. **除零显式保护**（`on_time_rate` 无历史取 `0.5`，`max_active_dispatch >= 1` 由 DB `CHECK` 保证）。

---

### 3.5 幂等执行与点位防重落地机制

- **单阶段 append-only 幂等基础设施（`audit_logs` 承载 + PG 事务级咨询锁，12 个必需 `POST` 端点见 `docs/API.md` §6.4）**：
  - 前置校验 `Idempotency-Key` 头（缺失 → `400 IDEMPOTENCY_KEY_REQUIRED`；格式不符 `^[A-Za-z0-9_-]{8,128}$` → `400 IDEMPOTENCY_KEY_INVALID`），并计算规范化请求载荷 SHA-256（`request_payload_hash`）。`audit_logs` 严格 **append-only**（只插不改、无 `updated_at`、**不写任何「进行中」占位行**）。
  - **并发门禁与锁内双重读取 4 步执行流程**：
    1. **事务级非阻塞咨询锁抢占**：收到带 `Idempotency-Key` 的请求后（可先无锁快速查一次已完成 `2xx` 记录命中热路径），在同一数据库事务内先执行非阻塞事务级咨询锁：
       `SELECT pg_try_advisory_xact_lock(hashtextextended(actor_account_id::text || ':' || method || ':' || path || ':' || idempotency_key, 0))`
       （64-bit `bigint` key，事务 `COMMIT`/`ROLLBACK` 自动释放，不可且无需手工 `unlock`，不写任何占位行；12 个必需端点均要求已鉴权，`actor_account_id` 非空，详见 PG 17 官方文档 `https://www.postgresql.org/docs/17/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS`）。
    2. **锁争用立即冲突**：若返回 `false`（同键请求正在执行事务中）→ **立即返回 `409 IDEMPOTENCY_REQUEST_IN_PROGRESS`**（不阻塞等锁）。
    3. **锁保护下查重/重放（消除 TOCTOU 竞态）**：若返回 `true`（获锁），在锁保护下查询 `audit_logs` 是否已有同 `(actor_account_id, method, path, idempotency_key)` 且 `200 <= response_status < 300` 的已完成记录：若有则比对 `request_payload_hash`（一致 → 原样重放 `response_status` + `response_body_json`（存储的响应体快照）并附 **本项目自定义扩展头 `X-Idempotent-Replay: true`**，非 IETF 草案内容；不一致 → `422 IDEMPOTENCY_PAYLOAD_MISMATCH`）。
    4. **单次业务写入与 append-only 落盘**：若无既有记录，执行业务写入并在成功（`2xx`）时同事务向 `audit_logs` **只插入 1 条**完整审计+幂等记录（由部分唯一索引 `uq_audit_logs_idempotency` 兜底）；若业务返回 `4xx`/`5xx` 或抛异常，事务回滚、不写幂等占用行、咨询锁自动释放，后续重试按新请求处理。
  - **失败（4xx/5xx）不占用幂等键**：部分唯一索引 `uq_audit_logs_idempotency` 仅约束 `WHERE idempotency_key IS NOT NULL AND response_status >= 200 AND response_status < 300`，保证用户收到 `409`/`422` 修正重提不被误锁。
  - **并发竞态唯一可断言不变量**：同键并发请求时仅保证 `fault_tickets` 精确 `+1` 行且 `audit_logs` 该幂等键精确 `1` 行（E2E 禁断言两个并发请求都返回 `2xx`）。
- **点位活跃故障单防重落地**：
  - 应用层先按 `(lighting_point_id, fault_type)` 查非终态活跃单，命中即返带 `details.existing_ticket_id/no/status` 的 `409 DUPLICATE_ACTIVE_TICKET`（`details` 三字段仅供前端渲染跳转链接，详见 `docs/API.md` §4）。
  - 并发竞争由 PG 部分唯一索引 `uq_fault_tickets_active_point_type` 兜底；服务层捕获 `psycopg.errors.UniqueViolation`（`23505`）后**必须精确比对 `e.diag.constraint_name == 'uq_fault_tickets_active_point_type'`** 才翻译为 `409 DUPLICATE_ACTIVE_TICKET`，防止把 `ticket_no` 撞号等真缺陷伪装成业务冲突。

---

## 4. 数据库 Schema 设计（17 业务表 / 76 CHECK / 41 外键列 / 22 UNIQUE / 37 普通索引）

### 4.1 口径细则、三套编号映射与逐表约束对账总表

#### 口径细则、`alembic_version` 排除声明与五类排除项

- **`alembic_version` 独立分层声明**：`alembic_version` 仅含 `version_num varchar(32) PRIMARY KEY`，属 Alembic 迁移元数据表，**不计入 17 张业务表**、不由应用访问、不进 ER 图；任何查表断言必须加 `WHERE table_name <> 'alembic_version'`（迁移期断言 5）。
- **五类排除项**：① `alembic_version`；② PG 主键自动创建的 `*_pkey` 索引；③ FK 支撑索引（PG 不会为外键自动建索引，本设计已逐表手写声明，计入 **普通索引 37**）；④ `NOT NULL`（PG `pg_attribute.attnotnull`，不进 `table_constraints`，不计入 **CHECK 76**）；⑤ 列内联 `UNIQUE` 隐式索引（统一用具名约束，计入 **UNIQUE 22**，不重复计）。
- **两个术语陷阱（必须区分）**：
  1. **外键列数 `41` ≠ 外键约束数**：`41` 是 17 表引用列的 DDL 设计计数，迁移落地后以 `pg_constraint` `contype='f'` 实测为准，若因合并复合 FK 产生差异须逐条解释并回写。
  2. **部分唯一索引 `6` ≠ 业务防重 `4`**：6 个部分唯一索引 = **4 个业务防重**（`uq_fault_tickets_active_point_type`、`uq_dispatch_records_active_ticket`、`uq_rework_orders_active_ticket`、`uq_audit_logs_idempotency`）+ **2 个配置态唯一**（`uq_scope_authorizations_account_region_type`、`uq_business_rules_code_active`）。另 **`uq_rework_orders_ticket_round`（全时）与 `uq_rework_orders_active_ticket`（仅活跃态）是两个不同约束**，不可合并；`accounts.status` 与 `maintenance_regions.is_active` 上的部分索引为**非唯一部分索引**（属 12 项非唯一部分索引之列）。

#### 迁移落地后 5 条实测对账 SQL

```sql
-- 1) 业务表数（期望 = 17，排除 alembic_version）
SELECT count(*) FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name <> 'alembic_version';

-- 2) CHECK 约束数（期望合计 = 76）
SELECT count(*) FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname <> 'alembic_version' AND con.contype = 'c';

-- 3) 外键约束数（与设计期外键列数 41 对照，有差异须逐条解释）
SELECT count(*) FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname <> 'alembic_version' AND con.contype = 'f';

-- 4) 唯一索引（期望 = 22，含 6 个部分唯一；以 NOT i.indisprimary 排除 PG 主键自动生成的 *_pkey / pk_* 唯一索引）
SELECT count(*) AS uniq_total,
       count(*) FILTER (WHERE i.indpred IS NOT NULL) AS partial_uniq
FROM pg_index i
JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relname <> 'alembic_version'
  AND i.indisunique AND NOT i.indisprimary;

-- 5) 普通（非唯一）索引（期望 = 37，含 12 个非唯一部分索引）
SELECT count(*) AS plain_total,
       count(*) FILTER (WHERE indexdef LIKE '%WHERE%') AS partial_plain
FROM pg_indexes
WHERE schemaname = 'public' AND tablename <> 'alembic_version'
  AND indexdef NOT LIKE 'CREATE UNIQUE INDEX%';
```

#### 三套编号映射、三处错位与物理建表拓扑序

- **圈号 ①–⑰**（用户清单业务视角序）≠ **物理建表序 1–17**（Alembic 迁移拓扑执行唯一权威）。
- **三处错位原因**：① 账号在业务清单居 ①，但物理必须先建无业务外键的 `maintenance_regions`（1）与 `roles`（2）；② `acceptance_records`（⑯）与 `rework_orders`（⑰）构成**循环外键**，物理按 `tables.md` §0.1 先建 `rework_orders`（14）再建 `acceptance_records`（15）并按方案 B 后补 FK；同时 `dispatch_records`（11）虽排在 `rework_orders`（14）之前，但其可空列 `rework_order_id → rework_orders.id` 构成前向 FK（`tables.md` §0.1/§0.2 未单列该前向延迟步骤），迁移时第 11 步仅建列不内联 FK，待第 14 步建成后显式 `op.create_foreign_key()` 补齐（见 §4.5）；③ `audit_logs`（⑦）与 `notification_tasks`（⑥）引用面最广，物理推后至 16/17。
- **17 步物理建表序**：`1. maintenance_regions` → `2. roles` → `3. lighting_points` → `4. accounts` → `5. account_roles` → `6. scope_authorizations` → `7. business_rules` → `8. facility_assets` → `9. fault_tickets` → `10. fault_evidences` → `11. dispatch_records` → `12. repair_processes` → `13. material_usages` → `14. rework_orders` → `15. acceptance_records` → `16. audit_logs` → `17. notification_tasks`。

#### 中文表名 ↔ 英文物理名映射与逐表约束/索引汇总表

| 圈号 | 中文表名 | 物理表名 | 物理序 | CHECK | 外键列 | UNIQUE | 普通索引 | 部分唯一索引（6 个） |
|---:|---|---|---:|---:|---:|---:|---:|---|
| ① | 账号 | `accounts` | 4 | 4 | 1 | 2 | 1 | — |
| ② | 角色 | `roles` | 2 | 1 | 0 | 1 | 0 | — |
| ③ | 账号角色 | `account_roles` | 5 | 0 | 3 | 2 | 1 | — |
| ④ | 业务范围授权 | `scope_authorizations` | 6 | 2 | 3 | 1 | 2 | `uq_scope_authorizations_account_region_type` |
| ⑤ | 业务规则 | `business_rules` | 7 | 2 | 1 | 2 | 0 | `uq_business_rules_code_active` |
| ⑥ | 通知任务 | `notification_tasks` | 17 | 7 | 4 | 0 | 4 | — |
| ⑦ | 操作审计 | `audit_logs` | 16 | 4 | 1 | 1 | 6 | `uq_audit_logs_idempotency` |
| ⑧ | 运维区域 | `maintenance_regions` | 1 | 2 | 1 | 1 | 2 | — |
| ⑨ | 照明点位 | `lighting_points` | 3 | 6 | 1 | 1 | 2 | — |
| ⑩ | 设施资产 | `facility_assets` | 8 | 4 | 1 | 1 | 1 | — |
| ⑪ | 故障单 | `fault_tickets` | 9 | 14 | 6 | 2 | 7 | `uq_fault_tickets_active_point_type` |
| ⑫ | 故障证据 | `fault_evidences` | 10 | 8 | 2 | 1 | 1 | — |
| ⑬ | 派工记录 | `dispatch_records` | 11 | 7 | 5 | 1 | 3 | `uq_dispatch_records_active_ticket` |
| ⑭ | 维修过程 | `repair_processes` | 12 | 4 | 3 | 1 | 2 | — |
| ⑮ | 材料使用 | `material_usages` | 13 | 3 | 2 | 1 | 1 | — |
| ⑯ | 验收记录 | `acceptance_records` | 15 | 3 | 4 | 1 | 2 | — |
| ⑰ | 返修单 | `rework_orders` | 14 | 5 | 3 | 3 | 2 | `uq_rework_orders_active_ticket` |
| | **合计** | **17 表** | — | **76** | **41** | **22** | **37** | **6** |

**逐项求和复算**：
- **CHECK 76** = `4+1+0+2+2+7+4+2+6+4+14+8+7+4+3+3+5 = 76`
- **外键列 41** = `1+0+3+3+1+4+1+1+1+1+6+2+5+3+2+4+3 = 41`
- **UNIQUE 22** = `2+1+2+1+2+0+1+1+1+1+2+1+1+1+1+1+3 = 22`（含 **6** 个部分唯一索引）
- **普通索引 37** = `1+0+1+2+0+4+6+2+2+1+7+1+3+2+1+2+2 = 37`（含 **12** 个非唯一部分索引：`ix_maintenance_regions_is_active`、`ix_accounts_status`、`ix_scope_authorizations_account_id_active`、`ix_fault_tickets_{manual_review,pending_candidates,pending_dispatch,sla_scan}`、`ix_dispatch_records_technician_active`、`ix_audit_logs_ticket_id`、`ix_notification_tasks_{dead_letter,pending_due,recipient_unread}`）

---

### 4.2 核心表 ⑪ `fault_tickets`（33 列 / 14 CHECK / 2 UNIQUE 含 1 部分唯一）

> **口径说明**：本表完整 DDL 定义为 33 列、6 外键列、14 个具名 `CHECK`、`2 UNIQUE`（1 全表唯一 `uq_fault_tickets_ticket_no` + 1 部分唯一 `uq_fault_tickets_active_point_type`）、7 个普通索引（含 4 个部分普通索引）。（早期曾有摘要标题误写作「36 列 / 2 部分唯一」，实以本节逐列与 §4.1 汇总表为准，全库部分唯一索引合计仍为 6。）

#### 全列定义（33 列）

| 列 | 类型 | 可空 / 默认 | 约束 / 说明 |
|---|---|---|---|
| `id` | `bigserial` | NO | PK `pk_fault_tickets` |
| `ticket_no` | `varchar(32)` | NO | UNIQUE `uq_fault_tickets_ticket_no`，格式 `FT-YYYYMMDD-NNNN` |
| `region_id` | `bigint` | NO | FK → `maintenance_regions`（**冗余列**，必等于点位所属区域） |
| `lighting_point_id` | `bigint` | NO | FK → `lighting_points` |
| `facility_asset_id` | `bigint` | YES NULL | FK → `facility_assets`（为空时 `grading_rules` 的 `asset_importance` 为 `null`） |
| `report_source` | `varchar(24)` | NO | `AI_CANDIDATE` / `MANUAL_INSPECT` / `MANUAL_ADMIN` |
| `fault_type` | `varchar(24)` | NO | `LAMP_OUT`/`FIXTURE_DAMAGED`/`OCCLUDED`/`ELECTRIC_HAZARD`/`FLICKERING`/`OTHER` |
| `title` | `varchar(128)` | NO | 标题 |
| `description` | `text` | YES NULL | 描述 |
| `status` | `varchar(32)` | NO | 主状态机 9 态（§2.1） |
| `priority` | `varchar(4)` | YES NULL | `P1`..`P4`；`AI_CANDIDATE_PENDING` 期间为 `NULL` |
| `grading_score` | `numeric(6,2)` | YES NULL | 保留可空兼容列（确定性规则分级下恒写 `NULL`） |
| `grading_snapshot_json` | `jsonb` | YES NULL | 分级快照（`rule_code`/`rule_version`/`matched_rule_id`/`inputs`/`emergency_action_required`） |
| `reporter_account_id` | `bigint` | NO | FK → `accounts` |
| `confirmed_by_inspector` | `boolean` | NO `false` | 巡查员确认标志（V1 硬门禁） |
| `confirmed_by_inspector_id` | `bigint` | YES NULL | FK → `accounts` |
| `confirmed_at` | `timestamptz` | YES NULL | 确认时刻（`T_discover` 起算点） |
| `ai_suggestion_json` | `jsonb` | YES NULL | AI 建议汇总快照 |
| `ai_max_confidence` | `numeric(4,3)` | YES NULL | `0.000`–`1.000` |
| `response_deadline_at` | `timestamptz` | YES NULL | 默认 **`NULL`**（仅配入合法 `response_minutes` 时写入） |
| `repair_deadline_at` | `timestamptz` | YES NULL | `T_discover + 48h`（郑州令第 152 号第 19 条第 3 款；`regrade`/改派不顺延） |
| `is_sla_overdue` | `boolean` | NO `false` | SLA 超时标记 |
| `overdue_stage` | `varchar(16)` | YES NULL | `RESPONSE` / `REPAIR` / `NULL` |
| `overdue_marked_at` | `timestamptz` | YES NULL | 标记时刻 |
| `rework_count` | `smallint` | NO `0` | **冗余列**（= `count(rework_orders)`） |
| `dispatch_reject_count` | `smallint` | NO `0` | 累计拒单数（≥3 转 `MANUAL_REVIEW`） |
| `manual_review_reason` | `varchar(256)` | YES NULL | `MANUAL_REVIEW` 时必填 |
| `cancel_reason` | `varchar(256)` | YES NULL | 撤销原因 |
| `cancelled_by_account_id` | `bigint` | YES NULL | FK → `accounts` |
| `cancelled_at` | `timestamptz` | YES NULL | 撤销时间 |
| `closed_at` | `timestamptz` | YES NULL | 关闭时间 |
| `created_at` / `updated_at` | `timestamptz` | NO `now()` | 时间戳（2 列） |

#### 14 个具名 `CHECK` 约束与核心边界 SQL

| # | 约束名 | 语义 |
|---:|---|---|
| 1 | `ck_fault_tickets_report_source_enum` | `report_source IN ('AI_CANDIDATE','MANUAL_INSPECT','MANUAL_ADMIN')` |
| 2 | `ck_fault_tickets_fault_type_enum` | `fault_type IN ('LAMP_OUT','FIXTURE_DAMAGED','OCCLUDED','ELECTRIC_HAZARD','FLICKERING','OTHER')` |
| 3 | `ck_fault_tickets_status_enum` | `status` ∈ 9 态枚举 |
| 4 | `ck_fault_tickets_priority_enum` | `priority IS NULL OR priority IN ('P1','P2','P3','P4')` |
| 5 | `ck_fault_tickets_counters_non_negative` | `rework_count >= 0 AND dispatch_reject_count >= 0` |
| 6 | `ck_fault_tickets_grading_score_range` | `grading_score IS NULL OR (grading_score >= 0 AND grading_score <= 100)` |
| 7 | `ck_fault_tickets_ai_max_confidence_range` | `ai_max_confidence IS NULL OR (ai_max_confidence >= 0 AND ai_max_confidence <= 1)` |
| 8 | **`ck_fault_tickets_inspector_confirmation_serialized`** | **V1 三态串行化硬门禁**（见下 SQL） |
| 9 | **`ck_fault_tickets_ai_source_excludes_unsupported_types`** | **V3 DB 兜底**（见下 SQL） |
| 10 | `ck_fault_tickets_manual_review_reason_required` | `status <> 'MANUAL_REVIEW' OR manual_review_reason IS NOT NULL` |
| 11 | `ck_fault_tickets_cancel_fields_consistent` | `status='CANCELLED'` ⟺ `cancel_reason`/`cancelled_by_account_id`/`cancelled_at` 全非空 |
| 12 | `ck_fault_tickets_closed_at_consistent` | `(status = 'CLOSED') = (closed_at IS NOT NULL)` |
| 13 | `ck_fault_tickets_overdue_consistent` | `is_sla_overdue` ⟺ `overdue_stage` 与 `overdue_marked_at` 全非空 |
| 14 | `ck_fault_tickets_overdue_stage_enum` | `overdue_stage IS NULL OR overdue_stage IN ('RESPONSE','REPAIR')` |

```sql
-- V1 硬门禁：四列原子绑定，防「confirmed_by_inspector=true 却无确认时间或仍挂待确认态」的自相矛盾数据
ALTER TABLE fault_tickets ADD CONSTRAINT ck_fault_tickets_inspector_confirmation_serialized CHECK (
  (confirmed_by_inspector = FALSE AND confirmed_by_inspector_id IS NULL
     AND confirmed_at IS NULL AND status = 'AI_CANDIDATE_PENDING')
  OR
  (confirmed_by_inspector = TRUE AND confirmed_by_inspector_id IS NOT NULL
     AND confirmed_at IS NOT NULL AND status <> 'AI_CANDIDATE_PENDING')
);

-- V3 硬门禁：AI 候选永不产出时序闪烁与电气隐患
ALTER TABLE fault_tickets ADD CONSTRAINT ck_fault_tickets_ai_source_excludes_unsupported_types CHECK (
  NOT (report_source = 'AI_CANDIDATE' AND fault_type IN ('FLICKERING', 'ELECTRIC_HAZARD'))
);
```

- **索引（2 UNIQUE + 7 普通索引）**：`uq_fault_tickets_ticket_no`、`uq_fault_tickets_active_point_type`（`ON (lighting_point_id, fault_type) WHERE status NOT IN ('CLOSED','CANCELLED')`）；4 个部分普通索引（`ix_fault_tickets_pending_candidates`、`ix_fault_tickets_pending_dispatch`、`ix_fault_tickets_sla_scan`、`ix_fault_tickets_manual_review`）+ 3 个全表索引（`lighting_point_id`、`reporter_account_id`、`created_at DESC`）。
- **两冗余列处置纪律（`D-24`）**：`region_id`（避高频区域过滤全表 JOIN `lighting_points`，由迁移期断言 1/2 守卫）与 `rework_count`（返修阈值热路径与 CHECK 可读性，由迁移期断言 4 守卫）均为刻意冗余。**绝不允许保留列但不校验**；若冗余代价过高，合法选择唯有删列改查询。

---

### 4.3 证据表 ⑫ `fault_evidences`（8 CHECK）与可空 `ticket_id` 裁决

| 列 | 类型 | 可空 / 默认 | 约束 / 说明 |
|---|---|---|---|
| `id` | `bigserial` | NO | PK `pk_fault_evidences` |
| `ticket_id` | `bigint` | **YES NULL** | FK → `fault_tickets`（**仅零候选归档可空**） |
| `analysis_id` | `uuid` | YES NULL | 单图分析 ID（`ticket_id IS NULL` 时必填） |
| `evidence_type` | `varchar(24)` | NO | `INSPECTION_PHOTO` / `ANNOTATED_PHOTO` / `REPAIR_PHOTO` / `ACCEPTANCE_PHOTO` |
| `media_mime` | `varchar(32)` | NO | 仅 `image/jpeg` / `image/png` / `image/webp` |
| `storage_path` | `varchar(512)` | NO | 相对 `uploads/{yyyy}/{mm}/{uuid4}.{ext}` 路径 |
| `file_size_bytes` | `bigint` | NO | `> 0 AND <= 15728640`（15 MiB，`D-02`） |
| `sha256` | `char(64)` | NO | `^[0-9a-f]{64}$` |
| `image_width` / `image_height` | `integer` | NO | EXIF 转正后尺寸，`> 0 AND <= 20000` |
| `ai_result_json` | `jsonb` | YES NULL | 单图 `FaultCandidateResult`（含 `max_fused_confidence`） |
| `ai_is_baseline_mode` | `boolean` | NO `false` | 是否基线模式 |
| `uploaded_by_account_id` | `bigint` | NO | FK → `accounts` |
| `captured_at` | `timestamptz` | YES NULL | 拍摄时间（可选，**不作昼夜判定依据**） |
| `created_at` | `timestamptz` | NO `now()` | 清理回收依据 |

#### `ticket_id` 为什么可空（Lead 裁决 #1）与两类「无票」区分

- **原始矛盾**：白天拍到正常熄灭的路灯，业务要求「不建故障票」（否则 `uq_fault_tickets_active_point_type` 会把点位锁死直到 72h TTL 过期）；但若 `ticket_id NOT NULL`，证据物理上无法落库，「白天灭灯不误报」就沦为不可在 DB 侧断言的口头承诺。
- **方案**：`ticket_id` 置可空，并以第 6 条 CHECK `ck_fault_evidences_ticket_or_orphan_inspection` 封死「只许巡查照片且已产 `analysis_id` 的零候选证据」可空（`MUT-16` 专测该 `AND` 不得变成 `OR`）。
- **必须区分的两类「无票」**：① **零候选归档**（正常抑制）→ `fault_evidences` 有行且 `ticket_id = NULL`，返回 `200 {ticket: null, evidences: [...]}`；② **视觉崩溃**（异常）→ `fault_tickets` 0 行、`fault_evidences` **0 行**且删除磁盘文件，返回 `503 VISION_*`。

- **索引（1 UNIQUE + 1 普通索引）**：`uq_fault_evidences_sha256_per_ticket`（`(ticket_id, sha256)` 唯一索引）与 `ix_fault_evidences_ticket_id_type`（`(ticket_id, evidence_type)` 普通索引）。

#### `uq_fault_evidences_sha256_per_ticket` 对 NULL 行不生效的已知取舍

PostgreSQL 默认把 `NULL` 视作互不相等，故 `uq_fault_evidences_sha256_per_ticket (ticket_id, sha256)` 对 `ticket_id IS NULL` 的行**不生效**（同一张白天灭灯图重复上传 N 次会写入 N 行孤儿证据）。**本设计明确处置**：物理文件在 `uploads/` 下按 `sha256` 内容去重落盘，DB 行不建复杂表达式唯一索引去重，由清理任务按 `created_at` 定时回收。**绝对不得在文档或测试中声称「孤儿证据表已 DB 去重」。**

#### 8 个具名 `CHECK` 约束

| # | 约束名 | 内容 |
|---:|---|---|
| 1 | `ck_fault_evidences_evidence_type_enum` | `evidence_type IN ('INSPECTION_PHOTO','ANNOTATED_PHOTO','REPAIR_PHOTO','ACCEPTANCE_PHOTO')` |
| 2 | **`ck_fault_evidences_media_mime_static_image_only`** | `media_mime IN ('image/jpeg','image/png','image/webp')`（不接视频的 DB 硬落点） |
| 3 | `ck_fault_evidences_file_size_range` | `file_size_bytes > 0 AND file_size_bytes <= 15728640`（`D-02` 15 MiB） |
| 4 | `ck_fault_evidences_image_dimensions_positive` | `image_width > 0 AND image_height > 0 AND image_width <= 20000 AND image_height <= 20000` |
| 5 | `ck_fault_evidences_sha256_format` | `sha256 ~ '^[0-9a-f]{64}$'` |
| 6 | **`ck_fault_evidences_ticket_or_orphan_inspection`** | `ticket_id IS NOT NULL OR (evidence_type = 'INSPECTION_PHOTO' AND analysis_id IS NOT NULL)`（`MUT-16`） |
| 7 | `ck_fault_evidences_baseline_confidence_cap` | `ai_is_baseline_mode = FALSE OR ai_result_json IS NULL OR coalesce((ai_result_json->>'max_fused_confidence')::numeric, 0) <= 0.65`（`yolo26s.pt` 基线防假定兜底封顶） |
| 8 | `ck_fault_evidences_ai_safety_flags_required` | `ai_result_json IS NULL OR ((ai_result_json->>'requires_inspector_confirmation')::boolean IS TRUE AND (ai_result_json->>'replaces_electrical_safety_check')::boolean IS FALSE)`（V1/V2 反向校验） |

---

### 4.4 其余 15 张业务表结构与约束规范

| 圈号 | 表名（职责） | 关键列与类型 | FK / UNIQUE / 普通索引 | 具名 CHECK（数量对齐 §4.1） |
|---:|---|---|---|---|
| ① | `accounts`（三角色统一账号） | `id`, `username` `varchar(64)`, `password_hash` (`pwdlib[argon2]`), `full_name`, `employee_no` (NULL), `phone`, `email`, `status` (`ACTIVE`/`DISABLED`/`LOCKED`), `skills` `text[]`, `max_active_dispatch` `smallint` 默认 3, `token_version` `int` 默认 1（O(1) 吊销）, `manager_account_id` (自引用 NULL), `last_login_at`, `created_at`/`updated_at` | 1 FK (`manager_account_id` SET NULL)；2 UNIQUE (`uq_accounts_username`, `uq_accounts_employee_no`)；1 部分普通索引 (`ix_accounts_status` `WHERE status='ACTIVE'`) | **4**：`status` 枚举、`token_version >= 1`、`max_active_dispatch BETWEEN 1 AND 20`、`username ~ '^[a-z0-9][a-z0-9_.-]{2,63}$'` |
| ② | `roles`（角色字典只读 + 种子） | `id` `smallserial`, `code` `varchar(32)`, `name`, `description`, `created_at`（迁移内 `op.bulk_insert` `ADMIN`/`INSPECTOR`/`TECHNICIAN`） | 0 FK；1 UNIQUE (`uq_roles_code`)；0 普通索引 | **1**：`ck_roles_code_enum` (`ADMIN`/`INSPECTOR`/`TECHNICIAN`) |
| ③ | `account_roles`（账号角色关联） | `id`, `account_id` FK (`CASCADE`), `role_id` FK (`RESTRICT`), `granted_by_account_id` FK (`SET NULL`), `granted_at`（虽是多对多结构，当前约束**一账号一角色**） | 3 FK；2 UNIQUE (`uq_account_roles_account_id`, `uq_account_roles_account_id_role_id`)；1 普通索引 (`role_id`) | **0** |
| ④ | `scope_authorizations`（区域授权事实源） | `id`, `account_id` FK (`CASCADE`), `region_id` FK (`RESTRICT`), `scope_type`, `is_active` 默认 `true`, `valid_from`, `valid_to` (NULL=长期), `granted_by_account_id` FK (`SET NULL`), `remark`, `created_at`/`updated_at`（**软停用不物理删**） | 3 FK；1 部分唯一 (`uq_scope_authorizations_account_region_type` ON `(account_id, region_id, scope_type) WHERE is_active = true`)；2 普通索引（`ix_scope_authorizations_region_type_active` + 部分索引 `ix_scope_authorizations_account_id_active` `WHERE is_active=true`） | **2**：`scope_type` ∈ `INSPECT`/`REPAIR`/`MANAGE`、`valid_to IS NULL OR valid_to > valid_from` |
| ⑤ | `business_rules`（版本化规则） | `id`, `rule_code` ∈ `{grading_rules, sla_policy, dispatch_scoring_weights, rework_policy, ai_candidate_ttl_hours}`, `version` `int`, `rule_value` `jsonb`, `description`, `is_active`, `effective_from`, `created_by_account_id` FK (`SET NULL`), `created_at`（禁原地改 `rule_value` → `422 RULE_VALUE_IMMUTABLE`） | 1 FK；2 UNIQUE (`uq_business_rules_code_version`, 部分唯一 `uq_business_rules_code_active` `WHERE is_active = true`)；0 普通索引 | **2**：`version >= 1`、`jsonb_typeof(rule_value) = 'object'` |
| ⑥ | `notification_tasks`（站内通知与重试队列） | `id`, `recipient_account_id` FK (`CASCADE`), `event_type` (19 种业务事件), `channel` (`IN_APP`/`SMS`/`EMAIL`, 本期实现 `IN_APP`), `title`, `body`, `payload_json` `jsonb`, `related_ticket_id` FK (`CASCADE` NULL), `related_dispatch_id` FK (`CASCADE` NULL), `status`, `attempt_count`, `max_attempts`, `next_retry_at`, `last_error`, `sent_at`, `is_read`, `read_at`, `acknowledged_by_account_id` FK (`SET NULL` NULL), `acknowledged_at`, `created_at`/`updated_at` | 4 FK；0 UNIQUE；4 普通索引（含 3 部分索引：`ix_notification_tasks_pending_due`、`ix_notification_tasks_recipient_unread`、`ix_notification_tasks_dead_letter` + `related_ticket_id`） | **7**：`status` 5 态、`channel` 3 态、`attempt_count`/`max_attempts` 范围、`(status='SENT')=(sent_at IS NOT NULL)`、死信需 `last_error`、重试/待发窗口一致性、`(is_read=TRUE)=(read_at IS NOT NULL)` |
| ⑦ | `audit_logs`（只插不改审计 + 幂等存储） | `id`, `request_id` `uuid`, `actor_account_id` FK (`SET NULL` NULL), `actor_username`, `actor_role_code`, `action` (约 40 值，含 `FORBIDDEN_ACCESS_DENIED`), `resource_type` (17 值), `resource_id`, `ticket_id` (不建 FK), `method`, `path`, `request_payload` `jsonb`（禁存 `password` / `access_token`）, `request_payload_hash` `char(64)`, `idempotency_key` `varchar(128)`, `response_status` `smallint`, `error_code`, `business_snapshot_json` `jsonb`, `response_body_json` `jsonb`（幂等原样重放必需）, `client_ip` `inet`, `user_agent`, `created_at` | 1 FK；1 部分唯一 (`uq_audit_logs_idempotency` ON `(actor_account_id, method, path, idempotency_key) WHERE idempotency_key IS NOT NULL AND response_status >= 200 AND response_status < 300`)；6 普通索引（含部分索引 `ix_audit_logs_ticket_id` `WHERE ticket_id IS NOT NULL`） | **4**：`response_status BETWEEN 100 AND 599`、`method` 5 态、`ck_audit_logs_payload_hash_format`（`request_payload_hash IS NULL OR request_payload_hash ~ '^[0-9a-f]{64}$'`）、`(response_status < 400) OR error_code IS NOT NULL` |
| ⑧ | `maintenance_regions`（区域树） | `id`, `region_code` `varchar(32)`, `name`, `parent_region_id` FK (自引用 `RESTRICT` NULL), `level` `smallint` 默认 3, `boundary_geojson` `jsonb` (仅展示，不判单), `description`, `is_active`, `created_at`/`updated_at` | 1 FK；1 UNIQUE (`uq_maintenance_regions_region_code`)；2 普通索引（`parent_region_id` + 部分索引 `ix_maintenance_regions_is_active` `WHERE is_active=true`） | **2**：`level BETWEEN 1 AND 3`、`parent_region_id IS NULL OR parent_region_id <> id` |
| ⑨ | `lighting_points`（照明点位） | `id`, `point_code` `varchar(64)`, `region_id` FK (`RESTRICT`), `name`, `road_name`, `road_level`, `longitude` `numeric(10,7)`, `latitude` `numeric(10,7)`, `install_height_m` `numeric(5,2)`, `lamp_count` `smallint`, `power_supply_type` (`GRID`/`SOLAR`/`OTHER` 纯属性无控制), `status` (`IN_SERVICE`/`SUSPENDED`/`RETIRED`), `description`, `created_at`/`updated_at` | 1 FK；1 UNIQUE (`uq_lighting_points_point_code`)；2 普通索引（`(region_id, status)`, `road_name`） | **6**：`road_level`、`status`、`power_supply_type`、`longitude` ∈ `[-180,180]`、`latitude` ∈ `[-90,90]`、`lamp_count >= 1` |
| ⑩ | `facility_assets`（设施资产台账） | `id`, `asset_code` `varchar(64)`, `lighting_point_id` FK (`RESTRICT`), `asset_type` (`LUMINAIRE`/`POLE`/`BRACKET`/`CONTROL_BOX`/`CABLE`/`OTHER`), `name`, `model_spec`, `rated_power_w` `numeric(8,2)` (**仅铭牌属性，不做能耗计算/预测**), `importance` (`CRITICAL`/`HIGH`/`NORMAL`), `installed_at` `date`, `status` (`IN_SERVICE`/`FAULTY`/`REPAIRING`/`RETIRED`), `description`, `created_at`/`updated_at` | 1 FK；1 UNIQUE (`uq_facility_assets_asset_code`)；1 普通索引 (`lighting_point_id`) | **4**：`asset_type`、`importance`、`status`、`rated_power_w IS NULL OR rated_power_w >= 0` |
| ⑬ | `dispatch_records`（派工记录） | `id`, `ticket_id` FK, `rework_order_id` FK (NULL), `region_id` (冗余), `technician_account_id` FK, `scope_authorization_id` FK (因果链), `dispatched_by_account_id` FK, `dispatch_mode` (`MANUAL`/`AUTO_RECOMMENDED`/`REASSIGN`), `status` (7 态), `recommend_score` `numeric(6,4)`, `candidate_snapshot_json` `jsonb`, `dispatched_at`, `accept_deadline_at` (防死锁兜底默认取票 `repair_deadline_at`), `accepted_at`/`rejected_at`/`completed_at`/`escalated_at`/`cancelled_at`, `reject_reason`/`escalate_reason`, `created_at` | 5 FK；1 部分唯一 (`uq_dispatch_records_active_ticket` `WHERE status IN ('PENDING_ACCEPT','ACCEPTED')`)；3 普通索引（含部分索引 `ix_dispatch_records_technician_active`） | **7**：`status` 7 态、`dispatch_mode` 3 态、`recommend_score >= 0`、拒单必填理由与时间、升级/超时必填时间、完成与 `completed_at` 一致、接单/完成与 `accepted_at` 一致 |
| ⑭ | `repair_processes`（维修过程 + V2 硬门禁） | `id`, `dispatch_record_id` FK, `ticket_id` FK, `technician_account_id` FK, `arrived_at`, `electrical_safety_checked` `boolean`, `safety_checklist_json` `jsonb` (5 项全 `true`：`power_isolated`/`voltage_verified_zero`/`insulation_intact`/`grounding_connected`/`ppe_worn`), `safety_note`, `root_cause`, `repair_summary`, `repair_started_at`, `repair_finished_at`, `created_at`/`updated_at` | 3 FK；1 UNIQUE (`uq_repair_processes_dispatch_record_id`)；2 普通索引 (`ticket_id`, `(technician_account_id, created_at DESC)`) | **4**：`ck_repair_processes_safety_checklist_complete`（V2 DB 兜底）、`safety_note` 非空白、`repair_summary` 非空白、时间序先后一致 |
| ⑮ | `material_usages`（材料消耗明细） | `id`, `repair_process_id` FK (`CASCADE`), `ticket_id` FK, `material_code`, `material_name`, `quantity` `numeric(10,3)`, `unit` (`PCS`/`M`/`KG`/`SET`), `unit_price` `numeric(12,2)`, `created_at`（不建独立材料目录表） | 2 FK；1 UNIQUE (`uq_material_usages_repair_code` ON `(repair_process_id, material_code)`)；1 普通索引 (`ticket_id`) | **3**：`quantity > 0`、`unit` 枚举、`unit_price IS NULL OR unit_price >= 0` |
| ⑯ | `acceptance_records`（验收记录） | `id`, `ticket_id` FK, `repair_process_id` FK, `rework_order_id` FK (循环外键 NULL), `inspector_account_id` FK, `decision` (`APPROVED`/`REJECTED` — **非 `result='passed'`**), `comment`, `accepted_at`, `created_at` | 4 FK；1 UNIQUE (`uq_acceptance_records_repair_process_id`)；2 普通索引 (`(ticket_id, accepted_at DESC)`, `(inspector_account_id, accepted_at DESC)`) | **3**：`decision` 枚举、驳回必填 `comment` 非空白、`(decision='REJECTED')=(rework_order_id IS NOT NULL)` |
| ⑰ | `rework_orders`（返修单） | `id`, `rework_no` `varchar(32)` (`RW-YYYYMMDD-NNNN`), `ticket_id` FK (`RESTRICT`), `source_acceptance_id` FK (循环外键), `round_no` `smallint`, `region_id` (冗余), `status` (8 态), `reject_reason`, `requires_manual_review` `boolean`, `manual_review_decided_by_account_id` FK, `manual_review_decided_at`, `manual_review_decision`, `manual_review_note`, `closed_at`, `created_at`/`updated_at` | 3 FK；3 UNIQUE (`uq_rework_orders_rework_no`, `uq_rework_orders_ticket_round` 全时, 部分唯一 `uq_rework_orders_active_ticket` `WHERE status NOT IN ('PASSED','CLOSED_BY_REVIEW','CANCELLED')`)；2 普通索引 (`(status, created_at)`, `(region_id, created_at DESC)`) | **5**：`status` 8 态、`round_no >= 1`、`manual_review_decision` 枚举、`requires_manual_review` 与状态一致性、复核结论字段成套一致性 |

> **`notification_tasks.event_type` 19 种枚举值（非表）**：`AI_CANDIDATE_CREATED`、`TICKET_CONFIRMED`、`TICKET_CANCELLED`、`TICKET_DISPATCHED`、`DISPATCH_ACCEPTED`、`DISPATCH_REJECTED`、`DISPATCH_RESPONSE_TIMEOUT`、`REPAIR_COMPLETED`、`REPAIR_SLA_TIMEOUT`、`TICKET_ESCALATED`、`DISPATCH_TIMEOUT_REASSIGNED`、`ACCEPTANCE_APPROVED`、`ACCEPTANCE_REJECTED`、`REWORK_CREATED`、`REWORK_MANUAL_REVIEW_REQUIRED`、`MANUAL_REVIEW_REQUIRED`、`TICKET_CLOSED`、`AI_CANDIDATE_EXPIRED`、`NOTIFICATION_DEAD_LETTER`。

---

### 4.5 循环外键、迁移期 5 条断言、合并候选表与类型纪律

#### 循环外键与前向 FK 处置（`rework_orders` ↔ `acceptance_records`，及 `11. dispatch_records` → `14. rework_orders`）

```text
rework_orders.source_acceptance_id  → acceptance_records.id   （14 → 15，循环外键）
acceptance_records.rework_order_id  → rework_orders.id        （15 → 14，循环外键）
dispatch_records.rework_order_id    → rework_orders.id        （11 → 14，前向外键）
```

> **如实核对 `tables.md` 口径**：`.team/notes/backend-dev/stage1-backend-spec-tables.md` §0.1/§18 将 `dispatch_records` 排在物理序 `11`、`rework_orders` 排在 `14`（`11. dispatch_records → 12. repair_processes → 13. material_usages → 14. rework_orders → 15. acceptance_records`），§11 定义 `dispatch_records.rework_order_id`（`bigint NULL`）为 `FK → rework_orders`（未显式标注 `ON DELETE`，PG 默认 `NO ACTION`），但 `tables.md` §0.1 L27 与 §0.2 仅处置了 `rework_orders ↔ acceptance_records` 二元循环，未单列该前向 FK 的延迟步骤。为保持 `1..17` 物理序一致并消除前向 FK 断链，迁移必须按如下方式一并处理：

- **ORM 层（`create_all()` 场景）**：`rework_orders.source_acceptance_id` 写 `ForeignKey("acceptance_records.id", ondelete="RESTRICT")`，`dispatch_records.rework_order_id` 写 `ForeignKey("rework_orders.id")`，**均严禁加 `use_alter=True`**——① SQLAlchemy `MetaData.create_all()`/`drop_all()`（配合具名 `naming_convention`）已内置拓扑解环（`https://docs.sqlalchemy.org/en/20/core/constraints.html`）；② 在 Alembic 中一旦模型加了 `use_alter=True`，`op.create_table(..., ForeignKeyConstraint(..., use_alter=True))` 会**静默跳过外键创建**（退出码为 0 但库中无 FK，见 Alembic issues #326/#387/#626）。
- **迁移层（Alembic 唯一权威做法，方案 B：inline + 后补 `op.create_foreign_key`）**：
  1. 建 `dispatch_records`（物理序 11）时，`rework_order_id` **先只建可空 `bigint` 列，不在 `op.create_table` 内联声明 FK**（因 `14. rework_orders` 尚不存在，内联建 FK 会直接报错 `relation "rework_orders" does not exist`）；
  2. 建 `rework_orders`（物理序 14）时，`source_acceptance_id` 列**先只建列（+ 索引），不带 FK 约束**（因 `15. acceptance_records` 尚不存在）；
  3. 再建 `acceptance_records`（物理序 15），其 `rework_order_id` **随建表 inline 声明 FK** `REFERENCES rework_orders(id) ON DELETE RESTRICT`（`rework_orders` 此时已存在），由 `MetaData.naming_convention` 生成约束名 `fk_acceptance_records_rework_order_id_rework_orders`；
  4. `rework_orders`（14）与 `acceptance_records`（15）均建成后，显式调用 `op.create_foreign_key()` 补齐两条延迟 FK——**1 条补齐循环反向 FK**（构成方案 B 双向共 2 条），**1 条补齐前向 FK**（消除 `11 → 14` 断链）：
     ```python
     # (1) 方案 B：补齐 rework_orders → acceptance_records 循环反向 FK（1 条）
     op.create_foreign_key(
         "fk_rework_orders_source_acceptance_id_acceptance_records",
         "rework_orders", "acceptance_records",
         ["source_acceptance_id"], ["id"], ondelete="RESTRICT",
     )
     # (2) 补齐 dispatch_records → rework_orders 前向 FK（tables.md §11 未指定 ON DELETE，默认 NO ACTION）
     op.create_foreign_key(
         "fk_dispatch_records_rework_order_id_rework_orders",
         "dispatch_records", "rework_orders",
         ["rework_order_id"], ["id"],
     )
     ```
  5. `downgrade` 顺序反向：先 `op.drop_constraint` 删除 `fk_rework_orders_source_acceptance_id_acceptance_records` 与 `fk_dispatch_records_rework_order_id_rework_orders`，再按 `15. acceptance_records` → `14. rework_orders` → `13. material_usages` → `12. repair_processes` → `11. dispatch_records` 逆序删表。
- **迁移后硬证据**（不可只看 `alembic upgrade head` 退出码 0）：

```sql
-- ① 方案 B 循环外键校验（期望：恰好 2 行 = 1 条 inline + 1 条后补 op.create_foreign_key）
SELECT conname, conrelid::regclass, confrelid::regclass
FROM pg_constraint
WHERE contype = 'f'
  AND conname IN (
    'fk_acceptance_records_rework_order_id_rework_orders',
    'fk_rework_orders_source_acceptance_id_acceptance_records'
  );

-- ② 前向补齐 FK 校验（期望：恰好 1 行，证明第 11 步未静默丢外键）
SELECT conname, conrelid::regclass, confrelid::regclass
FROM pg_constraint
WHERE contype = 'f'
  AND conname = 'fk_dispatch_records_rework_order_id_rework_orders';
```

#### 迁移期 5 条数据完整性断言（写进首个迁移 `upgrade()` 末尾，任一失真即回滚）

```sql
-- 断言 1：点位区域一致性（守 fault_tickets.region_id 冗余列；期望 0 行）
SELECT t.id, t.region_id, p.region_id
FROM fault_tickets t JOIN lighting_points p ON p.id = t.lighting_point_id
WHERE t.region_id <> p.region_id;

-- 断言 2：派工技术员当时具备该区域 REPAIR 授权（守 dispatch_records.region_id 与因果链；期望 0 行）
SELECT d.id
FROM dispatch_records d
LEFT JOIN scope_authorizations s
  ON s.id = d.scope_authorization_id
 AND s.account_id = d.technician_account_id
 AND s.region_id = d.region_id
 AND s.scope_type = 'REPAIR'
 AND (s.valid_from IS NULL OR s.valid_from <= d.dispatched_at)
 AND (s.valid_to   IS NULL OR s.valid_to   >= d.dispatched_at)
WHERE s.id IS NULL;

-- 断言 3：非终态票最多 1 条活跃派工（期望 0 行）
SELECT ticket_id, count(*) FROM dispatch_records
WHERE status IN ('PENDING_ACCEPT','ACCEPTED')
GROUP BY ticket_id HAVING count(*) > 1;

-- 断言 4：rework_count == 返修单行数（守 fault_tickets.rework_count 冗余列；期望 0 行）
SELECT t.id, t.rework_count, count(r.id) AS actual
FROM fault_tickets t LEFT JOIN rework_orders r ON r.ticket_id = t.id
GROUP BY t.id, t.rework_count
HAVING t.rework_count <> count(r.id);

-- 断言 5：17 张基础表全部存在（排除 alembic_version；期望 = 17）
SELECT count(*) FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
  AND table_name <> 'alembic_version';
```

#### 四张被合并候选表（解释为什么是 17 表而非 21 表）

| 候选表 | 合并落点 | 理由 |
|---|---|---|
| `idempotency_keys` | → `audit_logs.idempotency_key` + `uq_audit_logs_idempotency` + 事务级咨询锁 | 生命周期与审计重合；`audit_logs` 严格 **append-only**（仅 `2xx` 插入 1 行，无占位行、无 `UPDATE`、无 `updated_at`）；并发竞争由 `pg_try_advisory_xact_lock(hashtextextended(..., 0))` + 锁内双重读取串行裁决（争用直接返 `409 IDEMPOTENCY_REQUEST_IN_PROGRESS`），最终由部分唯一索引兜底，**不需两阶段写入或占位行** |
| `sessions` / `refresh_tokens` | **不建表**（无状态 JWT + `accounts.token_version`） | 改密/禁用实现 O(1) 吊销，无需会话清理任务 |
| `technician_skills` | → `accounts.skills text[]` | 技能属账号属性，无独立生命周期 |
| `sla_alerts` | → `fault_tickets.is_sla_overdue`/`overdue_stage` + `notification_tasks` | 超时是工单属性与通知任务，避免两份真相 |

#### PG 类型选型与清理纪律

| 类别 | 选型 | 理由与验证 |
|---|---|---|
| 主键 | `bigserial` | 单机顺序递增，避免 UUID 索引膨胀 |
| 时间 | **`timestamptz`**（禁用 `timestamp`，`MUT-14`） | 跨时区偏移不丢失；迁移后需核 `information_schema.columns` `data_type` 全为 `timestamp with time zone` |
| 数量/精度 | **`numeric(p,s)`**（禁用 `float`/`double`，`MUT-13`） | 避免浮点误差破坏 `CHECK` 与统计 |
| 半结构化 | `jsonb`（非 `json`） | 支持路径运算符 `->>` 与索引 |
| 枚举 | `varchar` + `CHECK`（不用 PG `ENUM`） | 规避 `ALTER TYPE` 不可逆迁移痛点 |
| 技能数组 | `accounts.skills text[]`（`D-26` 不建 GIN 索引） | 百级运维人员表全表扫成本更低，千级规模再加 GIN |
| 孤儿文件清理 | 每日一次清理任务（`D-25`） | 扫描 `uploads/` 中无对应 `fault_evidences` 行且 `mtime > 24h` 文件并删除，批大小 `500` |

> **PG 17.12 实测抽验注记**：`backend-dev` 已在临时数据库 `scratch_backend_dev_sqlcheck` 上实跑验证 `uq_fault_tickets_active_point_type`（`23505`）、`ck_fault_tickets_inspector_confirmation_serialized`（`23514` 三态绑定）与 `I2` 跨表检测器（`0` 行正常 / 注入违规返回 `1` 行）；全量 17 表 DDL 迁移尚未在真库执行。

---

## 5. 视觉分析、训练与推理管线

### 5.1 媒体四层白名单、昼夜亮度判定与候选输出契约

#### 媒体四层白名单（服务端 Pillow + `tomori.vision` 纵深防御）

- **请求规范**：`POST /tickets/ai-candidates` 接收 `multipart/form-data` `files`（**`1..10` 张**，`D-03`；`0` 张 `422 NO_FILES`、`>10` 张 `422 TOO_MANY_FILES`），逐图独立分配 `analysis_id` 处理，单批不合并图像推理。
- **四层门禁与错误优先级**（完整 12 步处理顺序与 5 步优先级见 `docs/API.md` §5）：
  1. **体积上限**：单文件 `<= 15 MiB`（`15,728,640` B，`D-02`），超限 → `413 IMAGE_SIZE_EXCEEDED`。
  2. **魔数校验**（不信 `Content-Type`）：仅放行 JPEG（`FF D8 FF`）、PNG（`89 50 4E 47 0D 0A 1A 0A`）、WebP（`RIFF....WEBP`）；非白名单格式/伪装扩展名/解码失败 → `415 UNSUPPORTED_IMAGE_FORMAT`。
  3. **多帧与视频流分离拒绝（RFC 9110 §15.5.16）**：
     - **多帧动图**（APNG `acTL`、WebP `n_frames > 1`、`.gif`）归格式问题 → **`415 UNSUPPORTED_IMAGE_FORMAT`**；
     - **视频容器或实时流**（MP4/MKV/AVI/FLV 魔数、`video/*` MIME、`rtsp://`·`rtmp://`·`http(s)://*.m3u8`·`stream_url`·`camera_*` 字段）归红线告警 → **`415 MEDIA_VIDEO_STREAM_FORBIDDEN`**。
  4. **像素防炸弹**：解码前读头校验总像素 `<= 40,000,000`（`Image.MAX_IMAGE_PIXELS` 禁设 `None`，将 `DecompressionBombWarning` 转为错误）、单边 `<= 12,000`，超限 → `422 IMAGE_PIXEL_LIMIT_EXCEEDED`；随后执行 `ImageOps.exif_transpose()` 纠正手机竖拍方向并转 `np.ndarray`（绝不把原始 URL/字符串传给 `model.predict(source=...)`）。

#### 昼夜亮度判定（`D-31`）与 `streetlight_on` 回传定案（`D-30`）

- **昼夜判定不依赖 EXIF 拍摄时间（`D-31`）**：因 EXIF 可缺失、篡改或时区错乱，仅根据 OpenCV HSV `V` 通道（8-bit BGR→HSV 下 `H ∈ [0,179]`、`S,V ∈ [0,255]`；若走 `cv2.imdecode` 须同时前置判空 0 字节 buffer 防 `cv2.error: !buf.empty()` 并检查返回 `None`）整图与顶部 1/3 天空区域亮度特征判定 `scene_lighting` ∈ `day`/`twilight`/`night`（工程初值如 `v_median`/`sky_v_median`/`roi_v_p95`/`luminance_contrast` 待阶段二真实样本标定）。
- **白天灭灯抑制（`DAYTIME_UNPOWERED_EXPECTED`）**：`scene_lighting == "day"` 下检出 `streetlight_off` 时，置 `candidate_fault_type=null`、`is_fault_candidate=false`、`fusion_status="daytime_unpowered_suppressed"`；若全批图无任何 `is_fault_candidate=true`，不建故障单、不锁点位，仅将证据写入 `fault_evidences`（`ticket_id=NULL`）并返回 `200 {ticket: null, evidences: [...]}`。
- **✅ `D-30` 已定案：夜间正常亮灯 `streetlight_on` 回传前端作参照框**：用户已明确裁决「回传」。夜间/黄昏检测到正常亮灯 `streetlight_on` 时，**回传至前端 `candidates[]`**：置 `candidate_fault_type=null`、`visual_signal_state="NORMAL_LIT"`、**`is_fault_candidate=false`**、`suggested_severity_hint="none"`。巡查员可在图上明确区分「**AI 看到且判定正常亮灯**」与「**AI 未检出**」，避免漏报误判。

#### 检测标签 → 业务枚举映射与 `FaultCandidateResult`

> **`nc=3`/`occluded` 弃用口径（`D-41`/`task-165`/`merged_v7`）**：`merged_v7` 起训练集为 `nc=3`（`names = [streetlight_on, streetlight_off, streetlight_damaged]`，`occluded` 由 `d1` 独源 `235` 框（`merged_v5` 合并 `227` 框，`8` 去重损失）约 `95%` 错标 + 自证救回率 `~22%` 弃用——`D-41`）；下表 `streetlight_occluded` 行保留**仅为 `API` `fault_type=OCCLUDED` 手工报障兼容**（`merged_v7`/`streetlight_v3` 模型不产出该 `raw_class`，`mapping.py:164-167, 352-353` 分支与 `contract.py:20-23` 字面量仅契约兼容保留）。

| YOLO `raw_class` | 场景 | `candidate_fault_type` | `visual_signal_state` | `is_fault_candidate` |
|---|---|---|---|---|
| `streetlight_on` | `night` / `twilight` | `null` | `NORMAL_LIT` | **`false`**（`D-30` 回传参照框） |
| `streetlight_on` | `day` | `OTHER` | `DAYTIME_ALWAYS_ON_SUSPECT` | `true`（白天疑似常亮提示） |
| `streetlight_off` | `night` / `twilight` | `LAMP_OUT` | `LAMP_OUT` | `true` |
| `streetlight_off` | `day` | `null` | `DAYTIME_UNPOWERED_EXPECTED` | **`false`**（白天正常灭灯抑制） |
| `streetlight_damaged` | 昼 / 夜 | `FIXTURE_DAMAGED` | `FIXTURE_DAMAGED` | `true` |
| `streetlight_occluded`（已弃用，仅手工报障兼容） | 昼 / 夜 | `OCCLUDED` | `OCCLUDED` | `true` |

> **输出契约核心字段**：每图独立 `FaultCandidateResult` 存 `fault_evidences.ai_result_json`，同时提供 `bbox_norm`（`0.0..1.0` 供前后端多端缩放渲染）与 `bbox_px`（像素坐标），恒注入 `requires_inspector_confirmation=true`、`replaces_electrical_safety_check=false` 与 `disclaimer`；当采用 `baseline_pretrained_opencv` 模式时恒置 `ai_is_baseline_mode=true` 且 `max_fused_confidence <= 0.65`。

---

### 5.2 智能安全边界（V1–V3）与视觉降级契约（D1–D3）

#### 三条智能安全边界（V1–V3，不可协商）

| # | 边界要求 | 三层强制落点（前端 / 服务层 / DB） |
|---:|---|---|
| **V1** | AI 候选**必须**经巡查员确认才能流转，无任何自动化绕过路径 | 前端派工按钮 `disabled` + `422 AI_CANDIDATE_NOT_CONFIRMED` + DB `ck_fault_tickets_inspector_confirmation_serialized` |
| **V2** | 模型识别**绝不能替代**现场电气安全检查（6 项 checklist） | 前端完工按钮 `disabled` + `422 ELECTRICAL_SAFETY_CHECK_REQUIRED` + DB `ck_repair_processes_safety_checklist_complete` + `ck_fault_evidences_ai_safety_flags_required` |
| **V3** | 静态图片视觉模型**永不产出** `FLICKERING`（需多帧时序）或 `ELECTRIC_HAZARD`（需现场检测） | Pydantic `FaultCandidateResult` 校验 + 人工改型需 `manual_note` + DB `ck_fault_tickets_ai_source_excludes_unsupported_types` |

#### 视觉降级契约（D1–D3）与三条互斥 `503`

| 契约 | 硬约束 |
|---|---|
| **D1** | **`POST /tickets`（人工报障，支持 JSON 与 `multipart/form-data` `0..10` 张图）永不依赖视觉可用性**：即使 `tomori.vision` import 失败、权重缺失或 `YOLO_INFERENCE_ENABLED=false`，必须始终返回 `201`（`report_source='MANUAL_*'`）；`src/tomori/vision/client.py` 必须在函数内延迟 `import tomori.vision`，禁止顶层裸导入 |
| **D2** | **`POST /tickets/ai-candidates` 视觉失败时整笔回滚**：`fault_tickets` 新增 `0` 行、`fault_evidences` 新增 `0` 行，**且已写入磁盘的临时/上传文件必须删除**（防止磁盘泄漏与部分失败假绿） |
| **D3** | **错误携带可操作兜底引导**：必须返回 `error.details.fallback.action == "MANUAL_REPORT"`、`resubmit_required == true`、非空 `message_key`（`vision.disabled.manual_fallback` / `vision.timeout.manual_fallback` / `vision.failed.manual_fallback`），**且不含 `evidence_ids`**（文件已回滚删除，兜底时前端携原文件走 `POST /tickets` 重投）。前端锚点 `data-testid="inspection-vision-disabled"`（`web/src/views/inspection/InspectionReportView.vue:660`，并额外携带可测属性 `data-code`〔`VISION_DISABLED` 等〕与 `data-fallback-action="MANUAL_REPORT"`） |

| 触发场景 | HTTP | `error.code`（三条互斥） | `details.analysis_stage` |
|---|---:|---|---|
| `YOLO_INFERENCE_ENABLED=false` 或权重不可用（含 `strict_custom` 门禁未通过） | `503` | `VISION_DISABLED` | `CONFIG_CHECK` |
| 单次推理超过 `VISION_TIMEOUT_SECONDS`（固定 `60`s，**后端代码常量，不入 `.env`**） | `503` | `VISION_TIMEOUT` | `INFERENCE` |
| 视觉解码 / 推理 / 后处理抛出异常 | `503` | `VISION_INFERENCE_FAILED` | `DECODE` / `INFERENCE` / `POSTPROCESS` |

#### 五项核心红线（N1–N5，明确「不做」）

| # | 不做 / 红线 | 依据与技术防线 |
|---:|---|---|
| **N1** | **不接实时监控** | `415 MEDIA_VIDEO_STREAM_FORBIDDEN`（独立统计红线告警，与 `UNSUPPORTED_IMAGE_FORMAT` 分界）；无 RTSP/流媒体依赖 |
| **N2** | **不做远程灯控**（开关灯/调光/继电器/PLC） | 无 MQTT/IoT 客户端及控制端点；静态扫描正则含 `灯控\|开关灯\|调光\|继电器\|PLC` |
| **N3** | **不做能耗/电量预测** | 无时序预测库与能耗表；`facility_assets.rated_power_w` 仅铭牌属性不参与计算 |
| **N4** | **不跳过巡查员确认** | V1 三层强制（前端 `disabled` + `422 AI_CANDIDATE_NOT_CONFIRMED` + DB 三态串行化 `CHECK`） |
| **N5** | **不跳过电气安全检查** | V2 三层强制（前端 `disabled` + `422 ELECTRICAL_SAFETY_CHECK_REQUIRED` + DB 六件套 `CHECK`） |

---

### 5.3 模型选型、远端 GPU 训练与本地推理边界

#### 模型与分辨率唯一锁死（`D-06` + `D-28`）

- **唯一底模：`yolo26s.pt`（官方 `20,422,725` B，禁用 `n`/`m`/`l`/`x` 及架构变体）**。4 条一手出处：① Ultralytics 模型主页 `https://docs.ultralytics.com/models/yolo26`（`released January 2026`，Detection 含 `yolo26n/s/m/l/x.pt`）；② 本项目锁定 `ultralytics==8.4.216` 源码内含 `ultralytics/cfg/models/26/yolo26.yaml`；③ `downloads.py` L26 checkpoint 列表；④ GitHub `assets/releases/tag/v8.4.0` 可直下权重 `20,422,725` B。
- **唯一输入尺寸：`imgsz=1280` 四端严格一致（`D-28`）**：官方默认 `640`，本项目经 D1 全量只读 EDA（🟢 8,428 图，其中 `81.86%` / 6,899 张为 `1280×720`、`86.64%` 宽 `>=1280`）实测支撑，将工程初值由 `1024` 升级锁定为 **`1280`**（`1280=2×640`，对主力 `1280×720` 原图实现 `1:1` 零下采样、100% 保留原生像素，并将 `<16px` 极小灯头框占比由 `14.72%` 降至 `5.81%`，详见 `docs/TRAIN.md` §2.2）。**训练（`imgsz=1280`）→ 导出（`export(format="onnx", imgsz=1280, nms=False, dynamic=False)`）→ 配置（`.env` `YOLO_IMG_SIZE=1280`）→ 本地 `onnxruntime.InferenceSession` CPU 推理（启动期核验 `sess.get_inputs()[0].shape == [1, 3, 1280, 1280]`、端到端无 NMS 输出 `[1, 300, 6]`；`InferenceSession.run()` 直接推理时尺寸不符即抛 `INVALID_ARGUMENT`，同时本地运行环境设 `YOLO_AUTOINSTALL=False`）四端必须同值**。训练时同时保留原始双头 `best.pt` 与 `best_e2e.onnx`（`nms=False`）。

#### 远端 GPU 开发训练 vs 本地仅推理职责边界（`D-01` + `D-06` + `D-27`）

| 运行端 | 实测环境与唯一目录 | 允许工作 | 明确禁止 |
|---|---|---|---|
| **远端 GPU 服务器**（`ssh -p 13142`，个人远端机器） | `RTX 4080 SUPER / 32760 MiB`（驱动 `595.71.05` / CUDA `12.8`）；**统一落盘 `/root/autodl-tmp/tomori/`**（230G，根分区余 7.8G **零增长**）；使用预装绝对路径 **`/root/.local/bin/uv` `0.13.7`**（无需重装，详见 `docs/RUNBOOK.md` §6.1.1–§6.1.2） | 视觉代码开发、数据集准备、训练（先以官方 `batch=-1` AutoBatch 探测约 60% 显存后再向下取 2 的幂锁死；`epochs=500, patience=20`，配合 `early_stopping_diagnostics` 与可选 `--mosaic-polish-epochs`，详见 `docs/TRAIN.md` §3）、离线评估与 ONNX 导出 | 禁占根分区 `overlay`；禁换用 `n/m/l/x`；禁通过 git 传大数据集或权重 |
| **本地研发/运行机** | `./vision/weights/` + 进程内 `import tomori.vision` | `SHA256SUMS` 校验、权重启用门禁（`docs/RUNBOOK.md` §6.6）、CPU/CUDA 本地推理验证与 FastAPI 联调 | **严禁本地训练、严禁本地开发训练脚本、严禁「本地 CPU 冒烟」路径** |

- **基线回退策略（权重未回传时）**：`YOLO_INFERENCE_MODE=auto` 下允许启用 `baseline_pretrained_opencv`（真实执行 Layer 0 魔数/尺寸 + Layer 1 清晰度/昼夜亮度 + Layer 3 ROI 物理启发式提取与画框，**严禁返回硬编码假 JSON**），必须置 `ai_is_baseline_mode=true` 且置信度受 DB `CHECK` 封顶 **`0.65`**；`strict_custom` 模式下缺权重或门禁不通过一律返 `503 VISION_DISABLED`。

#### ✅ `src/tomori/vision/` 11 个模块已落盘（新增只读 EDA `eda.py`，另有 `dataset_prep.py`、`trainer.py`）与阶段二修复状态（`F14` 诚实声明）

原根级 `vision/` 下的 8 个预研草稿（`__init__.py`、`config.py`、`contract.py`、`errors.py`、`image_io.py`、`metrics.py`、`whitelist.py`、`baseline.py`）**已于阶段二迁入 `src/tomori/vision/`，并新增 `client.py`、`mapping.py` 与只读数据集 EDA 工具 `eda.py`（共 11 个模块；另有数据准备与训练流水线模块 `dataset_prep.py`、`trainer.py`，目录内共 13 个 `.py` 文件），原根级 `vision/*.py` 已移除、仅留空目录 `vision/` 供运行时 `weights/` 与 `reports/` 产物（gitignored）**。阶段二实现期已完成下列 5 项修复与补充（待随全链路 pytest / 远端训练产物继续实测验收）：
1. **`src/tomori/vision/errors.py`**：已删除私有码 `VISION_WEIGHTS_NOT_READY`，统一改为 `VISION_DISABLED`（`503`），并补齐 `VISION_TIMEOUT` / `VISION_INFERENCE_FAILED` / `TOO_MANY_FILES` / `NO_FILES` / `INTERNAL_ERROR` 及 `vision.*.manual_fallback` key；
2. **`src/tomori/vision/whitelist.py`**：已将 `n_frames != 1` 多帧动图由 `MEDIA_VIDEO_STREAM_FORBIDDEN` 更正为 `415 UNSUPPORTED_IMAGE_FORMAT`（对齐 `docs/API.md` §3.5）；
3. **`src/tomori/vision/config.py` & `src/tomori/vision/contract.py`**：已补全 `SEVERITY_HINTS` / `SeverityHint` 的 `"P4"`、`EXPECTED_YOLO_IMG_SIZE = 1280` 及基线模式 `BASELINE_CONFIDENCE_CAP = 0.65` 校验；
4. **`src/tomori/vision/client.py` & `src/tomori/vision/mapping.py`**：已落地函数内延迟重依赖导入（保证纯后端无 `--extra vision` 可安全 import `tomori.vision`）与标签→业务枚举映射及 `streetlight_on` 参照回传（`D-30`）；
5. **`src/tomori/vision/eda.py`（新增只读数据集 EDA 工具，另有 `dataset_prep.py`、`trainer.py`）**：只读统计数据集分辨率/长宽比/体积、YOLO 标注框尺度分布与 COCO 尺度桶、HSV `V` 昼夜亮度及 `video_id` 分组规模，并定量对比 `1024 vs 1280` 小目标保留率（模块顶层仅导入标准库、遵循 `D1` 延迟导入且绝不写/改数据集源文件；`dataset_prep.py` 与 `trainer.py` 详见 `docs/TRAIN.md` §2–§3）。

#### 数据集定案（`D-29` ✅ 已裁定）与 D1–D7 真实元数据备查表

> **✅ `D-29` 用户最终裁决**：「所有数据集不用考虑合规性，直接使用。这是我做着玩的项目，所有代码都只会在本机。不用管。」
> **本系统落地结论**：本项目为个人本地研究与练手项目（业务与推理均在本机运行，仅模型训练按 `D-06` 在个人远端 GPU 执行，不对外商用分发），**所有公开数据集均可直接用于阶段二训练与实验，无需向作者发函申请书面商用授权**。同时严格遵守 **`F14` 事实纪律**：下表如实记录 D1–D7 的真实来源、原始许可证/无声明元数据与技术数据缺陷（不虚构开源许可，也不阻塞使用）。

| 编号 | 数据集 ID / 来源 | 真实许可证元数据（🟢 实测） | 规模与类别 | 技术使用注意（与许可无关的数据质量事实） |
|---|---|---|---|---|
| **D1** | `ngocleltt/lamp-state-detection` (`https://huggingface.co/datasets/ngocleltt/lamp-state-detection`) | `cc-by-4.0`（保留原作者署名元数据备查） | 8,428 张 `1280×720` JPG（`lamp_on`/`lamp_off`/`lamp_occluded`） | `metadata.csv` 含 `video_id` 连续抽帧，**必须按 `video_id` 分组切分**防泄漏（`70%/15%/15%`）；无破损类 |
| **D2** | `WHU-USI3DV/WHU-Infra3D` (`https://github.com/WHU-USI3DV/WHU-Infra3D`) | 仓库无 `LICENSE` 文件、README 未声明许可 | 5,449 张 `8192×4096` 全景图 / 175,021 个 2D 框（含 `Street Light` `label_id=2` 及中文状态/破损属性） | 全景图需裁切适配；需通过仓库问卷获取下载链接 |
| **D3** | Roboflow `jeong-yeonsu/streetlamppole` (`https://universe.roboflow.com/jeong-yeonsu/streetlamppole`) | 页面未标注 License 标签 | 993 张图像（`Damaged` 1 类） | 仅含破损单类，无亮灭标签 |
| **D4** | Roboflow `godspeed-yqpeo/damaged-lights` (`https://universe.roboflow.com/godspeed-yqpeo/damaged-lights`) | 页面未标注 License 标签 | 619 张图像（`Working`/`Not Working` 2 类） | 名字含 damaged 但实际仅亮灭 2 类 |
| **D5** | `Team16Project/Street-Light-Dataset` (`https://github.com/Team16Project/Street-Light-Dataset` / `arXiv:2407.01117`) | 仓库无 `LICENSE` 文件 | `Raw Dataset` 含 529+291=820 静态图与 `Flicker/` 22 `.mp4`（去重 20 个）；`Resized Dataset/` 443+270 张小图 | 仅整图目录分类（无检测框），5 个 README 为占位说明；**`.mp4` 视频必须剔除**，仅静态图可参考 |
| **D6** | `AnantRajput415/Nighttime-Street-Light-Functional-Audit-...` | 未声明许可 | 仓库本体无数据文件（仅 HTML + 2 Notebook；README 外链 Google Drive 未核验） | 仅作低照度预处理（CLAHE+Gamma+Unsharp）与按路段防泄漏方法论参考 |
| **D7** | Roboflow `poles-defect/damaged_pole` v2 (`https://universe.roboflow.com/poles-defect/damaged_pole/dataset/2`) | 页面未标注 License 标签 | Train 2,400 / Valid 182 / Test 73（杆体缺陷） | 含 `3×` 预增强及 `640×640` 拉伸，**引入前须先按 64-bit pHash 去重防泄漏** |

- **辅助来源备查**：`hotosm/streetlevel-poles`（`cc-by-sa-4.0`，3.5K `pole`/`tower` 仅辅助定位杆体）；COCO（标注 `CC-BY-4.0`、图像归 Flickr 原作者 ToU 约束，仅含 `traffic light` 无路灯，只作预训练基座/背景负样本）。
- **数据增强禁区与 `D-18` 承诺边界**：训练集严禁上下翻转（`flipud=0.0`），控制亮度/饱和度扰动（`hsv_v <= 0.25`, `hsv_s <= 0.4`）以免把 `streetlight_off` 洗白为发光状态；`val`/`test` 严禁混入合成增强样本。因破损类 `FIXTURE_DAMAGED` 仍缺高质量全场景公开框标注、须阶段二结合自采与受控增强迭代，**`D-18` 已裁定阶段一不承诺破损检出率指标**。

---

## 6. 质量门禁与阶段二验证基线

### 6.1 静态检查、构建门禁与首日实测项

#### 静态与构建门禁（全部正交，禁止合并单命令）

| 工具 / 命令 | 作用域与规则 | 当前状态 / `D-08` 口径 |
|---|---|---|
| `uv run ruff check src tests` | Python 代码质量与静态缺陷检查（**与下一条正交，必须独立运行**） | 阶段二应用代码落地起硬门禁 |
| `uv run ruff format --check src tests` | Python 代码格式一致性检查 | 同上 |
| `uv run mypy src/tomori` | Python 静态类型检查（覆盖 `AsyncSession` / `Mapped[]` / Pydantic） | **`D-08` 工程定案**：阶段一未预装未运行；阶段二初期只报告不阻塞，存量清零后转硬门禁 |
| `pnpm --dir web typecheck` | 运行 `vue-tsc --noEmit`（锁 `typescript@6.0.3`，防 TS7 崩溃） | 阶段二 `web/` 初始化起硬门禁 |
| `pnpm --dir web lint` / `pnpm --dir web build` | 前端规范校验与生产构建验证 | 同上 |
| `uv run alembic -c alembic.ini upgrade head` | 根级 `alembic.ini` 执行 `migrations/` 全量异步迁移（需配 §4.1/§4.5 实测 SQL 核对） | 阶段二 `migrations/` 落地起硬门禁 |
| `uv run pytest tests` | Python 单元与集成测试（`tests/unit/` + `tests/integration/`；E2E 见下行独立串行门禁） | 阶段二 `tests/` 初始化起硬门禁 |
| `uv run pytest tests/e2e` | **Python `pytest-playwright`**：必须核验 `collect N == junit tests N == passed N == artifacts N`，单进程串行并受 `claim-e2e` 锁保护 | 见 `docs/team/TEAM.md` §6 & `docs/RUNBOOK.md` §4 |

#### 阶段二首日必跑实测项

1. **`D-20` / `D-21` / `D-40` Chromium 实测闭环**：在真实 Chromium 下验证 A/B/C 三类代表组件的 `fill()`/`check()`/`Teleport` 挂载、验证 `el-option` 属性是否真透传至选项 DOM、验证 `el-upload` 内部 `<input type="file">` 定位（不可控则立即改用原生 `<input type="file">` 外包封装）。
2. **`D-22` OpenCV 5.x 兼容实测**：验证 `opencv-python-headless==5.1.0.95` 在 `src/tomori/vision/metrics.py`、`src/tomori/vision/baseline.py`（`cv2.imread`/`cvtColor`/`Laplacian`/`Canny`/`connectedComponentsWithStats`）的返回值与签名兼容性；若出现破坏性变更则降锁 4.x 末版。
3. **17 表真 PG 17.12 迁移对账**：在 `tomori_test` 首次执行 `uv run alembic -c alembic.ini upgrade head` 后，跑通 §4.1 5 条计数 SQL、§4.5 循环外键 `pg_constraint` 查询与 5 条迁移期断言。

---

### 6.2 防假绿变异测试基线（27 行 / 30 实验）

参照 `docs/team/TEAM.md` §4.1–§4.6 反假绿体系（现象唯一定义：**变异后仍全绿**；原因分 **A–G 七形态 + `no-op`**，其中 `F14` 为「断言必须附出处」纪律而非形态编号）。阶段二核心守卫测试完成后，必须逐一注入以下 **27 行（含 `MUT-01b..e` 4 个 reason，共 30 个可执行实验）** 单点变异并验证转红：

| 变异分组 | 卡片 ID | 守卫目标与关键要求 |
|---|---|---|
| 区域派单不合格原因（5 实验） | `MUT-01a` / `MUT-01b..e` | 逐项短路 `NO_REGION_SCOPE` / `SCOPE_EXPIRED` / `ACCOUNT_INACTIVE` / `WRONG_ROLE` / `OVERLOADED`（**一次只动 1 个 reason**，防止 A 名字骗人） |
| RBAC 越权与零脏写 | `MUT-02` | `require_role` 短路后验证 `403` 变 `200` 且四表零脏写断言同步转红 |
| AI 候选未确认禁派工（V1） | `MUT-03a` / **`MUT-03b`** | `MUT-03a` 短路服务层守卫；`MUT-03b` **删除 DB 约束 `ck_fault_tickets_inspector_confirmation_serialized`**（独立穿透验证 DB 兜底） |
| 电气安全六件套（V2） | `MUT-04a` / `MUT-04b` / **`MUT-04c`** | `MUT-04a` 改 `all(...) → any(...)`（必配逐项缺失正反例）；`MUT-04b` 清空 `details.missing_items`；`MUT-04c` **删除 DB 约束 `ck_repair_processes_safety_checklist_complete`** |
| 确定性规则分级（`D-13`） | **`MUT-05a`** / **`MUT-05b`** | `MUT-05a` 短路 `return "P3"`（144 组合静态真值表转红）；`MUT-05b` 倒序规则或将 `R1` `"any"` `OR→AND`（必配双用例封死求值序与逻辑算子） |
| 返修闭环（`D-14`） | **`MUT-06a`** / **`MUT-06b`** | `MUT-06a` 置 `create_rework(...) → None` 测**单事务四件事**；`MUT-06b` 改 `round_no > max` → `>=`（必测 `== max` 与 `== max+1` 双边界） |
| 幂等与点位防重 | `MUT-07a` / `MUT-07b` / `MUT-07c` | `MUT-07a` 跳过幂等查询；`MUT-07b` 跳过活跃单预检靠部分唯一索引拦截；`MUT-07c` 重放分支错调重新执行导致 `notification_tasks` `+2` |
| 媒体白名单与白天抑制 | `MUT-08a` / `MUT-08b` / `MUT-08c` | 魔数短路、白天灭灯抑制短路、错误码映射 `UNSUPPORTED_IMAGE_FORMAT: 415 → 422` |
| 状态机与前端/事务边界 | **`MUT-09`** / `MUT-10` / `MUT-11` / `MUT-12` | `MUT-09` 扩放白名单（含 `IN_REPAIR → REWORK_PENDING_DISPATCH` 与非法取消）；`MUT-10` 前端 `:disabled="false"`；`MUT-11` 删完修行计数断言；`MUT-12` `DUPLICATE_ACTIVE_TICKET: 409 → 422` |
| PG 强类型与无主证据守卫 | **`MUT-13`** / **`MUT-14`** / **`MUT-16`** | `MUT-13` 丢 `numeric(10,3)` 精度；`MUT-14` `repair_deadline_at` 转字符串比较丢时区偏移（**仅真 PG 可检，SQLite 必假绿**）；`MUT-16` 改 `ck_fault_evidences_ticket_or_orphan_inspection` `AND→OR`（必配双负例） |
| 郑州 48h SLA 守卫（`D-11`） | **`MUT-15`** / **`MUT-17`** | `MUT-15` 删 ② 法定 48h 维修养护分支 `AND is_sla_overdue = false` 守卫（**连续两次 `scan` 第二次才转红**）；`MUT-17` 守卫 `P4` 观察不免除 48h 且 `repair_deadline_at` 必自 `T_discover` 起算 |

> **6 步留证硬纪律（缺一不可）**：① baseline `exit=0`；② `SHA_BEFORE`；③ 单点注入后红 `exit≠0`；④ 失败测试名；⑤ `RESTORED OK sha256`（备份目录必须为 `.team/notes/tester/mut-backup/`，**禁止 `/tmp`**）；⑥ 恢复后 `exit=0`。对于修改 DB 约束的 `MUT-03b` / `MUT-04c` / `MUT-16`，须用 `\d+` DDL 前后 diff 为空替代文件 SHA256（详见 `docs/team/TEAM.md` §4.6）。

---

## 7. 待用户裁决清单 → 见 `docs/DECISION.md`

全项目 **40/40** 裁决项已**全部落定**（`20 ✅ / 0 🔴 / 0 🟡 / 20 🟢`，零待决项）。本文件原三源技术与业务裁决指针已全部并入 **`docs/DECISION.md`**（全项目唯一权威裁决清单，本文不维护第二套长表）：

| 原编号 | 原归属域（本文件对应节） | `docs/DECISION.md` | 最终状态 |
|---|---|---|---|
| `TS-1` | 后端 ORM 锁 2.0 线（本文 §1.2） | **`D-05`** | ✅ 已裁定：维持 `sqlalchemy[asyncio]>=2.0.58,<2.1` |
| `TS-2` | 模型选型与尺寸（本文 §5.3） | **`D-06`** + **`D-33`** + **`D-28`** | ✅ 已裁定：只用 `yolo26s.pt`、`imgsz=1280`，远端训练本地推理 |
| `TS-3` | OpenCV 5.x 兼容性（本文 §6.1） | **`D-22`** | 🟢 工程定案（阶段二首验） |
| `TS-4` | 前端精确版本 pin（本文 §1.3） | **`D-23`** | 🟢 工程定案（`^` + `pnpm-lock.yaml`） |
| `TS-5` | `el-input` `data-testid` 透传（本文 §1.3） | **`D-34`** | 🟢 已闭合（源码级确证落内层 `<input>`） |
| `TS-6` | 单包 vs 双包依赖落点（本文 §1.1） | **`D-32`** | 🟢 已闭合（单根 `pyproject.toml` + `vision` extra） |
| `BL-1` | 法定时限基线（本文 §3.3） | **`D-11`** | ✅ 已裁定：锁定郑州令第 152 号 48h，废除四档分钟数 |
| `BL-2` | 中文优先级显示名（本文 §3.3） | **`D-12`** | ✅ 已裁定：维持「特急 / 加急 / 普通 / 观察」 |
| `BL-3` | 返修阈值默认值（本文 §3.4） | **`D-14`** | ✅ 已裁定：`max_rework_rounds = 2` |
| `BL-4` | AI 候选过期时间（本文 §2.1） | **`D-15`** | ✅ 已裁定：`ai_candidate_ttl_hours = 72` |
| `BL-5` | 亮灯率 / 完好率 KPI（本文 §3.3） | **`D-17`** | ✅ 已裁定：不实现 |
| `SD-1` | 冗余列 `region_id` / `rework_count`（本文 §4.2） | **`D-24`** | 🟢 已闭合（保留并由迁移期断言 1/2/4 守卫） |
| `SD-2` | 孤儿文件回收任务（本文 §4.5） | **`D-25`** | 🟢 工程定案（每日清理 `mtime > 24h`，批 500） |
| `SD-3` | `accounts.skills` GIN 索引（本文 §4.5） | **`D-26`** | 🟢 工程定案（百级规模不建，千级再加） |
| `SD-4` | PostgreSQL 17.12 兼容范围（本文 §1.2） | **`D-10`** | ✅ 已裁定：仅支持 PG 17.12，不兼容 16 |

> 其余相关用户裁决亦已全部吸收落定：`D-01`（远端 `/root/autodl-tmp/tomori/` + 预装 `uv 0.13.7`）、`D-02`（15 MiB）、`D-03`（全链路 `1..10`）、`D-08`（`mypy`）、`D-13`（表驱动确定性 `grading_rules`）、`D-16`（在途上限 3）、`D-18`（不承诺破损检出率）、**`D-29`**（个人本地研究项目直接使用数据集，见 §5.3）、**`D-30`**（夜间正常亮灯 `streetlight_on` 回传参照，见 §5.1）。

---

## 8. 本文档的诚实边界与变更记录

### 8.1 诚实边界

**已证实的（🟢）**：
- 全部后端核心/开发/视觉包版本、`requires_python` 与依赖约束（2026-11-21 PyPI JSON API 实测），以及前端 11+1 包版本、`peerDependencies`、TS7 崩溃与 Element Plus `data-testid` 三类透传行为（npm registry + `element-plus@2.14.8` 源码 + jsdom 实测）。
- Context7 MCP 通道在本环境**间歇性不稳定**（多次 `TypeError: fetch failed`，含连续 4 次与 6 次批次及 `task-52` 复测；`task-51` 抽验 `6/8` 成功且同库不同次成败不一；另有 Vue 2 索引陈旧），已定性为不可作为唯一取证通道，改以 Exa 官方页面直取 + 锁定版本源码交叉核验。
- 4 套状态机 `29` 状态 / `36` 转移规则条目（按 `(from,to)` 图边计 `47`）/ `11` 终态 / `0` 死路、`I1`–`I8`、`grading_rules` 144 种枚举组合穷尽覆盖（`94 + 34 + 14 + 2 = 144`）、以及郑州市令第 152 号（2020 第 237 号二次修正）48h 法源全文。
- 17 张业务表的列/约束/索引完整设计与逐项求和复算口径（`76/41/22/37/6/12`）；`backend-dev` 已在临时 PG 17.12 库实跑验证 `uq_fault_tickets_active_point_type`、`ck_fault_tickets_inspector_confirmation_serialized` 与 `I2` 跨表检测 SQL。
- 远端 GPU 实测基线（`RTX 4080 SUPER` / `32760 MiB` / `/root/autodl-tmp/tomori/` / 预装 `/root/.local/bin/uv 0.13.7`，见 `docs/RUNBOOK.md` §6.1.1–§6.1.2）及 D1–D7 数据集真实来源与原始许可元数据。
- 全项目 `40/40` 裁决已全部定案（含 `D-29`、`D-30`）。

**未证实与未运行范围（🟡 / 🔴）**：
- **阶段一与阶段二当前落地边界**：阶段一冻结基线（`42eb94e`）结束时无应用代码、迁移与测试运行；进入阶段二后，根级 `pyproject.toml`、`migrations/versions/0001..0004`（含 `0003_circular_fk_scheme_b.py`）与 `src/tomori/vision/` 11 个模块（含新增只读 EDA 工具 `eda.py`，另有 `dataset_prep.py`、`trainer.py`，共 13 个 `.py` 文件）已落盘且原根级 `vision/*.py` 已移除（根级 `vision/` 仅留空目录供运行时 `weights/` 与 `reports/`），其余业务/前端/测试链路及远端训练权重仍按进度推进与验收。
- **17 表全量 DDL 未在真实 PostgreSQL 执行**：`76/41/22/37/6/12` 是 DDL 文本计数，非 `information_schema` 实测值；「外键列数 41」与未来 `pg_constraint` `contype='f'` 约束数允许存在差异，迁移落地后须逐条核对解释。
- `mypy` 运行全量业务代码、27 行（30 实验）变异卡、`D-20`/`D-21`/`D-40` 真实 Chromium 下的 `fill()`/`check()`/`Teleport` 与 `el-option`/`el-upload` 定位、`D-22` `opencv-python-headless` 5.x 兼容及 `httpx2` `ASGITransport` 签名尚待对应阶段二模块/测试完成后实测闭环。
- `src/tomori/vision/config.py`（已迁入 `src/tomori/vision/` 并补 `"P4"`）亮度/清晰度阈值与 `epochs=500, patience=20`（附早停源码诊断与可选 `--mosaic-polish-epochs`）、`batch=-1` 实机显存探测均属工程初值，待阶段二远端 GPU 实测标定；破损类 `FIXTURE_DAMAGED` 检出率不作承诺（`D-18`）。

**明确不属于本文档的**：
- 9 UC / 29 FR / 29 VC 需求编号与验收判据 → `docs/REQUIREMENTS.md`
- 57 条路由行（含 2 条合并行，展开为 60 个 `(method, path)` 端点）、34 错误码细节、请求/响应与 128 冻结 `data-testid` 清单 → `docs/API.md`
- 40 条全项目裁决台账与用户原话原文 → `docs/DECISION.md`
- 本地容器部署、备份恢复与远端训练操作规程 → `docs/RUNBOOK.md`（`dev-ops` 独占）
- 团队 9 角色职责、写入域边界、反假绿体系与提交门禁 → `docs/team/TEAM.md`、`docs/team/LEAD.md`（本地过程产物，已 `.gitignore` 不入库）

---

### 8.2 变更记录

| 日期 | 变更 | 触发 |
|---|---|---|
| 2026-11-21 | 三源初版定稿与审计修正：建立 17 表（`76 CHECK / 41 外键列 / 22 UNIQUE / 37 普通索引 / 6 部分唯一 / 12 非唯一部分索引`）、4 状态机（`29 态 / 36 边 / 11 终态 / 0 死路`，修正通知 `SENT` 为终态）、`fault_evidences.ticket_id` 可空与已知取舍、`YOLO26` 四条官方出处、Element Plus `data-testid` 三类透传实测推翻误判、Context7 双形态故障如实披露 | `task-7` / `task-13` / `task-15` / `task-18` / `audits/stage1-audit.md` |
| 2026-11-24 | 阶段一定案同步：锁定 `sqlalchemy[asyncio]>=2.0.58,<2.1`（`D-05`）、PG `17.12`（`D-10`）、唯一底模 `yolo26s.pt` + 统一输入尺寸（初值 `1024`，已于 `task-77` 升级锁定 `1280`）+ 远端训练本地推理（`D-06`/`D-28`）；废除无依据四档分钟数与 `100×100` 权重魔数，改锁郑州市令第 152 号 48h 基线与表驱动确定性规则分级 `grading_rules`（144 组合全覆盖，`D-11`–`D-13`）；补齐 `MUT-05a/05b/15/16/17` 变异卡 | `task-26` / `task-29` / `task-37` |
| 2026-11-24 | **`task-44` 三合一精简重写为 `docs/DESIGN.md`**：由原 `docs/TECH-STACK.md`、`docs/BUSINESS-LOGIC.md`、`docs/SCHEMA.md`（曾用名 `docs/SCHEMA-DESIGN.md`）合并而成，完整保留全部契约；对齐 `docs/RUNBOOK.md` §6.1.1–§6.1.2 远端 `/root/autodl-tmp/tomori/` 与预装 `/root/.local/bin/uv 0.13.7` 实测事实；融入用户最新裁决 **`D-29`**（个人本地项目直接使用数据集、保留 D1–D7 真实元数据）与 **`D-30`**（夜间正常亮灯 `streetlight_on` 回传参照框、`is_fault_candidate=false`）；同步 40/40 零待决口径 | `task-44` 文档整合 + 用户 `D-29`/`D-30` 裁决 |
| 2026-11-24 | **`task-52` 终审收口**：① **P1-3**：移除 `.env` 视觉配置表中错配的超时环境变量，统一 `VISION_TIMEOUT_SECONDS = 60` 为后端代码常量（不入 `.env`）；② **P1-4**：消除 §3.5「两阶段占位」矛盾，定案 `pg_try_advisory_xact_lock(hashtextextended(..., 0))` 事务级咨询锁 + 锁内双重读取 + `audit_logs` 严格 append-only；③ **P1-5**：§4.5 循环外键统一为方案 B（`acceptance_records.rework_order_id` 随表 inline FK = `fk_acceptance_records_rework_order_id_rework_orders`；两表建成后**单次** `op.create_foreign_key` 后补 `fk_rework_orders_source_acceptance_id_acceptance_records`，双向共 2 条；另第 11 步建 `dispatch_records` 时 `rework_order_id` 仅建可空列、待第 14 步建成后显式后补 `fk_dispatch_records_rework_order_id_rework_orders` 前向 FK）；④ **P2-3**：`vision` extra 补 `onnx`/`onnxslim` 并统一 `ultralytics==8.4.216`、`opencv-python-headless>=5.1.0,<6`；⑤ **P2-4**：更正旧版 `el-teleport` 写法为 Vue 原生 `<teleport>`（Element Plus PR `#23219`）；⑥ **P2-11 / P2-12 / P3-4**：Context7 在 §1.2/§1.4/§8.1 统一为「间歇性不稳定，不可作唯一取证通道」、§8.1 补 `57 条路由行（含 2 条合并行，初记口径已于 task-58 修正为展开 60 个 (method, path) 端点）`、`ck_audit_logs_payload_hash_format` 补 `IS NULL OR`、`A15`–`A17` 与 `review-resolve` 补「（若存在活跃返修单）」限定 | `task-52` 阶段一终审收口（`audits` 审计与 Lead 裁决） |
| 2026-11-24（task-58） | **P2-3 / P2-12 口径同步**：① **P2-3**：§1.2 依赖表与 `[project.optional-dependencies] vision` 更新 `onnx`（实测 `1.23.2`，约束 `>=1.16.0,<2.0.0`，`requires_python >=3.10`）与 `onnxslim`（实测 `0.1.99`，约束 `>=0.1.82`，`requires_python >=3.8`），并补充对齐 `ultralytics==8.4.216` `exporter.py:1207/1217` 的 `check_requirements(("onnx>=1.16.0,<2.0.0", "onnxslim>=0.1.82"))` 硬要求说明，防止 `YOLO_AUTOINSTALL=False` 下 `0.1.31..0.1.81` 被拒导；② **P2-12**：将 §8.1 API 指针更新为 `展开为 60 个 (method, path) 端点` 并同步修正 `task-52` 记录中的旧组合数口径 | `task-58` 二次审计收口 |
| 2026-11-24（task-62） | **`src layout` 架构与门禁重写**：按用户放行阶段二并明确 `src layout` 与 Lead 架构裁决重写 §1.1（`pyproject.toml` 单包 `module-root="src"`、包名 `tomori`，所有 import 统一为 `tomori.*`）；全仓原拟议 `backend` 目录结构降维收拢为 `src/tomori/`；`alembic.ini` 与 `migrations/` 由原拟议子目录上提至仓库根；视觉源码迁入 `src/tomori/vision/`（`D-37` 改为进程内 `tomori.vision` 子包，保留 `D1` 函数内延迟 import 纪律），而运行时产物 `weights/` 与 `reports/` 仍在根级 `vision/`（gitignored）；同步修正 §0、§1.1、§3.1、§5.1–§5.3、§6.1 门禁命令（`ruff` / `mypy` / `pytest` / `alembic`）及 §8.1 诚实边界 | 用户放行阶段二指定 `src layout` + `task-62` Lead 架构裁决 |
| 2026-11-24（task-66） | **域外旧平铺目录残留清零与 `§1.2` 实测对齐**：① `README.md`、`docs/REQUIREMENTS.md`、`docs/DECISION.md` 三处域外旧平铺后端目录名全部清零并统一为 `src layout` 口径（`src/tomori/` 单包 + 根级 `alembic.ini`/`migrations/` + 根级 `vision/` 仅运行时产物），同时区分「阶段一冻结基线 `42eb94e` 时无应用代码」与「阶段二已放行实现」；② `DECISION.md` 数字口径补全为 `57 条路由行（展开为 60 个具体 (method, path) 端点）`；③ **§1.2 与本机实测 `pyproject.toml` 对齐**：`[build-system]` 定案 `uv_build>=0.8.14,<0.14` + `[tool.uv.build-backend] module-name="tomori"/module-root="src"`、新增 **`[tool.uv] override-dependencies = [\"opencv-python; python_version < '0'\"]`**（`ultralytics==8.4.216` 无条件拉 GUI `opencv-python` 会覆盖 headless，须用永假 marker 覆盖）、`uvicorn[standard]>=0.54.0` 与 `httpx>=0.28.1,<1.0.0` 显式入主依赖、`respx>=0.23.1`（仅支持 `httpx`）、`vision` extra 补 `numpy>=2.0.0,<3.0.0`、`mypy>=2.3`（实测解析 `2.4.1`）、`sqlalchemy 2.0.58` 的 `requires_python` 校正为 `>=3.7`；④ 修正 `task-52` 记录中循环外键描述为「1 条 inline + 1 次后补 ALTER 双向共 2 条 + 第 11 步 `dispatch_records.rework_order_id` 前向 FK 后补」 | `task-66` |
| 2026-11-24（task-71） | **阶段二实落盘微同步**：① §1.1 迁移脚本名由原长名更新为实际落盘文件名 **`0003_circular_fk_scheme_b.py`**（Alembic `alembic_version.version_num` 列宽硬编码 `VARCHAR(32)`，34 字符会截断报错，故缩短为 25 字符）；② §1.1 / §5.3 / §8.1 同步 `src/tomori/vision/` 10 个模块已迁入并完成 4 项修复（原根级 `vision/*.py` 已移除，仅留空目录 `vision/` 供运行时 `weights/` 与 `reports/` 产物） | `task-71`（对齐 `migrations/` 与 `src/tomori/vision/` 实落盘） |
| 2026-11-24（task-77） | **视觉分辨率 1280 与长周期训练参数同步**：① 按 `D-28` 与 D1 全量 EDA（8,428 图 `81.86%` 为 `1280×720`）将 §1.1 `YOLO_IMG_SIZE`、§5.3 `imgsz` 与 ONNX 静态输入 `[1, 3, 1280, 1280]` 及 §7 `TS-2` 同步升级为 **`1280`**（`2×640` 1:1 零下采样并指向 `docs/TRAIN.md` §2.2）；② §5.3 与 §8.1 远端训练参数同步为 **`epochs=500, patience=20`**（配合 `early_stopping_diagnostics` 早停源码诊断与可选 `--mosaic-polish-epochs`，详见 `docs/TRAIN.md` §3）；③ §1.1 / §5.3 / §8.1 登记 `src/tomori/vision/` 已落盘 11 个模块（新增只读 EDA 工具 `eda.py`，另有 `dataset_prep.py`、`trainer.py`，共 13 个 `.py` 文件）；同步修正 `task-26` 旧记录中 `imgsz` 字面残留 | `task-77`（`D-28` 1280 + 500 epochs/patience 20 + `eda.py` + `TRAIN.md` EDA 引用） |
| 2026-11-24（task-109） | **文档-实现一致性同步**：以已入库 `web/src/views/inspection/InspectionReportView.vue:660` 为准，将 §5.2 `D3` 视觉降级横幅旧前端锚点更正为 **`data-testid="inspection-vision-disabled"`**，并补注其携带的 `data-code` 与 `data-fallback-action="MANUAL_REPORT"` 可测属性 | `tester` `task-106` 上报 + `task-109` Lead 裁决 |
| 2026-11-25（task-156） | **状态机边数两口径统一**：§0 汇总表、§2 段首与各机器标题（A/B/C/D）边数表述统一为「**`36` 转移规则条目**（`A15+B6+C8+D7`，每条守卫+审计 `action` 一个编号）**与** `(from,to)` 字面图边 **`47`**（`A16+B8+C14+D9`）」双口径并补口径说明脚注（A8 单条规则覆盖 `DISPATCHED → {PENDING_DISPATCH, MANUAL_REVIEW}` `2` 边等）；消除与 `state_machine.py` 展开图边 `47` 易混的旧单口径写法 | `auditor` `task-81` `P3-1` + `task-156`（与 `backend-dev` `task-155` 代码注释同口径） |
| 2026-11-25（task-170） | **`nc=3`/`occluded` 弃用同步**：§5.1 `raw_class` → 业务枚举映射表保留 `streetlight_occluded` 行并标注「已弃用，仅 `API` `fault_type=OCCLUDED` 手工报障兼容」（`merged_v7`/`streetlight_v3` `nc=3` 模型不产出该 `raw_class`）；补 `nc=3`/`occluded` 弃用口径说明（`D-41`/`task-165`） | 用户修订指令 + `task-170` |
