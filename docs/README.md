# `docs/` 文档导航（`#141` 公共照明故障工单系统）

> **阶段二完结**（`origin/main = 50df47e`，`39` 条严格线性全 `G` 签名提交，`43` 条决策收口 `23 ✅ / 20 🟢`）：
> `src/backend/tomori/`（`60` 业务端点 + `health`）+ `src/frontend/`（Vue 3 SPA）+ `src/database/`（`schema.sql`/`seed.sql`）+ `streetlight_v3`/`nc=3` 训练完结验收；`118` 单测/集成 + `31` E2E 全绿。

---

## 阅读顺序与角色指引

| 序 | 文档 | 适合 | 内容（全部以 `src/`/`tests/`/`metrics.json`/`prep_manifest.json`/`audits/` 实据为准） |
|---:|---|---|---|
| 1 | [`01-案例说明.md`](01-案例说明.md) | 全员 | `#141` 业务背景、`3` 角色/`17` 表/`60` 端点/`YOLO` `nc=3`/`D-30` 参照框、`fault_type`/`severity`/`scope`/`idempotency`/`state_machine`/`whitelist`/`mapping` 术语表 |
| 2 | [`02-需求规格说明.md`](02-需求规格说明.md) | 产品/测试/审计 | `3` 角色（`ADMIN`/`INSPECTOR`/`TECHNICIAN`/`scope_authorizations`/`deps.py` `FORBIDDEN_*`）/`FR-01..29`/`UC-01..09`/`N1-N5`/`business_rules` `5` 默认/`grading`/`sla`/`idempotency`/`audit_logs`/非功能需求——`FR` 每条带端点+测试映射 |
| 3 | [`03-概要设计.md`](03-概要设计.md) | 架构/新成员 | `FastAPI`+`Vue3`+`PG17` 架构、`src/{backend,frontend,database}` 三层布局、`api`/`services`/`models`/`workers`/`vision`/`web`/`tests` 模块划分与依赖红线 |
| 4 | [`04-详细设计.md`](04-详细设计.md) | 后端/视觉实现者 | `4` 状态机 `29` 态/`36` 规则条目·`47` `(from,to)` 图边/`11` 终态/`0` 死路 + `transition_*`/`upload_ticket_evidences_flow`/`_TERMINAL_TICKET_STATUSES`/`_normalize_raw_class`/`_train_kwargs`/`polish_kwargs`/`_out_names`/`_clean_samples`/`validate_data_yaml`/`_assign_group_splits`/`34` 码/`MANUAL_FALLBACK`/`resubmit_required`/`DEAD_LETTER`/`notifier`/`sla_scanner`——每类/方法带 `src/backend/...:行号` |
| 5 | [`05-数据设计.md`](05-数据设计.md) | 后端/DBA | `17` 表/`76` CHECK/`41` FK/`37` 索引/`6` 部分唯一/`alembic` `0001..0004`/`seed_demo_data`/`business_rules` `5` 默认/`schema.sql`/`seed.sql`——每表带列名/约束/`FK` |
| 6 | [`06-接口设计.md`](06-接口设计.md) | 前后端联调 | `61` 端点清单 + `health`/`ai-candidates`/`whitelist`/`mapping`/`contract`/`VisionClient`/`client.py`/`process_due_notifications_once`/`sla_scan`/`error.code` `34`/`HTTP_STATUS_BY_CODE`/`details`/`fallback`/`MANUAL_FALLBACK`/`requires_inspector_confirmation`/`replaces_electrical_safety_check`/`ai_is_baseline_mode`/`vision_ready`——每端点带方法/路径/角色/请求/响应/`error.code` |
| 7 | [`07-测试说明.md`](07-测试说明.md) | 测试/CI | `118` 单测/集成 + `31` E2E + `128` testid + `19` 变异杀灭 + `nc=3`/`occluded` 断言 + `test_train_config_*` + `27` 契约 + `69%` 覆盖率 + `PASS`/`SKIP`/`0` mock + `coverage` 未到 `90%`/`occluded` `AP≈0`/`Firefox`/`WebKit` `P3-3`/`mosaic_polish` 未实跑——每测试类带 `pytest --collect` 实据 |
| 8 | [`08-部署与使用说明.md`](08-部署与使用说明.md) | 运维/部署 | `PG`/`uv`/`pnpm`/`uv sync`/`alembic`/`compose`/`env.sh`/`remote GPU`/`SSH`/`.env`/`application.example.yml`/`uvicorn`/`vite`/`worker`/`trainer`/`dataset_prep`/`rsync`/`sha256sum`/`admin`/`inspector`/`technician`/`health`/`ai-candidates`/`tickets`/`dispatches`——每命令带真实可执行输出 |
| 9 | [`09-需求追踪表.md`](09-需求追踪表.md) | 审计/评审 | `requirement-traceability.md` `15` 格矩阵（`15/15` 过）+ `FR`→端点→测试 `29` 条映射 |

## 配套资料

- [`diagrams/`](diagrams/)：`use-case.puml`/`architecture.puml`/`domain-model.puml`/`report-sequence.puml`/`order-state.puml`/`data-model.puml`（PlantUML 源文件，`6` 份）。
- [`images/`](images/)：`use-case.svg`/`architecture.svg`/`screenshots/`（`4` 张 `E2E` 真实截图：`workorder-full-flow`/`ai-candidates-d30`/`admin-health-notifications`/`vision-disabled-fallback`，来自 `tester` `e2e-artifacts`）。
- `docs/local/`：**旧版权威文档快照**（`DECISION`/`REQUIREMENTS`/`DESIGN`/`API`/`RUNBOOK`/`TRAIN`）——属**本地过程产物**（`gitignored` 不入库），仅作追溯参考。

## 事实一致性原则（`F14`）

本文档每个数字/声明均带**可复现实据**：`src/` 代码坐标（`文件:行号`）、`vision/reports/{streetlight_v1,streetlight_v3}/metrics.json`（`20` 键实测）、`merged_v6/prep_manifest.json`（`clean_stats`/`splits`/`leakage_checks`/`split_objective`/`split_assignment`）、`audits/`（`stage2-final-audit.md`/`requirement-traceability.md`）、`.team/notes/{backend-dev,vision-dev,dev-ops,tester,auditor,git-keeper}/` 实测日志。发现与代码不一致时，以**当前代码与落盘实测**为真相并回填文档。
