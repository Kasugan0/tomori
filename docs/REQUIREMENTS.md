> 状态：阶段一定稿（待用户放行阶段二实现）

# 需求规格（REQUIREMENTS）

> **文档归属**：`docs-writer`｜**本文件写入域**：`docs/REQUIREMENTS.md`
> **读者**：用户（验收审阅）、`auditor`、`tester`、全体实现者
> **上游素材**：`.team/notes/tester/stage1-test-matrix-input.md`（可验证判据 `VC-01..VC-29`）、`.team/notes/backend-dev/stage1-backend-spec-api.md` §12（`UC`↔`FR` 对账）、`.team/notes/vision-dev/stage1-vision-spec-input.md`（智能边界）、`.team/notes/research/standards-and-stack.md`（行业阈值）、`.team/notes/research/zhengzhou-lighting-rules.md`（郑州法规取证）、`.team/notes/research/datasets.md`（数据集核查）
> **本文档地位**：`UC` / `FR` 的**精确编号与标题、法定合规与业务策略语义、五项红线与智能边界的唯一权威**。其他文档引用 `FR-xx` 或法定合规规则语义时以本文为准；状态机、算法公式、17 表 Schema 与技术栈见 `docs/DESIGN.md`。

---

## 0. 统计口径分层声明（`docs/team/TEAM.md` §5.3 硬要求）

**任何数字在本文件出现，必须同时给出分子、分母、排除项三层。**

| 项 | 分子 | 分母 | 排除项 |
|---|---:|---|---|
| 核心用例 | **9**（`UC-01`..`UC-09`） | 用户「建议用例数：9」 | 无（含系统级横切 `UC-09`，见 §2） |
| 功能需求 | **29**（`FR-01`..`FR-29`） | 用户「建议功能需求数：29」 | 无 |
| 可验证判据 | **29**（`VC-01`..`VC-29`，与 FR 1:1） | 与 `FR-01`..`FR-29` 一一绑定 | 无（`VC-xx` 是 `FR-xx` 的验收判据，不是新增需求） |
| 穷举标注 | 9 个 UC 与 4 周重点均显式标 `[穷举]` | `docs/team/TEAM.md` 方法论 | §2 含 `UC-09` 横切并列；§8 覆盖 7 项 4 周重点，无遗漏 |
| 角色 | **3**（`ADMIN`/`INSPECTOR`/`TECHNICIAN`） | 用户「主要角色：运维管理员、巡查员、维修人员」 | 无（无第 4 角色） |
| 业务表 | **17**（详见 `docs/DESIGN.md` §4） | 用户「建议核心数据表（17）」 | `alembic_version`（迁移元数据表）；PG 自动 `*_pkey` 索引；FK 支撑索引。**没有第 18 张业务表。** |

> **`alembic_version` 的口径声明**：迁移工具的版本记录表，仅一列 `version_num varchar(32)`，**不承载业务语义、不参与业务查询**。任何「数表」断言必须显式排除：`WHERE table_name <> 'alembic_version'`。
>
> **阶段一无数据库**：`web/`、`tests/`、`migrations/` 在阶段一冻结时均不存在（用户现已放行阶段二，Python 源码按 `src layout` 落于 `src/tomori/`），「17 表」是**设计口径**（用户给定清单，详见 `docs/DESIGN.md` §4），不是 `information_schema` 实测值。

---

## 1. 三个角色与权限边界

| 角色 | 代码 | 职责 | 用户原话依据 |
|---|---|---|---|
| 运维管理员 | `ADMIN` | 配置运维区域/点位/资产、分配授权、派工与改派、处置 `MANUAL_REVIEW`、配置业务规则、查看审计与通知 | 「面向运维管理员…完成报障→分级派工→维修→验收→关闭或返修闭环」 |
| 巡查员 | `INSPECTOR` | 上传静态巡查图片生成 AI 候选、人工确认/修正/剔除、纯人工报障、验收（通过/驳回）、撤销误报 | 同上 +「维修工单须经巡查员确认」 |
| 维修人员 | `TECHNICIAN` | 接单/拒单、登记维修过程与材料、填写电气安全检查清单、提交完工、升级上报 | 同上 |

### 1.1 权限模型：RBAC + 区域授权双层

| 层 | 机制 | 语义边界 |
|---|---|---|
| 第一层 | **角色**（`ADMIN`/`INSPECTOR`/`TECHNICIAN`） | 接口级准入，越权时以 `403 FORBIDDEN_ROLE` 拒绝 |
| 第二层 | **业务范围授权**（`scope_authorizations`，绑定 `account_id` + `region_id` + `scope_type` + `valid_from`/`valid_to`） | 同一角色在不同区域权限不同，过期或软停用（`is_active=false`）即失效；操作人无目标区域授权时以 `403 FORBIDDEN_REGION_SCOPE` 拒绝 |
| 第三层 | **账号状态**（`accounts.status`） | 账号停用（`status != ACTIVE`）即拒绝登录并拦截已签发令牌（`403 ACCOUNT_DISABLED`） |

**`scope_type` 三类有效期授权**：

| `scope_type` | 业务含义 | 典型角色 | 有效期与留档约束 |
|---|---|---|---|
| `INSPECT` | 可巡查上报、确认 AI 候选、执行验收该区域 | `INSPECTOR` | 支持 `valid_from`/`valid_to` 有效期；历史工单引用不可物理删除，停用走 `is_active=false` |
| `REPAIR` | 可被派工并接单维修该区域 | `TECHNICIAN` | 同上，且同时受 `accounts.max_active_dispatch` 在途上限约束 |
| `MANAGE` | 可维护该区域档案与规则、执行派工/改派/复核处置 | `ADMIN` | 同上 |

**推荐、指派、接单三处强校验语义（缺一即为假门禁）**：
1. **派单推荐过滤**：推荐接口仅按区域 `REPAIR` 有效授权、账号状态、角色与在途数过滤并列明剔除原因，属 UI 便利，**不构成安全边界**；
2. **手动指派 / 改派强校验**：管理员提交指派时，服务端必须独立重新校验操作人 `MANAGE` 权限与被指派维修员的 `REPAIR` 有效授权及在途负载，**绝不信任前端候选列表**；
3. **接单瞬间二次校验**：维修员点击接单时必须再次校验其对该区域的 `REPAIR` 授权仍有效（防止「派工后、接单前」授权被撤销或过期）。

> ⚠️ **两层拒绝语义必须严格区分**：**操作人自身无角色/无区域权限/账号停用**返回 `403`（「你不能这么做」）；**操作人有权限但所指定的目标维修员不合格**返回 `422 DISPATCH_TECHNICIAN_NOT_ELIGIBLE`（含 `NO_REGION_SCOPE` / `SCOPE_EXPIRED` / `ACCOUNT_INACTIVE` / `WRONG_ROLE` / `OVERLOADED`）或 `422 TECHNICIAN_OVERLOADED`（「你要求做的事参数对象不成立」）。具体 `403` vs `422` 判定细节与实现契约见 `docs/DESIGN.md` §3.2 与 `docs/API.md` §1.3。

---

## 2. 九个核心用例（UC-01..UC-09）`[穷举]`

> **`[穷举]` 口径声明**：下表 **9 行 = 全系统全部核心用例**（分子 9 / 分母 9 / 排除项 0）。`UC-09` 并非第 9 步串行用例，而是与 `UC-01`..`UC-08` 并列的系统级横切用例。

| UC | 标题 | 主角色 | 覆盖 `FR` | 端点前缀 | 涉及核心表 |
|---|---|---|---|---|---|
| `UC-01` | 认证、RBAC 与区域授权管理 | ADMIN | `FR-01`–`FR-04` | `/auth/*`、`/accounts*`、`/roles` | `accounts`、`roles`、`account_roles`、`scope_authorizations`、`audit_logs` |
| `UC-02` | 区域、点位与资产档案管理 | ADMIN | `FR-05`–`FR-10` | `/regions*`、`/lighting-points*`、`/facility-assets*`、`/scope-authorizations*` | `maintenance_regions`、`lighting_points`、`facility_assets`、`scope_authorizations`、`audit_logs` |
| `UC-03` | 静态巡查识别与人工确认报障 | INSPECTOR | `FR-11`–`FR-15` | `/tickets/ai-candidates`、`/tickets`(POST)、`/tickets/{id}/confirm`、`/tickets/{id}`(PATCH)、`/evidence/*` | `fault_tickets`、`fault_evidences`、`lighting_points`、`audit_logs` |
| `UC-04` | 规则驱动故障分级与 SLA 计算 | ADMIN | `FR-16`–`FR-18` | `/business-rules*`、`/tickets/{id}/regrade`、`/sla/scan` | `business_rules`、`fault_tickets`、`lighting_points`、`facility_assets`、`audit_logs` |
| `UC-05` | 防重提交与区域授权派工 | ADMIN | `FR-19`–`FR-21` | `/tickets/{id}/dispatch-candidates`、`/tickets/{id}/dispatch`、幂等键基础设施 | `fault_tickets`、`dispatch_records`、`scope_authorizations`、`notification_tasks`、`audit_logs` |
| `UC-06` | 维修接单/拒单与电气安全检查门禁 | TECHNICIAN | `FR-22`–`FR-24` | `/dispatches*`、`/repair-processes*`、`/material-usages`、`/tickets/{id}/evidences` | `dispatch_records`、`repair_processes`、`material_usages`、`fault_tickets`、`notification_tasks`、`audit_logs` |
| `UC-07` | 验收闭环与返修单自动流转 | INSPECTOR | `FR-25`–`FR-27` | `/tickets/{id}/acceptance`、`/acceptances`、`/rework-orders*` | `acceptance_records`、`rework_orders`、`fault_tickets`、`notification_tasks`、`audit_logs` |
| `UC-08` | 异常恢复、SLA 超时与撤销/复核 | ADMIN | `FR-28`–`FR-29` | `/tickets/{id}/cancel`、`/tickets/{id}/reassign`、`/dispatches/{id}/escalate`、`/tickets/{id}/review-resolve`、`/notifications*`、`/audit-logs` | `fault_tickets`、`dispatch_records`、`rework_orders`、`business_rules`、`notification_tasks`、`audit_logs` |
| `UC-09` | **系统级横切能力**（操作审计 + 通知任务） | 全部角色 | `FR-29`（+ 横切覆盖 `FR-01`–`FR-28` 的审计与通知侧） | **无独立业务前缀** | `audit_logs`、`notification_tasks` |

### 2.1 `UC-09` 的系统级横切语义（`D-36`）

**定案结论（`docs/DECISION.md` `D-36`）**：`UC-09` = **系统级横切用例**（操作审计 + 通知任务）。

- **不对应单一业务 API 前缀**，而是被 `UC-01`..`UC-08` 各主流程在写操作中**隐式调用**的旁路能力：每次业务状态变更同事务写 `audit_logs` + 按需创建 `notification_tasks`。
- **同时提供独立查询与恢复端点**：`/audit-logs`（只读追溯）、`/notifications*`（含 `POST /notifications/{id}/retry` 死信重试、`POST /notifications/{id}/acknowledge` 人工确认）。
- **并列关系**：它不是「做完前 8 个再做的第 9 步」，而是贯穿全部 8 个主流程的横切关注点（分子 9 / 分母 9 / 排除项 0）。

---

## 3. 二十九条功能需求（FR-01..FR-29）

> **编号权威**：本表。`VC-01`..`VC-29` 与 `FR-01`..`FR-29` **1:1 绑定**（详见 §4 追溯矩阵）。
> **状态声明**：本阶段全部为 `未实现` —— **阶段一只做设计，没有任何可运行的应用源码**。

### 3.1 UC-01 认证与授权（FR-01..FR-04）

| FR | 需求 | 验收判据 | 状态 |
|---|---|---|---|
| `FR-01` | 三角色账号登录，签发 JWT（`HS256`，载荷含 `sub`/`tv`/`role`/`exp`） | `VC-01` | 未实现 |
| `FR-02` | 禁用账号（`status != ACTIVE`）拒绝登录并拦截既有令牌 | `VC-01` | 未实现 |
| `FR-03` | RBAC 接口级角色权限校验，越权返回 `403 FORBIDDEN_ROLE` 且 DB 零脏写 | `VC-02` | 未实现 |
| `FR-04` | 管理员分配/停用/设有效期业务范围授权（绑定运维区域），变更写 `audit_logs` | `VC-03` | 未实现 |

### 3.2 UC-02 档案管理（FR-05..FR-10）

| FR | 需求 | 验收判据 | 状态 |
|---|---|---|---|
| `FR-05` | 运维区域增改查；`region_code` 唯一（重复 → `409`） | `VC-04` | 未实现 |
| `FR-06` | 照明点位档案维护（绑定区域、路段等级 `EXPRESS`/`MAIN`/`BRANCH`、坐标/杆号） | `VC-05` | 未实现 |
| `FR-07` | 区域外操作点位 → `403 FORBIDDEN_REGION_SCOPE` | `VC-05` | 未实现 |
| `FR-08` | 设施资产台账（灯具/灯杆/配电箱…）绑定照明点位 | `VC-06` | 未实现 |
| `FR-09` | 资产重要度（`CRITICAL`/`HIGH`/`NORMAL`）可被分级规则读取 | `VC-06` | 未实现 |
| `FR-10` | 业务范围授权的增删改查（停用走软停用 `is_active=false`）与区域绑定校验 | `VC-03` | 未实现 |

### 3.3 UC-03 智能报障（FR-11..FR-15）

| FR | 需求 | 验收判据 | 状态 |
|---|---|---|---|
| `FR-11` | **仅接受静态图片**：每次请求 `1..10` 张（`D-03`）、单图 `≤ 15 MiB`（`15,728,640` B，`D-02`）、像素 `≤ 40 MP` 的 JPEG/PNG/WebP；扩展名伪装/多帧动图/解码失败 → `415 UNSUPPORTED_IMAGE_FORMAT`，视频容器/`rtsp://`/`m3u8`/`stream_url`/`camera_*` → `415 MEDIA_VIDEO_STREAM_FORBIDDEN` 硬拒绝；支持正常亮灯参照回传语义（`D-30`，详见 `FR-12`） | `VC-07` | 未实现 |
| `FR-12` | 视觉返回 `FaultCandidateResult`（`bbox_norm`/`bbox_px`/置信度/仅基于图像亮度的昼夜场景保护标志 `D-31`），回传正常亮灯 `streetlight_on`（`is_fault_candidate=false`）供巡查员对照（`D-30`），永不产出 `FLICKERING`/`ELECTRIC_HAZARD`（`V3`），恒声明 `requires_inspector_confirmation=true` 与 `replaces_electrical_safety_check=false` | `VC-08` | **未实现（`vision/` 仅有未验证草稿，见 §6.2）** |
| `FR-13` | 巡查员逐条确认/修改/剔除 AI 候选（改为 `FLICKERING`/`ELECTRIC_HAZARD` 须附 `manual_note`），或纯人工补充报障 | `VC-09` | 未实现 |
| `FR-14` | **未确认禁派工与候选 TTL**：`confirmed_by_inspector=false` 的 `AI_CANDIDATE_PENDING` 严禁进入派工流程（前端禁用 + 后端 `422 AI_CANDIDATE_NOT_CONFIRMED` + DB 三态串行化 `CHECK` 三层）；超 `ai_candidate_ttl_hours`（默认 `72h`，`D-15`）自动过期置 `CANCELLED` 并通知 | `VC-10` | 未实现 |
| `FR-15` | **纯人工报障永不依赖视觉可用性**：视觉失败（`503 VISION_*`）时巡查员可携带提示跳过 AI 直接提交 `POST /tickets` 建单 | `VC-26` | 未实现 |

### 3.4 UC-04 分级与 SLA（FR-16..FR-18）

| FR | 需求 | 验收判据 | 状态 |
|---|---|---|---|
| `FR-16` | 管理员可配置并启停 5 类业务规则（`grading_rules` / `sla_policy` / `dispatch_scoring_weights` / `rework_policy` / `ai_candidate_ttl_hours`），禁止原地改 `rule_value`（`422 RULE_VALUE_IMMUTABLE`），变更写 `audit_logs`；**规则载荷 Pydantic 结构化校验**，非法 → `422 INVALID_RULE_PAYLOAD` | `VC-11` | 未实现 |
| `FR-17` | 确认报障或纯人工建单瞬间依 `grading_rules` + 点位等级 + 资产重要度（无资产取 `NULL`）+ 故障类型 + 安全隐患标志确定性计算 `P1`–`P4`（`grading_score` 写 `NULL`），写入自发现/确认时刻起 +48h（`2880` 分钟，`D-11` 覆盖 `P1`–`P4` 全等级）`repair_deadline_at`、`response_deadline_at` 默认 `NULL`（安全隐患置 `emergency_action_required=true`），固化**分级快照** | `VC-12` | 未实现 |
| `FR-18` | 后续修改规则**不得追溯破坏**已生成工单的历史快照与时限；手工调级 `regrade` 须附必填理由并落审计，且**不重置或顺延**法定 48h `repair_deadline_at` | `VC-13` | 未实现 |

### 3.5 UC-05 防重与派工（FR-19..FR-21）

| FR | 需求 | 验收判据 | 状态 |
|---|---|---|---|
| `FR-19` | **幂等**：12 个必需写端点同 `Idempotency-Key` + 同载荷并发/重放仅建 1 单（带扩展头 `X-Idempotent-Replay: true`）；同键异体 → `422 IDEMPOTENCY_PAYLOAD_MISMATCH`；原请求未完 → `409 IDEMPOTENCY_REQUEST_IN_PROGRESS`；缺键 → `400 IDEMPOTENCY_KEY_REQUIRED`；失败请求（4xx/5xx）不占用幂等键 | `VC-14` | 未实现 |
| `FR-20` | **点位防重**：同一点位同类活跃单重复报障 → `409 DUPLICATE_ACTIVE_TICKET`（返回既有单引用）；PG 部分唯一索引并发兜底 | `VC-15` | 未实现 |
| `FR-21` | **区域授权派工**：只能派给持有该区域 `REPAIR` 有效授权且在途派工 `< max_active_dispatch`（默认 `3` 单，`D-16`）的维修员；被指派人不合格 → `422 DISPATCH_TECHNICIAN_NOT_ELIGIBLE`（或 `422 TECHNICIAN_OVERLOADED`）；操作人无权限 → `403 FORBIDDEN_REGION_SCOPE`；同票单活跃派工 | `VC-16` | 未实现 |

### 3.6 UC-06 维修与安全门禁（FR-22..FR-24）

| FR | 需求 | 验收判据 | 状态 |
|---|---|---|---|
| `FR-22` | 授权维修员查看本人 `PENDING_ACCEPT` 派工并接单（二次校验区域 `REPAIR` 有效授权），派工 → `ACCEPTED`，票 → `IN_REPAIR` | `VC-17` | 未实现 |
| `FR-23` | 维修员附必填理由拒单，派工 → `REJECTED`，票自动回退 `PENDING_DISPATCH` 并通知改派；累计拒单 ≥ 阈值（默认 3）→ 转 `MANUAL_REVIEW` | `VC-18` | 未实现 |
| `FR-24` | 登记维修过程与材料使用（用量非负 DB 校验）；**电气安全检查硬门禁**：`electrical_safety_checked=false` 或 5 项清单任一缺失时严禁完工（前端 disabled + 后端 `422 ELECTRICAL_SAFETY_CHECK_REQUIRED` + DB `CHECK` 三层） | `VC-19`、`VC-20` | 未实现 |

### 3.7 UC-07 验收与返修（FR-25..FR-27）

| FR | 需求 | 验收判据 | 状态 |
|---|---|---|---|
| `FR-25` | 巡查员对本区域 `PENDING_ACCEPTANCE` 工单验收通过，落 `acceptance_records(decision='APPROVED')` 并将票推进 `CLOSED`（释放点位防重锁） | `VC-21` | 未实现 |
| `FR-26` | 验收驳回（附必填原因）在**单事务内**完成：写验收记录（`REJECTED`）+ 生成 `rework_orders(PENDING_DISPATCH)` + `rework_count += 1` + 票进 `REWORK_PENDING_DISPATCH`（`≤ max_rework_rounds` 默认 `2` 轮，`D-14`） | `VC-22` | 未实现 |
| `FR-27` | 返修单依次 `DISPATCHED` → `IN_REPAIR`（再次过安全检查门禁）→ `PENDING_ACCEPTANCE` → 二次验收通过后返修单 `PASSED` 且票 `CLOSED`；**超 `max_rework_rounds`（默认 `2` 轮，`D-14`）→ 仍建该轮返修单并置 `IN_MANUAL_REVIEW`，票强制 `MANUAL_REVIEW`** | `VC-23`、`VC-25` | 未实现 |

### 3.8 UC-08 异常恢复（FR-28..FR-29）

| FR | 需求 | 验收判据 | 状态 |
|---|---|---|---|
| `FR-28` | 派工前撤销误报 → `CANCELLED`（联动派工置 `CANCELLED`），释放点位锁；`MANUAL_REVIEW` 三出口（`APPROVE_REASSIGN`/`SPECIAL_CLOSE`/`TERMINATE_CANCEL`，社会产权设施督促留证闭环见 §7.1）；`IN_REPAIR` 可升级 `escalate` → `MANUAL_REVIEW` | `VC-24`、`VC-26`、`VC-28` | 未实现 |
| `FR-29` | **审计与通知横切**：每次状态变更同事务写且仅写 1 条 `audit_logs` + 按需 `notification_tasks`；`POST /sla/scan`（ADMIN 手动触发）按郑州 48h `repair_deadline_at` 标记超时（含 `MANUAL_REVIEW` 与 `P4` 观察）及派工防死锁超时回收；通知死信可重试/确认 | `VC-25`、`VC-27`、`VC-29` | 未实现 |

---

## 4. 追溯矩阵：`UC` ↔ `FR` ↔ `VC` ↔ 测试层次

> **三层口径**：分子 = 绑定成功的行数（29 行）；分母 = 29 个 `FR`；排除项 = 无（`FR-01`..`FR-29` 全部与 `VC-01`..`VC-29` 绑定）。

| `FR` | `UC` | `VC` | 测试层 | 4 周验收重点 |
|---|---|---|---|---|
| `FR-01` | UC-01 | `VC-01` | L1+L2+L4 | — |
| `FR-02` | UC-01 | `VC-01` | L2+L4 | — |
| `FR-03` | UC-01 | `VC-02` | L2+L4 | **权限拒绝** |
| `FR-04` | UC-01 | `VC-03` | L2+L4 | — |
| `FR-05` | UC-02 | `VC-04` | L2+L4 | — |
| `FR-06` | UC-02 | `VC-05` | L2+L4 | — |
| `FR-07` | UC-02 | `VC-05` | L2+L4 | **权限拒绝** |
| `FR-08` | UC-02 | `VC-06` | L2+L4 | — |
| `FR-09` | UC-02 | `VC-06` | L1+L2 | — |
| `FR-10` | UC-02 | `VC-03` | L2+L4 | — |
| `FR-11` | UC-03 | `VC-07` | L1+L2+L4 | **异常恢复** |
| `FR-12` | UC-03 | `VC-08` | L1+L2+L3 | — |
| `FR-13` | UC-03 | `VC-09` | L2+L4 | — |
| `FR-14` | UC-03 | `VC-10` | L1+L2+L4 | **智能安全边界** |
| `FR-15` | UC-03 | `VC-26` | L2+L4 | **异常恢复** |
| `FR-16` | UC-04 | `VC-11` | L1+L2+L4 | — |
| `FR-17` | UC-04 | `VC-12` | L1+L2+L4 | **故障分级（`grading_rules` + 郑州 48h SLA）** |
| `FR-18` | UC-04 | `VC-13` | L2+L4 | — |
| `FR-19` | UC-05 | `VC-14` | L2+L4 | **重复提交** |
| `FR-20` | UC-05 | `VC-15` | L2+L4 | **重复提交** |
| `FR-21` | UC-05 | `VC-16` | L2+L4 | **区域派单** |
| `FR-22` | UC-06 | `VC-17` | L2+L4 | — |
| `FR-23` | UC-06 | `VC-18` | L2+L4 | **异常恢复** |
| `FR-24` | UC-06 | `VC-19`+`VC-20` | L1+L2+L4 | **智能安全边界** |
| `FR-25` | UC-07 | `VC-21` | L2+L4 | — |
| `FR-26` | UC-07 | `VC-22` | L2+L4 | **返修闭环** |
| `FR-27` | UC-07 | `VC-23`+`VC-25` | L2+L4 | **返修闭环** |
| `FR-28` | UC-08 | `VC-24`+`VC-26`+`VC-28` | L2+L4 | **异常恢复** |
| `FR-29` | UC-08/UC-09 | `VC-25`+`VC-27`+`VC-29` | L1+L2+L4 | **异常恢复（含郑州 48h SLA 扫描）** |

> **测试分层定义**（转写自 `.team/notes/tester/stage1-test-matrix-input.md`）：
> - **L1** 纯函数单测（确定性 `grading_rules` 144 组合真值表 / `dispatch_scoring_weights` 推荐排序 / 郑州 48h `sla_policy` 计算，无 DB）
> - **L2** 集成测试（真实 PostgreSQL 17.12 `tomori_test` + `httpx.AsyncClient(ASGITransport)`）
> - **L3** 视觉模块测试（`vision/` 内部：Tier 3a 静态图校验与昼夜融合 + Tier 3b 远端 `yolo26s.pt` 产物本地推理校验）
> - **L4** E2E（`pytest-playwright`，真实浏览器 + 真实后端 + 真实数据库）

---

## 5. 五项核心红线（N1–N5，明确「不做」，不可扩展）

| # | 红线 | 用户原话/依据 | 本设计的保证方式与技术落点 |
|---:|---|---|---|
| **N1** | **不接实时监控** | 「只处理静态巡查图片，不接实时监控或远程灯控」 | 无 RTSP/流媒体依赖；`415 MEDIA_VIDEO_STREAM_FORBIDDEN` **主动拒绝**视频容器（MP4/MKV/AVI/FLV）、`video/*` MIME 与 `rtsp://`/`rtmp://`/`m3u8`/`stream_url`/`camera_*` 字段 |
| **N2** | **不做远程灯控** | 「不接灯控设备」「不接实时监控或远程灯控」 | 无任何灯控协议/IoT 客户端依赖；无开关灯/调光/继电器/PLC 端点与设备指令表；静态扫描正则含 `` `灯控\|开关灯\|调光\|继电器\|PLC` `` |
| **N3** | **不做能耗/电量预测** | 「不做能耗预测」 | 无时序预测库；无能耗相关表与端点；`facility_assets.rated_power_w` 等静态参数不作预测输入 |
| **N4** | **不跳过巡查员确认（`V1`）** | 「维修工单须经巡查员确认」 | 视觉仅产候选（`requires_inspector_confirmation` 恒 `true`）；前端禁用 + `422 AI_CANDIDATE_NOT_CONFIRMED` + DB 三态串行化 `CHECK` 三层强制 |
| **N5** | **不跳过现场电气安全检查（`V2`）** | 「模型不能替代电气安全检查」 | 电气安全检查是维修员填写的「总开关 + 5 项清单 + 检查说明」，与视觉模块**零耦合**；`replaces_electrical_safety_check` 恒 `false`；前端 disabled + `422 ELECTRICAL_SAFETY_CHECK_REQUIRED` + DB `CHECK` 三层兜底 |

> ⚠️ **`MEDIA_VIDEO_STREAM_FORBIDDEN` 与 `UNSUPPORTED_IMAGE_FORMAT` 必须分离**：拒绝实时监控不是「忽略」，而是显式报错 `415 MEDIA_VIDEO_STREAM_FORBIDDEN`（独立统计与红线告警）；而 `.gif` 与多帧动图（APNG `acTL` / WebP `n_frames>1`）归载体格式问题 `415 UNSUPPORTED_IMAGE_FORMAT`，避免红线告警被正常动图噪声污染。

---

## 6. 智能能力、展示与数据集边界（`FR-11`–`FR-15`，`D-18` / `D-29` / `D-30` / `D-31`）

### 6.1 用户原话与三条不可违反的安全约束（V1–V3）

> 「智能子任务：从巡查照片中识别灯具亮灭、明显破损或遮挡，辅助生成故障候选。智能业务范围与边界：只处理静态巡查图片，不接实时监控或远程灯控；维修工单须经巡查员确认，模型不能替代电气安全检查。」

在输入仅接受静态巡查图片（对应 `N1`）的前提下，业务与数据流转必须严守以下三条不可协商的约束（三层强制点及 DB `CHECK` 详情见 `docs/DESIGN.md` §5.2）：

| # | 安全约束 | 落地语义 | 违反后果 |
|---:|---|---|---|
| **V1** | AI 候选**必须**由巡查员人工确认，无任何自动化跳过路径 | `AI_CANDIDATE_PENDING` → 人工 `confirm` → `PENDING_DISPATCH`；未确认禁派工 | 模型直接驱动业务 = 越权 |
| **V2** | 模型识别**不得替代**现场电气安全检查 | `replaces_electrical_safety_check` 恒 `false`；安全清单与视觉零耦合 | 拿 AI 当安全认证 = 事故责任 |
| **V3** | 视觉**永不产出** `FLICKERING` / `ELECTRIC_HAZARD` | 闪烁需多帧时序（静态图不可判）、电气隐患需现场仪器检测；仅允许巡查员人工改选或手动报障并附 `manual_note` | 超出静态图像物理可判边界 = 误导现场 |

### 6.2 候选参照回传、昼夜判定与能力边界定案

1. **正常亮灯参照回传（`D-30` ✅ 已裁定回传）**：夜间/黄昏检测到正常亮灯 `streetlight_on` 时，视觉输出回传该检测框，但置 `is_fault_candidate=false`（`candidate_fault_type=null`、`visual_signal_state='NORMAL_LIT'`）。这使巡查员在前端图像上能明确区分「**AI 看到且判定正常**」与「**AI 完全未检出**」两种决策意义完全相反的情形。
2. **昼夜判定仅靠图像亮度特征（`D-31` 🟢 工程定案）**：全局 `scene_lighting`（`day`/`night`/`twilight`）及 ROI 亮灯校验**仅基于图像亮度与天空区域特征**（`v_median`、`sky_v_median`、`luminance_contrast`、`overexposed_ratio`），**不依赖 EXIF 拍摄时间**（防 EXIF 缺失、篡改或时区错乱）。白天场景下检出 `streetlight_off` 强制抑制（`is_fault_candidate=false`、`fusion_status='daytime_unpowered_suppressed'`），不自动生成故障单、不锁定照明点位（仅留存 `fault_evidences` 行且 `ticket_id=NULL` 供可审计留痕）。
3. **能力承诺与破损类边界（`D-18` ✅ 已裁定不承诺检出率）**：
   - 灯具**亮/灭**与**遮挡**有公开检测数据集 `ngocleltt/lamp-state-detection`（8,428 张，CC-BY-4.0）支撑；
   - **灯具破损（`FIXTURE_DAMAGED`）**：需求允许写「系统应支持从静态照片识别明显破损」，但**阶段一不承诺任何具体检出率/精确率指标**（`D-18` 已裁定不承诺；无训练、无验证集、无实测，严禁把未验证数字写为需求承诺，守 `F14`）。
4. **数据集使用边界（`D-29` ✅ 已裁定直接使用）**：用户已于 `docs/DECISION.md` `D-29` 明确批示——本项目为个人本机项目，所有公开与自采数据集**无需考虑商业授权合规性或发函申请，可直接用于训练与验证**；同时，各公开数据集的**原始许可元数据与仓库实测质量事实**（如 CC-BY-4.0、Roboflow/GitHub 无 LICENSE、占位仓库、无数据文件等）仍按 `F14` 如实记录于 `docs/DESIGN.md` §5.3，不虚构「开源」属性。
5. **`vision/` 目录当前状态（表述纪律）**：仓库根 `vision/` 下现有 **8 个 `.py` 文件**（`__init__.py`/`config.py`/`contract.py`/`errors.py`/`image_io.py`/`metrics.py`/`whitelist.py`/`baseline.py`）来自已撤回 `task-10` 的预研草稿，**未经运行验证、未经测试、未纳入阶段一交付范围**。阶段一唯一正确表述为「阶段二规划目录；当前存在未经运行验证的预研草稿」，**`FR-12` 真实状态仍为「未实现」**。

---

## 7. 法定合规基线与业务策略定案（`D-11`–`D-17`）

### 7.1 唯一法定时限基线：郑州市令第 152 号（2020 第 237 号二次修正）

> 用户已裁决（`D-11`）：「严格按照规范和法律，不允许使用更严格或者更宽松的。请按照郑州市的规则来」「没有依据的事情就不做」。取证全文见 `.team/notes/research/zhengzhou-lighting-rules.md`。

**① 48 小时维修养护 / 督促法定义务（唯一生效单票时限）**：
《郑州市城市照明管理办法》（2006 年 6 月 7 日市政府令第 152 号公布；2018 年第 229 号第一次修正；2020 年 1 月 17 日第 237 号第二次修正，现行文本；官方出处：<https://public.zhengzhou.gov.cn/guizhang/6085704.jhtml>）第 19 条第 3 款：

> 「市城市照明管理机构在对城市照明设施日常监督检查中发现城市照明设施损坏、缺失或需要维修养护的,应当在48小时之内维修养护或督促有关单位维修养护。」

这条义务的**三个限定一个都不能剥离**：

| 要素 | 法定原文限定 | 本系统业务落地与红线 |
|---|---|---|
| **触发（起算点）** | 「日常监督检查中发现」 | 以**确认/建单时刻** `T_discover`（`confirmed_at` / 人工建单 `created_at`）作为「发现」的可审计落点（`AI_CANDIDATE_PENDING` 草稿期不计时）；**改派、接单、`regrade` 调级均不重置或顺延** `repair_deadline_at` |
| **动作（双合规闭环）** | 「维修养护 **或** 督促有关单位维修养护」 | **直管设施**：48h 内完成维修养护（流转至 `PENDING_ACCEPTANCE` / `CLOSED`）；**社会单位投资建设/管理设施**（第 19 条第 2 款）：48h 内核实并下达督促后经 `MANUAL_REVIEW` 的 `SPECIAL_CLOSE` 闭环，须在 `reason` 与证据附件记录**督促时间、受督促单位与凭证**入 `audit_logs`（**不可用口头督促替代留证**，督促完成亦不等同故障本身已修复） |
| **终点与适用范围** | **48 小时（`2880` 分钟）**内完成该动作 | **`P1`–`P4`（含 `P4 观察`）全部适用**，绝不得以「观察」标签免除法定义务；超时扫描（含 `MANUAL_REVIEW` 活跃单）只标记 `is_sla_overdue=true` 并通知，**不自动关闭或降级** |

**② 无分钟级响应规定**：郑州市令第 152 号（及河南省实施细则豫建城〔2010〕52 号、住建部令第 4 号）均未规定故障等级数量与分钟级响应/到场时限。系统已**整体废除**无依据的四档分钟数（`15m/2h`、`30m/8h`、`2h/24h`、`8h/72h`），`sla_policy.response_minutes` 为 `null`，`fault_tickets.response_deadline_at` 默认 `NULL`（不编造分钟数；派工记录 `accept_deadline_at` 属防死锁内部边界，默认取所属票 `repair_deadline_at`）。

**③ 紧急抢险与 24 小时行政手续义务（不编造分钟数）**：
- 郑州市令第 152 号第 24 条第 2 款：「因自然灾害和其他原因严重危及城市照明设施安全运行,为紧急抢险确需掘动道路、花坛、草坪或砍伐、修剪树木的,城市照明管理机构应当及时采取紧急措施进行处理,并在24小时内告知相关部门,补办相关手续。」
- 住建部令第 4 号《城市照明管理规定》（2010-05-27 发布，2010-07-01 施行，<https://www.gov.cn/zhengce/2022-01/25/content_5711971.htm>）第 29 条：「损坏城市照明设施的单位和个人，应当立即保护事故现场，防止事故扩大，并通知城市照明主管部门。」
- 业务落点：对 `safety_flag=true` 或 `fault_type='ELECTRIC_HAZARD'` 工单，系统置 **`emergency_action_required=true`**（UI 强提示「立即保护现场并采取紧急措施」+ 通知区域管理员）并保留 `24` 小时行政告知补办手续提示（`emergency_notice_hours=24`），**不生成分钟级响应 deadline**，也不将 24h 手续义务写入 `repair_deadline_at`。

**④ 宏观亮灯率指标（第 22 条，非单票时限）**：第 22 条要求「保证不低于 97% 的路灯在规定的时间内亮灯」，属于**城市宏观整体考核指标**，而非单张工单修复时限，也不得与维修员准时率（`accounts.on_time_rate`）混淆。

### 7.2 行业团体标准与外地规则隔离边界

> **以下团体标准与外地规则对郑州市均无强制力，本项目一律不采用其数字，仅作行业背景对照。**

| 层级 | 文件与出处 | 关键条文（背景对照） | 隔离禁令（禁止误用） |
|---|---|---|---|
| **团体标准** | `T/CMEA 21—2021`《城市道路照明设施运行维护技术标准》（<https://cgj.sz.gov.cn/attachment/1/1238/1238599/10388154.pdf>） | **§4.4.5**：「1 一般故障报修不应超过 24h；2 系统性故障和重大故障报修应在 2h 内到达现场处理，并应采取临时措施亮灯，因设备和器件损坏原因应在 48h 内修复；3 强台风、强降雨等不可抗力因素造成的严重故障或设施损坏，应于 5 天内修复。」；**§8.0.2**（亮灯率 ≥98%/96%，完好率 ≥95%） | ① **「2h」是到达现场处理，不是修复完毕，也不是系统接单**；② 郑州灯饰处系参编单位，但**参编 ≠ 郑州采纳**；③ **严禁把附录 B 运维考核分或附录 D.0.5 完好率权重（`0.25·M1 + 0.15·M2 + 0.40·M3 + 0.20·M4`）误用为单票故障分级权重** |
| **外地规则** | 深圳市政府令第 309 号第 24 条；北京 `DB11/T 1876-2021` §5.5.6 / §8.2 | 深圳：单盏 24h / 线路 3 日，亮灯率 98%/96%；北京：一般 24h / 复杂 48–72h / 到场 45–90min，亮灯率 99.5%/98.5%/98%/96% | 均属外地规定，**不适用于郑州**，不进入本系统配置 |

### 7.3 业务规则定案汇总

| 裁决号 | 事项 | 最终定案与业务边界 |
|---|---|---|
| **`D-12`** | 优先级标识与显示名 | ✅ 稳定键名 `P1`–`P4`（借自国际运维实践，非中文规范用语）与 `display_name` 分离；界面终局显示名为 **`特急 / 加急 / 普通 / 观察`** |
| **`D-13`** | 故障分级方法 | ✅ 废除原浮点加权与 `100×100` Bug 及 `(0.45,0.25,0.20,0.10)` / `(85,65,40)` / `35` 魔数，改用**零魔数可解释的表驱动确定性规则分级 `grading_rules`**（`R1`–`R4` 覆盖全部 144 组合，未绑定资产时 `asset_importance` 为 `NULL`、`grading_score` 写 `NULL`，支持管理员附理由 `regrade`）。具体规则表、144 组合验证与快照结构见 `docs/DESIGN.md` §3.3 |
| **`D-14`** | 最大返修轮次 | ✅ `rework_policy.max_rework_rounds = 2`。第 3 次驳回（`round_no > 2`）仍生成第 3 张返修单并置 `IN_MANUAL_REVIEW`，工单强制转 `MANUAL_REVIEW` 交管理员人工复核裁决退出 |
| **`D-15`** | AI 候选过期时间 | ✅ `ai_candidate_ttl_hours = 72`。超过 72 小时未确认自动置 `CANCELLED` 并通知 |
| **`D-16`** | 维修员在途派工上限 | ✅ `accounts.max_active_dispatch` 默认 **`3` 单**（DB `CHECK` 约束允许配置范围 `1..20`） |
| **`D-17`** | 亮灯率 / 完好率 KPI | ✅ **不实现**亮灯率/完好率 KPI 考核（需全量点位亮灭普查、超出静态巡查边界且无第 18 张表；KPI 由外部系统负责，`accounts.on_time_rate` 仅指维修员相对法定 48h 的准时率；若外部系统在郑州考核亮灯率，法定口径为郑州令第 152 号第 22 条 **97%**，非团标 98%/96%） |

---

## 8. 4 周验收重点的落地映射（用户原话逐项）`[穷举]`

用户原话：
> 「4周验收重点：完成"报障→分级派工→维修→验收→关闭或返修"的端到端演示；验证区域派单、故障分级、返修闭环，覆盖权限拒绝、重复提交和至少一种异常恢复场景。」

> **`[穷举]` 口径声明**：下表 **7 行 = 用户原话要求全部 4 周验收重点项**（分子 7 / 分母 7 / 排除项 0）。

| 验收项 | 承载 `FR` | 承载 `VC` | 测试层 | `docs/DESIGN.md` 落地章节 |
|---|---|---|---|---|
| **端到端演示**（报障→分级派工→维修→验收→关闭或返修） | `FR-11`–`FR-27` | `VC-07`–`VC-23` | E2E 主链路 | §2 状态机、§3 业务机制、§4 Schema、§5 视觉管线 |
| **区域派单**（非 CRUD 机制一） | `FR-21` | `VC-16` | L2 + L4 | §3.2 机制一（区域授权派单）、§4 Schema |
| **故障分级**（非 CRUD 机制二） | `FR-16`、`FR-17`、`FR-18` | `VC-11`、`VC-12`、`VC-13` | L1 + L2 + L4 | §3.3 机制二（`grading_rules` + 郑州 48h）、§4 Schema |
| **返修闭环**（非 CRUD 机制三） | `FR-26`、`FR-27` | `VC-22`、`VC-23` | L2 + L4 | §2.3 返修状态机、§3.4 机制三、§4 Schema |
| **权限拒绝** | `FR-03`、`FR-07`、`FR-21` | `VC-02`、`VC-05`、`VC-16` | L2 + L4 | §3.2 双层权限校验（`403` vs `422`） |
| **重复提交** | `FR-19`、`FR-20` | `VC-14`、`VC-15` | L2 + L4 | §3.5 幂等与点位防重、§4 部分唯一索引 |
| **至少一种异常恢复场景** | `FR-15`（视觉降级）、`FR-23`（拒单回退）、`FR-28`（撤销/复核）、`FR-29`（SLA 超时/死信重试） | `VC-26`、`VC-18`、`VC-24`、`VC-25`、`VC-29` | L2 + L4 | §2.1–§2.4 状态机无死路路径、§5.2 视觉降级契约（D1–D3） |

> **「至少一种」的余量**：本设计提供**四种**互不相同的异常恢复路径（视觉降级直转人工报障 / 维修拒单回退或累计转复核 / 派工前撤销与 `MANUAL_REVIEW` 三出口 / 郑州 48h SLA 超时标记与通知死信重试）。验收时至少演示一种即可，但**四种都必须有自动化测试覆盖**。

---

## 9. 待用户裁决清单 → 见 `docs/DECISION.md`

本文件相关的 4 项（`RQ-1`–`RQ-4`）已全部并入 **`docs/DECISION.md`**（全项目唯一权威清单），且**均已定案**：

| 原编号 | 事项 | `DECISION.md` | 裁决状态 |
|---|---|---|---|
| `RQ-1` | 亮灯率 / 设施完好率 KPI 考核 | **`D-17`** | ✅ **已定案：不实现**（用户选 `A`） |
| `RQ-2` | `FR-12` 破损识别是否承诺检出率 | **`D-18`** | ✅ **已定案：不承诺**（用户选 `A`） |
| `RQ-3` | `P1`–`P4` 改用中文等级命名 | **`D-12`** | ✅ **已定案：维持「特急 / 加急 / 普通 / 观察」**（用户：「是最终名字」） |
| `RQ-4` | 规则与 SLA 口径定案 | **`D-11`** / **`D-13`** / **`D-14`** / **`D-15`** | ✅ **全部定案**（`D-11` 郑州 48h 法定基线、`D-13` 确定性规则分级 `grading_rules`、`D-14` 2 轮、`D-15` 72h） |

> 另：`D-29`（个人本机项目直接使用公开/自采数据集）与 `D-30`（回传 `streetlight_on` 且 `is_fault_candidate=false`）亦已在 `docs/DECISION.md` 获得用户批示定案（详见 §6.2）。**本文件不再保留第二套裁决表。**

---

## 10. 本文档的诚实边界

**已证实的（设计已定案且有出处支撑）**：
- 3 个角色、9 个 `UC`、29 个 `FR`、29 条 `VC`、17 张业务表的编号与口径，且均已对齐原始 18 列需求与 `UC-09` 横切裁决。
- 郑州市法定时限唯一基线（市政府令第 152 号 / 2020 第 237 号修正第 19 条第 3 款 `48h`「维修养护或督促」、第 22 条 `97%` 宏观亮灯率、第 24 条第 2 款及住建部令第 4 号第 29 条紧急抢险与 24h 补办手续），以及 `T/CMEA 21—2021` / 外地规则的隔离边界（§7.1–§7.2）。
- 全部业务策略与智能边界定案：`D-02`（15 MiB）、`D-03`（`1..10` 张）、`D-11`–`D-18`、`D-29`–`D-31`、`D-36`。

**未证实的（设计成立但无运行验证）**：
- **所有 29 条 `FR` 状态均为「未实现」**：`FR-01`–`FR-29` 的实际接口响应、状态流转、权限拦截、并发幂等与异常恢复均**没有任何实跑证据**。
- `vision/` 目录下的 8 个 `.py` 文件为**未经运行验证、未经测试的预研草稿**（§6.2），不计入阶段一交付。
- 郑州市城管局公开清单中未逐件取得全文的综合市政监管文件（见 `.team/notes/research/zhengzhou-lighting-rules.md`）尚未穷尽，部门内部非公开考核合同不在本规格承诺范围内。

**未运行 / 未审计范围**：
- **阶段一无任何应用源码与运行态**：`web/`、`tests/`、`migrations/` 在阶段一冻结时均不存在（用户现已放行阶段二，Python 源码按 `src layout` 落于 `src/tomori/`）；17 表 DDL 从未在 PostgreSQL 上执行，27 行（30 实验）变异卡与 Playwright E2E 剧本**全部未运行**。

**明确不属于本文档的（跨文档指针）**：
- 系统架构、4 套状态机（29 状态 / 36 合法边 / 11 终态 / `I1`–`I8`）、三大业务机制算法细节、17 表 Schema 与技术栈选型 → `docs/DESIGN.md`
- 57 条路由行（展开为 60 个 `(method, path)` 端点）、34 个 `error.code`、幂等契约与 128 个冻结 `data-testid` → `docs/API.md`
- 全项目 40 条裁决台账与用户原话档案 → `docs/DECISION.md`
- 本地 PG 栈部署、端口分配与远端 GPU 训练/产物回传运维 → `docs/RUNBOOK.md`
- 团队阵容、写入域边界、提交协议与反假绿质量体系 → `docs/team/TEAM.md`、`docs/team/LEAD.md`（本地过程产物，已 `.gitignore` 不入库）

---

## 11. 变更记录

| 日期 | 变更 | 触发 |
|---|---|---|
| 2026-11-21 | 初版。9 UC / 29 FR / 29 VC 追溯矩阵、角色与权限双层模型、4 类「不做」、智能边界、行业阈值三源印证、4 周验收映射 | `task-7` |
| 2026-11-24 | **按用户指示同步定案（`task-26` + `task-29`）**：① 修正 §4 表头 `43 周` → `4 周` 笔误；② 新增 §7.0 法源分层，明确**唯一生效为郑州令第 152 号 `48h`**，团体标准与外地规则均不采用；③ 更新 `FR-16`/`FR-17` 为表驱动确定性规则分级 `grading_rules` + 郑州 `48h` `repair_deadline_at`；④ 同步 §7.3、§9、§10 中 `D-11`/`D-12`/`D-13`/`D-14`/`D-15`/`D-17`/`D-18` 定案状态 | `task-26` + `task-29` |
| 2026-11-24 | **5 文档整合定稿（`task-44`）**：① 顶部状态更新为「阶段一定稿（待用户放行阶段二实现）」；② §0 对齐 `TEAM.md` §5.3、补全 `[穷举]` 标注并指向 `docs/DESIGN.md` §4；③ §1 吸纳 `scope_type` 有效期与推荐/指派/接单三处强校验语义；④ §3/§4/§5 合并原 `BUSINESS-LOGIC.md` §11 为五项核心红线 `N1`–`N5`（转义正则竖线），补齐 `D-02`/`D-03`/`D-14`–`D-16` 参数与 `grading_rules`/郑州 48h 术语；⑤ §6/§7 完整吸收原 `BUSINESS-LOGIC.md` §7.1/§7.9 郑州 48h「维修养护或督促」双合规语义、第 24 条与住建部令第 4 号第 29 条紧急抢险 24h 手续、团标/外地规则隔离边界及 `D-29`/`D-30`/`D-31` 定案；⑥ §8–§10 切换为最终 5 文档交叉引用体系 | `task-44` |
| 2026-11-24 | **终审指针修正（`task-52`）**：修正 §8 验收映射表中「重复提交」（原误写 `§3.1` → `§3.5 幂等与点位防重`）与「至少一种异常恢复场景」（移除原误写的 `§3.5` 错位指针，统一指向 `§5.2 视觉降级契约（D1–D3）`）的 `docs/DESIGN.md` 节号错配 | `task-52` |
| 2026-11-24 | **跨文档口径同步（`task-58`）**：同步 `docs/API.md` 路由展开统计口径，将 §10 跨文档指针中 `(method, path)` 展开口径更新为「57 条路由行（展开为 60 个 `(method, path)` 端点）」（与 `README.md` 同步） | `task-58` |
