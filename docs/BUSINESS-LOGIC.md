> 状态：阶段一定稿候选 —— 待用户审阅裁决（未定稿）

# 业务逻辑设计（BUSINESS-LOGIC）

> **文档归属**：`docs-writer`｜**本文件写入域**：`docs/BUSINESS-LOGIC.md`
> **读者**：`backend-dev`（唯一实现依据）、`tester`（状态机与机制断言依据）、`frontend-dev`、`auditor`
> **本文件的地位**：**状态机、三大非 CRUD 机制、跨表不变量的唯一权威**。实现期任何与此冲突的写法一律视为缺陷。

---

## 0. 统计口径分层声明（`docs/team/TEAM.md` §4.6）

| 项 | 分子 | 分母 | 排除项 |
|---|---:|---|---|
| 状态机数量 | **4** | 库中所有含 `status` 列的业务表 | `accounts.status`（简单的 `ACTIVE`/`DISABLED` 二值，非流程机） |
| 状态总数 | **29** | 4 套状态机之和 | — |
| 合法转移边 | **36** = A 15 + B 6 + C 8 + D 7 | 见 §1–§4 | **6 条「（无）→ 初态」入口行不计入边数**（A1/A2/B1/C1/C2/D1）；编号行共 **42**，42 − 6 = 36 |
| 终态数量 | **11** | 票 2（`CLOSED`/`CANCELLED`）+ 派工 5 + 返修 3 + **通知机 1（`SENT`）** | **无**。通知机**有**终态 `SENT`（出边 = 0，吸收态）。它**不是死路**——`DEAD_LETTER` 有 D7/D8 两个人工出口，见 §4.2（`audits/stage1-audit.md` P0-2） |
| 非 CRUD 机制 | **3** | 区域派单 / 故障分级 + SLA / 返修闭环 | 幂等与审计属横切能力，不计入机制 |
| 跨表不变量 | **8**（I1–I8） | 见 §5 | — |

> **4 套状态机分别约束**：`fault_tickets`（9 态）/ `dispatch_records`（7 态）/ `rework_orders`（8 态）/ `notification_tasks`（5 态）。

---

## 1. 状态机 A：故障单主状态机（`fault_tickets.status`，9 态）

### 1.1 状态全集与终态

**$S$ = 9 个状态**：
`AI_CANDIDATE_PENDING` / `PENDING_DISPATCH` / `DISPATCHED` / `IN_REPAIR` / `PENDING_ACCEPTANCE` / `REWORK_PENDING_DISPATCH` / `MANUAL_REVIEW` / `CLOSED` / `CANCELLED`

**$T$（终态）= 2 个**：`CLOSED` / `CANCELLED`。**终态不可复活**，再修必须另开新单（旧单永久留档）。

### 1.2 全量转移边 $E$（**17 行编号** = 2 条入口行 + **15 条合法边**，逐条给触发者/守卫/副作用）

| # | from → to | 触发者 | 端点 | 守卫（不满足即拒绝） | 审计 `action` |
|---:|---|---|---|---|---|
| **A1** | （无）→ `AI_CANDIDATE_PENDING` | INSPECTOR / ADMIN | `POST /tickets/ai-candidates` | 静态图片白名单通过；视觉返回 ≥1 个 `is_fault_candidate=true`；同点位同类无活跃单（否则 `409`） | `AI_CANDIDATE_GENERATED` |
| **A2** | （无）→ `PENDING_DISPATCH` | INSPECTOR / ADMIN | `POST /tickets` | 人工报障，`confirmed_by_inspector=true` 直接成立；同点位同类无活跃单 | `TICKET_CREATED` |
| **A3** | `AI_CANDIDATE_PENDING` → `PENDING_DISPATCH` | INSPECTOR（区域 `INSPECT`） | `POST /tickets/{id}/confirm` | 巡查员可改 `fault_type`/`priority`/标题；**确认后自动跑分级**写 `priority` + `grading_snapshot_json` + 两个 SLA deadline；改为视觉不产出的类型（`FLICKERING`/`ELECTRIC_HAZARD`）**必须带 `manual_note`** | `TICKET_CONFIRMED` + `TICKET_GRADED` |
| **A4** | `AI_CANDIDATE_PENDING` → `CANCELLED` | INSPECTOR（区域 `INSPECT`） | `POST /tickets/{id}/cancel` | 巡查员否定 AI 候选（误报）；`cancel_reason` 必填 | `TICKET_CANCELLED` |
| **A5** | `PENDING_DISPATCH` → `CANCELLED` | INSPECTOR（本人报障）/ ADMIN | `POST /tickets/{id}/cancel` | **仅派工前可撤销**；`cancel_reason` 必填；撤销后点位防重锁释放 | `TICKET_CANCELLED` |
| **A6** | `PENDING_DISPATCH` → `DISPATCHED` | ADMIN（区域 `MANAGE`） | `POST /tickets/{id}/dispatch` | ① `confirmed_by_inspector=true`（否则 `422 AI_CANDIDATE_NOT_CONFIRMED`）；② 目标维修员有该区域 `REPAIR` 有效授权（否则 `422 DISPATCH_TECHNICIAN_NOT_ELIGIBLE`）；③ 在途数 < `max_active_dispatch`（否则 `422 TECHNICIAN_OVERLOADED`）；④ 同票无活跃派工（部分唯一索引兜底）；同事务插入 `dispatch_records` | `TICKET_DISPATCHED` |
| **A7** | `DISPATCHED` → `IN_REPAIR` | TECHNICIAN（该派工单受派人） | `POST /dispatches/{id}/accept` | 派工记录 `status=PENDING_ACCEPT` 且未过 `accept_deadline_at`（过期走 SLA 扫描而非本端点）；`accepted_at=now()` | `DISPATCH_ACCEPTED` |
| **A8** | `DISPATCHED` → `PENDING_DISPATCH` | TECHNICIAN（受派人） | `POST /dispatches/{id}/reject` | `reject_reason` 必填；`dispatch_reject_count += 1`；派工记录置 `REJECTED`（退出活跃索引）；**若累计拒单 ≥ 阈值（默认 3）→ 反而转 `MANUAL_REVIEW`** | `DISPATCH_REJECTED` |
| **A9** | `IN_REPAIR` → `PENDING_ACCEPTANCE` | TECHNICIAN（受派人） | `POST /dispatches/{id}/complete` | ① `electrical_safety_checked=true` 且 `safety_checklist_json` 5 项全 `true`（否则 `422 ELECTRICAL_SAFETY_CHECK_REQUIRED`）；② `repair_summary` 非空；③ 同事务写 `repair_processes` + `material_usages[]` | `REPAIR_COMPLETED` |
| **A10** | `IN_REPAIR` → `MANUAL_REVIEW` | TECHNICIAN（受派人） | `POST /dispatches/{id}/escalate` | `escalate_reason` 必填（现场无法安全作业 / 缺件 / 超出技能）；派工记录置 `ESCALATED`。**这是维修员在 `IN_REPAIR` 的合法退出口** | `DISPATCH_ESCALATED` |
| **A11** | `PENDING_ACCEPTANCE` → `CLOSED` | INSPECTOR（区域 `INSPECT`） | `POST /tickets/{id}/acceptance`（`decision=APPROVED`） | 写 `acceptance_records(APPROVED)`；`closed_at=now()`；若有进行中返修单则同事务置 `PASSED`；发 `TICKET_CLOSED` 通知 | `ACCEPTANCE_APPROVED` |
| **A12** | `PENDING_ACCEPTANCE` → `REWORK_PENDING_DISPATCH` | INSPECTOR（区域 `INSPECT`） | `POST /tickets/{id}/acceptance`（`decision=REJECTED`） | ① `comment` 必填；② **同事务四件事**：写 `acceptance_records(REJECTED)` + `rework_count += 1` + 插入 `rework_orders(round_no=rework_count)` + 置票状态；③ `round_no ≤ max_rework_rounds`（默认 2）走本边，**超阈值走 A13** | `ACCEPTANCE_REJECTED` + `REWORK_CREATED` |
| **A13** | `PENDING_ACCEPTANCE` → `MANUAL_REVIEW` | INSPECTOR（区域 `INSPECT`） | 同上，`decision=REJECTED` | `round_no > max_rework_rounds`：返修单置 `requires_manual_review=true`、`status=IN_MANUAL_REVIEW`；票置 `MANUAL_REVIEW` + `manual_review_reason`（DB CHECK 强制非空）；通知全部 ADMIN | `ACCEPTANCE_REJECTED` + `REWORK_CREATED` + `REWORK_MANUAL_REVIEW_REQUIRED` |
| **A14** | `REWORK_PENDING_DISPATCH` → `DISPATCHED` | ADMIN（区域 `MANAGE`） | `POST /tickets/{id}/dispatch` | 同 A6 守卫，另需：存在该票活跃 `rework_orders`（受 `uq_rework_orders_active_ticket` 约束）且其 `requires_manual_review=false`；新 `dispatch_records.rework_order_id` 指向它 | `TICKET_DISPATCHED` |
| **A15** | `MANUAL_REVIEW` → `PENDING_DISPATCH` / `REWORK_PENDING_DISPATCH` | ADMIN | `POST /tickets/{id}/review-resolve`（`resolution=APPROVE_REASSIGN`） | `reason` 必填；返修单置 `CLOSED_BY_REVIEW` + `manual_review_decision=APPROVE_REASSIGN`；票回落待派工并**清空** `manual_review_reason` | `MANUAL_REVIEW_RESOLVED` |
| **A16** | `MANUAL_REVIEW` → `CLOSED` | ADMIN | 同上（`resolution=SPECIAL_CLOSE`） | `reason` 必填；仅用于「现场已由第三方处置 / 点位已撤除」等现实终结；返修单置 `CLOSED_BY_REVIEW`；`closed_at=now()` | `MANUAL_REVIEW_RESOLVED` |
| **A17** | `MANUAL_REVIEW` → `CANCELLED` | ADMIN | 同上（`resolution=TERMINATE_CANCEL`） | `reason` 必填（写入 `cancel_reason`）；返修单置 `CANCELLED`；释放点位防重锁 | `MANUAL_REVIEW_RESOLVED` + `TICKET_CANCELLED` |

> A1/A2 是「无 → 初态」入口，**不占 from 状态**。**合法边共 15 条**（A3–A17），跨 9 个状态。

### 1.3 无死路证明（逐状态穷举出边）

| 状态 | 出边 | 可达终态 | 自动路径 | 人工兜底入口 |
|---|---|---|---|---|
| `AI_CANDIDATE_PENDING` | A3 / A4 | ✅ 直连 | TTL 清理任务（`ai_candidate_ttl_hours`，默认 72h）自动置 `CANCELLED` + `AI_CANDIDATE_EXPIRED` 通知 | 巡查员 `cancel` |
| `PENDING_DISPATCH` | A5 / A6 | ✅ 直连 | 无（等管理员）；**响应 SLA 超时只标记 `is_sla_overdue` 并发通知，不改状态** | 管理员 `dispatch` / `cancel` |
| `DISPATCHED` | A7 / A8 | ✅ 经 A7→A9→A11 | SLA 扫描：`accept_deadline_at < now()` → 派工 `TIMEOUT_REASSIGNED` + 票回 `PENDING_DISPATCH` + 通知管理员 | 管理员 `reassign` |
| `IN_REPAIR` | A9 / A10 | ✅ | SLA 扫描：`repair_deadline_at < now()` → 标记 `overdue_stage=REPAIR` + 通知（**不改状态**） | 维修员 `escalate`；管理员 `reassign` |
| `PENDING_ACCEPTANCE` | A11 / A12 / A13 | ✅ 直连 | 无自动（**验收必须人工，红线：机器不得代替巡查员验收**） | 巡查员 `acceptance` |
| `REWORK_PENDING_DISPATCH` | A14 | ✅ 经 A14→A7→A9→A11 | 无 | 管理员 `dispatch` |
| `MANUAL_REVIEW` | A15 / A16 / A17 | ✅ 两个直连终态 | 24h 未处置发催办通知（最多 3 次后进死信 → 管理员可见） | 管理员 `review-resolve` |
| `CLOSED` | — 终态 | — | — | 只读 |
| `CANCELLED` | — 终态 | — | — | 只读 |

**结论：9 个状态 0 死路。** 4 个状态有自动路径（`AI_CANDIDATE_PENDING`/`DISPATCHED`/`IN_REPAIR`/`MANUAL_REVIEW`）；其余 5 个由人工端点驱动但**每个都有明确人工入口**。

> **`PENDING_DISPATCH`/`PENDING_ACCEPTANCE`/`REWORK_PENDING_DISPATCH` 是「等人操作」，不是「无法操作」**——区别在于后者没有任何端点。`auditor` 维度 3 会逐状态检查这一点。

### 1.4 明确**不允许**的转移（服务层白名单外的黑名单，越界即 `422 INVALID_STATE_TRANSITION`）

```
PENDING_DISPATCH   → CLOSED                （不许跳过维修与验收直接关闭）
PENDING_DISPATCH   → PENDING_ACCEPTANCE    （不许跳过维修）
DISPATCHED         → PENDING_ACCEPTANCE    （不许跳过接单）
DISPATCHED         → CLOSED
IN_REPAIR          → CLOSED
IN_REPAIR          → REWORK_PENDING_DISPATCH
PENDING_ACCEPTANCE → PENDING_DISPATCH
CLOSED             → *                     （终态不可复活）
CANCELLED          → *                     （终态不可复活）
*                  → AI_CANDIDATE_PENDING  （只能作为初始态）
```

> **`IN_REPAIR → REWORK_PENDING_DISPATCH` 这条禁令必须特别说明**：若允许维修员把自己直接推进返修，`rework_count` 就失去「验收被驳回次数」这个唯一语义，**返修阈值机制立刻失效**。维修员修不好的合法表达是 `escalate`（A10）。这是典型「看起来方便、实际毁掉一个不变量」的设计陷阱——`MUT-09` 变异卡专门测它。

---

## 2. 状态机 B：派工记录（`dispatch_records.status`，7 态）

### 2.1 转移边（**7 行编号** = 1 条入口行 + **6 条合法边**）

| # | from → to | 触发者 | 端点 | 守卫 |
|---:|---|---|---|---|
| **B1** | （无）→ `PENDING_ACCEPT` | ADMIN | `/tickets/{id}/dispatch`、`/reassign` | 见 A6/A14；同票无活跃派工 |
| **B2** | `PENDING_ACCEPT` → `ACCEPTED` | TECHNICIAN（受派人） | `/dispatches/{id}/accept` | 未过 `accept_deadline_at`；`accepted_at=now()` |
| **B3** | `PENDING_ACCEPT` → `REJECTED` | TECHNICIAN（受派人） | `/dispatches/{id}/reject` | `reject_reason` 必填；`rejected_at=now()` |
| **B4** | `PENDING_ACCEPT` → `TIMEOUT_REASSIGNED` | **系统**（SLA 扫描） | `POST /sla/scan` | `now() > accept_deadline_at`；**不做「等一会儿」的模糊判定** |
| **B5** | `ACCEPTED` → `COMPLETED` | TECHNICIAN（受派人） | `/dispatches/{id}/complete` | 见 A9 三项守卫 |
| **B6** | `ACCEPTED` → `ESCALATED` | TECHNICIAN（受派人） | `/dispatches/{id}/escalate` | `escalate_reason` 必填 |
| **B7** | `PENDING_ACCEPT`/`ACCEPTED` → `CANCELLED` | ADMIN | `/reassign` 内部；`/tickets/{id}/cancel` | 改派时旧派工置 `CANCELLED` 再建新一条；票撤销时若无派工则跳过 |

**终态 5 个**：`REJECTED` / `COMPLETED` / `TIMEOUT_REASSIGNED` / `ESCALATED` / `CANCELLED`
**活跃态 2 个**：`PENDING_ACCEPT` / `ACCEPTED` ——`uq_dispatch_records_active_ticket` 恰好只约束这 2 个。

### 2.2 无死路证明

- `PENDING_ACCEPT` 出边 4 条，其中 **B4 由定时扫描保证一定有人推**（不需要任何人操作）→ 必然离开。
- `ACCEPTED` 出边 3 条（B5/B6/B7），**B7 由管理员 `reassign` 保证人工可达**。不存在「接单后维修员消失且无人能改派」——`reassign` 对 `ACCEPTED` 生效。
- 五个终态均不可复活（`cancel` 与再派工都是**新插入**一行，历史保留）。

> **为什么派工记录需要独立状态机，而不复用票状态**：一张票可有多条派工记录（拒单、超时改派、人工改派）。票在 `PENDING_DISPATCH` 时历史派工可能已有 3 条 `REJECTED`。把派工状态塞进票里，就会丢掉「谁拒的、为什么拒、拒了几次」这条审计链。

---

## 3. 状态机 C：返修单（`rework_orders.status`，8 态）

### 3.1 转移边（**10 行编号** = 2 条入口行 + **8 条合法边**）

| # | from → to | 触发者 | 守卫 |
|---:|---|---|---|
| **C1** | （无）→ `PENDING_DISPATCH` | INSPECTOR（验收驳回） | `round_no = rework_count(new)` ≤ `max_rework_rounds`；`reject_reason` 快照自 `acceptance_records.comment`；同票无活跃返修单 |
| **C2** | （无）→ `IN_MANUAL_REVIEW` | INSPECTOR（验收驳回，超阈值） | `round_no > max_rework_rounds`；`requires_manual_review=true`；票同事务置 `MANUAL_REVIEW` |
| **C3** | `PENDING_DISPATCH` → `DISPATCHED` | ADMIN | 新 `dispatch_records.rework_order_id` 指向本单 |
| **C4** | `DISPATCHED` → `IN_REPAIR` | TECHNICIAN | 同 B2 |
| **C5** | `IN_REPAIR` → `PENDING_ACCEPTANCE` | TECHNICIAN | 同 B5（含电气安全检查硬门禁） |
| **C6** | `PENDING_ACCEPTANCE` → `PASSED` | INSPECTOR（验收通过） | 同 A11；`closed_at=now()` |
| **C7** | `PENDING_ACCEPTANCE` → `IN_MANUAL_REVIEW` | INSPECTOR（再次驳回且超阈值） | `requires_manual_review=true` |
| **C8** | `PENDING_ACCEPTANCE` → `CANCELLED` | INSPECTOR（再次驳回，未超阈值） | 本单让位给**下一轮新返修单**（C1 插入 `round_no+1`）；`cancelled` 语义是「本轮结束，转下一轮」 |
| **C9** | `IN_MANUAL_REVIEW` → `CLOSED_BY_REVIEW` | ADMIN | `manual_review_decision ∈ {APPROVE_REASSIGN, SPECIAL_CLOSE}`；`decided_by/at` 必填 |
| **C10** | `IN_MANUAL_REVIEW` → `CANCELLED` | ADMIN | `manual_review_decision=TERMINATE_CANCEL` |

**终态 3 个**：`PASSED` / `CLOSED_BY_REVIEW` / `CANCELLED`
**活跃态 5 个**：`PENDING_DISPATCH` / `IN_MANUAL_REVIEW` / `DISPATCHED` / `IN_REPAIR` / `PENDING_ACCEPTANCE` ——`uq_rework_orders_active_ticket` 约束这 5 个。

### 3.2 「三轮返修」完整时序（超阈值的**确切**行为，E2E 可逐字复现）

```
round=1：维修完成 → 巡查员驳回
         rework_orders#1 PENDING_DISPATCH，票 REWORK_PENDING_DISPATCH   （1 ≤ 2，正常返修）
         派工 → 维修完成 → 巡查员驳回
         rework_orders#1 CANCELLED（转下一轮）
         rework_orders#2 PENDING_DISPATCH，票 REWORK_PENDING_DISPATCH   （2 ≤ 2，仍正常）
         派工 → 维修完成 → 巡查员驳回
         round=3 > max_rework_rounds(2)
         rework_orders#2 CANCELLED
         rework_orders#3 IN_MANUAL_REVIEW + requires_manual_review=true
         票 MANUAL_REVIEW + manual_review_reason='rework_threshold_exceeded'
         通知全部 ADMIN（event_type=REWORK_MANUAL_REVIEW_REQUIRED）
```

> 🔴 **关键点**：**超阈值时仍然生成第 3 张返修单**（不是不生成），只是它一出生就是 `IN_MANUAL_REVIEW`。
> 这样「发生过几次驳回」在 `rework_orders` 里**永远可数**，不需要依赖 `fault_tickets.rework_count` 这个冗余列做审计（冗余列只用于 DB CHECK 与索引兜底）。**两处口径一旦不同即视为缺陷**——迁移期断言 4 就是在守这条。

### 3.3 无死路证明

- `PENDING_DISPATCH` → C3 由管理员派工推进（票侧待派工队列会显示它，不会丢）。
- `DISPATCHED` / `IN_REPAIR` → 与状态机 B **完全同步推进**（返修单状态跟着票与派工走，**不独立演化**）。
- `IN_MANUAL_REVIEW` → C9/C10 两个人工出口，且 `MANUAL_REVIEW` 催办通知保证管理员被提醒。

---

## 4. 状态机 D：通知任务（`notification_tasks.status`，5 态）—— 防静默失败

| # | from → to | 触发者 | 守卫 |
|---:|---|---|---|
| **D1** | （无）→ `PENDING` | 业务事务内 | **与业务事件同事务插入**——不靠事后补救；业务成功但通知没生成 = 静默失败 |
| **D2** | `PENDING` → `PROCESSING` | worker | `next_retry_at IS NULL OR next_retry_at <= now()`；`UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED)` 取任务（避免多 worker 抢同一条） |
| **D3** | `PROCESSING` → `SENT` | worker | 投递成功；`sent_at=now()` |
| **D4** | `PROCESSING` → `FAILED` | worker | 投递失败；`attempt_count += 1`；`next_retry_at = now() + backoff(attempt_count)`（1m/5m/15m）；`last_error` 记录 |
| **D5** | `FAILED` → `PENDING` | worker（重试） | `attempt_count < max_attempts` |
| **D6** | `FAILED` → `DEAD_LETTER` | worker | `attempt_count >= max_attempts`；**同时给全部 ADMIN 发 `NOTIFICATION_DEAD_LETTER`**（不能让死信自己静默堆着） |
| **D7** | `DEAD_LETTER` → `PENDING` | ADMIN | `POST /notifications/{id}/retry`：`attempt_count` 归零、清 `last_error`。**死信的唯一自动恢复出口** |
| **D8** | `DEAD_LETTER` → `SENT` | ADMIN | `POST /notifications/{id}/acknowledge`：`acknowledged_by/at` 必填语义；**不改变「投递失败」的审计事实**，只表示人已知晓 |

### 4.1 `PROCESSING` 卡死的自愈规则（**必须有，否则就是死路**）

```sql
-- updated_at 超过 10 分钟仍是 PROCESSING → 视为 worker 已死，退回 PENDING 并计一次 attempt
UPDATE notification_tasks
SET status='PENDING', attempt_count = attempt_count + 1, next_retry_at = now()
WHERE status='PROCESSING' AND updated_at < now() - interval '10 minutes'
  AND attempt_count < max_attempts;
```

> **没有这条规则的后果**：`PROCESSING` 会变成 `TEAM.md` §5.2 明确禁止的形态——「状态存在但没有自动恢复路径」的死路。这是本设计唯一一个「worker 崩溃后必须靠扫描自愈」的状态。

### 4.2 无死路证明（**通知机不是「没有死路问题」，而是「它的死路有人工入口」**）

**逐状态穷举出边**（判据同 §1.3 / §2.2 / §3.3：**出边 = 0 即终态**）：

| 状态 | 出边 | 可达终态 | 自动路径 | 人工兜底入口 |
|---|---|---|---|---|
| `PENDING` | D2 `→ PROCESSING` | ✅ `SENT` | worker 轮询 | — |
| `PROCESSING` | D3 `→ SENT` / D4 `→ FAILED` | ✅ `SENT` | §4.1 自愈扫描（10 min） | — |
| `SENT` | **— 终态（吸收态）** | — | — | **只读** |
| `FAILED` | D5 `→ PENDING` / D6 `→ DEAD_LETTER` | ✅ `SENT`（经 D5） | 退避重试 1m / 5m / 15m | — |
| `DEAD_LETTER` | D7 `→ PENDING` / D8 `→ SENT` | ✅ `SENT` | ⚠️ **无自动路径** | **D7 `POST /notifications/{id}/retry`**、**D8 `.../acknowledge`** |

**结论：0 死路**。5 个状态全部可达终态 `SENT`，**无任何「只能进不能出且到不了终态」的状态**。

> 🔴 **`SENT` 是终态，但这句话曾被安到错误的对象上**（`audits/stage1-audit.md` P0-2）：原文把「`SENT` 可因死信重试复位，非终态」当作「通知机无终态」的理由。**那是把 D7 的真实机制挂到了错误的状态上**——D7 `DEAD_LETTER → PENDING` 复位的是**死信**，与 `SENT` 无关；`SENT` 一旦到达就**永不离开**（全库零命中任何以 `SENT` 为 `from` 的转移行）。**修正后 `SENT` 计入终态，总数 11。**
>
> 💡 **这里才是真正的设计要点**：不能因为「终态不可复活」就认为通知机安全。`DEAD_LETTER` 若是吸收态，死信会永久静默堆积——所以 D6 在进死信的**同一个动作**里给全部 ADMIN 发 `NOTIFICATION_DEAD_LETTER`，并配 D7/D8 两个人工出口。**「无死路」靠的不是终态定义，而是每个非终态都有下一步。**

### 4.3 为什么 `SENT` 必须是终态而不是可复位状态

| 若允许 `SENT → *` | 后果 |
|---|---|
| 允许 `SENT → PENDING`（「重发」） | **同一业务事件重复通知**，且 `sent_at` 被覆盖 → 审计失去「何时真正送达」的事实 |
| 允许 `SENT → FAILED` | 已送达可被改写为失败，`audit_logs` 与通知事实**互相矛盾** |
| 想要重发 | **应新插一行** `notification_tasks`（新 `event_type` 或新 `recipient`），与「派工取消/再派工是新插入一行」的纪律（§2.2）一致 |

> 这条与 `fault_tickets` 的「终态不可复活，再修必须另开新单」（§1.1）是**同一条设计原则的两次应用**：**已发生的事实不可改写，只能追加。**

---

## 5. 跨表不变量（I1–I8，必须同事务维护）


| # | 不变量 | 违反后果 | 守它的机制 |
|---:|---|---|---|
| **I1** | `fault_tickets.status ∈ {DISPATCHED, IN_REPAIR}` ⟺ 恰有 **1** 条 `dispatch_records.status ∈ {PENDING_ACCEPT, ACCEPTED}` | 双人接同一单 / 无人接单却显示已派 | `uq_dispatch_records_active_ticket`（DB）+ 服务层同事务 |
| **I2** | `fault_tickets.status = PENDING_ACCEPTANCE` ⟺ 恰有 1 条 `repair_processes` 且 `electrical_safety_checked = true` | 跳过电气安全检查进验收 | `ck_repair_processes_safety_checklist_complete` + 服务层 `422` |
| **I3** | `fault_tickets.rework_count` = `count(rework_orders WHERE ticket_id=…)` | 返修阈值失效 | 同事务 `+1` + 迁移期断言 4 |
| **I4** | `fault_tickets.status = MANUAL_REVIEW` ⟹ `manual_review_reason IS NOT NULL` | 挂起原因丢失，无法处置 | `ck_fault_tickets_manual_review_reason_required` |
| **I5** | `dispatch_records.region_id = fault_tickets.region_id = lighting_points.region_id` | 区域统计与越权判定错位 | 服务层赋值 + 迁移期断言 1/2 |
| **I6** | 非终态票最多 **1** 条活跃返修单 | 多轮返修并发，轮次号错乱 | `uq_rework_orders_active_ticket` |
| **I7** | 每个状态变更必须有且仅有 **1** 条 `audit_logs`（`action` 与转移表一一对应） | 「谁改的」不可追溯 | 见 §5.1 |
| **I8** | 每个状态变更必须产生对应 `notification_tasks` | 当事人在系统外才知道自己被派单 | 见 §5.1 |

### 5.1 I7/I8 的实现纪律（**防绕过，阶段一即写死**）

`fault_tickets` / `dispatch_records` / `rework_orders` 的 `status` 更新**只允许**经由：

```
backend/app/services/state_machine.py 的 transition_*() 函数
```

**实现期任何 `session.execute(update(...).values(status=...))` 出现在 `state_machine.py` 之外，一律视为缺陷并要求改回。**

> **为什么必须写成硬规则**：I7/I8 靠 code review 记不住——三个月后有人为了「快速修个 bug」直接 `UPDATE status`，审计和通知就静默漏了。**这是唯一能防止不变量随实现漂移的办法：让违规在代码结构上显眼。**

---

## 6. 机制一：区域授权派单（**推荐与指派双重硬门禁**）

### 6.1 双层模型（RBAC + 区域授权）

| 层 | 判定 | 失败码 |
|---|---|---|
| 第 1 层 RBAC | 角色是否允许该动作 | `403 FORBIDDEN_ROLE` |
| 第 2 层 区域授权 | 操作人对目标区域是否有对应 `scope_type` | `403 FORBIDDEN_REGION_SCOPE` |
| 账号状态 | `status == ACTIVE` | `403 ACCOUNT_DISABLED` |

**`scope_type` 三类**：`INSPECT`（巡查）/ `REPAIR`（维修）/ `MANAGE`（管理）。授权带 `valid_from`/`valid_to`，**过期即失效**。

### 6.2 三处都必须强校验（缺一处即为「假门禁」）

| # | 位置 | 校验内容 |
|---:|---|---|
| ① | **推荐列表** `GET /tickets/{id}/dispatch-candidates` | 过滤掉无 `REPAIR` 授权 / 授权过期 / 账号停用 / 角色不符 / 在途超限者，进 `excluded[]` 并给 `reason` |
| ② | **手动指派** `POST /tickets/{id}/dispatch` | 后端重新查一遍授权（**绝不信前端传来的列表**） |
| ③ | **接单** `POST /dispatches/{id}/accept` | 再校验一次（防授权在派工后被撤销） |

> ⚠️ **只做第 ① 处是典型假门禁**：前端下拉里没有这个人，不代表直接 `curl` 调用不能派给他。**推荐是 UI 便利，不是权限边界。**

### 6.3 `403` 与 `422` 的判定分工（**`tester` 点名必须写进本文件**）

| 情形 | 状态码 | `code` | 谁不合格 |
|---|---:|---|---|
| **操作人自己**无角色 | `403` | `FORBIDDEN_ROLE` | 请求者 |
| **操作人自己**无该区域授权 | `403` | `FORBIDDEN_REGION_SCOPE` | 请求者 |
| 操作人账号被停用 | `403` | `ACCOUNT_DISABLED` | 请求者 |
| **被指派的目标维修员**不合格 | `422` | `DISPATCH_TECHNICIAN_NOT_ELIGIBLE` | 请求指定的对象 |
| 被指派维修员在途超限 | `422` | `TECHNICIAN_OVERLOADED` | 请求指定的对象 |

**判据一句话：`403` 说的是「你不能这么做」，`422` 说的是「你要求我做的这件事，参数本身不成立」。**

> 🔴 **这条必须写死，否则阶段二极易被「统一成 403」改回去**——把「被指派人不合格」写成 `403` 会让管理员以为是自己没权限，而实际是自己选错了人，排查方向完全相反。

### 6.4 `excluded[].reason` 五值枚举

| `reason` | 含义 |
|---|---|
| `NO_REGION_SCOPE` | 该维修人员未获此运维区域的维修授权 |
| `SCOPE_EXPIRED` | 区域授权已过期 |
| `ACCOUNT_INACTIVE` | 该账号已停用 |
| `WRONG_ROLE` | 该账号不是维修人员 |
| `OVERLOADED` | 当前在途工单已满 |

---

## 7. 机制二：故障分级 `P1`–`P4` + SLA 时限

### 7.1 命名依据声明（**必读，防止被当成无依据阈值**）

> **`P1`–`P4` 不是中文规范用法。** 中文应急/市政体系的等级命名是「**特级 / 一级 / 二级 / 三级**」或「**特急 / 加急 / 普通**」；`P1`–`P4` 是国际 IT/运维实践（Priority 1–4）的命名习惯。
>
> **本系统的定位：借国际设施管理实践命名，时限对齐 `T/CMEA 21—2021` 与地方规定。**
> - **命名**：沿用 `P1`–`P4`——因为全库枚举、`business_rules.priority_thresholds` 键名、前端 `data-priority` 都以它为契约，且国际实践对开发者更直观。
> - **时限**：对齐 `T/CMEA 21—2021`《城市道路照明设施运行维护技术标准》（中国市政工程协会团体标准，2021-12-01 实施）§4.4.5 的行业基准，而非凭空取值。

**`T/CMEA 21—2021` §4.4.5 原文**：

> 「设施故障报修响应时间应符合下列规定：1 一般故障报修不应超过 24h；2 系统性故障和重大故障报修应在 2h 内到达现场处理，并应采取临时措施亮灯，因设备和器件损坏原因应在 48h 内修复；3 强台风、强降雨等不可抗力因素造成的严重故障或设施损坏，应于 5 天内修复。」

出处：<https://cgj.sz.gov.cn/attachment/1/1238/1238599/10388154.pdf>（标准全文 PDF）

### 7.2 `level_code` 与 `display_name` **必须分离**

| 概念 | 列/字段 | 内容 | 可否改 |
|---|---|---|---|
| **稳定标识** | `priority`（`varchar(4)`）/ `level_code` | `P1`/`P2`/`P3`/`P4` | ❌ **不可改**——代码、DB 枚举、`business_rules` 键名、前端 `data-priority` 全依赖它 |
| **可配置显示名** | `display_name` | 出厂中文 `特急`/`加急`/`普通`/`观察` | ✅ 管理员可在业务规则页改 |

> **「标注解决「合规」，表驱动解决「可改」」**——这两件事必须同时做：
> - **标注**（§7.1 的命名依据 + §7.3 的规范出处）解决「凭什么用这个时限」的合规质疑。
> - **表驱动**（`level_code` 与 `display_name` 分离、时限存 `business_rules`）解决「用户想改名字/改时限」的成本问题。
>
> **若未来改成中文等级命名**：只需改 `display_name` 这批数据 + 一次迁移，**算法与阈值数值完全不动，代码零改动，E2E 断言零影响**（`data-code` 断言的是错误码不是等级名）。这是刻意保持的解耦。

🔴 **硬要求：故障等级不得硬编码为代码里的 enum 字面量**，必须由 `business_rules` 表驱动。`MUT-05` 变异卡把 `return computed_priority` 改成 `return "P3"` 就是为了抓这种硬编码。

### 7.3 四级定义与出厂时限（**全部标注来源**）

| 优先级 | `display_name` 出厂值 | 语义 | 响应时限 | 修复时限 | 与 `T/CMEA 21—2021` §4.4.5 的关系 |
|---|---|---|---:|---:|---|
| `P1` | 特急 | 有电气/公共安全风险，或主干道 + 完全灭灯 | 15 分钟 | 2 小时 | **严于**规范（规范要求 2h 内**到场**、48h 修复；本系统按 2h 内**修复**设定） |
| `P2` | 加急 | 主干道/快速路故障，或重要设施破损 | 30 分钟 | 8 小时 | **介于**规范两级之间。可按地方规定下调 |
| `P3` | 普通 | 一般道路灭灯/遮挡 | 2 小时 | 24 小时 | **恰好对齐**规范「一般故障报修不应超过 24h」 |
| `P4` | 观察 | 白天疑似常亮、非关键点位轻微异常等观察项 | 8 小时 | 72 小时 | 规范无对应条款（属观察项，非报修故障）。**纯工程初值，无规范依据** |

**其他规范依据（存档）**：
- **深圳令第 309 号第 24 条**：单盏灭灯 24h、线路设备损坏 3 日。
- **`DB11/T 1876-2021`**（北京）§5.5.6 / §8.2：地方性更严要求。

> ## ⚠️ 上表 4 行时限**全部是「工程初值，未标定」**
>
> - `P3` 的 24h 修复**对齐**规范；`P1` 的 2h 修复**严于**规范；`P2` 是**折中值**；`P4` **无规范依据**。
> - **不得**把本表写成「业务规定」或「行业标准要求」。必须逐条标注来源，并写明「**待按 `T/CMEA 21—2021` 与地方规定标定后填入**」。
> - 时限是 `business_rules['sla_policy']` 的**可配置默认值，不是代码常量**。上表是「出厂默认」，可在业务规则页调整。
> - 标定方法见 §7.7。

### 7.4 打分算法（唯一权威）

四因子各自归一化到 0–100：

```
score = 100 * ( w_fault  * S_fault_type
              + w_road   * S_road_level
              + w_asset  * S_asset_importance
              + w_safety * S_safety_flag )

默认权重 (w_fault, w_road, w_asset, w_safety) = (0.45, 0.25, 0.20, 0.10)
```

| 因子 | 取值来源 | 取值表 |
|---|---|---|
| `S_fault_type` | `fault_type_score[fault_type]` | `ELECTRIC_HAZARD=100` / `FIXTURE_DAMAGED=80` / `LAMP_OUT=55` / `FLICKERING=40` / `OCCLUDED=35` / `OTHER=20` |
| `S_road_level` | `road_level_score[lighting_points.road_level]` | `EXPRESS=100` / `MAIN=65` / `BRANCH=35` |
| `S_asset_importance` | `asset_importance_score[facility_assets.importance]` | `CRITICAL=100` / `HIGH=65` / `NORMAL=35`；**票上 `facility_asset_id IS NULL` 时取 35**（与 `NORMAL` 同级，**不是 0**——「没登记资产」不等于「不重要」） |
| `S_safety_flag` | `safety_flag_score["true"/"false"]` | `true` ⟺ `fault_type='ELECTRIC_HAZARD'` 或巡查员确认时勾选「存在安全隐患」 |

### 7.5 分级映射

```
priority = 第一个满足 score >= priority_thresholds[p] 的 p（按 P1→P4 顺序，P4 阈值 0 兜底）
默认阈值：P1 >= 85, P2 >= 65, P3 >= 40, P4 >= 0
```

**必须校验阈值单调递减**：`P1 > P2 > P3 >= 0` 且 `P4 == 0`。否则会出现「得分 90 却被判 P3」这类不可解释结果 → Pydantic 校验规则载荷时强制检查，`422 INVALID_RULE_PAYLOAD`。

### 7.6 触发时机（**只有 3 个**）

| 时机 | 端点 | 说明 |
|---|---|---|
| 人工报障 | `POST /tickets` | 创建时即分级 |
| AI 候选确认 | `POST /tickets/{id}/confirm` | 确认后分级（`AI_CANDIDATE_PENDING` 期间 `priority IS NULL`） |
| 管理员手工改级 | `POST /tickets/{id}/regrade` | 必须填 `reason`；**允许覆盖**，因为现场判断优先于公式 |

**`regrade` 规则**：
- 旧 `grading_snapshot_json` 保留于审计（`business_snapshot_json` 记 `from_priority`/`to_priority`/`score`）。
- 重算两个 SLA deadline（`response_deadline_at` 起点是分级时间；`repair_deadline_at` 起点是**首次接单时间**）。
- 若改级后 deadline 已过，**立即**标记 `is_sla_overdue=true`（不等到下次扫描）。

### 7.7 SLA deadline 公式（唯一）

```
T_grade  = 分级完成时刻（tickets.confirmed_at / created_at / regrade 时刻）
T_accept = 首次接单时刻（该票第一条 ACCEPTED 派工的 accepted_at）

response_deadline_at = T_grade  + sla_policy[priority].response_minutes
repair_deadline_at   = T_accept + sla_policy[priority].repair_minutes    （未接单时为 NULL）
```

> **`repair_deadline_at` 为什么依赖接单时刻而不是分级时刻**：维修时限是「给维修员的作业窗口」，不是「从报障到修好的总时长」。用分级时刻算会让「管理员迟迟不派工」吃掉维修员的作业时间，导致维修员一接单就已超时——**这会让准时率指标彻底失去意义**。

> **改派时 `repair_deadline_at` 不重算。** 改派不延长维修窗口（否则可无限改派刷时限）。管理员认为合理时可通过 `regrade` 调整优先级来延长，且留审计。

### 7.8 SLA 超时：标记，**不自动改状态**

`POST /sla/scan`（内部定时，默认每分钟；也可手动触发）执行三段：

```sql
-- ① 响应阶段超时：票还没派出去 / 派了没人接
UPDATE fault_tickets
SET is_sla_overdue = true, overdue_stage = 'RESPONSE', overdue_marked_at = now()
WHERE status IN ('PENDING_DISPATCH','REWORK_PENDING_DISPATCH','DISPATCHED')
  AND response_deadline_at IS NOT NULL AND response_deadline_at < now()
  AND is_sla_overdue = false;
-- 对每行：发 DISPATCH_RESPONSE_TIMEOUT 通知给区域管理员

-- ② 修复阶段超时：维修员接单后修不完
UPDATE fault_tickets
SET is_sla_overdue = true, overdue_stage = 'REPAIR', overdue_marked_at = now()
WHERE status = 'IN_REPAIR'
  AND repair_deadline_at IS NOT NULL AND repair_deadline_at < now()
  AND is_sla_overdue = false;
-- 对每行：发 REPAIR_SLA_TIMEOUT 通知给管理员 + 维修员

-- ③ 派工接单超时：派工记录超时未接 → 自动改派回待派工
UPDATE dispatch_records SET status='TIMEOUT_REASSIGNED', escalated_at=now()
WHERE status='PENDING_ACCEPT' AND accept_deadline_at < now();
-- 再把对应票从 DISPATCHED 推回 PENDING_DISPATCH（走 state_machine.transition_ticket）
```

**关键设计选择：SLA 超时只标记 + 通知 + 派工超时自动回收，不自动降级/关闭。** 理由：
- 自动关闭会**掩盖真实未修复的故障**——公共照明的安全风险不能靠「超时自动关了」消失。
- 自动改派在人工改派之外重复一套逻辑，会产生**两条不同的改派路径**。
- **但派工超时（没人接单）必须自动回收**——否则票卡在 `DISPATCHED` 而维修员从未接单，是纯死路。

> ⚠️ **`WHERE ... AND is_sla_overdue = false` 这个幂等守卫不能删**：`MUT-15` 变异卡就是删它，症状是「连续两次 `scan` 第二次的 `marked_overdue` 从 0 变成 >0」——只有第二次扫描才转红，第一眼很容易漏。

### 7.9 时限标定方法（从「工程初值」到「有依据的值」）

`T/CMEA 21—2021` §4.4.5 只给**三档**，本系统要**四档**，因此必然有 1–2 档是工程外推。标定步骤：

| 步骤 | 动作 | 产出 |
|---:|---|---|
| 1 | 取规范三档作为**锚点**：`P3.repair=24h`（一般故障）、`P1.repair≤2h` 且 `P1.response` 严于「2h 到场」（取 15 分钟，因无路程约束） | 2 个锚点 |
| 2 | 向用户/甲方索取所在地**地方性道路照明运维考核办法**（多数城市有，往往比团体标准更严） | 地方规定原文 |
| 3 | `P2` 落在锚点之间，`P4` 无规范依据 → **由用户业务口径决定，不得由开发者编造** | 用户裁决记录 |
| 4 | 用历史工单数据反推：统计各故障类型**实际修复时长分布**，取 P50/P90 作为 `P2`/`P3` 候选值 | 数据支撑 |
| 5 | 标定结果写入 `business_rules`（`rule_code='sla_policy'`）的**新版本**行，旧版 `is_active=false`。**不改代码、不改迁移** | 一条 `business_rules` 记录 |

> **为什么强调这一步**：`TEAM.md` §4.5（F14）要求断言带出处。把「4 个魔数」写成「业务规定」而不标来源，正是「无依据阈值」。本节的目的是让**任何人拿到本设计都能复现标定过程**，而不是被动接受一组数字。

---

## 8. 机制三：返修闭环 + 超阈值强制复核

### 8.1 参数（`business_rules['rework_policy']`）

```jsonc
{ "max_rework_rounds": 2, "on_exceed_action": "FORCE_ADMIN_REVIEW" }
```

### 8.2 驳回的原子事务（**7 步，缺一即回滚**）

```python
async with session.begin():                       # 单事务，失败全回滚
    # 1. 锁工单行（保证 round_no 计算与 rework_count 递增不并发错乱）
    ticket = await session.scalar(
        select(FaultTicket).where(FaultTicket.id == ticket_id).with_for_update()
    )
    # 2. 校验状态（乐观并发：调用方传 expected_status）
    assert ticket.status == "PENDING_ACCEPTANCE"

    # 3. 写验收记录
    acceptance = AcceptanceRecord(
        ticket_id=ticket.id, repair_process_id=repair_process_id,
        inspector_account_id=actor.id, decision="REJECTED",
        comment=payload.comment,                  # 必填，DB CHECK 也强制
        accepted_at=now,
    )
    session.add(acceptance); await session.flush()

    # 4. 计算轮次
    ticket.rework_count += 1
    round_no = ticket.rework_count

    # 5. 关闭上一张活跃返修单（若存在）—— C8 转移
    prev = await session.scalar(
        select(ReworkOrder).where(
            ReworkOrder.ticket_id == ticket.id,
            ReworkOrder.status.notin_(("PASSED", "CLOSED_BY_REVIEW", "CANCELLED")),
        )
    )
    if prev: prev.status = "CANCELLED"; prev.closed_at = now

    # 6. 生成新返修单（无论是否超阈值都生成，见 §3.2）
    exceed = round_no > max_rework_rounds
    rework = ReworkOrder(
        rework_no=await next_rework_no(session),
        ticket_id=ticket.id, source_acceptance_id=acceptance.id,
        round_no=round_no, region_id=ticket.region_id,
        status="IN_MANUAL_REVIEW" if exceed else "PENDING_DISPATCH",
        reject_reason=payload.comment,
    )
```

> **为什么必须 `with_for_update()`**：两个巡查员同时点「驳回」，不加行锁会让 `round_no` 算成同一个值，两张返修单 `round_no` 重复，`uq_rework_orders_ticket_round` 报冲突或轮次错乱。**这是并发正确性问题，不是性能优化。**

### 8.3 「单事务四件事」清单（`MUT-06a` 专门测它）

驳回必须在**一个事务**内完成，缺一即整笔回滚：

1. 写 `acceptance_records(decision=REJECTED)`
2. `fault_tickets.rework_count += 1`
3. 插入 `rework_orders(round_no = rework_count)`
4. `fault_tickets.status` 置 `REWORK_PENDING_DISPATCH` 或 `MANUAL_REVIEW`

> 变异 `create_rework(...)` → `None` 后，`rework_orders` 少 1 行，且 DB 双 `CHECK` 会报错——**这正是「DB 兜底」的价值：即使服务层漏了，DB 也会拒**。

---

## 9. 视觉降级契约（D1/D2/D3）

### 9.1 D1：人工报障**永不依赖**视觉可用性

`POST /tickets`（`application/json` 或 `multipart/form-data`）**必须始终返回 `201`**，`report_source='MANUAL'`，**不论视觉模块是否健康**。

> 视觉是**增强能力，不是前置条件**。若视觉故障导致不能人工报障，等于整个系统在视觉崩溃时停摆。

### 9.2 D2：视觉失败 → 整笔事务回滚

视觉失败时：`fault_tickets` **0 行**、`fault_evidences` **0 行**、**且已写盘的物理文件必须删除**。

> 「DB 回滚了但文件留在 `uploads/`」是典型的**部分失败假绿**：测试查 DB 全绿，磁盘却在泄漏。

### 9.3 D3：错误必须携带可行动信息

视觉降级错误必须带：`error.details.fallback.action == "MANUAL_REPORT"`、`resubmit_required == true`、`message_key` 非空，**且不含 `evidence_ids`**（没有证据可引用，给了就是骗前端）。

### 9.4 三条互斥的 `503`

| `code` | 触发 | `details` |
|---|---|---|
| `VISION_DISABLED` | `YOLO_INFERENCE_ENABLED=false` 或权重不可用 | — |
| `VISION_TIMEOUT` | 单次推理 > `VISION_TIMEOUT_SECONDS`（默认 60） | — |
| `VISION_INFERENCE_FAILED` | 推理抛异常 | `analysis_stage` ∈ `DECODE`/`INFERENCE`/`POSTPROCESS` |

**互斥**：一次请求只能命中其中一个。UI 文案键：`vision.disabled.manual_fallback` / `vision.timeout.manual_fallback`，锚点 `data-testid="vision-fallback-banner"`。

---

## 10. 智能安全边界（V1–V3，不可协商）

| # | 约束 | 三层强制点 |
|---:|---|---|
| **V1** | AI 候选**必须**由巡查员确认，**无任何自动化路径可跳过** | 前端禁用 + `422 AI_CANDIDATE_NOT_CONFIRMED` + DB `ck_fault_tickets_inspector_confirmation_serialized` |
| **V2** | **模型识别不能替代现场电气安全检查** | 前端禁用 + `422 ELECTRICAL_SAFETY_CHECK_REQUIRED` + DB `ck_repair_processes_safety_checklist_complete` |
| **V3** | 视觉**永不产出** `FLICKERING`/`ELECTRIC_HAZARD` | DB `ck_fault_tickets_ai_source_excludes_unsupported_types`（`report_source='AI_CANDIDATE'` 时禁止这两类） |

> **V3 的洞见来自 `vision-dev`**：闪烁需要**多帧时序**（单张静态图看不出闪），电气隐患需要**现场电气检查**（照片看不出漏电）。这两类必须由人工报障。
>
> 🔴 **`ck_fault_tickets_inspector_confirmation_serialized` 为什么用「三态串行化」而不是简单 `CHECK (confirmed_by_inspector OR status='AI_CANDIDATE_PENDING')`**：后者允许 `confirmed_by_inspector=true` 但 `confirmed_at IS NULL`——**「已确认」却没有确认时间，是自相矛盾的数据**。三态串行化把 `confirmed_by_inspector` / `confirmed_by_inspector_id` / `confirmed_at` / `status` 四者绑死，任一处矛盾都被 DB 拒绝。

---

## 11. 五项核心红线（明确「不做」）

| # | 不做 | 用户原话/依据 |
|---:|---|---|
| **N1** | **不接实时监控** | 「不接实时监控或远程灯控」——`415 MEDIA_VIDEO_STREAM_FORBIDDEN` 是这条红线的技术落点 |
| **N2** | **不做远程灯控**（开关灯/调光/继电器/PLC） | 同上。扫描正则含 `灯控|开关灯|调光|继电器|PLC` |
| **N3** | **不做能耗/电量预测** | 明确超出范围，无对应表与端点 |
| **N4** | **不跳过巡查员确认** | V1 |
| **N5** | **不跳过电气安全检查** | V2 |

> **`MEDIA_VIDEO_STREAM_FORBIDDEN` 与 `UNSUPPORTED_IMAGE_FORMAT` 必须分开**：前者承载红线告警（「疑似有人在接实时监控」），后者是普通格式拒绝。混在一起会让红线告警数字被动图噪声污染。

---

## 12. 待用户裁决清单 → 见 `docs/DECISION.md`

本文件相关的 5 项已并入 **`docs/DECISION.md`**（全项目唯一权威清单）：

| 原编号 | 事项 | `DECISION.md` |
|---|---|---|
| `BL-1` | `P1`–`P4` 时限标定 | **`D-11`** |
| `BL-2` | 中文等级命名是否改用 | **`D-12`** |
| `BL-3` | `max_rework_rounds` 默认值 2 | **`D-14`** |
| `BL-4` | `ai_candidate_ttl_hours` 默认 72h | **`D-15`** |
| `BL-5` | 亮灯率 / 完好率 KPI | **`D-17`** |

> **出处提示**：本文件 §4（机制二·故障分级 + SLA）与 §8（机制三·返修闭环）给出这些参数的**语义与标定方法**（§7.9 五步标定流程）——那是设计内容，不搬走。数值本身的裁决在 `docs/DECISION.md`。

---

## 13. 本文档的诚实边界

**已确定的**：
- 4 套状态机共 **29** 个状态、**36** 条合法边（`A 15 + B 6 + C 8 + D 7`，编号行 42 − 6 条入口行）、**11** 个终态，**0 死路**（逐状态穷举证明见 §1.3 / §2.2 / §3.3 / **§4.2**）。
- 8 条跨表不变量与其 DB 级/服务层级守护机制。
- 三大非 CRUD 机制的完整算法（打分公式、SLA 公式、返修 7 步事务）。
- 时限的规范出处（`T/CMEA 21—2021` §4.4.5 原文 + 深圳令 309 号 + `DB11/T 1876-2021`）。

**没有确定的**：
- **没有任何一行状态机代码被实现**。本文件全部是设计契约。
- **4 个时限值全部未标定**（§7.9 给了标定方法，但标定本身需要用户提供地方规定）。
- 返修阈值、候选 TTL 等均为工程初值。
- §12 的 5 项待用户裁决。

**明确不属于本文档的**：
- 表结构与约束 → `docs/SCHEMA.md`
- 端点与错误码 → `docs/API.md`
- 需求编号与验收判据 → `docs/REQUIREMENTS.md`

---

## 14. 变更记录

| 日期 | 变更 | 触发 |
|---|---|---|
| 2026-11-21 | 初版。4 套状态机 / 32 边 / 10 终态 / 0 死路 + I1–I8 + 三大机制 + Lead 裁决 B 三项 | `task-7` |
| 2026-11-21 | **审计修正**：终态 10 → **11**（`SENT` 计入；原文「`SENT` 可因死信重试复位」系把 D7 `DEAD_LETTER→PENDING` 错挂到 `SENT`）；新增 **§4.2 无死路证明**与 **§4.3 `SENT` 终态理由**；边数 32 → **36** 并删除无定义的「去重后」。**转移边、状态集、机制规则零改动** | `audits/stage1-audit.md` P0-2/P0-3 |
