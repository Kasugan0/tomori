# 测试运行指南（tests/README.md）

> Tomori（#141 公共照明故障工单系统）测试套件执行方法。配置：`pyproject.toml [tool.pytest.ini_options]`（`testpaths=["tests"]`、`pythonpath=["src/backend"]`、`asyncio_mode="auto"`）。

## 0. 前置条件

| 层级 | 依赖 |
|---|---|
| `tests/unit` | 仅本地 `.venv`（`uv sync --extra vision` 提供 `onnxruntime`/`ultralytics`/`Pillow`/`cv2`） |
| `tests/integration` | `tomori-postgres` `25432` 容器 + `TEST_DATABASE_URL` 指向 `tomori_test` |
| `tests/e2e` | `./deploy/stack-guard.sh claim-e2e` 独占窗口（`18142`/`15174`）+ `pnpm` 前端 + Playwright |

## 1. 各层执行命令

```bash
# 单元测试（无 DB）
uv run pytest tests/unit -q

# 集成测试（真 PG17，conftest 自动建/清表）
uv run pytest tests/integration -q

# E2E（必须先占 stack-guard 锁窗口）
./deploy/stack-guard.sh claim-e2e --owner tester
uv run pytest tests/e2e -q
./deploy/stack-guard.sh release-e2e --owner tester

# 全量（E2E 仅在窗口内才会 collect）
uv run pytest tests -q

# 覆盖率
uv run pytest tests/unit tests/integration --cov=src/backend/tomori --cov-report=term-missing

# 单用例 / 关键字 / 标记过滤
uv run pytest tests/integration/test_api_auth.py -q
uv run pytest tests/integration -k "idempotent" -q
uv run pytest -m real_commit -q          # 真事务+TRUNCATE 清表的用例
uv run pytest -m vision_weights -q       # 需真 YOLO26s 权重 + onnxruntime 校验
uv run pytest -m e2e -q                  # 等价 tests/e2e（需 claim-e2e 锁）

# 收集/用例计数核验（假绿反查：collect N == junit N == passed N == artifacts N）
uv run pytest tests/e2e --collect-only -q
uv run pytest tests -q --junitxml=/tmp/junit.xml
```

## 2. `stack-guard` 命令全表（`deploy/stack-guard.sh`）

```bash
./deploy/stack-guard.sh up            # docker compose up -d postgres（健康等待）
./deploy/stack-guard.sh status        # 栈状态 + 端口/锁占用
./deploy/stack-guard.sh stop          # docker compose stop（保留数据卷）
./deploy/stack-guard.sh stop --force  # down（仍保留数据卷，勿加 -v）
./deploy/stack-guard.sh claim-e2e --owner <name>
                                      # 校验 postgres 健康 + tomori_test 可连 + 18142/15174 空闲，原子建 .team/runtime/e2e.lock
./deploy/stack-guard.sh release-e2e --owner <name>
                                      # 释放锁前校验 18142/15174 已停监听
```

## 3. 共享测试输入（`tests/fixtures/`）

- `tests/fixtures/README.md`：静态二进制样图与负向媒体输入清单（`night_normal_lit`/`occluded_fault`/`minimal_40mp`/`corrupt_truncated.jpg`/`sample.svg`/`sample.mp4`/`d30_samples/`）；
- 程序化生成器仍归 `tests/integration/conftest.py`（`make_static_image`/`make_night_normal_lit_png`/`make_occluded_fault_png`/`make_animated_bytes`/`make_minimal_png_with_dimensions`）——`tests/e2e/conftest.py` 直接 `from tests.integration.conftest import ...` 复用；
- `demo` 口令哈希口径与 `src/database/seed.sql` 一致（`demo-2026-tomori`）。

## 4. 已知口径

- `pytest-asyncio`：`asyncio_mode="auto"`、`asyncio_default_fixture_loop_scope="fixture-scope"`、`asyncio_default_test_loop_scope="function"`；
- `testpaths` 仅 `tests/`；`pythonpath=["src/backend"]`（`tomori` 包即 `src/backend/tomori`）；
- 集成测试隔离：`tests/conftest.py` `validate_test_database_url` 强制 `TEST_DATABASE_URL` 非空且指向 `tomori_test`（`!~= tomori_dev` 校验）；
- E2E 默认跳过：未占 `claim-e2e` 锁时 `tests/e2e/conftest.py` `pytest_collection_modifyitems` 将全部用例 `skip`（`E2E 需显式指定 pytest tests/e2e 或 TOMORI_E2E=1`）。
