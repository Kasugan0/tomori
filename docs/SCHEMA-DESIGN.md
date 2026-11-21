> 状态：阶段一定稿候选 —— 待用户审阅裁决（未定稿）

# 数据库结构设计（SCHEMA-DESIGN）

> **文档归属**：`docs-writer`｜**本文件写入域**：`docs/SCHEMA-DESIGN.md`
> **读者**：`backend-dev`（唯一实现依据）、`tester`（DB 约束断言依据）、`auditor`
> **本文件的地位**：**17 张业务表的物理结构、约束、索引、迁移顺序的唯一权威**。

---

## 0. 统计口径分层声明（`docs/team/TEAM.md` §4.6）

**本节的每一个数字都必须连「分子 / 分母 / 排除项」三层一起引用。只写「共 76 个 CHECK」而不给口径，是 `TEAM.md` §4.6 明令禁止的形态。**

| 项 | 分子 | 分母 | 排除项 |
|---|---:|---|---|
| **业务表** | **17** | 本文件 §2 逐表 | **`alembic_version` 不计入**——它是 Alembic 迁移元数据表，不是业务表 |
| **CHECK 约束** | **76** | 17 表 DDL 内**显式声明**的 CHECK | 见 §0.2 排除项 ④（`NOT NULL` 不进 `information_schema.table_constraints`） |
| **外键列数** | **41** | 17 表所有引用列 | **≠ 外键约束数**（见 §0.3 术语陷阱 1） |
| **UNIQUE 约束/唯一索引** | **22** | 具名约束 + 唯一索引 | PG 自动创建的 `*_pkey` 主键索引；列内联 `UNIQUE` 产生的隐式索引（本设计统一用具名约束，已计入本数） |
| **普通（非唯一）索引** | **37** | 手写声明的非唯一索引 | **FK 支撑索引**（PG **不会**为外键自动建索引，本设计已逐表手写声明，计入本数） |
| **部分唯一索引** | **6** | 带 `WHERE` 的唯一索引 | 其中**仅 4 个承担业务防重**（见 §0.3 术语陷阱 2） |
| **非唯一部分索引** | **12** | 带 `WHERE` 的非唯一索引 | 只为缩小扫描面，**不承担唯一性** |

### 0.1 `alembic_version` 的单独分层声明

**`alembic_version` 不是第 18 张业务表。** 它是 Alembic 迁移工具的元数据表，只有一列 `version_num varchar(32) PRIMARY KEY`，记录当前 schema 版本号。

| 项 | 值 |
|---|---|
| 是否业务表 | ❌ 否 |
| 是否计入「17 张表」 | ❌ **不计入** |
| 是否由应用代码访问 | ❌ 否（仅 Alembic 读写） |
| 是否出现在 ER 图 | ❌ 否 |
| 迁移期断言 5 的表述 | 「**排除 `alembic_version` 后**，恰好存在 17 张基础表」 |

> **为什么必须单独声明**：`tester` 的迁移期断言 5 会数 `information_schema.tables`。若不排除 `alembic_version`，数出来是 **18**，与文档的 17 不符——**这个 1 之差会被误判成「多建了一张表」或「文档少写一张」**。

### 0.2 五类排除项（逐条说明为什么不计入）

| # | 排除项 | 为什么不计入 |
|---:|---|---|
| ① | `alembic_version` 迁移元数据表 | 见 §0.1 |
| ② | PG 自动创建的 `*_pkey` 主键索引 | 由 `PRIMARY KEY` 隐含，非手写声明 |
| ③ | FK 支撑索引 | **PG 不会为外键自动建索引**，本设计逐表手写声明 → 计入「普通索引 37」 |
| ④ | `NOT NULL` 约束 | PG 以 `pg_attribute.attnotnull` 表达，**不进 `information_schema.table_constraints`** → 不计入 CHECK 数 |
| ⑤ | 列内联 `UNIQUE` 产生的隐式索引 | 本设计统一用具名约束，已计入「UNIQUE 22」，**不重复计** |

### 0.3 两个术语陷阱（**不区分则对数必然不一致**）

#### 陷阱 1：「外键**列数** 41」 ≠ 「外键**约束数**」

- 「外键列数 41」是**设计期的可数事实**：17 张表里共有 41 个引用其他表的列。
- 实际 FK 约束数**可能不同**：`fault_tickets` 的 6 个 FK 列在 Python 侧可能被合并，或一个复合 FK 覆盖多列。
- **实测口径**：以 `pg_constraint` 的 `contype='f'` 行数为准。迁移落地后若与 41 不同，**差异必须逐条解释并回写本节**。

#### 陷阱 2：「部分唯一索引 6」 ≠ 「业务防重 4」

| 类别 | 数量 | 索引 |
|---|---:|---|
| **业务防重**（防重复业务单据） | **4** | `uq_fault_tickets_active_point_type`、`uq_dispatch_records_active_ticket`、`uq_rework_orders_active_ticket`、`uq_audit_logs_idempotency` |
| **配置态唯一**（防重复配置行） | 2 | `uq_scope_authorizations_account_region_type`（同账号同区域同类型只一条生效授权）、`uq_business_rules_code_active`（同规则只一条活跃版本） |
| 合计 | **6** | — |

> 另有两个易错点：
> - **`uq_rework_orders_ticket_round` 与 `uq_rework_orders_active_ticket` 是两个不同约束，别合并**：前者 `(ticket_id, round_no)` **全时**唯一（轮次号不可重复）；后者 `(ticket_id)` **仅活跃期**唯一（活跃返修单不可重复）。
> - `accounts.is_active` / `maintenance_regions.is_active` 上的部分索引是**非唯一**索引（只为缩小扫描面），历史上曾被误计入「部分唯一索引」，此处已从 6 项中排除。

### 0.4 实测对账命令（迁移落地后必跑）

```sql
-- CHECK 约束数（期望 76）
SELECT count(*) FROM pg_constraint WHERE contype = 'c'
  AND conrelid::regclass::text IN (/* 17 张表 */);

-- 外键约束数（与文档的「外键列数 41」对照，差异须逐条解释）
SELECT count(*) FROM pg_constraint WHERE contype = 'f'
  AND conrelid::regclass::text IN (/* 17 张表 */);

-- 唯一索引数（期望 22，含 6 个部分唯一）
SELECT count(*) FROM pg_indexes WHERE schemaname='public'
  AND indexdef LIKE 'CREATE UNIQUE INDEX%';

-- 普通索引数（期望 37）
SELECT count(*) FROM pg_indexes WHERE schemaname='public'
  AND indexdef NOT LIKE '%UNIQUE%';

-- 业务表数（期望 17，排除 alembic_version）
SELECT count(*) FROM information_schema.tables
WHERE table_schema='public' AND table_type='BASE TABLE'
  AND table_name <> 'alembic_version';
```

---

## 1. 三套编号体系（**绝不混淆**）

### 1.1 三套编号的定义

| 编号 | 来源 | 用途 | 权威性 |
|---|---|---|---|
| **圈号 ①–⑰** | **用户给定清单的顺序** | 业务视角核对（「用户要的 17 张，一张不少」） | 用户清单权威 |
| **章号 §1–§17** | 本文档章节号 = **物理建表序** | 阅读顺序 | 与物理序一致 |
| **物理建表序 1–17** | **外键依赖拓扑序** | **写 Alembic 迁移只看这个** | DDL 执行权威 |

### 1.2 完整映射表（圈号 → 物理表 → 章号 → 物理序）

| 圈号 | 中文表名 | 物理表名 | 章号 | 物理序 |
|---:|---|---|---:|---:|
| ① | 账号 | `accounts` | §4 | 4 |
| ② | 角色 | `roles` | §2 | 2 |
| ③ | 账号角色 | `account_roles` | §5 | 5 |
| ④ | 业务范围授权 | `scope_authorizations` | §6 | 6 |
| ⑤ | 业务规则 | `business_rules` | §7 | 7 |
| ⑥ | 通知任务 | `notification_tasks` | §17 | **17** |
| ⑦ | 操作审计 | `audit_logs` | §16 | **16** |
| ⑧ | 运维区域 | `maintenance_regions` | §1 | 1 |
| ⑨ | 照明点位 | `lighting_points` | §3 | 3 |
| ⑩ | 设施资产 | `facility_assets` | §8 | 8 |
| ⑪ | 故障单 | `fault_tickets` | §9 | 9 |
| ⑫ | 故障证据 | `fault_evidences` | §10 | 10 |
| ⑬ | 派工记录 | `dispatch_records` | §11 | 11 |
| ⑭ | 维修过程 | `repair_processes` | §12 | 12 |
| ⑮ | 材料使用 | `material_usages` | §13 | 13 |
| ⑯ | 验收记录 | `acceptance_records` | §14 | **15** |
| ⑰ | 返修单 | `rework_orders` | §15 | **14** |

### 1.3 三处错位（**必须理解原因，否则迁移必炸**）

| 错位 | 现象 | 原因 |
|---|---|---|
| 第 1 处 | 圈号序 ≠ 章号序 | 用户按业务视角列清单（账号放第 ① 个），物理必须先建 `maintenance_regions`（无依赖） |
| 第 2 处 | **`acceptance_records`（物理 15）与 `rework_orders`（物理 14）互换** | ⑯ 引用 ⑰、⑰ 也引用 ⑯，构成**循环外键**，必须择一先后 |
| 第 3 处 | ⑥⑦（`notification_tasks`/`audit_logs`）被推到物理 16/17 | 它们引用几乎所有业务表，必须最后建 |

> 🔴 **写 Alembic 迁移只看「物理序」，不看圈号。**
> 按圈号写会在建 `acceptance_records`（圈号 ⑯）时报：
> ```
> sqlalchemy.exc.ProgrammingError: (psycopg.errors.UndefinedTable)
> relation "rework_orders" does not exist
> ```
> 因为圈号 ⑰ 的 `rework_orders` 还没建。

### 1.4 物理建表序（**迁移的唯一依据**）

```
1. maintenance_regions
2. roles
3. lighting_points        (→ maintenance_regions)
4. accounts
5. account_roles          (→ accounts, roles)
6. scope_authorizations   (→ accounts, maintenance_regions)
7. business_rules
8. facility_assets        (→ lighting_points)
9. fault_tickets          (→ maintenance_regions, lighting_points, facility_assets, accounts)
10. fault_evidences       (→ fault_tickets, accounts)
11. dispatch_records      (→ fault_tickets, accounts, scope_authorizations, maintenance_regions)
12. repair_processes      (→ fault_tickets, dispatch_records, accounts)
13. material_usages       (→ repair_processes)
14. rework_orders         (→ fault_tickets, maintenance_regions, acceptance_records)
15. acceptance_records    (→ fault_tickets, repair_processes, accounts, rework_orders)
16. audit_logs            (→ accounts)
17. notification_tasks    (→ accounts, maintenance_regions, fault_tickets)
```

---

## 2. 17 张表逐表结构（按圈号序，**含中文名↔英文名映射**）

### 2.1 中文表名 ↔ 英文物理名一一映射（`auditor` 21 项必查之一）

| 圈号 | 中文表名 | 英文物理名 | 一句话职责 |
|---:|---|---|---|
| ① | 账号 | `accounts` | 三角色统一账号；含 `token_version` 用于 O(1) 吊销 |
| ② | 角色 | `roles` | `ADMIN`/`INSPECTOR`/`TECHNICIAN` 字典表 |
| ③ | 账号角色 | `account_roles` | 账号↔角色多对多（本项目一账号一角色，仍建关联表留扩展） |
| ④ | 业务范围授权 | `scope_authorizations` | 账号↔区域↔`scope_type`，带有效期 |
| ⑤ | 业务规则 | `business_rules` | 版本化规则载荷（分级权重/阈值/SLA/返修策略） |
| ⑥ | 通知任务 | `notification_tasks` | 事务内生成、可重试、带死信 |
| ⑦ | 操作审计 | `audit_logs` | 全量写操作审计 + **幂等键存储** |
| ⑧ | 运维区域 | `maintenance_regions` | 区域树（`parent_region_id` 自引用） |
| ⑨ | 照明点位 | `lighting_points` | 灯杆/点位主数据，含经纬度与道路等级 |
| ⑩ | 设施资产 | `facility_assets` | 点位下的具体设备（灯具/灯杆/电源） |
| ⑪ | 故障单 | `fault_tickets` | 主流程载体，9 态状态机 |
| ⑫ | 故障证据 | `fault_evidences` | 静态图片 + AI 判定快照 |
| ⑬ | 派工记录 | `dispatch_records` | 每次派工一行，7 态 |
| ⑭ | 维修过程 | `repair_processes` | 施工记录 + **电气安全检查六件套** |
| ⑮ | 材料使用 | `material_usages` | 维修消耗的物料明细 |
| ⑯ | 验收记录 | `acceptance_records` | `decision` ∈ `APPROVED`/`REJECTED` |
| ⑰ | 返修单 | `rework_orders` | 驳回后生成，8 态，含轮次号 |

### 2.2 逐表约束与索引数量汇总（按圈号序，可直接与 `information_schema` 对账）

| 圈号 | 物理表名 | CHECK | 外键列 | UNIQUE | 普通索引 | 部分唯一索引 |
|---:|---|---:|---:|---:|---:|---|
| ① | `accounts` | 4 | 1 | 2 | 1 | — |
| ② | `roles` | 1 | 0 | 1 | 0 | — |
| ③ | `account_roles` | 0 | 3 | 2 | 1 | — |
| ④ | `scope_authorizations` | 2 | 3 | 1 | 2 | `uq_scope_authorizations_account_region_type` |
| ⑤ | `business_rules` | 2 | 1 | 2 | 0 | `uq_business_rules_code_active` |
| ⑥ | `notification_tasks` | 7 | 4 | 0 | 4 | — |
| ⑦ | `audit_logs` | 4 | 1 | 1 | 6 | `uq_audit_logs_idempotency` |
| ⑧ | `maintenance_regions` | 2 | 1 | 1 | 2 | — |
| ⑨ | `lighting_points` | 6 | 1 | 1 | 2 | — |
| ⑩ | `facility_assets` | 4 | 1 | 1 | 1 | — |
| ⑪ | `fault_tickets` | 14 | 6 | 2 | 7 | `uq_fault_tickets_active_point_type` |
| ⑫ | `fault_evidences` | **8** | 2 | 1 | 1 | — |
| ⑬ | `dispatch_records` | 7 | 5 | 1 | 3 | `uq_dispatch_records_active_ticket` |
| ⑭ | `repair_processes` | 4 | 3 | 1 | 2 | — |
| ⑮ | `material_usages` | 3 | 2 | 1 | 1 | — |
| ⑯ | `acceptance_records` | 3 | 4 | 1 | 2 | — |
| ⑰ | `rework_orders` | 5 | 3 | 3 | 2 | `uq_rework_orders_active_ticket` |
| | **合计** | **76** | **41** | **22** | **37** | **6** |

**逐项求和复算**（任何人可用这几行复核上表）：
- CHECK：`4+1+0+2+2+7+4+2+6+4+14+8+7+4+3+3+5` = **76**
- 外键列：`1+0+3+3+1+4+1+1+1+1+6+2+5+3+2+4+3` = **41**
- UNIQUE：`2+1+2+1+2+0+1+1+1+1+2+1+1+1+1+1+3` = **22**
- 普通索引：`1+0+1+2+0+4+6+2+2+1+7+1+3+2+1+2+2` = **37**

---

## 3. `fault_tickets`（⑪，核心表，36 列 / 14 CHECK / 2 部分唯一）

### 3.1 关键列

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | `bigserial` | PK | — |
| `ticket_no` | `varchar(32)` | NOT NULL, UNIQUE `uq_fault_tickets_ticket_no` | 格式 `FT-YYYYMMDD-NNNN` |
| `region_id` | `bigint` | NOT NULL, FK→`maintenance_regions` | **必须等于其 `lighting_point_id` 的 `region_id`**（冗余列，见 §3.3） |
| `lighting_point_id` | `bigint` | NOT NULL, FK→`lighting_points` | — |
| `facility_asset_id` | `bigint` | NULL, FK→`facility_assets` | 未登记资产时为 NULL（打分取 35，非 0） |
| `report_source` | `varchar(24)` | NOT NULL, CHECK | `AI_CANDIDATE`/`MANUAL_INSPECT`/`MANUAL_ADMIN` |
| `fault_type` | `varchar(24)` | NOT NULL, CHECK | `LAMP_OUT`/`FIXTURE_DAMAGED`/`OCCLUDED`/`ELECTRIC_HAZARD`/`FLICKERING`/`OTHER` |
| `status` | `varchar(32)` | NOT NULL, CHECK | 9 态（见 `BUSINESS-LOGIC.md` §1） |
| `priority` | `varchar(4)` | **NULL**, CHECK | `P1`..`P4`。**`AI_CANDIDATE_PENDING` 期间可空**——分级尚未发生 |
| `grading_score` | `numeric(6,2)` | NULL, CHECK | 0–100 |
| `grading_snapshot_json` | `jsonb` | NULL | 分级时的权重/阈值快照，保证历史可解释 |
| `reporter_account_id` | `bigint` | NOT NULL, FK | — |
| **`confirmed_by_inspector`** | `boolean` | NOT NULL default `false` | 巡查员确认标志（V1 硬门禁） |
| `confirmed_by_inspector_id` | `bigint` | NULL, FK | — |
| `confirmed_at` | `timestamptz` | NULL | — |
| `ai_suggestion_json` | `jsonb` | NULL | AI 建议快照 |
| `ai_max_confidence` | `numeric(4,3)` | NULL, CHECK | 0.000–1.000 |
| `response_deadline_at` | `timestamptz` | NULL | = 分级完成 + `sla_policy.response_minutes` |
| `repair_deadline_at` | `timestamptz` | NULL | = **首次接单** + `sla_policy.repair_minutes`；未接单时 **NULL** |
| `is_sla_overdue` | `boolean` | NOT NULL default `false` | — |
| `overdue_stage` | `varchar(16)` | NULL, CHECK | `RESPONSE`/`REPAIR` |
| `overdue_marked_at` | `timestamptz` | NULL | — |
| `rework_count` | `smallint` | NOT NULL default 0, CHECK ≥0 | **冗余列**，见 §3.3 |
| `dispatch_reject_count` | `smallint` | NOT NULL default 0, CHECK ≥0 | 累计拒单数，≥3 转 `MANUAL_REVIEW` |
| `manual_review_reason` | `varchar(256)` | NULL | `MANUAL_REVIEW` 时**必填**（DB CHECK 强制） |
| `cancel_reason` | `varchar(256)` | NULL | — |
| `cancelled_by_account_id` | `bigint` | NULL, FK | — |
| `cancelled_at` | `timestamptz` | NULL | — |
| `closed_at` | `timestamptz` | NULL | — |
| `created_at`/`updated_at` | `timestamptz` | NOT NULL default `now()` | — |

### 3.2 14 个 CHECK（逐条给名字与语义）

| # | 约束名 | 内容 |
|---:|---|---|
| 1 | `ck_fault_tickets_report_source_enum` | `report_source IN (...)` |
| 2 | `ck_fault_tickets_fault_type_enum` | `fault_type IN (...)` |
| 3 | `ck_fault_tickets_status_enum` | 9 态枚举 |
| 4 | `ck_fault_tickets_priority_enum` | **`priority IS NULL OR priority IN ('P1','P2','P3','P4')`** |
| 5 | `ck_fault_tickets_counters_non_negative` | `rework_count >= 0 AND dispatch_reject_count >= 0` |
| 6 | `ck_fault_tickets_grading_score_range` | `grading_score BETWEEN 0 AND 100` |
| 7 | `ck_fault_tickets_ai_max_confidence_range` | `ai_max_confidence BETWEEN 0 AND 1` |
| 8 | **`ck_fault_tickets_inspector_confirmation_serialized`** | **三态串行化**，详见 §3.4 |
| 9 | **`ck_fault_tickets_ai_source_excludes_unsupported_types`** | `NOT (report_source='AI_CANDIDATE' AND fault_type IN ('FLICKERING','ELECTRIC_HAZARD'))` |
| 10 | `ck_fault_tickets_manual_review_reason_required` | `status <> 'MANUAL_REVIEW' OR manual_review_reason IS NOT NULL` |
| 11 | `ck_fault_tickets_cancel_fields_consistent` | 撤销字段一致性 |
| 12 | `ck_fault_tickets_closed_at_consistent` | **`(status='CLOSED') = (closed_at IS NOT NULL)`** |
| 13 | `ck_fault_tickets_overdue_consistent` | `is_sla_overdue = false OR overdue_stage IS NOT NULL` |
| 14 | `ck_fault_tickets_overdue_stage_enum` | `overdue_stage IS NULL OR overdue_stage IN ('RESPONSE','REPAIR')` |

**部分唯一索引**：`uq_fault_tickets_active_point_type`（`(lighting_point_id, fault_type) WHERE status NOT IN (终态)`）—— 同点位同类型只允许一张活跃单。

### 3.3 两个冗余列的处置纪律

`region_id` 与 `rework_count` 是**刻意的冗余**：

| 冗余列 | 为什么冗余 | 为什么保留 |
|---|---|---|
| `region_id` | 可从 `lighting_point_id → maintenance_regions` 推出 | 区域越权校验与区域统计是**最高频查询**，每次 JOIN 不可接受 |
| `rework_count` | 可从 `count(rework_orders)` 推出 | 返修阈值判断在**热路径**，且 `CHECK` 需要列级可读 |

**纪律：冗余列必须被不变量守住，否则就是灾难。**
- `region_id` ← 迁移期断言 1/2（点位区域一致性、派工技术员当时有 `REPAIR` 授权）
- `rework_count` ← 迁移期断言 4（`rework_count == count(rework_orders)`）

> **若冗余的代价过高**：可以**删掉列并改查询**，但**绝不允许「保留列但不校验」**——那是纯粹的「两处真相」，必然漂移。

### 3.4 `ck_fault_tickets_inspector_confirmation_serialized`（V1 硬门禁的 DB 落点）

```sql
CHECK (
  (confirmed_by_inspector = false
     AND confirmed_by_inspector_id IS NULL
     AND confirmed_at IS NULL
     AND status = 'AI_CANDIDATE_PENDING')
  OR
  (confirmed_by_inspector = true
     AND confirmed_by_inspector_id IS NOT NULL
     AND confirmed_at IS NOT NULL
     AND status <> 'AI_CANDIDATE_PENDING')
)
```

**为什么必须写成「三态串行化」而不是简单版**：

简单的 `CHECK (confirmed_by_inspector OR status='AI_CANDIDATE_PENDING')` 允许：
- `confirmed_by_inspector = true` 但 `confirmed_at IS NULL` → **「已确认」却没有确认时间，自相矛盾的数据**
- `confirmed_by_inspector = true` 但 `status = 'AI_CANDIDATE_PENDING'` → 已确认却还挂在待确认态

三态串行化把 `confirmed_by_inspector` / `confirmed_by_inspector_id` / `confirmed_at` / `status` **四者绑死**，任一处矛盾都被 DB 拒绝。

### 3.5 `ck_fault_tickets_ai_source_excludes_unsupported_types`（V3 的 DB 落点）

```sql
CHECK (NOT (report_source = 'AI_CANDIDATE'
            AND fault_type IN ('FLICKERING', 'ELECTRIC_HAZARD')))
```

> **洞见来自 `vision-dev`**：闪烁需要**多帧时序**（单张静态图看不出闪），电气隐患需要**现场电气检查**（照片看不出漏电）。这两类**必须由人工报障**。即使视觉代码有 bug 错报，DB 也会拒。

---

## 4. `fault_evidences`（⑫，8 CHECK —— **本表是 Lead 裁决的落点**）

### 4.1 关键列与 `ticket_id` 可空性

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | `bigserial` | PK | — |
| **`ticket_id`** | `bigint` | **NULL**, FK→`fault_tickets` | **可空**，见 §4.2 |
| `analysis_id` | `uuid`/`varchar` | NOT NULL | 每图独立分析 ID |
| `evidence_type` | `varchar(32)` | NOT NULL, CHECK | `INSPECTION_PHOTO` 等 |
| `file_path` | `varchar(512)` | NOT NULL | `uploads/{yyyy}/{mm}/{uuid4}.{ext}` |
| `file_size_bytes` | `bigint` | NOT NULL, CHECK | `> 0 AND <= 15728640`（15 MiB） |
| `mime_type` | `varchar(64)` | NOT NULL, CHECK | **仅 `image/jpeg`/`image/png`/`image/webp`** |
| `sha256` | `char(64)` | NOT NULL, CHECK | `~ '^[0-9a-f]{64}$'` |
| `image_width`/`image_height` | `int` | NOT NULL, CHECK | 各 `<= 20000` |
| `ai_result_json` | `jsonb` | NULL, CHECK | 见 §4.4 |
| `max_fused_confidence` | `numeric(4,3)` | NULL, CHECK | baseline 模式上限 0.65 |
| `created_at` | `timestamptz` | NOT NULL default `now()` | 清理任务按此回收 |

### 4.2 🔴 `ticket_id` 为什么可空（**Lead 裁决 #1，必须完整解释**）

#### 原始硬矛盾

视觉分析返回**零个故障候选**时，业务规则是「**不建票**」（白天拍到灭灯不是故障）。但原设计的 `ticket_id NOT NULL` 让这条证据**物理上无法存储**——于是「白天灭灯不误报」这条红线**只是口头承诺，DB 侧无法验证**。

#### 修复方案

`ticket_id` 改为**可空**，并新增第 8 个 CHECK：

```sql
CONSTRAINT ck_fault_evidences_ticket_or_orphan_inspection CHECK (
  ticket_id IS NOT NULL
  OR (evidence_type = 'INSPECTION_PHOTO' AND analysis_id IS NOT NULL)
)
```

**这个 CHECK 的作用**：可空**不等于随便空**。只有「巡查照片 + 有分析 ID」这一种孤儿证据是合法的——即「**系统确实看过这张图并做出了判定**」。其他任何 `ticket_id IS NULL` 的情况被 DB 拒绝。

#### 为什么必须落库而不是全丢弃

| # | 理由 |
|---:|---|
| 1 | 巡查员事后申辩「我明明拍到了」时，能拿出**当时的图 + 当时的 AI 判定**（`ai_result_json`） |
| 2 | 「白天灭灯不误报」这条红线要能**在 DB 侧断点**（`tester` 断言「`fault_tickets` 0 行 + `fault_evidences` 有行 + 点位未被锁」） |
| 3 | 若全丢弃，就没有任何记录能证明系统「看到了但正确抑制」→ **红线变成不可验证的口头承诺** |

#### 必须区分两种「无票」情形

| 情形 | `fault_evidences` | 响应 |
|---|---|---|
| **零候选归档**（正常业务抑制） | 有行，`ticket_id = NULL` | `200` + `{ticket: null, evidences: [...]}` |
| **视觉崩溃**（异常） | **0 行**，且已写盘文件必须删除 | `503 VISION_DISABLED`/`VISION_TIMEOUT`/`VISION_INFERENCE_FAILED` |

> 🔴 **这两者绝不能混**：前者是「系统正常工作并正确抑制」，后者是「系统坏了」。若把零候选也丢弃，就无法在 DB 层区分这两者。

### 4.3 ⚠️ `uq_fault_evidences_sha256_per_ticket` 对 NULL 行**不生效**（已知取舍，**不得写成「已解决」**）

**PG 的 `UNIQUE` 把 NULL 视为互不相同**，因此 `uq_fault_evidences_sha256_per_ticket`（`(ticket_id, sha256)`）**对 `ticket_id IS NULL` 的行完全不起作用**。

**后果**：同一张「白天灭灯」图片上传 N 次 → 产生 **N 行孤儿证据**。

**本设计的处置（明确写死，不得含糊）**：

> **物理文件按 `sha256` 去重落盘；DB 行不去重，重复上传同一张图会产生多行存档，由清理任务按 `created_at` 回收。**

🔴 **绝对不得写成「证据表已去重」**——那是虚假声明。

**为什么不做 DB 去重**（`tables.md` §10 的取舍理由）：
- 零候选证据**不参与任何业务防重**（无票、无点位锁、无流程）。
- 强行去重需要建 `COALESCE(ticket_id, 0)` 的**表达式唯一索引**，把复杂度提到**不值得**的水平。
- 重复孤儿证据是**纯存储成本**，由清理任务按 `created_at` 回收即可。

### 4.4 8 个 CHECK 全表

| # | 约束名 | 内容 |
|---:|---|---|
| 1 | `ck_fault_evidences_evidence_type_enum` | `evidence_type IN (...)` |
| 2 | **`ck_fault_evidences_media_mime_static_image_only`** | `mime_type IN ('image/jpeg','image/png','image/webp')` ← **「不接视频」红线的 DB 落点** |
| 3 | `ck_fault_evidences_file_size_range` | `file_size_bytes > 0 AND <= 15728640` |
| 4 | `ck_fault_evidences_image_dimensions_positive` | 宽高各 `> 0 AND <= 20000` |
| 5 | `ck_fault_evidences_sha256_format` | `sha256 ~ '^[0-9a-f]{64}$'` |
| 6 | **`ck_fault_evidences_ticket_or_orphan_inspection`** | 见 §4.2 |
| 7 | `ck_fault_evidences_baseline_confidence_cap` | baseline 模式 `max_fused_confidence <= 0.65` |
| 8 | `ck_fault_evidences_ai_safety_flags_required` | `ai_result_json IS NULL OR (requires_inspector_confirmation IS TRUE AND replaces_electrical_safety_check IS FALSE)` |

> **CHECK 7（baseline 置信度上限 0.65）的意义**：Fallback 模型（`yolo26s.pt` 通用模型）的置信度**不得**与自训练模型同权展示，DB 侧强制封顶。
> 📌 **`yolo26s.pt` 的官方出处**（`task-15` 核实）：Ultralytics 官方模型文档 <https://docs.ultralytics.com/models/yolo26> 的 Detection 行列出 `yolo26n/s/m/l/x.pt`；本文档配套的 `ultralytics==8.4.216` 内含 `cfg/models/26/yolo26.yaml`（五档 scales）。**该文件名不是杜撰**，权重可从 `github.com/ultralytics/assets/releases/tag/v8.4.0` 下载（`yolo26s.pt` = 20,422,725 B）。核实记录见 `.team/notes/research/yolo26-official-sources.md`。
> ⚠️ **注意边界**：具体选哪一档（`n`/`s`/`m`/`l`/`x`）是**工程初值未标定**，见 `docs/TECH-STACK.md` §8 `TS-2`。**CHECK 7 的 0.65 阈值本身是工程初值，不是官方数字。**
> **CHECK 8 的意义**：只要写了 `ai_result_json`，就必须同时声明「需巡查员确认 = true」且「不替代电气安全检查 = false」。**这是把 V1/V2 两条安全约束焊进 JSONB 字段的唯一办法**——否则 AI 结果里可能出现 `replaces_electrical_safety_check: true` 这种致命声明。

---

## 5. 其余 15 张表（按圈号序，列关键约束）

| 圈号 | 表 | 关键约束摘要 |
|---:|---|---|
| ① | `accounts` | `username` UNIQUE；`status` CHECK（`ACTIVE`/`DISABLED`）；`token_version` int NOT NULL（吊销）；`skills text[]`（原 `technician_skills` 表被合并）；`max_active_dispatch` CHECK。4 CHECK / 1 FK / 2 UNIQUE / 1 idx |
| ② | `roles` | `code` UNIQUE（`ADMIN`/`INSPECTOR`/`TECHNICIAN`）。1 CHECK |
| ③ | `account_roles` | `(account_id, role_id)` UNIQUE。0 CHECK / 3 FK / 2 UNIQUE |
| ④ | `scope_authorizations` | `scope_type` ∈ `INSPECT`/`REPAIR`/`MANAGE`；`valid_from`/`valid_to` CHECK。`uq_scope_authorizations_account_region_type` 部分唯一（`WHERE is_active`）。**软停用不物删**——历史派工引用它 |
| ⑤ | `business_rules` | `rule_code` + `version`；`rule_value jsonb`；`uq_business_rules_code_active` 部分唯一（`WHERE is_active`）。**禁止原地改 `rule_value`** → `422 RULE_VALUE_IMMUTABLE` |
| ⑥ | `notification_tasks` | `status` CHECK（5 态）；`attempt_count`/`max_attempts` CHECK；`next_retry_at`。7 CHECK / 4 FK / 4 idx（含 `dead_letter`/`pending_due`/`recipient_unread` 三个部分索引） |
| ⑦ | `audit_logs` | **`uq_audit_logs_idempotency`** 部分唯一（`idempotency_key WHERE response_status BETWEEN 200 AND 299`）；`request_id`；`business_snapshot_json`。**只读表，无写端点** |
| ⑧ | `maintenance_regions` | `region_code` UNIQUE；`parent_region_id` 自引用 FK；`level` CHECK。2 CHECK / 1 FK |
| ⑨ | `lighting_points` | `point_code` UNIQUE；`region_id` FK；`road_level` ∈ `EXPRESS`/`MAIN`/`BRANCH`；经纬度范围 CHECK。6 CHECK |
| ⑩ | `facility_assets` | `asset_type`/`importance`（`CRITICAL`/`HIGH`/`NORMAL`）CHECK。4 CHECK |
| ⑬ | `dispatch_records` | `status` CHECK（7 态）；`uq_dispatch_records_active_ticket` 部分唯一（`WHERE status IN ('PENDING_ACCEPT','ACCEPTED')`）；`rework_order_id` FK。7 CHECK / 5 FK |
| ⑭ | `repair_processes` | **`ck_repair_processes_safety_checklist_complete`**（电气安全检查六件套，V2 的 DB 落点）；`electrical_safety_checked`；`safety_checklist_json`。4 CHECK / 3 FK |
| ⑮ | `material_usages` | `quantity` CHECK > 0；`material_code`。3 CHECK / 2 FK |
| ⑯ | `acceptance_records` | **`decision` ∈ `APPROVED`/`REJECTED`**（**不是 `result='passed'`**）；`comment`；`rework_order_id` FK。3 CHECK / 4 FK |
| ⑰ | `rework_orders` | `status` CHECK（8 态）；`round_no`；`requires_manual_review`；**`uq_rework_orders_ticket_round`（全时）+ `uq_rework_orders_active_ticket`（仅活跃）是两个约束**；`source_acceptance_id` ↔ `acceptance_records.rework_order_id` 循环外键 |

---

## 6. 循环外键的处置（`rework_orders` ↔ `acceptance_records`）

### 6.1 问题

```
rework_orders.source_acceptance_id  → acceptance_records.id
acceptance_records.rework_order_id  → rework_orders.id
```

两表互相引用，**任何建表顺序都无法避免前置表不存在**。

### 6.2 处置方案：ORM 层与迁移层分开处理

#### ORM 层（`create_all()` 场景）

```python
ForeignKey("acceptance_records.id", ondelete="RESTRICT")   # 不加 use_alter
```

**不加 `use_alter` 的理由**：SQLAlchemy 2.0 的 `create_all()` 能自行解析循环依赖并排序，`use_alter` 会强制走 `ALTER TABLE ADD CONSTRAINT`，反而增加迁移复杂度。

#### 迁移层（Alembic 场景）

用 `op.create_foreign_key()` 在**两表都建完之后**单独补 FK：

```python
# 1. 建 rework_orders（source_acceptance_id 列先不加 FK）
op.create_table("rework_orders", ..., sa.Column("source_acceptance_id", sa.BigInteger(), nullable=True), ...)

# 2. 建 acceptance_records（rework_order_id 列先不加 FK）
op.create_table("acceptance_records", ..., sa.Column("rework_order_id", sa.BigInteger(), nullable=True), ...)

# 3. 两表都建好后补 FK
op.create_foreign_key(
    "fk_rework_orders_source_acceptance", "rework_orders",
    "acceptance_records", ["source_acceptance_id"], ["id"], ondelete="RESTRICT",
)
op.create_foreign_key(
    "fk_acceptance_records_rework_order", "acceptance_records",
    "rework_orders", ["rework_order_id"], ["id"], ondelete="RESTRICT",
)
```

> **已核实的坑**：Alembic 对循环外键的自动排序有历史缺陷（见 Alembic issue #626 / #387）。**不要依赖自动排序**，显式分两步。

### 6.3 硬证据：`pg_constraint` 计数

迁移后必须验证两个 FK 都真的建成了：

```sql
SELECT conname, conrelid::regclass, confrelid::regclass
FROM pg_constraint
WHERE contype = 'f'
  AND conname IN ('fk_rework_orders_source_acceptance', 'fk_acceptance_records_rework_order');
-- 期望：恰好 2 行
```

> **不能只看 `alembic upgrade head` 退出码为 0**：循环 FK 若在模型层用 `use_alter` 而迁移层漏了，退出码仍是 0 但约束不存在——**这是典型假绿**。

---

## 7. 迁移期 5 条数据完整性断言（**全部必须通过**）

每条断言都必须返回 **0 行**（或断言 5 返回 `=17`）。

### 断言 1：点位区域一致性

```sql
-- 期望 0 行：票的 region_id 必须等于其点位的 region_id
SELECT t.id, t.region_id, p.region_id
FROM fault_tickets t JOIN lighting_points p ON p.id = t.lighting_point_id
WHERE t.region_id <> p.region_id;
```

### 断言 2：派工技术员当时有 `REPAIR` 授权

```sql
-- 期望 0 行：每条派工在派工时刻必须有有效 REPAIR 授权
SELECT d.id
FROM dispatch_records d
LEFT JOIN scope_authorizations s
  ON s.account_id = d.technician_account_id
 AND s.region_id = d.region_id
 AND s.scope_type = 'REPAIR'
 AND s.is_active = true
 AND (s.valid_from IS NULL OR s.valid_from <= d.dispatched_at)
 AND (s.valid_to   IS NULL OR s.valid_to   >= d.dispatched_at)
WHERE s.id IS NULL;
```

### 断言 3：非终态票最多 1 条活跃派工

```sql
-- 期望 0 行
SELECT ticket_id, count(*) FROM dispatch_records
WHERE status IN ('PENDING_ACCEPT','ACCEPTED')
GROUP BY ticket_id HAVING count(*) > 1;
```

### 断言 4：`rework_count` == 返修单行数

```sql
-- 期望 0 行
SELECT t.id, t.rework_count, count(r.id) AS actual
FROM fault_tickets t LEFT JOIN rework_orders r ON r.ticket_id = t.id
GROUP BY t.id, t.rework_count
HAVING t.rework_count <> count(r.id);
```

### 断言 5：17 张基础表存在（**排除 `alembic_version`**）

```sql
-- 期望 = 17
SELECT count(*) FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
  AND table_name <> 'alembic_version';
```

### 7.1 断言 2 与 4 的特殊地位：**它们守的是「冗余列」**

- **断言 1/2** 守 `fault_tickets.region_id` 与 `dispatch_records.region_id` 两个冗余列。
- **断言 4** 守 `fault_tickets.rework_count` 冗余列。

> 🔴 **处置纪律**：若冗余代价过高，**删掉列并改查询**是合法选择；**「保留列但跳过校验」是非法选择**——那会让冗余列变成「第二处真相」，必然漂移。

---

## 8. 四张被合并候选表（**为什么没有第 18 张表**）

| 候选表 | 处置 | 理由 |
|---|---|---|
| `idempotency_keys` | → `audit_logs.idempotency_key` + 部分唯一索引 | 幂等键的生命周期与审计记录完全重合，独立建表只会多一次 JOIN 且破坏「审计即幂等凭据」的单一真相 |
| `sessions` / `refresh_tokens` | **不建表** | 无状态 JWT + `accounts.token_version` 实现 O(1) 吊销。建表会引入清理任务与状态同步问题 |
| `technician_skills` | → `accounts.skills text[]` | 技能是账号的属性而非独立实体；查询模式只有「按技能筛选账号」 |
| `sla_alerts` | → `fault_tickets.is_sla_overdue`/`overdue_stage` + `notification_tasks` | SLA 是票的状态而非独立事件流；通知已由 `notification_tasks` 承载 |

> **合并的代价**：`accounts.skills` 用 `text[]` 后无法建普通 B-tree 索引（需 GIN）。本项目技能数据量极小，**全表扫可接受**——这是显式取舍。

---

## 9. 类型选型与 PG 兼容性纪律

| 类别 | 选型 | 理由 |
|---|---|---|
| 主键 | `bigserial` | 单机顺序递增，避免 UUID 的索引膨胀 |
| 时间 | **`timestamptz`（不是 `timestamp`）** | 系统跨时区（`TZ=Asia/Shanghai`），`timestamp` 会丢失偏移。**`MUT-14` 变异卡专门测它** |
| 金额/得分 | **`numeric(p,s)`（不是 `float`/`double`）** | 浮点比较会因精度导致 `CHECK` 与统计不一致。**`MUT-13` 专门测它** |
| 半结构化 | `jsonb`（不是 `json`） | `jsonb` 支持索引与路径查询，`json` 只存文本 |
| 布尔 | `boolean` + `NOT NULL DEFAULT` | 避免三值逻辑（`NULL` 语义歧义） |
| 枚举 | `varchar` + `CHECK`（**不用 PG `ENUM` 类型**） | PG `ENUM` 加值需 `ALTER TYPE`，迁移麻烦；`varchar + CHECK` 改起来简单 |

> **`timestamptz` vs `timestamp` 的检查命令**（迁移后必跑）：
> ```sql
> SELECT table_name, column_name, data_type FROM information_schema.columns
> WHERE table_schema='public' AND data_type LIKE 'timestamp%';
> -- 期望：全部为 'timestamp with time zone'
> ```

---

## 10. 待用户裁决清单

| # | 事项 | 现状 | 影响 |
|---:|---|---|---|
| **SD-1** | 冗余列 `region_id`/`rework_count` 是否保留 | 保留并由断言 1/2/4 守住 | 若删列需改多处查询 |
| **SD-2** | 孤儿证据回收任务的具体策略（保留天数、批大小） | **未设计** | 重复上传会产生多行，需清理 |
| **SD-3** | `accounts.skills` 是否需要 GIN 索引 | 当前全表扫 | 技能数据量大时需索引 |
| **SD-4** | PG 版本 17.12 是否需兼容 16 | 实测环境 17.12 | 若需兼容须避免 17-only 特性 |

---

## 11. 本文档的诚实边界

**已确定的**：
- 17 张表的完整列/约束/索引**设计**，与逐项求和可复算的 76/41/22/37/6。
- 三套编号的完整映射与**三处错位的原因**。
- 循环外键的两层处置（ORM 不加 `use_alter`；迁移显式分两步）。
- 5 条迁移期断言的完整 SQL。

**没有确定的**：
- ⚠️ **阶段一没有任何数据库迁移被运行过**。`backend/`、`migrations/` 目录**不存在**。
- ⚠️ 76/41/22/37/6 **全部是 DDL 文本计数，不是 `information_schema` 实测值**——没有 DB 可查。迁移落地后必须用 §0.4 的命令对账。
- 「外键列 41」与未来 `pg_constraint` 实测的 FK 约束数**允许不同**，差异须逐条解释。
- §10 的 4 项待用户裁决。

**明确不属于本文档的**：
- 状态机转移语义 → `docs/BUSINESS-LOGIC.md`
- 端点与错误码 → `docs/API-CONTRACT.md`

---

## 12. 变更记录

| 日期 | 变更 | 触发 |
|---|---|---|
| 2026-11-21 | 初版。17 表 / 76 CHECK / 41 外键列 / 22 UNIQUE / 37 索引 / 6 部分唯一；吸收 Lead 的 `fault_evidences.ticket_id` 可空裁决与 `uq_..._sha256_per_ticket` 已知取舍 | `task-7` |
| 2026-11-21 | §4.4 CHECK 7 注中 `yolo26s.pt` **补官方出处**（Ultralytics 官方模型文档 + `ultralytics==8.4.216` 源码坐标），并标注「选哪一档 scale 属工程初值未标定」。**17 表结构、76/41/22/37/6 计数、CHECK 语义零改动** | `audits/stage1-audit.md` P1-2 |
