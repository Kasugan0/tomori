# 阶段一：原始 18 列需求 ↔ 现行设计 一致性独立审计（founding-alignment-audit）

> **审计员**：`auditor`（独立只读）｜**任务**：`task-25`（P0）｜**范围**：当前设计套件 vs 用户最原始 18 列需求文本
> **权威基线**：用户本轮亲手提供的 18 列逐字原文（Lead 逐字转录，作为唯一权威来源）
> **工作树状态**：`HEAD = a16335c`；`docs/DECISION.md` 含用户未提交裁决（`git diff +51/−1`）；`vision/` 为未跟踪草稿（8 文件）。
> **原则**：坐标四级判据；每条结论带 `file:line`；「未证实」与「已证实」同等声明；零命中必须给出扫描范围与排除项。

---

## 0. 统计口径分层声明（TEAM §4.6）

| 断言 | 分子 | 分母 | 排除项 | 扫描范围（含排除项） |
|---|---|---|---|---|
| 18 列逐项比对 | 18 列 | 18 列 | 无 | 逐字原文（用户提供） |
| SLA 外推值命中 | 见 §B-4 行表 | 全仓 `docs/` + `.team/notes/`（非 logs） | `.team/notes/auditor/logs/`（历史快照，不计现状） | `rg '15 分钟\|2 小时\|30 分钟\|8 小时\|72 小时\|24 小时'` |
| `yolo26n/m/l/x`/Track A/B/C 命中 | 见 §B-2 行表 | `docs/` + `.env.example` + `README.md` | `.team/notes/`（素材非定稿） | `rg 'Track [ABC]\|yolo26[nmlx]'` |
| 10 MiB 残留命中 | 见 §B-1 行表 | `docs/` + `.env.example` + `README` + `vision/` + `.team/notes/`（非 logs） | `auditor/logs/`、`git-keeper/` | `rg '10 MiB\|10MiB\|10485760'` |
| 第 4 角色命中 | 0 | 全仓 docs | — | `rg 'OPERATOR\|VIEWER\|SUPERVISOR\|MANAGER\|GUEST\|审计员角色'` |

> **零命中声明纪律**：下文凡「未在 X 找到」均写明扫描范围；本次 `backend/`、`web/`、`tests/`、`migrations/` 目录**不存在**（阶段一无实现），故「代码无违背」仅在「文档/骨架/草稿」层面成立。

---

## A. 18 列原始需求矩阵逐项比对

**结论总表（18/18）**：**17 列完全一致 / 0 列直接违背 / 1 列（表数与数据表）存在「已授权实现细节」需单独解释**（非偏离）。无任何一列存在「悄悄偏离」。

| # | 列名 | 原始文本（逐字） | 现行设计对应 | 结论 |
|---:|---|---|---|---|
| 1 | 序号 | `141` | `RUNBOOK.md:3`、`stack-guard.sh:4`（`#141`） | ✅ |
| 2 | 领域分类 | `环境能源与设施运维` | `RUNBOOK.md:3`（「公共照明」+「设施运维」） | ✅ |
| 3 | 项目名称 | `公共照明故障工单系统` | `RUNBOOK.md:3`、全部 docs 标题 | ✅ |
| 4 | 项目目标 | `面向…闭环…异常处置` | `REQUIREMENTS.md:35`、闭环见 §5 下 | ✅ |
| 5 | 主要角色 | `运维管理员、巡查员、维修人员` | `ADMIN/INSPECTOR/TECHNICIAN`，`REQUIREMENTS.md:35-37`、`SCHEMA.md:176` | ✅ 无第4角色 |
| 6 | 业务范围 | `…派工、维修、验收和返修；…撤销、驳回、超时或复核路径` | `REQUIREMENTS.md` UC-02..UC-08；`BUSINESS-LOGIC.md` 17 边覆盖撤销(A5)/驳回(A12)/超时(§8)/复核(A15-17) | ✅ |
| 7 | 核心业务闭环 | `报障→分级派工→维修→验收→关闭或返修` | `BUSINESS-LOGIC.md` A1-A17 边 + `REQUIREMENTS.md:307` | ✅ |
| 8 | 非CRUD机制 | `区域派单、故障分级、返修闭环` | `REQUIREMENTS.md` UC-04/05/07；`BUSINESS-LOGIC.md` §5/§7 | ✅ |
| 9 | 建议核心数据表 | 17 个中文表名 | `SCHEMA.md:111-127` 圈号①–⑰ 一一映射 | ✅ 见 §A-9a |
| 10 | 建议表数 | `17` | `SCHEMA.md:111-127`、`REQUIREMENTS.md:22` | ✅ 见 §A-10a |
| 11 | 明确不做 | `不接灯控设备，不做能耗预测` | `REQUIREMENTS.md:214-215` N1/N2；骨架零 IoT/时序依赖 | ✅ |
| 12 | 4周验收重点 | `端到端演示；…至少一种异常恢复场景` | `REQUIREMENTS.md:307`（逐字引用）+ `:309-319` 映射 | ✅ |
| 13 | 建议用例数 | `9` | `UC-01..UC-09`，`REQUIREMENTS.md:18,63-69` | ✅ |
| 14 | 建议功能需求数 | `29` | `FR-01..FR-29`，`REQUIREMENTS.md:19` | ✅ |
| 15 | 智能技术适配 | `建议` | 设计采纳（TECH-STACK §2） | ✅ |
| 16 | 建议技术 | `YOLO/OpenCV` | Ultralytics YOLO26 + OpenCV，TECH-STACK:29 | ✅ |
| 17 | 智能子任务 | `识别灯具亮灭、明显破损或遮挡，辅助生成故障候选` | `vision/config.py:81-86` 4 类 raw（on/off/damaged/occluded）；`REQUIREMENTS.md` FR-12 | ✅ 见 §A-17a |
| 18 | 智能业务范围与边界 | `只处理静态巡查图片…不能替代电气安全检查` | `REQUIREMENTS.md:216-217` N3/N4 + `vision/__init__.py:3-9` 红线 | ✅ |

### A-9a / A-10a：17 表口径 —— 精确逐名比对（无增无减）

用户 17 个中文表名逐字对应 `docs/SCHEMA.md:111-127` 圈号①–⑰，**一一命中、无遗漏、无多余业务表**：

| 圈号 | 中文名（用户） | 物理表名（设计） | 备注 |
|---|---|---|---|
| ①–⑰ | 账号…返修单（逐字） | `accounts`…`rework_orders` | 全部命中 |

**显式排除项（保持 17 表不增不减的判据）**——`SCHEMA.md:577-580` 明确声明四张「合并候选」**不单独建表**，而是并入既有表：

| 候选表 | 处置 | 坐标 |
|---|---|---|
| `idempotency_keys` | → `audit_logs.idempotency_key` + 部分唯一索引 | `SCHEMA.md:577` |
| `sessions`/`refresh_tokens` | 不建表（无状态 JWT + `token_version`） | `SCHEMA.md:578` |
| `technician_skills` | → `accounts.skills text[]` | `SCHEMA.md:579` |
| `sla_alerts` | → `fault_tickets.is_sla_overdue/overdue_stage` + `notification_tasks` | `SCHEMA.md:580` |
| `alembic_version` | 迁移元数据表，明确排除于 17 业务表外 | `SCHEMA.md:28-38`、`REQUIREMENTS.md:22` |

> **判定**：17 表**既未膨胀也未缩水**，四张「合并候选」是**显式标注的实现细节**（在现有表内承载语义），不构成第 18 张业务表。属**已授权的合理设计决策**，非偏离。

### A-17a：智能子任务「亮灭/破损/遮挡」的视觉类别口径

用户要的是「亮灭、明显破损或遮挡」。视觉草稿与 Schema 的类别关系：

| 用户要求 | 视觉 `RAW_CLASSES`（`config.py:81-86`） | DB `fault_type` 6 枚举（`SCHEMA.md:236`） | 判定 |
|---|---|---|---|
| 亮灭 | `streetlight_on`/`streetlight_off` | `LAMP_OUT` | ✅ |
| 明显破损 | `streetlight_damaged` | `FIXTURE_DAMAGED` | ✅ |
| 遮挡 | `streetlight_occluded` | `OCCLUDED` | ✅ |
| —（无） | `streetlight_on` | `OTHER` | ✅ 兜底类，非膨胀 |
| —（**严禁视觉产出**） | — | `ELECTRIC_HAZARD`/`FLICKERING` | ⚠️ 业务超集，见下 |

> **`ELECTRIC_HAZARD`/`FLICKERING` 属业务 `fault_type` 枚举超集**（`SCHEMA.md:236`），但 `BUSINESS-LOGIC.md:561`（V3）+ `SCHEMA.md:273` CHECK + `vision/contract.py:160-161` **三层强制视觉永不产出**——它们仅由**人工确认**时改 `fault_type` 产生（`API.md:115`、`BUSINESS-LOGIC.md:41` A3 须带 `manual_note`）。这是「模型能给出 ≠ 模型给出全部类型」的清晰边界，**非越界，是人工补充分类通道**。属于合理的业务枚举设计。

---

## A+.「是否过度设计 / 未授权膨胀」专项核查（18 列之第 14 项）

**结论**：未发现「新增用户没要的模块/角色/KPI」。所有疑似新增项均属**实现细节**或**显式授权的裁决产物**，且每一处都已在文档标注来源与边界：

| 疑似新增 | 是否属未授权膨胀 | 依据坐标 |
|---|---|---|
| `MANUAL_REVIEW` 状态 + `review-resolve` 端点 | **否——实现细节** | `BUSINESS-LOGIC.md:48,53-55`：技术员 `escalate`、累计拒单 ≥3、超 `max_rework_rounds` 均路由至此；是用户「保留必要的撤销、驳回、超时或复核路径」的直接实现 |
| `fault_evidences.ticket_id` 可空（孤儿证据） | **否——Lead 裁决 #1，且为红线可验证性所必需** | `SCHEMA.md:351-368`：零候选时「白天灭灯不误报」红线必须在 DB 侧可验证，否则只是口头承诺；CHECK 约束使可空「不随便空」 |
| 模型/框架 fallback（`auto`/`strict_custom`/`baseline_pretrained_opencv`/`yolo26_pt`/`yolo26_onnx_e2e`） | **否——诚实降级机制，非膨胀** | `RUNBOOK.md:284` `auto` 诚实标注 + `strict_custom` 静默降级禁令；DB `CHECK` 封顶 0.65（`vision/config.py:76`）；`vision/baseline.py` 是占位主干非硬编码假 JSON |
| 34 个 `error.code` / 57 端点 / 128 `data-testid` | **否——契约细化，非范围扩张** | `API.md:20` 计数声明 |
| 4 状态机 / 29 状态 / 36 边 | **否——用户「状态追踪与异常处置」的实现形态** | `BUSINESS-LOGIC.md` |

> **唯一一处「超出原始字面」的设计选择**：`fault_type` 枚举含 `ELECTRIC_HAZARD`/`FLICKERING` 两类，**超出**用户智能子任务的「亮灭/破损/遮挡」字面——但其业务语义是「人工确认补充类别」（`BUSINESS-LOGIC.md:367` `S_fault_type` 评分权重含全部 6 类），且被三层约束禁止视觉产出。**判定：合理业务扩展，非偏离**（在 §B-D 中配合 `manual_note` 校验）。

---

## B. 用户新裁决与现行文档的冲突清单

> 基线：`git diff docs/DECISION.md` = `+51/−1`（HEAD `a16335c`）。用户裁决文本逐字取自学到的 diff。坐标经本次逐一读取核实。

### B-1 `D-02` 上传上限 15 MiB —— 文档已对齐，**但 1 处素材残留**

用户裁决：`确定 15MiB`（`DECISION.md:89`）。

| 位置 | 值 | 状态 |
|---|---|---|
| `.env.example:43` `MAX_UPLOAD_SIZE=15`；`:41-42` 注释说明 10MiB 会误拒合规原图 | 15 | ✅ 已对齐 |
| `docs/RUNBOOK.md:63` | 15 | ✅ |
| `docs/API.md:280`（`≤15 MiB → 413`）| 15 | ✅ |
| `docs/SCHEMA.md` `fault_evidences` CHECK `<=15728640` | 15 | ✅ |
| `docs/DECISION.md:84` `413 IMAGE_SIZE_EXCEEDED` | 15 | ✅（已把 `PAYLOAD_TOO_LARGE` 改名） |
| ⚠️ `.team/notes/frontend-dev/stage1-frontend-spec-input.md:457,639` | **10 MiB（取更严者）** | 🔴 **遗留缺陷**（前端素材仍按 `.env.example` 旧值 10 推断「取更严者」） |

> **判定**：`docs/` 定稿层 5 处全对齐；唯 `frontend-dev` **素材文件** `:457`（错误映射表）与 `:639`（`before-upload` 校验）仍写「≤ 10 MiB」，其推理依据的 `.env.example` 旧值 `10` 已改为 `15`——**这是真实遗留缺陷**（Lead 已确认）。影响：前端会错误拦截合规的 8–14 MiB 原图。**修正范围**：仅 `.team/notes/frontend-dev/stage1-frontend-spec-input.md:457,639`（本轮不改，待终审统一改）。

### B-2 `D-06` 模型档位与本地冒烟 —— **多处未同步，真实冲突**

用户裁决（`DECISION.md:188`）：`明确只用 yolo26s。没有本地冒烟这一说，以后必须在远程服务器上跑开发和训练。推理才会在本地跑。`

冲突坐标（**全部未按新裁决改写**）：

| 坐标 | 现文 | 与用户裁决的冲突 |
|---|---|---|
| `docs/RUNBOOK.md:222` | `本地 CPU 冒烟测试可临时用 yolo26n.pt，但交付训练一律从 s 起` | 🔴 直接违背「没有本地冒烟这一说」「只用 yolo26s」 |
| `docs/RUNBOOK.md:259-264` | `本地哈希比对与推理冒烟`（CPU 或 GPU 本地执行推理验证）| 🔴 本地冒烟测试段未删 |
| `docs/RUNBOOK.md:234` | `需要更高分辨率时优先升级到 yolo26m.pt/yolo26l.pt` | 🔴 违背「只用 yolo26s」 |
| `docs/RUNBOOK.md:224` | `Track B/C 若改用 1280 训练…` | 🔴 残留 Track B/C（用户只用 s 单档） |
| `.env.example:62` | `1024 = Track A training resolution; override to 1280 only for Track B/C checkpoints` | 🔴 Track A/B/C 表述未清 |
| `docs/DECISION.md:181` | `本地 CPU 冒烟可临时用 n，交付训练一律从 s 起` | 🔴 D-06 段内自身历史方案 |
| `docs/DECISION.md:536` | `1024（Track A）。Track B/C 若用 1280…` | 🔴 第三档 D-28 残留多档模型 |
| `docs/TECH-STACK.md:340` | 列 `yolo26n/s/m/l/x.pt` 五档 | 🟡 属官方出处列举（非选型承诺），但需在选型章节明确「本项目锁 s」 |

> **正面**：`RUNBOOK.md:222` 已写「交付训练一律从 `s` 起」、`docs/DECISION.md:184` 状态行已标注 `yolo26s.pt`；`compose.yaml:5` 与 `.env.example:55` `YOLO_FALLBACK_MODEL=yolo26s.pt` 已是 s。**冲突集中在「本地冒烟」与「Track B/C 多档」的残留文案**。
> **界定「本地推理」与「本地冒烟」**：用户明确「推理才会在本地跑」→ `RUNBOOK.md:259-264` 本地哈希比对属**合规的本地推理集成验证**，不算冒烟；`RUNBOOK.md:222` 的「本地 CPU 冒烟测试用 `yolo26n.pt`」属**训练侧冒烟**，直接违背。**需 Lead 裁决**：`259-264` 本地推理验证是否保留（推理本地化合规）还是全部移至远端。

### B-3 `D-11` SLA 时限按郑州规则 —— **4 档外推值全面冲突**

用户裁决（`DECISION.md:293`）：`严格按照规范和法律，不允许使用更严格或者更宽松的。请按照郑州市的规则来。`

**冲突本质**：现行设计的 4 档时限是**工程外推值**，且含「严于规范」「无规范依据」的自我标注，**与用户「不更严不更松」+「按郑州市规则」的指令直接冲突**。现存引用地方规范的是**深圳/北京**条款，**零郑州市条款**。

**冲突坐标（docs 层，全部需按郑州规则重定）**：

| 坐标 | 现文 | 性质 |
|---|---|---|
| `docs/BUSINESS-LOGIC.md:336-339` | P1=15min/2h「严于规范」；P2=30min/8h「介于」；P3=2h/24h「恰好对齐」；P4=8h/72h「无规范依据，纯工程初值」 | 🔴 4 档全部非郑州规则；P1 严于规范（违反「不更严」）；P4 无依据（违反「按规范」）|
| `docs/BUSINESS-LOGIC.md:448` | 取 `T/CMEA` 三档锚点，`P1.response` 取 15 分钟 | 🔴 锚定团体标准而非郑州地方规定 |
| `docs/BUSINESS-LOGIC.md:605` | 规范出处 = `T/CMEA 21—2021` + 深圳令 309 + `DB11/T 1876-2021` | 🟡 来源清单无郑州 |
| `docs/DECISION.md:275-280` | D-11 段内同一张 4 档表 | 🔴 同 BUSINESS-LOGIC |
| `docs/DECISION.md:286` | `P4 完全无依据…多数城市地方考核办法比团体标准更严` | 🔴 自我承认外推 |
| `docs/REQUIREMENTS.md:282-287` | 规范阈值表（T/CMEA + 深圳令 + DB11） | 🟡 阈值表本身无错（是出处），但缺郑州 |
| `.team/notes/backend-dev/stage1-backend-spec-tables.md:404-407` | `sla_policy` JSON：`P1:{15,120}` `P2:{30,480}` `P3:{120,1440}` `P4:{480,4320}` | 🔴 落到规则载荷的具体数值 |
| `.team/notes/backend-dev/stage1-backend-spec-mechanisms.md:143-146,251` | 4 档表 + 15min 锚定 | 🔴 后端素材 |

> **关键缺口**：全仓 `rg '郑州|Zhengzhou|河南|Henan'` 在 `docs/` 与 `.team/notes/`（非 logs）**仅命中 `docs/DECISION.md:293` 用户原话一行**——**整个设计套件没有任何郑州地方规定的条款依据**。当前唯一「地方规定」证据是深圳令第 309 号与北京 `DB11/T 1876-2021`，**均非郑州**。
> **判定**：`sla_policy` 的 4 档数值（15/30/120/480 → 120/480/1440/4320 分钟）**全部需重新标定**；且需补充**郑州市**《城市道路照明设施运行维护》或《城市照明管理办法》类规定的原文条款作为新锚点，替换 T/CMEA 三档锚定。**需 Lead 提供郑州原文条款**（或其明确要求「团队自行检索郑州规范」）——此为裁决阻塞项。

### B-4 `D-07` 单人 + REBASE —— **未发现冲突，但需核实**

用户裁决（`DECISION.md:214`）：`我们只有一个人。我们只用 REBASE。`

| 核查点 | 结果 | 判定 |
|---|---|---|
| `COMMIT-PROTOCOL.md` 分支策略 | 全文 `rg '分支\|rebase\|merge\|冲突'` → **0 命中**（`TEAM.md:499` 亦声明协议未规定分支） | ✅ 无「merge」规范冲突；**需补充** REBASE-only 规则（新增加载而非改写冲突） |
| `TEAM.md`「9 人 = 1 Lead + 8」 | `TEAM.md:30,44` 团队阵容 | ⚠️ **非冲突**——9 人是 **agent 角色域**（非人类开发者数）；D-07 的「我们只有一个人」指**人类开发者**单人，与 9 个 agent 角色域不矛盾。需在 TEAM 或决策注记中显式区分「agent 域 ≠ 人类开发者数」防误读 |
| `DECISION.md:201` `(a) 全在 main 上直线推进` | 现状方案含「不建分支」 | ⚠️ 与「用 REBASE」语义相容（rebase 是在分支或无分支下的线性化手段），但建议把备选改写为「单人单仓库 + `rebase` 线性历史」以贴合用户意图 |

> **判定**：**无实质性偏离**。需做的只有两件小事：① 在 `COMMIT-PROTOCOL.md` 补一条「单人 + rebase 线性历史」规则；② 在 `TEAM.md` 决策注记中写明「9 人是 agent 域，人类开发者为单人」，消除「9 人 vs 一个人」的表面冲突。

### B-5 `D-03` 前后端上传张数 —— **真实冲突在前端 UI 单图，非后端/视觉能力**

用户裁决（`DECISION.md:111`）：`前后端必须对应，请见我们需求文档。`

> ⚠️ **Lead 已确认的关键修正**：`vision-dev` **本来就支持 ≤10 批量**（`vision-dev/stage1-vision-spec-input.md:88`「单次批量 ≤10 张」），所以 D-03 的真实冲突**只在「前端 UI 是否只做单图」**，而非「后端/视觉能力不支持批量」。

| 位置 | 现状 | 判定 |
|---|---|---|
| `docs/API.md:111,120,279` | `files`(1–10) 三处、`TOO_MANY_FILES`/`NO_FILES` | 后端能力 1..10 ✅ |
| `.team/notes/vision-dev/stage1-vision-spec-input.md:88` | 单次批量 ≤10 | 视觉能力 ≤10 ✅ |
| `.team/notes/frontend-dev/stage1-frontend-spec-input.md:464,1187` | 前端「按单图（1 张）实现」+ `TOO_MANY_FILES` 文案「单次最多上传 10 张」 | 🔴 **前端 UI 单图 vs 后端 1..10 契约不一致** |
| `docs/REQUIREMENTS.md` | **零命中**（`rg 'files\|张数\|单次上传\|一批\|多张\|单张'` 无结果） | 🟡 需求文档**未规定**张数 |

> **判定**：用户说「请见我们需求文档」，但 `REQUIREMENTS.md` **根本不写上传张数**（零命中）——**需求文档本身没有裁决依据**。因此 D-03 的真实裁决不在 REQUIREMENTS，而在「后端 1..10 契约」与「前端单图实现」之间取齐。两个合法选项：(a) 后端改 `1..1`+删 `TOO_MANY_FILES`；(b) 前端做多图 UI。**不存在「后端不支持批量」的阻塞**（视觉已支持 ≤10）。需 Lead 在两者间裁决。

### B-6 `D-04` `data-testid` 121 vs 128 —— 用户未裁决，团队内部待定

用户回答（`DECISION.md:140`）：`这里和我没有关系，是你们团队内部的事情。`

| 事实 | 坐标 |
|---|---|
| 冻结清单 121 | `API.md:435` |
| 灰区 7 个（`dashboard-summary-card-*`/`*-row-*`） | `API.md:439` |
| 并集 128 | `API.md:20,437` |
| 「两个数字都对，层级不同」 | `API.md:445` |

> **判定**：用户明确授权团队内部决定。**数字本身无错**（121 冻结 + 7 灰区 = 128 并集，层级分明），问题在「7 个灰区未冻结 → `tester` 无据可依」。这是 `D-04` 留给 Lead 的内部裁决项（补入 128 或明确排除 121）。**非需求偏离**，属待 Lead 决断的实现细节。

### B-7 `D-10`/`D-12`/`D-17`/`D-18` —— 全部与现行文档一致

| 裁决 | 用户答案 | 现行文档 | 判定 |
|---|---|---|---|
| `D-10` PostgreSQL 17.12 | `A`（只支持 17.12）| `compose.yaml:5` `postgres:17.12-alpine` digest 钉死；`TECH-STACK:20,28` 仍写 `16/17` | 🟡 **镜像已锁定 17，但 TECH-STACK 表述残留 `16/17`**——需把 `16/17` 改为 `17.12`（小事）|
| `D-12` 中文等级命名 | `是最终名字` | `SCHEMA` 无「特级/一级」；`BUSINESS-LOGIC:305` 声明 `P1-P4` 非中文规范用法、`display_name` 出厂「特急/加急/普通/观察」 | ✅ 一致（`display_name` 可配置，命名已分离）|
| `D-17` 亮灯率 KPI | `A`（不实现）| `REQUIREMENTS.md:297,300`「17 表内无统计表，若实现需加第 18 张表」「阶段一不实现为功能需求」 | ✅ 一致 |
| `D-18` 破损检出率承诺 | `A`（不承诺）| `REQUIREMENTS.md:258-259`「不能承诺」「阶段一不给数字」 | ✅ 一致 |

### B-8 `D-01`/`D-05`/`D-08`/`D-13`/`D-14..D-16` —— 无冲突或属已记录待决

| 裁决 | 用户答案 | 判定 |
|---|---|---|
| `D-01` SSH 接入 | `选择 A…ssh -p 13142 …` | 🟡 `.env.example:73` `GPU_SSH_PORT=22` 与用户给 `13142` 不符——**占位符未更新**；`GPU_SSH_HOST`/`GPU_SSH_USER` 仍为 `replace-with-*`。属**参数待填充**（已知空缺），非设计冲突 |
| `D-05` SQLAlchemy 2.0 | `维持当前` | ✅ `pyproject` 空依赖、`TECH-STACK` 锁 `>=2.0.58,<2.1` 一致 |
| `D-08` mypy/pyright 门禁 | `这是什么，和我详细讲一下` | 🟡 用户在**提问**而非裁决；`DECISION.md` 该条仍为 🔴 待裁决。无冲突，待 Lead 解释 |
| `D-13` 分级权重 | `我不知道` | 🟡 非裁决，保持工程初值待标定，无冲突 |
| `D-14`/`D-15`/`D-16` | `A` / `A` / `A` | ✅ 与 `DECISION.md` 工程初值一致（2 轮返修/72h TTL/3 单上限）|

---

## C. 新增风险项（非 18 列偏离，属实施可行性）

| 风险 | 证据 | 影响 | 建议 |
|---|---|---|---|
| **远端存储不足** | Lead 实测：远端 `overlay 30G` 已用 `23G`，**仅余 7.8G** | `D-06` 要求「开发+训练全在远端」，但单 `yolo26s.pt`(20MB)+训练数据集+虚拟环境+`best.pt` 输出可能超限 | **需用户裁决**：清理远端空间 / 挂载额外存储 / 或放宽 `D-06` 允许部分工作本地进行 |
| `GPU_SSH_*` 占位符 | `.env.example:72-74` | 训练链路悬空 | 填入用户提供的 host/port(`13142`)/user |
| `mypy`/`pyright` 未跑 | `DECISION.md D-08` | 类型检查空白 | 用户需先理解后裁决 |

---

## D. 审计结论

### 总体判定

| 维度 | 结论 |
|---|---|
| **18 列是否偏离** | **未发现实质性偏离**：17/18 列逐字一致，1 列（数据表）为已授权实现细节且显式标注；智能边界、明确不做、三角色、9 UC/29 FR/17 表全部命中 |
| **过度设计/未授权膨胀** | **未发现**：`MANUAL_REVIEW`、孤儿证据、fallback 模型、`error.code`/端点/状态机计数均属实现细节或 Lead 裁决产物，逐处有来源标注 |
| **新裁决冲突** | **4 处真实冲突需修复**：`D-02` 前端素材 10MiB 残留；`D-06` 本地冒烟 + Track B/C + m/l 档残留；`D-11` SLA 外推值 + 零郑州条款（**最严重**）；`D-10` TECH-STACK `16/17` 残留 |
| **待 Lead 内部裁决** | `D-04`（121 vs 128 补入）；`D-03`（前端单图 vs 后端 1..10 对齐方向）；`D-08`（门禁待解释）；`D-06` 本地推理验证范围 |
| **新增风险** | 远端 7.8G 存储 vs 「远端开发+训练」可行性（**需用户裁决**）|

### 严重度分级

- **P0（需独立核实）**：`D-11` SLA 全面外推 + 零郑州证据 —— **用户指令性冲突，阻塞验收**。必须由 Lead 提供郑州条款或明确授权团队检索。
- **P1**：`D-06` 本地冒烟/多档残留、`D-02` 前端 10MiB 残留、`D-03` 前端单图 vs 后端批量方向。
- **P2**：`D-10` `16/17` 表述、`.env.example` SSH 占位符、`D-07`「9 人 vs 一个人」表述注记、`D-04` 内部裁决。
- **P3**：`REQUIREMENTS.md:170`「43 周」应为「4 周」（明显笔误，语义无歧义）；远端存储容量。

---

## E. 审计边界与未证实声明（诚实标注）

| 已证实 | 未证实 / 未运行 |
|---|---|
| 18 列逐字比对（基于用户提供的权威原文） | 未运行任何代码；`vision/` 8 文件为未验证草稿，仅静态核查逻辑与契约 |
| 全部 `docs/` + `.env.example` + `pyproject.toml` + `compose.yaml` + `vision/` 坐标 | `backend/`、`web/`、`tests/`、`migrations/` 不存在，无法核查实现层偏离 |
| `git diff docs/DECISION.md` 精确变更点 | 用户 D-03 所指「需求文档」中并无张数条款（零命中已证实）——裁决需团队补齐方向 |
| SLA 外推值坐标 + 郑州条款零命中 | 郑州地方规定原文条款**未检索**（本轮未用 Exa，属外部事实缺口）|
| 远端 GPU 实测（Lead 提供） | 远端实际可用磁盘、依赖安装、训练可行性未实测 |

> **扫描范围声明**：凡「零命中」均附范围与排除项；`.team/notes/auditor/logs/` 为历史快照，不计现状；`.serena/` 未扫描（属 Serena 内部状态非设计文档）。
