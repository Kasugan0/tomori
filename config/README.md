# `config/` 目录

> **占位配置目录**——为 `Settings` 字段与部署文件提供 `__PLACEHOLDER__` 模板；真实值写本地 `.env`（`gitignored` 不入库）。`application.yml` 已加入 `.gitignore`，但应用不读取它（见下）。

## 文件

| 文件 | 用途 | 备注 |
|---|---|---|
| `application.example.yml` | `Settings` 字段的 `__PLACEHOLDER__` 模板（`app.env`/`database.*`/`server.*`/`auth.*`/`upload.*`/`vision.*`/`gpu_ssh.*`） | 每个字段带 `.env`/`settings.py` 映射注释；仅供字段审计/部署参考，**应用不加载 YAML** |

## 与 `.env` 的关系

- `.env.example`（仓库根）：`.env` 文件模板的**权威版本**（含 `DATABASE_URL`/`JWT_*`/`BACKEND_PORT=18141`/`FRONTEND_PORT=15173`/`E2E_*`/`YOLO_*`/`GPU_SSH_*` 全量字段 + 注释）；
- `config/application.example.yml`：同一组字段的 **YAML 形态**（供 `settings.py` 字段审计与部署参考）；
- 生效配置：**应用只加载 `.env` 与环境变量**（`settings.py` `env_file=".env"`），无 `application.yml` 加载逻辑；**真实值严禁提交**（`.env` 与 `application.yml` 均已 `gitignored`）。
