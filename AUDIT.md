# v0.1.0 独立审计与修复摘要

原审计基线为提交 `9a093ca17cd15385223c81604b416308e3c4c57d`，当时项目声明版本为 `0.1.0`；未证实该提交对应已发布的 v0.1.0 标签。五个修复提交的末项为 `9264ffbefd87e9a1d0f23ef69e14b45acfe256fb`；本摘要和后续部署文档修改尚未包含在该提交中，若提交将形成后续版本。测试主要针对提交前持续变化的工作树运行，不能把结果直接当作该提交的完整重跑证据。

原审计覆盖后端业务状态和权限、视觉训练与推理、前端流程、接口契约及部署。发现包括固定演示管理员、逾期重派、返修状态脱节、孤儿证据绑定、并发负载上限、模型产物清单、候选确认、部署命令和配置说明等问题；另有需要业务裁决或额外运行验证的风险。

## 修复提交

| 提交 | 范围 |
|---|---|
| `c19c8afbd7fe762c14cafcfd575fff196eeb0a4a` | 固定迁移 0003 的表集合基线 |
| `457b371a7d1c5d69231b217ceeded89c5c365387` | 候选处置、派工、返修、SLA、授权与上传 |
| `05d6b0489fd98f024ad8b6fcc4be8e4dc5ace7e2` | 前端路由、列表、会话与写入后刷新 |
| `5db69a25076abc12b3be9c721db3da12ea2632a7` | 数据集复用、权重清单及推理并发 |
| `9264ffbefd87e9a1d0f23ef69e14b45acfe256fb` | 部署脚本、环境配置、文档及忽略规则 |

## 测试证据与时点

以下测试日志位于本地 `.team/notes/tester/logs/final-gate/`，该目录被忽略，**不随仓库交付**。结果来自审计期间的工作树；日志只保存测试程序输出，不包含执行命令或退出码。命令和退出状态来自本次会话中的 bash 工具调用及 job_output 记录，没有随日志保存的独立退出码元数据；会话记录也不随仓库交付，克隆仓库无法直接重建这些历史证据。

| 命令 | 结果及边界 |
|---|---|
| `uv run --no-sync pytest tests/unit -q` | 最终 JSON schema 收紧后：`62 passed in 5.04s`。 |
| `uv run --no-sync pytest tests/integration -q` | 最终 JSON schema 收紧后：`117 passed, 1 skipped in 121.47s`；跳过项不计作通过。 |
| 下方完整 E2E 命令 | `36 passed, 2 warnings in 102.50s`；运行早于最终 JSON schema 收紧，收紧后未重跑全量 E2E。 |
| `uv run --no-sync mypy src tests` | schema 收紧及测试类型修复后：`Success: no issues found in 115 source files`。 |

E2E 命令在项目根目录执行，重定向路径仅用于本地证据归档：

```bash
uv run --no-sync python -c 'from pathlib import Path; import tests.e2e.conftest as c; import pytest; c.LOG_DIR=Path(".team/notes/tester/logs/final-e2e3/e2e-run").resolve(); c.ARTIFACT_DIR=Path(".team/notes/tester/logs/final-e2e3/e2e-artifacts").resolve(); raise SystemExit(pytest.main(["tests/e2e", "-q"]))'
```

最终 schema 收紧及类型、格式修复后的聚焦运行，依次为：

```bash
uv run --no-sync pytest tests/unit/test_body_limit_regression.py tests/unit/test_typed_body_limit_regression.py tests/unit/test_vision_concurrency_regression.py -q
uv run --no-sync pytest tests/integration/test_candidate_decisions_persistence.py tests/integration/test_candidate_persistence_regression.py tests/integration/test_facility_asset_regression.py tests/integration/test_reference_candidate_regression.py tests/integration/test_manual_reference_decisions.py tests/integration/test_migration_table_guard_regression.py -q
uv run --no-sync python -c 'from pathlib import Path; import tests.e2e.conftest as c; import pytest; c.LOG_DIR=Path(".team/notes/tester/logs/typing-e2e/run").resolve(); c.ARTIFACT_DIR=Path(".team/notes/tester/logs/typing-e2e/artifacts").resolve(); raise SystemExit(pytest.main(["tests/e2e/test_role_isolation.py", "tests/e2e/test_reference_manual_regression.py", "-q"]))'
```

依次为 `19 passed in 1.71s`、`26 passed, 1 skipped in 29.80s`、`7 passed, 2 warnings in 22.25s`。聚焦浏览器测试不等于最终版本全量 E2E；执行时未保存工作树完整快照，不能确定与最终提交逐字节一致。

独立复审的 PostgreSQL 聚焦回归命令：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -p no:cacheprovider -q tests/integration/test_regression_dispatch.py tests/integration/test_regression_evidence.py tests/integration/test_facility_asset_regression.py tests/integration/test_candidate_persistence_regression.py tests/integration/test_notification_lifecycle.py
```

审计记录结果为 `27 passed in 25.94s`，覆盖逾期重派、返修、并发负载、证据所有权、生产种子、资产分级、候选与通知中的测试路径；不代表所有并发交错或授权组合都已验证。

独立单元聚焦命令：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -p no:cacheprovider -q tests/unit/test_body_limit_regression.py tests/unit/test_typed_body_limit_regression.py tests/unit/test_vision_concurrency_regression.py tests/unit/test_trainer_export_manifest_regression.py
```

审计记录结果为 `22 passed in 1.95s`；视觉并发和导出清单回归使用模拟，不能替代真实模型训练或压力测试。

## 尚未验证的范围

- 历史 `streetlight_v5` 的 `metrics.json` 中 `best.pt` 哈希为 `a557f4a7…`，而 `SHA256SUMS` 中为 `a067b0b0…`。原精修目录缺失，无法追溯权重身份或确定差异原因；需重新训练或重建并核对权重、指标和导出模型。
- 未完成真实模型多进程并发和压力测试、完整重训与数据集重建，也未验证所有故障及权限组合。
- 最终 schema 收紧后的完整浏览器 E2E 未重跑；局部浏览器通过不能替代完整闭环。
- 远端 GPU 环境、实际部署同步与备份恢复、生产配置、真实外部通知网关及其中断重试未验证；Python 依赖未完成漏洞库扫描。
- 独立复审报告包含中途状态与后续修复，不能把其中阶段性结论直接当成最终版本的运行结果。`audits/` 与 `.team/` 是被 Git 忽略的本地材料，不随仓库交付；`docs/team/` 亦为本地协作材料。

此摘要记录已核对证据和已知边界，不表示系统不存在其他缺陷，也不构成部署或提交批准。
