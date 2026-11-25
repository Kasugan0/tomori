# 文档导航（`docs/`）

> **公共照明故障工单系统（`#141`）· 阶段二完结版本**（`origin/main` `39` 条提交，`43` 条决策收口）。

---

## 阅读顺序

| 序 | 文档 | 适合谁 | 内容 |
|---:|---|---|---|
| 1 | [`01-案例说明.md`](01-案例说明.md) | 全员 | 背景、目标、业务范围与术语——**先读**，理解系统做什么、不做什么 |
| 2 | [`02-需求规格说明.md`](02-需求规格说明.md) | 产品/测试/审计 | `3` 角色 / `FR-01..29` / `UC-01..09` / `N1-N5` 红线 / 非功能需求 |
| 3 | [`03-概要设计.md`](03-概要设计.md) | 架构/新成员 | 系统架构、`src/{backend,frontend,database}` 布局、模块划分与依赖 |
| 4 | [`04-详细设计.md`](04-详细设计.md) | 后端/视觉实现者 | `4` 状态机 `29` 态·`36` 规则条目·`47` 图边、`transition_*` 事务、`34` 错误码 |
| 5 | [`05-数据设计.md`](05-数据设计.md) | 后端/DBA | `17` 表 / `76` CHECK / `41` FK / `37` 索引 / `6` 部分唯一 / `alembic` / `seed_demo_data` |
| 6 | [`06-接口设计.md`](06-接口设计.md) | 前后端联调 | `60` 业务端点 + `health`、幂等、`error.code` `34`、视觉契约 `D-30` |
| 7 | [`07-测试说明.md`](07-测试说明.md) | 测试/CI | `118` 单测/集成 + `31` E2E + `128` testid + `19` 变异杀灭 + `69%` 覆盖率 |
| 8 | [`08-部署与使用说明.md`](08-部署与使用说明.md) | 运维/部署 | 环境准备、`.env`/`application.yml`、启动命令、远端 GPU 训练链路 |
| 9 | [`09-需求追踪表.md`](09-需求追踪表.md) | 审计/评审 | `requirement-traceability.md` `15` 格矩阵 + `FR`→端点→测试映射 |

## 配套资料

- [`diagrams/`](diagrams/)：PlantUML 源文件（`use-case`/`architecture`/`domain-model`/`report-sequence`/`order-state`/`data-model`）。
- [`images/`](images/)：渲染图（`use-case.svg`/`architecture.svg`）与 [`screenshots/`](images/screenshots/) `E2E` 真实截图（`workorder-full-flow`/`ai-candidates-d30`/`admin-health-notifications`/`vision-disabled-fallback`）。
- `docs/local/`：**旧版权威文档快照**（`DECISION`/`REQUIREMENTS`/`DESIGN`/`API`/`RUNBOOK`/`TRAIN`）——属**本地过程产物**（`gitignored` 不入库），仅作追溯参考。

## 文档与代码一致性原则（`F14`）

本文档全部条目以**代码/测试/`metrics.json`/`prep_manifest.json`/`audits/` 实据**为准——不搬旧文措辞、不虚构未实测数字。如发现文档与 `src/`、`tests/`、`metrics.json` 不一致，以**当前代码与落盘实测**为真相并回填文档。
