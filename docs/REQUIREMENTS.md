> 状态：阶段一定稿候选 —— 待用户审阅裁决（未定稿）

# 需求规格（REQUIREMENTS）

> **文档归属**：`docs-writer`｜**本文件写入域**：`docs/REQUIREMENTS.md`
> **读者**：用户（裁决）、`auditor`、`tester`、全体实现者
> **上游素材**：`.team/notes/tester/stage1-test-matrix-input.md`（可验证判据 `VC-01..VC-29`）、`.team/notes/backend-dev/stage1-backend-spec-api.md` §12（UC↔FR 对账）、`.team/notes/vision-dev/stage1-vision-spec-input.md`（智能边界）、`.team/notes/research/standards-and-stack.md`（行业阈值）、`.team/notes/research/datasets.md`（数据集合规）
> **本文档地位**：`UC` / `FR` 的**精确编号与标题以本文件为唯一权威**（`tester` 素材 §三 口径声明的要求）。其他文档引用 `FR-xx` 时以本文为准。

---

## 0. 统计口径分层声明（`docs/team/TEAM.md` §4.6 硬要求）

**任何数字在本文件出现，必须同时给出分子、分母、排除项三层。**

| 项 | 分子 | 分母 | 排除项 |
|---|---:|---|---|
| 核心用例 | **9**（`UC-01`..`UC-09`） | 用户「建议用例数：9」 | 无 |
| 功能需求 | **29**（`FR-01`..`FR-29`） | 用户「建议功能需求数：29」 | 无 |
| 穷举标注 | 9 个 UC | `docs/team` 方法论 | **本表是穷举，有 `UC-09` 并列** |
| 角色 | **3**（`ADMIN`/`INSPECTOR`/`TECHNICIAN`） | 用户「主要角色：运维管理员、巡查员、维修人员」 | 无 |
| 业务表 | **17** | 用户「建议核心数据表（17）」 | `alembic_version`（迁移元数据表）；PG 自动 `*_pkey` 索引；FK 支撑索引。**没有第 18 张业务表。** |
| 可验证判据 | **29**（`VC-01`..`VC-29`） | 与 `FR-01`..`FR-29` 一一绑定 | 无（`VC-xx` 是 `FR-xx` 的验收判据，不是新增需求） |

> **`alembic_version` 的口径声明**：迁移工具的版本记录表，仅一列 `version_num varchar(32)`，**不承载业务语义、不参与业务查询**。任何「数表」断言必须显式排除：`WHERE table_name <> 'alembic_version'`。
>
> **阶段一无数据库**，故「17 表」是**设计口径**（用户给定清单），不是 `information_schema` 实测值。任何把 17 说成「实测结果」的表述都是错的。

---

## 1. 三个角色与权限边界

| 角色 | 代码 | 职责 | 用户原话依据 |
|---|---|---|---|
| 运维管理员 | `ADMIN` | 配置运维区域/点位/资产、分配授权、派工与改派、处置 `MANUAL_REVIEW`、配置业务规则、查看审计与通知 | 「面向运维管理员…完成报障→分级派工→维修→验收→关闭或返修闭环」 |
| 巡查员 | `INSPECTOR` | 上传静态巡查图片生成 AI 候选、人工确认/修正/剔除、纯人工报障、验收（通过/驳回）、撤销误报 | 同上 +「维修工单须经巡查员确认」 |
| 维修人员 | `TECHNICIAN` | 接单/拒单、登记维修过程与材料、填写电气安全检查清单、提交完工、升级上报 | 同上 |

### 1.1 权限模型：RBAC + **区域授权**双层

| 层 | 机制 | 拒绝时的错误码 |
|---|---|---|
| 第一层 | **角色**（`ADMIN`/`INSPECTOR`/`TECHNICIAN`）：接口级准入 | `403 FORBIDDEN_ROLE` |
| 第二层 | **业务范围授权**（`scope_authorizations`，绑定 `account_id` + `region_id` + `scope_type`）：同一角色在不同区域权限不同 | `403 FORBIDDEN_REGION_SCOPE` |
| 第三层 | **账号状态**（`accounts.status`） | `403 ACCOUNT_DISABLED` |

**`scope_type` 三类**：

| `scope_type` | 含义 | 典型角色 |
|---|---|---|
| `INSPECT` | 可巡查、可确认、可验收该区域 | `INSPECTOR` |
| `REPAIR` | 可被派工到该区域 | `TECHNICIAN` |
| `MANAGE` | 可配置该区域、可派工 | `ADMIN` |

> ⚠️ **两层错误码必须严格区分，这是 `tester` 明确要求的**：
> - **操作人自己无权限** → `403 FORBIDDEN_REGION_SCOPE`
> - **被指派人（维修员）不合格** → `422 DISPATCH_TECHNICIAN_NOT_ELIGIBLE`（`details.reason` ∈ `NO_REGION_SCOPE` / `SCOPE_EXPIRED` / `ACCOUNT_INACTIVE` / `WRONG_ROLE` / `OVERLOADED`）
>
> 把这两者混为一谈会导致「管理员以为维修员不在自己辖区」这种无法排查的错误。**`403` 说的是「你不能这么做」，`422` 说的是「你要求我做的这件事，参数本身不成立」。**

---

## 2. 九个核心用例（UC-01..UC-09）

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

### 2.1 `UC-09` 的特殊性（**已由 Lead 裁决，不再未决**）

**Lead 裁决（2026-11-21 07:42 +08:00）**：`UC-09` = **系统级横切用例**（操作审计 + 通知任务）。

- **不对应单一 API 前缀**，而是被 `UC-01..UC-08` 各主流程在写操作中**隐式调用**的旁路能力：每次业务变更同事务写 `audit_logs` + 按需创建 `notification_tasks`。
- 同时提供**独立的查询与重试端点**：`/audit-logs`、`/notifications*`（含 `POST /notifications/{id}/retry` 死信重试、`POST /notifications/{id}/acknowledge` 人工确认）。
- 它**不是**「先做完前 8 个再做的第 9 步」，而是**贯穿全部 8 个用例的横切关注点**。

> 计数口径：上表 **9 行 = 9 个 UC**，分子 9 / 分母 9 / 排除项 0。`UC-09` 不是「额外补充」，它是并列的第 9 个。

---

## 3. 二十九条功能需求（FR-01..FR-29）

> **编号权威**：本表。`tester` 的 `VC-xx` 与本表 `FR-xx` **一对一绑定**（见 §4 追溯矩阵）。
> **验收判据**列引用 `VC-xx`，详细判据见 §4。
> **状态**列：本阶段全部为 `未实现` —— **阶段一只做设计，没有任何可运行代码**。

### 3.1 UC-01 认证与授权（FR-01..FR-04）

| FR | 需求 | 验收判据 | 状态 |
|---|---|---|---|
| `FR-01` | 三角色账号登录，签发 JWT（`HS256`，载荷含 `sub`/`tv`/`role`/`exp`） | `VC-01` | 未实现 |
| `FR-02` | 禁用账号（`status != ACTIVE`）拒绝登录 | `VC-01` | 未实现 |
| `FR-03` | RBAC 接口级角色权限校验，越权返回 `403 FORBIDDEN_ROLE` 且 DB 零脏写 | `VC-02` | 未实现 |
| `FR-04` | 管理员分配/停用/设有效期业务范围授权（绑定运维区域），变更写 `audit_logs` | `VC-03` | 未实现 |

### 3.2 UC-02 档案管理（FR-05..FR-10）

| FR | 需求 | 验收判据 | 状态 |
|---|---|---|---|
| `FR-05` | 运维区域增改查；`region_code` 唯一（重复 → `409`） | `VC-04` | 未实现 |
| `FR-06` | 照明点位档案维护（绑定区域、路段等级、坐标/杆号） | `VC-05` | 未实现 |
| `FR-07` | 区域外操作点位 → `403 FORBIDDEN_REGION_SCOPE` | `VC-05` | 未实现 |
| `FR-08` | 设施资产台账（灯具/灯杆/配电箱…）绑定照明点位 | `VC-06` | 未实现 |
| `FR-09` | 资产重要度（`CRITICAL`/`HIGH`/`NORMAL`）可被分级规则读取 | `VC-06` | 未实现 |
| `FR-10` | 业务范围授权的增删改查与区域绑定校验 | `VC-03` | 未实现 |

### 3.3 UC-03 智能报障（FR-11..FR-15）

| FR | 需求 | 验收判据 | 状态 |
|---|---|---|---|
| `FR-11` | **仅接受静态图片**：JPEG/PNG/WebP；非图片/伪造扩展名/视频容器/`rtsp://` → `415` 硬拒绝 | `VC-07` | 未实现 |
| `FR-12` | 视觉返回 `FaultCandidateResult`（`bbox_norm`/`bbox_px`/置信度/昼夜场景保护标志），恒声明 `requires_inspector_confirmation=true` 与 `replaces_electrical_safety_check=false` | `VC-08` | **未实现（`vision/` 仅有未验证草稿，见 §6.3）** |
| `FR-13` | 巡查员逐条确认/修改/剔除 AI 候选，或纯人工补充报障 | `VC-09` | 未实现 |
| `FR-14` | **未确认禁派工**：`confirmed_by_inspector=false` 的 `AI_CANDIDATE_PENDING` 严禁进入派工流程（前端禁用 + 后端 `422 AI_CANDIDATE_NOT_CONFIRMED` + DB `CHECK` 三层） | `VC-10` | 未实现 |
| `FR-15` | **纯人工报障永不依赖视觉可用性**：视觉失败时巡查员可跳过 AI 直接建单 | `VC-26` | 未实现 |

### 3.4 UC-04 分级与 SLA（FR-16..FR-18）

| FR | 需求 | 验收判据 | 状态 |
|---|---|---|---|
| `FR-16` | 管理员可配置并启停业务规则（分级权重/SLA 时限/派单权重/返修阈值/AI 候选 TTL），变更写 `audit_logs`；**规则载荷 Pydantic 结构化校验**，非法 → `422 INVALID_RULE_PAYLOAD` | `VC-11` | 未实现 |
| `FR-17` | 确认报障瞬间依规则 + 点位等级 + 资产重要度 + 故障类型自动计算 `P1`–`P4` 与两个 SLA deadline，固化**分级快照** | `VC-12` | 未实现 |
| `FR-18` | 后续修改规则**不得追溯破坏**已生成工单的历史快照；手工调级须附必填理由并落审计 | `VC-13` | 未实现 |

### 3.5 UC-05 防重与派工（FR-19..FR-21）

| FR | 需求 | 验收判据 | 状态 |
|---|---|---|---|
| `FR-19` | **幂等**：同 `Idempotency-Key` + 同载荷并发/重放仅建 1 单；同键异体 → `422 IDEMPOTENCY_PAYLOAD_MISMATCH`；原请求未完 → `409 IDEMPOTENCY_REQUEST_IN_PROGRESS`；缺键 → `400 IDEMPOTENCY_KEY_REQUIRED` | `VC-14` | 未实现 |
| `FR-20` | **点位防重**：同一点位同类活跃单重复报障 → `409 DUPLICATE_ACTIVE_TICKET`（返回既有单引用）；PG 部分唯一索引并发兜底 | `VC-15` | 未实现 |
| `FR-21` | **区域授权派工**：只能派给持有该区域 `REPAIR` 有效授权的维修员；被指派人不合格 → `422 DISPATCH_TECHNICIAN_NOT_ELIGIBLE`；操作人无权限 → `403 FORBIDDEN_REGION_SCOPE`；同票单活跃派工 | `VC-16` | 未实现 |

### 3.6 UC-06 维修与安全门禁（FR-22..FR-24）

| FR | 需求 | 验收判据 | 状态 |
|---|---|---|---|
| `FR-22` | 授权维修员查看本人 `PENDING_ACCEPT` 派工并接单，派工 → `ACCEPTED`，票 → `IN_REPAIR` | `VC-17` | 未实现 |
| `FR-23` | 维修员附必填理由拒单，派工 → `REJECTED`，票自动回退 `PENDING_DISPATCH` 并通知改派；累计拒单 ≥ 阈值（默认 3）→ 反而转 `MANUAL_REVIEW` | `VC-18` | 未实现 |
| `FR-24` | 登记维修过程与材料使用（用量非负 DB 校验）；**电气安全检查硬门禁**：`electrical_safety_checked=false` 或清单缺失时严禁完工（前端 disabled + 后端 `422 ELECTRICAL_SAFETY_CHECK_REQUIRED` + DB `CHECK` 三层） | `VC-19`、`VC-20` | 未实现 |

### 3.7 UC-07 验收与返修（FR-25..FR-27）

| FR | 需求 | 验收判据 | 状态 |
|---|---|---|---|
| `FR-25` | 巡查员对本区域 `PENDING_ACCEPTANCE` 工单验收通过，落 `acceptance_records` 并将票推进 `CLOSED`（释放点位防重锁） | `VC-21` | 未实现 |
| `FR-26` | 验收驳回（附原因）在**单事务内**完成：写验收记录 + 生成 `rework_orders(PENDING_DISPATCH)` + `rework_count += 1` + 票进 `REWORK_PENDING_DISPATCH` | `VC-22` | 未实现 |
| `FR-27` | 返修单依次 `DISPATCHED` → `IN_REPAIR`（再次过安全检查门禁）→ `PENDING_ACCEPTANCE` → 二次验收通过后返修单 `PASSED` 且票 `CLOSED`；**超 `max_rework_rounds`（默认 2）轮 → 强制 `MANUAL_REVIEW`** | `VC-23`、`VC-25` | 未实现 |

### 3.8 UC-08 异常恢复（FR-28..FR-29）

| FR | 需求 | 验收判据 | 状态 |
|---|---|---|---|
| `FR-28` | 派工前撤销误报 → `CANCELLED`（派工态联动 `CANCELLED`），释放点位锁；`MANUAL_REVIEW` 三出口（`APPROVE_REASSIGN`/`SPECIAL_CLOSE`/`TERMINATE_CANCEL`）；`IN_REPAIR` 可升级 `escalate` → `MANUAL_REVIEW` | `VC-24`、`VC-26`、`VC-28` | 未实现 |
| `FR-29` | **审计与通知横切**：每次状态变更同事务写且仅写 1 条 `audit_logs` + 按需 `notification_tasks`；`POST /sla/scan`（ADMIN 手动触发）标记超时；通知死信可重试/确认 | `VC-25`、`VC-27`、`VC-29` | 未实现 |

---

## 4. 追溯矩阵：`UC` ↔ `FR` ↔ `VC` ↔ 测试层次

> **三层口径**：分子 = 绑定成功的行数；分母 = 29 个 `FR`；排除项 = 无（29 个 `FR` 全部有判据）。

| `FR` | `UC` | `VC` | 测试层 | 43 周验收重点 |
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
| `FR-17` | UC-04 | `VC-12` | L1+L2+L4 | **故障分级** |
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
| `FR-29` | UC-08/UC-09 | `VC-25`+`VC-27`+`VC-29` | L1+L2+L4 | **异常恢复** |

> **测试分层定义**（转写自 `.team/notes/tester/stage1-test-matrix-input.md`）：
> - **L1** 纯函数单测（`grading` / `dispatch_scoring` / `sla` 计算，无 DB）
> - **L2** 集成测试（真实 PostgreSQL + `TestClient(ASGITransport)`）
> - **L3** 视觉模块测试（`vision/` 内部，可用合成图）
> - **L4** E2E（Playwright，真实浏览器 + 真实后端）

---

## 5. 四类显式「不做」清单（用户原话，**不可扩展**）

| # | 不做 | 用户原话 | 本设计的保证方式 |
|---:|---|---|---|
| **N1** | **不接灯控设备** | 「不接灯控设备」 | 无任何灯控协议/IoT 客户端依赖；无远程控制端点；无设备指令表 |
| **N2** | **不做能耗预测** | 「不做能耗预测」 | 无时序预测库；无能耗相关表；`power_density` 类字段不作预测输入 |
| **N3** | **不接实时监控** | 「不接实时监控或远程灯控」 | 无 RTSP/流媒体依赖；`415 MEDIA_VIDEO_STREAM_FORBIDDEN` **主动拒绝**视频容器与 `rtsp://`/`m3u8`/`stream_url`/`camera_*` 字段 |
| **N4** | **AI 不替代电气安全检查** | 「模型不能替代电气安全检查」 | 电气安全检查是维修员填写的 5 项清单，与视觉模块**零耦合**；`replaces_electrical_safety_check` 恒为 `false`；DB `CHECK` 兜底 |

> ⚠️ **N3 的落地细节**：拒绝不是「忽略」，而是**显式报错**。用户传 `.mp4` 或带 `rtsp://` 字段，系统返回 `415`，而不是静默丢弃。这样「不做实时监控」才是可审计的，而不是靠代码里没写来实现。

---

## 6. 智能能力的需求边界（`FR-11`–`FR-15` 的约束）

### 6.1 用户原话逐字

> 「智能子任务：从巡查照片中识别灯具亮灭、明显破损或遮挡，辅助生成故障候选。智能业务范围与边界：只处理静态巡查图片，不接实时监控或远程灯控；维修工单须经巡查员确认，模型不能替代电气安全检查。」

### 6.2 三条不可违反的约束（与 `docs/TECH-STACK.md` §2.3 一致）

| # | 约束 | 落地 | 违反后果 |
|---:|---|---|---|
| V1 | 只处理静态图片 | 服务端魔数+帧数校验；视频/流 → `415` | 违反 N3 |
| V2 | AI 候选须经巡查员确认才能派工 | 状态机 + 服务层 + DB `CHECK` 三层 | 模型直接驱动业务 = 越权 |
| V3 | 模型不得替代电气安全检查 | 安全检查与视觉零耦合；DB `CHECK` 兜底 | 拿 AI 当安全认证 = 事故责任 |

### 6.3 ⚠️ `vision/` 目录当前状态（**表述纪律**）

| 事实 | 值 |
|---|---|
| 目录 | `vision/` |
| 内容 | **8 个 `.py`**：`__init__/config/contract/errors/image_io/metrics/whitelist/baseline` |
| 来源 | **已撤回的 `task-10`** 的预研草稿 |
| 运行状态 | **未经运行验证、未经测试** |
| 交付状态 | **未纳入阶段一交付范围** |
| ✅ 唯一正确表述 | 「阶段二规划目录；当前存在**未经运行验证的预研草稿**，尚未纳入交付范围」 |

**禁止的表述**：❌「视觉模块已实现」❌「`vision/` 已完成 8 个模块」❌ 把 8 个 `.py` 计入阶段一交付进度 ❌ 在本文档「状态」列给 `FR-12` 打勾。

> **`FR-12` 的真实状态是「未实现」**，与 `FR-01` 到 `FR-11` 完全一样。阶段一的真实状态是：**只有设计，没有可运行的视觉代码。**

### 6.4 能力承诺的诚实边界

| 能力 | 阶段一能否承诺 | 依据 |
|---|---|---|
| 灯具**亮/灭**识别 | 🟡 有 A 档数据支撑（`ngocleltt/lamp-state-detection`，8,428 张，CC-BY-4.0） | `.team/notes/research/datasets.md` §3.1 |
| 灯具**遮挡**识别 | 🟡 同一数据集含 `lamp_occluded` 类 | 同上 |
| 灯具**破损**识别 | 🔴 **不能承诺**。破损类**全球无 A 档公开数据，必须自采** | 同上，34 组 HF 检索 + Roboflow + GitHub 三平台均无带许可证的破损类来源 |
| 检出率/精确率指标 | 🔴 **阶段一不给数字**。无训练、无验证集、无实测 | 未做训练 |

> **这条必须写进需求文档**：需求里可以写「系统**应支持**从静态照片识别破损」，但**不能写「识别准确率不低于 X%」**——那需要实测，而阶段一没有训练。`F14` 禁止把未验证的数字当需求承诺。

---

## 7. 行业规范对齐（阈值出处）

> 完整证据见 `.team/notes/research/standards-and-stack.md` §2/§3/§6。

### 7.1 亮灯率与设施完好率（**三源印证**）

| 指标 | 阈值 | 出处 |
|---|---|---|
| 快速路、主次干道**亮灯率** | **≥ 98%** | `T/CMEA 21—2021` §8.0.2 第 1 款；深圳市《城市照明管理办法》（市政府令第 309 号）第二十四条；`DB11/T 1876-2021` §8.2（一级 ≥98.5%、二级 ≥98%） |
| 支路**亮灯率** | **≥ 96%** | 同上（T/CMEA §8.0.2；深圳令 309 号 §24；DB11 其他类 ≥96%） |
| **设施综合完好率** | **≥ 95%** | `T/CMEA 21—2021` §8.0.2 第 2 款 |
| 计算公式（完好率） | `0.25·M1 + 0.15·M2 + 0.40·M3 + 0.20·M4` | `T/CMEA 21—2021` 附录 D.0.5。M1 配电设备 25% / M2 线路管道工井 15% / M3 照明器具 40% / M4 灯杆及金属构件 20% |

### 7.2 故障响应与修复时限

| 故障类别 | 时限 | 出处（逐字） |
|---|---|---|
| 一般故障报修 | **≤ 24h** | `T/CMEA 21—2021` §4.4.5 第 1 款 |
| 系统性故障、重大故障 | **2h 内到场** + 采取临时措施亮灯；设备/器件损坏原因 **48h 内修复** | 同上 §4.4.5 第 2 款 |
| 不可抗力（强台风/强降雨）严重故障 | **5 天内修复** | 同上 §4.4.5 第 3 款 |
| 单盏灭灯等简单故障 | **24 小时内**处理完毕 | 深圳市《城市照明管理办法》第二十四条 |
| 线路、设备损坏等较复杂故障 | **3 日内**处理完毕 | 同上 |
| 一般故障（北京） | **24h 内**；复杂故障宜 **48h**、**不超过 72h**；到场 45min（特殊地区 90min） | `DB11/T 1876-2021` §5.5.6 |

> ⚠️ **`P1`–`P4` 分级与上表的映射关系见 `docs/BUSINESS-LOGIC.md`**。本文档只给规范阈值，不定义系统分级。

### 7.3 ⚠️ 亮灯率 KPI 是否实现（**待用户裁决，本文档不承诺**）

| 事实 | 影响 |
|---|---|
| `T/CMEA 21—2021` §8.0.2 要求亮灯率考核 | 行业惯例 |
| **计算亮灯率需要全量点位的亮灭状态普查** | **超出**本项目「只处理静态巡查图片」的业务边界 |
| 17 张表中**无亮灯率统计表** | 若实现需加第 18 张表，违反用户给定的 17 表口径 |
| `accounts.on_time_rate` 是**维修员准时率** | 与亮灯率**不是同一指标**，不可混用 |

**结论**：亮灯率/完好率作为**行业背景与本系统的设计参照**记录，**阶段一不实现为功能需求**。若用户要求实现，需新增统计表 → 会突破 17 表口径 → **须用户裁决**。已列入 §9 待裁决。

---

## 8. 4 周验收重点的落地映射（用户原话逐项）

用户原话：
> 「4周验收重点：完成"报障→分级派工→维修→验收→关闭或返修"的端到端演示；验证区域派单、故障分级、返修闭环，覆盖权限拒绝、重复提交和至少一种异常恢复场景。」

| 验收项 | 承载 `FR` | 承载 `VC` | 承载测试 |
|---|---|---|---|
| **端到端演示**（报障→分级派工→维修→验收→关闭或返修） | `FR-11`–`FR-27` | `VC-07`–`VC-23` | E2E 主链路 |
| **区域派单**（非 CRUD 机制一） | `FR-21` | `VC-16` | L2 + L4 |
| **故障分级**（非 CRUD 机制二） | `FR-16`、`FR-17`、`FR-18` | `VC-11`、`VC-12`、`VC-13` | L1 + L2 + L4 |
| **返修闭环**（非 CRUD 机制三） | `FR-26`、`FR-27` | `VC-22`、`VC-23` | L2 + L4 |
| **权限拒绝** | `FR-03`、`FR-07`、`FR-21` | `VC-02`、`VC-05`、`VC-16` | L2 + L4 |
| **重复提交** | `FR-19`、`FR-20` | `VC-14`、`VC-15` | L2 + L4 |
| **至少一种异常恢复场景** | `FR-15`（视觉降级）、`FR-23`（拒单回退）、`FR-28`（撤销/复核）、`FR-29`（SLA 超时/死信重试） | `VC-26`、`VC-18`、`VC-24`、`VC-25`、`VC-29` | L2 + L4 |

> **「至少一种」的余量**：本设计提供**四种**互不相同的异常恢复路径（视觉降级 / 拒单回退 / 撤销与复核三出口 / SLA 超时与通知死信重试）。验收时至少演示一种即可，但**四种都必须有测试**。

---

## 9. 待用户裁决清单（**本文件相关**）

| # | 事项 | 现状 | 影响 |
|---:|---|---|---|
| **RQ-1** | 是否实现亮灯率 / 设施完好率 KPI 考核 | 行业标准有要求（§7.1），但需全量点位普查 + 新增统计表，**突破 17 表口径** | 若实现 → 需加第 18 张表并改 `SCHEMA-DESIGN.md`。**当前不实现** |
| **RQ-2** | `FR-12` 破损识别是否要承诺检出率 | 破损类无 A 档公开数据，必须自采；阶段一无训练 | 若用户要求具体指标，需先自采数据 + 训练 + 实测 |
| **RQ-3** | 是否要求 `P1`–`P4` 改用中文等级命名 | 中文规范用「特级/一级/二级/三级」或「特急/加急/普通」；`P1`–`P4` 是借国际实践 | 改的是 `business_rules.display_name` 数据，**不改代码**。见 `docs/BUSINESS-LOGIC.md` |
| **RQ-4** | 全部算法参数（SLA 时限、分级权重、派单权重、返修阈值、AI 候选 TTL）的标定值 | **均为工程初值，未标定** | 见 `docs/BUSINESS-LOGIC.md` §4 与 `.team/notes/backend-dev/stage1-backend-spec-input.md` §5.3 U-4 |

---

## 10. 本文档的诚实边界

**已确定的**：
- 9 个 UC、29 个 FR、3 个角色、17 张表的编号与口径（用户给定 + Lead 裁决 `UC-09`）。
- 29 条 `VC` 可验证判据（来自 `tester` 素材，逐条可执行）。
- 行业阈值的三源印证（`T/CMEA 21—2021` + 深圳令 309 号 + `DB11/T 1876-2021`，逐字取得）。
- 数据集合规结论（唯一 A 档数据集、破损类必须自采、D2–D7 不得称开源）。

**没有确定的**：
- **全部算法参数未标定**（`RQ-4`）。
- **亮灯率 KPI 是否实现**（`RQ-1`）。
- **破损识别的能力承诺**（`RQ-2`，无数据无训练）。
- **所有 `FR` 状态都是「未实现」**——阶段一只做设计，**零行可运行代码**。`vision/` 的 8 个 `.py` 是未验证草稿（§6.3）。
- 17 表完整 DDL **未在 PostgreSQL 上跑过**（阶段一不写迁移）。
- `FR-12`、`FR-22`–`FR-24` 的实际行为**没有任何测试验证过**——因为没有任何实现。

**明确不属于本文档的**：
- 表结构细节 → `docs/SCHEMA-DESIGN.md`
- 状态机与算法规则 → `docs/BUSINESS-LOGIC.md`
- API 端点到字段级 → `docs/API-CONTRACT.md`
- 技术栈与版本 → `docs/TECH-STACK.md`
- 团队角色与纪律 → `docs/TEAM-CHARTER.md`
- 部署与运维 → `docs/RUNBOOK.md`（`dev-ops` 独占）

---

## 11. 变更记录

| 日期 | 变更 | 触发 |
|---|---|---|
| 2026-11-21 | 初版。9 UC / 29 FR / 29 VC 追溯矩阵、角色与权限双层模型、4 类「不做」、智能边界、行业阈值三源印证、4 周验收映射 | `task-7` |
