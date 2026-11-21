# Tomori 运维与基础设施手册（RUNBOOK）

> 适用项目：`tomori`（#141 公共照明故障工单系统）  
> 维护角色：`dev-ops`  
> 核心边界：本地容器化共享数据库栈 + 同机多项目端口隔离 + 远端 32G–48G 显存 GPU 训练服务器对接规范。  
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
  - `YOLO_DEVICE=cpu`：无 GPU 的本地开发/CI 环境使用 `cpu`；本地具备可用 CUDA GPU 时可设为 `0`。
  - `YOLO_IMG_SIZE=1024`：**必须与回传模型 `metrics.json` 中的 `imgsz` 完全相等**（`export(..., dynamic=False)` 已把输入尺寸固化进 ONNX 图），启用门禁见 §6.6。
  - `YOLO_INFERENCE_MODE=auto`：`auto` 允许自定义权重缺失时回退基线并如实标注；`strict_custom` 绝不回退，缺失自定义权重时健康检查显式报未就绪（上线检查项见 §6.6）。

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

## 6. 远端 GPU 训练服务器对接手册（32G–48G 显存）

### 6.1 架构与职责定位

- 本系统视觉能力仅针对**静态巡查图片**进行公共照明故障检测（Ultralytics YOLO26 目标检测/故障分类，不接实时监控或远程灯控）。
  > 出处：Ultralytics 官方模型文档 https://docs.ultralytics.com/models/yolo26 （「released January 2026」，Detection 行列出 `yolo26n/s/m/l/x.pt`）；本项目锁定的 `ultralytics==8.4.216` 内含 `ultralytics/cfg/models/26/yolo26.yaml`。核实记录见 `.team/notes/research/yolo26-official-sources.md`。
- 模型训练在用户提供的 **32G–48G 显存 GPU 服务器**上完成；本地研发机与业务后端仅负责**数据集打包上传 → 远端触发训练 → 校验回传 `best.pt` 权重 → 本地集成推理**。

### 6.2 待用户提供的接入参数清单（阶段二训练前置依赖）

在启动远端 YOLO26 训练前，需请用户（或由 Lead 向用户收集）提供以下参数：

| 类别 | 参数项 | 示例 / 格式 | 说明与用途 |
|---|---|---|---|
| **SSH 接入** | `GPU_SSH_HOST` | `192.168.x.x` 或域名 | 远端 GPU 服务器地址 |
| **SSH 接入** | `GPU_SSH_PORT` | `22` 或自定义端口 | SSH 端口 |
| **SSH 接入** | `GPU_SSH_USER` | `ubuntu` / `yiming` | 登录用户名 |
| **SSH 接入** | 认证方式 / 跳板机 | `~/.ssh/id_ed25519`、`ProxyJump` | 私钥路径或 SSH Config 别名 |
| **硬件环境** | GPU 型号与数量 | 如 `RTX 6000 Ada 48GB × 1` / `A100 40GB` | 决定 `batch`、`imgsz` 与模型尺寸 |
| **硬件环境** | 驱动与 CUDA 版本 | `nvidia-smi` 输出 | 决定 PyTorch CUDA wheel 版本 |
| **存储路径** | 持久化工作目录 | `/workspace/tomori-vision` | 存放训练代码与虚拟环境（确认重启不丢失） |
| **存储路径** | 数据集存放目录 | `/data/tomori/datasets` | 存放静态巡查图片与 YOLO 标注集（确认剩余磁盘空间） |
| **存储路径** | 训练输出目录 | `/workspace/tomori-vision/runs` | 保存 `weights/best.pt`、`results.csv` 与评估图表 |
| **网络与源** | 包源 / 权重下载能力 | 可联网 / 需国内镜像 / 离线 | 是否需配置 PyPI 镜像或预先上传底模 `yolo26s.pt`（官方权重下载：https://github.com/ultralytics/assets/releases/tag/v8.4.0 ，`yolo26s.pt` = 20,422,725 B；离线环境须预先上传） |

### 6.3 远端训练环境初始化规程

获取上述 SSH 参数后，按以下步骤初始化远端独立训练环境（避免污染系统 Python）：

1. **配置本地 `~/.ssh/config` 别名**（推荐命名为 `tomori-gpu`），并实测 SSH 与 GPU 状态：
   ```bash
   ssh tomori-gpu "hostname && nvidia-smi && df -h"
   ```
2. **在远端建立独立目录与虚拟环境**（建议 Python `3.12` 与本地 `.python-version` 一致）：
   ```bash
   ssh tomori-gpu "mkdir -p /workspace/tomori-vision /data/tomori/datasets"
   # 若远端已安装 uv：
   ssh tomori-gpu "cd /workspace/tomori-vision && uv venv --python 3.12 .venv && source .venv/bin/activate && uv pip install ultralytics torch torchvision"
   ```
   （若远端使用 `conda` 或需指定 CUDA index-url，按实际 `nvidia-smi` 最高支持 CUDA 版本选择对应 PyTorch wheel。）
3. **验证 PyTorch CUDA 与显存可见性**：
   ```bash
   ssh tomori-gpu "cd /workspace/tomori-vision && .venv/bin/python -c 'import torch, ultralytics; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0), round(torch.cuda.get_device_properties(0).total_memory/1024**3, 1))'"
   ```

### 6.4 数据集上传与训练任务编排

1. **增量同步数据集（禁止通过 git 传数据集）**：
   ```bash
   rsync -avz --progress --partial ./vision/datasets/ tomori-gpu:/data/tomori/datasets/
   ```
   同步前确认远端 `data.yaml` 中的 `path` 指向远端实际路径 `/data/tomori/datasets/...`。

2. **在持久会话中启动训练（防 SSH 断开中断，日志落盘）**：
   使用 `tmux` 或 `screen` 启动训练并将输出写入日志文件：
   ```bash
   ssh -t tomori-gpu "tmux new -s tomori-train"
   # 在 tmux 会话内执行：
   cd /workspace/tomori-vision
   source .venv/bin/activate
   yolo train \
     model=yolo26s.pt \
     data=/data/tomori/datasets/streetlight/data.yaml \
     epochs=120 \
     imgsz=1024 \
     batch=32 \
     device=0 \
     amp=True \
     project=/workspace/tomori-vision/runs \
     name=streetlight_v1 \
     2>&1 | tee /workspace/tomori-vision/runs/train_streetlight_v1.log
   ```
   > **起始 checkpoint 统一为 `yolo26s.pt`**（与 `.env` 的 `YOLO_FALLBACK_MODEL` 同一档位）：32G–48G 显存下 `s` 相对 `n` 的耗时差异可忽略，统一成一个档位可避免「`n` 还是 `s` 才是主模型」的认知负担。**本地 CPU 冒烟测试可临时用 `yolo26n.pt`，但交付训练一律从 `s` 起。**
   > 出处：`yolo26n/s/m/l/x.pt` 均为官方真实文件名，见 https://docs.ultralytics.com/models/yolo26 （Detection 行）；权重可从 https://github.com/ultralytics/assets/releases/tag/v8.4.0 下载。核实记录见 `.team/notes/research/yolo26-official-sources.md`。
   > **`imgsz` 必须与 `.env` 的 `YOLO_IMG_SIZE` 完全一致（当前 `1024`）**：训练尺寸 → `export(..., imgsz=..., dynamic=False)` → `.env` → 推理端四者必须同值，否则 ONNX 图内固化的输入尺寸与推理传入尺寸不符，首次推理即张量形状报错。Track B/C 若改用 `1280` 训练，必须同步把 `.env` 的 `YOLO_IMG_SIZE` 改为 `1280` 并重新导出。
   > **32G–48G 显存调参建议**
   >
   > 🟢 **有官方出处的一条**：
   > - 建议先用 `batch=-1` 让 AutoBatch 自动探测（官方定量：单卡目标约 **60% 显存占用**，也可写小数如 `batch=0.70`），再取探测值向下取 2 的幂写死，保证可复现。
   >   出处：https://docs.ultralytics.com/guides/model-training-tips ；`ultralytics==8.4.216` 的 `cfg/default.yaml` 逐字 `batch: 16 # (int | float) batch size as int (e.g. 16), -1 for AutoBatch, or float in (0.0, 1.0) for GPU memory fraction`
   >
   > 🟡 **以下为本项目工程初值，未标定，官方未给出按显存分档的具体数字**：
   > - `imgsz=1024` 下 32G 暂定 `batch=16~32`、48G 暂定 `batch=32~64`；显存紧张时逐步减半 `batch`。
   > - `epochs=120` 亦为工程初值，非官方推荐轮数。
   > - 小目标（如远景灯头）需要更高分辨率时优先升级到 `yolo26m.pt` / `yolo26l.pt`，而不是无限提升 `batch`。
   > - 若样本量 `< 1,000` 张，可配合小数据集配方：`optimizer=AdamW lr0=0.001 patience=20 mosaic=0.5 mixup=0.0`。
   >
   > ⚠️ **两条易错点（无出处说法已删除）**：
   > - 官方对 OOM 的原话只是「**reduce the batch size incrementally until the model trains smoothly**」（渐进减小 `batch`），**未**承诺「单卡 OOM 会自动减半重试」——该说法无出处，已从本文档删除。OOM 会直接中断训练，请据实减小 `batch` 后重跑。
   > - 官方 `imgsz` 默认是 **`640`**；本项目用 `1024` 是为远景小目标自选的**工程初值**，**不是官方推荐值**。
   >
   > **写作纪律**：本段落曾把「项目工程初值」与「官方结论」混在一句「依据官方指南」之下，导致读者连带误信。**每条断言各自标出处，不共享段落级出处。**

### 6.5 训练产物回传与完整性校验规程

训练结束后，严禁仅凭口头汇报或手工改名回传模型，必须执行哈希校验与指标留证：

1. **远端生成校验文件与确认产物清单**：
   ```bash
   ssh tomori-gpu "cd /workspace/tomori-vision/runs/streetlight_v1 && sha256sum weights/best.pt weights/last.pt results.csv args.yaml > SHA256SUMS && cat SHA256SUMS"
   ```
2. **回传权重与评估指标至本地（权重放本地 ignored 目录 `./vision/weights/`）**：
   ```bash
   mkdir -p ./vision/weights ./vision/reports/streetlight_v1
   rsync -avz --progress tomori-gpu:/workspace/tomori-vision/runs/streetlight_v1/weights/best.pt ./vision/weights/best.pt
   rsync -avz --progress tomori-gpu:/workspace/tomori-vision/runs/streetlight_v1/SHA256SUMS ./vision/reports/streetlight_v1/
   rsync -avz --progress tomori-gpu:/workspace/tomori-vision/runs/streetlight_v1/results.csv ./vision/reports/streetlight_v1/
   rsync -avz --progress tomori-gpu:/workspace/tomori-vision/runs/streetlight_v1/args.yaml ./vision/reports/streetlight_v1/
   ```
3. **本地哈希比对与推理冒烟**：
   ```bash
   sha256sum ./vision/weights/best.pt
   # 与 ./vision/reports/streetlight_v1/SHA256SUMS 中 weights/best.pt 的哈希逐字符比对
   ```
   确认本地 `.env` 中 `YOLO_WEIGHTS_PATH=./vision/weights/best.pt` 指向该文件，并在本地执行推理验证（CPU 或 GPU）确认类别映射与阈值符合后端契约后再交付联调。

### 6.6 权重启用门禁：`imgsz` 一致性与推理模式检查（任一失败即拒绝启动）

回传权重**不得直接投入使用**。启用前必须逐项核对，任一项不通过即拒绝加载该权重，禁止以「先跑起来再说」降级处理：

1. **`imgsz` 一致性门禁（硬性）**：核对 `metrics.json` 的 `imgsz` 字段与 `.env` 的 `YOLO_IMG_SIZE` **完全相等**（当前均应为 `1024`）。
   ```bash
   # 回传的 metrics.json 中记录的训练/导出尺寸
   rg -n '"imgsz"' ./vision/reports/streetlight_v1/metrics.json
   # 拟用于推理的环境变量取值
   rg -n '^YOLO_IMG_SIZE=' .env
   ```
   `export(..., dynamic=False)` 已将输入尺寸固化进 ONNX 计算图；两者不一致会在**首次推理**时抛出张量形状不匹配错误。**不一致则拒绝启动**，必须先按正确尺寸重新导出（或修正 `.env` 后重启），不得放行。
2. **`strict_custom` 模式上线前检查项**：若线上要求「必须加载自定义权重、绝不允许基线兜底」，将 `.env` 置为 `YOLO_INFERENCE_MODE=strict_custom`，并在上线前确认以下全部成立：
   - [ ] `.env` 中 `YOLO_INFERENCE_MODE=strict_custom`（生产环境不得为 `auto`）。
   - [ ] `YOLO_WEIGHTS_PATH` 指向的权重文件**确实存在**且 `sha256sum -c SHA256SUMS` 通过。
   - [ ] 权重启用门禁其余项全部通过：ONNX 输入 `(1, 3, YOLO_IMG_SIZE, YOLO_IMG_SIZE)`、输出 `(1, 300, 6)`；`metrics.json` 类别列表顺序与代码枚举一致；已知真值样例图冒烟推理返回 ≥1 个候选。
   - [ ] **反向验证（必须实测）**：临时把 `YOLO_WEIGHTS_PATH` 指向一个不存在的路径并重启，确认健康检查/启动日志**显式报「自定义权重未就绪」**而不是静默改用 `YOLO_FALLBACK_MODEL` 继续对外服务；验证后恢复正确路径。
   - [ ] 健康检查/启动日志在失败时打印**具体失败环节**（`sha256` 不匹配 / 输出形状不符 / 类别列表不符 / 冒烟推理空结果 / 权重缺失），而非笼统的 "failed to load model"。
3. **`auto` 模式的诚实标注要求**：`YOLO_INFERENCE_MODE=auto` 且自定义权重缺失时允许回退到 `YOLO_FALLBACK_MODEL`，但回退期间响应必须如实标注 `ai_is_baseline_mode=true` 且 `inference_mode=baseline_pretrained_opencv`，融合置信度受后端 `CHECK (ai_is_baseline_mode = FALSE OR ai_max_confidence <= 0.65)` 约束；严禁以基线权重冒充定制模型对外服务。
