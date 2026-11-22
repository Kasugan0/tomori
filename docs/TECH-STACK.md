> 状态：阶段一定稿候选 —— 待用户审阅裁决（未定稿）

# 技术栈定稿（TECH-STACK）

> **文档归属**：`docs-writer`（`docs/` 设计文档）｜**本文件写入域**：`docs/TECH-STACK.md`
> **读者**：`backend-dev`、`vision-dev`、`frontend-dev`、`dev-ops`、`tester`、`auditor`
> **上游素材**：`.team/notes/backend-dev/stage1-backend-spec-package.md`（包结构与依赖）、`.team/notes/dev-ops/stage1-infra-facts.md`（环境事实）、`.team/notes/research/standards-and-stack.md`（版本实测）、`.team/notes/research/datasets.md`（数据集合规）
> **编号约定**：`TS-xx` 表示技术栈条目；`F14` 指 `docs/team/TEAM.md` §4.5 的「断言必须附出处」纪律
> **数字口径**：凡出现版本号、数量，均给出「采样日期 + 测量命令」；凡工程判断，均标注 `🟡 工程判断`
>
> **本文档的证据等级**：
> - 🟢 **一手实测** —— 本次通过 `curl https://pypi.org/pypi/<pkg>/json`、`registry.npmjs.org`、GitHub raw 源码、官方文档原文取得
> - 🟡 **工程判断** —— 无外部证据支撑的团队决策，**不得当作事实引用**
> - 🔴 **工具故障** —— 通道不可用，已如实记录（见 §7）

---

## 0. 一句话技术栈

**单应用单体架构**：后端 FastAPI（异步）+ PostgreSQL 16/17 + SQLAlchemy 2.0 Async + psycopg v3；前端 Vue 3 + TypeScript + Vite + Element Plus；视觉 YOLO 系列（Ultralytics）+ OpenCV，**以进程内 Python 模块形式嵌入后端**，不做独立服务。

| 层 | 选型 | 一句话理由 |
|---|---|---|
| 后端框架 | **FastAPI**（异步） | 原生 async、Pydantic v2 集成、OpenAPI 自动生成、依赖注入够用 |
| ORM | **SQLAlchemy 2.0 Async**（锁 2.0 维护线） | `Mapped[]` 类型化声明式、`AsyncSession`、复杂 `CHECK`/部分索引表达力 |
| 驱动 | **`psycopg[binary]` v3** | 用户裁决；v3 统一同步/异步 URL，替代 psycopg2 |
| 迁移 | **Alembic**（异步模板） | SQLAlchemy 官方迁移工具，无第二选择 |
| 数据库 | **PostgreSQL 16/17** | 部分唯一索引、`jsonb`、`CHECK`、`text[]`、`SKIP LOCKED` —— 本项目全靠这些 |
| 视觉 | **Ultralytics YOLO** + **OpenCV** | 检测任务 SOTA 工程化实现；训练在 32G–48G 远程 GPU |
| 前端 | **Vue 3 + TypeScript + Vite** | 用户裁决；`<script setup>` + Composition API |
| UI 组件 | **Element Plus** | 中后台表格/表单/弹窗完备，运维后台适配度高 |
| 状态管理 | **Pinia** | Vue 3 官方推荐 |
| 路由 | **Vue Router 4** | 官方 |
| 包管理 | **uv**（Python）/ **pnpm**（Node） | 团队工具链规范（`~/.dsh/AGENTS.md`） |
| 容器 | **Docker Compose** | 隔离开发栈，PostgreSQL 单独容器 |

---

## 1. 后端技术栈

### 1.1 FastAPI

| 项 | 值 | 出处 |
|---|---|---|
| 版本 | **`0.165.0`**（2026-11-18） | 🟢 `curl https://pypi.org/pypi/fastapi/json` → `info.version` |
| `requires_python` | `>=3.11` | 🟢 同上 → `info.requires_python` |
| 关键依赖约束 | `starlette>=1.6.0`、`pydantic>=2.9.0` | 🟢 同上 → `info.requires_dist` |
| 安装形态 | `fastapi[standard]` | 🟡 团队决策：`[standard]` 附带的 `uvicorn` / `python-multipart` 都是本项目必需的 |

**为什么是 FastAPI 而不是 Flask/Django**：
- 本项目**全部 I/O 是数据库 + 文件**，异步收益真实存在（`asyncpg`/`psycopg` 异步驱动、`AsyncSession`）。
- Pydantic v2 的请求/响应校验直接复用为 OpenAPI 契约，省掉一套手写序列化。
- **不选 Django**：17 张表的约束密度（**76 条 `CHECK`**、41 个外键列、6 个部分唯一索引）用 Django ORM 表达不出部分唯一索引与复杂 `CHECK`，必然退化为裸 SQL，反而丢掉 ORM 价值。
- **不选 Flask**：无原生异步、无内建依赖注入、无 OpenAPI。

**必须注意的版本事实** 🟢：`fastapi 0.165` 已依赖 `starlette>=1.6.0`（Starlette **1.x**）。网上大量教程仍是 Starlette 0.2x/0.3x 时代的写法（如自定义 `TestClient` 参数、`Middleware` 签名）。**以 FastAPI 官方文档为准，不照抄旧教程。**

### 1.2 SQLAlchemy —— 版本决策记录（**必读**）

| 项 | 值 | 出处 |
|---|---|---|
| 本项目锁定 | **`sqlalchemy[asyncio]>=2.0.58,<2.1`** | 🟡 **Lead 裁决（2026-11-21）** |
| 2.0 维护线最新 | **`2.0.58`**（2026-11-16） | 🟢 `curl https://pypi.org/pypi/sqlalchemy/json` → 过滤 `^2\.0\.\d+$` 后取 max |
| 2.1 线最新 | **`2.1.9`**（2026-11-16） | 🟢 同上，过滤 `^2\.1\.\d+$` 后取 max |
| `requires_python` | `>=3.11`（两条线相同） | 🟢 同上 → `info.requires_python` |

#### 1.2.1 版本决策记录：**知道 2.1 存在，主动选择不升**

> 这一节的存在意义：审计时必须能看出「团队知道 2.1 已 GA 并主动选择留在 2.0 线」，而不是「团队不知道 2.1 存在」。**默默写 `<2.1` 而不解释，等于把决策过程销毁。**

**事实**：`SQLAlchemy 2.1.9` 已于 2026-11-16 发布，且与 `2.0.58` **同日发版**——说明 2.0 维护线仍在积极维护，有安全修复通道，不是「用旧版硬撑」。

**选择 2.0 线的四条理由** 🟡 团队决策：

1. **用户原话是契约**。用户在技术栈裁决中明确写下「SQLAlchemy 2.0 Async」。`2.1.9` 已 GA 不等于用户授权变更主线；改主线属于技术方案变更，应由用户拍板，不由团队在阶段一顺手升级。
2. **2.0 维护线同日发版 `2.0.58`**，证明它仍是活跃维护线。
3. **阶段一的关键设计全部是 2.0 API**：`create_async_engine` / `async_sessionmaker` / `expire_on_commit=False` / `AsyncAttrs`。`backend-dev` 的库文档证据亦全部取自 SQLAlchemy `rel_2_0_*` 分支。切 2.1 意味着整套已核证的技术断言要重新取证，而 **Context7 通道当前不可用**（§7.1），重新取证成本极高且不可靠。
4. **4 周交付期不该叠加未验证的主线变更**。

**后续动作**：**阶段三（如有）再评估 2.1 迁移**。迁移时需重点验证 `greenlet` 依赖行为、`AsyncAttrs` 是否有变化、以及 `alembic` 的 `SQLAlchemy>=2.0` 宽松约束是否已收紧。

> 🔴 **标注：🟡 待用户确认**。若用户希望直接用 2.1，改为 `sqlalchemy[asyncio]>=2.1.9,<2.2` 即可，其余设计不变。已列入 §8 待裁决清单。

#### 1.2.2 `[asyncio]` extra 是**强制**的，不是可选的

🟢 **SQLAlchemy 2.1 起 `greenlet` 不再默认安装，必须显式 `[asyncio]` extra**。若不写 extra，会在第一个 `await session.execute()` 报 `MissingGreenlet`。

> **注意这条的特殊性**：本项目锁的是 2.0 线，但 `[asyncio]` extra 的写法**两条线都适用且都是必需的**。写 `sqlalchemy>=2.0.58,<2.1` 而漏掉 `[asyncio]`，同样会在运行期炸。**这是最容易漏、最难排查的一条**（报错发生在第一次真实 DB 往返，而非启动时）。

#### 1.2.3 必须遵守的 4 个 2.0 API 用法

| # | 用法 | 为什么 | 出处 |
|---:|---|---|---|
| 1 | `create_async_engine(url, echo=False, pool_pre_ping=True)` | 异步引擎入口；`pool_pre_ping` 防容器重启后的死连接 | 🟢 SQLAlchemy 2.0 官方 Async 文档 |
| 2 | `async_sessionmaker(engine, expire_on_commit=False)` | **`expire_on_commit=False` 必须显式设**。默认 `True` 会在 commit 后使 ORM 对象过期，FastAPI 序列化响应时触发 `MissingGreenlet`（因为序列化发生在 sync 上下文） | 🟢 同上 |
| 3 | `class Base(AsyncAttrs, DeclarativeBase)` | `AsyncAttrs` 提供 `await obj.awaitable_attrs.rel`，避免异步懒加载炸裂 | 🟢 同上 |
| 4 | `MetaData(naming_convention=...)` | 让 `CHECK`/`FK`/`UNIQUE`/索引有确定性名字。**本项目的约束名是 E2E 断言对象**（如 `23505` 错误里的约束名），没有命名约定就无法稳定断言 | 🟡 工程判断，但影响 F14 可验证性 |

### 1.3 psycopg v3

| 项 | 值 | 出处 |
|---|---|---|
| 包名 | **`psycopg`**（**不是** `psycopg2`） | 🟢 用户裁决 + PyPI |
| 版本 | **`3.3.9`**（2026-11-12） | 🟢 `curl https://pypi.org/pypi/psycopg/json` |
| 安装形态 | **`psycopg[binary]`** | 🟡 免编译 wheel，容器内不需要 `libpq-dev` |
| 连接串 scheme | **`postgresql+psycopg://`** | 🟢 SQLAlchemy 方言名 |
| `requires_python` | `>=3.10` | 🟢 PyPI |

> ⚠️ **最常见的错误**：写 `postgresql+psycopg2://`。本项目的驱动是 **psycopg v3**，scheme 是 `+psycopg`（无 `2`）。写 `+psycopg2` 会在导入时 `ModuleNotFoundError: psycopg2`，或在装了 psycopg2 时静默用错驱动（同步驱动跑在异步引擎里 → `MissingGreenlet`）。

**为什么升级到 v3 而不是继续用 psycopg2**（用户裁决，理由记录）：
- v3 提供**统一的同步/异步 API**，同一个驱动包支持两种模式，SQLAlchemy 异步方言直接用 `psycopg` 而非额外的 `asyncpg`。
- v3 有 `[binary]` wheel，避免容器内编译。
- psycopg2 的异步支持是第三方的（`psycopg2` 本身无 async），与 SQLAlchemy `AsyncSession` 配合需要 `asyncpg`，等于两套驱动。

### 1.4 Alembic

| 项 | 值 | 出处 |
|---|---|---|
| 版本 | **`1.20.1`**（2026-10-22） | 🟢 `curl https://pypi.org/pypi/alembic/json` |
| 依赖约束 | `SQLAlchemy>=2.0`（**宽松**，2.0/2.1 都满足） | 🟢 同上 → `info.requires_dist` |
| 模板 | **异步模板**（`alembic init -t async`） | 🟢 Alembic 官方异步模板 |

**两条硬性纪律**（转写自 `.team/notes/backend-dev/stage1-backend-spec-package.md` §2）：

1. **`env.py` 的 `target_metadata` 必须 import 全部 17 个 models**。否则 autogenerate「看不到表」，会生成一批 `drop_table` —— 这是**破坏性**的。正确做法：`from app.models import * `（通过 `models/__init__.py` 汇总），并 import `Base`。
2. **`alembic.ini` 的 `sqlalchemy.url` 必须留空**。URL 由 `env.py` 从 `Settings` 读，避免密钥进版本库。这是安全要求，不是风格偏好。

**`alembic_version` 的口径声明**（与 `docs/SCHEMA.md` §0 一致）：它是迁移工具的版本记录表，仅一列 `version_num varchar(32)`，**不承载业务语义、不参与业务查询**。任何「数表」的断言必须显式排除：`WHERE table_name <> 'alembic_version'`。

### 1.5 PostgreSQL

| 项 | 值 | 出处 |
|---|---|---|
| 版本 | **16 或 17** | 🟡 允许区间；`dev-ops` 实测环境为 **17.12** |
| 用途 | 唯一持久化存储 | — |

**为什么必须是 PostgreSQL，不能是 MySQL/SQLite**：

| 本项目依赖的特性 | PostgreSQL | MySQL 8 | SQLite |
|---|---|---|---|
| **部分唯一索引**（`WHERE status IN (...)`） | ✅ | ❌ | ✅（但无并发） |
| `jsonb` + `->>` 运算符 | ✅ | ⚠️ JSON 较新 | ⚠️ |
| 复杂 `CHECK`（跨列、正则、枚举集合） | ✅ | ⚠️ 8.0.16+ 才有 | ⚠️ |
| `text[]` 数组 | ✅ | ❌ | ❌ |
| `FOR UPDATE SKIP LOCKED` | ✅ | ✅ | ❌ |
| `timestamptz` 与 `zoned` 比较 | ✅ | ⚠️ | ❌ |

**决定性理由**：本项目的核心防重机制「同一点位同类型只允许一条活跃故障单」靠**部分唯一索引**实现（`uq_fault_tickets_active_point_type ... WHERE status NOT IN ('CLOSED','CANCELLED')`）。MySQL 无部分索引，只能靠应用层加锁或触发器，可靠性差一个数量级。**这一条就足以排他。**

### 1.6 其他后端依赖

> 🔴 **当前事实**（`audits/stage1-audit.md` P1-4）：根 `pyproject.toml` 是 `uv init` 原始脚手架（`description = "Add your description here"`、`dependencies = []`、`main.py` 是 hello world、`uv.lock` 只有 1 个 virtual package）。**本表 20 个依赖一个都没装**。阶段一按用户裁决「先定稿设计再编码」不装依赖，这是**有意为之**，不是遗漏。

#### 依赖落点：**单包 + `vision` optional extra**（`TS-6` 已裁决）

> 🟢 **`Lead` 裁决（2026-11-21）：不是双包。** 根 `pyproject.toml` 是**唯一** Python 包定义；`backend/` 与 `vision/` 是根包下的**子目录模块**，**不建子 `pyproject.toml`**。

**为什么不是双包**：用户裁决节奏是 4 周交付单体应用。双包要引入 workspace 概念、两套 lockfile、跨包 import 路径配置——**复杂度与问题规模不匹配**。

**那 `vision/` 的依赖隔离怎么办**：`torch` 是 GB 级，后端开发不该被拖死，这个隔离是**真实需求**。解法是 `[project.optional-dependencies]` 分组，**不是拆包**：

```toml
# 根 pyproject.toml（阶段二由 backend-dev 重写，替换 uv init 脚手架）
[project]
name = "tomori"
requires-python = ">=3.12"
dependencies = [
  "fastapi", "uvicorn[standard]", "sqlalchemy[asyncio]", "psycopg[binary]",
  "alembic", "pydantic", "pydantic-settings", "pwdlib[argon2]", "pyjwt",
  "pillow", "python-multipart", "tzdata",
]

[project.optional-dependencies]
vision = ["ultralytics", "opencv-python-headless"]   # 拉入 torch / torchvision
```

| 场景 | 命令 | 装了什么 |
|---|---|---|
| 纯后端开发（默认） | `uv sync` | 12 个后端主依赖，**不含** `ultralytics`/`torch` |
| 视觉开发 / 训练 / E2E 全量 | `uv sync --extra vision` | 上述 + `ultralytics` + `opencv-python-headless`（及其 `torch`） |

> **隔离达成，且只有一个 lockfile、零 workspace 概念。** 这正是「用 extra 解决隔离」而不是「用拆包解决隔离」的理由——**两件事要分开**：`torch` 需要分组（真需求）≠ 需要拆包（过度设计）。
>
> ⚠️ **§2.4.5 已预先记录**「`ultralytics` 作为**可选依赖组**，不进后端主依赖」，与本裁决一致。
> ⚠️ **边界**：本节只定「依赖落在哪、怎么装」。**不改 20 个依赖清单本身，不改任何表结构。**

| 依赖 | 版本 | 作用 | 出处 |
|---|---|---|---|
| `pydantic` | **`2.14.4`**（2026-11-19） | 校验与序列化；v2 语法（`model_config`、`field_validator`） | 🟢 PyPI |
| `pydantic-settings` | **`2.16.3`**（2026-11-13） | 从 `.env` 读配置。`BaseSettings` 已迁出 pydantic 主包，**不装就没有** | 🟢 PyPI |
| `uvicorn` | **`0.54.0`**（2026-09-25） | ASGI server | 🟢 PyPI |
| `pwdlib[argon2]` | `0.4.2` | 密码哈希。FastAPI 官方教程已从 `passlib` 迁到 `pwdlib`（`passlib` 对 bcrypt 4.x 有兼容问题且长期未更新） | 🟡 团队选型 |
| `pyjwt` | `2.15.1` | JWT 签发/校验 | 🟡 团队选型 |
| `pillow` | `>=11.0.0` | **服务端**二次魔数/帧数/像素校验 + `Image.MAX_IMAGE_PIXELS` 防解压炸弹 | 🟡 见下方安全说明 |
| `python-multipart` | — | `multipart/form-data` 解析。缺它会 500 | 🟡 |
| `tzdata` | `2026.6` | Alpine 容器无系统 tzdata，`ZoneInfo("Asia/Shanghai")` 会 `ZoneInfoNotFoundError` | 🟡 |

> 🔐 **Pillow 是安全边界，不是图像工具**：视觉模块（`vision/`）**不是可信输入源**——它接收的图片来自巡查员上传。因此服务端必须在**交给视觉模块之前**用 Pillow 做一次独立校验：魔数、帧数（拒动图）、像素上限（防解压炸弹）。**不能假设 `vision` 会正确校验**。详见 `docs/API.md` 媒体错误码章节。

### 1.7 明确**不引入**的依赖及理由

| 不引入 | 替代 | 理由 |
|---|---|---|
| `sqlmodel` | 原生 SQLAlchemy 2.0 声明式 | SQLModel 不支持 `Mapped[]` 全套、部分索引与 `CHECK` 表达受限；本项目约束密度高（**76 条 `CHECK`**），必须用原生 |
| `fastapi-users` | 手写 `auth` 模块 | 它自带 `users` 表结构，与 17 表口径冲突，会诱使加第 18 张表 |
| `passlib` | `pwdlib[argon2]` | 长期无更新，bcrypt 4.x 已知告警 |
| `celery` / `arq` / `rq` | `asyncio.Task` + `notification_tasks` 表 + 单进程 worker | 本项目是单应用演示，引入 broker 会多 3 个容器与一套部署复杂度；而「至少一种异常恢复场景」用 DB 表驱动的重试已能完整演示，且**重试状态持久化**（跨进程重启可恢复），比内存队列更符合验收要求 |
| `redis` | 无 | 幂等键落在 `audit_logs`，不需要外部 KV |
| `postgis` | `boundary_geojson jsonb` | 派单判定只用 `region_code` 相等，不做几何相交 |
| `orjson` | 默认 JSON | 数据量级不构成瓶颈，少一个 C 扩展 |
| `structlog` | 标准 `logging` + `request_id` 过滤器 | 日志量级小；`request_id` 已由中间件注入 |
| `tenacity` | 手写重试（`attempt_count`/`next_retry_at` 在表里） | 重试状态必须**持久化**，内存装饰器做不到 |

---

## 2. 视觉技术栈

### 2.1 模型与框架

| 项 | 值 | 出处 |
|---|---|---|
| 框架 | **Ultralytics** | 用户裁决「YOLO/OpenCV」 |
| 版本 | **`8.4.216`**（2026-11-20） | 🟢 `curl https://pypi.org/pypi/ultralytics/json` |
| 依赖 | `torch>=1.8.0`、`torchvision>=0.9.0`（**未固定上限**） | 🟢 同上 → `info.requires_dist` |
| 图像处理 | **OpenCV**，安装为 **`opencv-python-headless` `5.1.0.95`**（2026-11-17） | 🟢 `curl https://pypi.org/pypi/opencv-python-headless/json` |
| 训练硬件 | **远程 GPU，32G–48G 显存** | 用户裁决原话 |
| 推理形态 | **进程内 `import vision`**，不做独立视觉服务 | Lead 裁决 U-3（2026-11-21 07:42 +08:00） |

### 2.2 ⚠️ `vision/` 目录当前状态（**必须精确表述，不得写成「已实现」**）

| 事实 | 值 |
|---|---|
| 目录 | `vision/` |
| 当前内容 | **8 个 `.py` 文件**：`__init__.py`、`config.py`、`contract.py`、`errors.py`、`image_io.py`、`metrics.py`、`whitelist.py`、`baseline.py` |
| 来源 | 来自**已撤回的 `task-10`** 的预研草稿 |
| **运行状态** | **未经运行验证、未经测试** |
| **交付状态** | **未纳入阶段一交付范围** |
| 文档中的正确表述 | 「**阶段二规划目录**；当前存在**未经运行验证的预研草稿**，尚未纳入交付范围」 |

**禁止的表述**：
- ❌ 「视觉模块已实现」
- ❌ 「`vision/` 已完成 8 个模块」
- ❌ 把 8 个 `.py` 计入阶段一交付的代码量或进度
- ❌ 在 `docs/REQUIREMENTS.md` 的「已完成」列打勾

> **为什么这条要单列**：这是一份**设计文档**，读者会拿它判断「什么已经能用」。把未运行验证的草稿写成「已实现」，属于宣称了一个**没有运行证据**的断言，违反 `docs/team/TEAM.md` §4.5（**F14：断言必须带出处**）——「已实现」是必须有可复现运行证据的断言，而阶段一没有。阶段一的真实状态是：**只有设计，没有可运行的视觉代码**。

### 2.3 智能能力的安全边界（用户原话，逐字约束）

用户原话：
> 「智能业务范围与边界：只处理静态巡查图片，不接实时监控或远程灯控；维修工单须经巡查员确认，模型不能替代电气安全检查。」

落为三条**不可违反**的设计约束：

| # | 约束 | 落地方式 | 违反后果 |
|---:|---|---|---|
| V1 | **只处理静态图片** | 服务端魔数 + 帧数校验；视频容器/`rtsp://`/`stream_url`/`camera_*` 一律 `415` | 违反用户「不做」清单 |
| V2 | **AI 候选必须经巡查员确认才能派工** | 状态机：`AI_CANDIDATE_PENDING` →（人工 `confirm`）→ `PENDING_DISPATCH`；`confirmed_by_inspector=false` 时派工返回 `422 AI_CANDIDATE_NOT_CONFIRMED`，且有 DB `CHECK` 兜底 | 模型直接驱动业务 = 越权 |
| V3 | **模型不得替代电气安全检查** | 电气安全检查是**维修员**在完工时填写的 5 项清单（`safety_checklist_json`），`electrical_safety_checked=false` 时完工返回 `422 ELECTRICAL_SAFETY_CHECK_REQUIRED`，DB `CHECK` 兜底 | 拿 AI 当安全认证 = 事故责任 |

> **V2 与 V3 是 DB 层强制的，不是「约定」**。两者都有 `CHECK` 约束与服务层校验两层。详见 `docs/BUSINESS-LOGIC.md` 状态机与 `docs/SCHEMA.md` §9.1。

### 2.4 YOLO 训练方案与数据集合规（**本节是交付的许可证义务，不可删减**）

#### 2.4.1 数据集分档结论

| 档 | 定义 | 数据集 | 数量 |
|---|---|---|---|
| **A 可直接训练** | 有明确许可证 + 数据真实可用 | **`ngocleltt/lamp-state-detection`**（CC-BY-4.0） | **1 个（唯一）** |
| **B 仅可评估** | 许可证可用但有传染性（ShareAlike） | `hotosm/streetlevel-poles`（ODbL） | 1 个 |
| **C 需授权** | 无许可证 = 保留所有权利，须联系作者 | D2 / D3 / D4 / D5 / D7 / D17 / D18 | 7 个 |
| **C 不可用** | 连数据都没有 | D6 | 1 个 |

> **唯一 A 档可训练数据集只有 1 个：`ngocleltt/lamp-state-detection`。**
> **破损类（`FIXTURE_DAMAGED`）全球无 A 档公开数据，必须自采。**

#### 2.4.2 唯一 A 档数据集的训练约束

| 项 | 值 | 出处 |
|---|---|---|
| 数据集 | `ngocleltt/lamp-state-detection` | 🟢 `mcp__magpie__huggingface__hub_repo_details` → `license: cc-by-4.0`、3,122 downloads |
| 规模 | **8,428 张** | 🟢 HF API `siblings` → `annotations/`、`images/`、`labels/` **各 8,428** 条（三路独立印证） |
| 类别 | `lamp_on` / `lamp_off` / `lamp_occluded` | 🟢 README 类别表 |
| **必须按 `video_id` 分组切分** | **是** | 🟡 **工程判断，但属硬要求**：同一视频抽出的帧在视觉上高度相似，随机划分会让验证集「见过」训练集，指标虚高。必须 grouped split by `video_id` |
| 许可证 | **CC-BY-4.0** | 🟢 同上 |

#### 2.4.3 🚫 D2–D7 的表述禁令

**D2 / D3 / D4 / D5 / D7 / D17 / D18 均无许可证文件** → 法律状态是「**保留所有权利**」（默认版权）。

- ❌ **禁止**称它们为「开源数据集」
- ❌ **禁止**称它们为「可训练数据源」
- ✅ 正确表述：「公开可见但无许可证，法律状态为保留所有权利，**不得用于训练**，如需使用须逐一联系作者取得授权」

**D5 另有数据质量否决**（即使拿到许可证也不能用）：`Team16Project/Street-Light-Dataset` 是**未完工的占位仓库**——5 个 `README.md` 中 4 个是 *"Here, you need to upload your ... dataset of ... images"* 的上传说明，`Raw Dataset/Flicker/` 只有 22 个 `.mp4`（其中 2 对重名文件 blob SHA 相同 → 20 个唯一视频），且**无 LICENSE 文件**。

> **结论的层次**：这条不只是「没有许可证」，而是「**即使有许可证，数据本身也不足以支撑训练**」。判断「能不能用」必须同时过「合规」与「可用」两道门。

#### 2.4.4 ✅ CC-BY-4.0 署名文本（**许可证义务，必须随交付物提供**）

若本项目使用 `ngocleltt/lamp-state-detection` 训练或评估，**必须**在交付物中保留以下署名。这是 CC-BY-4.0 §3(a) 的强制要求，**不是可选项**：

```text
本项目的视觉模型训练使用了以下数据集：

  Lamp State Detection
  作者：ngocleltt
  来源：https://huggingface.co/datasets/ngocleltt/lamp-state-detection
  许可证：Creative Commons Attribution 4.0 International (CC-BY-4.0)
  许可证全文：https://creativecommons.org/licenses/by/4.0/legalcode

本项目对该数据集做了以下修改（CC-BY-4.0 §3(a)(1)(B) 要求声明修改）：
  1. 类别标签重映射：lamp_on → streetlight_on
                       lamp_off → streetlight_off
                       lamp_occluded → streetlight_occluded
  2. 按 video_id 分组重新划分训练/验证集（避免同源帧泄漏）
  3. 受控数据增强（亮度/对比度扰动，模拟昼夜与曝光差异）

本署名不表示原作者对本次使用或本项目成果的认可或背书
（CC-BY-4.0 §3(a)(2)：no endorsement）。
```

> ⚠️ **三项不可省略**：① 许可证名称 + 链接；② **明确列出修改内容**（CC-BY-4.0 §3(a)(1)(B) 硬要求，很多团队漏掉）；③ **无背书声明**（§3(a)(2)）。缺任一项即为许可证违规。

#### 2.4.5 训练与推理的工程约束

| 项 | 值 | 说明 | 等级 |
|---|---|---|---|
| 训练环境 | 远程 GPU 32G–48G | 用户裁决。`ultralytics` 作为**可选依赖组**，不进后端主依赖 | 🟢 用户 |
| 推理入口 | 进程内 `import vision` | Lead 裁决 U-3 | 🟢 Lead |
| 视觉不可用时的降级 | `POST /tickets`（纯人工报障）**永不**依赖视觉可用性 | Lead 裁决 U-3 附加硬要求。视觉失败 → `503 VISION_*`，巡查员可跳过 AI 直接人工报障 | 🟢 Lead |
| 推理超时 | `VISION_TIMEOUT_SECONDS` | 超时 → `503 VISION_TIMEOUT` | 🟡 初值未标定 |
| 破损类能力 | **无 A 档公开数据，必须自采** | 阶段一不承诺 `FIXTURE_DAMAGED` 的检出率 | 🟡 诚实边界 |

> ✅ **YOLO26 出处已取得（`task-15`，2026-11-21）**：初版写「未取得官方出处」是**当时的如实记录**，现已核实——`YOLO26` 是 **Ultralytics 官方正式发布的模型家族**，4 条一手证据：
>
> | # | 证据 | 逐字 / 坐标 |
> |---:|---|---|
> | 1 | 官方模型文档 | <https://docs.ultralytics.com/models/yolo26>：「released **January 2026**」；Detection 行列出 `yolo26n.pt` `yolo26s.pt` `yolo26m.pt` `yolo26l.pt` `yolo26x.pt` |
> | 2 | **本项目锁定版本的源码** | `ultralytics==8.4.216` tag 下 `ultralytics/cfg/models/26/yolo26.yaml` 存在，含 `n/s/m/l/x` 五档 scales |
> | 3 | 源码内 checkpoint 名单 | `v8.4.216/ultralytics/utils/downloads.py` L26：`[f"yolo26{k}{suffix}.pt" for k in "nsmlx" ...]` |
> | 4 | 官方权重发布 | `api.github.com/repos/ultralytics/assets/releases/tags/v8.4.0` → `yolo26s.pt` = 20,422,725 B，可直下 |
>
> **纪律不变，改的是「有出处就写具体名字」**：本文档与 `docs/RUNBOOK.md` / `docs/SCHEMA.md` / `.env.example` 中提到 `yolo26*.pt` 时，**必须能指向上表任一条**。写具体 checkpoint **不再违规**——**没有出处**才违规（`F14`）。
> 完整核实记录（含复现命令与原始输出）：`.team/notes/research/yolo26-official-sources.md` + `.team/notes/research/logs/yolo26-official-evidence.log`
>
> 🔴 **但仍有一条相邻的 `F14` 违例未清（属 `dev-ops` 域，不在本文档）**：`docs/RUNBOOK.md:222` 把「32G/48G 显存 → `batch=16~32`/`32~64`」标注为「依据 Ultralytics YOLO26 **官方训练指南**」。经查，官方**从未**按显存容量给分档数字，只说「reduce the batch size incrementally」（渐进减小）并推荐 `batch=-1`；官方 `imgsz` 默认 `640` 而非 `1024`。**同段里只有「`batch=-1` 约 60% 显存」一条真有出处。** 替换文案已写入 `.team/notes/research/yolo26-official-sources.md` §3.5，待 `dev-ops` 执行。

---

## 3. 前端技术栈

> **本节版本号已由 `task-13` 独立复核（`.team/notes/research/stack-frontend.md`，769 行），采集时间 `2026-11-21 07:49–07:57 Asia/Shanghai`。**
> 统一入参：`curl -sS --max-time 40 https://registry.npmjs.org/<pkg>/latest`；原始 JSON 落盘 `.team/notes/research/logs/npm-*-latest.json`（12 个文件）。

### 3.0 精确版本表（**🟢 全部实测**）

| 依赖 | 最新稳定版 | 本项目采用 | 关键字段（逐字） |
|---|---|---|---|
| `vue` | **3.5.46** | `^3.5.46` | **`engines` 字段不存在**（`jq 'has("engines")'` → `false`） |
| `element-plus` | **2.14.8** | `^2.14.8` | `peerDependencies: {"vue":"^3.3.7"}`；依赖含 `"@element-plus/icons-vue":"^2.3.2"` |
| `@element-plus/icons-vue` | **2.3.2** | `^2.3.2`（**必须显式声明**） | **`deprecated` 字段不存在**（未废弃） |
| `vite` | **8.3.8** | `^8.3.8` | `engines:{"node":"^20.19.0 \|\| >=22.12.0"}` |
| `@vitejs/plugin-vue` | **6.0.11** | `^6.0.11` | `peerDependencies:{"vite":"^5 \|\| ^6 \|\| ^7 \|\| ^8"}` → 兼容 Vite 8 ✅ |
| `pinia` | **4.1.0**（latest） | 🔴 **禁 v4，用 `^3.0.4`** | `peerDependencies:{"vue":"^3.5.11"}` |
| `vue-router` | **5.5.1**（latest） | 🔴 **禁 v5，用 `^4.6.5`** | dist-tags `"v4-latest":"4.6.5"`；v5 peer 要 `vue:^3.5.34` |
| `typescript` | **7.0.2**（latest） | 🔴 **禁 7.x，锁 `6.0.3`** | 见陷阱 1（**TS7 会让 `vue-tsc` 崩溃**） |
| `vue-tsc` | **3.3.14** | `^3.3.14` | `peerDependencies:{"typescript":">=5.0.0"}` ← **该范围对 TS7 是假的** |
| `@volar/typescript`（传递依赖） | 2.4.31 | （由 `vue-tsc` 锁 `2.4.30`） | `peerDependencies:{"typescript":"<7"}` ← **真实上界在此** |
| `pnpm` | **12.21.0**（latest） | `11.1.1`（团队实测环境）/ 或 `12.21.0` | dist-tags `"latest-11":"11.28.12"` |
| `@playwright/test` | **1.65.1** | `^1.65.1` | — |

> **「本项目采用」列的原则**：
> - **`pinia` / `vue-router` / `typescript` 三项必须降线**（latest 分别是 4 / 5 / 7，全部会造成实际故障或大版本迁移）。
> - 其余按 latest 稳定版。
> - **精确 pin 由 `web/pnpm-lock.yaml` 决定**，在阶段二 `web/` 初始化时生成；本文档承诺的是**主版本区间 + 三条降线纪律**。

### 3.1 前端架构决策

| 决策 | 值 | 理由 |
|---|---|---|
| 应用形态 | **单应用三角色工作台** | 用户裁决。不是三个独立前端 |
| 路由守卫 | **全局守卫**按 `to.meta.roles` + 登录态 | 三角色共用入口，按角色渲染工作台 |
| 状态管理 | Pinia（setup store 风格） | Vue 3 官方推荐 |
| UI | Element Plus | 中后台表格/表单/弹窗完备 |
| 类型 | **严格 TS，禁止 `any`** | 团队规范 |
| E2E | **Playwright（Python，`pytest-playwright`）** | 与后端测试同语言，`uv run pytest` 一套命令。**非 Node 侧 `@playwright/test`** |

### 3.2 前端版本陷阱（**8 条，全部有实测依据或明确标注未验证**）

| # | 陷阱 | 后果 | 规避 | 等级 |
|---:|---|---|---|---|
| **1** | 🔴 **`typescript@latest` = 7.0.2 会让 `vue-tsc` 直接崩** | 装完报 `ERR_PACKAGE_PATH_NOT_EXPORTED: './lib/tsc'`（TS7 删了该导出） | **锁 `typescript@6.0.3`** | 🟢 实测崩溃 |
| **2** | 🔴 **`data-testid` 透传行为分三类**（不是两类） | C 类**静默丢弃**，Vue/TS/`vue-tsc` 全不报错，E2E 运行时才炸 | 见 §3.2.1 | 🟢 源码+运行时 |
| **3** | `element-plus` 的 `peer vue: ^3.3.7` 是**假下限** | 按 `^3.3.7` 装会与 pinia/vue-router 冲突 | 真实交集下限 **`>=3.5.34`**（pinia 要 `^3.5.11`、vue-router 要 `^3.5.34`） | 🟢 实测 peer |
| **4** | `vue-router` `latest` 是 **5.x 不是 4.x** | 直接 `pnpm add vue-router` 装到 v5，大版本 API 差异 | 显式 `^4.6.5`（`dist-tags.v4-latest`） | 🟢 实测 dist-tags |
| **5** | `pinia` `latest` 是 **4.x** | 装到 v4 | 显式 `^3.0.4` | 🟢 实测 |
| **6** | `@element-plus/icons-vue` **未废弃但极不活跃**（2.3.2 发布于 2025-08-05，15 个月无新版） | 若靠传递依赖引入，pnpm 严格提升下是**幽灵依赖** | **必须显式写进 `dependencies`** | 🟢 实测 |
| **7** | `vue` 包**没有 `engines` 字段** | 「`vue` 要求 Node X」这句话在 npm 元数据上无据可查 | Node 要求来自脚手架/文档：`^22.18.0 \|\| >=24.12.0`（Vue 文档 + `create-vue@3.24.0` 的 `engines` 一致） | 🟢 实测 |
| **8** | `vue` 3.6.0-rc.16 已发布但 `latest` 仍是 3.5.46 | 误装 rc 版本 | **禁用 rc**，锁 3.5.46 | 🟢 实测 dist-tags |

#### 3.2.1 🔴 Element Plus `data-testid` 透传：**三类组件行为不一致**

> **本节已从「两类」修正为「三类」**——`task-13` 的运行时实测（jsdom 挂载 `vue@3.5.46` + `element-plus@2.14.8`，20 个组件）发现了第三类：**静默丢弃**。

| 类 | 行为 | 组件 | 锚点写法 |
|---|---|---|---|
| **A** | 落到**内层原生控件** | `el-input`、`el-autocomplete`、`el-button` | **直接** `getByTestId('x').fill()` |
| **B** | 落到**根包装元素** | `el-checkbox`/`el-radio`→`label`；`el-select`/`el-table`/`el-pagination`/`el-dialog`/`el-form-item`/`el-switch`/`el-input-number`/`el-tabs`/`el-dropdown`/`el-color-picker`→`div` | 需 `.locator('input')` 等下钻 |
| **C** | 🔴 **静默丢弃（0 命中）** | `el-tooltip`、`el-popover`、`el-date-picker`、`el-time-picker`、`el-cascader` | **必须外层包 `<div data-testid>`** |

**A 类的源码证据**（`el-input`，tag `2.14.8`）：

| 行 | 内容 |
|---|---|
| `input.vue` L240 | `inheritAttrs: false` |
| `input.vue` L36（textarea 分支 L134） | **`v-bind="attrs"`** ← 在**内层 `<input>`** 上 |
| `input.vue` L270 | `const attrs = useAttrs()` —— **空调用** |
| `use-attrs/index.ts` L12 | `DEFAULT_EXCLUDE_KEYS = ['class','style']` |

**C 类的源码链路**（attrs 在此蒸发）：
```
date-picker.tsx L57-79  render 返回 <CommonPicker {...props}>，未转发 attrs
  → picker.vue L8       <el-tooltip ... v-bind="$attrs">
  → tooltip.vue L89-91  根是 <el-popper>
  → popper.vue          全文 53 行，只有 <slot /> + inheritAttrs: false
                        rg "attrs" popper.vue → 无匹配  ⇒  attrs 消失
```

> 🔴 **禁止的推论**：「因为 `el-input` 的 testid 不落根元素，所以要改用原生 `<input>`」。**错。** testid 落到内部 `<input>` 完全可用于 `getByTestId().fill()`。反过来，对 `el-input` 多写 `.locator('input')` 才是**必然 not found** 的失配源。
>
> 📌 **证据来源**：`.team/notes/research/stack-frontend.md` §3（`task-13`，`docs-writer` 独立复核生产物）。
> ⚠️ **未实测**：真实 Chromium 下的 `fill()`/`check()`/传送门定位；只抽测 20 / ~90 个组件；**`el-upload` 未验证**。


## 4. 项目目录结构规划

```
tomori/
├── pyproject.toml                 # ★ 唯一 Python 包定义（单包，见 §1.6）
│                                  #   [project.optional-dependencies] vision = [...]
├── backend/                       # 阶段二创建（根包下的子目录模块，无独立 pyproject.toml）
│   ├── alembic.ini
│   ├── migrations/{env.py,script.py.mako,versions/}
│   └── app/
│       ├── main.py                # create_app() + lifespan + 异常处理器注册
│       ├── settings.py            # pydantic-settings Settings
│       ├── db/{base.py,session.py,types.py}
│       ├── models/                # 17 张表，一表一文件
│       ├── schemas/               # Pydantic 请求/响应模型，与 models 一一对应
│       ├── api/{deps.py,errors.py,v1/}
│       ├── services/              # ★ 唯一允许改状态的地方
│       ├── vision/{client.py,mapping.py}
│       └── workers/{sla_scanner.py,notifier.py}
├── vision/                        # 阶段二规划目录（当前仅有未验证草稿，见 §2.2）
├── web/                           # 阶段二创建
└── tests/{conftest.py,unit/,integration/,e2e/}
```

### 4.1 分层纪律（实现期 code review 的硬判据）

| 层 | 允许 | 禁止 |
|---|---|---|
| `api/v1/*` | 解析请求、调 `services/`、序列化响应 | **不写业务规则**；不直接 `session.execute(update(...).values(status=...))` |
| `services/*` | 业务规则、状态机、事务编排 | 不 import `fastapi.Request/Response`（纯函数须可独立单测） |
| `models/*` | 表结构、约束、关系 | 不写业务逻辑 |
| `db/*` | 引擎/会话/元数据 | 不 import `models`（靠 `models/__init__.py` 汇总，避免循环） |

> **唯一例外**：`db/base.py` 提供 `MetaData(naming_convention=...)` 与 `Base`，方向是 `models → db`，不反向。

### 4.2 状态变更的唯一入口（**防绕过，硬纪律**）

`fault_tickets` / `dispatch_records` / `rework_orders` 的 `status` 更新**只允许**经由 `backend/app/services/state_machine.py` 的 `transition_*()` 函数。实现期任何 `session.execute(update(...).values(status=...))` 出现在 `state_machine.py` 之外，一律视为缺陷并要求改回。

**理由**：审计不变量 I7（每个状态变更必须有且仅有 1 条 `audit_logs`）与 I8（每个状态变更必须产生对应 `notification_tasks`）靠 code review 记不住，只能靠「唯一入口」物理保证。

---

## 5. 端口分配表

> 数据来源：`.team/notes/dev-ops/stage1-infra-facts.md`（`dev-ops` 实测环境事实）。**端口以 `.env.example` 与 `docs/RUNBOOK.md` 为最终权威**——本文档只做一致性核对，不自行分配端口。
>
> **「五方一致」是本项目自定的工程要求（`docs-writer` 提出，`Lead` 放行）**：`docs/TECH-STACK.md`（本表）/ `docs/RUNBOOK.md` / `compose.yaml` / `.env.example` / `deploy/stack-guard.sh`。本文档与其他四方不一致即为缺陷。
>
> 🔴 **归因修正**（`audits/stage1-audit.md` P0-5）：此处原写「`docs/team/TEAM.md` §5 要求的『五方一致』」，但 `rg "五方一致" docs/team/TEAM.md` **全库零命中**，且 `TEAM.md` §5 不讲端口一致性。**这是一条无出处的断言（违反 `docs/team/TEAM.md` §4.3 的 `F14`），但要求本身是合理的，故保留要求、只改归因。**
>
> ✅ **已被 `task-18` 消除的隐患**：本条原有一句「`docs/team/TEAM.md` 中旧参考项目的服务端口不适用于本项目」的警告——那些异项目端口已随 `task-18` 的 `docs/team/` 合并全部清除，`docs/team/` 目录内已**零命中**（实测 0 行）；`docs/` 下仅在 **§10 变更记录**（2026-11-21 审计修正行）中作为历史沿革保留，该警告在正文中已无对象可指，故删除。**端口数字的唯一权威是本文件下表与 `docs/RUNBOOK.md` §2。**

| 服务 | 端口 | 绑定 | 配置项 | 说明 |
|---|---|---|---|---|
| PostgreSQL 容器 | **`25432`** → `5432/tcp` | `127.0.0.1:25432` | `TOMORI_PG_PORT=25432` | **刻意避开 `15432`**（已被宿主机其他 PG 容器占用）与 `5432` |
| 后端 API（开发） | **`18141`** | `127.0.0.1:18141` | `BACKEND_PORT=18141` | FastAPI / uvicorn |
| Vite 前端（开发） | **`15173`** | `127.0.0.1:15173` | `FRONTEND_PORT=15173` | 开发热更新 |
| 后端 API（E2E） | **`18142`** | `127.0.0.1:18142` | `E2E_BACKEND_PORT=18142` | 仅 `claim-e2e` 独占窗口内使用 |
| Vite 前端（E2E） | **`15174`** | `127.0.0.1:15174` | `E2E_FRONTEND_PORT=15174` | 同上 |

> **为什么开发与 E2E 必须分端口**：E2E 要跑真实 Uvicorn + 真实前端，若复用开发端口，人工开着页面调试会与 E2E 抢同一进程/同一 DB 状态，导致间歇性假红。`deploy/stack-guard.sh claim-e2e` 提供独占锁（`.team/runtime/e2e.lock`）。

### 5.1 实测环境版本（`dev-ops` 采集）

| 组件 | 版本 | 备注 |
|---|---|---|
| PostgreSQL 服务端 | **`17.12`** | 镜像 `postgres:17.12-alpine`（**digest 钉版，禁 `:latest`**） |
| `psql` / `pg_isready` 客户端 | `18.7` | 宿主工具，版本高于服务端属正常 |
| Python（`.venv`） | **`3.12.11`** | 与 `.python-version=3.12`、`requires-python=">=3.12"` 三者一致 |
| `uv` | `0.8.14` | — |
| Node | `v26.13.1` | — |
| `pnpm` | `11.1.1` | — |
| Docker / Compose | `29.10.0` / `5.8.0` | — |

> ⚠️ **本表不含 `docker compose ps` 的原始输出**。`dev-ops` 素材只给出「容器达 `healthy`」的结论陈述，全量 `docker ps -a` 原始输出在其日志 `.team/notes/dev-ops/logs/stage1-final-evidence.log`。**若需粘贴原始输出，必须去该日志取并标明出处，不得声称来自本文档或 `stage1-infra-facts.md`。**

> 🔴 **本文档不复制 `docs/RUNBOOK.md` 的容器/卷/网络/密钥清单**。`RUNBOOK.md` 归 `dev-ops` 独占，是部署事实的唯一权威。**两份文档若冲突，以 `RUNBOOK.md` 为准，并回写本文档。**

---

## 6. 配置项与密钥纪律

| 项 | 纪律 |
|---|---|
| 配置文件 | `.env`（从 `.env.example` 复制）；`.env` **不进版本库** |
| 密钥 | `JWT_SECRET_KEY` **无默认值，缺失即启动失败**。不给默认密钥，杜绝生产误用开发密钥 |
| 数据库 URL | `postgresql+psycopg://...`；`alembic.ini` 的 `sqlalchemy.url` **留空**，由 `env.py` 从 `Settings` 读 |
| 上传上限 | `MAX_UPLOAD_SIZE_MB = 15`（= `15,728,640` B），Lead 已裁决 |
| 像素上限 | `40_000_000`（代码常量，与 `Image.MAX_IMAGE_PIXELS` 同值） |
| 视觉开关 | `YOLO_INFERENCE_ENABLED`；`false` 时 AI 候选端点返回 `503 VISION_DISABLED`，**纯人工报障不受影响** |

> **`.env.example` 归 `dev-ops` 独占**，本文档只描述语义，不修改文件。

---

## 7. 工具与方法的诚实记录

### 7.1 🔴 Context7 MCP 在本环境不可用

```
mcp__magpie__context7__resolve-library-id(libraryName="SQLAlchemy", query="SQLAlchemy 2.0 async ORM create_async_engine")
→ Error: Error searching libraries: TypeError: fetch failed

mcp__magpie__context7__resolve-library-id(libraryName="FastAPI", query="...")
→ Error: Error searching libraries: TypeError: fetch failed
```

- **复现次数**：`docs-writer` 独立复现 **4 次**（含 `libraryName="Vue"` 一次）；`frontend-dev` 亦独立复现。`docs-writer` 侧结果一致，均为 `TypeError: fetch failed`。

> 🔴 **两种故障形态都被观测到，必须并列记录（不得只写其中一种）**：
>
> | 观测方 | 形态 | 逐字返回 |
> |---|---|---|
> | `docs-writer`（4 次） | **网络层故障** | `Error: Error searching libraries: TypeError: fetch failed` |
> | `task-13` 独立复核（2 次） | **库索引陈旧**（无网络错误） | `resolve-library-id("Vue")` → 返回 `/vuejs/vue`，Versions 仅到 `v2.7.16`（**无 Vue 3**）；`query-docs` → 返回整段 "Vue 2 has reached End of Life" |
>
> **这两者不矛盾**：同一 MCP 服务在不同时刻可表现出不同故障面。**结论相同：无论哪种形态，Context7 都不能用于本项目的版本/API 核查。**
> `task-13` 明确指出「`fetch failed` 是错误归因」——**这一判断对 `task-13` 自身的观测成立，但不能否定 `docs-writer` 侧的 `fetch failed` 记录**。两者都是实测。
> **本文档的表述纪律**：写「不可用」，并把两种形态都列出；**不得**二选一，也不得声称「已修好」。
- **处置**：不隐藏、不静默降级。全部库文档结论改用下列通道，并逐条标注来源：

| 用途 | 实际通道 | 可靠性 |
|---|---|---|
| PyPI 包版本、`requires_python`、依赖约束 | `curl https://pypi.org/pypi/<pkg>/json` | 🟢 一手官方 registry |
| npm 包版本、`peerDependencies` | `curl https://registry.npmjs.org/<pkg>/latest` | 🟢 一手官方 registry |
| 库 API 行为 | GitHub raw 源码 | 🟢 一手 |
| 规范条文原文 | 官方标准 PDF / 政府网站（Exa 抓取） | 🟢 一手 |

> **这条限制留在 `docs/TECH-STACK.md` 里，是阶段一交付的真实合规记录。隐藏它 = `docs/team/TEAM.md` §3.1 的 **A 形态（名字骗人）**——宣称用了某条文档通道，实际那条通道是坏的。**同时违反 §4.5（F14）：任何「库文档已核对」的断言都必须能指出是哪条通道核对的。**

### 7.2 与「不做」清单的对照

| 用户「不做」项 | 本技术栈如何保证 |
|---|---|
| 不接灯控设备 | 无任何灯控协议/远程控制依赖；无 MQTT/IoT 客户端 |
| 不做能耗预测 | 无时序预测库；无能耗相关表 |
| 不接实时监控 | 无 RTSP/流媒体依赖；`415 MEDIA_VIDEO_STREAM_FORBIDDEN` 明确拒绝视频流字段 |

---

## 8. 待用户裁决清单 → 见 `docs/DECISION.md`

本文件相关的 6 项已并入 **`docs/DECISION.md`**（全项目唯一权威清单）：

| 原编号 | 事项 | `DECISION.md` | 状态 |
|---|---|---|---|
| `TS-1` | SQLAlchemy 是否锁 2.0 线（`2.1.9` 已 GA） | **`D-05`** | 🟡 `Lead` 已裁决 `<2.1`，待你确认 |
| `TS-2` | YOLO26 命名出处 + 走哪档 scale | **`D-06`** + **`D-33`** | 🟡 命名已闭环；档位暂定 `s`，待确认 |
| `TS-3` | `opencv-python-headless` 5.x 兼容性 | **`D-22`** | 🟡 阶段二首验 |
| `TS-4` | 前端精确版本 pin | **`D-23`** | 🟡 `^` + lock 文件 |
| `TS-5` | Element Plus `data-testid` 透传独立复核 | **`D-34`** | 🟢 已闭合（源码级确证，推翻原自查结论） |
| `TS-6` | 依赖落点：根 `pyproject.toml` vs 子包 | **`D-32`** | 🟢 已闭合（单包 + `vision` optional extra） |

> **两处据实同步**（原文已过时，已不再写进本文档）：
> - **`TS-2`**：`dev-ops` 在 `task-16` 已把 `docs/RUNBOOK.md` 训练 checkpoint 统一为 `yolo26s.pt`，与 `.env.example` 的 `YOLO_FALLBACK_MODEL` **两边已一致**——不存在「`RUNBOOK` 用 `n`、配置用 `s`」的分歧。
> - **`TS-5`**：`el-input` 透传 `data-testid` 已**源码级确证**（`input.vue` L240/L36 + `use-attrs` L12），落到内层原生 `<input>`；`frontend-dev` 原判断被 `Lead` 裁决 `P1-2` 推翻。

---

## 9. 本文档的诚实边界

**已证实的**（🟢）：
- 全部后端 13 个包的版本号、`requires_python`、依赖约束（PyPI 官方 registry 实测，采样 2026-11-21）。
- 唯一 A 档可训练数据集的许可证、规模、类别（HF API `siblings` 三路印证）。
- D2–D7 无许可证、D5 是占位仓库、D6 无数据（HF API + GitHub 全量树实测）。
- CC-BY-4.0 的署名义务原文（`creativecommons.org` 官方许可全文）。
- Context7 通道故障（`docs-writer` 独立复现 4 次 `fetch failed`；`task-13` 另观测到「索引陈旧」形态，两种形态均见 §7.1）。

**未证实的**（🟡 / 🔴）：
- **全部算法参数（SLA 时限、分级权重、派单权重、返修阈值）均为工程初值，未标定**。本文档不重复这些数值，见 `docs/BUSINESS-LOGIC.md`。
- ✅ **已由 `task-13` 补齐**：前端 11 个包的精确版本号与 `peerDependencies`（npm registry 实测）；8 条版本陷阱（其中 TS7 崩溃为**实测**）。
- ✅ **已由 `task-13` 补齐**：Element Plus `data-testid` 透传的**三类**行为（源码级 + jsdom 运行时实测）。**A 类 `el-input` 落内层 `<input>` 已确证**；C 类（tooltip 家族）静默丢弃已定位到 `popper.vue` 无 `$attrs`。
- 🟡 **仍未实测**：真实 Chromium 下的 `fill()`/`check()`；只抽测 20 / ~90 个组件；**`el-upload` 未验证**；`el-option` 透传**无任何出处**。
- 🟡 **仍未实测**：`typescript@6.0.3` + `vue-tsc` 只验证「能启动」，未在真实 SFC 项目跑完整类型检查。
- `opencv-python-headless` 5.x 与现有代码的兼容性。
- `httpx`/`httpx2` 与 `starlette>=1.6` 的兼容性。
- **`vision/` 的 8 个 `.py` 文件未经运行、未经测试**（§2.2）。
- **17 表完整 DDL 未在 PostgreSQL 上跑过**（阶段一不写迁移）。`backend-dev` 已抽验 3 条最要害约束在真 PG 17.12 上通过，但全量迁移未验证。
- **没有任何 `.py` 被写过**，故包结构与依赖清单是**设计**而非编译验证。

**明确不属于本文档的**：
- 端口/容器/部署细节 → `docs/RUNBOOK.md`（`dev-ops` 独占）
- 表结构 DDL → `docs/SCHEMA.md`
- 业务规则与状态机 → `docs/BUSINESS-LOGIC.md`
- API 端点与错误码 → `docs/API.md`
- 需求与验收判据 → `docs/REQUIREMENTS.md`
- 团队角色与协作纪律 → `docs/team/TEAM.md`、`docs/team/LEAD.md`
- **全部待裁决事项的唯一权威清单** → `docs/DECISION.md`

---

## 10. 变更记录

| 日期 | 变更 | 触发 |
|---|---|---|
| 2026-11-21 | 初版。定稿技术栈、版本决策记录、数据集合规与 CC-BY 署名、`vision/` 状态澄清、Context7 故障披露 | `task-7` |
| 2026-11-21 | **审计修正**：「五方一致」出处归因为**无出处断言**（`TEAM.md` 零命中）→ 改为本项目自定要求并保留要求本身；补注 `TEAM.md` 的 `18080`/`18081`/`19090`/`19092` **不适用于本项目**；§1.6 补「当前 0 依赖」事实说明 + 登记 `TS-6` 依赖落点待裁（**不替架构做决定**）。**技术选型与版本号零改动** | `audits/stage1-audit.md` P0-5/P0-6/P1-4 |
| 2026-11-21 | **`TS-6` 裁决落地**：依赖落点 = **单包 + `vision` optional extra**（不是双包）。根 `pyproject.toml` 唯一；`ultralytics`/`opencv-python-headless` 入 `[project.optional-dependencies] vision`，`uv sync --extra vision` 装。§1.6 新增裁决块与 `pyproject.toml` 骨架示例；§4 目录树删除 `backend/pyproject.toml` 行、改为根 `pyproject.toml` 唯一。**20 个依赖清单与版本号零改动** | `Lead` 裁决 A（`task-15`） |
| 2026-11-21 | **`TS-2` 命名出处闭环**：`YOLO26` 经 4 条一手证据核实为 Ultralytics **官方**模型家族（官方文档 `released January 2026` / `ultralytics==8.4.216` 内含 `cfg/models/26/yolo26.yaml` / `downloads.py` checkpoint 名单 / `assets v8.4.0` 权重 20,422,725 B）。§2.4.5 禁令从「不得断言」改为「**必须带下述出处**」——有出处即可写具体 checkpoint，无出处才违规。**同时指出 `RUNBOOK.md:222` 相邻的 `F14` 违例（把工程初值归因为官方指南），替换文案已交给 `dev-ops`** | `audits/stage1-audit.md` P1-2（`Lead` 裁决 B） |
| 2026-11-21 | **文档重组同步（`task-18`）**：§5 归因引用更新为 `docs/team/TEAM.md` §4.3，删除因 `docs/team/` 合并而失去对象的旧端口警告并写明口径（`docs/team/` 零命中、`docs/` 仅本表留史）；§8 改为指向 `docs/DECISION.md` 的短指针并同步 `TS-2`/`TS-5` 最新状态；§9 交叉引用同步 `SCHEMA.md` / `API.md` / `docs/team/` / `DECISION.md`。**技术选型与版本号零改动** | 用户要求 + `task-18` |
