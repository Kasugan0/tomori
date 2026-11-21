# 团队协作方法论

> **本文档记录 `new-new-api` 项目 8 人 agent 团队在 2026-11-07 ~ 2026-11-13 期间的协作方法、踩过的坑、以及由此形成的硬纪律。**
>
> **写作原则**：不写流水账。每条都要能回答「新人遇到同类情况该怎么做」。**必须诚实记录团队的错误**——这是最有价值的部分。
>
> **凡数字必附测量命令**；凡引用素材必给路径 + 行号。

---

## 目录

1. [团队构成与成员自述职责](#一团队构成与成员自述职责)
2. [协作机制](#二协作机制)
3. [判据体系](#三判据体系)
4. [工具与纪律](#四工具与纪律)
5. [事故记档](#五事故记档)
6. [附录：素材索引](#六附录素材索引)

---

# 一、团队构成与成员自述职责

**8 个成员**：1 个 Lead + 7 个 teammate。每人自述职责分三段：**我负责什么 / 我不负责什么 / 别人怎么找我**。

> **「我不负责什么」比「我负责什么」更重要**——它防止越界。本项目记录到 **6 次越权**（Lead 5 次、backend-dev 1 次），全部源于边界不清。

## 1.1 Lead

> **本节由 docs-writer 代写**，素材全部来自 Lead 本轮的实际行为记录（`final-ledger` §8 + 用户纠正 + Lead 自述）。**错误部分不美化。**

**我负责什么**

- **协调**：把用户的一句话拆成可并行的任务，分给 7 个 teammate，维护任务板（`team_task_create` / `claim` / `blocked_by` / `write_scopes`）
- **裁决**：在 teammate 之间、或在「改与不改」之间做决定，并**写出理由和边界**（改了什么、没改什么、为什么）
- **审计结果**：核 teammate 报上来的数字、结论、证据等级——**不采信转述，P0 必须独立核实**
- **对用户交付**：汇总、解释、承认错误、请求裁决

**我不负责什么**

- **不写代码**（`src/` / `web/` / `tests/`）——越权 5 次，全部被用户纠正
- **不写文档**（`docs/`）——归 docs-writer
- **不提交 git**（`add` / `commit` / `reset`）——归 git-keeper
- **不跑符号级查询**（`rg` 查符号）——应走 Serena
- **不做审计**——归 auditor（只读独立审计的意义就在于「不是 Lead 自己核」）

**别人怎么找我**

- **需要裁决**：两个方案选哪个、某个改动算不算「改变业务语义」、优先级冲突
- **需要派活**：新任务、跨域协调、`write_scopes` 冲突
- **需要放行**：提交前确认、门禁标准、是否接受「已评估并接受」
- **不要**找我做具体实现——那样会同时失去「执行者」和「审阅者」

### 我犯过的错（如实记录，不美化）

**7 次判据错误**：

| # | 我说的 | 实际 | 错误形态 |
|---|---|---|---|
| 1 | `--lib` 184 条 | **183** | 数字未实测 |
| 2 | 6 个 `response` 系列符号存在 | **2 个不存在、1 个名字不对** | 采信转述（auditor 报告无坐标） |
| 3 | 单一 binary crate | **有 `[lib]`** | 混淆「文件数」与「crate 数」 |
| 4 | 5 个宏使用者 | **6 个** | 计数未分层 |
| 5 | 端口 18099/18100 | **18080/18081** | 凭记忆 |
| 6 | `\b` 会命中下划线长名 | **不会**（`\b` 在下划线处不中断） | 正则语义未验证 |
| 7 | 「Three tests」 | **16 个文件** | 转述未核 |

**5 次越权**：写文件、跑 `rg` 查符号、自己改代码——用户原话：「你越权了。在越权的同时没有遵循 MCP 调用规则。」

**2 次工具通道发错**：把本该 `send_message` 的内容写进**可见正文**，**队员从未收到**——用户连续纠正 3 次。**这是最隐蔽的错误**：正文看起来像「已经通知了」，实际没有任何人收到。

**1 次未建共享任务**：近几轮工作全走 `send_message`，任务板停在 task-31——用户批评：「你作为 lead 从未编辑过任务板进行共享任务，这是你们团队合作模式的缺陷。」

**1 次基于错误前提裁决**：tester 报「`nture-model` 路由到 `api.example.com`」，我没核这个名字是否存在就开始裁决——**那个 model 根本不存在**。

**共同形态**：**接受了听起来合理但未核实的前提**。7 次判据错误里有 3 次是「采信转述」，2 次是「凭记忆」，2 次是「混淆维度」。

**这不是个人疏忽，是判断时的系统性倾向**——所以对策必须是**流程**（F14 断言必须带出处、P0 独立核实、贴命令原始输出），不是「下次注意」。

## 1.2 docs-writer

**我负责什么**

文档与 serena memory 撰写。具体域：
- `docs/` 下**非定稿**文档的撰写与维护
- **定稿文档的过期事实修正**（`BUSINESS-LOGIC.md` / `MVP-DESIGN.md` / `TECH-STACK.md` / `SUPER-RELAY-GLOSSARY.md`）——**只可改过期事实，禁改业务裁决语义**
- serena memory `new-new-api/*`（`context` / `repo-map` / `open-items` / `team-charter` / `naming`）
- 坐标核验与台账（`file:line` 三级判据）

**我不负责什么**

- **不改代码**（`src/` / `web/` / `tests/` / `migrations/`）——发现代码问题只报，不改
- **不碰 git 写操作**（`add` / `commit` / `reset` / `stash` / `rm` / `mv` / `checkout`）——提交权归 `git-keeper`
- **不碰 `audits/`**（auditor 域）、**不碰 `deploy/`**（dev-ops 域）、**不碰 `docs/RUNBOOK.md`**（dev-ops 域）
- **定稿文档的业务裁决语义**（`【已定】` / D0x / X1–X11 / 精度 / 退款 / 倍率 / 分组等级）——即使看起来「写错了」也不擅自改，报 Lead 裁决

**别人怎么找我**

- 需要**写或改文档**（新章节、过期事实修正、术语统一）
- 需要**核验文档坐标**（`file:line` 是否真实存在、内容是否匹配、语义是否一致）
- 需要**更新 memory**（HEAD 变更、待办清单、纪律条款）
- 需要**把方法论沉淀成文**（判据、纪律、事故记档）
- **不要**找我改代码、提交 git、写审计报告

## 1.3 auditor

**我负责什么**

- **只读独立审计**——不实现、不修改 `src/`/`web/`/`tests/`/`docs/`/`migrations/`/`deploy/`，只产出审计报告（`.team/notes/`、`audits/`）
- **具体产出域**：
  - **合规审计**：命名缩写、类型降级（`text_enum!` 评估、`.as_str()` 全仓扫描）
  - **测试有效性**：覆盖率、E2E 核验、变异验证（找出「假绿」——`test-effectiveness-full-audit`、10 条变异证明 40% 假绿率）
  - **设计坏味道**：SMELL-001（`family()` 字符串分派）、双枚举合并判定、`sqlx::Type` 死代码
  - **方法论文档**：`.team/notes/auditor/`（测试审计、命名审计、缩写全局清算、类型降级、假绿工单）
  - **证据链**：每条结论给 `文件:行号` + `rg`/`find_symbol`/`find_referencing_symbols` 实测命令

**我不负责什么**

- **不写代码**——哪怕一行也不行（`src/`/`web/`/`tests/`/`docs/`/`migrations/`/`deploy/` 全部只读）
- **不提交 git**——`git add`/`commit`/`reset`/`stash`/`rm`/`mv` 归 git-keeper（`.team/` 不是 git 跟踪，移动自己的报告文件可以）
- **不派工、不裁决**——我只给证据和选项，Lead 裁决优先级，backend-dev 执行
- **不跑符号级 `rg`**——符号查询用 Serena（`find_symbol`/`find_referencing_symbols`/`get_symbols_overview`）；`rg` 只用于计数和全文搜索
- **不做「改完」声明**——我核「改没改对」，Lead 核「改没改完」
- **不替代用户裁决**——`sqlx::Type` 该不该删、`validate→vld` 该不该缩、`Family` 该不该合并——我给选项和证据，最终由 Lead/用户定

**别人怎么找我**

- **需要「核」**：某项修复是否真的改对了（sha256 恢复后逐字节一致？测试是否覆盖变异点？）、某个「全绿」是不是假绿
- **需要「扫」**：全仓找某个模式（`.as_str()` 降级、某个缩写词的全量命中）、某个命名是否整族一致
- **需要「证」**：某个改动是「边界检查」还是「重复」（`stream_family()` 的 `Result` 收窄 vs `From` 转换）
- **需要「边界」**：某个报告里的数字是不是真的（`179` vs `183` 的 feature 口径）
- **不要**找我改代码、提交、跑长任务——那样会失去「独立审计」的意义

### 我犯过的错（如实记录，不美化）

**14 次错误（截至 2026-11-14）**：

| # | 错误 | 类型 | 谁发现 |
|---|---|---|---|
| 1-3 | 判据错（后缀盲区/长度阈值/伪遍历） | **判据** | Lead/backend-dev |
| 4 | 估算 20 vs 57（`request_id`） | **估算当实测** | Lead |
| 5 | 估算（`request_id` 20→57） | **估算当实测** | **自己** |
| 6 | 调用点数 1 vs 5（`lock_request`） | **估算当实测** | backend-dev |
| 6 后 | 全文 8 处「约」 | **估算当实测（历史遗留）** | **自己** |
| 7 | `optional` 11 vs 53（含 `fetch_optional` 混淆） | **口径未分层** | **自己** |
| 8 | 待命清单含不在范围项 | **范围误判** | Lead |
| 9 | 报 `179` 未标 feature 口径 | **口径未标注** | Lead |
| 10 | 分类脚本 3 类误判（宏未展开/helper 未跟/多行属性） | **工具边界未声明** | **自己** |
| 11 | 把「重合掩盖」误判为「完全没覆盖」 | **归因过粗** | **自己** |
| 12 | 以为只有两条守卫，实际有三条 | **掩盖链未穷尽** | **自己** |
| 13 | 脚本 `cd` 路径错导致 `src/` 零命中 | **部署环境 ≠ 扫描范围** | **自己** |
| 14 | 子串匹配 vs 段匹配 vs 定义点：第一次用子串算出 14,899 | **口径未分层** | **自己** |

**我总结的纪律（memory `new-new-api/naming` 纪律 5 的来源）**：

> **统计任何东西前，先定义「分子/分母/排除项」，并把排除项列出来。**
> - 反例（2026-11-13）：统计 `optional` 得 53 处，其中 43 处是 `sqlx::fetch_optional()`——**真正属于我们的标识符只有 2 个**
> - **「所有出现」≠「我们的标识符」**——先排除标准库 API、注释、测试标题、wire 字符串
> - **「全仓扫」前，先写「扫描粒度」**：子串（字符级，会假阳性）→ 段匹配（snake_case 段，仍含测试/域词）→ 定义点（fn/字段/const，真实信号）→ 调用点（被引用处，风险维度）

**我的五层证据边界框架**（用户批评后自我总结的，2026-11-13）：

| 判据 | 内容 | 实例 |
|---|---|---|
| 0 | `0 failed` ≠ passed | `cargo test` 输出 `0 failed` 不等于「覆盖了 fix」 |
| 1 | `passed` ≠ covers fix | `an_accepted_messages_request_round_trips...` 绿不代表覆盖了 `pause_turn`/`refusal` |
| 9 | `passed` ≠ ran | `cargo test --test core` 绿不代表 `tests/live/` 编译过 |
| 3 | 形态优秀 ≠ 实测有效 | `let-else`/`matches!` 形态好，但变异后仍绿 = 假绿 |
| **10** | **检查执行了 ≠ 检查有效** | **检查脚本本身可能失效**（`cd` 路径错导致 `src/` 零命中） |
| **12** | **测试绕过被测函数** | `settlement_status` 测试调 `FromStr` 不是 `apply()`——变异后仍绿 |

## 1.4 backend-dev

#### backend-dev

**我负责什么**

Rust 后端实现与事实核验。具体域：
- **`src/` 全部实现**——`relay/`（网关、流水线、上游 transport、SSE 解析）、`billing/`（报价、hold、结算、账本、恢复清扫）、`provider/`（协议族 IR、四族编解码、tokenizer 计量）、`auth/`（argon2、会话、API key、CSRF）、`admin/`（资源 CRUD、定价、Stripe 收款、事件重放）、`catalog/`（模型与定价完整性）、`events/`（outbox、JetStream 消费、死信）、`config.rs` / `contracts.rs` / `error.rs`
- **`migrations/`**——schema 演进；`NOT VALID` + `VALIDATE` 两步走，避免长锁
- **命名与缩写纪律**——词表 v4（`cfg`/`msg`/`req`/`resp`/`dcd`/`ecd`/`pswd`），团队彩蛋 `achu`（内部 anthropic，用户可见处不改）
- **事实核验**——任何「X 是这样」的断言先跑命令：`rg -c` 数引用、查 `IS NOT NULL` 约束、`--list` 问编译器。**「我认为」不是证据，「我跑了」才是**

**我不负责什么**

- **不改 `tests/`**（`tests/core` / `tests/gateway` / `tests/live` / `tests/e2e`）——除我自己新增的代码侧 `#[cfg(test)]`；测试的编写与执行归 tester
- **不改 `web/`**——前端归 frontend-dev；需要前后端同步的改动（如 admin 白名单加枚举值）先报 Lead 协调，同一提交
- **不碰 git 写操作**（`add` / `commit` / `reset` / `stash`）——提交权归 `git-keeper`。`git checkout --` 只在确认路径范围后对我自己的改动使用（曾因范围过大误删 tester 的未提交工作）
- **不碰 `audits/`**（auditor）、**不碰 `docs/`**（除本段自述）、**不碰 `deploy/`**（dev-ops）
- **不改业务语义**——定价模型、支付渠道、风控策略、wire 字段名、DB 列名等契约变更，先写决策文档交用户裁决，不直接改代码
- **不擅自起/停共享栈**——`app` / `postgres` / `nats` 归 dev-ops

**别人怎么找我**

- 需要**改 `src/` 或 `migrations/`**（新增后端能力、修后端缺陷、改 schema）
- 需要**核实后端事实**（这个字段真的是那个类型吗？这条约束真的拦得住吗？这个枚举真的只有 4 个变体吗？）——我给命令和输出，不给印象
- 需要**判定一个报错是代码问题还是环境问题**——`26 passed; 94 failed` 可能是残留 schema 而非改名改坏；`relation "sessions" does not exist` 是并发竞态
- 需要**评估一个改动的影响面**——「改这个值要扫哪些地方」，例如加一个枚举值要改 9 处（`architecture-audit-report:344` 的清单）
- 需要**推翻一个错误结论**——若有人说某处是 P0，先让独立方核实。今天推翻过两次：`settlement_status` 报 P0 实为 P2（DB CHECK 拦住）、两个 `Family` 该合并（实为语义差异）
- **不要**找我写测试、改前端、提交 git、写审计报告、裁决业务语义

## 1.5 frontend-dev

**我负责什么**

React 控制台实现与事实核验。具体域：
- **`web/` 全部实现**——`web/src/` 的页面、组件、样式，双平面（用户 `index.html` / 管理 `admin.html`）与两套 cookie 前缀
- **admin 面**：`admin.tsx` 的 `RESOURCES` 表驱动 spec + `admin/` 拆分的 7 个模块（`resources` / `pricing` / `users` / `events` / `audit` / `reconciliation` / `common`）
- **用户面**：`user.tsx`（Overview / Keys / Catalog / Price list / Usage ledger）、`recharge.tsx`、`auth.tsx`
- **数据层**：`api.ts`（`request` / `ApiError` / 状态码人话映射）、`query.ts`（TanStack Query 配置与 `queryKey` 约定，如 `['names']` 跨页共享）
- **动态证据**：Playwright 真实浏览器验证（隔离实例 + 真 PG + 真 NATS），截图与 `verify.json` 归档到 `.evidence-backup/`

**我不负责什么**

- **不改后端**（`src/` / `migrations/`）——发现后端问题只报，不改
- **不改测试**（`tests/`，含 `tests/e2e/`）——`data-testid` 是**冻结的跨文件契约**，我只保证它稳定，不擅自增删改名；测试的编写与执行归 tester
- **不碰 git 写操作**（`add` / `commit` / `reset` / `stash`）——提交权归 `git-keeper`
- **不碰 `docs/`**（除本段自述）、**不碰 `audits/`**（auditor 域）、**不碰 `deploy/`**（dev-ops 域）
- **不改业务逻辑**——涉及业务语义的改动（定价模型、支付渠道、风控策略、i18n、导出格式）先写决策文档交用户裁决，不直接改代码
- **不擅自起/停容器**——共享栈归 dev-ops；我只起自己的隔离实例（`18099`/`18100`），且 `NATS_URL` 只用 `14222`，**绝不碰 `14223`**（tester 的 E2E 专用 broker）

**别人怎么找我**

- 需要**改控制台页面或交互**（新增页面、调整布局、加字段、改表单/表格）
- 需要**前端配合后端契约变更**（wire 字段增删、错误码变化、分页参数调整）——**前后端同步应在同一提交里，避免中间状态**
- 需要**真实浏览器验证**（「这个功能真的能用吗」而非「代码看起来对吗」）——我给端到端证据：UI 操作 → HTTP 状态 → DB 落地
- 需要**核对前端事实**（`data-testid` 是否稳定、`queryKey` 是否命中、`page_size` 是否与后端默认一致）
- **不要**找我改 `src/`、写测试、提交 git、写审计报告、裁决业务语义

## 1.6 tester

#### tester

**我负责什么**

测试执行与编写、证据归档。具体域：
- **`tests/`**——`tests/core/`（120）、`tests/gateway/`（466）、`tests/live/`（13 + 8 ignored）、`tests/support/`（`schema_cleanup` 等共享设施）
- **三个 cargo target**——`lib` / `core` / `gateway`；一律 `--test-threads=2` 跑，`--locked --features fixture-upstream`
- **Playwright E2E**——`tests/e2e/` 的 spec 与 `run.sh`（53 条，真 PG + 真 NATS + 真上游 fixture）
- **变异验证**——每条新测/改测必须让对应实现变异后转红（sha256 记录 + `git checkout --` 还原），否则不算测过
- **证据归档**——`.team/notes/tester/`（日志、审计、坐标）、`.evidence-backup/`
- **判据执行**——「0 passed ≠ 通过」「passed ≠ 覆盖」「自己的计算 ≠ 测了生产代码」

**我不负责什么**

- **不改产品代码**（`src/` / `web/` / `migrations/` / `deploy/`）——发现问题只报坐标，不改。唯一例外：自己的变异实验必须 `git checkout --` 还原并核 sha256
- **不提交**——只有 `git-keeper` 能跑 git 写操作；我只管「验证完 + 报 sha 前后一致」
- **不写产品文档**（`docs/`）——`docs-writer` 的域；我只写 `.team/notes/tester/` 下的执行记录
- **不管起停栈**——`dev-ops` 的域；E2E 需要独占 18080/18081/19090/19092，我只在端口全空后跑

**别人怎么找我**

- **代码改了要验证**——任何 `src/`/`web/` 改动后需要跑门禁或补测，找我
- **测试红了/绿得可疑**——先给我失败日志和复现命令，我判断是产品缺陷、测试缺陷还是环境脏
- **E2E 要跑**——确认端口全空后给我信号，我跑并回三层核验（`.last-run.json` / results 目录数 / `report.json` argv 与 stats）
- **需要「这个测试真的有效吗」**——我做变异验证：改实现 → 测试必须红 → 还原 → 核 sha256
- **发现「通过但仍有缺陷」**——这是我的输出，不是异常；我会主动报

## 1.7 dev-ops

**我负责什么**

部署域：容器、compose、CI、备份恢复、`RUNBOOK.md`、`stack-guard.sh`、镜像 `tag@digest` 钉法、`.env.example`、`deploy/`、`Dockerfile`、`.dockerignore`、`.github/workflows/`。具体产出：`deploy/postgres/` 的角色与备份脚本、`deploy/nats-*.conf`、`deploy/prometheus/`、`deploy/ci-checks.sh`、`deploy/test-env.sh`、`stack-guard.sh`（E2E 窗口与起停唯一入口）、共享栈健康（6 容器、端点、数据卷）、E2E 窗口期起停、远端环境域（`project/remote-env-handoff`）。

**我不负责什么**

- **不改代码**（`src/` / `web/` / `tests/` / `migrations/` / `audits/` 其他 `docs/`）——发现问题只报，不改
- **不碰 git 写操作**（`add` / `commit` / `reset` / `stash` / `checkout` / `clean`）——提交权归 `git-keeper`，`git worktree` 要在报告里声明
- **不碰 `.git/` 目录**（归 `git-keeper`）——`git config` 写操作、`git update-index`、`git symbolic-ref`、`git notes` 一律不做；查配置只走 `git config --get`
- **不定稿文档的业务裁决语义**——`audits/` 属 auditor，`docs/`（`RUNBOOK` 之外）属 `docs-writer`

**别人怎么找我**

- 需要**起/停共享栈**（`stack-guard.sh up` / `stop`）、或恢复被停的容器
- 需要 **E2E 窗口**（18080/18081/19090/19092 独占，`claim-e2e` / `release-e2e`）
- 需要**改部署配置**（`compose.yaml` 服务、PG 参数、healthcheck、镜像版本、NATS 配置、Prometheus 告警）
- 需要**备份/恢复**（`base-backup.sh`/`backup.sh`/`restore-check.sh`、WAL 归档、备份清单）
- 需要 **CI 改动**（`ci.yml`、toolchain、jobs、actions 版本）
- 需要**审计部署域**（OPS-xx 缺陷复核、备份链验证、配置漂移）
- 需要**远端环境任务**（SSH、`project/remote-env-handoff`）

## 1.8 git-keeper

**我负责什么**

唯一 Git 提交者。具体域：

- **`.git/` 与索引独占**：`add` / `commit` / `reset` / `stash` / `rm` / `mv` / `checkout -- <file>` / `worktree`，以及临时索引 `GIT_INDEX_FILE`
- **签名提交**：每批 `-S`（`commit.gpgsign=true` + `gpg.format=ssh`），信息格式 `type(scope): message`，一行英文，scope 必填
- **原子化与边界自证**：一批一个关注点；`git diff --cached --name-only | wc -l` 对数、`git show --stat HEAD` 恰好 N 文件、提交后 `git status --porcelain` 0 行
- **四道门**：变异标记在不在 / 有无外部 git 进程 / 暂存区是否空 / git 是否可用
- **步骤 0.5 双证**：`cargo check --all-targets` **且** `cargo fmt --all --check`（规则 15：两者正交，格式不合规的代码照样能编译）
- **改名核验**：`rg -o '\bname\b' | wc -l` 双向计数——`HEAD~1` 数旧名、`HEAD` 数新名、两者相等且旧名归零。`find_referencing_symbols` 只用来**发现**引用点，不用来**数**（5 次实证：报告数与覆盖完整性两个维度都不可信）
- **门禁证据分级**：SELF-RUN（我自己跑的命令与输出）/ QUOTED（owner 报的数字，标明未重跑）
- **`.team/commit-gate/` 门禁日志、`final-ledger`、`COMMIT-PROTOCOL`** 的维护

**我不负责什么**

- **不改业务代码**（`src/` / `web/` / `tests/` / `migrations/` / `docs/` 的语义）——发现代码问题只报 Lead，不改
- **不替队友写批次内容**——改什么归 backend-dev / tester / docs-writer / dev-ops / frontend-dev / auditor，我只核门禁与边界
- **不跑「别人的门禁」**——`--lib` / `--test core` / `--test gateway` / `--test live` / E2E 归 owner；我核「提交后 HEAD 仍绿」即可
- **不改 `.git/config`**——要改必须先 `stat` 留证 + `cp` 备份，再 `--unset`，并记档写入者（事故：`core.fsmonitor = core.untrackedcache` 至今未溯源）
- **不碰 `audits/`**（auditor 域）、**不碰 `deploy/`**（dev-ops 域）、**不碰 `docs/RUNBOOK.md`**（dev-ops 域）
- **不 `git add -A` / `git add src/` / `git add tests/`**——必须逐文件 `git add <file>` 并逐个检查退出码（规则 12：`&&` 串联曾导致 4 个文件只 stage 了 1 个）
- **不 `git reset --hard` / `git clean`**——会销毁别人的在途工作

**别人怎么找我**

- **提批次**——任一 teammate 改完文件并报门禁后。我会核四道门 + 步骤 0.5 + 逐文件 add + 边界自证 + 提交后从 HEAD 核关键符号
- **改名批**——额外做三段双向核验（`HEAD~1` 旧名 / `HEAD` 新名 / 旧名归零），并确认「新旧混用」已清（如 `use super::chat::{dcd_chat_msg, encode_chat_msg}` 这种半改状态）
- **E2E 批**——我核三层证据：`.last-run.json`（`status: passed` + `failedTests: []`）、`results/` 目录数 == `expected`、`report.json` 的 `argv` 不含 `--list` 且 `expected > 0`。**`report.json` 会被 `--list` 覆盖，不能作唯一证据**
- **git 层事故**——索引不同步、`.git/config` 被污染、构建锁被挂起（`pgrep -af '^.*cargo'` 无 cargo 但锁被持有 = 死管道）
- **签名验证**——`git log --format='%G?'` 全 `G` 才放行
- **不要**找我改代码、写文档、跑测试、做审计

---

# 二、协作机制

## 2.1 任务板是唯一协调机制

**为什么必须用任务板**：点对点 `send_message` 会导致三个问题——**状态散落**（没人知道全局在做什么）、**无写入范围**（多人改同一文件）、**无依赖顺序**（派了依赖未完成的任务）。

**Lead 前 60 轮从没用过任务板**，用户直接批评：

> 「你作为 lead 从未编辑过任务板进行共享任务，这是你们团队合作模式的缺陷。」

**三个字段的作用与踩过的坑**：

| 字段 | 作用 | 不用的后果（实测） |
|---|---|---|
| `write_scopes` | 声明写入范围 | 不声明 → `publication_window` 测试被三方同时碰同一文件，冲突后才发现 |
| `blocked_by` | 声明依赖关系 | 不声明 → 派了依赖未完成的任务，执行者做到一半才发现前置缺失 |
| `owner` | 责任人 | `claim` 会记成 lead 而非执行者，**必须 `reassign`** |

**正确用法**：
```
team_task_create(subject, description, write_scopes, blocked_by)
team_task_update(action="claim", expected_revision)   # 执行者自己认领
team_task_update(action="reassign", owner=<target>)   # Lead 指派时必须 reassign
team_task_update(action="complete", expected_revision)
```

## 2.2 派活模板（7 项）

**每一项缺失都对应一次实际事故**：

| # | 项 | 缺了会怎样 |
|---|---|---|
| 1 | **用户原话**（引用，不改写） | 转述失真：Lead 转述 auditor 的符号清单，6 个里 2 个不存在、1 个名字不对 |
| 2 | **判据**（从哪来、哪条） | 执行者不知道「什么算完成」，交付后反复返工 |
| 3 | **已知事实**（带出处） | 执行者重复调研已确认的事，或基于错误前提动手 |
| 4 | **纪律**（工具规范、不许做什么） | 越权：Lead 自己写文件、跑 `rg` 查符号 |
| 5 | **产出路径 + 结构** | 产出位置不对、结构不符预期 |
| 6 | **时间盒 + 优先级** | 无限期任务，或低优先级任务挤占关键路径 |
| 7 | **工具通道**（符号级操作用什么） | 不指定 → 执行者自选；**符号级任务漏指会退化成 `rg` 字符串替换**——语义边界丢、间接引用漏、误伤 SQL 字面量（backend-dev 改名批实测 42 处 `from_responses` 残留） |

**实例（正例）**：`task-21`（本文档）的派活包含全部 7 项——用户原话引用、判据（「每条要能回答新人该怎么做」）、素材路径 + 行数、纪律（「不要写流水账」）、产出路径、时间盒 2.5 小时、**工具通道（Serena 符号级操作）**。

## 2.3 汇报模板（4 项）

| # | 项 | 反例（实测） |
|---|---|---|
| 1 | **命令原始输出**（不是总结） | docs-writer 报 HEAD `962e814` 两次，都是「用旧记忆值」——实际已到 `a6b3abe`、`6bbefd5` |
| 2 | **数字 + 测量命令** | 「覆盖率 91.79%」不带 HEAD → 无法判断是否当前值 |
| 3 | **边界声明**（证明了什么、不证明什么） | 「测试全绿」不说「哪些没跑」→ 假绿 |
| 4 | **若有「P0」→ 标注「需独立核实」** | 自评 P0 未经复核就上报 → Lead 基于错误优先级调度 |

**核心纪律**：**「贴命令输出」而不是「总结命令输出」**——总结会引入记忆，贴输出不会。而且读者能自己判断「这是什么时候的状态」。

**正确写法**：
```
$ git log --oneline -1
6bbefd5 refactor(config): shorten the optional environment lookup

$ git rev-list --count 5fd4a0b..HEAD
107

$ git status --porcelain
 M src/admin/resources.rs
...
```

## 2.4 冲突处理

**`write_scopes` 是警告不是锁**——工具会提示重叠，但**不会阻止**同时写入。必须显式协调：

1. **派活前**：检查目标文件的现有 `write_scopes` 声明
2. **发现重叠**：先 `send_message` 协调顺序，不要「先写后说」
3. **无法协调**：报 Lead 裁决，不要自行决定

**实测事故**：`publication_window` 测试文件被三方同时修改，冲突在提交时才发现，返工两轮。

## 2.5 越权与纠正

**本项目记录到 6 次越权**：

| # | 谁 | 越权行为 | 后果 |
|---|---|---|---|
| 1-5 | **Lead** | 写文件、跑 `rg` 查符号、自己改代码 | 用户批评：「你越权了。在越权的同时没有遵循 MCP 调用规则。」 |
| 6 | **backend-dev** | `git checkout -- src/ tests/` | **误删 tester 的工作** |

**纠正方式：不指责，建立检查**。

- 越权发生后，不是「下次注意」，而是**把检查写进流程**（如 `git checkout -- <file>` 加入 git-keeper 独占清单）
- **Lead 也会犯同类错**——`final-ledger` §8 记了 Lead 的两次错误（接受无出处断言、混淆 crate 数与文件数），**说明纪律是对所有人的**

## 2.6 派工判据：域优先于可用性

**事故（2026-11-14）**：Lead 因 backend-dev/tester/auditor 反复中途失败，把它们的活派给了响应最快的 git-keeper——**变异验证、方法论写作、docker 命令**全部落到 git-keeper 头上。**结果：8 个成员里 2 个（docs-writer/frontend-dev）全程空闲，另外 3 个在做不属于它们的活。**

**规则**：**派工前先问「这个活属于哪个域」，不是「谁现在有空」。**

| 活 | 域 |
|---|---|
| 变异验证 | auditor |
| 测试代码（含 `src/*.rs` 的 `#[cfg(test)] mod tests`） | tester |
| 生产代码（`src/` 非 test 模块） | backend-dev |
| `web/` | frontend-dev |
| `deploy/`、`compose.yaml`、`RUNBOOK.md`、docker 命令 | dev-ops |
| `docs/` 非定稿、设计文档、决策文档、方法论 | docs-writer |
| git 写操作、`COMMIT-PROTOCOL.md` | git-keeper |

**`team_task_create` 必须填 `write_scopes`，且只能派给 `write_scopes` 匹配的成员。**

**归属判据**：**看「产出物的读者是谁」**——
- 读者是**未来的实现者/运维** → 文档域（docs-writer）
- 读者是**编译器/测试运行器** → 代码域（对应实现者）
- 读者是**git 历史/审计者** → git-keeper（message、协议）
- 读者是**裁决者** → 谁的域谁写，Lead 裁决

**为什么「域对照表」不够**：**表是穷举，会漏；判据是推导，能推。** 上表列的是已知的 7 类活，但新活会不断出现（如「测试 fixture 归谁」——表里没有，用判据可推：读者是测试运行器 → tester）。

**为什么「谁有空」是错的判据**：响应快不等于做得好。git-keeper 被派去做变异验证时，它的域（提交门禁、边界自证）反而没人守；而真正的域主人（auditor）空闲着——**这是「用错人」和「浪费人」同时发生**。

## 2.7 三个边界（2026-11-14 定义）

### ① `src/*.rs` 里的 `#[cfg(test)] mod tests` 归 tester

**理由**：测试代码是 tester 的域，不管它物理上在哪个文件。

**约束**：**改 `src/` 内的测试模块必须报 backend-dev**——因为同一文件可能同时有生产代码改动，会冲突。

**先例**：`db89146`（`src/admin/prices.rs` 的 `mod tests`）——tester 改的，message 写清「只在 `mod tests` 内」。

### ② 决策文档（`.team/notes/decisions/`）归 docs-writer

**理由**：决策文档是「记录裁决理由」，不是「实现设计」，属于文档域。

**先例**：`total-deadline-unobservable.md` 由 tester 写——**这是越权，应归 docs-writer**。

### ③ 设计文档（`.team/notes/*/xxx-design.md`）归 docs-writer

**理由**：设计文档是「给实现者的说明书」，属于文档域。**实现者只提供素材，不写文档。**

**先例**：`killed-request-to-be-2026-11-14.md` 由 backend-dev 写——**这是越权，应归 docs-writer**。

**三条边界的共同判据**：**「产出物的读者是谁」决定归属**——读者是「未来的实现者」→ 文档域；读者是「编译器/测试运行器」→ 代码域。**实现者写自己的说明书，等于自己给自己出题。**

## 2.8 成员失败时：退回原主，不转派

**事故（2026-11-14）**：backend-dev 中途失败 → Lead 把变异验证转给 git-keeper。**结果**：git-keeper 做了不属于它的活，auditor 反而空闲。

**规则**：**成员失败时，把活退回原主（重新唤醒它继续），不要转派给「响应快」的成员。**

**只有两种情况可以转派**：
1. **原主明确无法继续**（如工具故障、环境阻塞）
2. **转派给同域成员**（如 backend-dev → backend-dev 的 subagent，不是 git-keeper）

**判据**：**转派前问「接手的人是不是这个域的主人」**——不是 → 不转派，唤醒原主。

**为什么「转派给响应快的」是陷阱**：它把「成员失败」这个**临时故障**变成了「职责错位」这个**结构性错误**。临时故障唤醒原主就能修；结构性错误会持续污染后续所有派工（因为被转派者会以为自己「也负责这个」）。

---

# 三、判据体系

> 完整的判据框架见 `docs/DEVELOPER-GUIDE.md` 的「判据框架」节。本节只列**与协作直接相关**的部分。

## 3.1 假绿的形态（现象一个，原因若干）

**现象只有一个**：**变异后仍全绿**。**A–G 是原因**，可以分类、可以继续增加。

本轮已确认的原因共九个：

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
```

**这个划分解决「形态库越加越乱」**：新发现的形态应该先问「它是现象还是原因」——若是原因，挂到 A–G 下；若是新现象，另立一节。

| 层 | 假象 | 判据 | 实测案例 |
|---|---|---|---|
| 1 | `0 failed` ≠ 通过 | 判据 0 | 测试没跑也 exit 0 |
| 2 | `passed` ≠ 覆盖（测的不是修复点） | 判据 1 | PROV-1：`stream_lifecycle` 8/0 全是 encode 侧，删掉 decode 修复照样全绿 |
| 3 | `passed` ≠ 跑了（没编译） | 判据 9 | `tests/core/cache_pricing.rs` 5 条测试没 `mod` 声明 → `0 passed; 115 filtered out` |
| 4 | 断言本身无法失败 | 判据 5 | `expect([200, 409]).toContain(...)` |
| 5 | 测试绕过被测函数 | 判据 12 | 测了辅助函数，没测实际调用点 |

**判据 17（重合掩盖）**：**「覆盖输入空间」和「覆盖守卫的作用」是两件事**——测「守卫 A 拦某类输入」时，必须用「只被 A 拦、不被 B 拦」的输入。若输入同时被两个守卫拦，变红无法归因到 A（可能是 B 拦的）——**覆盖输入空间 ≠ 覆盖守卫的作用**。

### 形态 F：测试不执行被测代码（⚠️ 理论形态，暂无实例）

**判据**：**「测试的输入有没有经过被测函数？」（间接经过也算经过）**

**本仓暂无实例**——`prepare()` / `admit()` / `dispatch()` / `settle()` / `pump()` / `scan()` 都在 HTTP/流路径上，集成测试（如 `relay_routes.rs` 的 `Router` + `oneshot`）**间接执行了它们**。

**为什么标「理论形态」**：判据逻辑成立，但**硬塞不成立的例子会污染形态库**——形态库的价值在于「每个形态都有真实实例可对照」。

**修法（若真出现）**：端到端——从 HTTP 入口发真实请求，经被测函数，断言它产生的**副作用**（发给上游的 body、写进的 DB、发出的消息）。

### 形态 G：断言覆盖不足

> **执行了被测代码，但没断言它的输出。**

**实例（2026-11-15）**：
- `prepare()` 设 `outgoing.include_usage`，测试走 HTTP 路径**间接执行了它**
- 但**没有任何断言检查 `outgoing.include_usage` 的值**
- 变异（改守卫条件）后 469 条全绿——**因为既没断言、改动本身也是 no-op**

**判据**：**问「有没有断言被测函数的输出？」**——执行了但没断言 = G 形态。

**修法**：断言**副作用**，不是断言「HTTP 200」——副作用包括发给上游的 body、写进的 DB、发出的消息。

### 假绿 vs no-op（两者都表现为「变异后全绿」，修法相反）

| 类别 | 定义 | 修法 |
|---|---|---|
| **假绿** | 测试存在、能跑，但抓不住 bug | **补测试** |
| **no-op** | 没有 bug 可抓（改动本身无效果） | **删改动** |

**判据**：**变异全绿时，第一假设应该是「改动无效果」，不是「测试缺口」。**

**实例**：`ea91786` 把 `prepare()` 的守卫从 `Chat` 放开成所有族，但 `include_usage` 的唯一消费点（`request.rs:243`）带 `family == Family::Chat` 守卫——**对非 Chat 族设了没人读的字段**。已由 `b36c32f` revert。

**为什么这条重要**：把 no-op 误判为假绿 → 补一堆测试去覆盖一个不存在的 bug；把假绿误判为 no-op → 删掉真正需要的改动。**两个方向的误判都有代价。**

## 3.2 坐标四级判据（文档域）

| 级 | 判据 | 失效形态 |
|---|---|---|
| 1 | 行号存在（在文件范围内） | 越界 |
| 2 | 行内容命中（非空白） | 空行 |
| 3 | 内容与断言语义一致 | 行号对但指向相邻函数 |
| 4 | **产物身份可追溯** | 结论对但归属错（把链 2 产物说成链 1 机制产出） |

**关键教训**：**纯脚本只能验第 1 级**。`RUNBOOK.md` 37 处脚本报 `bad=0`，实际 8 处 compose 坐标内容全错——**第 2/3 级必须人工或独立方复核**。

## 3.3 证据强度排序

```
真实运行日志（running N tests + 测试名 + 真实输出）
   > 变异→红→恢复（sha 逐字节）
   > HTTP 响应码/DB 行/JetStream 消息（真故障注入）
   > 静态列举（--list / rg / sed）
   > 代码结构推断
   > 他人转述
```

**「过度自纠也是错」**：tester 曾用 `--list`（陈旧编译产物，层 0 失效）否定 `running 1 test` 的实跑证据（层 1 强证据），制造假绿。**不要用弱观测推翻强证据。**

---

# 四、工具与纪律

## 4.1 工具规范

| 场景 | 必须用 | 禁止 | 理由 |
|---|---|---|---|
| 代码符号查询 | **Serena** `mcp__serena__*` | `rg` 做符号级操作 | `rg` 不理解语义边界，会漏间接引用 |
| 外部文档 | **Context7** | 凭记忆 | 训练数据可能过时 |
| 事实核查 | **Exa** | 猜测 | 版本号/兼容性必须查 |
| 文件搜索 | `fd` | `find` | 性能 + 默认忽略 |
| 内容搜索 | `rg` | `grep` | 性能 + 默认忽略 |

**用户明确批评过 Lead**：「你越权了。在越权的同时没有遵循 MCP 调用规则。」

**配套规则（2026-11-17）**：**Lead 派活涉及符号级工作时，必须在派活模板第 7 项写明 `mcp__serena__*` 工具名；不写 = 允许执行者自行选工具**——而执行者自选时会退化成 `rg` 字符串替换（实测 backend-dev 改名批：漏间接引用 + 误伤 SQL 字面量 42 处 + `from_responses` 残留）。**这与 §2.2 第 7 项是同一规则的两面：Lead 端要写，执行者端要用。**

## 4.2 提交协议硬规则

完整版见 `.team/notes/git-keeper/COMMIT-PROTOCOL.md`（规则条数以该文件为准，本文不写死数字——**计数型标题是「写在两处的同一个事实」，加规则时标题不会自动改**）。**最严重的 3 条**：

| 规则 | 内容 | 后果 |
|---|---|---|
| **14** | **长命令禁止管道到 `rg`/`grep`/`tail`**——必须 `> "$LOG" 2>&1` 后对文件单独 `rg` | 🔴 **全员停摆 23 分钟**：`cargo` 退出后 `rg` 仍等 stdin，`flock` 不释放，构建锁被持 23 分钟，backend-dev 日志 0 字节，批 1/批 A 全部阻塞。**规则 1–13 的后果是「提交错了」（可回滚），这条是「全员停摆」（不可回滚的时间损失）** |
| **10** | 提交前工作树必须 `cargo check --all-targets` exit 0 | `27513d7` 提交了「调用方已改、被调用方未改」的中间态 → **HEAD 编译不过** |
| **11** | 提交后必须从 HEAD 核关键符号存在 | **「工作树能编译」≠「HEAD 能编译」**——提交可能漏掉被调用方 |

**规则 1–13 与 14 的本质区别**：前者是「做错了可以改」，后者是「时间损失不可逆」。

## 4.3 构建锁

**`build-lock.sh` 串行化所有 `cargo` 操作**——多人同时编译会互相污染 `target/`。

**关键事实**：`build-lock.sh` **不设置 `CARGO_TARGET_DIR`**（`rg 'CARGO_TARGET_DIR' .team/build-lock.sh` 零命中）。隔离验证（`git worktree add`）必须在**调用方**显式传：
- **短验证**（<5 分钟）→ `CARGO_TARGET_DIR=<主仓库>/target`（省磁盘）
- **长验证**（全量 build/coverage）→ worktree 内独立 `target/`，**用完立即 `rm -rf`**
- **绝不落 `/tmp`**（红线 2：曾致全机失去 shell）

## 4.4 提交权 = 索引权

**只有 `git-keeper` 可执行写索引或改工作树状态的 git 命令**：`git add` / `git commit` / `git reset` / `git stash` / `git rm` / `git mv` / `git checkout -- <file>` / `git restore`。

**`.git/` 目录整体归 `git-keeper` 独占**（`config` / `hooks/` / `objects/` / `refs/` / `packed-refs`）——`hooks/` 会主动执行、`config` 决定 git 行为、`packed-refs` 影响提交对象，**这些不是「提交边界之外」，而是提交操作的一部分**。

**事故来源（2026-11-11 23:22:19）**：`.git/config` 被写入非法值 `core.fsmonitor = core.untrackedcache`（特征像程序化写入时变量替换错），导致 git 每次命令报 `error: cannot run core.untrackedcache`。**7 个 teammate 自查全否认，未溯源。**

## 4.5 断言必须带出处（F14）

**技术断言**（「库 X 的行为是 Y」「API 返回 Z」「工具不支持 W」）如果没有**代码坐标 / 官方文档 URL / 可复现命令**，**不得作为决策依据**。

**实例（2026-11-13）**：backend-dev 断言「sqlx 只校验 version 存在，不校验 checksum」，**无出处**。Lead 据此批准了一个 30 行的重复实现（M2 `reject_drifted_migrations`），dev-ops 实现后做反向验证，报的实际是 sqlx **原生**文案 `Error: migration 1 was previously applied but has been modified`——**报错形态反证了检查没生效**。

**正确做法**：先 `rg` 源码 / 读文档确认「轮子存不存在」，再决定造不造。**「不重复造轮子」的前提是先确认轮子不存在。**

**推论（dev-ops）**：**「不接受转述」不只意味着「不信别人的结论」，还包括「断言没给出处时，不要基于它行动」。**

## 4.6 统计口径必须分层（使用纪律第 5 条）

**统计任何东西前，先定义「分子/分母/排除项」**，并把排除项列出来。

**反例（2026-11-13）**：统计 `optional` 得 53 处，分口径后：
| 类别 | 数量 | 是我们的标识符？ |
|---|---|---|
| `sqlx::fetch_optional()` | 43 | ❌ 标准库 API |
| 注释 | 7 | ❌ |
| 测试函数名 | 3 | ❌ |
| **我们的标识符** | **2** | ✅ |

**「53 处里只有 2 处是我们的」——「所有出现」≠「我们的标识符」。**

**与「不得用出现项数冒充调用点数」的关系**：后者管**终点**（别把出现数当调用数），本条管**起点**（先定义什么算出现）。

---

# 五、事故记档

> **这一节是本文档最有价值的部分**——它记录团队真实犯过的错，每条都对应一条纪律的由来。

## 5.1 六起 git 层事故

详见 `.team/notes/git-keeper/final-ledger-2026-11-12.md` §2（`:209-374`）。**共同形态**：**「看起来正常」比「明显失败」更危险**。

## 5.2 静默失败的形态

本轮已确认的形态共六种：

| # | 实例 | 吞掉方式 |
|---|---|---|
| 1 | `stripe.rs:747` | `unwrap_or_default()` → 订单永不过期却返回 `Ok(Expired)` |
| 2 | `valid_finish` | 非 terminal 的非法值静默过滤，调用方不知有违规 |
| 3 | `schema_cleanup` | 清理失败 panic **被建议改成警告**（否决：会让残留再次无声积累） |
| 4 | dev-ops 的 `>/dev/null` | 吞掉 `must be owner` → **只清了 45/99 却报「成功」** |
| 5 | 无引号 heredoc | `\gexec` 部分前缀静默失败 |
| 6 | `xargs` 的 identifier 截断警告 | 被忽略 → **整批 50 个一个都没删** |

**super-relay 的同类死路**：`reconcile_required` 状态的预留**永不自动回收**——过期任务只扫 `status='active'`（`ledger_repository.go:337-341`），`reconcile_required` 的预留一直冻结，**而手工入口目前不存在**。这是「`needs_review` 的亲属」：两者都是「状态存在但没有自动恢复路径，也没有人工入口」——**预留挂着不是「看起来像钱」，是「真的锁住钱」**。

**判据**：**「看起来正常」比「明显失败」更危险**——后者会被修，前者会一直累积到某天以完全无关的形式爆发。

## 5.3 Lead 的两次错误

| # | 错误 | 后果 | 发现方式 |
|---|---|---|---|
| 1 | 批准 M2 时接受了一个**无出处**的断言（「sqlx 不校验 checksum」） | 30 行重复实现 | dev-ops 反向验证的**报错文案**是 sqlx 原生文案 |
| 2 | 说「3 个 gateway 测试文件 = 3 个 crate」 | 错误裁决，叫停了 tester 的正确方向 | `ls tests/*.rs` 一条命令证伪 |

**共同形态**：**接受了听起来合理但未核实的前提**（第 1 次接受别人的断言，第 2 次接受自己的推断）。

**为什么记这个**：**「Lead 也会犯同类错」比「Lead 指出了别人的错」更有价值**——它说明纪律是**对所有人**的。

## 5.4 git-keeper 的两次错误

| # | 错误 | 发现方式 | 修正 |
|---|---|---|---|
| 1 | 批 K 提交「调用方已改、被调用方未改」→ HEAD 损坏 | 用户发现 HEAD 编译不过 | 修 + 协议步骤 0.5/5 + 硬规则 10/11 |
| 2 | `--test core` 跑时没 `source deploy/test-env.sh` → 3 个测试红 | 复跑后绿 | 不是「与我无关」的推断，是「环境没配对」的事实 |

**共同教训**：**「看起来像我错了」vs「实际错了」**——第 1 条是真的错了，第 2 条看起来像错了（实际是环境问题）。**两者都需要「亲自复验」来区分。**

## 5.5 docs-writer 的错误

| # | 错误 | 形态 |
|---|---|---|
| 1 | 报 `RUNBOOK.md` 坐标 `ok=37/bad=0` | **脚本只验第一级**（行号存在 + 非空），实际 8 处 compose 坐标内容全错 |
| 2 | 报 HEAD `962e814` | **用旧记忆值**（实际已 `a6b3abe`） |
| 3 | 报 HEAD `a6b3abe` | **同类第 2 次**（实际已 `6bbefd5`） |

**第 1 条的价值**：**「工具的作者被自己的工具骗过」**——我写了「坐标三级判据」，我的脚本只跑到第一级。

**第 2、3 条的价值**：**「知道要核」≠「形成习惯」**——纠正方式不是「记住要核」，而是**把三个命令写进报告模板**（模板强制 > 记忆提醒）。

## 5.6 E2E 里手工种账务数据的三条独立代价（2026-11-14）

**结论先行**：**账务数据必须由生产代码创建，或严格照抄已验证的正确种子**（`frontend-fixes.spec.ts:165-186`）。

**试图「手工种一条 hold」会同时撞三条独立机制**：

| # | 机制 | 具体表现 | 坐标 |
|---|---|---|---|
| 1 | **`users_check` 数据库约束拒绝** | `CHECK (balance >= 0 AND held >= 0 AND held <= balance)`——种 `holds.remaining_held=1` 但 `users.balance=0` 时 `held > balance`，**数据库层面非法**。即使想「补上 `users.held`」也补不了，除非先给用户充值 | `migrations/0001_core.sql:12` |
| 2 | **`recovery_task` 报 `HeldDrift` → `settlement_gate.halted=true`** | **全局停止自动结算**，后续所有结算请求返回 503 `SETTLEMENT_HALTED` | `recovery.rs:69`（`pub struct HeldDrift`）、`:179`（查询） |
| 3 | **`assert-ledger.sql` 账务断言红** | `users.held <> SUM(holds.remaining_held WHERE status='pending')` | `tests/e2e/assert-ledger.sql` |

> **一条代价可能是偶然；三条独立机制同时拒绝，说明这条路根本不通。**

**方法学价值**：**「三条独立机制都拒绝」是比「一条报错」强得多的信号**——一条报错可能是配置问题，三条从数据库约束、后台任务、测试断言三个正交方向同时拒绝，说明这个做法**违背了系统的核心不变量**，不是「换个方式绕过」能解决的。

**这也是「不要手工造状态」的通用判据**：当一个捷径需要同时骗过约束层、任务层、断言层时，它骗过的不是系统，是自己。

---

# 六、附录：素材索引

**行数与条数一律不在本文写死**——它们每天都在变（本轮 `COMMIT-PROTOCOL.md` 从 382 行涨到 1116 行、硬规则从 14 条涨到 17 条），**而「行数」对「去哪找」这个目的零贡献**。**过时的坐标比没有坐标更糟**（读者按 382 行去翻，会翻到无关内容）。

| 文件 | 内容（**行数与条数以该文件为准**） |
|---|---|
| `.team/notes/git-keeper/final-ledger-2026-11-12.md` | 方法论总账：提交史、六起 git 事故、代码审计、假绿形态、判据 10/11/12、Lead 与 git-keeper 错误记档 |
| `.team/notes/git-keeper/COMMIT-PROTOCOL.md` | 硬规则 + 症状索引 + 判据体系（**条数与行数以该文件为准**） |
| `.team/notes/backend-dev/mistakes-2026-11-13.md` | backend-dev 的自述错误 |
| `.team/notes/design-smells/` | 代码风格审计、设计坏味道审计、`SMELL-001`、`text-enum` 评估、命名重扫、类型降级审计（**文件数以该目录为准**） |

**为什么删掉行数**：**「行数」是「写在两处的同一个事实」**——文件一改，引用处的数字就过期，而**没有任何机制会提醒你改**。这与「计数型标题」是同一个病：**唯一来源原则**——数字只应该出现在它的唯一来源（文件本身）里，引用处只写「以该文件为准」。

**测量命令**（需要时现跑，不要引用本文历史数字）：
```bash
wc -l .team/notes/git-keeper/final-ledger-2026-11-12.md \
      .team/notes/git-keeper/COMMIT-PROTOCOL.md \
      .team/notes/backend-dev/mistakes-2026-11-13.md
wc -l .team/notes/design-smells/*.md
```

---

## 版本

- **v1（2026-11-13）**：骨架 + docs-writer 自述 + 第二/四部分。其他成员自述待补。
