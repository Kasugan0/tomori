# Tomori 运维与基础设施手册（RUNBOOK）

> 适用项目：`tomori`（#141 公共照明故障工单系统）  
> 维护角色：`dev-ops`  
> 核心边界：本地容器化共享数据库栈 + 同机多项目端口隔离 + 远端 GPU（实测 RTX 4080 SUPER / 32G 显存）训练服务器对接规范。  
> **红线声明**：严禁停止、修改或干扰宿主机上任何非 `tomori` 项目的 Docker 容器、卷、网络与监听端口。

---

## 1. 宿主机实测基线与隔离原则

以下基线由 `dev-ops` 于阶段一在宿主机实测采集（测量命令见 `.team/notes/dev-ops/stage1-infra-facts.md`）：

- **宿主机系统**：Arch Linux（`7.3.5-arch1-1 x86_64`）
- **核心工具链**：
  - Python：宿主机 `Python 3.14.10`，项目 `.python-version` 为 `3.12`（`.venv` 实测 `Python 3.12.11`），`uv 0.8.14`
  - Node / 前端：Node `v26.13.1`，`pnpm 11.1.1`
  - PostgreSQL 客户端：`psql (PostgreSQL) 18.7`，`pg_isready (PostgreSQL) 18.7`
  - 容器引擎：Docker `29.10.0`，Docker Compose `5.8.0`
- **宿主机既有容器与已占用端口（严禁触碰）**：
  - `5432`：`artex-pg`（`postgres:16-alpine`）
  - `15432`：`new-new-api-postgres-1`（`postgres:17.11-alpine`）
  - `55434`：`agentutor-resume-postgres-1`、`55443`：`new-api-dev-pg`
  - `14222` / `14223`：`new-new-api-nats-1` / `new-new-api-nats_e2e-1`
  - `3782`、`8082`、`13783`、`16379`、`18002`、`27017`、`3306`、`6379` 等其他运行服务及 `32768–60999` 宿主机临时端口段（`ip_local_port_range`）。

因宿主机 `5432` 与 `15432` 均已被其他项目占用，本项目 PostgreSQL 映射端口避开 `15432` 而分配至 **`127.0.0.1:25432`**。

---

## 2. Tomori 专属端口与容器资源分配表

所有本地服务默认仅绑定 `127.0.0.1`，禁止直接暴露至 `0.0.0.0`：

| 组件 / 用途 | 绑定地址与端口 | 容器 / 隔离资源 | 说明 |
|---|---|---|---|
| **PostgreSQL 17.12** | `127.0.0.1:25432 -> 5432/tcp` | 容器 `tomori-postgres` / 卷 `tomori_pgdata` / 网络 `tomori_net` | 镜像钉死 `postgres:17.12-alpine@sha256:526f86d93c6ea08fd3f1fe5605650cacc781b2c6013169a0ad8f42c7d3d84f32` |
| **开发数据库** | 复用 `127.0.0.1:25432` | DB `tomori_dev`（owner: `tomori`） | 本地开发与人工调试专用 |
| **测试 / E2E 数据库** | 复用 `127.0.0.1:25432` | DB `tomori_test`（owner: `tomori`） | 自动化集成测试与 E2E 隔离专用 |
| **FastAPI 后端（开发）** | `127.0.0.1:18141` | 本地进程 | 日常开发 API 服务 |
| **Vite 前端（开发）** | `127.0.0.1:15173` | 本地进程 | 日常开发单应用三角色工作台 |
| **FastAPI 后端（E2E）** | `127.0.0.1:18142` | 受 `claim-e2e` 锁保护 | Playwright 端到端测试专用后端 |
| **Vite 前端（E2E）** | `127.0.0.1:15174` | 受 `claim-e2e` 锁保护 | Playwright 端到端测试专用前端 |

---

## 3. 环境配置与 psycopg v3 连接规范

项目根目录提供 `.env.example` 作为唯一配置模板：

```bash
cp .env.example .env
```

根据项目技术裁决，数据库驱动采用 **`psycopg` v3 异步栈**（与 SQLAlchemy 2.0 及 Alembic 配合）：

- **开发库连接串**：`DATABASE_URL=postgresql+psycopg://tomori:tomori_dev_password@127.0.0.1:25432/tomori_dev`
- **测试库连接串**：`TEST_DATABASE_URL=postgresql+psycopg://tomori:tomori_dev_password@127.0.0.1:25432/tomori_test`
- **E2E 连接串**：`E2E_DATABASE_URL=postgresql+psycopg://tomori:tomori_dev_password@127.0.0.1:25432/tomori_test`
- SQLAlchemy 2.0 中 `postgresql+psycopg://` 同时兼容 `create_async_engine()`（FastAPI 异步运行时）与同步/异步 Alembic 迁移引擎，无需混用 `psycopg2` 或 `asyncpg`。
- **静态图片与视觉权重配置**：
  - `UPLOAD_DIR=./uploads`：巡查报障图片与维修验收图片本地存储目录（不接实时视频流或灯控设备）。
  - `MAX_UPLOAD_SIZE_MB=15`：单文件上限 15 MiB，与视觉错误码契约「单文件 > 15 MiB => `413 IMAGE_SIZE_EXCEEDED`」一致（现代手机原图常 8–14 MiB，10 MiB 会误拒合规原图）。
  - `YOLO_WEIGHTS_PATH=./vision/weights/best.pt`：远端 GPU 服务器训练完成后回传至本地的模型权重路径。
  - `YOLO_DEVICE=cpu`：无 GPU 的本地推理环境使用 `cpu`；本地具备可用 CUDA GPU 时可设为 `0`（视觉开发与训练按 `D-06` 在远端 GPU 执行，不走此变量）。
  - `YOLO_IMG_SIZE=1024`：**必须与回传模型 `metrics.json` 中的 `imgsz` 完全相等**（`export(..., dynamic=False)` 已把输入尺寸固化进 ONNX 图），启用门禁见 §6.6。
  - `YOLO_INFERENCE_MODE=auto`：`auto` 允许自定义权重缺失时回退基线并如实标注；`strict_custom` 绝不回退，缺失自定义权重时健康检查显式报未就绪（上线检查项见 §6.6；互斥 `503` 错误契约见 `docs/API.md` §3.4 & `docs/DESIGN.md` §5.2）。
  - `YOLO_AUTOINSTALL=False`：禁止 Ultralytics 在推理或导出期静默触发 `uv`/`pip` 自动装包（`docs/DESIGN.md` §5.3；官方出处 `https://docs.ultralytics.com/modes/export/` 与 `ultralytics==8.4.216` `ultralytics/utils/__init__.py`）。
- **5 文档体系交叉引用**（阶段一定稿）：
  - `docs/DESIGN.md`：架构/单包目录/`pyproject.toml` `[project.optional-dependencies] vision`（§1.1–§1.2）、17 表 Schema 与异步迁移（§4）、视觉管线/昼夜判定/`D-30`/`V1–V3`/`D-29` 数据集元数据表（§5.1–§5.3）、质量门禁（§6.1）。
  - `docs/REQUIREMENTS.md`：五项核心红线 `N1–N5`（§5）、`V1–V3` 与 `D-18`/`D-29`/`D-30`/`D-31` 智能及数据集边界（§6）。
  - `docs/API.md`：`POST /tickets/ai-candidates` 媒体上传顺序与错误映射（§3.3 `413 IMAGE_SIZE_EXCEEDED`、§3.4 `503 VISION_*`、§3.5 两个 `415` 分界、§5 处理顺序；视觉降级契约 `D1–D3` 见 `docs/DESIGN.md` §5.2）。
  - `docs/DECISION.md`：`D-01`（远端 SSH）、`D-06`（只用 `yolo26s.pt`）、`D-10`（PG 17.12）、`D-28`（`imgsz=1024`）、`D-29`（个人项目直接使用数据集）、`D-30`（`streetlight_on` 回传参照）、`D-32`（单根包）。

> 注意：`.env`、`.env.local`、`uploads/`、`*.pt`、`*.onnx`、`vision/datasets/` 与 `.team/` 均不入 git，严禁将真实凭据、大体积图片或权重文件提交至仓库。

---

## 4. 本地共享栈操作规程（`deploy/stack-guard.sh`）

所有本地数据库栈起停与 E2E 独占窗口申请，必须统一通过 `./deploy/stack-guard.sh` 执行（禁止直接手写 `docker rm -f` 或 `docker compose down -v` 误删数据）。

### 4.1 常用命令

1. **启动并自检数据库栈**
   ```bash
   ./deploy/stack-guard.sh up
   ```
   执行内容：
   - 校验 `deploy/postgres/init-dbs.sql` 权限可被容器内 `postgres` 用户读取（至少 `0644`）。
   - 若 `tomori-postgres` 未运行，先通过 `ss -tlnH` 检查 `25432` 未被其他进程占用。
   - 调用 `docker compose -f compose.yaml up -d --wait` 等待健康检查通过。
   - 使用 `pg_isready` 与 `psql -h 127.0.0.1` 逐一验证 `tomori_dev` 与 `tomori_test` 均可通过密码认证连接读写。

2. **查看当前状态与端口占用**
   ```bash
   ./deploy/stack-guard.sh status
   ```
   输出容器状态、健康状态、`tomori_dev`/`tomori_test` 连通性、5 个分配端口（`25432/18141/15173/18142/15174`）监听状态及当前 E2E 锁状态。

3. **停止数据库栈（保留数据卷）**
   ```bash
   ./deploy/stack-guard.sh stop
   ```
   - 若存在活跃 E2E 锁（`.team/runtime/e2e.lock`），默认拒绝停止以防打断测试；确需强制停止时使用 `--force`。
   - 仅执行 `docker compose -f compose.yaml stop`，保留 `tomori_pgdata` 卷，不触碰任何其他项目容器。

4. **申请与释放 E2E 独占测试窗口**
   ```bash
   ./deploy/stack-guard.sh claim-e2e --owner tester
   # ... 在 18142 / 15174 端口 + tomori_test 库执行 E2E 测试，完成后务必关闭 E2E 前后端进程 ...
   ./deploy/stack-guard.sh release-e2e --owner tester
   ```
   - `claim-e2e` 会校验 `tomori-postgres` 健康、`tomori_test` 可连接、`18142` 与 `15174` 端口空闲，并原子创建 `.team/runtime/e2e.lock`。
   - `release-e2e` 在释放锁前会检查 `18142` 与 `15174` 是否已停止监听，防止遗留僵尸进程污染下一轮 E2E。

---

## 5. 数据库备份、恢复与初始化脚本重放

### 5.1 逻辑备份与恢复（强烈推荐）

```bash
# 备份开发库（自定义格式，支持选择性恢复）
mkdir -p .team/backups
docker exec -e PGPASSWORD=tomori_dev_password tomori-postgres \
  pg_dump -h 127.0.0.1 -U tomori -Fc tomori_dev > .team/backups/tomori_dev_$(date +%Y%m%d_%H%M%S).dump

# 恢复到开发库（--clean --if-exists 会覆盖现有对象，请先确认目标库）
cat .team/backups/tomori_dev_YYYYMMDD_HHMMSS.dump | \
  docker exec -i -e PGPASSWORD=tomori_dev_password tomori-postgres \
  pg_restore -h 127.0.0.1 -U tomori -d tomori_dev --clean --if-exists --no-owner
```

### 5.2 重放建库脚本（幂等）

`deploy/postgres/init-dbs.sql` 仅在 `tomori_pgdata` 首次为空初始化时由 PostgreSQL 容器入口自动执行。若历史上卷已初始化但缺少某个库，可从宿主机安全重放：

```bash
PGPASSWORD=tomori_dev_password psql -v ON_ERROR_STOP=1 \
  -h 127.0.0.1 -p 25432 -U tomori -d tomori_dev -f deploy/postgres/init-dbs.sql
```

### 5.3 彻底重建本地空库（危险操作，仅限本地销毁重置）

仅在明确放弃本地开发数据时执行，**严禁对全局 Docker 执行 `prune`**，必须显式限定 `-f compose.yaml`：

```bash
./deploy/stack-guard.sh stop --force
docker compose -f compose.yaml down -v
./deploy/stack-guard.sh up
```

---

## 6. 远端 GPU 训练服务器对接手册（实测 RTX 4080 SUPER / 32G 显存）

### 6.1 架构与职责定位

- 本系统视觉能力仅针对**静态巡查图片**进行公共照明故障检测（Ultralytics YOLO26 目标检测/故障分类，不接实时监控或远程灯控；安全边界 `V1–V3`、红线 `N1–N5` 与 `D-30`/`D-31` 见 `docs/DESIGN.md` §5.1–§5.2 及 `docs/REQUIREMENTS.md` §5–§6）。
  > 出处：Ultralytics 官方模型文档 https://docs.ultralytics.com/models/yolo26 （「released January 2026」，Detection 行列出 `yolo26n/s/m/l/x.pt`）；本项目锁定的 `ultralytics==8.4.216`（`docs/DESIGN.md` §1.2）内含 `ultralytics/cfg/models/26/yolo26.yaml`。核实记录见 `.team/notes/research/yolo26-official-sources.md`。
- **开发与训练全部在远端 GPU 服务器上执行**；本地研发机与业务后端仅负责**数据集打包上传 → 校验回传 `best.pt` / `best_e2e.onnx` 权重 → 本地进程内 `import vision` 集成推理**（`docs/DESIGN.md` §5.3）。
- 用户已裁决（`D-06`）：「**明确只用 yolo26s。没有本地冒烟这一说，以后必须在远程服务器上跑开发和训练。推理才会在本地跑。**」
  - **在远端做**：环境初始化、数据集拉取与清洗（`D-29 ✅`）、视觉代码开发、训练、ONNX 导出（`nms=False`）、离线评估。
  - **在本地做**：权重哈希校验、启用门禁（§6.6）、CPU/CUDA 推理验证与 FastAPI 联调。
  - **本地不做**：训练、开发调试、CPU「冒烟测试」——该路径已按用户裁决关闭。

### 6.1.1 远端环境实测基线（**已由 `dev-ops` 实测，非设计推测**）

> 实测依据：用户 `D-01` 提供 `ssh -p 13142` 可连后，`dev-ops` 于 **2026-11-24** 登录实测。以下为**实测值**，与任何设计假设冲突时**以本节为准**。

| 项目 | 实测值 | 结论 |
|---|---|---|
| GPU | `RTX 4080 SUPER` / `32760 MiB` | 32G 档（§6.4 只用 32G 分档） |
| 驱动 / 算力 | `595.71.05`；算力 `8.9`；CUDA toolkit `12.8` | `torch 2.8.0+cu128` 匹配 |
| 存储 | `/root/autodl-tmp` **230G（88G 可用）** `/dev/md0` xfs 可写；`/autodl-fs/data` 200G 空、可写；`/autodl-pub` 只读 | **统一落盘 `/root/autodl-tmp/tomori/`**（见 §6.1.2） |
| 根分区 | `overlay 30G`，已用 23G，**余 7.8G** | **约定：根分区零增长**，所有 uv 数据/venv/数据/权重一律不放根分区 |
| Python | 系统 `3.10.12`；`/root/miniconda3` 下 `3.12.3` | 用 `uv` 托管 `3.12`（见 §6.1.2） |
| 包管理 | **`uv 0.13.7` 已装**于 `/root/.local/bin/uv`（**不在非交互 SSH 的 `PATH` 中，须用绝对路径**）；`conda 24.4.0` 亦已装 | **无需安装 uv**（注：早期探测报「无 uv/无 conda」为非交互 `PATH` 缺项导致的误报，已更正） |
| 网络 | PyPI/清华/阿里镜像均 200；GitHub 不稳定；HuggingFace 超时、`hf-mirror` 200 | 用 `HF_ENDPOINT` + 必要时的 `UV_*_MIRROR`（见 §6.1.2） |

### 6.1.2 远端落盘方案（**已定案，无需用户裁决**）

> **决策来源**：用户确认「服务器放 `/root/autodl-tmp` 是对的。」以下方案由团队查明 Astral `uv` **官方文档**后定案，出处见 `.team/notes/dev-ops/uv-remote-setup-research.md`（逐条附官方 URL 与逐字原文）。

**落盘根**：`/root/autodl-tmp/tomori/`（`/dev/md0` xfs，230G，余 88G），根 `overlay`（余 7.8G）**零增长**。

```
/root/autodl-tmp/tomori/
├── repo/        # 代码
├── venv/        # UV_PROJECT_ENVIRONMENT
├── cache/uv/    # UV_CACHE_DIR
├── python/      # UV_PYTHON_INSTALL_DIR（托管 3.12）
├── tools/       # UV_TOOL_DIR
├── config/      # YOLO_CONFIG_DIR（存放 Ultralytics settings.json，不落 ~/.config）
├── hf/          # HF_HOME（权重与数据集缓存）
├── datasets/    # 数据集（含 D1 等公开数据集，D-29 ✅）
├── runs/        # 训练产物
└── weights/     # yolo26s.pt 底模
```

**环境变量**（写进远端 `~/.bashrc`，`tmux` 会话自然继承）：

```bash
export UV_CACHE_DIR=/root/autodl-tmp/tomori/cache/uv
export UV_PYTHON_INSTALL_DIR=/root/autodl-tmp/tomori/python
export UV_TOOL_DIR=/root/autodl-tmp/tomori/tools
export UV_PROJECT_ENVIRONMENT=/root/autodl-tmp/tomori/venv
export UV_PYTHON_BIN_DIR=/root/autodl-tmp/tomori/bin
export HF_HOME=/root/autodl-tmp/tomori/hf
export HF_ENDPOINT=https://hf-mirror.com
export YOLO_CONFIG_DIR=/root/autodl-tmp/tomori/config
export YOLO_AUTOINSTALL=False
```

| 变量 | 官方作用（逐字出自 https://docs.astral.sh/uv/reference/environment/ 与 https://docs.astral.sh/uv/reference/storage/ ，以及 Ultralytics 源码/文档） | 本项目取值理由 |
|---|---|---|
| `UV_CACHE_DIR` | 「use this directory for caching instead of the default cache directory」 | 默认 `~/.cache/uv` 在根分区；uv 缓存可达数 GB |
| `UV_PYTHON_INSTALL_DIR` | 「the directory for storing managed Python installations」 | 默认 `~/.local/share/uv/python` 在根分区 |
| `UV_TOOL_DIR` | 「the directory where uv stores managed tools」 | 同上 |
| `UV_PROJECT_ENVIRONMENT` | 「the path to the directory to use for a project virtual environment」 | 否则 venv 建在 `repo/.venv`，torch 3–6 GB 撑爆根分区 |
| `UV_PYTHON_BIN_DIR` | 「the directory to place links to installed, managed Python executables」 | `python3.12` 软链不落到 `~/.local/bin` |
| `HF_HOME` | 「where `huggingface_hub` will locally store data … the cache will be stored in this folder」 | 缓存默认在 `~/.cache/huggingface`（根分区） |
| `HF_ENDPOINT` | 维护者回复「set the environment variable: `export HF_ENDPOINT=https://hf-mirror.com`」 | 远端 `huggingface.co` 实测超时；须在 `import huggingface_hub` 前注入 |
| `YOLO_CONFIG_DIR` | `ultralytics==8.4.216` `utils/__init__.py` `get_user_config_dir()`：「Return a writable config dir, preferring `YOLO_CONFIG_DIR`」 | 默认 `~/.config/Ultralytics/settings.json` 在根分区 |
| `YOLO_AUTOINSTALL` | `https://docs.ultralytics.com/modes/export/`：「set `YOLO_AUTOINSTALL=False`」；`utils/__init__.py`：`env_bool("YOLO_AUTOINSTALL", True)` | 禁止训练/导出/推理期静默执行 `uv`/`pip` 装包破坏锁版本 |

> ⚠️ **官方明确警告**（https://docs.astral.sh/uv/reference/installer/ ）：「Changing the installation path only affects where the uv binary is installed. uv will still store its data (cache, Python installations, tools, etc.) in the default locations.」
> → **只设 `UV_INSTALL_DIR` 只搬二进制、不搬数据**，必须像上表这样另设数据目录变量。
>
> ⚠️ **官方性能要求**（https://docs.astral.sh/uv/concepts/cache/ ）：「For best performance, locate the cache directory on the same file system as the Python environment uv is operating on. Otherwise, uv cannot link files from the cache into the environment and must fall back to slower copy operations.」
> → 上表 `UV_CACHE_DIR` 与 `UV_PROJECT_ENVIRONMENT` **同在 `/dev/md0`**，满足该前提。

**`uv` 无需安装**：远端已有 `uv 0.13.7`，但**不在非交互 SSH 的 `PATH`** 中，因此 §6.3 一律用绝对路径 `/root/.local/bin/uv` 调用（或先 `export PATH=/root/.local/bin:$PATH`）。

**若将来确需重装**（官方命令，逐字）：
```bash
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="/root/autodl-tmp/tomori/bin" sh
```

**Python 3.12**：系统 `3.10.12` 不满足 `pyproject.toml` 的 `requires-python = ">=3.12"`，uv 会自动跳过系统 Python 并下载托管 3.12（官方原句，其中的版本号仅为文档示例：「uv will ensure that Python 3.11.6 is available — downloading and installing it if necessary」，见 https://docs.astral.sh/uv/concepts/python-versions/ ）。显式执行一次更可预期：
```bash
/root/.local/bin/uv python install 3.12
```
> ⚠️ **阶段二首个真实风险点**：托管 CPython 由 `python-build-standalone` 经 GitHub 拉取，而远端 GitHub 实测不稳定。官方提供 `UV_PYTHON_INSTALL_MIRROR` 变量可替换该下载源（`added in 0.2.35`），必要时再配。**本轮不放行实现，未预置镜像值**（未经核实的镜像地址不写入文档）。

### 6.2 远端接入参数清单（**除主机名外均已实测确认**）

| 类别 | 参数项 | 实测 / 已定值 | 说明与用途 |
|---|---|---|---|
| **SSH 接入** | `GPU_SSH_HOST` | 由 `.env` 提供（不入文档） | 远端 GPU 服务器地址 |
| **SSH 接入** | `GPU_SSH_PORT` | **`13142`** | 用户 `D-01`：「选择 A。ssh -p 13142 … 即可连接。」 |
| **SSH 接入** | `GPU_SSH_USER` | **`root`**（`uid=0`） | 实测登录身份 |
| **SSH 接入** | 认证方式 | **key-based 免密**（`BatchMode=yes` 零交互通过） | 无需口令 / `ssh-agent` 前置 |
| **硬件环境** | GPU 型号与数量 | **`NVIDIA GeForce RTX 4080 SUPER` × 1，`32760 MiB`** | 按 32G 档选 `batch` |
| **硬件环境** | 驱动 / CUDA | 驱动 `595.71.05`；toolkit `12.8`；算力 `8.9` | 用 `cu128` wheel（已预装 torch） |
| **存储路径** | 落盘根目录 | **`/root/autodl-tmp/tomori/`** | 见 §6.1.2；230G，根分区零增长 |
| **存储路径** | 数据集目录 | **`/root/autodl-tmp/tomori/datasets/`** | |
| **存储路径** | 训练输出目录 | **`/root/autodl-tmp/tomori/runs/`** | 存 `weights/best.pt`、`results.csv` |
| **存储路径** | 底模目录 | **`/root/autodl-tmp/tomori/weights/`** | `yolo26s.pt` = 20,422,725 B |
| **网络与源** | PyPI | 官方源与清华/阿里镜像**均可达** | 可选加速：`UV_DEFAULT_INDEX`（**非**弃用的 `UV_INDEX_URL`） |
| **网络与源** | HuggingFace | 直连**超时**；`hf-mirror.com` 可达 | `HF_ENDPOINT=https://hf-mirror.com` |
| **网络与源** | 底模下载 | GitHub releases **不稳定** | **首选：本地下载后 `rsync` 上传**；见 §6.4 |

### 6.3 远端训练环境初始化规程

**前提**：§6.1.2 的 9 个环境变量已写入远端 `~/.bashrc`。

1. **配置本地 `~/.ssh/config` 别名**（推荐 `tomori-gpu`）：
   ```
   Host tomori-gpu
       HostName <GPU_SSH_HOST>
       Port 13142
       User root
   ```
   实测连通与 GPU：
   ```bash
   ssh tomori-gpu "hostname && nvidia-smi --query-gpu=name,memory.total --format=csv && df -h /root/autodl-tmp"
   ```

2. **建立落盘目录**（全部在 `/root/autodl-tmp`，**不碰根分区**）：
   ```bash
   ssh tomori-gpu "mkdir -p /root/autodl-tmp/tomori/{repo,venv,cache/uv,python,tools,config,hf,datasets,runs,weights,bin}"
   ```

3. **安装托管 Python 3.12**（系统 `3.10.12` 不满足 `requires-python >= 3.12`）：
   ```bash
   ssh tomori-gpu "/root/.local/bin/uv python install 3.12 && /root/.local/bin/uv python list --only-installed"
   ```
   > `uv` **已预装**（`0.13.7`）但不在非交互 `PATH` 中，用绝对路径调用。安装目录由 `UV_PYTHON_INSTALL_DIR` 指到 `/root/autodl-tmp/tomori/python`。

4. **克隆代码并安装 `[project.optional-dependencies] vision` 虚拟环境**（venv 落在 `UV_PROJECT_ENVIRONMENT` 指定的路径，**不建 `repo/.venv`**）：
   ```bash
   ssh tomori-gpu "cd /root/autodl-tmp/tomori/repo && /root/.local/bin/uv sync --extra vision --python 3.12"
   ```
   > **单根包 `D-32` 依据（`docs/DESIGN.md` §1.1–§1.2）**：仓库根仅有一份 `pyproject.toml` + `uv.lock`，纯后端仅跑 `uv sync`；**远端视觉开发/训练必须带 `--extra vision`** 以安装 `ultralytics==8.4.216`、`opencv-python-headless`、`onnxruntime` 与 `torch`。**根分区零增长**是验收点：完成后执行 `df -h /` 确认 `Avail` 未明显下降。
   > 若导出 ONNX 时启用了 `simplify=True`（Ultralytics 默认开启）且 `YOLO_AUTOINSTALL=False` 阻止静默装包，须提前在 `pyproject.toml`（或 dev/vision 依赖）中显式包含 `onnx`、`onnxslim` 等导出所需包。

5. **绑定 Ultralytics 目录设置并验证 CUDA 可见性**：
   ```bash
   ssh tomori-gpu "source ~/.bashrc && /root/autodl-tmp/tomori/venv/bin/yolo settings \
     datasets_dir=/root/autodl-tmp/tomori/datasets \
     weights_dir=/root/autodl-tmp/tomori/weights \
     runs_dir=/root/autodl-tmp/tomori/runs"
   ssh tomori-gpu "source ~/.bashrc && /root/autodl-tmp/tomori/venv/bin/python -c 'import torch, ultralytics; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0), round(torch.cuda.get_device_properties(0).total_memory/1024**3,1), ultralytics.__version__)'"
   ```
   > 依据 `https://docs.ultralytics.com/usage/settings/`：`yolo settings` 将持久配置写入 `$YOLO_CONFIG_DIR/Ultralytics/settings.json`（不写根分区 `~/.config`）。现 conda base 已有 `torch 2.8.0+cu128`，但**新的 uv 虚拟环境不继承它**，由 `uv sync --extra vision` 独立安装；不推荐使用 `--system-site-packages`。

6. **验收门禁**：`df -h /` 的 `Avail` 不低于初始化前的 7.8G；`/root/autodl-tmp/tomori/` 下各目录均存在；`YOLO_AUTOINSTALL=False` 已生效。

### 6.4 数据集拉取、训练与 ONNX 导出编排

0. **`D-29 ✅` 数据集使用边界与远端直拉（免发函，守 `F14` 事实纪律）**：
   - **授权定案（`docs/DECISION.md` `D-29`、`docs/REQUIREMENTS.md` §6.2、`docs/DESIGN.md` §5.3）**：本项目为个人本地研究与练手项目（业务与推理在本机运行，模型开发与训练按 `D-06` 在个人远端 GPU 执行，不对外商用分发），**D1–D7 公开与自采数据集均可直接用于阶段二训练与实验，无需向原作者发函申请商用授权**；同时按 `F14` 在 `docs/DESIGN.md` §5.3 保留 D1–D7 真实原始许可与质量元数据。
   - **远端拉取 `D1`（`ngocleltt/lamp-state-detection`，`cc-by-4.0`，8,428 张 `1280×720` JPG，1.69 GB）**：因远端直连 `huggingface.co` 超时，须走 `HF_ENDPOINT=https://hf-mirror.com`；官方 `huggingface_hub` CLI 命令为 `hf download`（`https://huggingface.co/docs/huggingface_hub/guides/cli`）：
     ```bash
     # 在远端执行（加 HF_HUB_DISABLE_XET=1 规避 hf-mirror 上可能出现的 xet-read-token 404，
     # 依据：https://hf-mirror.com/ 与 huggingface_hub env var 文档）：
     ssh tomori-gpu "source ~/.bashrc && \
       HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \
       /root/autodl-tmp/tomori/venv/bin/hf download \
       --repo-type dataset ngocleltt/lamp-state-detection \
       --local-dir /root/autodl-tmp/tomori/datasets/d1_lamp_state"
     ```
   - **D1–D7 清洗防泄漏硬纪律（详见 `docs/DESIGN.md` §5.3）**：
     - `D1`：`metadata.csv` 含 `video_id` 连续抽帧，**必须按 `video_id` 分组切分**（`70%/15%/15%`），严禁随机打散跨集泄漏。
     - `D5`（`Team16Project/Street-Light-Dataset`）：**必须剔除 `Flicker/` 目录下 `.mp4` 视频文件**（守 `N1` 禁实时视频红线），仅静态图可参考。
     - `D7`（`poles-defect/damaged_pole` v2）：含 `3×` 预增强，引入前**须先按 64-bit pHash 去重防泄漏**。
     - `val`/`test` 严禁混入合成增强样本。

1. **增量同步自采/预处理数据集（禁止通过 git 传数据集）**：
   ```bash
   rsync -avz --progress --partial ./vision/datasets/ tomori-gpu:/root/autodl-tmp/tomori/datasets/
   ```
   同步前确认远端 `data.yaml` 中的 `path` 指向 `/root/autodl-tmp/tomori/datasets/...`。

2. **底模上传（首选路径）**：远端 `github.com` 实测不稳定，**先在本地下载再上传**：
   ```bash
   # 本地下载官方权重（20,422,725 B）
   curl -L -o ./yolo26s.pt https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26s.pt
   # 校验体积后上传
   stat -c '%s' ./yolo26s.pt
   rsync -avz --progress ./yolo26s.pt tomori-gpu:/root/autodl-tmp/tomori/weights/
   ```
   备选：远端 `source /etc/network_turbo` 开学术加速后直连下载（该脚本自述「不承诺稳定性」）。

3. **在持久会话中启动训练（防 SSH 断开中断，日志落盘）**：
   使用 `tmux` 启动训练（`tmux`/`screen` 实测均已安装）：
   ```bash
   ssh -t tomori-gpu "tmux new -s tomori-train"
   # 在 tmux 会话内执行（环境变量已由 ~/.bashrc 注入；用 venv 内 yolo）：
   cd /root/autodl-tmp/tomori/repo
   /root/autodl-tmp/tomori/venv/bin/yolo train \
     model=/root/autodl-tmp/tomori/weights/yolo26s.pt \
     data=/root/autodl-tmp/tomori/datasets/streetlight/data.yaml \
     epochs=120 \
     imgsz=1024 \
     batch=32 \
     device=0 \
     amp=True \
     flipud=0.0 \
     hsv_v=0.25 \
     hsv_s=0.4 \
     project=/root/autodl-tmp/tomori/runs \
     name=streetlight_v1 \
     2>&1 | tee /root/autodl-tmp/tomori/runs/train_streetlight_v1.log
   ```
   > **增强参数说明（防昼夜洗白，`docs/DESIGN.md` §5.3）**：`ultralytics==8.4.216` `cfg/default.yaml` 默认 `hsv_s=0.7`、`hsv_v=0.4`，本项目必须显式压低为 `flipud=0.0`、`hsv_v=0.25`、`hsv_s=0.4`，避免把夜间 `streetlight_off` 洗白为发光状态或上下颠倒路灯拓扑。
   > **只用 `yolo26s.pt` 一个档位**（用户 `D-06`）。`n`/`m`/`l`/`x` 一律不使用；需要更强表达力时通过 `imgsz` 与数据增强解决，**不换 scale**（§6.4 末注）。
   > 出处：`yolo26n/s/m/l/x.pt` 均为官方真实文件名，见 https://docs.ultralytics.com/models/yolo26 （Detection 行）；权重可从 https://github.com/ultralytics/assets/releases/tag/v8.4.0 下载。核实记录见 `.team/notes/research/yolo26-official-sources.md`。
   > **`imgsz` 必须与 `.env` 的 `YOLO_IMG_SIZE` 完全一致（当前 `1024`）**：训练尺寸 → `export(..., imgsz=1024, nms=False, dynamic=False)` → `.env` → 推理端四者必须同值，否则 ONNX 图内固化的输入尺寸与推理传入尺寸不符，首次推理即张量形状报错（`docs/DESIGN.md` §5.3）。
   > **显存调参建议（本机实测 32G 档）**
   >
   > 🟢 **有官方出处的一条**：
   > - 建议先用 `batch=-1` 让 AutoBatch 自动探测（官方定量：单卡目标约 **60% 显存占用**，也可写小数如 `batch=0.70`），再取探测值向下取 2 的幂写死，保证可复现。
   >   出处：https://docs.ultralytics.com/guides/model-training-tips ；`ultralytics==8.4.216` 的 `cfg/default.yaml` 逐字 `batch: 16 # (int | float) batch size as int (e.g. 16), -1 for AutoBatch, or float in (0.0, 1.0) for GPU memory fraction`
   >
   > 🟡 **以下为本项目工程初值，未标定，官方未给出按显存分档的具体数字**：
   > - **本机实测显存为 32G（`32760 MiB`），只用 32G 分档**：`imgsz=1024` 下暂定 `batch=16~32`。48G 分档（`batch=32~64`）留档供未来机型，当前不适用。
   > - `epochs=120` 亦为工程初值，非官方推荐轮数。
   > - 小目标（如远景灯头）需要更高分辨率时，通过**提高 `imgsz`、调整数据增强**等手段处理，**不换 scale**（用户已明确只用 `yolo26s`，故 `m`/`l`/`x` 不在阶段二选项内）。
   > - 若样本量 `< 1,000` 张，可配合小数据集配方：`optimizer=AdamW lr0=0.001 patience=20 mosaic=0.5 mixup=0.0`。
   >
   > ⚠️ **两条易错点（无出处说法已删除）**：
   > - 官方对 OOM 的原话只是「**reduce the batch size incrementally until the model trains smoothly**」（渐进减小 `batch`），**未**承诺「单卡 OOM 会自动减半重试」——该说法无出处，已从本文档删除。OOM 会直接中断训练，请据实减小 `batch` 后重跑。
   > - 官方 `imgsz` 默认是 **`640`**；本项目用 `1024` 是为远景小目标自选的**工程初值**，**不是官方推荐值**。
   >
   > **写作纪律**：本段落曾把「项目工程初值」与「官方结论」混在一句「依据官方指南」之下，导致读者连带误信。**每条断言各自标出处，不共享段落级出处。**

4. **导出端到端无 NMS ONNX（`best_e2e.onnx`，`docs/DESIGN.md` §5.3）**：
   ```bash
   /root/autodl-tmp/tomori/venv/bin/yolo export \
     model=/root/autodl-tmp/tomori/runs/streetlight_v1/weights/best.pt \
     format=onnx \
     imgsz=1024 \
     nms=False \
     dynamic=False
   # Ultralytics 默认输出 weights/best.onnx（见 https://docs.ultralytics.com/modes/export/），
   # 显式留存为约定文件名 best_e2e.onnx 供本地 onnxruntime.InferenceSession 加载：
   cp /root/autodl-tmp/tomori/runs/streetlight_v1/weights/best.onnx \
      /root/autodl-tmp/tomori/runs/streetlight_v1/weights/best_e2e.onnx
   ```
   > 导出前确保已设置 `YOLO_AUTOINSTALL=False`；如缺少 `onnx`/`onnxslim`，Ultralytics 会显式报错提示缺少依赖而不会静默装包（`https://docs.ultralytics.com/modes/export/`）。不硬编码 `opset=17`（沿用 Ultralytics 官方默认 opset 18，`docs/DESIGN.md` §5.3），回传本地统一扁平放置于 `./vision/weights/{best.pt,best_e2e.onnx}`。

### 6.5 训练产物回传与完整性校验规程

训练与导出结束后，严禁仅凭口头汇报或手工改名回传模型，必须执行哈希校验与指标留证：

1. **远端生成校验文件与确认产物清单**：
   ```bash
   ssh tomori-gpu "cd /root/autodl-tmp/tomori/runs/streetlight_v1 && sha256sum weights/best.pt weights/best_e2e.onnx weights/last.pt results.csv args.yaml metrics.json > SHA256SUMS && cat SHA256SUMS"
   ```
2. **回传权重与评估指标至本地（权重放本地 ignored 目录 `./vision/weights/`）**：
   ```bash
   mkdir -p ./vision/weights ./vision/reports/streetlight_v1
   rsync -avz --progress tomori-gpu:/root/autodl-tmp/tomori/runs/streetlight_v1/weights/best.pt ./vision/weights/best.pt
   rsync -avz --progress tomori-gpu:/root/autodl-tmp/tomori/runs/streetlight_v1/weights/best_e2e.onnx ./vision/weights/best_e2e.onnx
   rsync -avz --progress tomori-gpu:/root/autodl-tmp/tomori/runs/streetlight_v1/SHA256SUMS ./vision/reports/streetlight_v1/
   rsync -avz --progress tomori-gpu:/root/autodl-tmp/tomori/runs/streetlight_v1/results.csv ./vision/reports/streetlight_v1/
   rsync -avz --progress tomori-gpu:/root/autodl-tmp/tomori/runs/streetlight_v1/args.yaml ./vision/reports/streetlight_v1/
   rsync -avz --progress tomori-gpu:/root/autodl-tmp/tomori/runs/streetlight_v1/metrics.json ./vision/reports/streetlight_v1/
   ```
3. **权重回传与完整性校验**（本地**只做校验与推理验证，不做训练/开发**）：
   ```bash
   sha256sum ./vision/weights/best.pt ./vision/weights/best_e2e.onnx
   # 与 ./vision/reports/streetlight_v1/SHA256SUMS 中 weights/best.pt 与 weights/best_e2e.onnx 的哈希逐字符比对
   ```
   确认本地 `.env` 中 `YOLO_WEIGHTS_PATH=./vision/weights/best.pt`（与同目录 `best_e2e.onnx`）及 `YOLO_AUTOINSTALL=False` 已就绪，并在本地通过 `onnxruntime.InferenceSession` 执行推理验证（`CPUExecutionProvider` 或可用 CUDA）确认输入/输出张量、类别映射与阈值符合 `docs/DESIGN.md` §5.1–§5.3 契约后再交付联调。

### 6.6 权重启用门禁：`imgsz` 一致性与推理模式检查（任一失败即拒绝启动）

回传权重**不得直接投入使用**。启用前必须逐项核对，任一项不通过即拒绝加载该权重，禁止以「先跑起来再说」降级处理（`docs/DESIGN.md` §5.2–§5.3、`docs/API.md` §3.4）：

1. **`imgsz` 一致性门禁（硬性）**：核对 `metrics.json` 的 `imgsz` 字段与 `.env` 的 `YOLO_IMG_SIZE` **完全相等**（当前均应为 `1024`）。
   ```bash
   # 回传的 metrics.json 中记录的训练/导出尺寸
   rg -n '"imgsz"' ./vision/reports/streetlight_v1/metrics.json
   # 拟用于推理的环境变量取值
   rg -n '^YOLO_IMG_SIZE=' .env
   ```
   `export(..., dynamic=False)` 已将输入尺寸固化进 ONNX 计算图；两者不一致会在**首次推理**时抛出张量形状不匹配错误（`onnxruntime` 抛 `INVALID_ARGUMENT`）。**不一致则拒绝启动**，必须先按正确尺寸重新导出（或修正 `.env` 后重启），不得放行。
2. **`strict_custom` 模式上线前检查项**：若线上要求「必须加载自定义权重、绝不允许基线兜底」，将 `.env` 置为 `YOLO_INFERENCE_MODE=strict_custom`（且保留 `YOLO_AUTOINSTALL=False`），并在上线前确认以下全部成立：
   - [ ] `.env` 中 `YOLO_INFERENCE_MODE=strict_custom`（生产环境不得为 `auto`）。
   - [ ] `YOLO_WEIGHTS_PATH`（及 `best_e2e.onnx`）指向的权重文件**确实存在**且 `sha256sum -c SHA256SUMS` 通过。
   - [ ] 权重启用门禁其余项全部通过：ONNX 输入 `(1, 3, YOLO_IMG_SIZE, YOLO_IMG_SIZE)`、输出 `(1, 300, 6)`（`nms=False` 端到端头）；`metrics.json` 类别列表顺序与代码枚举（`streetlight_on`/`streetlight_off`/`streetlight_damaged`/`streetlight_occluded`）一致；已知真值样例图推理验证返回 ≥1 个候选。
   - [ ] **反向验证（必须实测）**：临时把 `YOLO_WEIGHTS_PATH` 指向一个不存在的路径并重启，确认健康检查/启动日志**显式报未就绪**且视觉接口返回 **`503 VISION_DISABLED`**（`docs/API.md` §3.4、`docs/DESIGN.md` §5.2），而不是静默改用 `YOLO_FALLBACK_MODEL` 继续对外服务；验证后恢复正确路径。
   - [ ] 健康检查/启动日志在失败时打印**具体失败环节**（`sha256` 不匹配 / 输出形状不符 / 类别列表不符 / 推理验证空结果 / 权重缺失），而非笼统的 "failed to load model"。
3. **`auto` 模式的诚实标注要求**：`YOLO_INFERENCE_MODE=auto` 且自定义权重缺失时允许回退到 `YOLO_FALLBACK_MODEL`（或内置 `baseline_pretrained_opencv`），但回退期间响应必须如实标注 `ai_is_baseline_mode=true` 且 `inference_mode=baseline_pretrained_opencv`，融合置信度受后端 `fault_evidences` 表约束 **`ck_fault_evidences_baseline_confidence_cap`**（`docs/DESIGN.md` §4.3 / §5.1）限制；夜间正常亮灯 `streetlight_on` 按 `D-30` 回传参照框且 `is_fault_candidate=false`；严禁以基线权重冒充定制模型对外服务。
