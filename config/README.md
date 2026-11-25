# `config/` 目录

> **占位配置目录**——为 `Settings` 字段与部署文件提供 `__PLACEHOLDER__` 模板；真实值仅写本地 `.env` / `application.yml`（均 `gitignored` 不入库）。

## 文件

| 文件 | 用途 | 备注 |
|---|---|---|
| `application.example.yml` | `Settings` 字段 → `application.yml` 的 `__PLACEHOLDER__` 模板（`app.env`/`database.*`/`server.*`/`auth.*`/`upload.*`/`vision.*`/`gpu_ssh.*`） | 每个字段带 `.env`/`settings.py` 映射注释；拷贝为 `application.yml` 后逐项填入真实值 |

## 与 `.env` 的关系

- `.env.example`（仓库根）：`.env` 文件模板的**权威版本**（含 `DATABASE_URL`/`JWT_*`/`BACKEND_PORT=18141`/`FRONTEND_PORT=15173`/`E2E_*`/`YOLO_*`/`GPU_SSH_*` 全量字段 + 注释）；
- `config/application.example.yml`：同一组字段的 **YAML 形态**（供 `settings.py` 字段审计、`application.yml` 生成或 CI 注入参考）；
- 优先级：`.env`（开发/容器首选）→ `application.yml`（YAML 部署备选）→ 环境变量覆盖；**真实值严禁提交**（`.env`/真实 `application.yml` 已 `gitignored`）。
