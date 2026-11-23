> 状态：**阶段一 5 文档合并零丢失 · 联网出处 · 全仓引用与格式 独立全量审计**
> **审计者**：`auditor`（完全只读独立审计员）
> **审计对象**：`task-51`（`task-44`..`task-50` 五文档合并链的最终独立复核）
> **基线**：`HEAD = a16335cc3bb0b8ddeb026dfee54d4cc2cb126b80`
> **审计时间**：2026-11-24
> **审计方法**：3 个并行只读子代理分治 + `auditor` 本人对**每一条结论**的第一手独立复核（不采信转述）
> **审计纪律**：全程零写仓库文件、零 git 写操作；日志仅落 `.team/notes/auditor/logs/task51/`

---

# 一、结论摘要

| 维度 | 结论 | 等级 |
|---|---|---|
| **零丢失合并（计数型契约）** | **通过**：17 表 / 76 CHECK / 41 FK 列 / 22 UNIQUE（16 全时 + 6 部分）/ 37 普通索引（25 常规 + 12 非唯一部分）逐项复算一致；4 状态机 29 态 / 36 边 / 11 终态 / 0 死路 / `I1`–`I8`；3 角色 / 9 UC / 29 FR / 29 VC；34 错误码 / 12 幂等 / 128 `data-testid`；`grading_rules` 144 = `94+34+14+2`；郑州 48h / `2880` min；`SKIP LOCKED`；`N1`–`N5`；`yolo26s.pt @ 1024` / `15 MiB` / `1..10`；`D-29`/`D-30` 全部在位 | ✅ |
| **5 文档结构门禁** | **通过**：`docs/` 根精确 5 份 + `team/{TEAM,LEAD}.md`；3 份旧文档已从工作树删除 | ✅ |
| **旧文件名零活体残留** | **通过**：`docs/` / `README.md` / `.env.example` / `.serena/` 与 6 个角色 notes 顶层文件**零活体旧引用** | ✅ |
| **链接与表格完整性** | **通过**：29 条相对/锚点链接 **0 死链 0 断锚**；`docs/` + `README.md` 全部 353 张表 **0 错列** | ✅ |
| **`DECISION.md` 独立复算** | **通过**：40 条、四档 `9/8/14/9`、**`20 ✅ / 0 🔴 / 0 🟡 / 20 🟢`**、19 项用户原话逐字保全 | ✅ |
| **Serena 记忆 / `.gitignore:47` / `.env.example`** | **通过**：4 份记忆全部同步且 `git check-ignore` 精确命中 `.gitignore:47`；`.env.example` 40 个活跃键 | ✅ |
| **合并**质量**（语义一致性）** | **不通过**：**6 个 P1 + 13 个 P2 + 5 个 P3** 实质性缺陷，其中 4 项是**本次合并新引入**的矛盾 | ❌ |
| **校验脚本** | `PASS=75 FAIL=1`（唯一 FAIL 为 `§X` 故意注入自证）——**通过，但脚本自身存在 2 处假绿盲区** | ⚠️ |
| **合计** | **P0 = 0｜P1 = 6｜P2 = 13｜P3 = 5** | — |

**核心判断**：**「零丢失」目标达成**——所有计数型契约 100% 保全、可独立复算。但**合并过程产生了新的语义矛盾与指针漂移**，其中 4 项属 `task-44` 新引入的回归：

1. **`DECISION.md` 唯一权威裁决清单内部自相矛盾**（`D-29` 正文说「直接不用」，同块标题/用户原话/状态说「直接使用」）；
2. **`README.md` 仍写「四条红线」并遗漏用户强制的 V1 红线**，而它自己引用的 `REQUIREMENTS.md §5` 已改为五项 `N1`–`N5`；
3. **`RUNBOOK.md` 用 `fault_evidences` 上不存在的列名写 `CHECK`**，且同名错误复制进了 `vision-dev` 素材；
4. **超时配置项在两个名字之间摇摆**（`YOLO_TIMEOUT_SECONDS` vs `VISION_TIMEOUT_SECONDS`），且不在 `.env.example`。

若阶段二实现者照文档直写代码，上述 4 项会直接产出错误产物或静默走默认值。**文档层面不可放行阶段二。**

---

# 二、审计范围与方法（可复现）

## 2.1 并行分治（3 个只读子代理，互不重叠）

| 子代理 | 分工 | 日志落点 |
|---|---|---|
| **Subagent 1** | **零丢失合并核验**：`DESIGN.md`/`REQUIREMENTS.md`/`API.md`/`RUNBOOK.md` 对 `HEAD:docs/{SCHEMA,TECH-STACK,BUSINESS-LOGIC,REQUIREMENTS}.md` 的逐项复算与三级坐标校验 | `.team/notes/auditor/logs/task51/sub1/`（`audit_check.{py,log}`、`verify_details.{py,log}`、`verify_level3_refs.{py,log}`、`extra_checks.{py,log}`、`idempotency_scan.log`） |
| **Subagent 2** | **全仓引用与格式**：`docs/` 结构门禁、旧名扫描、`§X.Y`/`:line` 交叉引用、链接与表格、`DECISION.md` Python 复算、`task-18-verify.sh` 执行与脚本审查、Serena/`.gitignore`/`.env.example` | `.team/notes/auditor/logs/task51/sub2/`（`item2_*`、`item3_*`、`item4_*`、`item6_*`、`verify.log`、`repo_meta_check.log`） |
| **Subagent 3** | **联网出处 MCP F14 抽验**：Context7 / Exa / GitHub raw / PyPI / npm 对 5 项精度升级与全部版本号的独立取证 | `.team/notes/auditor/logs/task51/sub3/`（`pypi_npm_verify.txt`、`pypi_npm_exact_pins.txt`、`check_ep_lines.txt`、`yolo26_release_check.txt`） |

## 2.2 Lead（`auditor` 本人）独立复核

- 全部 3 份子代理报告的**每一条结论**均由本人用 `read` / `rg` / `python3` 在**原始文件**上重新取证（日志：`lead-verify-round{2..9}.log`、`lossless-merge-audit.log`、`subitems-deep-verify.log`、`api-ep-check.log`、`final-counts.log`）。
- 关键计数用**本人独立编写的 Python 脚本**复算，不复用子代理脚本。
- **本轮纠正了子代理的 2 处计数值**：① 子代理 1 报「60 个方法+路径组合」，实测 **59**；② 子代理 1 把 `DECISION.md:340/378` 的 `FR-20`/`FR-17` 错配列为合并引入，实测**在 `HEAD:DECISION.md:323/357` 已存在**，属历史遗留而非本次回归。

## 2.3 复现命令（节选）

```bash
# 17 表 76/41/22/37 复算（本人脚本，解析 DESIGN.md §4.1 汇总表 17 行求和）
python3 - <<'PY' ... PY
# → CHECK=76 FK=41 UNIQUE=22 PLAIN_IX=37；物理建表序 1..17 全序无重复

# grading_rules 144 组合穷举
python3 - <<'PY'  # itertools.product(6 fault_type × 3 road_level × 4 asset_importance × 2 safety_flag)
# → {'R1':94,'R2':34,'R3':14,'R4':2}，Sum=144

# 4 状态机建图
# → entries=6, edges=36, numbered=42；终态 A2/B5/C3/D1=11；死路 0

# API 端点口径
# → 路由行 57；展开 (method,path) 组合 59（L136 +1、L145 +1）
```

---

# 三、P1 缺陷（6 条，均经第一手独立核验）

## P1-1　`D-29` 条目内部自相矛盾，`D-18` 同源同病 ❌【本次合并引入】

| 坐标 | 现文 |
|---|---|
| `docs/DECISION.md:507` | `## D-29 ✅ 公开数据集使用策略：个人本地项目直接使用（免除发函授权）` |
| **`docs/DECISION.md:510`** | **`当前方案`：不发函，直接不用。**……**D2–D7 全部 C 档**……**结论就是不可训练** |
| **`docs/DECISION.md:511`** | **`建议`：不发函，直接不用。**理由：……团队可以用「D1 + 自采」推进 |
| `docs/DECISION.md:515` | **用户原话**：`所有数据集不用考虑合规性，直接使用。这是我做着玩的项目，所有代码都只会在本机。不用管。` |
| `docs/DECISION.md:517-518` | **团队结论**：按用户定案，个人研究/练手项目**直接使用**公开与自采数据集，**无需发函**申请商用授权…… |
| `docs/DECISION.md:520` | **状态**：✅ **已裁定：个人本地项目直接使用，无需发函授权** |
| **`docs/DECISION.md:412`**（`D-18`） | **`当前方案`**：……唯一 A 档数据集 D1（CC-BY-4.0）只覆盖 `lamp_on/off/occluded`，**无破损类**；**D2–D7 全部 C 档不可训练**，阶段一零训练 |

**问题**：`:510`/`:511`/`:412` 是 `D-29` 裁定**之前**的旧方案文本。`task-44` 只改了标题、用户原话、团队结论与状态，**没有改写这三行**。同一 `D-xx` 块内标题说「直接使用」、正文说「直接不用」，是 `DECISION.md` 作为「**全项目唯一权威裁决清单**」的**自证矛盾**。

**为什么是 P1 而非 P2**：`docs/team/TEAM.md §5.6`、`DESIGN.md §7`、`REQUIREMENTS.md §6.2` 都把数据集边界指向 `DECISION.md`。阶段二 `vision-dev` 读 `:510` 会改回「仅 D1 + 自采」；读 `:518` 会认为 D1–D7 全可用。这正是 `task-45` 要在 `RUNBOOK.md:314` 与 `.env.example` 对齐 `D-29` 的原因——**但权威源自身没有对齐**。

**修法**（`docs-writer` 域，`auditor` 只报不修）：把 `:510`/`:511`/`:412` 改写为现行方案，或显式标注「裁定前方案，已作废」。

**独立核实**：`read docs/DECISION.md:407-431,498-535` 逐行确认；Subagent 1、Subagent 2 各自独立报出同一坐标（`:510`/`:511`），`:412` 由 Subagent 1 补充。

---

## P1-2　`RUNBOOK.md` 与 `vision-dev` 用 `fault_evidences` 上不存在的列写 `CHECK` ❌

| 坐标 | 现文 |
|---|---|
| **`docs/RUNBOOK.md:448`** | 融合置信度受后端 **`CHECK (ai_is_baseline_mode = FALSE OR ai_max_confidence <= 0.65)`**（`docs/DESIGN.md` §4.3 / §5.1）约束 |
| **`.team/notes/vision-dev/stage1-vision-spec-input.md:380`** | `fault_evidences.ai_is_baseline_mode boolean NOT NULL DEFAULT false` + **`CHECK (ai_is_baseline_mode = FALSE OR ai_max_confidence <= 0.65)`**（`docs/DESIGN.md` §4.3 / §5.3） |
| `docs/DESIGN.md:920` | `fault_evidences.ai_result_json` `jsonb` YES NULL — 单图 `FaultCandidateResult`（含 `max_fused_confidence`） |
| `docs/DESIGN.md:948` | **`ck_fault_evidences_baseline_confidence_cap`**：`ai_is_baseline_mode = FALSE OR ai_result_json IS NULL OR coalesce((ai_result_json->>'max_fused_confidence')::numeric, 0) <= 0.65` |

**问题**：`fault_evidences` **没有 `ai_max_confidence` 列**（该列在 `fault_tickets`）。两处 `CHECK` 表达式在 `fault_evidences` 上**无法编译**。且两处都遗漏了 `DESIGN.md:948` 的第 2 个分支 `ai_result_json IS NULL`（`ai_result_json` 可空，缺此分支会让合法 NULL 行判定不确定）。

**阶段二直接后果**：`dev-ops` 若照 `RUNBOOK.md:448` 写迁移断言/门禁脚本 → `column "ai_max_confidence" does not exist`；若「修」成给 `fault_evidences` 加该列 → 与 17 表 Schema 和 76 CHECK 计数全部冲突（变 77 CHECK）。

**修法**：两处改为**引用约束名** `ck_fault_evidences_baseline_confidence_cap`，不内联重写表达式。

**独立核实**：`read docs/DESIGN.md:907-950`、`docs/RUNBOOK.md:425-448`、`vision-dev:374-384` 三向对照；`rg ai_max_confidence docs/` 仅 `fault_tickets` 命中。Subagent 1 与本人各自独立定位。

---

## P1-3　超时配置项在两个名字之间摇摆，且不在 `.env.example` ❌【本次合并引入】

| 坐标 | 现文 |
|---|---|
| **`docs/DESIGN.md:131`** | `视觉配置`：`YOLO_INFERENCE_ENABLED`；`YOLO_INFERENCE_MODE`；`YOLO_IMG_SIZE=1024`；**`YOLO_TIMEOUT_SECONDS=60`** |
| **`docs/DESIGN.md:1117`** | 单次推理超过 **`VISION_TIMEOUT_SECONDS`**（默认 `60`s）→ `503 VISION_TIMEOUT` |
| **`docs/API.md:215`** | `503` \| `VISION_TIMEOUT` \| 单次推理 > **`VISION_TIMEOUT_SECONDS`**（默认 60） |
| `.team/notes/backend-dev/stage1-backend-spec-api.md:173,453` | 单次推理超过 **`VISION_TIMEOUT_SECONDS`**（默认 `60`，**代码常量**） |
| `.env.example`（40 键） | **零命中 `TIMEOUT`** |

**问题**：`DESIGN.md:131` 位于「**视觉配置**（环境变量）」表行，把该变量呈现为 env var 且命名为 `YOLO_TIMEOUT_SECONDS`；而 `DESIGN.md:1117`、`API.md:215`、`backend-dev` 三处一致用 `VISION_TIMEOUT_SECONDS`，`backend-dev` 还明标「**代码常量**」。

两种解读都有害：
- 若它是 env var → 名字错，实现读 `YOLO_TIMEOUT_SECONDS` 永远拿不到值，静默用默认 60；
- 若它是代码常量 → `DESIGN.md:131` 不应把它列进「视觉配置」env 表行。

**判定**：`backend-dev` 的「代码常量」+ `.env.example` 无此键 + `API.md` 用 `VISION_` 前缀，三票指向「**是代码常量，不是 env var**」。则 `DESIGN.md:131` 既错名又错类。**必须统一**：`VISION_TIMEOUT_SECONDS` 定为代码常量并移出 env 表行，或补进 `.env.example` 并统一命名。

**独立核实**：`rg 'YOLO_TIMEOUT_SECONDS|VISION_TIMEOUT_SECONDS' docs/ .env.example .team/notes/backend-dev/`；`rg TIMEOUT .env.example` → 零命中。Subagent 1 报告，本人独立确认。

---

## P1-4　「两阶段幂等」与「`audit_logs` 只插不改」直接冲突 ❌【本次合并引入】

| 坐标 | 现文 |
|---|---|
| **`docs/DESIGN.md:734`** | **两阶段幂等键基础设施**（`audit_logs` 承载，12 个必需 `POST` 端点） |
| **`docs/DESIGN.md:736`** | 同 `(actor_account_id, method, path, idempotency_key)` **存在进行中记录/行锁占位** → `409 IDEMPOTENCY_REQUEST_IN_PROGRESS` |
| **`docs/DESIGN.md:963`** | `audit_logs`（**只插不改**审计 + 幂等存储）……无 `updated_at` |
| `docs/DESIGN.md:963` | `uq_audit_logs_idempotency` ON `(actor_account_id, method, path, idempotency_key)` **`WHERE idempotency_key IS NOT NULL AND response_status >= 200 AND response_status < 300`** |
| `.team/notes/backend-dev/stage1-backend-spec-input.md:43` | 合并后重放与审计共用一行，并发唯一性由**同一个索引**保证，**不需两阶段写入** |
| `.team/notes/backend-dev/stage1-backend-spec-tables.md:73` | 同上；`:1028-1034` 明确「**只插入，不更新不删除**（无 `updated_at`，也无 `deleted_at`）」 |

**问题（四重矛盾）**：
1. **「两阶段/行锁占位」需要先插一行状态为「进行中」的记录，事后 UPDATE 成「已完成」**——但 `audit_logs` **只插不改、无 `updated_at`**，物理上做不到；
2. **`uq_audit_logs_idempotency` 的谓词是 `response_status >= 200 AND response_status < 300`**。占位行的 `response_status` 要么 `NULL`、要么非 2xx，**两种都不被该部分唯一索引覆盖**——即占位行根本没有唯一性保护，同键并发会插入多行占位，「行锁占位」承诺不成立；
3. `backend-dev` 两处**显式声明「不需两阶段写入」**；
4. 但 `DESIGN.md:737` 又说「失败不占用幂等键」「只有 2xx 写索引」——这与 `:736` 的「占位」直接打架。

**阶段二直接后果**：`backend-dev` 无法按 `DESIGN.md:734-737` 实现；两个并发同键请求的 `409 IDEMPOTENCY_REQUEST_IN_PROGRESS` 分支会**永远不触发**（因为没有可观测的「进行中」行），最终只能落到 2xx 后的唯一索引冲突，返回 `409` 之外的码 —— 与 `API.md §6.2` 的五条落地规则不符。

**修法**：删除「两阶段/占位」表述，改为**事务级 `pg_advisory_xact_lock`** 或在 `API.md §6.5` 已声明的「并发唯一不变量」框架内表述；`audit_logs` 保持 append-only。**这是设计决策，须 `Lead` 裁决，不属 `auditor` 域。**

**独立核实**：`read docs/DESIGN.md:730-742,960-965`、`backend-dev input.md:40-46`、`tables.md:70-76,1026-1034`；`idempotency_scan.log` 佐证。Subagent 1 报告，本人独立确认四重矛盾全部成立。

---

## P1-5　循环外键的**约束名**与**验收行数**在 `DESIGN.md` 与 `backend-dev` 之间冲突 ❌

| 坐标 | 现文 |
|---|---|
| `docs/DESIGN.md:987` | 迁移层：先建 `rework_orders`（14，列先不带 FK），再建 `acceptance_records`（15，列先不带 FK），两表建成后**显式调用两次 `op.create_foreign_key()`** 补齐双向 FK |
| `docs/DESIGN.md:994-995` | `WHERE conname IN ('fk_rework_orders_source_acceptance', 'fk_acceptance_records_rework_order')` **`-- 期望：恰好 2 行`** |
| **`.team/notes/backend-dev/stage1-backend-spec-tables.md:53-62`** | 仅创建**一个** FK：`op.create_foreign_key("fk_rework_orders_source_acceptance_id_acceptance_records", "rework_orders", "acceptance_records", ["source_acceptance_id"], ["id"], ondelete="RESTRICT")`；`acceptance_records.rework_order_id` **随建表 inline 创建** |
| **`.team/notes/backend-dev/stage1-backend-spec-tables.md:66-68`** | `SELECT count(*) ... WHERE conname = 'fk_rework_orders_source_acceptance_id_acceptance_records'` **`-- 必须 = 1（不是 0，也不是 2）`** |

**问题**：两套方案**技术上都能跑通**（方案 A：两次 ALTER；方案 B：一次 inline + 一次 ALTER），但产出的：
- **约束名不同**：`fk_rework_orders_source_acceptance` vs `fk_rework_orders_source_acceptance_id_acceptance_records`；
- **验收断言互斥**：`DESIGN.md` 要求 `count = 2`（按名字 IN 两条查）；`backend-dev` 要求 `count = 1`（且**明确写「不是 2」**）。

若 `backend-dev` 按自己的 `tables.md` 实现并用 `DESIGN.md:990-996` 的 SQL 验收 → 查出 **1 行**，被判「期望 2 行」失败；反之亦然。**两个权威文档给出互斥的验收判据，这是 P1。**

**额外风险**：`DESIGN.md §1.2` 的 `MetaData(naming_convention=...)` 若含 `fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s`，则方案 A 的裸名 `fk_rework_orders_source_acceptance` 与方案 B 的长名都可能被 naming convention **改写**，实际落库名第三个样——`pg_constraint` 断言须以实测名为准。

**修法**：`Lead` 裁决采用方案 A 或 B 之一，然后把 `DESIGN.md:987-996` 与 `backend-dev/tables.md:48-68` **同步成同一套名字与同一套断言**。

**独立核实**：`read docs/DESIGN.md:979-996`、`sed -n '48,70p' backend-dev/stage1-backend-spec-tables.md` 双向对照。Subagent 1 报告，本人独立确认互斥性。

---

## P1-6　`README.md` 仍写「四条红线」，**遗漏用户强制的 V1 红线** ❌【本次合并引入】

| 坐标 | 现文 |
|---|---|
| **`README.md:24`** | `docs/REQUIREMENTS.md`：3 个角色 / 9 个核心用例 / 29 条功能需求 + 29 条可验证判据 / 追溯矩阵 / **四类「不做」清单**与郑州 48h 法定基线 |
| **`README.md:61`** | **用户明确要求的四条红线**（`docs/REQUIREMENTS.md` §5，不可扩展）： |
| `README.md:63-66` | 4 条：不接灯控设备 / 不做能耗预测 / 不接实时监控 / AI 不替代电气安全检查 |
| `docs/REQUIREMENTS.md:210` | `## 5. **五项核心红线（N1–N5，明确「不做」，不可扩展）**` |
| `docs/REQUIREMENTS.md:214-218` | **5 条**：`N1` 不接实时监控 / `N2` 不做远程灯控 / `N3` 不做能耗预测 / **`N4` 不跳过巡查员确认（`V1`）** / **`N5` 不跳过现场电气安全检查（`V2`）** |

**问题（两层）**：
1. **数量矛盾**：`README.md:61` 写「四条」并**自引 `REQUIREMENTS.md §5`**（现为五项）——自引即自证矛盾；
2. **内容遗漏**：README 的 4 条对应旧 `HEAD:REQUIREMENTS.md` 的 `N1`–`N4` 编号；合并后 `N4` 已变为「**不跳过巡查员确认（`V1`）**」，对应用户原话「**维修工单须经巡查员确认**」。**README 完全没有这一条**——而它是用户四条原话要求之一（`V1` 三层强制：前端 `disabled` + `422 AI_CANDIDATE_NOT_CONFIRMED` + DB 三态串行化 `CHECK`）。

**为什么是 P1**：`README.md` 是仓库首页、第一份被读的文档。它把一个**用户明确要求、不可扩展**的红线从「边界」章节里整个漏掉，且数量与所引章节不符。用户看首页会误以为只有 4 条红线。

**已排除「历史遗留」可能**：`git diff HEAD -- README.md` 实测本次改动**只碰了 `:24` 那一行表格（追加「与郑州 48h 法定基线」）**，`:61-66` 的「边界」章节**一个字节没动**——即 `REQUIREMENTS.md` 从 4 条扩到 5 条时，`README.md` 未被同步。**属本次合并链回归。**

**修法**：`README.md:24` 改「五类「不做」清单」；`:61` 改「五条红线」并按 `REQUIREMENTS.md:214-218` 的 `N1`–`N5` 重列 5 条（或直接改为「见 `docs/REQUIREMENTS.md` §5」的单行指针，符合 `TEAM.md §5.6` 唯一来源原则）。

**独立核实**：`git show HEAD:README.md`、`git show HEAD:docs/REQUIREMENTS.md`（§5 为 4 条 `N1`–`N4`）、`sed -n '20,32p;56,70p' README.md`、`sed -n '210,222p' docs/REQUIREMENTS.md`、`git diff HEAD -- README.md`。Subagent 1 报告，本人独立确认且补全「遗漏 V1」证据。

---

# 四、P2 缺陷（13 条）

## P2-1　`REQUIREMENTS.md` §8 验收映射表两处章节号**互换**

| 坐标 | 现文 | 实际 |
|---|---|---|
| `docs/REQUIREMENTS.md:314` | 「重复提交」→ `§3.1 幂等与点位防重` | `DESIGN.md:467` `§3.1` = 「跨表不变量（I1–I8）」；幂等与点位防重在 **`§3.5`**（`DESIGN.md:732`） |
| `docs/REQUIREMENTS.md:315` | 「至少一种异常恢复场景」→ `§3.5 视觉降级`、`§5.2` | `§3.5` = 幂等；视觉降级 `D1–D3` 在 **`§5.2`**（`DESIGN.md:1096`） |

两行**恰好互换**，是 `task-44` 重排后的典型残留。同类问题在 `§8` 表其余 5 行**全部正确**（已逐行核验 `§3.2`/`§3.3`/`§3.4`/`§2.3`/`§2.1–§2.4`）。

---

## P2-2　`vision-dev` §4.5 对齐表已过期且漏 `YOLO_AUTOINSTALL`

| 坐标 | 现文 | 实际 |
|---|---|---|
| `vision-dev:463` | `🟡 ...远端工作/数据/输出目录待 dev-ops task-27 统一切至 /root/autodl-tmp/tomori/` | `.env.example:85-89` 已全部为 `/root/autodl-tmp/tomori/{repo,datasets,runs,venv,weights}`，`task-27` 早已完成 |
| `vision-dev:458` | 「注释中旧多档/高分辨率遗留描述归 `dev-ops` `task-27` 清理」 | 已清理完毕 |
| `vision-dev` §4.5 表（`:456-463`） | **无 `YOLO_AUTOINSTALL` 行** | `.env.example:73` 已有 `YOLO_AUTOINSTALL=False`（`task-45` 新增，第 40 键）；`vision-dev:111,254,259,432,476` 正文均已引用该变量 |

**性质**：`vision-dev` 素材是 `DESIGN.md §5` 的上游来源之一，留「🟡 待同步」会让人误判远端目录未定案；§4.5 表号称「对齐状态」却缺最关键的新增键。

---

## P2-3　`DESIGN.md` `vision` extra 缺 `onnx`/`onnxslim`，且版本 pin 策略与 `RUNBOOK` 不一致

| 坐标 | 现文 |
|---|---|
| `docs/DESIGN.md:196-200` | `vision = ["ultralytics>=8.4.216", "opencv-python-headless>=4.10.0", "onnxruntime>=1.20.0,<2.0.0"]` |
| `docs/RUNBOOK.md:297` | 「若导出 ONNX 时启用了 `simplify=True`（Ultralytics 默认开启）且 `YOLO_AUTOINSTALL=False` 阻止静默装包，**须提前在 `pyproject.toml`（或 dev/vision 依赖）中显式包含 `onnx`、`onnxslim` 等导出所需包**」 |
| `docs/RUNBOOK.md:296` | 以 `ultralytics==8.4.216`（`==`）表述 |
| `docs/DESIGN.md:170,1193` | 实测 `opencv-python-headless` = `5.1.0.95` |

**一手出处（Subagent 3 取证，本人复核结论）**：`ultralytics v8.4.216` 的 `pyproject.toml` 把 `onnx`/`onnxslim` 放在 `[export-base]` extra，**不在核心依赖**；`exporter.py:1211-1222` 在 `simplify=True` 时通过 `check_requirements()` 请求安装，被 `YOLO_AUTOINSTALL=False`（`utils/__init__.py`：`AUTOINSTALL = env_bool("YOLO_AUTOINSTALL", True)`）禁用后**直接报错**。

**问题**：`RUNBOOK` 说「必须显式包含」，但**唯一权威的 `pyproject.toml` 骨架（`DESIGN.md:196-200`）没有列出**。另：`DESIGN.md` 用 `>=8.4.216`/`>=4.10.0`，`RUNBOOK` 用 `==8.4.216`，`DESIGN.md:170` 实测 `5.1.0.95` —— 三套 pin 口径。`ultralytics` 已于 `2026-11-23` 发布 `8.4.217`，`>=` 会解析到 `8.4.217`（与「锁定 `8.4.216`」的意图不符）。

**修法**：`DESIGN.md` extra 补 `onnx`/`onnxslim`（或明确写「见 `RUNBOOK §6.3`」），并统一 `ultralytics` pin 为 `==8.4.216`（或明标「由 `uv.lock` 定版」）。

---

## P2-4　`<el-teleport>` 标签在 Element Plus `2.14.8` 中**不存在**（3 处误引）

| 坐标 | 现文 |
|---|---|
| `docs/DESIGN.md:280` | 「对 **`<el-teleport>`** 默认挂载至 `document.body`（`#el-popper-container-*`）的下拉/浮层…」 |
| `docs/API.md:598` | 「下拉浮层经 **`<el-teleport>`** 挂 `body` 时…」 |
| `.team/notes/research/merge-online-cross-check.md:85` | 声称 `tooltip/src/content.vue` 用 **`<el-teleport :disabled="!teleported" :to="appendTo">`** |
| `.team/notes/research/merge-online-cross-check.md:114` | 同上 |

**一手出处（Subagent 3 取证）**：Element Plus PR `#23219`（`2.14.0` 发布）**已彻底移除 `el-teleport`**；`2.14.8` 的 `tooltip/src/content.vue:2` 与 `dialog/src/dialog.vue:2` 用的是 Vue 原生 **`<teleport>`**。

**性质**：结论（`page.get_by_test_id()` 可整页穿透 Teleport）**正确**，但**标签名不存在**——`F14` 类问题：断言附了源码坐标，坐标指向的符号却在目标版本中已删除。阶段二 `frontend-dev` 照抄 `<el-teleport>` 会得到未知组件。

---

## P2-5　`API.md` §8.5 关于 `el-option` 的两段自相矛盾

| 坐标 | 现文 |
|---|---|
| `docs/API.md:599` | `el-option`：🟡 **源码已核单根 `<li>`，待 Chromium 首验** …… `2.14.8` `option.vue` 未设 `inheritAttrs: false` |
| **`docs/API.md:604`** | 「`el-option` 的 `disabled`/`data-eligible`/`data-reason`/`data-testid` 全依赖「属性透传到选项 DOM」这个**未被举证的假设**。」 |
| `docs/DESIGN.md:282` | 「`element-plus@2.14.8` `select/src/option.vue` 经核实为单根 `<li role="option">`（未设 `inheritAttrs: false`），按 Vue 3 规则可承接 `data-testid`」 |

`:604` 说「未被举证」，`:599` 与 `DESIGN.md:282` 已给出源码级举证。「**待 Chromium 首验**」（运行时未验）与「**未被举证**」（源码未查）是两件事，`API.md` 混为一谈。

---

## P2-6　`API.md` 三处内部章节指针错位

| 坐标 | 现文 | 实际 |
|---|---|---|
| `docs/API.md:13` | `## 0. 统计口径分层声明（docs/team/TEAM.md **§4.6**）` | `TEAM.md:331` `§4.6` = 「本项目专属的变异卡」；「统计口径必须分层」在 **`§5.3`**（`TEAM.md:387`）。`DESIGN.md:12` 与 `REQUIREMENTS.md:12` **均已写 `§5.3`**，仅 `API.md` 漏改 |
| `docs/API.md:19` | `幂等必需端点 \| 12（见 **§6.3**）` | `§6.3`（`:346`）=「两处项目自定义选择」；12 端点清单在 **`§6.4`**（`:362`） |
| `docs/API.md:20`、`:491` | `121（**§10.2** 冻结表去重）` | `API.md §10`（`:636`）=「待用户裁决清单」，**全文无 `§10.2`**；`§10.2` 是 `frontend-dev` 素材的内部章节号 |

---

## P2-7　`API.md:103` 载荷校验指向错误的 `DESIGN.md` 章节

`docs/API.md:103`：「…（权威清单与载荷校验见 §7.4 与 `docs/DESIGN.md` **§4.4 ⑤**）」。

- `DESIGN.md:961`（`§4.4 ⑤`）= `business_rules` 的列清单 + 2 条 CHECK，**只列 5 个 `rule_code` 名**；
- **Pydantic 载荷校验规则**在 **`DESIGN.md §3.3`**（`:596-606`，逐 `rule_code` 的强制校验表）；
- `API.md §7.4`（`:419`）方向正确，只有 `DESIGN.md` 那半句错。

---

## P2-8　`DECISION.md:618` 指向不含该内容的 `DESIGN.md §8`

`docs/DECISION.md:618`：「…详见附录 B 及 `docs/DESIGN.md` **§3.2 / §8** / `docs/API.md` §2.2」。

实测 `DESIGN.md §8`（`:1246-1279`）**全文零命中** `U-4` / `dispatch_scoring_weights` / 派单权重；该内容只在 **`§3.2`**（`:534-538`）。`§8` 是 `task-44` 重写编号后新增的**空指针**。

---

## P2-9　`RUNBOOK` + `dev-ops` 将 `503 VISION_DISABLED` / `413` 指向错误的 `API.md` 子节

| 坐标 | 现文 | 实际 |
|---|---|---|
| `docs/RUNBOOK.md:432` | 权重门禁 → `docs/API.md` **`§3.4 / §5.2`** | `§3.4`（`:206`）正确；**`§5.2`（`:311`）是「零候选为什么仍落库」，不含 503** |
| `docs/RUNBOOK.md:446` | `503 VISION_DISABLED`（`docs/API.md` **`§3.4、§5.2`**） | 同上 |
| `.team/notes/dev-ops/stage1-infra-facts.md:82` | 同上 `§3.4 / §5.2` | 同上 |
| **`docs/RUNBOOK.md:72`** | `413 IMAGE_SIZE_EXCEEDED` 与 `503 VISION_*` 降级契约（**`§3.4–§3.5、§5`**） | `413 IMAGE_SIZE_EXCEEDED` 在 **`§3.3`（`:201`）**，不在 `§3.4–§3.5` |
| `.team/notes/dev-ops/stage1-infra-facts.md:79` | `MAX_UPLOAD_SIZE_MB=15` → `docs/API.md` **`§3.4`** | 应在 **`§3.3`（`:201`）** |

`docs/API.md §5.2` 的实际标题是「零候选为什么仍落库（`ticket_id = NULL` 的唯一合法用途）」，与视觉降级/权重门禁无关。视觉降级契约在 **`DESIGN.md §5.2`** —— 疑似把 `DESIGN` 的节号误抄给了 `API`。

---

## P2-10　`API.md:358` IETF 引文归因错误

`docs/API.md:358`：「草案 **§2.6** 把「错误是否算 completed」列为开放问题（*"Open: do errors count as 'completed'? Practice differs; #48 suggests a retry after a 4xx is processed as new"*）」。

Subagent 3 独立核对：该英文原文**不在** `draft-08` §2.6，逐字来自 **IETF 127 HTTPAPI WG 幻灯片**（`slides-127-httpapi-the-idempotency-key-http-header-field-00`）。`API.md:350` 引 IETF 127 材料时归因正确，`:358` 却把它算到草案 §2.6 头上。

**性质**：结论（4xx/5xx 不占键）不受影响，但**出处坐标错**——`F14` 归因缺陷。

---

## P2-11　`DESIGN.md` 内部 Context7 复现次数前后不一致，且「不可用」结论与实测冲突

| 坐标 | 现文 |
|---|---|
| `docs/DESIGN.md:289`（`§1.4`） | 网络层故障（`docs-writer` 与 `task-44` 联网核验**累计复现 10 次**） |
| `docs/DESIGN.md:1252`（`§8.1`） | Context7 MCP 通道两种不可用故障形态（**4 次** `TypeError: fetch failed` + 2 次 Vue 2 索引陈旧） |
| `docs/DESIGN.md:288` | 🔴 **Context7 MCP 在本环境不可用**（绝对化） |

**本轮实测（Subagent 3 原始输出）**：`SQLAlchemy`→`/sqlalchemy/sqlalchemy`、`Ultralytics`→`/ultralytics/ultralytics`、`Alembic`→`/websites/alembic_sqlalchemy_en`、`OpenCV`→`/opencv/opencv`、`Element Plus`→`/element-plus/element-plus`、`Playwright Python`→`/websites/playwright_dev_python` —— **6 个库 resolve + query 全部成功**；仅 `Vue` 与 `FastAPI` 两次 `TypeError: fetch failed`。本人亦独立复现同现象（`resolve-library-id("SQLAlchemy")` → 成功；`query-docs` → 成功；`resolve-library-id("SQLAlchemy")` 另一次 → `fetch failed`）。

**问题**：① 同一文档两处数字不一致（`10` vs `4`）；② `§1.4` 的「**不可用**」是绝对化结论，与本轮 6/8 成功不符。**建议改口径为「不稳定（间歇 `fetch failed`），不可作为唯一取证通道」并追加本次实测记录**——这既保住 `F14` 诚实性，也不误导后续成员完全放弃该通道。

> 本条含 `【需独立核实（由 Lead 复核）】` 的旁支：「不可用」是否需下调属 `docs-writer` 判断，本审计只报事实（**6 成功 / 2 失败 / 同一库不同次结果不一致**）。

---

## P2-12　「57 端点」实为**路由行数**，展开后是 **59 个** `(method, path)` 组合

| 项 | 数值 |
|---|---|
| `docs/API.md §2` 表格路由行 | **57**（`12+12+14+8+11`） |
| 展开多 method / 多 path 合并行后 | **59**（`L136` `GET`/`PATCH`×2 路径 → +1；`L145` `GET`×2 路径 → +1） |
| `docs/API.md:17` 口径声明 | 「REST 端点数 \| **57**（见 §2 逐条） \| 本文件 §2 全部**路由行**」 |

**问题**：`§0` 表头写「REST **端点**数」，分子定义却是「**路由行**」——两个概念差 2。`API.md §0` 自身是**内部自洽**的（明文写「路由行」），但 `README.md:27`、`DESIGN.md:1266`、`backend-dev/input.md:504` 都转述为「**57 端点**」。阶段二若按「57 个端点已实现」验收，实际会有 59 条路由。

**修法**（低成本、高收益）：`§0` 表头改「REST **路由行**数」，或补一行注「57 行含 2 条合并行，展开为 59 个 `(method, path)`」。**不建议改数字**——57 有历史锚点价值，加口径注即可。

---

## P2-13　`DECISION.md` 两处 `FR` 编号错配（**历史遗留，非本次回归**）

| 坐标 | 现文 | 实际 |
|---|---|---|
| `docs/DECISION.md:340`（`D-14` `max_rework_rounds`） | 影响什么：`business_rules['rework_policy']`、**`FR-20`**、`MUT-06b` | `FR-20`（`REQUIREMENTS.md:138`）= **点位防重**；返修是 **`FR-26`/`FR-27`**（`:154-155`） |
| `docs/DECISION.md:378`（`D-16` `max_active_dispatch`） | 影响什么：`accounts.max_active_dispatch`、**`FR-17`**、派单推荐过滤逻辑 | `FR-17`（`:130`）= **故障分级 + 48h SLA**；区域派工与 `max_active_dispatch` 是 **`FR-21`**（`:139`） |

**归因诚实声明**：实测 **`HEAD:DECISION.md:323`（`FR-20`）与 `:357`（`FR-17`）已存在同样错配** —— 这两条是**历史遗留缺陷**，`task-44` 未修也未引入。仍应列入待修清单（`DECISION.md` 是唯一权威清单，`FR` 交叉引用错会误导 `tester` 设计用例）。

---

# 五、P3 缺陷（5 条）

## P3-1　`.team/notes` 两处 Markdown 表格裸竖线错列（不影响 `docs/`）

| 坐标 | 问题 |
|---|---|
| `.team/notes/backend-dev/stage1-backend-spec-api.md:287` | 表头 5 列，该行因 `` `{decision: "APPROVED"\|"REJECTED", ...}` `` 内未转义 `\|` 拆成 **6 列** |
| `.team/notes/dev-ops/remote-gpu-probe.md:13` | 表头 3 列，该行因 `` torch 2.8.0+cu128 \| cuda_available True \| ... `` 含 3 个未转义 `\|` 拆成 **6 列** |

`docs/**/*.md` 与 `README.md` **0 错列**（353 张表全扫，含表头/分隔符/全部数据行/行内代码）。

---

## P3-2　`task-18-verify.sh` 的两处假绿盲区

| 位置 | 盲区 |
|---|---|
| 脚本 `L345` | 表格列数校验仅比较**表头行与分隔符行**（`re.match(r"^\|[:\-\s\|]+\|$", l)`），**数据行恒 `True`**。若 `docs/` 数据行出现未转义 `\|`，脚本仍恒绿 |
| 脚本 `L297`（`§8`） | `if t.startswith(('http','#','mailto')): continue` **跳过全部同文件 `#anchor` 链接**；不校验 `path#anchor` 锚点存在性；不跳过围栏代码块；不扫 `.team/notes/` |
| 脚本 `§3` | 旧名扫描只覆盖 `docs/**/*.md` + `README.md`，不含 `.env.example` / `.team/notes/` / `.serena/memories/` |

**说明**：本轮已用独立 Python 脚本补扫三处盲区，`docs/` 与 `README.md` 结论仍为 **0 错列、0 死链**——即**盲区确实存在，但本次未掩盖缺陷**。建议修 `L345`/`L297`，否则下轮审计仍必须靠人工补扫。

---

## P3-3　`.team/notes` 内大量 `TEAM.md` 旧章节号引用

`docs/team/TEAM.md` 现有 `§1.1`–`§1.3` / `§2.1`–`§2.6` / `§3.1`–`§3.5` / `§4.1`–`§4.6` / `§5.1`–`§5.6` / `§6.1`–`§6.7` / `§7.1`–`§7.3`，**不存在 `§1.5`、`§1.6`、`§1.8`、`§2.7`**。以下 notes 仍引用不存在小节：

- `backend-dev`：`input.md:5` `§2.7`；`input.md:25,75` 与 `tables.md:1210` 用 `§4.6` 指「统计口径」（现 `§4.6` 是变异卡，统计口径在 `§5.3`）
- `frontend-dev`：`input.md:108` `§2.7`；`input.md:837` `§1.5`
- `tester`：`input.md:3,6,12,433,557` `§1.6`；`:433` 另含 `§1.8`；`:3` 含 `§2.7`
- 另有 `§3.1`/`§3.3`/`§4.2`/`§4.5`/`§5.2`/`§5.6` 等旧节号指向**现已不同**的内容（例：notes 用 `§3.1` 指「假绿八形态」，现 `§3.1` 是「任务板」，假绿八形态已移至 `§4.2`）

**性质**：notes 是历史素材、不入库（`.gitignore:45`），不影响 `docs/` 交付。**不建议现在批量改**（改动面大、收益低）；建议阶段二开工时以 `docs/team/TEAM.md` 现文为准。

---

## P3-4　`DESIGN.md` 三处精度瑕疵

| 坐标 | 现文 | 说明 |
|---|---|---|
| `docs/DESIGN.md:963` | `ck_audit_logs_payload_hash_format` 摘要为 `request_payload_hash ~ '^[0-9a-f]{64}$'` | `backend-dev/tables.md:1071` 实为 `request_payload_hash **IS NULL OR** request_payload_hash ~ '^[0-9a-f]{64}$'`；摘要漏 `IS NULL OR` 分支 |
| `docs/DESIGN.md:328`（`A15`） | 返修单置 `CLOSED_BY_REVIEW` | 未限定「**若存在活跃返修单**」；`MANUAL_REVIEW` 可由 `A8`（累计拒单）或 `A10`（升级）进入而无 `rework_orders` 行，此时该动作无对象 |
| `docs/DESIGN.md:719` | 三出口描述同上 | 同缺限定语 |

---

## P3-5　`vision-dev` / `RUNBOOK` 导出参数与权重目录轻微分歧

| 坐标 | 现文 |
|---|---|
| `vision-dev:254` | `export(..., **opset=17**, simplify=True, dynamic=False)` |
| `docs/RUNBOOK.md:391-397` | `yolo export ... imgsz=1024 nms=False dynamic=False` —— **无 `opset`**（Ultralytics 默认 18） |
| `vision-dev:268-271` | 权重目录 `vision/weights/**yolo26s-streetlight-v1.0/**{best.pt,best_e2e.onnx,...}`（**嵌套一层**） |
| `.env.example:51`、`RUNBOOK.md:416-417,428` | `./vision/weights/best.pt`、`./vision/weights/best_e2e.onnx`（**扁平**） |

`opset=17` vs 默认 18 会让两侧产出的 ONNX 图版本不同（onnxruntime 1.32 两者均支持，故仅为不一致而非故障）；权重目录嵌套 vs 扁平会让 `YOLO_WEIGHTS_PATH` 与实际落点对不上。另 `.team/notes/backend-dev/stage1-backend-spec-input.md:503` 的 `package.md §5` 映射行只写 `RUNBOOK §4 / §6`，**漏 `§3`**（`:47-75` 环境变量表，`MAX_UPLOAD_SIZE_MB=15` 在 `:63`）。

---

# 六、零丢失合并逐项对账（PASS 明细）

## 6.1 Schema 计数（`DESIGN.md §4` vs `HEAD:docs/SCHEMA.md`）

| 项 | 期望（`HEAD`） | 实测（`DESIGN.md`） | 结果 |
|---|---:|---:|---|
| 业务表 | 17（排除 `alembic_version`） | 17（圈号 ①–⑰ + 物理序 1–17） | ✅ |
| `CHECK` 合计 | 76 | **76**（`4+1+0+2+2+7+4+2+6+4+14+8+7+4+3+3+5`） | ✅ |
| 外键列合计 | 41 | **41**（`1+0+3+3+1+4+1+1+1+1+6+2+5+3+2+4+3`） | ✅ |
| `UNIQUE` 合计 | 22 | **22** = **16 全时 + 6 部分唯一** | ✅ |
| 普通索引合计 | 37 | **37** = **25 常规 + 12 非唯一部分** | ✅ |
| 6 部分唯一索引名单 | — | `uq_scope_authorizations_account_region_type`、`uq_business_rules_code_active`、`uq_fault_tickets_active_point_type`、`uq_dispatch_records_active_ticket`、`uq_rework_orders_active_ticket`、`uq_audit_logs_idempotency` | ✅ |
| 12 非唯一部分索引 | — | `ix_maintenance_regions_is_active`、`ix_accounts_status`、`ix_scope_authorizations_account_id_active`、`ix_fault_tickets_{manual_review,pending_candidates,pending_dispatch,sla_scan}`、`ix_dispatch_records_technician_active`、`ix_audit_logs_ticket_id`、`ix_notification_tasks_{dead_letter,pending_due,recipient_unread}` | ✅ |
| `fault_tickets` 列数 | 33 | 33（与 `tables.md §9` 集合差 `set()`） | ✅ |
| `fault_tickets` / `fault_evidences` CHECK | 14 / 8 | 14 / 8 | ✅ |
| 四张被合并候选表 | — | `idempotency_keys`→`audit_logs`；`sessions`/`refresh_tokens` 不建表；`technician_skills`→`accounts.skills`；`sla_alerts`→`fault_tickets`+`notification_tasks` | ✅ |
| `notification_tasks.event_type` | 19 种 | **19** | ✅ |
| 5 条迁移期断言 | 5 | 5（`DESIGN.md:998-1033`；断言 2 相对 `HEAD` 有**改进**：去掉 `s.is_active=true` 以避免历史授权软停用后误报） | ✅ |

> **口径声明**：以上为 **DDL 文本计数**，非 `information_schema` 实测值；`DESIGN.md:1260` 已如实声明此边界，审计认可。

## 6.2 状态机（`DESIGN.md §2`）

| 项 | 期望 | 实测 | 结果 |
|---|---:|---:|---|
| 状态机 | 4 | 4（A 工单 / B 派工 / C 返修 / D 通知） | ✅ |
| 状态总数 | 29 | 29（A9+B7+C8+D5） | ✅ |
| 合法边 | 36 | **36**（A15+B6+C8+D7） | ✅ |
| 编号行 | 42 | 42（6 入口 + 36 边） | ✅ |
| 终态 | 11 | **11**（A2+B5+C3+D1） | ✅ |
| 死路 | 0 | 0（四机 BFS 建图验证，含 `PROCESSING` 10 分钟自愈与 `SENT` 终态理由） | ✅ |
| `I1`–`I8` | 8 | 8（`DESIGN.md:471-478`） | ✅ |
| 黑名单 | `IN_REPAIR → REWORK_PENDING_DISPATCH` 禁 | 在位，含 `MUT-09` 守卫理由（`:346-361`） | ✅ |
| 7 步驳回事务 | 7 步 | 7 步（`:656-718`），含**第 6 步循环外键回填** `acceptance.rework_order_id = rework.id`（修掉了 `HEAD:BUSINESS-LOGIC.md:507` 的缺步） | ✅ |

## 6.3 需求与接口

| 项 | 期望 | 实测 | 结果 |
|---|---:|---:|---|
| 角色 | 3 | 3 | ✅ |
| UC | 9 | 9（`UC-01`–`UC-09`） | ✅ |
| FR | 29 | 29（唯一；追溯矩阵亦 29） | ✅ |
| VC | 29 | 29（1:1 绑定） | ✅ |
| 路由行 | 57 | 57 | ✅（口径注见 P2-12） |
| `error.code` | 34 | **34**（9+12+6+7，全部唯一） | ✅ |
| 幂等端点 | 12 | **12**（`§6.4` 逐条列全，含 `regrade`） | ✅ |
| 冻结 `data-testid` | 128 | **128** = `7+17+5+26+12+8+24+19+3+7`（A–J 十组） | ✅ |
| `rule_code` | 5 | 5 | ✅ |

## 6.4 业务规则与视觉管线

| 项 | 期望 | 实测 | 结果 |
|---|---|---|---|
| `grading_rules` | `94+34+14+2 = 144` | **`{'R1':94,'R2':34,'R3':14,'R4':2}`，Sum 144**（0 死规则 0 空洞） | ✅ |
| `grading_score` | 恒 `NULL` | `DESIGN.md:578` | ✅ |
| 无资产 `asset_importance` | `null` | `DESIGN.md:567` | ✅ |
| 郑州 48h | `statutory_deadline_hours=48`（`2880` min）自 `T_discover`，覆盖 `P1`–`P4` | `DESIGN.md:546-563`；`regrade`/改派不延长 `repair_deadline_at` | ✅ |
| `response_deadline_at` | 默认 `NULL` | 全仓一致 | ✅ |
| `emergency_action_required` | 第 24 条第 2 款 / 住建部令第 4 号第 29 条 → 24h 补办 | `DESIGN.md:548,572,592` | ✅ |
| 双分支合规（维修养护 / 督促留证） | 在位 | `DESIGN.md:647-649`、`REQUIREMENTS.md:258-262`（`SPECIAL_CLOSE` 需 `len(reason)>=10`） | ✅ |
| SLA ② 扫描分支含 `MANUAL_REVIEW` | 在位 | `DESIGN.md:631-638`（`AND is_sla_overdue = false` 幂等守卫） | ✅ |
| `dispatch_scoring_weights` | `(0.40, 0.35, 0.25)` 仅启发式 | `DESIGN.md:534-538`（含「不影响法定 48h」性质边界 + 三级并列裁决） | ✅ |
| `N1`–`N5` | 5 条 | `DESIGN.md:1124-1128` + `REQUIREMENTS.md:214-218` 双份一致 | ✅（README 转述不一致见 P1-6） |
| `V1`–`V3` / `D1`–`D3` | 3+3 | `DESIGN.md:1102-1112` + `REQUIREMENTS.md:226-249` | ✅ |
| `yolo26s.pt @ imgsz=1024` | 唯一档位+尺寸 | 全仓一致；`20,422,725 B` 经 GitHub Releases API 实测确认 | ✅ |
| `15 MiB` / `1..10` | `15,728,640 B` / `1..10` | `.env.example:43-44`、`API.md:201,279`、`DESIGN.md` §4.3/§5.1、`REQUIREMENTS.md:119` 全一致 | ✅ |
| `D-30` 批量仅 `streetlight_on` | `200 {ticket:null, evidences:[...]}` | `DESIGN.md:1078-1085`、`API.md:297,313`、`REQUIREMENTS.md:240`、`DECISION.md:532`、`vision-dev`/`backend-dev`/`frontend-dev`/`tester` **九处一致** | ✅ |
| 27 行 / 30 实验变异卡 | `MUT-01a`–`MUT-17` | 27 个 ID（`MUT-01b..e` 4 个 reason） | ✅ |
| `vision/` 8 草稿 3 处差异 | `errors.py:18,25` / `whitelist.py:168-173` / `config.py:106`+`contract.py:41` | `DESIGN.md:1150-1153` 与 `vision-dev:527-529` 一致，且经 `rg` **逐行验证草稿确如所述** | ✅ |

## 6.5 五文档结构、引用与格式

| 项 | 实测 | 结果 |
|---|---|---|
| `docs/` 根内容 | 精确 5 份 `.md`（`API.md` 686 / `DECISION.md` 666 / `DESIGN.md` 1279 / `REQUIREMENTS.md` 366 / `RUNBOOK.md` 448）+ `team/` | ✅ |
| `docs/team/` | 精确 `LEAD.md`(455) / `TEAM.md`(597) | ✅ |
| 3 份旧文档 | `git status` 均为 `D` | ✅ |
| 旧名活体引用 | **0**（`docs/`15 命中 + `.serena/`4 命中全为历史声明；`RUNBOOK.md`/`LEAD.md`/`README.md`/`.env.example` 零命中） | ✅ |
| Markdown 链接 | 29 条，**死链 0 / 断锚 0** | ✅ |
| 表格完整性 | 353 张，`docs/`+`README.md` **0 错列** | ✅ |
| `DECISION.md` | 40 条 / `9-8-14-9` / **`20✅ 0🔴 0🟡 20🟢`** / 19 原话逐字（`D-29` `:515`、`D-30` `:529` 原文完整） | ✅ |
| `DECISION.md` 顶部总览 vs 附录 A/B/C | 三处口径完全吻合 | ✅ |
| Serena 4 份记忆 | 全部同步 5 文档体系与全部核心数字 | ✅ |
| `.gitignore:47` | `git check-ignore -v` 精确命中 `.serena/memories/` | ✅ |
| `.env.example` 活跃键 | **40**（`HEAD` 37 → `task-27` 39 → `task-45` 40） | ✅ |
| `task-18-verify.sh` | `PASS=75 FAIL=1`（唯一 FAIL 为 `§X` 故意注入自证） | ✅（脚本盲区见 P3-2） |

---

# 七、联网出处校验（F14）结果

## 7.1 MCP 通道实况

| 通道 | 结果 |
|---|---|
| **Context7** | **间歇可用**（与 `DESIGN.md:288` 的绝对化「不可用」不符）：6 个库成功 / 2 次失败，详见 **P2-11** |
| **Exa** | 全部成功（GitHub raw、官方文档、PyPI JSON、npm registry、IETF Datatracker、郑州政府规章库） |

## 7.2 五项精度升级逐条判定

| # | 断言 | 判定 | 依据 |
|---|---|---|---|
| (a) | 未装 `greenlet` 时 `create_async_engine()` 抛 `ValueError`（非 `MissingGreenlet`） | **✅ `DESIGN.md:219` 准确** | `rel_2_0_58/lib/sqlalchemy/util/concurrency.py`：`AsyncAdaptedLock()`→`_not_implemented()`→`ValueError`；`MissingGreenlet` 仅在 greenlet 已装但同步上下文触发隐式异步 IO 时出现（`docs.sqlalchemy.org/en/20/errors.html`）。**注**：`.team/notes/research/merge-online-cross-check.md:46,101` 称 2.1 亦抛 `ValueError`，实测 `rel_2_1_9` 在 `import` 期抛 `ImportError`——但 `DESIGN.md` 只锁 2.0 线，**未被污染** |
| (b) | Alembic `use_alter=True` 静默跳过 FK；须显式 `op.create_foreign_key()` | **✅ `DESIGN.md:986-987` 准确** | Alembic `#326` 仍 open（`2026-10-01` 更新）；`rel_1_20_1` 的 `impl.create_table` 走 `schema.CreateTable` 且 `include_foreign_key_constraints=None`，`use_alter=True` 的 FK 被 DDL 编译器从 `CREATE TABLE` 省略且无后续 `ALTER` → 退出码 0 但 0 FK |
| (c) | `cv2.imdecode` 空 buffer 抛 `cv2.error`，损坏非空 buffer 返回 `None` | **✅ `DESIGN.md:1077` 准确** | `loadsave.cpp:1317` `CV_Assert(!buf.empty())`；`!decoder`/`!readHeader()` 路径返回 `false`→空 Mat/`None` |
| (d) | `onnxruntime` 静态 shape 校验 + `YOLO_AUTOINSTALL`/`YOLO_CONFIG_DIR` 名称 | **✅ 名称与核心行为准确**；依赖落点缺陷见 **P2-3** | `v8.4.216/utils/__init__.py`：`AUTOINSTALL = env_bool("YOLO_AUTOINSTALL", True)`；`get_user_config_dir()` 读 `YOLO_CONFIG_DIR`；`cfg/default.yaml` `flipud:0.0 hsv_s:0.7 hsv_v:0.4 simplify:True`；`exporter.py:800` `model.end2end = self.args.nms is False` → `[1,300,6]` |
| (e) | Playwright `page.get_by_test_id()` 整页穿透 vs `locator.locator()` 子树限制 | **✅ 行为准确**；标签名错误见 **P2-4** | `playwright.dev/python/docs/locators`；Element Plus 源码逐文件核实 |

## 7.3 版本号实页核对（`DESIGN.md:155-170`、`:249-262`）

PyPI / npm 全部命中（`2026-11-21` 快照）：

- **后端**：`fastapi 0.165.0`（要求 `starlette>=1.6.0`）、`sqlalchemy 2.0.58`/`2.1.9`（同日发布）、`psycopg[binary] 3.3.9`、`alembic 1.20.1`、`pydantic 2.14.4`、`pydantic-settings 2.16.3`、`uvicorn 0.54.0`、`pwdlib 0.4.2`、`pyjwt 2.15.1`、`pillow 13.0.0`、`python-multipart 0.0.34`、`tzdata 2026.6`、`pytest 9.2.0`、`pytest-asyncio 1.5.0`、`pytest-cov 7.1.0`、`httpx2 2.16.2`、`asgi-lifespan 2.1.0`、`ruff 0.17.6`、`mypy 2.4.1`、`pytest-playwright 0.10.0`、`ultralytics 8.4.216`、`opencv-python-headless 5.1.0.95`、`onnxruntime 1.32.0`
- **前端**：`vue 3.5.46`（无 `engines`）、`element-plus 2.14.8`（`peer vue: ^3.3.7`）、`@element-plus/icons-vue 2.3.2`、`vite 8.3.8`、`@vitejs/plugin-vue 6.0.11`、`pinia 3.0.4`（latest `4.1.0`）、`vue-router 4.6.5`（`v4-latest`）、`typescript 6.0.3` vs `7.0.2`（`7.0.2` 缺 `./lib/tsc` 导出）、`vue-tsc 3.3.14`、`@volar/typescript 2.4.30`（`peer typescript <7`）、`@playwright/test 1.65.1`
- **`yolo26s.pt`**：GitHub `assets/releases/tag/v8.4.0` 实测 **`20,422,725` B** ✅

> **两条时效性提醒（非缺陷）**：① 本审计时刻（`2026-11-24`）`pnpm` 已从 `12.21.0` 升至 `12.23.0`；② `ultralytics` 已于 `2026-11-23` 发布 `8.4.217` —— pin 策略不一致见 **P2-3**。

## 7.4 郑州规章、IETF 草案与 `uv` 环境变量

- **郑州**：`https://public.zhengzhou.gov.cn/guizhang/6085704.jhtml` 与 `DESIGN.md:546-549` **逐字一致**——2006 年市政府令第 152 号（2018 第 229 号、2020 第 237 号修正）；第 19 条第 3 款「48 小时之内维修养护或督促有关单位维修养护」；第 22 条「不低于 97%」；第 24 条第 2 款「24 小时内告知相关部门，补办相关手续」。✅
- **IETF**：`draft-ietf-httpapi-idempotency-key-header-08` 的 §2.2 唯一性原文、§2.7 的 `400`/`409`/`422`、`X-Idempotent-Replay: true` 明标「项目扩展」——`API.md:330-354`、`DESIGN.md:736` 全部准确 ✅（一处归因偏差见 **P2-10**）。
- **`uv` 环境变量**（`RUNBOOK.md §6.1.2`）：`UV_CACHE_DIR`、`UV_PYTHON_INSTALL_DIR`、`UV_TOOL_DIR`、`UV_PROJECT_ENVIRONMENT`、`UV_PYTHON_BIN_DIR`、`UV_DEFAULT_INDEX`（`UV_INDEX_URL` 官方已标 Deprecated）、`UV_INSTALL_DIR`、同文件系统缓存要求 —— 与 `docs.astral.sh/uv/reference/environment/` **逐字一致** ✅。

---

# 八、总体验收判定

| 判定项 | 结论 |
|---|---|
| **零丢失合并是否达成** | ✅ **达成**——所有计数型契约（17/76/41/22/37、29/36/11、9/29/29、57/34/12/128、144、48h、15 MiB、1..10、`N1`–`N5`、`D-29`/`D-30`）**100% 保全且可独立复算** |
| **5 文档体系是否成立** | ✅ **成立**——目录、格式、链接、表格、旧名清理全部通过 |
| **是否可放行阶段二（文档层面）** | ❌ **否**——须先修 6 项 P1 |
| **`P0`** | **0 条** |

## 建议修派顺序（均属 `docs-writer` / `dev-ops` / `backend-dev` / `vision-dev` 域，`auditor` 不代修）

| 序 | 缺陷 | 责任域 | 理由 |
|---:|---|---|---|
| 1 | **P1-1** `DECISION.md:510-511,412` `D-29`/`D-18` 旧方案残留 | `docs-writer` | 唯一权威裁决清单内部自相矛盾，最高危害 |
| 2 | **P1-4** 两阶段幂等 vs append-only `audit_logs` | **`Lead` 裁决** + `docs-writer`/`backend-dev` | 需先定并发控制方案（`pg_advisory_xact_lock`？），再改三处文档 |
| 3 | **P1-5** 循环外键名/验收行数互斥 | **`Lead` 裁决** + `docs-writer`/`backend-dev` | 两套断言互斥，必须先选方案 A 或 B |
| 4 | **P1-2** `RUNBOOK.md:448` + `vision-dev:380` 无效 `CHECK` | `dev-ops` + `vision-dev` | 改为引用约束名，不内联表达式 |
| 5 | **P1-3** `YOLO_TIMEOUT_SECONDS` vs `VISION_TIMEOUT_SECONDS` | `docs-writer` | 先定「代码常量 or env var」，再统一命名 |
| 6 | **P1-6** `README.md` 四条红线遗漏 `V1` | `docs-writer` | 改五项 `N1`–`N5` 或改单行指针 |
| 7 | **P2-3** `onnx`/`onnxslim` + pin 策略 | `docs-writer` / `dev-ops` | 否则远端 `yolo export` 首跑即失败 |
| 8 | P2-1/6/7/8/9/13 章节与 `FR` 指针 | `docs-writer` | 一次性批量修，成本低 |
| 9 | P2-2 `vision-dev` §4.5 表 | `vision-dev` | 补 `YOLO_AUTOINSTALL`、清「待 `task-27`」 |
| 10 | P2-4/5/10/11 `el-teleport`/`el-option`/IETF/Context7 | `docs-writer` + `docs-writer`（research） | `F14` 归因类 |
| 11 | **P2-12** 端点口径注 | `docs-writer` | 加一行口径注即可，不改数字 |
| 12 | **P3-2** 修 `task-18-verify.sh` `L345`/`L297` | `tester` | 否则下轮审计仍需人工补扫 |

---

# 九、诚实边界

**已证实（🟢，附一手证据）**：
- 第六节全部计数与契约（本人独立 Python 脚本复算 + 逐行 `read` + 3 个子代理独立取证 + 本轮 8 轮 `lead-verify-round*` 交叉确认）。
- 6 项 P1、13 项 P2、5 项 P3（每处均给出 `file:line` 与实测对照；每项均经本人第一手核验，**未直接采信子代理结论**）。
- Context7 间歇可用（6 成功 / 2 失败，本人另独立复现同样现象）。
- 郑州规章与 IETF 草案原文逐字比对；`uv` 环境变量逐字比对。
- `task-18-verify.sh` 实跑 `PASS=75 FAIL=1`。

**未证实 / 未运行（🟡 / 🔴）**：
- **17 表全量 DDL 未在真实 PostgreSQL 执行**——`76/41/22/37` 是 DDL 文本计数，非 `information_schema` 实测值（`DESIGN.md:1260` 已自陈此边界）。
- **27 行（30 实验）变异卡全部未执行**；`vision/` 8 个 `.py` 未经任何运行验证。
- **真实 Chromium 下的 `fill()`/`check()`/`Teleport` 与 `el-option`/`el-upload` 定位未实测**（`D-20`/`D-21`/`D-40`）。
- `opencv-python-headless` 5.x 与 `httpx2` `ASGITransport` 未实测。
- 本地无 `mypy`，未运行静态类型门禁。
- 本审计**只覆盖文档一致性与联网出处**，不覆盖阶段二代码正确性。

**会失效的条件**：
- 若 `docs/` 在本次审计后再次被改写，本报告所有 `file:line` 坐标需重新核对（尤其 `DESIGN.md` 1279 行版本）。
- 若 `pnpm`/`ultralytics` 等继续发版，§7.3 的版本核对结论有时效性（审计时刻 `2026-11-24`）。
- P1-4 与 P1-5 的「正确修法」取决于 `Lead` 裁决，本审计只证明「当前两处无法同时成立」。

---

# 十、`task-56` 最终复核与清零结论（2026-11-24）

> **复核对象**：`task-52`（`docs-writer`）、`task-53`（`dev-ops`）、`task-54`（`vision-dev`）、`task-55`（`backend-dev`）收口修复结果
> **复核基线**：`HEAD = a16335cc3bb0b8ddeb026dfee54d4cc2cb126b80`（工作树 `docs/DESIGN.md` 1323 行、`docs/API.md` 689 行、`docs/DECISION.md` 667 行、`docs/REQUIREMENTS.md` 367 行、`docs/RUNBOOK.md` 448 行、`README.md` 81 行、`.env.example` 89 行）
> **并行分工与一手核验**：`sub1`（P1 全仓扫描 + 前向/循环外键 + 红线）、`sub2`（`task-18-verify.sh` + 目录/链接/表格/`DECISION.md` + Serena）、`sub3`（PG 17 `pg_proc.dat` / `functions-admin` / `system_views.sql`、IETF 127、PyPI `onnx`/`onnxslim`/`ultralytics` 实页取证），以及 `auditor` 本人对全部结果的第一手逐行复核（日志：`.team/notes/auditor/logs/task56/`）。

---

## 10.1 复核总结论（正式交付物 `0 P0 / 0 P1 / 2 P2 / 4 P3`）

| 审计层面 | 结论 | 说明 |
|---|---|---|
| **正式交付物**（`docs/` 5 文档 + `docs/team/` + `README.md` + `.env.example` + `compose.yaml`） | **✅ `0 P0 / 0 P1`；⚠️ `2 P2 / 4 P3`** | 原 `6 P1` 与 `P3-1, P3-2, P3-4, P3-5` **全部清零、零回归**；原 `13 P2` 中 `11` 项彻底清零。**本轮 3 个子代理 + 本人第一手复核新检出正式交付物内 `2 P2`**（`DESIGN.md` 视觉依赖下界低于锁定上游硬要求；`59` 具体端点少计 1 个）**与 `4 P3`**（`DESIGN.md:1323` 变更记录自相矛盾；`TEAM.md:595` 引空行；`DECISION.md:634` 裸 `57`；`RUNBOOK.md:297` 与 `D-32` 口径不一），详见 §10.5 |
| **伴随素材 `.team/notes/` 与 `.serena/`** | **⚠️ 8 条非阻断滞后（记 P3 跟踪，不阻塞 Stage 1 冻结）** | 含 `backend-dev` 未同步第 11 步前向 FK、`A15` 目标态、`<el-teleport>`、咨询锁分隔符、IETF 归因、行号漂移、opset 残余等；详见 §10.6 |

---

## 10.2 `P1-1`–`P1-6` 清零逐条核验

| 编号 | 原问题 | 本次实测核验（精确 `file:line`） | 状态 |
|---|---|---|---|
| **P1-1** | `DECISION.md` `D-29`/`D-18` 旧「不可训练/不用」正文自相矛盾 | `docs/DECISION.md:510-511` 已改写为「个人本地研究/练手项目直接使用…已作废」；`:412`（`D-18`）已同步 `D-29` 放行但零训练不承诺指标口径；全仓活跃文件检索 `不发函.*直接不用`/`D2–D7.*不可训练` **0 命中**；19/19 用户原话逐字保留（`D-29 :515`、`D-30 :529` 完整） | ✅ 已清零 |
| **P1-2** | `RUNBOOK.md:448` 与 `vision-dev:380` 用不存在列 `ai_max_confidence` 写 `fault_evidences` CHECK | `docs/RUNBOOK.md:448` 与 `.team/notes/vision-dev/stage1-vision-spec-input.md:380` 均已改引用具名约束 `ck_fault_evidences_baseline_confidence_cap`；全仓活跃文件中 `fault_evidences` 上无任何 `ai_max_confidence`（仅 `fault_tickets.ai_max_confidence` 保留） | ✅ 已清零 |
| **P1-3** | `YOLO_TIMEOUT_SECONDS` vs `VISION_TIMEOUT_SECONDS` 双名摇摆 | 全仓活跃文件检索 `YOLO_TIMEOUT_SECONDS` **0 命中**；`docs/DESIGN.md:1158`、`docs/API.md:215`、`.team/notes/backend-dev/stage1-backend-spec-api.md:173,453` 统一锁定 **`VISION_TIMEOUT_SECONDS = 60`（后端代码常量，不入 `.env`）**；`.env.example` 维持 40 键（0 `TIMEOUT`） | ✅ 已清零 |
| **P1-4** | 「两阶段幂等占位」与 `audit_logs` 只插不改物理冲突 | 活跃文件检索 `两阶段`/`进行中记录`/`行锁占位` **0 命中**；`docs/DESIGN.md:740-750,1082`、`docs/API.md:346,379` 与 `backend-dev` 四文件（`input.md:43,135,426-427`、`tables.md:81,1039-1043`、`mechanisms.md:404-420`、`api.md:381-388`）及 `.serena/memories/tomori/context.md:18` 全部统一为 **`pg_try_advisory_xact_lock(hashtextextended(..., 0))` 事务级咨询锁 + 锁内双重读取 `audit_logs` + `audit_logs` 严格 append-only（仅 `2xx` 写入 1 行，锁争用返 `409 IDEMPOTENCY_REQUEST_IN_PROGRESS`）** | ✅ 已清零 |
| **P1-5** | 循环外键 `rework_orders` ↔ `acceptance_records` 约束名与行数互斥 | 统一 **方案 B**：`docs/DESIGN.md:1002-1039`、`backend-dev/tables.md:47-76,951`、`.serena/memories/tomori/context.md:17` 一致为：`rework_orders`（14）先建列不建 FK → `acceptance_records`（15）建表 **inline** 建 `fk_acceptance_records_rework_order_id_rework_orders` → 后补一次 `op.create_foreign_key("fk_rework_orders_source_acceptance_id_acceptance_records", ...)` → 双向 2 条校验 SQL 完全一致。（注：`DESIGN.md` 第 11 步前向 FK 补充见 §10.5） | ✅ 已清零 |
| **P1-6** | `README.md` 仍写「四条红线」漏 `V1`，且 `REQUIREMENTS` 写不存在列 `power_density` | `README.md:24,61-67` 已补齐五项核心红线 `N1`–`N5`（含 `N4` 巡查员确认 `V1`）；`README.md:65`、`docs/REQUIREMENTS.md:216`、`docs/DESIGN.md:978,1169` 均对齐 `facility_assets.rated_power_w`；全仓检索 `power_density` **0 命中** | ✅ 已清零 |

---

## 10.3 `P2-1`–`P2-13` 与 `P3-1` / `P3-2` / `P3-4` / `P3-5` 清零核验

| 编号 | 复核结果与精确坐标 | 状态 |
|---|---|---|
| **P2-1** | `docs/REQUIREMENTS.md:314` 改 `§3.5 幂等与点位防重`，`:315` 改 `§5.2 视觉降级契约（D1–D3）`；§8 表 7 行全部命中 `DESIGN.md` 真实章节 | ✅ 已清零 |
| **P2-2** | `.team/notes/vision-dev/stage1-vision-spec-input.md:452-467` 已清除「待 `task-27`」过期状态并补入 `YOLO_AUTOINSTALL=False`（对齐 `.env.example:73`） | ✅ 已清零 |
| **P2-3** | `docs/DESIGN.md:170,197-202,217`、`backend-dev/package.md`、`vision-dev` 均已补入 `onnx`/`onnxslim` 并锁定 `ultralytics==8.4.216`、`opencv-python-headless>=5.1.0,<6`；**但 `sub3` 核出残余下界偏差（记 P2-新1）**：`DESIGN.md:170,201` 写 **`onnxslim>=0.1.31`**，低于 `ultralytics==8.4.216` `[export-base]` 与 `exporter.py:1217` 的硬性要求 **`onnxslim>=0.1.82`**（`0.1.31..0.1.81` 虽满足 `DESIGN.md` 却会在 `YOLO_AUTOINSTALL=False` 下被 `check_requirements` 拒绝导出）；另 `:170,200` `onnx>=1.16.0` 漏 `exporter.py:1207` 的 `<2.0.0` 上界、`:170` `requires_python` 漏列 `onnx`（`>=3.10`） | ⚠️ **未完全清零（残余 P2）** |
| **P2-4** | `docs/DESIGN.md:284`、`docs/API.md:600,670`、`.team/notes/research/merge-online-cross-check.md:37,88,117,141` 全部改用 Vue 原生 `<teleport>` 并附 Element Plus PR `#23219` 依据；`docs/` 与 `research/` 检索 `<el-teleport>` **0 命中**（`frontend-dev`/`tester` 笔记残留见 §10.5） | ✅ 已清零 |
| **P2-5** | `docs/API.md:606`（原 `:604`）已改写与 `:601`/`DESIGN.md:284` 一致（「`option.vue` 源码已核实为单根 `<li role="option">`，真实 Chromium 交互待首验」），消除内部自相矛盾 | ✅ 已清零 |
| **P2-6 / P2-7** | `docs/API.md:13` 改 `TEAM.md §5.3`、`:19` 改 `§6.4`、`:20,491` 改 `§8.2/§8.3`（无裸 `§10.2`）、`:103` 改 `DESIGN.md §3.3（表结构见 §4.4 ⑤）` | ✅ 已清零 |
| **P2-8** | `docs/DECISION.md:618`（`U-4` / `dispatch_scoring_weights`）已去除不存在的 `§8` 空指针 | ✅ 已清零 |
| **P2-9** | `docs/RUNBOOK.md:72,432,446` 与 `.team/notes/dev-ops/stage1-infra-facts.md:79,82` 已将 `413 IMAGE_SIZE_EXCEEDED` 改指 `API.md §3.3`，`503 VISION_DISABLED` 改指 `API.md §3.4` + `DESIGN.md §5.2`（不再误指 `API.md §5.2`） | ✅ 已清零 |
| **P2-10** | `docs/API.md:360` 已精确归因至 IETF 127 HTTPAPI WG 幻灯片 `slides-127-httpapi-the-idempotency-key-http-header-field-00`（附 URL） | ✅ 已清零 |
| **P2-11** | `docs/DESIGN.md:236-239,1294` 与 `.team/notes/research/merge-online-cross-check.md` 均已将 Context7 从「绝对不可用」下调为「间歇性不稳定，严禁作为唯一取证源」并统一实测次数 | ✅ 已清零 |
| **P2-12** | `docs/API.md:17,71,661`、`README.md:26`、`DESIGN.md:1309`、`REQUIREMENTS.md:353`、`backend-dev/input.md:503` 及 Serena 均改为 `57 条路由行（展开为 59 个 (method, path) 组合）`；**但对照 `backend-dev/api.md:274-276` 核出 59→60 口径偏差（记 P2-新2）**：`API.md:136` 合并行 `GET/PATCH` `/repair-processes、/repair-processes/{id}` 实对应 **3 个具体端点**（`GET /repair-processes`、`GET /repair-processes/{id}`、`PATCH /repair-processes/{id}`，无 `PATCH /repair-processes`），加 `API.md:145` 2 个与其余 55 行，具体端点数应为 **`60`** 而非 `59` | ⚠️ **未完全清零（残余 P2）** |
| **P2-13** | `docs/DECISION.md:340` 改 `FR-26` / `FR-27`、`:378` 改 `FR-21` | ✅ 已清零 |
| **P3-1** | `backend-dev/stage1-backend-spec-api.md:287` 与 `dev-ops/remote-gpu-probe.md:13` 表格竖线已转义；全 `docs/` + `README.md` + 6 个角色 `.team/notes/` **25 文件 / 353 张表 = 0 错列** | ✅ 已清零 |
| **P3-2** | `task-18-verify.sh` `L308-413`（§8 跳过代码块并校验全部 `#anchor` slug 与重复后缀）、`L455-505`（§10 校验表头 + 分隔符 + **全数据行**）、`L35-99`（§3 扫 `.env.example` + `.serena/memories/tomori/`）三处盲区已实质修补；实跑 **`PASS=80 FAIL=1`**（唯一 FAIL 为 `§X` 自证） | ✅ 已清零 |
| **P3-4** | `docs/DESIGN.md:776-782` SQL #4 已改为 `pg_index` + `AND i.indisunique AND NOT i.indisprimary`（排除 `*_pkey`/`pk_*`）；`:975` `audit_logs` CHECK 摘要补 `IS NULL OR`；`:332-334,723` `A15`–`A17` 与 `review-resolve` 补「若存在 `IN_MANUAL_REVIEW` / 活跃返修单」限定（并修正 `A15` 统一回落 `PENDING_DISPATCH`） | ✅ 已清零 |
| **P3-5** | `docs/RUNBOOK.md:403` 与 `vision-dev:253,264-272` 均已统一使用默认 opset 18（去掉硬编码 `opset=17`）与扁平 `./vision/weights/{best.pt,best_e2e.onnx}`；`backend-dev/input.md:504` 已补 `docs/RUNBOOK.md §3` | ✅ 已清零 |

---

## 10.4 本轮新增技术断言的联网/源码 F14 复核（`sub3` + 本人）

| 检查对象 | 一手出处 | 实测核对结果 |
|---|---|---|
| `pg_try_advisory_xact_lock(hashtextextended(..., 0))` | PG 17 源码 `pg_proc.dat`（L1220 / L8746）与官方文档 `9.28.10` | `hashtextextended(text, int8)` 返回 `int8`；`pg_try_advisory_xact_lock(int8)` 返回 `bool`（与 `(int4, int4)` 重载按参数个数/类型无歧义），事务提交/回滚自动释放；`DESIGN.md:742-743` 准确 ✅ |
| SQL #4 `NOT i.indisprimary` | PG 17 `51.26 pg_index` 与 `system_views.sql` L154-165 (`pg_indexes`) | `pg_indexes` 视图本身包含 `indisprimary = true` 的主键索引；改查 `pg_index i ... WHERE i.indisunique AND NOT i.indisprimary` 才能精准排除 `*_pkey`/`pk_*` 仅计业务 `UNIQUE` 22 项（16 全时 + 6 部分）。SQL #5 用 `indexdef NOT LIKE 'CREATE UNIQUE INDEX%'` 天然排除主键唯一索引，37 项无误 ✅ |
| IETF 127 幻灯片引文 | `https://datatracker.ietf.org/meeting/127/materials/slides-127-httpapi-the-idempotency-key-http-header-field-00`（L44-47） | 逐字命中 *"Open: do errors count as 'completed'? Practice differs; #48 suggests a retry after a 4xx is processed as new"* 与 *"#46 replay indicator: not proposed..."*，`API.md:352,360` 准确 ✅ |
| **`onnx` / `onnxslim` 版本边界** | Ultralytics `v8.4.216` `pyproject.toml`（L94-102 `[export-base]`）& `exporter.py:1207,1217` + PyPI JSON | 上游 `[export-base]` 与 `exporter.py:1217` 硬要求 `onnx>=1.12.0,<2.0.0` 与 **`onnxslim>=0.1.82`**；PyPI 实测最新 **`onnx 1.23.2`**（`requires_python >=3.10`）、**`onnxslim 0.1.99`**（`requires_python >=3.8`）。新装解析 `0.1.99` 可满足，但 **`DESIGN.md:170,201` 的下界 `onnxslim>=0.1.31` 低于 `0.1.82` 且 `:200` 缺 `<2.0.0`**，列为 **P2** ⚠️ |
| `compose.yaml` 与结构/计数 | `docker compose -f compose.yaml config --quiet`；独立 Python 复算 | `compose.yaml` exit=0；`ls docs/` 精确 5 份 + `team/{LEAD,TEAM}.md`；`DECISION.md` `40`（`9/8/14/9`、`20✅ 0🔴 0🟡 20🟢`、19 原话）；29 链接 **0 死链 0 断锚**；`.serena/memories/tomori/` 4 文件同步且命中 `.gitignore:47` ✅ |

---

## 10.5 本轮新检出：`docs/` 正式交付物的 2 项 P2 + 4 项 P3 残留

> 以下均由 `sub1` / `sub2` / `sub3` 提出线索，**由本人在原文件上第一手复核确认**（含纠正子代理的错误判断，见末行说明）。

### 10.5.1 2 项 P2（正式交付物内，建议 `docs-writer` 补正后提交）

| 编号 | 缺陷（精确坐标） | 一手证据 |
|---|---|---|
| **P2-A** | `docs/DESIGN.md:170,200-201` 的 `vision` extra 版本边界低于锁定上游的硬要求 | `docs/DESIGN.md:201` 写 `"onnxslim>=0.1.31"`、`:200` 写 `"onnx>=1.16.0"`（无上界），`:170` 表行同为 `>=1.16.0` / `>=0.1.31`；而 `ultralytics==8.4.216` 的 `pyproject.toml:98` `[export-base]` 与 `exporter.py:1217`（`if self.args.simplify: requirements += ["onnxslim>=0.1.82"]`）**硬要求 `onnxslim>=0.1.82`**，`exporter.py:1207` 为 `onnx>=1.12.0,<2.0.0`。故 `0.1.31..0.1.81` 会满足本设计却在 `YOLO_AUTOINSTALL=False` 下被 `check_requirements()` 直接拒绝导出。另 `:170` 的 `requires_python` 列 `>=3.8 / >=3.6` 未覆盖 `onnx`（PyPI 实测 `>=3.10`） |
| **P2-B** | `59` 个具体端点少计 1 个，应为 **`60`** | `docs/API.md:17,71,661`（及 `README.md:26`、`DESIGN.md:1309`、`REQUIREMENTS.md:353`、Serena）统一写「`57 条路由行`（展开为 `59` 个 `(method, path)` 组合）」；但 `docs/API.md:136` 的合并行 `GET`/`PATCH` `/repair-processes`、`/repair-processes/{id}` 实对应 **3 条**（`GET /repair-processes`、`GET /repair-processes/{id}`、`PATCH /repair-processes/{id}`，**无** `PATCH /repair-processes`），与 `.team/notes/backend-dev/stage1-backend-spec-api.md:274-276` 三条独立行逐一对上；57 行中含 2 条合并行（`:136` 3 条、`:145` 2 条）→ `57 - 2 + 3 + 2 = 60` |

### 10.5.2 4 项 P3（正式交付物内的精度问题，不阻塞）

| 编号 | 缺陷（精确坐标） | 一手证据 |
|---|---|---|
| **P3-α** | `docs/DESIGN.md:1323`（§8.2 `task-52` 变更记录）仍写「**一次** `op.create_foreign_key` 补齐 `fk_rework_orders_source_acceptance_id_acceptance_records`」，与同文件 `:1006-1020` 的**两次**调用（含 `fk_dispatch_records_rework_order_id_rework_orders`）自相矛盾 | 提取 `:1323` 原文片段：`③ P1-5：§4.5 循环外键统一为方案 B（acceptance_records.rework_order_id 随表 inline FK + 一次 op.create_foreign_key 补齐 fk_rework_orders_source_acceptance_id_acceptance_records）`；`:1006` 明写「**1 条补齐循环反向 FK**（构成方案 B 双向共 2 条），**1 条补齐前向 FK**（消除 `11 → 14` 断链）」 |
| **P3-β** | `docs/team/TEAM.md:595` 引用「详细去重归因见 `docs/DECISION.md:593`」，但 `:593` 是**空行**（`D-09` 条目跨 `:573-597`，`cat -A` 显示 `:593` 仅 `$`） | `sed -n '593p' docs/DECISION.md \| cat -A` → `$` |
| **P3-γ** | `docs/DECISION.md:634` 以「**数字均带口径**」起句，同行却写裸 `57 端点`，未带 `路由行 / 59（或 60）组合` 限定 | `:634` 原文：`- 数字均带口径：57 端点 / 34 错误码 / ...` |
| **P3-δ** | `docs/RUNBOOK.md:297` 仍写「须提前在 `pyproject.toml`（**或 dev/vision 依赖**）中显式包含 `onnx`、`onnxslim`」，与 `docs/DESIGN.md:196-202` 已定案「只在 `[project.optional-dependencies] vision`」不一致（`D-32` 单包口径） | `:297` 原文；`DESIGN.md:196` 起为 `vision = [...]` 分组 |

> **对子代理结论的两处纠正（不采信转述）**：① `sub1` 称 `vision-dev:531` 误标 `§5.3`、实为 `§5.2`——**错**：`docs/DESIGN.md` 的 `### 5.3` 起于 `:1175`，`#### ⚠️ vision/ 目录 8 个 .py` 起于 `:1191`，故 `:1148-1153` 属**同一 §5.3 的错行号**，并非跨节；② `sub2` 称 `DESIGN.md:1236` / `DECISION.md:456` 的 `opencv-python-headless==5.1.0.95` 与 `:198` 的 `>=5.1.0,<6` 冲突——**不成立**：`:198` 行内注释已写明「2026-11-21 PyPI 实测 `5.1.0.95`，精确版本锁入 `uv.lock`」，属「约束 + 实测锁定版」的刻意双层表述，非缺陷。

---

## 10.6 伴随素材（`.team/notes/` 与 `.serena/`）的 8 条非阻断观察（记 P3，不阻塞 Stage 1 冻结）

> **权威边界**：按 `docs/team/TEAM.md §5.6` 与 `docs/API.md:9`，**实现以 `docs/` 5 文档为唯一权威**。下列条目位于 gitignored 的 `.team/notes/`（`.gitignore:45`）与 `.serena/memories/`（`.gitignore:47`），仅作阶段二开工前的同步提醒。

| # | 位置 | 滞后内容 | 阶段二应以何处为准 |
|---:|---|---|---|
| 1 | `.team/notes/backend-dev/stage1-backend-spec-tables.md:27,36,53,61-74,738,1288` | 【Lead 点名核实项】未同步第 11 步前向 FK：`§0.1:27` 依赖行漏 `rework_orders`；`§0.2:53` 仍写「**一次** `op.create_foreign_key`」；`:61` `downgrade` 漏 drop `fk_dispatch_records_rework_order_id_rework_orders`；`:62-74` 验收 SQL 只查 2 条循环 FK；`§11:738` 无延迟 FK 警示（`§15:964` 有）；`:1288` 自检 SQL 仍用 `pg_indexes` 未加 `NOT i.indisprimary` | `docs/DESIGN.md:991-1039`（11→14 前向 FK 与 2 次 `create_foreign_key` 的权威写法，`:792`/`:999` 已如实注明 `tables.md` 缺口） |
| 2 | `.team/notes/backend-dev/stage1-backend-spec-{state-machines.md:30, mechanisms.md:364, api.md:318}` | `A15` `APPROVE_REASSIGN` 目标态仍写旧口径「`REWORK_PENDING_DISPATCH` 或 `PENDING_DISPATCH`」 | `docs/DESIGN.md:332`（`C9` 终结返修单后票统一回落 `PENDING_DISPATCH` 由 `A6` 派工） |
| 3 | `.team/notes/research/merge-online-cross-check.md:77` | P3-5 残余：仍写「官方 `export` 默认 `opset=18`（本项目显式传 `opset=17` 属合法显式指定）」，与已定案「不硬编码 opset」相反 | `docs/RUNBOOK.md:403`、`vision-dev:253`（沿用上游默认 opset 18） |
| 4 | `.team/notes/vision-dev/stage1-vision-spec-input.md:380,531` | `:380` 引 `DESIGN.md §4.3 :948`，实际 `ck_fault_evidences_baseline_confidence_cap` 在 `:960`；`:531` 引 `§5.3 lines 1148–1153`，实际三处草稿差异在 `:1193-1196` | `docs/DESIGN.md:960`、`:1191-1196` |
| 5 | `.team/notes/backend-dev/stage1-backend-spec-package.md:4,17,229-230,355`；`vision-dev:438` | `:4` 引「父文件 `input.md §9`」，但 `input.md` 仅有 `§0`–`§7`；另有未加边界的 `"onnx"`, `"onnxslim"` | `docs/DESIGN.md:200-201`（补正后的边界） |
| 6 | `.team/notes/frontend-dev/stage1-frontend-spec-input.md:79,1046`；`.team/notes/tester/stage1-test-matrix-input.md:634,642` | 仍写 `<el-teleport>`（`docs/` 与 `merge-online-cross-check.md` 已清零） | `docs/DESIGN.md:284`（Vue 原生 `<teleport>`，EP PR `#23219`） |
| 7 | `.team/notes/tester/stage1-test-matrix-input.md:120`；`.team/notes/backend-dev/stage1-backend-spec-{mechanisms.md:413, api.md:367,383, input.md:43,135,427, tables.md:81,1043}` | `:120` 的 `B2` 行仍把 `Idempotency-Key` 竞态归于 `SELECT ... FOR UPDATE` 真实行锁（定案为咨询锁，无对应行可锁）；咨询锁拼接分隔符用竖线 `\|` 而 `DESIGN.md:742` 用冒号 `:`（且 `mechanisms.md:413` 缺 `::text`）；`api.md:367` 仍把 IETF 127 幻灯片引文归因给「草案 §2.6」 | `docs/DESIGN.md:742`、`docs/API.md:346,360` |
| 8 | `.team/notes/research/datasets.md:377`；`.team/notes/research/logs/task-18-verify.sh:508`；`P3-3` | `datasets.md:377` 1 处行内代码竖线（`hdr=5 row=8`）；`task-18-verify.sh:508` 的 `grep -rq "<el-teleport>"` 匹配面偏窄（漏 `<el-teleport :disabled=...>` 形式）且未覆盖 `.team/notes/{frontend-dev,tester}/`；`P3-3`（历史 notes 旧 `TEAM.md` 节号）按 Lead 明示**不列入本轮修复范围** | 不影响 `docs/` 与 6 角色素材；建议 `tester` 后续把该断言改为 `grep -rqE "<el-teleport[ >]"` 并扩大扫描面 |

> **另需注意（并发改动，非缺陷）**：本轮复核期间 `.serena/memories/tomori/{context.md,repo-map.md}` 于 `05:18:13-14` 被其他角色更新——`context.md:19` 已写 `"onnx>=1.16.0,<2.0.0"`（实测 `1.23.2`）+ `"onnxslim>=0.1.82"`（实测 `0.1.99`）、`repo-map.md:15` 已写 `onnxslim>=0.1.82`，即 **Serena 已按正确值先行更新，而 `docs/DESIGN.md:200-201` 仍是旧值**，二者当前不一致（见 P2-A）。这同时说明 P2-A 是真实待修项。

---

## 10.7 `task-56` 最终放行判定与诚实边界

- **正式交付物审计结论**：**`0 P0 / 0 P1 / 2 P2 / 4 P3`** —— 阶段一 `6` 项 P1 **全部清零且零回归**，结构与脚本类问题全部达标；**无任何 P0/P1 阻断项**，`git-keeper` 可签名提交。建议 `docs-writer` 在提交前顺手补正 §10.5.1 的 2 项 P2（`docs/DESIGN.md:170,200-201` 版本边界；`docs/API.md:17,71,661` 等 `59→60` 口径）；如 Lead 判定可带注归档，本审计亦不反对。
- **审计基线快照（本次结论所依据的精确版本，供复核复用）**：
  `docs/DESIGN.md` 1323 行 `825da30c8da3647c…`、`docs/API.md` 689 行 `e99e540b4e172f32…`、`docs/DECISION.md` 667 行 `8c7c4f265a306e54…`、`docs/REQUIREMENTS.md` 367 行 `14d51f1c01c10e96…`、`docs/RUNBOOK.md` 448 行 `a1b644ef17382ade…`、`README.md` 81 行 `6452c9723aea2050…`、`.env.example` 89 行 `25ed198e67d762ed…`、`docs/team/TEAM.md` 597 行 `30088b005c815c51…`、`docs/team/LEAD.md` 455 行 `b5a58f363f6a3b2f…`（`sha256` 前 16 位）。
- **并发改动声明（诚实边界）**：复核期间工作树被他角色变动（`.serena/memories/tomori/{context.md,repo-map.md}` `05:18` 更新；`git status` 新增 `D main.py` / `?? src/tomori/main.py`）。上述 `docs/` 五项与 `README.md`、`.env.example` 的行号与哈希**以本快照为准**；若 `docs/` 再次被改写，§10 全部 `file:line` 需重核。
- **未运行范围（与第九节一致，未变更）**：17 表全量 DDL 未在真 PG 执行（`76/41/22/37` 仍为 DDL 文本计数）；27 行（30 实验）变异卡未执行；`vision/` 8 个 `.py` 未运行；真实 Chromium 下 `D-20`/`D-21`/`D-40`、`opencv 5.x` 与 `mypy` 均未运行；本审计只覆盖文档一致性与联网出处，不覆盖阶段二代码正确性。
- **只读与 git 零写声明**：`auditor` 在 `task-56` 全程仅写 `audits/` 与 `.team/notes/auditor/`，未改动 `docs/`、`README.md`、`.env.example`、`.serena/` 或任何他人目录；零 git 写操作，`HEAD` 仍为 `a16335cc3bb0b8ddeb026dfee54d4cc2cb126b80`。

> **最终复核人**：`auditor`｜**复核副本**：`.team/notes/auditor/stage1-doc-merge-audit-report.md`（与本文件逐字节一致）。

---

# 十一、`task-61` 提交前终核与清零结论（`0 P0 / 0 P1 / 0 P2`）

> **复核对象**：`task-58`（`docs-writer`）、`task-59`（`backend-dev`）、`task-60`（`vision-dev`）收口修改
> **复核基线**：`HEAD = a16335cc3bb0b8ddeb026dfee54d4cc2cb126b80`（工作树实时日志：`.team/notes/auditor/logs/task61/`）

## 11.1 `P2-A` / `P2-B` 与咨询锁/IETF 127 一手复核

| 核验项 | 精确位置与实测结果 | 状态 |
|---|---|---|
| **P2-A**（`onnx` / `onnxslim` 约束与 PyPI 实测） | `docs/DESIGN.md:170` 已写 `onnx 1.23.2（>=1.16.0,<2.0.0，requires_python >=3.10）`、`onnxslim 0.1.99（>=0.1.82，requires_python >=3.8）`；`:200-201` 与 `:217` 均改为 `"onnx>=1.16.0,<2.0.0"`、`"onnxslim>=0.1.82"` 并对齐 `ultralytics==8.4.216` `exporter.py:1207/1217`；`backend-dev/stage1-backend-spec-package.md:17,229-230,267,355`、`vision-dev/stage1-vision-spec-input.md:207,254,433,438,465,527` 与 `.serena/memories/tomori/{context.md:19, repo-map.md:15}` 全部一致；全仓活跃文件 `onnxslim>=0.1.31` **0 命中** | ✅ **已清零** |
| **P2-B**（`57` 路由行 → `60` 具体端点） | `docs/API.md:17,71,136,661,689-690`、`README.md:26`、`docs/DESIGN.md:1309,1323-1324`、`docs/REQUIREMENTS.md:353,368`、`backend-dev/stage1-backend-spec-input.md:503` 与 `.serena/memories/tomori/{context.md:32, repo-map.md:10,16}` 全仓统一为 **`57` 条路由行（展开为 `60` 个具体 `(method, path)` 端点）**，明示 `API.md:136` 对应 3 端点；全仓正式文件与 6 角色素材检索 `\b59\b` **0 命中**（仅历史快照 `task52-closeout-report.md` 留存） | ✅ **已清零** |
| **咨询锁全称 & IETF 127 统一** | `docs/DESIGN.md:742`、`docs/API.md:346,379,689-690`、`backend-dev` 四文件（`input.md:43,135,427`、`tables.md:81,1043`、`api.md:383`、`mechanisms.md:413`）及 `context.md:18` 全部统一为 **`pg_try_advisory_xact_lock(hashtextextended(actor_account_id::text \|\| ':' \|\| method \|\| ':' \|\| path \|\| ':' \|\| idempotency_key, 0))`**（统一 `':'` + `::text` + `, 0`；表格内竖线正确转义 `\|\|`）；`API.md` 零 `hashtextextended(...)` 略写残留；`backend-dev/stage1-backend-spec-api.md:367` 已同步为 `IETF 127 HTTPAPI WG 幻灯片（slides-127-httpapi-the-idempotency-key-http-header-field-00）` | ✅ **已清零** |
| **校验脚本与结构自检** | 实跑 `bash .team/notes/research/logs/task-18-verify.sh` → **`PASS=83 FAIL=1`**（`exit=0`，唯一 `FAIL` 为 `§X` 自证，含新增 3 条断言 `L512-514`）；全 `docs/` + `README.md` + 6 角色 `.team/notes/` 共 **25 文件 / 353 张表 = 0 错列**、**29 条链接 = 0 死链 0 断锚** | ✅ **全通过** |

## 11.2 最终放行判定（`0 P0 / 0 P1 / 0 P2`）与阶段二口径说明

- **正式交付物审计结论**：**`0 P0 / 0 P1 / 0 P2`** —— 阶段一原 `6 P1 / 13 P2` 及本轮复核新检出之 `P2-A` / `P2-B` **全部清零**，**正式放行 `git-keeper` 执行阶段一文档冻结签名提交**。
- **不阻塞之既有 `P3` 与笔记提醒**：§10.5.2 所记 4 项 `docs/` 精度 `P3`（`DESIGN.md:1323` 变更记录 `一次 op.create_foreign_key`、`TEAM.md:595`→`DECISION.md:593` 空行、`DECISION.md:634` 裸 `57 端点`、`RUNBOOK.md:297` `或 dev/vision 依赖`）及 §10.6 所余伴随 `.team/notes/` 观察项（`tables.md` 第 11 步前向 FK 与 SQL #4、`backend-dev` `A15` 旧目标态、`frontend-dev`/`tester` `<el-teleport>`、`tester:120` `B2` 行、历史 `P3-3` 等）**均不阻塞阶段一冻结提交**（实现以 `docs/DESIGN.md` / `docs/API.md` 为唯一权威）。
- **阶段二 `src layout` 口径预告（不阻塞当前提交）**：用户已放行实现并指定 **`src layout`**（当前工作树已见 `D main.py` / `?? src/tomori/main.py`），后续阶段二实施期间将由 `docs-writer` / `dev-ops` 对 `docs/DESIGN.md`、`docs/RUNBOOK.md` 等做 `src layout` 目录口径同步。
- **只读与未运行边界**：`auditor` 在 `task-61` 全程仅写 `audits/` 与 `.team/notes/auditor/`，`HEAD` 仍 `a16335cc3bb0b8ddeb026dfee54d4cc2cb126b80`，零 git 写操作；17 表 DDL、27 行/30 实验变异卡、`vision/` 8 `.py`、真实 Chromium `D-20`/`D-21`/`D-40`、`opencv 5.x` 与 `mypy` 仍处于阶段一未运行边界。

> **终核人**：`auditor`｜**复核副本**：`.team/notes/auditor/stage1-doc-merge-audit-report.md`（与本文件逐字节一致）。



