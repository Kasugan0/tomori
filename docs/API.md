> 状态：阶段一定稿（待用户放行阶段二实现）

# 接口契约（API）

> **文档归属**：`docs-writer`｜**本文件写入域**：`docs/API.md`
> **曾用名**：`docs/API-CONTRACT.md`（`task-18` 更名，**设计内容零改动**；`audits/stage1-audit.md` 中的旧坐标按此文件名对应，行号不变）
> **读者**：`frontend-dev`（唯一实现依据）、`backend-dev`、`tester`（E2E 断言依据）、`auditor`
> **上游素材**：`.team/notes/backend-dev/stage1-backend-spec-api.md`（端点/错误码/幂等）、`.team/notes/frontend-dev/stage1-frontend-spec-input.md`（`data-testid` 冻结清单）、`.team/notes/vision-dev/`（视觉契约）
> **本文件的地位**：**端点、错误码、幂等语义、`data-testid` 锚点的唯一权威**。前端实现以本文件为准；本文与素材冲突时以本文为准并经 `Lead` 裁决。

---

## 0. 统计口径分层声明（`docs/team/TEAM.md` §5.3）

| 项 | 分子 | 分母 | 排除项 |
|---|---:|---|---|
| REST 端点数 | **57**（见 §2 逐条） | 本文件 §2 全部 57 条路由行（含 §2.4/§2.5 共 2 条多方法/多路径合并行，展开为 60 个 (method, path) 端点） | `/health` 与静态资源挂载：**实测 §2 表内 `/health` 出现 0 次**，故该排除项对本计数无影响（`audits/stage1-audit.md` P0-1） |
| `error.code` 数量 | **34**（见 §3 逐行） | 本文件 §3 表 | 无（每个 `code` 唯一映射一个状态码；原 33 系 `VISION_INFERENCE_FAILED` 只在脚注未入表，见 `audits/stage1-audit.md` P1-1） |
| 幂等必需端点 | **12**（见 §6.4） | 全部 `POST` 写端点 | 其余 `POST`/`GET`/`PATCH`/`DELETE` 忽略 `Idempotency-Key` |
| 冻结 `data-testid` | **128** = 121（§8.2/§8.3 冻结表去重）+ 7（原灰区，`D-04` 已裁决补入，见 §8.2/§8.3 J 组） | 本文件 §8 | `shell-*` 中纯展示无断言需求者见 §8.3 |

> **`error.code` 与 HTTP 状态码不是一对一**（多个 `code` 共享一个状态码，如 4 个 `415`/`422` 家族），**但每个 `code` 到状态码的映射必须唯一**——这是 `§3` 表存在的唯一理由。**同一 `code` 不允许出现在两个状态码下。**

---

## 1. 全局约定

### 1.1 基础

| 项 | 值 |
|---|---|
| Base path | `/api/v1` |
| 认证 | `Authorization: Bearer <JWT>`（`HS256`，载荷 `sub`/`tv`/`role`/`exp`） |
| 请求体编码 | `application/json`（上传端点用 `multipart/form-data`） |
| 响应体编码 | `application/json`（原图/标注图用二进制流） |
| 时间格式 | ISO 8601 带时区偏移，如 `2026-11-21T08:15:30.123456+08:00` |
| 字段命名 | `snake_case`（与 DB 列名一致，**不做 camelCase 转换**） |
| 分页 | `?page=1&page_size=20`（`page_size` 上限 100），响应含 `{items, total, page, page_size}` |
| 图片访问 | `GET /evidence/{id}/raw` 与 `/annotated`，**不提供公开静态目录**，`Cache-Control: private, max-age=3600` |

### 1.2 三层鉴权顺序（每个端点都必须按此次序）

```
1. 认证        无/错 token      → 401 TOKEN_MISSING / TOKEN_INVALID / TOKEN_EXPIRED
2. 账号状态    status != ACTIVE → 403 ACCOUNT_DISABLED
3. 角色        角色不允许        → 403 FORBIDDEN_ROLE
4. 区域授权    操作人无区域授权  → 403 FORBIDDEN_REGION_SCOPE
5. 业务校验    ↓ 见各端点
```

> ⚠️ **第 3 与第 4 层的 `403` 必须用不同 `code`**：`FORBIDDEN_ROLE` 是「你这个角色永远不能做这件事」，`FORBIDDEN_REGION_SCOPE` 是「你这个角色能做，但不在这个区域」。合成一个 `code` 会让前端无法给出正确文案。

### 1.3 `403` 与 `422` 的判定分工（**`tester` 明确要求写进文档的硬规则**）

| 情形 | 状态码 | `code` | 谁不合格 |
|---|---:|---|---|
| **操作人自己**无角色 | `403` | `FORBIDDEN_ROLE` | 请求者 |
| **操作人自己**无该区域授权 | `403` | `FORBIDDEN_REGION_SCOPE` | 请求者 |
| 操作人账号被停用 | `403` | `ACCOUNT_DISABLED` | 请求者 |
| **被指派的目标维修员**不合格 | `422` | `DISPATCH_TECHNICIAN_NOT_ELIGIBLE` | 请求里指定的对象 |
| 目标资源状态不合法 | `422` | `INVALID_STATE_TRANSITION` 等 | 目标资源 |

**判据一句话**：**`403` 说的是「你不能这么做」，`422` 说的是「你要求我做的这件事，参数本身不成立」。**

> **这条必须写死，否则阶段二极易被「统一成 403」改回去**——把「被指派人不合格」写成 `403` 会让管理员以为是自己没权限，而实际是自己选错了人，排查方向完全相反。

---

## 2. 端点清单（57 个）

> **计数口径**：`§2.1`–`§2.5` 五个表格的路由行之和 = `12 + 12 + 14 + 8 + 11 = 57`（含 §2.4 `GET`/`PATCH` `/repair-processes`、`/repair-processes/{id}`〔L136〕与 §2.5 `GET` `/rework-orders`、`/rework-orders/{id}`〔L145〕共 2 条多方法/多路径合并行，展开为 60 个 (method, path) 端点：L136 行对应 3 个端点（`GET /repair-processes`、`GET /repair-processes/{id}`、`PATCH /repair-processes/{id}`，无 `PATCH /repair-processes`），加 L145 行 2 个（`GET /rework-orders`、`GET /rework-orders/{id}`）与其余 55 行，共 `3 + 2 + 55 = 60` 个具体 `(method, path)` 端点）。改动记录见 `audits/stage1-audit.md` P0-1。

### 2.1 认证与账号（`UC-01`，12 个）

| 方法 | 路径 | 允许角色 | 说明 |
|---|---|---|---|
| `POST` | `/auth/login` | 匿名 | → `{access_token, token_type:"bearer", expires_in, account}`。失败 `401 INVALID_CREDENTIALS`；**不区分「用户不存在」与「密码错」**（防用户枚举） |
| `POST` | `/auth/logout` | 任意 | `accounts.token_version += 1`，该账号所有已发 token 立即失效 |
| `GET` | `/auth/me` | 任意 | 当前账号 + `role_code` + `scope_authorizations[]`（前端据此渲染菜单） |
| `POST` | `/auth/change-password` | 任意 | 成功后 `token_version += 1`（改密即吊销） |
| `GET` | `/accounts` | ADMIN | `?role_code=&region_id=&status=&keyword=` |
| `POST` | `/accounts` | ADMIN | 创建账号 + 单一角色（同事务写 `account_roles`） |
| `PATCH` | `/accounts/{id}` | ADMIN | **禁用时 `token_version += 1`**；**禁止改自己的 `status`**（防管理员自锁死） |
| `POST` | `/accounts/{id}/reset-password` | ADMIN | → `token_version += 1` |
| `GET` | `/roles` | ADMIN | 三角色字典（只读） |
| `GET` | `/scope-authorizations` | ADMIN | `?account_id=&region_id=&scope_type=&is_active=` |
| `POST` | `/scope-authorizations` | ADMIN | `{account_id, region_id, scope_type, valid_from?, valid_to?}` |
| `DELETE` | `/scope-authorizations/{id}` | ADMIN | **软停用**（`is_active=false`），**不物理删除**——历史派工引用它 |

### 2.2 区域/点位/资产/规则（`UC-02`/`UC-04`，12 个）

| 方法 | 路径 | 允许角色 | 说明 |
|---|---|---|---|
| `GET` | `/regions` | 全部（按授权过滤） | `?flat=true`。ADMIN 看全部，其他角色只看有授权的 |
| `POST` | `/regions` | ADMIN | `{region_code, name, parent_region_id?, level, description?}`；重复 `region_code` → `409` |
| `PATCH` | `/regions/{id}` | ADMIN | `is_active=false` 后不得再派单 |
| `GET` | `/lighting-points` | 全部（按授权过滤） | `?region_id=&road_name=&status=&keyword=` |
| `POST` | `/lighting-points` | ADMIN | `{point_code, region_id, name, road_name, road_level, longitude, latitude}` |
| `PATCH` | `/lighting-points/{id}` | ADMIN | **改 `region_id` 时若该点位有活跃单 → `422 POINT_HAS_ACTIVE_TICKET`** |
| `GET` | `/facility-assets` | 全部 | `?lighting_point_id=&asset_type=&importance=` |
| `POST` | `/facility-assets` | ADMIN | — |
| `PATCH` | `/facility-assets/{id}` | ADMIN | — |
| `GET` | `/business-rules` | ADMIN | `?rule_code=&is_active=`。**合法 `rule_code` 共 5 个**：`grading_rules` / `sla_policy` / `dispatch_scoring_weights` / `rework_policy` / `ai_candidate_ttl_hours`（权威清单与载荷校验见 §7.4 与 `docs/DESIGN.md` §3.3（表结构见 §4.4 ⑤）） |
| `POST` | `/business-rules` | ADMIN | **创建新版本**：Pydantic 结构化校验（非法 → `422 INVALID_RULE_PAYLOAD`）；同 `rule_code` 已有活跃版本 → 旧版自动 `is_active=false`、`version+1`、同事务 |
| `PATCH` | `/business-rules/{id}` | ADMIN | 只能改 `is_active`/`description`。**禁止原地改 `rule_value`**（会破坏历史快照可解释性）→ `422 RULE_VALUE_IMMUTABLE`；要改载荷必须 `POST` 新版本 |

### 2.3 故障单与证据（`UC-03`/`UC-06`，14 个）

| 方法 | 路径 | 允许角色 | 说明 |
|---|---|---|---|
| `POST` | `/tickets/ai-candidates` | INSPECTOR（区域 `INSPECT`） | `multipart/form-data`：`files`(1–10)、`lighting_point_id?`、`region_id?`、`scene_lighting_hint`（`auto`/`day`/`night`/`twilight`）。**处理顺序见 §5** |
| `POST` | `/tickets` | INSPECTOR / ADMIN | **纯人工报障，永不依赖视觉**。支持 `application/json` **与** `multipart/form-data`（带 `files` 0–10，用于视觉失败兜底）→ `201`，`PENDING_DISPATCH`、`confirmed_at=created_at`，同事务按 `grading_rules` 分级并写入 `repair_deadline_at`（+48h；`response_deadline_at` 默认 `null`） |
| `GET` | `/tickets` | 全部（按角色与区域过滤） | `?status=&priority=&region_id=&lighting_point_id=&reporter_account_id=&technician_account_id=&is_sla_overdue=&keyword=&created_from=&created_to=` |
| `GET` | `/tickets/{id}` | 全部（区域校验） | 票 + `evidences[]` + `dispatches[]` + `repair_processes[]` + `acceptances[]` + `rework_orders[]` + `timeline[]` |
| `PATCH` | `/tickets/{id}` | INSPECTOR（报障人，仅 `AI_CANDIDATE_PENDING`）/ ADMIN | 可改 `title`/`description`/`fault_type`/`facility_asset_id`。**改 `fault_type` 为 `FLICKERING`/`ELECTRIC_HAZARD` 必须带 `manual_note`** |
| `POST` | `/tickets/{id}/confirm` | INSPECTOR（区域 `INSPECT`） | `{fault_type?, priority_override?, manual_note?}` → `PENDING_DISPATCH` + 按 `grading_rules` 分级 + `repair_deadline_at`（确认起 +48h；`response_deadline_at` 默认 `null`） |
| `POST` | `/tickets/{id}/cancel` | INSPECTOR（本人报障）/ ADMIN | `{cancel_reason}`（必填）。**仅 `AI_CANDIDATE_PENDING`/`PENDING_DISPATCH` 可撤**；`REWORK_PENDING_DISPATCH` 走 `/review-resolve` |
| `POST` | `/tickets/{id}/regrade` | ADMIN | `{priority, reason}` → 更新 `priority` + 审计（**法定 48h `repair_deadline_at` 不因改级顺延**） |
| `GET` | `/tickets/{id}/evidences` | 全部（区域校验） | 含 `url` 与 `ai_result_json` |
| `POST` | `/tickets/{id}/evidences` | TECHNICIAN（受派人）/ INSPECTOR | `multipart/form-data`：`files`(1–10)、`evidence_type` |
| `GET` | `/evidence/{id}/raw` | 全部（区域校验） | 原图二进制流 |
| `GET` | `/evidence/{id}/annotated` | 全部（区域校验） | 标注图；无标注图 → `404 EVIDENCE_NOT_ANNOTATED` |
| `GET` | `/tickets/{id}/dispatch-candidates` | ADMIN（区域 `MANAGE`） | → `{candidates[], excluded[]}`，`excluded[].reason` ∈ 5 值枚举 |
| `POST` | `/tickets/{id}/dispatch` | ADMIN（区域 `MANAGE`） | `{technician_account_id, scope_authorization_id, dispatch_mode?, remark?}` → `201`；票 → `DISPATCHED` |

### 2.4 派工/维修/验收/返修（`UC-05`/`UC-06`/`UC-07`，8 个）

| 方法 | 路径 | 允许角色 | 说明 |
|---|---|---|---|
| `POST` | `/tickets/{id}/reassign` | ADMIN（区域 `MANAGE`） | `{technician_account_id, scope_authorization_id, reason}` → 旧派工 `CANCELLED`（或带 `escalate=true` 时 `ESCALATED`）+ 新派工 `PENDING_ACCEPT`；票保持 `DISPATCHED` |
| `GET` | `/dispatches` | 全部 | TECHNICIAN 只看本人；ADMIN 看区域。`?status=&ticket_id=&technician_account_id=` |
| `POST` | `/dispatches/{id}/accept` | TECHNICIAN（本人） | 票 → `IN_REPAIR` |
| `POST` | `/dispatches/{id}/reject` | TECHNICIAN（本人） | `{reject_reason}`（必填）→ 票回 `PENDING_DISPATCH`；`dispatch_reject_count += 1` |
| `POST` | `/dispatches/{id}/escalate` | TECHNICIAN（本人） | `{escalate_reason}`（必填）→ 票 → `MANUAL_REVIEW` |
| `POST` | `/dispatches/{id}/complete` | TECHNICIAN（本人） | 维修完工提交（含电气安全检查六件套，见 §7.3） |
| `GET`/`PATCH` | `/repair-processes`、`/repair-processes/{id}` | ADMIN / 本人 | **L136 行对应 3 个端点（`GET /repair-processes`、`GET /repair-processes/{id}`、`PATCH /repair-processes/{id}`，无 `PATCH /repair-processes`），加 L145 行 2 个（`GET /rework-orders`、`GET /rework-orders/{id}`）与其余 55 行，共 `3 + 2 + 55 = 60` 个具体 `(method, path)` 端点**；`PATCH` **仅在票为 `IN_REPAIR` 时可改**；`materials` 覆盖式（传全量列表，服务层 diff） |
| `GET` | `/material-usages` | ADMIN / 本人 | — |

### 2.5 验收/返修/复核/通知/审计（`UC-07`/`UC-08`/`UC-09`，11 个）

| 方法 | 路径 | 允许角色 | 说明 |
|---|---|---|---|
| `POST` | `/tickets/{id}/acceptance` | INSPECTOR（区域 `INSPECT`） | `{decision: "APPROVED"\|"REJECTED", comment?, evidence_ids?[]}`。**列名是 `decision`，不是 `result`** |
| `GET` | `/acceptances` | 全部（区域过滤） | `?ticket_id=&decision=` |
| `GET` | `/rework-orders`、`/rework-orders/{id}` | 全部（区域过滤） | `?status=&ticket_id=&requires_manual_review=` |
| `POST` | `/tickets/{id}/review-resolve` | ADMIN | `{resolution, reason}`，`resolution` ∈ `APPROVE_REASSIGN`/`SPECIAL_CLOSE`/`TERMINATE_CANCEL` |
| `GET` | `/notifications` | 本人 | `?is_read=&event_type=`；**仅返回 `recipient_account_id = 当前账号`** |
| `POST` | `/notifications/{id}/read` | 本人 | `is_read=true`, `read_at=now()` |
| `GET` | `/notifications/dead-letters` | ADMIN | `status=DEAD_LETTER` |
| `POST` | `/notifications/{id}/retry` | ADMIN | `DEAD_LETTER → PENDING`，`attempt_count=0` |
| `POST` | `/notifications/{id}/acknowledge` | ADMIN | `DEAD_LETTER → SENT`（人工放弃） |
| `GET` | `/audit-logs` | ADMIN | `?actor_account_id=&action=&resource_type=&resource_id=&ticket_id=&created_from=&created_to=&response_status=`。**只读，无写端点** |
| `POST` | `/sla/scan` | ADMIN（或内部定时） | 手动触发一次 SLA 扫描 → `{marked_overdue, timeout_reassigned, notifications_created}` |

> **`POST /sla/scan` 存在的唯一目的**：让 SLA 超时测试**不依赖等待真时钟**。E2E 必须调用它而不是 `sleep`。

---

## 3. 错误码 → HTTP 状态码唯一映射（34 个）

> **每个 `code` 只出现在一行**。E2E 断言 `data-code` 时以本表为唯一依据。
> **`details` 不属于契约**（见 §4）。

### 3.1 认证与权限（9 个）

| 状态码 | `error.code` | 触发 |
|---:|---|---|
| `401` | `INVALID_CREDENTIALS` | 用户名/密码错（**不区分二者**） |
| `401` | `TOKEN_EXPIRED` | JWT `exp` 已过 |
| `401` | `TOKEN_INVALID` | 签名/格式错，或 `tv` 与 `accounts.token_version` 不匹配（已吊销） |
| `401` | `TOKEN_MISSING` | 无 `Authorization` 头 |
| `403` | `FORBIDDEN_ROLE` | **操作人**角色不允许 |
| `403` | `FORBIDDEN_REGION_SCOPE` | **操作人**对目标区域无对应 `scope_type` 授权 |
| `403` | `ACCOUNT_DISABLED` | 账号 `status != ACTIVE` |
| `404` | `RESOURCE_NOT_FOUND` | 通用（`details.resource_type`/`resource_id`） |
| `404` | `EVIDENCE_NOT_ANNOTATED` | 该证据无标注图 |

### 3.2 状态与业务规则（12 个）

| 状态码 | `error.code` | 触发 |
|---:|---|---|
| `409` | `DUPLICATE_ACTIVE_TICKET` | 同点位同类型活跃单（`details.existing_ticket_id/no/status`） |
| `409` | `IDEMPOTENCY_REQUEST_IN_PROGRESS` | 同键原请求未完成 |
| `422` | `INVALID_STATE_TRANSITION` | 状态机白名单外（`details.from_status`/`to_status`/`allowed_from`） |
| `422` | `AI_CANDIDATE_NOT_CONFIRMED` | 未确认 AI 候选就派工 |
| `422` | `ELECTRICAL_SAFETY_CHECK_REQUIRED` | 未做电气安全检查就完工（`details.missing_items`） |
| `422` | `DISPATCH_TECHNICIAN_NOT_ELIGIBLE` | **被指派维修员**不合格（`details.reason` ∈ 5 值） |
| `422` | `TECHNICIAN_OVERLOADED` | 被指派维修员在途超限 |
| `422` | `POINT_HAS_ACTIVE_TICKET` | 改点位区域时存在活跃单 |
| `422` | `RULE_VALUE_IMMUTABLE` | 试图原地改 `business_rules.rule_value` |
| `422` | `INVALID_RULE_PAYLOAD` | 规则载荷语义校验失败（`grading_rules` 缺 `default_priority=='P4'` / 条件枚举非法、`sla_policy.statutory_deadline_hours <= 0`、`dispatch_scoring_weights` 权重和 ≠ 1 等） |
| `422` | `IDEMPOTENCY_PAYLOAD_MISMATCH` | 同键不同载荷 |
| `422` | `VALIDATION_ERROR` | Pydantic 校验失败（`details.errors`） |

### 3.3 媒体与容量（6 个）

| 状态码 | `error.code` | 触发 |
|---:|---|---|
| `400` | `IDEMPOTENCY_KEY_REQUIRED` | 必需端点缺幂等键 |
| `400` | `IDEMPOTENCY_KEY_INVALID` | 键格式不合法（`^[A-Za-z0-9_-]{8,128}$`） |
| `413` | `IMAGE_SIZE_EXCEEDED` | 单文件 > 15 MiB（**15,728,640 B**；`details.file_index`/`size_bytes`） |
| `415` | `UNSUPPORTED_IMAGE_FORMAT` | 魔数不符 / MIME 非 JPEG-PNG-WebP / **多帧动图（APNG `acTL`、WebP `n_frames>1`）** / 解码失败 |
| `415` | `MEDIA_VIDEO_STREAM_FORBIDDEN` | **仅**视频容器（MP4/MKV/AVI/FLV 魔数）、`video/*` MIME、`rtsp://`·`rtmp://`·`http(s)://*.m3u8`·`stream_url`·`camera_*` 实时流字段。**不含任何动图** |
| `422` | `IMAGE_PIXEL_LIMIT_EXCEEDED` | 像素 > **40,000,000**（`details.width`/`height`/`pixels`） |

### 3.4 服务端（7 个）

| 状态码 | `error.code` | 触发 |
|---:|---|---|
| `422` | `TOO_MANY_FILES` | 文件数 > 10 |
| `422` | `NO_FILES` | 文件数 = 0 |
| `500` | `INTERNAL_ERROR` | 未预期异常（**不泄漏堆栈**，只给 `request_id`） |
| `503` | `SERVICE_UNAVAILABLE` | DB 不可用 / 启动未完成 |
| `503` | `VISION_DISABLED` | `YOLO_INFERENCE_ENABLED=false` 或权重不可用 |
| `503` | `VISION_TIMEOUT` | 单次推理 > `VISION_TIMEOUT_SECONDS`（默认 60） |
| `503` | `VISION_INFERENCE_FAILED` | 推理抛异常（`details.analysis_stage` ∈ `DECODE`/`INFERENCE`/`POSTPROCESS`） |

> **本节 7 个码**。全表 `9 + 12 + 6 + 7 = 34` 个 `code`，与本文件 §0、§3 标题一致。
> 🔴 **`VISION_INFERENCE_FAILED` 原先只在脚注里**，导致「33 vs 34」口径歧义（`audits/stage1-audit.md` P1-1）。现正式入表——`tester` 的 `data-code` 断言必须**唯一**，不能在脚注里藏第 34 个码。

### 3.5 两个 `415` 码的分界（**Lead 二次裁决，必须带上理由**）

| `code` | 承载语义 | 触发含义 | 审计口径 |
|---|---|---|---|
| `UNSUPPORTED_IMAGE_FORMAT` | **载体格式问题** | 「这个文件格式我不收」——含多帧动图（**会动的图仍然是图**） | 普通 4xx，计入格式拒绝率 |
| `MEDIA_VIDEO_STREAM_FORBIDDEN` | **业务红线**（用户明确「不接实时监控或远程灯控」） | 「有人试图喂实时流」——需求级边界违规 | **独立统计与告警** |

**裁决理由（必须保留，这不是任意选择）**：
1. 运维看到「不支持视频流」但用户传的是会动的图 → **文案误导**。
2. 把动图混进 `MEDIA_VIDEO_STREAM_FORBIDDEN` 会让「疑似接实时监控」的告警数字被**噪声污染**，红线告警失真。

> `.gif` 本就在扩展名白名单外、更早被 `415 UNSUPPORTED_IMAGE_FORMAT` 拦下，正好印证「**动图归格式、流归红线**」这条分类线自洽。

### 3.6 **绝不存在的错误码**（出现即缺陷）

| 错误写法 | 正确写法 | 说明 |
|---|---|---|
| `IMAGE_TOO_LARGE` | `413 IMAGE_SIZE_EXCEEDED` | **该码不存在**，任何文档/代码里出现都是错的 |
| `DISPATCH_TECHNICIAN_OUT_OF_SCOPE` | `422 DISPATCH_TECHNICIAN_NOT_ELIGIBLE` | 已改名 |
| `422 UNSUPPORTED_IMAGE_FORMAT` | `415 UNSUPPORTED_IMAGE_FORMAT` | 状态码错 |
| `422 MEDIA_VIDEO_STREAM_FORBIDDEN` | `415 MEDIA_VIDEO_STREAM_FORBIDDEN` | 状态码错 |
| `409 INVALID_STATE_TRANSITION` | `422 INVALID_STATE_TRANSITION` | 状态机冲突是语义问题不是资源冲突 |

---

## 4. `details` **不属于契约**（严格纪律）

**契约只包含三个字段**：`error.code`（唯一断言依据）、HTTP 状态码、`message_key`（前端文案键）。

| 允许 | 禁止 |
|---|---|
| 前端 E2E **只断言 `data-code` + `data-http-status`** | 断言 `details.*` 的具体结构 |
| `details` 可以**演进**（加字段/改字段名） | 把 `details` 字段写进前端类型定义的必填项 |
| `details` 可含诊断信息 | **`details` 不得包含堆栈、SQL 语句、内部文件路径、凭证/密钥** |

**响应信封固定**：

```json
{
  "error": {
    "code": "DUPLICATE_ACTIVE_TICKET",
    "message_key": "error.duplicate_active_ticket",
    "request_id": "01HQZ...",
    "details": { }
  }
}
```

> **为什么 `details` 必须排除在契约外**：一旦 E2E 断言 `details.existing_ticket_id`，后端就永远不敢改 `details` 的字段名——而 `details` 是诊断信息，它的演进成本本应是零。**把诊断信息升格成契约，等于给自己加了一份没有收益的兼容负担。**

> **唯一例外（已显式声明）**：`409 DUPLICATE_ACTIVE_TICKET` 的 `details.existing_ticket_id/no/status` **前端会用来渲染「查看既有工单」链接**——这属于「前端消费了它」，因此**该三字段被冻结**，但仍**不得在 E2E 里断言其存在性**（应断言链接是否渲染）。

---

## 5. 媒体上传处理顺序（`POST /tickets/ai-candidates`，**唯一权威，实现期不得调整**）

```
1. 认证 + 角色 + 区域授权（INSPECT）
2. 文件个数 1..10                  → 422 TOO_MANY_FILES / 422 NO_FILES
3. 每文件大小 ≤ 15 MiB             → 413 IMAGE_SIZE_EXCEEDED（details.file_index）
4. 魔数校验（不信 Content-Type）   → 415 UNSUPPORTED_IMAGE_FORMAT
   JPEG FF D8 FF / PNG 89 50 4E 47 0D 0A 1A 0A / WEBP RIFF....WEBP
5. 载体形态判定（只读容器头，不解像素）
   5a 多帧动图：APNG acTL / WebP n_frames>1        → 415 UNSUPPORTED_IMAGE_FORMAT
   5b 视频容器：MP4/MKV/AVI/FLV 魔数，或 video/*   → 415 MEDIA_VIDEO_STREAM_FORBIDDEN
   5c 实时流字段：rtsp:// · rtmp:// · *.m3u8 · stream_url · camera_* → 415 MEDIA_VIDEO_STREAM_FORBIDDEN
6. 像素总量 ≤ 40,000,000           → 422 IMAGE_PIXEL_LIMIT_EXCEEDED
7. EXIF 方向归一化 + 解码（失败 → 415 UNSUPPORTED_IMAGE_FORMAT）
8. 写盘 uploads/{yyyy}/{mm}/{uuid4}.{ext} + sha256
9. 调视觉推理（每图独立 analysis_id）
10. 若无任何 is_fault_candidate=true → 不建票；仍把每图写成 fault_evidences
    （ticket_id = NULL），响应 200 + {ticket: null, evidences: [...]}
11. 否则建票（AI_CANDIDATE_PENDING）+ 逐图建 fault_evidences（ticket_id 非空，同事务）
12. 幂等键落 audit_logs
```

> **正常亮灯参照回传（`D-30` 定案）**：视觉检出的夜间正常亮灯 `streetlight_on` 按 `D-30` 定案以 `is_fault_candidate=false` 随 `ai_result_json` 回传前端供巡查员参照；若图中仅有 `streetlight_on` 而无任何 `is_fault_candidate=true`，同样走第 10 步 `200 + {ticket: null, evidences: [...]}` 留证不建票（详见 `docs/DESIGN.md` §5.1）。

### 5.1 **五步错误码的优先级**（Leads's mandated order）

| 顺序 | 判定 | 错误码 |
|---:|---|---|
| ① | > 15 MiB | `413 IMAGE_SIZE_EXCEEDED` |
| ② | 扩展名/MIME 不符 | `415 UNSUPPORTED_IMAGE_FORMAT` |
| ③ | 视频容器或流字段 | `415 MEDIA_VIDEO_STREAM_FORBIDDEN` |
| ④ | 解码后 `n_frames > 1` | `415 UNSUPPORTED_IMAGE_FORMAT` |
| ⑤ | 像素 > 40,000,000 | `422 IMAGE_PIXEL_LIMIT_EXCEEDED` |

> **为什么顺序不能换**：一个 20 MiB 的 `.mp4` 会同时命中 ①③。若先判 ③，用户看到「不支持视频流」但真实问题是文件太大——**报错必须指向最先能确定的那个事实**，否则运维按错误提示去压缩视频（无效）。

### 5.2 零候选为什么仍落库（`ticket_id = NULL` 的唯一合法用途）

第 10 步是刻意的：**白天拍到「灭灯」不是故障**（`DAYTIME_UNPOWERED_EXPECTED`），**夜间检出的正常亮灯 `streetlight_on`（按 `D-30` 定案置 `is_fault_candidate=false` 随 `ai_result_json` 回传前端供巡查员参照）同样不是故障**；若图中仅有 `streetlight_on` 而无任何 `is_fault_candidate=true`，同样走第 10 步 `200 + {ticket: null, evidences: [...]}` 留证不建票。若无故障候选却强行建票，`uq_fault_tickets_active_point_type` 会把这张「非故障票」**锁住点位 72 小时**。

**但必须写 `fault_evidences` 而非全丢弃**，理由三条：
1. 巡查员事后申辩「我明明拍到了」时，能拿出当时的图 + 当时的 AI 判定（`ai_result_json`）；
2. 「白天灭灯不误报」这条红线要能**在 DB 侧断点**（`tester` 用例断言「`fault_tickets` 0 行 + `fault_evidences` 有行 + 点位未被锁」）；
3. 若全丢弃，就没有任何记录能证明系统「看到了但正确抑制」，**红线变成不可验证的口头承诺**。

**代价**：`fault_evidences.ticket_id` 必须可空（见 `docs/DESIGN.md` §4.3 的 `ck_fault_evidences_ticket_or_orphan_inspection`）。

---

## 6. 幂等契约

### 6.1 权威出处（IETF）

| 断言 | 出处原文 |
|---|---|
| 键**不得**复用于不同载荷 | `draft-ietf-httpapi-idempotency-key-header-08` §2.2：*"The idempotency key MUST be unique and MUST NOT be reused with another request with a different request payload."* |
| 缺必需键 → `400` | §2.7 |
| 同键不同载荷 → `422` | §2.7 |
| 原请求处理中 → `409` | §2.7 |
| 重放可返回存储的原响应 | §2.6 Retry |

### 6.2 五条落地规则

| # | 场景 | 响应 |
|---:|---|---|
| 1 | 必需端点缺 `Idempotency-Key` | `400 IDEMPOTENCY_KEY_REQUIRED` |
| 2 | 键格式不合法（`^[A-Za-z0-9_-]{8,128}$`） | `400 IDEMPOTENCY_KEY_INVALID` |
| 3 | 同键 + 同载荷 + 原请求已完成 | **原状态码 + 原响应体原样重放**，附 `X-Idempotent-Replay: true` |
| 4 | 同键 + **不同载荷** | `422 IDEMPOTENCY_PAYLOAD_MISMATCH` |
| 5 | 同键 + 原请求**仍在处理中** | `409 IDEMPOTENCY_REQUEST_IN_PROGRESS` |

> **并发同键控制与严格 append-only 机制（P1-4 同步定案）**：**绝不向 `audit_logs` 插入占位行或做两阶段 `UPDATE`**。并发同键控制采用 **PostgreSQL 事务级非阻塞咨询锁 `pg_try_advisory_xact_lock(hashtextextended(actor_account_id::text || ':' || method || ':' || path || ':' || idempotency_key, 0))` + 锁内查询 `audit_logs` + `audit_logs` 严格 append-only**：请求进入事务后先尝试获取对应 `(actor_account_id, method, path, idempotency_key)` 的事务级非阻塞咨询锁，锁争用（返回 `false`）立即返回 `409 IDEMPOTENCY_REQUEST_IN_PROGRESS`；持锁后在锁内查询 `audit_logs`（命中 `2xx` 且同载荷则原样重放、不同载荷则 `422`）；仅业务执行成功返回 `2xx` 时向 `audit_logs` 写入 1 行幂等记录，`4xx`/`5xx` 不写幂等行且事务结束（提交或回滚）自动释放锁（详见 `docs/DESIGN.md` §3.5）。

### 6.3 两处**项目自定义选择**（草案留白，必须标注）

#### (a) `X-Idempotent-Replay: true` 是**项目扩展，非 IETF 草案内容**

IETF 127 HTTPAPI WG 材料明写：*"#46 replay indicator: not proposed. Its main use case is a generic intermediary, which is out of scope"*。

**本项目仍加它的理由**：前端需要区分「我这次真的新建了东西」和「我的重试被重放」，以决定是否需要刷新列表；E2E 也需要可观测凭据。

> ⚠️ **文档与代码注释必须标注「项目扩展，非 IETF 草案内容」**，**不得写成标准**。写成标准 = F14 违规（断言无真实出处）。

#### (b) 失败的请求（4xx/5xx）**不占用**幂等键

IETF 127 HTTPAPI WG 幻灯片（slides-127-httpapi-the-idempotency-key-http-header-field-00）把「错误是否算 completed」列为**开放问题**（*"Open: do errors count as 'completed'? Practice differs; #48 suggests a retry after a 4xx is processed as new"*；出处：`https://datatracker.ietf.org/meeting/127/materials/slides-127-httpapi-the-idempotency-key-http-header-field-00`）。本项目选择：**只有 `2xx` 写 `audit_logs` 幂等键索引**（`uq_audit_logs_idempotency` 带 `WHERE response_status BETWEEN 200 AND 299`）。

**后果（期望行为）**：用户遇到 `409 DUPLICATE_ACTIVE_TICKET` 后修正重提**不会被判 payload mismatch**。

### 6.4 必需幂等键的 **12** 个端点

```
POST /tickets/ai-candidates      POST /tickets/{id}/dispatch
POST /tickets                     POST /tickets/{id}/reassign
POST /tickets/{id}/confirm       POST /tickets/{id}/acceptance
POST /tickets/{id}/cancel        POST /tickets/{id}/review-resolve
POST /tickets/{id}/regrade       POST /dispatches/{id}/accept
POST /dispatches/{id}/reject     POST /dispatches/{id}/complete
```

> **共 12 个**。其余端点**忽略 `Idempotency-Key` 头**（不报错、不存储）——这样 GET 带上该头不会 `400`，降低前端心智负担。

### 6.5 并发语义的诚实边界

两个**真正并发**（都还没完成）的同键请求下，服务端采用 **PostgreSQL 事务级非阻塞咨询锁 `pg_try_advisory_xact_lock(hashtextextended(actor_account_id::text || ':' || method || ':' || path || ':' || idempotency_key, 0))` + 锁内查询 `audit_logs` + `audit_logs` 严格 append-only**：先持锁者进入锁内查库并执行业务（仅 `2xx` 写入 1 行 `audit_logs`，`4xx`/`5xx` 不写幂等行且事务结束自动释放锁），后到达者在锁争用下立即返回 `409 IDEMPOTENCY_REQUEST_IN_PROGRESS`（或待前者提交释放锁后再入锁查询 `audit_logs` 原样重放 `2xx`；详见 `docs/DESIGN.md` §3.5）。结果取决于谁先持锁提交，**确定性不变量只有两条**：
1. `fault_tickets` 精确 **`+1`** 行；
2. `audit_logs` 中该幂等键精确 **`1`** 行。

> 🔴 **E2E 绝对不得断言「两个并发请求都返回 2xx」**——那是竞态依赖的断言，会**间歇性假红**。正确断言只有上面两条不变量。

---

## 7. 关键契约细节（易错点，逐条给正确写法）

### 7.1 字段名纠正表（**阶段一已确认的错误写法**）

| ❌ 错误写法 | ✅ 正确写法 | 后果 |
|---|---|---|
| `pole_id` | **`lighting_point_id`** | 全库无 `pole_id` |
| `sla_deadline` | **`repair_deadline_at`**（确认/发现起 +48h，不因未接单或改级为 `null`）+ 可空 **`response_deadline_at`**（默认 `null`，郑州规章无分钟级响应时限） | 两个 deadline 语义不同 |
| `max_rework_count` | **`rework_policy.max_rework_rounds`** | 规则键名 |
| `acceptance_records.result = 'passed'` | **`acceptance_records.decision`** ∈ `APPROVED`/`REJECTED` | 列名与取值都不同 |
| `INVALID_STATE_TRANSITION` 作 `409` | **`422`** | 状态机冲突是语义问题 |
| `IMAGE_TOO_LARGE` | **`413 IMAGE_SIZE_EXCEEDED`** | **该码不存在** |

### 7.2 `DISPATCH_TECHNICIAN_NOT_ELIGIBLE` 的 5 种 `details.reason`

| `reason` | 前端文案 |
|---|---|
| `NO_REGION_SCOPE` | 该维修人员未获此运维区域的维修授权 |
| `SCOPE_EXPIRED` | 区域授权已过期 |
| `ACCOUNT_INACTIVE` | 该账号已停用 |
| `WRONG_ROLE` | 该账号不是维修人员 |
| `OVERLOADED` | 当前在途工单已满 |

> 同一枚举用于 `dispatch-candidates` 响应的 `excluded[].reason`，前端文案表**只写一份**。

### 7.3 电气安全检查「六件套」（`POST /dispatches/{id}/complete`）

必须同时满足：总开关 `electrical_safety_checked=true` **且** 5 项 checklist 全为 `true`：
`power_isolated`（已断电）/ `voltage_verified_zero`（验电为零）/ `insulation_intact`（绝缘完好）/ `grounding_connected`（接地可靠）/ `ppe_worn`（佩戴防护用品）。

**三层校验（缺一即失败）**：前端 `disabled` + 后端 `422 ELECTRICAL_SAFETY_CHECK_REQUIRED`（`details.missing_items` 列出**全部**缺失项，不是命中第一个就返回）+ DB `CHECK` 兜底。

> **`missing_items` 必须收集全部缺失项**：只报第一个会让用户改一次提一次，体验与可审计性都差。DB 层绕过服务层时必须抛 `CheckViolation` 且 `constraint_name == 'ck_repair_processes_safety_checklist_complete'`。

### 7.4 `/business-rules` 载荷契约示例（`grading_rules` 与 `sla_policy`）

`POST /business-rules` 创建新版本，请求体为 `{ "rule_code": ..., "rule_value": ..., "description": ... }`（非法载荷返回 `422 INVALID_RULE_PAYLOAD`；废除旧版 `grading_weights` / `priority_thresholds` 浮点魔数）：

```jsonc
// 1) rule_code = "grading_rules"（确定性表驱动分级，高到低首个命中，P4 必兜底）
{
  "rule_code": "grading_rules",
  "rule_value": {
    "rules": [
      {
        "rule_id": "R1_SAFETY_OR_EXPRESS_CRITICAL",
        "priority": "P1",
        "any": [
          { "fault_type": "ELECTRIC_HAZARD" },
          { "safety_flag": true },
          { "road_level": "EXPRESS", "asset_importance_in": ["CRITICAL", "HIGH"] }
        ]
      },
      {
        "rule_id": "R2_EXPRESS_OR_MAIN_CORE_OR_CRITICAL_ASSET",
        "priority": "P2",
        "any": [
          { "road_level": "EXPRESS" },
          { "road_level": "MAIN", "fault_type_in": ["FIXTURE_DAMAGED", "LAMP_OUT"] },
          { "asset_importance_in": ["CRITICAL", "HIGH"] }
        ]
      },
      {
        "rule_id": "R3_BRANCH_OR_MAIN_MINOR",
        "priority": "P3",
        "any": [
          { "road_level": "BRANCH", "fault_type_in": ["LAMP_OUT", "OCCLUDED", "FLICKERING", "FIXTURE_DAMAGED"] },
          { "road_level": "MAIN", "fault_type_in": ["OCCLUDED", "FLICKERING", "OTHER"] }
        ]
      }
    ],
    "default_priority": "P4",
    "default_rule_id": "R4_FALLBACK_OBSERVATION"
  }
}

// 2) rule_code = "sla_policy"（郑州市令第 152 号第 19 条第 3 款：48h 统一基线，不编造分钟数）
{
  "rule_code": "sla_policy",
  "rule_value": {
    "statutory_deadline_hours": 48,
    "response_minutes": null,
    "emergency_notice_hours": 24,
    "applies_to_priorities": ["P1", "P2", "P3", "P4"]
  }
}
```

---

## 8. `data-testid` 冻结锚点

### 8.1 冻结纪律

| 规则 | 内容 |
|---|---|
| 变更成本 | `data-testid` 是**跨仓契约**（前端 + `tester`）。**任何改名/删除必须先经 `Lead` 协调并同步本文件**，否则 E2E 会集体失效 |
| 唯一断言凭据 | **错误码断言用 `data-code`，不用 `data-http-status`**（HTTP 状态码重复承载多个 `code`：`400`×2、`409`×2、`415`×2） |
| 按钮两阶段断言 | 门禁按钮**保持 DOM 可见但 `disabled`**（不 `v-if` 移除），以便 `toBeDisabled()` → `toBeEnabled()` |
| 常驻横幅 | `403`/`409`/`422` 必须走**置顶常驻 `el-alert`**（手动关闭或切路由才消失），**不得用 3s 自动关闭的 Toast**——后者会造成 E2E 竞态漏检 |

### 8.2 `data-testid` 统计口径（三层）

| 层 | 值 |
|---|---:|
| 源素材全文 `data-testid=` 真实属性写法 | 5 处 |
| §8.2 / §8.3 冻结表去重后的名字 | 121 |
| 路由表额外声明、**已由团队裁决补入冻结清单** | +7 |
| **冻结总数（唯一口径）** | **128** |

**7 个原灰区锚点已裁决补入**：`dashboard-summary-card-${key}`、`region-row-*`、`point-row-*`、`asset-row-*`、`rule-row-*`、`scope-row-*`、`account-row-*`。

> ✅ **裁决记录（`task-26`，2026-11-24）**：用户对 `docs/DECISION.md` `D-04` 的答复是「这里和我没有关系，是你们团队内部的事情」——**授权团队自决**。团队裁定**采纳 (a)：补入冻结清单，`121 → 128`**。
>
> **理由**：这 7 个是**列表行 / 汇总卡片**锚点，恰恰是 E2E 最需要断言的元素（「列表渲染了 N 行」「卡片显示了汇总值」）。排除它们等于让最关键的可视化断言失去锚点。
>
> **后果**：`tester` 现在可依本节完整清单写剧本，**不再有「未冻结锚点」**。原「已列入 §10 待裁决」的悬空状态解除。

### 8.3 分组清单（**128** 个已冻结，按职责）

> **`128 = 121 + 7` 的来源**：前九组之和 `7+17+5+26+12+8+24+19+3 = 121`（`audits/stage1-audit.md` P0-4 两路独立计数逐组吻合、全局零重复）；**第 J 组为 7 个原灰区锚点，已由团队裁决补入**（`D-04`，见 §8.2）。二者相加即 `§0` 声明的 **128**。**现行唯一冻结口径是 128，不得再引用 121 作为总数。**

| 组 | 数量 | 代表锚点 |
|---|---:|---|
| A 登录与会话 | 7 | `auth-username-input`、`auth-login-submit`、`auth-login-error`、`auth-logout-btn`、`shell-user-role` |
| B 三角色导航 | 17 | `nav-desktop-*`（12）、`nav-mobile-*`（5） |
| C 全局异常横幅 | 5 | **`error-banner`（挂 `data-code`/`data-http-status`）**、`error-banner-code`、`conflict-existing-workorder-link` |
| D 巡查上传与 AI 候选 | 26 | `inspection-photo-input`、`inspection-ai-disclaimer`、`inspection-candidate-confirm-${idx}` |
| E 工单列表与详情 | 12 | `workorder-row-${workorderNo}`、`workorder-detail-priority`（**必须渲染 `P1`..`P4` 原文**） |
| F 区域派单 | 8 | `dispatch-assignee-select`、`dispatch-assignee-option-${username}`（挂 `data-eligible`/`data-reason`） |
| G 维修 | 24 | `repair-safety-checkbox`、`repair-safety-item-*`（5 项） |
| H 验收/返修/超时/审计 | 19 | `accept-*`、`escalation-*`、`audit-log-table`、`notification-retry-btn` |
| I 兜底页 | 3 | `forbidden-page`、`not-found-page` |
| **J 列表行与汇总卡片（原灰区，`D-04` 补入）** | **7** | `dashboard-summary-card-${key}`、`region-row-*`、`point-row-*`、`asset-row-*`、`rule-row-*`、`scope-row-*`、`account-row-*` |
| **合计** | **128** | `7+17+5+26+12+8+24+19+3+7 = 128` |

### 8.4 ⚠️ Element Plus 的 `data-testid` 透传行为（**三类，必须逐组件判定**）

> **背景**：`frontend-dev` §10.3 R7 与 §12 出处 #28 断言「`data-testid` 绑在 `el-input` 上会落到根 `<div>`，必须全面改用原生控件」。**该断言的技术依据与结论均不成立**，本节给出源码 + 运行时双向证据。

**证据层级**：
- 🟢 **源码级**（tag `2.14.8`，与 `dev` 分支 `diff` 完全一致）
- 🟢 **运行时级**（jsdom 挂载 `vue@3.5.46` + `element-plus@2.14.8`，逐组件挂载测量落点，20 个组件）
- 🟡 **未实测**：真实 Chromium 下的 `fill()`/`check()`/传送门定位

#### 8.4.1 `el-input`：`data-testid` **能**到达内层 `<input>`（推翻 R7）

```bash
curl -sS "https://raw.githubusercontent.com/element-plus/element-plus/2.14.8/packages/components/input/src/input.vue"
curl -sS "https://raw.githubusercontent.com/element-plus/element-plus/2.14.8/packages/hooks/use-attrs/index.ts"
```

| 证据 | 位置 | 内容 |
|---|---|---|
| 根节点**不**绑 `$attrs` | `input.vue` L2–L14 | 根 `<div>` 只绑 `:class`/`:style`/`@mouseenter`/`@mouseleave` |
| `inheritAttrs: false` | `input.vue` L240 | 关闭自动透传到根 |
| `useAttrs()` **空调用** | `input.vue` L270 | `const attrs = useAttrs()` —— **未传任何参数** |
| **内层 `<input>` 绑 `v-bind="attrs"`** | `input.vue` L36（textarea 分支 L134） | ← **决定性证据** |
| `useAttrs` 默认排除项 | `use-attrs/index.ts` L12 | `const DEFAULT_EXCLUDE_KEYS = ['class', 'style']` |

**运行时落点**：`el-input` → `<input class="el-input__inner">`；`el-input type="textarea"` → `<textarea class="el-textarea__inner">`；`el-autocomplete` → `<input>`。

**结论**：`getByTestId('x').fill()` **可直接使用**。

> 🔴 **R7 的反向后果才是真陷阱**：对 `el-input` 多写一层 `.locator('input')` 会变成「在 input 里找 input」→ **必然 not found**。`tester` 素材 §6.2 担心的「静默失配」方向**恰好说反了**。
> **R7 的错误技术根因**：它引用 `useAttrs({ excludeKeys: containerAttrs })`——**该写法在源码中不存在**，`input.vue` 全文搜不到 `containerAttrs`。

#### 8.4.2 三类透传行为（**契约核心**）

| 类 | 行为 | 组件 | 锚点写法 |
|---|---|---|---|
| **A** | 落到**内层原生控件** | `el-input`、`el-autocomplete`、`el-button` | **直接** `getByTestId('x').fill()`，**不要**加 `.locator('input')` |
| **B** | 落到**根包装元素**（未设 `inheritAttrs`） | `el-checkbox`→`label`、`el-radio`→`label`、`el-select`/`el-table`/`el-pagination`/`el-dialog`/`el-form-item`/`el-switch`/`el-input-number`/`el-tabs`/`el-dropdown`/`el-color-picker`→`div` | 需 `.locator('input')` 等下钻 |
| **C** | **静默丢弃（0 命中）** | `el-tooltip`、`el-popover`、`el-date-picker`、`el-time-picker`、`el-cascader` | 🔴 **必须外层包 `<div data-testid>`** |

**C 类根因（源码链路，逐级定位）**：

```
date-picker.tsx L57-79  render 返回 <CommonPicker {...props}>，未转发 attrs
  → picker.vue L8       <el-tooltip ... v-bind="$attrs">        （attrs 交给 tooltip）
  → tooltip.vue L89-91  defineOptions({name:'ElTooltip'})，根是 <el-popper>
  → popper.vue          全文 53 行，只有 <slot /> + inheritAttrs: false
                        rg "attrs" popper.vue → 无匹配  ⇒  attrs 在此蒸发
```

**实测**：`<el-date-picker data-testid="x" />` 渲染后，扫描全文档任何含 `testid` 的属性 → `nodesWithTestidAttr: []`，**属性彻底消失**，不是选择器写错。

> 🔴 **C 类是最危险的失败模式**：Vue 不报错、TypeScript 不报错、`vue-tsc` 也不报错（它就是合法 attr），**只在 E2E 运行时炸**。比 strict-mode violation 更早、更隐蔽。

#### 8.4.3 对 `frontend-dev` R7 的最终裁定

| 项 | 裁定 |
|---|---|
| **结论**（`el-input` 落根 div） | ❌ **推翻**。落在内层 `<input>` |
| **技术依据**（`useAttrs({excludeKeys: containerAttrs})`） | ❌ **源码中不存在** |
| **工程决定**（全用原生控件） | 🟡 **不再必要**。`el-input` 可直接承载 testid。是否仍改原生控件属**前端偏好**，不是技术约束 |
| **真正需要保护的点** | ✅ **C 类组件必须外层包 wrapper**——这条 R7 完全没提，却是唯一会静默失效的 |

> **本契约不要求前端改回 `el-input`**——原生控件同样能工作。但**不得**再以「`el-input` 的 testid 会丢」作为理由写入文档或注释，那是错误的公开记录。

#### 8.4.4 冻结清单的锚点层级标注要求

因为三类行为不一致，**`data-testid` 冻结清单必须为每个锚点标注实际命中的 DOM 层级**。禁止出现「Element Plus 都一样」的推断。

**升级回归脚本**：`.team/notes/research/logs/ep-runtime-testid-probe-script.log` 可复现本节落点表，建议固化为 Element Plus 升级后的契约回归测试。

### 8.5 承载 `data-testid` 的组件处置表

| 组件 | 可承载 `data-testid` | 实际落点 | 处置 |
|---|---|---|---|
| 原生 `<input>`/`<textarea>`/`<button>`/`<a>`/`<div>` | ✅ | 元素本身 | 直接用 |
| `el-alert` | ✅ | **根元素**（`error-banner` 挂 `data-code`/`data-http-status`） | 直接用 |
| `el-input` / `el-autocomplete` / `el-button` | ✅ **A 类** | 内层原生控件 | 直接用 `page.get_by_test_id("x").fill(...)`（JS `getByTestId`），**不加 `.locator('input')`** |
| `el-select` | ✅ **B 类** | 组件根 `div` | 下钻 `.locator('input')`；下拉浮层经 Vue 原生 `<teleport>` 挂 `body` 时 `page.get_by_test_id(...)` 可整页穿透，仅父容器链式 `locator` 需 `:teleported="false"` |
| `el-option` | 🟡 **源码已核单根 `<li>`，待 Chromium 首验** | `<li role="option">`（`2.14.8` `option.vue` 未设 `inheritAttrs: false`） | 阶段二首验真实浏览器下 `data-testid`/`data-eligible`/`data-reason` |
| `el-checkbox` / `el-radio` | ✅ **B 类** | 根 `label` | 下钻；`frontend-dev` 仍选择原生 checkbox（**理由应改为「B 类需下钻」，非「属性丢失」**） |
| `el-tooltip` / `el-popover` / `el-date-picker` / `el-time-picker` / `el-cascader` | ❌ **C 类，禁止** | **丢弃** | **外层包 `<div data-testid>`** |
| `el-upload` | 🟡 **未验证** | 未知 | `frontend-dev` R2 的「内部 input 无法从外层命中」**仍属未核实断言** |

> `el-option` 的 **`element-plus@2.14.8` `option.vue` 源码已核实为单根 `<li role="option">`（未设 `inheritAttrs: false`），但 `disabled`/`data-eligible`/`data-reason`/`data-testid` 在真实 Chromium 下的交互落点仍待阶段二首个前端任务实测首验**；否则 `dispatch-assignee-option-*` 的断言仍无法最终闭环。


## 9. E2E 断言纪律（`tester` 可直接照抄）

### 9.1 必须用 `POST /sla/scan` 而非等待真时钟

SLA 超时测试**不得** `sleep` 到 deadline 之后。调用 `POST /sla/scan`（ADMIN）确定性触发一次扫描。

### 9.2 两条语义不同的超时路径，**断言不得写「或」**

| 路径 | 旧派工终态 | 触发者 |
|---|---|---|
| **自动回收** | `TIMEOUT_REASSIGNED` | 系统扫描 |
| **管理员升级改派** | `ESCALATED` | ADMIN 主动操作 |

> 用「`status IN ('TIMEOUT_REASSIGNED','ESCALATED')`」断言会把两条不同业务含义的路径合并，**掩盖其中一条没实现**。

### 9.3 并发断言只断言不变量

见 §6.5：只能断言「`fault_tickets` +1 行」与「`audit_logs` 该键 1 行」，**不得断言两个请求都 2xx**。

### 9.4 副作用三层断言（不得只断言 HTTP 状态码）

每个写操作至少断言：① HTTP 状态码 + `data-code`；② 目标表行数/字段变化；③ `audit_logs` 精确 `+1` 行、`notification_tasks` 按需 `+1` 条。

### 9.5 403 零脏写

`403` 用例必须同时断言「响应是 403」**和**「四张相关表行数均未变」——否则测不出「先写后拒」的脏写。

---

## 10. 待用户裁决清单 → 见 `docs/DECISION.md`

本文件相关的 5 项已并入 **`docs/DECISION.md`**（全项目唯一权威清单）：

| 原编号 | 事项 | `DECISION.md` | 状态 |
|---|---|---|---|
| `AC-1` | 7 个路由表 `data-testid` 是否补入冻结清单 | **`D-04`** | ✅ **已裁定：补入，`121 → 128`**（用户授权团队自决） |
| `AC-2` | `el-option` 属性透传是否成立 | **`D-20`** | 🟢 工程定案（阶段二首验） |
| `AC-3` | 单次上传张数：后端 `1..10` vs 前端按单图 | **`D-03`** | ✅ **已协调统一：全链路 `1..10`**（`frontend-dev` `task-30` 同步） |
| `AC-4` | `422 VALIDATION_ERROR` 是否进冻结码表 | **`D-19`** | 🟢 工程定案（已列入，错误码 **34**） |
| `AC-5` | `413` 阈值 15 MiB | **`D-02`** | ✅ **已裁定：15 MiB** |

> **`AC-5` 已裁定 → 15 MiB**：前端「取更严者」算法引用的 `.env.example` 值已从 10 改为 15（`dev-ops` `task-9`），故 `min(15, 15) = 15`。前端文案与 `before-upload` 阈值须用 **15 MiB / `15,728,640` B**。**此项已是用户裁决结论**（`docs/DECISION.md` `D-02`：「确定 15MiB」），仅剩前端实现待落地。
>
> **`AC-1` 已裁定 → 128**：用户答复「是你们团队内部的事情」= 授权团队自决，团队采纳 (a)。详见 §8.2 裁决记录。**`§8.3` 的九组 121 是历史分组口径，现行冻结总数以 128 为准。**
>
> **`AC-3` 已协调统一 → `1..10`**：`API.md` 与 `vision/` 本就支持 `1..10`，Lead 已派 `frontend-dev`（`task-30`）同步前端设计为 `1..10`，不再上推用户。

---

## 11. 本文档的诚实边界

**已确定的**：
- **57** 条路由行（含 2 条合并行，展开为 60 个 `(method, path)` 端点，即 `3 + 2 + 55 = 60`）、**34** 个 `error.code` 的唯一映射（端点行数见 §2 各小节；错误码见 §3 表 34 行，两者均已按 `audits/stage1-audit.md` P0-1/P1-1 复算）。
- 幂等五条规则 + 12 个必需端点 + 两处项目自定义选择（IETF 草案逐字引用）。
- 媒体处理五步顺序与优先级（Lead 裁决）。
- `el-input`/`el-checkbox`/`el-select` 等 20 个组件的 `data-testid` 实际落点（**§8.4.1 列出 `el-input` 相关的 2 条 `curl` + 源码逐行独立复核，§8.4.2/§8.5 附 20 个组件 jsdom 运行时落点实测与 `el-option` 源码单根 `<li role="option">` 核实**）。
- `/business-rules` 的 `grading_rules` 与郑州 48h `sla_policy` 载荷契约（§7.4）。

**没有确定的**：
- **没有任何端点被实现过**。本文档全部是设计契约，**零行可运行代码**。
- 所有端点的实际响应体**从未被真实调用验证**。
- `el-input`/`el-checkbox`/`el-select`（等 20 个组件）已完成源码与 jsdom 落点核实，`el-option` 已完成源码级单根 `<li role="option">` 核实；真实 Chromium 下的 `fill()`/`check()`/`<teleport>` 交互以及 `el-upload` 透传将在阶段二首验。

**明确不属于本文档的**：
- 系统架构与技术栈、4 套状态机、核心业务机制、17 张表 Schema 与约束、视觉管线与质量门禁 → `docs/DESIGN.md`
- 需求编号与验收判据 → `docs/REQUIREMENTS.md`
- 全项目裁决事项的唯一权威清单 → `docs/DECISION.md`
- 部署与端口 → `docs/RUNBOOK.md`

---

## 12. 变更记录

| 日期 | 变更 | 触发 |
|---|---|---|
| 2026-11-21 | 初版。45 端点 / 33 错误码 / 幂等 12 端点 / 媒体五步 / 128 冻结 testid；**独立复核推翻 `el-input` 落根 div 的断言** | `task-7` |
| 2026-11-21 | **审计修正**：端点 45 → **57**（P0-1）、`§2.5` 标题 9 → **11**（P0-1）、`§8.3` 标题 128 → **121**（P0-4）、错误码 33 → **34**（P1-1，`VISION_INFERENCE_FAILED` 入表）、`AC-5` 统一 **15 MiB**（P1-3）。**设计内容零改动** | `audits/stage1-audit.md` |
| 2026-11-21 | **改名**：`docs/API-CONTRACT.md` → `docs/API.md`（`task-18`）。标题 `# 接口契约（API-CONTRACT）` → `# 接口契约（API）`，自指写入域与全仓交叉引用同步更新。**设计内容零改动**：57 端点 / 34 `error.code` / 12 幂等 / 128 = 121 + 7 全部不变 | 用户要求「`API-CONTRACT.md` 改为 `API.md`」 |
| 2026-11-24 | **用户裁决同步（`task-26` + `task-29`）**：① `D-04` 补入 7 个灰区锚点，§8.2/§8.3 冻结口径统一为 **128**（九组 121 + J 组 7）；② `D-03` 协调统一全链路 `1..10`；③ 新增 §7.4 `/business-rules` 确定性规则 `grading_rules` 与郑州 48h `sla_policy` 载荷契约；④ 同步 `POST /tickets/{id}/confirm`、`/regrade` 与 §7.1 `sla_deadline` 口径（`repair_deadline_at` 自确认起 +48h，不因改级顺延；`response_deadline_at` 默认 `null`）。**57 端点 / 34 `error.code` / 12 幂等不变** | `task-26` + `task-29` |
| **2026-11-24** | **文档合并同步与 `D-30` 定案落盘（`task-44`）**：① 顶部状态更新为「阶段一定稿（待用户放行阶段二实现）」；② §5 / §5.2 同步 `D-30` 定案（`streetlight_on` 以 `is_fault_candidate=false` 随 `ai_result_json` 回传前端作参照；若仅检出 `streetlight_on` 而无任何 `is_fault_candidate=true`，仍走第 10 步 `200 + {ticket: null, evidences: [...]}` 留证不建票）；③ §2.2 / §5.2 / §11 原指向 `SCHEMA.md` / `BUSINESS-LOGIC.md` / `TECH-STACK.md` 的引用统一更新至 `docs/DESIGN.md` 对应章节。**57 端点 / 34 `error.code` / 12 幂等 / 128 `data-testid` 不变** | `task-44` |
| 2026-11-24（task-52） | **终审收口修正（`task-52`）**：① §6.2/§6.5 同步 P1-4，明确采用 PostgreSQL 事务级非阻塞咨询锁 `pg_try_advisory_xact_lock(hashtextextended(actor_account_id::text \|\| ':' \|\| method \|\| ':' \|\| path \|\| ':' \|\| idempotency_key, 0))` + 锁内查询 `audit_logs` + `audit_logs` 严格 append-only（仅 `2xx` 写入 1 行，锁争用立即返回 `409 IDEMPOTENCY_REQUEST_IN_PROGRESS`，`4xx`/`5xx` 不写幂等行且事务结束自动释放锁，详见 `docs/DESIGN.md` §3.5）；② §8.5 将 `el-select` 浮层原误写标签更正为 Vue 原生 `<teleport>`（P2-4），并同步 `:606`（原 `:604`）`el-option` 源码已核单根 `<li>`、待 Chromium 首验口径以消除与 `:601` 矛盾（P2-5）；③ 修正 §0/§2.2/§8.2 章节指针（`docs/team/TEAM.md` §5.3、§6.4、§8.2/§8.3、`docs/DESIGN.md` §3.3 与 §4.4 ⑤，P2-6/P2-7）；④ §6.3 (b) 开放问题归因修正为 IETF 127 HTTPAPI WG 幻灯片 `slides-127-httpapi-the-idempotency-key-http-header-field-00`（P2-10）；⑤ §0 与 §2 补注 57 条路由行展开为 60 个 `(method, path)` 端点口径（P2-12）。**57 路由行（60 端点）/ 34 `error.code` / 12 幂等 / 128 `data-testid` 不变** | `task-52` |
| 2026-11-24（task-58） | **P2-12 展开口径订正与咨询锁全称统一（`task-58`）**：① §0/§2/§11 及 L136/L689 将合并路由行展开口径精确订正为 **60 个 `(method, path)` 端点**，并在 §2（L71）与 L136 明示 L136 行对应 3 个端点（`GET /repair-processes`、`GET /repair-processes/{id}`、`PATCH /repair-processes/{id}`，无 `PATCH /repair-processes`）、L145 行 2 个（`GET /rework-orders`、`GET /rework-orders/{id}`）与其余 55 行共 `3 + 2 + 55 = 60` 个具体 `(method, path)` 端点；② §6.2/§6.5/§12 将咨询锁略写统一替换为与 `docs/DESIGN.md:742` 逐字一致的显式全称 `pg_try_advisory_xact_lock(hashtextextended(actor_account_id::text \|\| ':' \|\| method \|\| ':' \|\| path \|\| ':' \|\| idempotency_key, 0))`（表内竖线按 GFM 表格规范转义为 `\|\|`）。**57 路由行 / 34 `error.code` / 12 幂等 / 128 `data-testid` 不变** | `task-58` |
