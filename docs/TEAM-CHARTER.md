> 状态：阶段一定稿候选 —— 待用户审阅裁决（未定稿）

# 团队章程（TEAM-CHARTER）

> **文档归属**：`docs-writer`｜**本文件写入域**：`docs/TEAM-CHARTER.md`
> **读者**：全队 9 人 + `auditor`
> **本文档的地位**：本项目的**团队阵容、写入域边界、汇报纪律、反假绿纪律的唯一权威**。
> **上游依据**：`docs/team/TEAM.md`（方法论母本，749 行）、`.team/notes/git-keeper/COMMIT-PROTOCOL.md`（318 行）、各成员阶段一素材。

---

## 0. `docs/team/` 的适用范围声明（**先读这一节，再读 `TEAM.md`**）

`docs/team/TEAM.md` 与 `docs/team/LEAD.md` 是**另一个项目（`new-new-api`）**的方法论记录。本项目**参考**它们的方法论，**不照搬**它们的项目事实。

| 类别 | `docs/team/` 中的值 | 本项目实际值 | 处置 |
|---|---|---|---|
| **端口** | `18080` / `18081` / `19090` / `19092` | `25432`（PG）/ `18141`（dev Uvicorn）/ `15173`（dev Vite）/ `18142`（E2E Uvicorn）/ `15174`（E2E Vite） | **以本文档 §2 与 `docs/TECH-STACK.md` §5 为准** |
| **团队阵容** | 8 人 = 1 Lead + 7 | **9 人 = 1 Lead + 8**（含 `vision-dev`） | 见 §1 |
| **假绿形态编号** | `F1`..`F13` | **不存在**。本项目是 **A–G 七种形态 + `no-op`**；`F14` 是 §4.5 的**独立纪律条目，不是形态编号** | 见 §4 |
| **业务表数** | `new-new-api` 的表数 | **17 张**（排除 `alembic_version`） | 见 `docs/SCHEMA-DESIGN.md` |
| **代码路径** | `src/*.rs`、`migrations/0001_core.sql` 等 | 本仓库**不存在** Rust 代码 | — |

> 🔴 **不得修改 `docs/team/TEAM.md` / `docs/team/LEAD.md` 一个字节。**
> 它们是**历史参照物**。用户原话：「**这个方法论是另外一个项目的，但是你们团队可以参考。**」——改它等于**篡改参照物**，会把「参考」变成「伪造同源」。**正确做法是声明适用范围，而不是把参照物改成看起来像本项目的。**
>
> ✅ **本项目事实的唯一权威**：本文档 + `docs/TECH-STACK.md` + `docs/RUNBOOK.md` + 其余 `docs/*.md`。
> 引用 `docs/team/` 内容做**方法论**依据时，**必须**同时说明「该方法论适用，但其项目事实不适用」。

---

## 1. 团队真实阵容：9 人 = 1 Lead + 8

| # | 成员 | 负责 | 明确不负责 | 写入域 |
|---:|---|---|---|---|
| — | **Lead** | 裁决、派单、跨域协调、最终判定 | 不直接写业务代码/文档 | 无（只读 + 派单） |
| 1 | `docs-writer` | `docs/` 设计规格（除 `RUNBOOK.md`）、`.team/notes/research/`、`.team/notes/decisions/`、Serena Memories | 不写代码、不写 `RUNBOOK.md`、不做 git 写 | `docs/*.md`（除 RUNBOOK）、`.team/notes/research/`、`.team/notes/decisions/` |
| 2 | `auditor` | 九维度审计、P0 判据、结论分级 | 不修问题（只报）、不写设计文档 | `.team/notes/auditor/` |
| 3 | `backend-dev` | `backend/app/`、`migrations/`、`tests/unit/`、`tests/integration/` | 不写 `docs/`、不动 `web/` | `backend/`、`migrations/`、`tests/{unit,integration}/`、`pyproject.toml` |
| 4 | `vision-dev` | `vision/`、`tests/vision/`、数据集与权重 | 不碰业务 API、不碰前端 | `vision/`、`tests/vision/` |
| 5 | `frontend-dev` | `web/src/`、`web/tests/unit/` | 不写 `docs/`、不动 `backend/` | `web/` |
| 6 | `tester` | `tests/e2e/`、变异测试、证据归档 | 不写业务代码、不改被测实现 | `tests/e2e/`、`.team/notes/tester/` |
| 7 | `dev-ops` | `deploy/`、`compose.yaml`、`.env.example`、`docs/RUNBOOK.md` | 不写业务代码 | `deploy/`、`compose.yaml`、`.env.example`、`docs/RUNBOOK.md` |
| 8 | `git-keeper` | **唯一 git 写权限持有者**、提交协议、静态门禁 | 不写业务代码/文档 | `.git/`、`.gitignore`、`.team/notes/git-keeper/`、`.team/commit-gate/` |

> ⚠️ **`auditor` 21 项必查之一：团队阵容必须写「9 人 = Lead + 8（含 `vision-dev`）」，不得照抄 `TEAM.md` 的「8 人（Lead + 7）」。** 后者是 `new-new-api` 项目的记录。

### 1.1 单应用三角色架构（**不是多入口拆两套 SPA**）

| 项 | 决定 |
|---|---|
| 应用形态 | **单个 Vue 3 应用**，一套构建产物 |
| 角色 | `ADMIN` / `INSPECTOR` / `TECHNICIAN` 三角色**共用同一 SPA** |
| 角色区分方式 | `GET /auth/me` 返回 `role_code` + `scope_authorizations[]` → 前端据此渲染菜单与按钮 |
| 路由守卫 | **全局守卫**按 `to.meta.roles` 判定（**非逐条 `requiresAuth`**） |
| 移动端 | `nav-mobile-*` 响应式，**不是独立应用** |

> **为什么不做两套 SPA**：三角色的页面重叠度高（工单详情三种角色都要看），拆两套会立刻产生重复组件与状态同步问题。**一套应用 + 角色驱动渲染是更少的概念。**

---

## 2. 写入域边界（**越界即事故**）

### 2.1 三条硬边界（`docs/team/TEAM.md` §2.7 定义）

| # | 边界 | 归属 |
|---:|---|---|
| ① | `#[cfg(test)] mod tests` / `tests/*` 里的测试代码 | `tester` |
| ② | 决策文档（`.team/notes/decisions/`） | `docs-writer` |
| ③ | 设计文档（`.team/notes/*/xxx-design.md`） | `docs-writer` |

### 2.2 写入域矩阵（行=成员，列=目录）

|  | `docs/` | `docs/RUNBOOK.md` | `backend/` | `vision/` | `web/` | `tests/` | `migrations/` | `deploy/` | `.git/` | `pyproject.toml` |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| `docs-writer` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `backend-dev` | ❌ | ❌ | ✅ | ❌ | ❌ | ✅ unit/integ | ✅ | ❌ | ❌ | ✅ |
| `vision-dev` | ❌ | ❌ | ❌ | ✅ | ❌ | ✅ vision | ❌ | ❌ | ❌ | ❌ |
| `frontend-dev` | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ web/unit | ❌ | ❌ | ❌ | ❌ |
| `tester` | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ e2e | ❌ | ❌ | ❌ | ❌ |
| `dev-ops` | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ |
| `git-keeper` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |

> **`docs/RUNBOOK.md` 只归 `dev-ops`**，`docs-writer` 对它是**只读**。这是唯一一个 `docs/` 下的例外。

### 2.3 零 git 写纪律

**除 `git-keeper` 外，所有成员零 git 写操作。**

| 允许 | 禁止 |
|---|---|
| `git status` / `git log` / `git diff` / `git show` | `git add` / `git commit` |
| `git config --get <key>` | `git reset` / `git stash` / `git rm` / `git mv` |
| `git ls-files` | `git checkout -- <file>` / `git restore` |
| — | `git worktree` / 设 `GIT_INDEX_FILE` |

**`git-keeper` 的独占范围**：`git add` / `commit -S` / `reset` / `stash` / `rm` / `mv` / `checkout -- <file>` / `restore` / `worktree` / `GIT_INDEX_FILE`；`.git/{config,hooks/,objects/,refs/,packed-refs}`。

### 2.4 阶段一冻结纪律

**未获 Lead 放行（且须经用户审阅裁决后按批次派单），严禁 `git add` / `git commit`。**

阶段一允许的 git 操作仅：
- 只读检查（`status`/`log`/`diff`/`ls-files`）
- `git-keeper` 维护 `.gitignore` / `.team/notes/git-keeper/` / `.team/commit-gate/`

---

## 3. 汇报纪律

### 3.1 四项汇报模板（`TEAM.md` §2.3）

每个完成任务的汇报**必须**包含这四项：

| # | 项 | 要求 |
|---:|---|---|
| ① | **命令原始输出** | 贴真实输出，不是「已通过」 |
| ② | **数字 + 测量命令** | 「17 张表」必须附上产生这个数字的命令 |
| ③ | **边界声明** | 明确说清「已证实 / 未证实 / 未运行·未审计范围」 |
| ④ | **P0 核实标注** | 涉及 P0 的必须标 `【需独立核实（由 Lead 复核）】` |

> **为什么必须贴原始输出**：`TEAM.md` §5.5 记录了 `docs-writer` 自己犯的错——报「坐标 `ok=37/bad=0`」，实际脚本只验了第一级，8 处内容全错。**「报告通过」和「真的通过」之间差一个原始输出。**

### 3.2 证据强度排序（`TEAM.md` §3.3）

```
真实运行日志（running N tests + 测试名 + 真实输出）
   > 变异→红→恢复（sha 逐字节）
   > HTTP 响应码 / DB 行 / 消息（真故障注入）
   > 静态列举（--list / rg / sed）
   > 代码结构推断
   > 他人转述
```

> ⚠️ **「过度自纠也是错」**：不要用弱观测推翻强证据。曾有用 `--list`（陈旧编译产物）否定 `running 1 test` 实跑证据的案例——**那本身制造了一次假绿**。

### 3.3 统计口径必须分层（`TEAM.md` §4.6）

**统计任何东西前，先定义「分子 / 分母 / 排除项」**，并把排除项列出来。

| 反例（`TEAM.md` §4.6 原文） | 数量 |
|---|---:|
| `sqlx::fetch_optional()`（标准库 API） | 43 |
| 注释 | 7 |
| 测试函数名 | 3 |
| **我们的标识符** | **2** |

**「53 处里只有 2 处是我们的」——「所有出现」≠「我们的标识符」。**

> **本项目实例**：`fault_evidences` 的 CHECK 数**必须写「8」并附「分子=DDL 内显式声明 / 排除项含 `NOT NULL`」**，不能只写数字。`17 张表` 必须附「排除 `alembic_version`」。

---

## 4. 反假绿纪律（**本项目质量体系的核心**）

### 4.1 现象的唯一定义

**现象只有一个：变异后仍全绿。**

### 4.2 八项形态（A–G + `no-op`）

```
假绿（现象：变异后仍全绿）
  ├── A 名字骗人              → cache_pricing
  ├── B 模块盲区              → settlement_idempotency
  ├── C 命名错觉              → （文件级）
  ├── D 重合掩盖
  │     ├── v1 同输入          → Usage::validate
  │     ├── v2 顺序            → verify_webhook
  │     └── v3 守卫密度        → fixture_rejects
  ├── E 同义反复              → a_cache_priced_item
  ├── F 测试不执行被测代码     → ⚠️ 理论形态，暂无实例
  └── G 断言覆盖不足          → include_usage（2026-11-15）

no-op（不是假绿，修法相反）
  └── 改动本身无效果           → ea91786 的 include_usage 守卫
```

**共 8 项 = A–G 七形态 + `no-op`。**

> 🔴 **纪律：本项目不存在 `F1..F14` 形态编号。**
> 唯一的 `F` 编号是 **`F14` = 「技术断言必须带出处」**（`TEAM.md` §4.5），它是**一条独立纪律**，**不是**形态编号。把 `F14` 当成「第 14 种假绿形态」是错误用法。
> **新发现的形态应先问「它是现象还是原因」**：若是原因，挂到 A–G 下；若是新现象，另立一节。

### 4.3 `F14`：技术断言必须带出处（`TEAM.md` §4.5）

**技术断言**（「库 X 的行为是 Y」「API 返回 Z」「工具不支持 W」）如果没有**代码坐标 / 官方文档 URL / 可复现命令**，**不得作为决策依据**。

**母本实例**：`backend-dev` 断言「sqlx 只校验 version 存在，不校验 checksum」**无出处**，Lead 据此批准了 30 行重复实现；实现后反向验证，报的其实是 sqlx **原生**文案——**报错形态反证了检查没生效**。

**推论**：**「不接受转述」不只意味着「不信别人的结论」，还包括「断言没给出处时，不要基于它行动」。**

> **本项目实例（必须记录）**：`frontend-dev` 素材 §10.3 R7 断言「`el-input` 的 `data-testid` 落到根 `<div>`」，其技术依据 `useAttrs({ excludeKeys: containerAttrs })` **在 Element Plus 2.14.8 源码中不存在**。该无出处断言若被采信，会导致全面误改原生控件。**这正是 F14 要防的形态。**

### 4.4 假绿 vs `no-op`（**修法相反，误判两个方向都有代价**）

| 类别 | 定义 | 修法 |
|---|---|---|
| **假绿** | 测试存在、能跑，但抓不住 bug | **补测试** |
| **`no-op`** | 没有 bug 可抓（改动本身无效果） | **删改动** |

**判据：变异全绿时，第一假设应该是「改动无效果」，不是「测试缺口」。**

> 把 `no-op` 误判为假绿 → 补一堆测试去覆盖一个不存在的 bug；把假绿误判为 `no-op` → 删掉真正需要的改动。

### 4.5 五层「假象」与判据（`TEAM.md` §3.1 表）

| 层 | 假象 | 实测案例 |
|---:|---|---|
| 1 | `0 failed` ≠ 通过 | 测试没跑也 exit 0 |
| 2 | `passed` ≠ 覆盖（测的不是修复点） | 8/0 全是 encode 侧，删掉 decode 修复照样全绿 |
| 3 | `passed` ≠ 跑了（没编译） | 5 条测试没 `mod` 声明 → `0 passed; 115 filtered out` |
| 4 | 断言本身无法失败 | `expect([200, 409]).toContain(...)` |
| 5 | 测试绕过被测函数 | 测了辅助函数，没测实际调用点 |

**判据 17（重合掩盖）**：「覆盖输入空间」和「覆盖守卫的作用」是两件事——测「守卫 A 拦某类输入」时，必须用「**只被 A 拦、不被 B 拦**」的输入。

### 4.6 本项目专属的变异卡（24 张）

`tester` 已定稿 24 张变异卡：`MUT-01a/01b/02/03a/03b/04a/04b/04c/05/06a/06b/07a/07b/07c/08a/08b/08c/09/10/11/12/13/14/15`。

| 变异卡 | 抓的形态 | 说明 |
|---|---|---|
| `MUT-01b..e` | A 名字骗人 | 合并为一行（4 个 reason）→ 可执行实验 **24 行 + 3 额外 reason = 27** |
| `MUT-03b` / `MUT-04c` | — | **改 DB 约束**，SHA256 恢复不适用 → 用 `\d+` DDL before/after diff 为空 |
| `MUT-05` | — | `return computed_priority` → `return "P3"`，抓**硬编码等级** |
| `MUT-06a` | — | `create_rework(...)` → `None`，抓**单事务四件事**漏项 |
| `MUT-09` | — | 允许 `IN_REPAIR → REWORK_PENDING_DISPATCH`，抓**损坏 `rework_count` 语义** |
| `MUT-13` | — | NUMERIC precision，抓**浮点比较** |
| `MUT-14` | — | TIMESTAMPTZ，抓**时区丢失** |
| `MUT-15` | — | 删 `AND is_sla_overdue = false` 幂等守卫，抓**重复扫描** |

**每张变异卡必须产出 6 件证据**：
1. baseline `exit=0`
2. `SHA_BEFORE`
3. 变异后红 `exit≠0`
4. **失败的测试名**
5. `RESTORED OK sha256`
6. 恢复后 `exit=0`

> **备份目录必须是 `.team/notes/tester/mut-backup/`，`/tmp` 禁止**——`/tmp` 会被清理，导致「恢复后全绿」的假象无法复现。

---

## 5. 提交协议（`git-keeper` 独占，据 `COMMIT-PROTOCOL.md`）

### 5.1 开批前四道门

| # | 门 | 判据 |
|---:|---|---|
| ① | **变异残余门** | 无未恢复的变异改动 |
| ② | **并发 Git 进程与锁门** | `pgrep -af '(^|/)git([[:space:]]|$)'` 为空；`.git/index.lock` 不存在 |
| ③ | **暂存区纯净门** | `git diff --cached --name-only` 必须 **0 行** |
| ④ | **工具签名门** | `commit.gpgsign` 必须 `true`；`gpg.format` 必须 `ssh` |

### 5.2 提交信息规范（Conventional Commits）

```
type(scope): concise imperative English summary
```

| 项 | 要求 |
|---|---|
| 语言 | 一行英文，祈使句，无句号，**≤72 字符** |
| `type` | **10 值**：`feat` `fix` `refactor` `perf` `test` `docs` `build` `ci` `chore` `revert` |
| `scope` | **必填**，15 建议值：`docs` `infra` `db` `auth` `rbac` `workorder` `grading` `dispatch` `rework` `audit` `vision` `web` `test` `e2e` `git` |
| 签名 | **强制 `git commit -S`**（SSH signing） |

### 5.3 逐文件暂存纪律

| 要求 | 判据 |
|---|---|
| 逐文件 `git add -- <file>` 并逐个校验 `$?` | — |
| **禁** `git add -A` / `git add .` / 目录级 / `&&` 长串 | — |
| 暂存文件数精确等于 N | `git diff --cached --name-only \| wc -l` |
| 签名验证 | `git log -1 --format='%H %G? %GS %s'` 的 **`%G?` 必须为 `G`** |

### 5.4 拦截红线资产（**永不入库**）

```
.env  .env.local  *.pt  *.onnx
vision/datasets/  uploads/  .team/  .evidence-backup/
web/node_modules/  web/dist/  .venv/
```

### 5.5 静态门禁（**正交，必须独立跑**）

| 命令 | 说明 |
|---|---|
| `uv run ruff check .` | **与下一行正交，必须独立跑，不得合并** |
| `uv run ruff format --check .` | — |
| `uv run mypy backend vision` | ⚠️ **Stage 1 未预装 mypy/pyright** → 必须显式声明「未运行」，**严禁伪造通过** |
| `pnpm --dir web typecheck` | `vue-tsc --noEmit` **必跑** |
| `pnpm --dir web lint` | — |
| `pnpm --dir web build` | — |

**`data-testid` 变更**：必须已同步 `docs/API-CONTRACT.md` 且有 Lead 放行记录。

### 5.6 长命令与破坏性命令纪律

| 规则 | 内容 |
|---|---|
| **禁** `cmd \| rg/grep/tail/head` | 必须 `cmd > "$LOG" 2>&1` 后单独读日志 |
| **禁** `git reset --hard` | — |
| **禁** `git clean` | — |
| worktree/缓存/构建目录 | **禁放 `/tmp`**；用完立即 `git worktree remove` |
| 验库范围 | 仅限 `tomori_test` |

### 5.7 本协议**未规定**的事项（**不得编造**）

`COMMIT-PROTOCOL.md` 全文**无分支命名规范、无 rebase/merge/冲突解决策略**（`rg 'branch|rebase|merge|冲突|conflict'` 仅 2 处，且都是读分支名的命令）。

> **本协议未规定分支规范，由 Lead 另定。** 在 Lead 另行规定前，**不得**自行发明分支命名或 rebase 策略。

---

## 6. 文档域纪律

### 6.1 坐标四级判据（`TEAM.md` §3.2，`docs-writer` 的核心工具）

| 级 | 判据 | 失效形态 |
|---:|---|---|
| 1 | 行号存在（在文件范围内） | 越界 |
| 2 | 行内容命中（非空白） | 空行 |
| 3 | 内容与断言语义一致 | 行号对但指向相邻函数 |
| 4 | **产物身份可追溯** | 结论对但归属错 |

> 🔴 **关键教训：「纯脚本只能验第 1 级」。**
> `RUNBOOK.md` 37 处坐标脚本报 `bad=0`，实际 **8 处 compose 坐标内容全错**——第 2/3 级必须人工或独立方复核。**这是 `docs-writer` 自己犯过的错，记在 `TEAM.md` §5.5。**

### 6.2 三个边界声明（`auditor` 强制要求）

每份文档的诚实边界节必须**三栏齐全**：

| 栏 | 内容 |
|---|---|
| **已证实** | 有坐标/命令/原始输出支撑的 |
| **未证实** | 设计成立但无运行验证的 |
| **未运行/未审计范围** | **阶段一无应用源码 / 无 DB 迁移运行态 / 无 E2E 实跑 / 未触及远端 GPU 实机** |

### 6.3 报告模板强制坐标（`TEAM.md` §5.5 的纠正结论）

`docs-writer` 曾两次报错 HEAD 哈希（用旧记忆值）。**纠正方式不是「记住要核」，而是把三个命令写进报告模板**——**模板强制 > 记忆提醒**。

```bash
git rev-parse HEAD
git --no-optional-locks log --oneline -1
wc -l <目标文件>
```

> **「过时的坐标比没有坐标更糟」**——读者按 382 行去翻会翻到无关内容。**这也与「计数型标题」是同一个病：数字只应出现在它的唯一来源里。**

---

## 7. 协作纪律

### 7.1 任务板是唯一协调机制（`TEAM.md` §2.1）

- 口头/消息里说的**不算**派工，必须在任务板上。
- 认领后 `status=in_progress`；完成必须 `action="complete"` + `expected_revision`。
- **`ready=false` 只是任务板显示态**，不代表不能写（Lead 已确认）。

### 7.2 派工判据：域优先于可用性（`TEAM.md` §2.6）

**谁拥有这个域，就派给谁**——不因为某人「现在有空」就把他的域的任务派给别人。

> **本项目实例**：Lead 明确裁决「调研任务请交给 `docs-writer`，不要给其它队员。」即使其他人更空闲，域归属优先。

### 7.3 成员失败时：退回原主，不转派（`TEAM.md` §2.8）

成员失败时，**退回原主**（让其修正），**不转派给他人**。转派会让原主失去反馈闭环，同类错误会重复。

### 7.4 冲突处理与越权纠正（`TEAM.md` §2.4 / §2.5）

| 情形 | 处置 |
|---|---|
| 两成员写入域冲突 | 提交 Lead 裁决 |
| 发现他人越权 | 记录并报 Lead，**不自行回滚他人改动**（回滚也是写操作） |
| 契约冲突 | 以 `docs/` 定稿为准，并报 Lead |

---

## 8. 待用户裁决清单

| # | 事项 | 现状 | 影响 |
|---:|---|---|---|
| **TC-1** | 分支命名规范 / rebase-merge 策略 | `COMMIT-PROTOCOL.md` **未规定** | 需 Lead 另定 |
| **TC-2** | `mypy`/`pyright` 是否纳入门禁 | Stage 1 **未预装** | 现必须声明「未运行」 |
| **TC-3** | 远端 GPU（32G–48G）接入参数 | **待用户提供** `GPU_SSH_HOST/PORT/USER` 等 | 阶段二视觉训练阻塞 |
| **TC-4** | `auditor` 结论是否需用户终审 | 现为三档（`通过`/`带条件通过`/`驳回`） | — |

### 8.1 远端 GPU 待用户提供的接入参数清单（`auditor` 21 项之一）

| 参数 | 环境变量 | 状态 |
|---|---|---|
| SSH 主机 | `GPU_SSH_HOST` | 🔴 待提供 |
| SSH 端口 | `GPU_SSH_PORT` | 🔴 待提供 |
| SSH 用户 | `GPU_SSH_USER` | 🔴 待提供 |
| 远程工作目录 | `GPU_REMOTE_WORK_DIR` | 🟡 有默认值 |
| 远程数据集目录 | `GPU_REMOTE_DATASET_DIR` | 🟡 有默认值 |
| 远程 runs 目录 | `GPU_REMOTE_RUNS_DIR` | 🟡 有默认值 |

---

## 9. 本文档的诚实边界

**已确定的**：
- 团队真实阵容 **9 人**（复印件 `TEAM.md` 的 8 人已明确标注为 `new-new-api` 项目记录）。
- 写入域矩阵、零 git 写纪律、`docs/RUNBOOK.md` 的例外归属。
- 反假绿 8 形态（A–G + `no-op`）+ `F14` 的正确用法（**不是形态编号**）。
- 提交协议四道门、10 个 `type`、15 个 `scope`、逐文件暂存、静态门禁。
- 坐标四级判据与「纯脚本只能验第 1 级」。

**没有确定的**：
- **阶段一无任何代码运行**：`backend/`、`vision/`、`web/`、`tests/`、`migrations/` 目录**均不存在**。
- **24 张变异卡全部未执行**——它们是被设计出来的实验，不是已完成的证据。
- `mypy`/`pyright` **未预装**，静态门禁中该项**从未运行**。
- §8 的 4 项待用户裁决。

**明确不属于本文档的**：
- 需求编号 → `docs/REQUIREMENTS.md`
- 技术栈 → `docs/TECH-STACK.md`
- 部署与端口 → `docs/RUNBOOK.md`（`dev-ops` 独占）

---

## 10. 变更记录

| 日期 | 变更 | 触发 |
|---|---|---|
| 2026-11-21 | 初版。9 人阵容 / 写入域矩阵 / 反假绿 8 形态 / `F14` 正名 / 提交协议四道门 / 坐标四级判据 | `task-7` |
| 2026-11-21 | **新增 §0「`docs/team/` 适用范围声明」**：明确 `TEAM.md`/`LEAD.md` 是 `new-new-api` 项目记录，参考不照搬；表格列出端口/阵容/形态编号/表数/代码路径五类差异。**`docs/team/` 两文件零改动**（审计 P0-6 根治方案，`Lead` 裁决 C） | `audits/stage1-audit.md` P0-6 |
