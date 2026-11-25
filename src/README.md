# `src/` 源码结构（阶段二完结布局）

> **本目录为项目唯一应用源码根**——Python 源码统一位于 `src/backend/tomori/` 单包（`tomori.*` import），`alembic.ini`/`migrations/` 位于**仓库根**，`web/` 前端已并入 `src/frontend/`。

```text
src/
├── backend/
│   └── tomori/           # 后端 Python 包（可 `uv sync` 安装，`src` layout）
│       ├── api/          # 薄壳（deps/errors/v1：auth/dispatches/health/master_data/operations/tickets）
│       ├── services/     # 重逻辑（state_machine/ticket_service/dispatch/rework/sla/grading/auth/media/exceptions）
│       ├── models/       # SQLAlchemy 17 表（76 CHECK/41 FK/37 索引/6 部分唯一）
│       ├── schemas/      # Pydantic 出参/错误契约（auth/common/dispatch/master_data/operations/ticket）
│       ├── workers/      # notifier（通知投递）+ sla_scanner（定时 SLA 扫描）
│       ├── vision/       # 视觉 13 文件（whitelist/client/mapping/metrics/contract/errors/image_io/baseline/config/eda/dataset_prep/trainer/__init__）
│       ├── db/           # base / session / types
│       └── settings.py   # Settings（.env/application.yml 字段映射）
├── frontend/             # Vue 3 + TypeScript + Vite + Element Plus（pnpm 独立安装，不再用 `web/`）
│   ├── src/              # views / components / stores / api / router / main.ts
│   ├── vite.config.ts    # 开发/构建（proxy → 18141）
│   ├── package.json      # pin 精确版本（`D-23`）
│   └── dist/             # 构建产物（gitignored）
└── database/
    ├── schema.sql        # 17 表直建 DDL（独立于 Alembic，psql 直建）
    └── seed.sql          # 演示种子（3 角色/3 区域/4 账号/6 授权/5 规则/3 点位/2 工单）

config/application.example.yml  # `__PLACEHOLDER__` YAML 模板（与 `settings.py`/`env` 字段一一映射）
migrations/versions/            # Alembic 迁移（0001..0004）
tests/{unit,integration,e2e}    # 118 单测/集成 + 31 E2E
deploy/                         # stack-guard.sh + compose.yaml + init-dbs.sql
```

## 依赖安装

```bash
# 后端 Python 依赖（单包 src layout + vision extra 隔离）
uv sync                       # 主依赖（FastAPI / SQLAlchemy / psycopg / uvicorn / pydantic / alembic 等）
uv sync --extra vision        # 视觉依赖（ultralytics / onnxruntime / opencv-python-headless / pillow / numpy / onnx / onnxslim）

# 前端依赖（src/frontend/，pnpm）
pnpm --dir src/frontend install
```

## 构建与启动

```bash
# 后端
uv run uvicorn tomori.main:app --host 127.0.0.1 --port 18141

# 前端开发
pnpm --dir src/frontend dev           # http://127.0.0.1:15173

# 前端构建
pnpm --dir src/frontend build         # → src/frontend/dist/（gitignored）

# 类型检查/静态门禁
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src/backend/tomori
pnpm --dir src/frontend typecheck
```

## 关键纪律

- `import` 统一为 **`tomori.*`**（`src/backend/tomori` 为唯一包根，`module-root = "src"`）；
- `vision` 重型依赖（`ultralytics`/`onnxruntime`/`cv2`）走**函数内延迟导入**（`baseline`/`client`/`mapping` 层），保证纯后端无 `vision` extra 也可 `import tomori.*`；
- `vision` 模块共 **13** `.py` 文件（`baseline`/`client`/`config`/`contract`/`dataset_prep`/`eda`/`errors`/`image_io`/`__init__`/`mapping`/`metrics`/`trainer`/`whitelist`）；
- 运行时产物 `vision/{weights,reports,datasets,runs}`（gitignored）仍在**仓库根 `vision/`**，与 `src/backend/tomori/vision/` 源码分离；
- `web/` 目录已废止——前端权威位置为 **`src/frontend/`**。
