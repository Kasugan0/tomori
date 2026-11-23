# 阶段一定稿文档与素材全量一致性独立终审报告（`stage1-final-consistency-audit`）

> **审计员**：`auditor`（完全只读）｜**任务**：`task-32`（初版终审）＋ `task-41`（12 项清零复核）＋ **`task-43`（`N-1` 清零终版收口，见 §五）**
> **仓库状态**：`HEAD = a16335cc`（3 个签名 commit）；工作树含阶段一最终修订（`M .env.example`、`M README.md`、`M docs/*.md`、`M docs/team/TEAM.md`、`?? vision/`、`?? audits/founding-alignment-audit.md`、`?? audits/stage1-final-consistency-audit.md`）。阶段二未开始，**零 git 写操作**。
> **终版总判定（`task-43` 终审后）**：**`0 P0 / 0 P1 / 0 P2`** —— 原 `2 P0 / 4 P1 / 6 P2` 共 12 项 **及** `task-41` 新发现 `N-1`（`393 行` 硬编码漂移）全部实测清零，无回归、无遗留缺陷；`N-2`（`RUNBOOK §6.2` 历史 10 行→现 13 行）已登记为历史口径说明，不属缺陷。阶段一在文档与契约层面**全面达标，可正式冻结**。

---

## 0. 统计口径分层声明与扫描范围（TEAM §4.6 / §5.4）

| 审计项 | 分子 | 分母 | 排除项 | 实测方式 / 命令 |
|---|---:|---|---|---|
| 终审发现（初版 `task-32`） | **12**（`2 P0 / 4 P1 / 6 P2`）→ 收口后 **0 / 0 / 0** | 全仓 `docs/` + `README.md` + `.env.example` + `.team/notes/{backend-dev,frontend-dev,vision-dev,tester,git-keeper,dev-ops}/` + `.serena/memories/tomori/` | `.team/notes/auditor/logs/`、`.team/notes/research/logs/`（仅历史日志与 npm 原始 JSON） | `rg` + Python 独立脚本 + 逐行 `read`；复核日志见 `.team/notes/auditor/logs/task41/` |
| 无依据 SLA 分钟数残留 | **0 处作为现行默认值**（仅 `docs/` 废除/对照声明） | 全仓设计文档与素材 | 废除说明与背景对照（`BUSINESS-LOGIC:304,348,354,375`、`DECISION:253,260`、`REQUIREMENTS:298-307`）；`72h` 仅指 `D-15` 已裁定 `ai_candidate_ttl_hours`（`API.md:311`） | `.team/notes/auditor/logs/task32/full-scan.log` |
| `DECISION.md` 条目与状态 | **40**（分档 `9/8/14/9`，`18✅/1🔴/1🟡/20🟢`） | 32 条独立状态行 + 第四档表 8 行 | 附录 A/B/C 非正文条目 | Python 独立解析脚本（`independent-checks.log`） |
| 用户原话保全 | **17 / 17**（11 段多字原话 + 6 条单字 `A`） | `D-01`–`D-18`（排除既成事实闭合 `D-09`） | `D-09`（用户未直接作答） | Python 逐块精确行匹配（`independent-checks.log`） |
| `grading_rules` 组合覆盖 | **144 / 144**（`94+34+14+2`） | $6 \times 3 \times 4 \times 2 = 144$ 输入组合 | 0 条死规则、0 个未命中空洞 | Python 笛卡尔积枚举验证（`independent-checks.log`） |
| Markdown 死链与表格错列 | **0 死链 / 0 错列** | 全仓 `docs/**/*.md` + `README.md` | 外部 `http(s)` URL 不在本地存在性检查内 | Python 解析 + `task-18-verify.sh`（`PASS=67 FAIL=1`） |

> **诚实边界**：阶段一**零行应用代码、迁移脚本或测试被运行过**（`backend/`、`web/`、`tests/`、`migrations/` 均不存在；`vision/` 为 8 个未验证草稿）。以下所有通过项均仅指**文档与素材契约层一致性**。

---

## 一、核心缺陷清单（不回避真冲突，逐条附 `file:line`）

### P0-1：`tester` 测试素材的 `sla_policy` 与 `grading_rules` JSON 契约结构、字段名及规则数与权威定稿（`API.md` / `BUSINESS-LOGIC.md` / `backend-dev`）严重冲突 【需独立核实（由 Lead 复核）】

| 维度 | 权威定稿（`docs/API.md`、`docs/BUSINESS-LOGIC.md`、`backend-dev`） | `tester` 素材（`.team/notes/tester/stage1-test-matrix-input.md`） | 阶段二直接后果 |
|---|---|---|---|
| **`sla_policy` 键名** | `{"statutory_deadline_hours": 48, "response_minutes": null, "emergency_notice_hours": 24, "applies_to_priorities": ["P1","P2","P3","P4"]}`（`API.md:462-467`、`BUSINESS-LOGIC.md:386-393`、`tables.md:432-437`） | `:212` 写 **`maintenance_deadline_minutes = 2880`** 与 **`deadline_applies_to_priorities`**；`:758` 校验 **`maintenance_deadline_minutes <= 0`** | 测试种子与规则校验用例全按不存在字段名构造，运行即爆 `422 INVALID_RULE_PAYLOAD` |
| **`grading_rules` 规则数与规则名** | **4 条规则**（`R1_SAFETY_OR_EXPRESS_CRITICAL` / `R2_EXPRESS_OR_MAIN_CORE_OR_CRITICAL_ASSET` / `R3_BRANCH_OR_MAIN_MINOR` + 顶层 `default_priority: "P4"`, `default_rule_id: "R4_FALLBACK_OBSERVATION"`；条件键为 `"any"`，`API.md:424-456`、`BUSINESS-LOGIC.md:408-413`） | `:150,:282,:501,:1095` 写 **`8 条有序规则`（`R1..R8`）**；`:502` `MUT-05b` 写不存在的 **`R1_SAFETY_OR_HAZARD`** 与 **`any_of`/`all_of`**；`:758` 写 **`is_default=true`** 与 **`缺 P1..P4 display_names`**（`API.md:422-456` 示例无 `display_names`，`tables.md:454` 限定为「若含」） | 单测与变异卡直接对不上真实 `grading_rules` Schema |
| **无资产取值** | **`null`**（`BUSINESS-LOGIC.md:417,430`、`SCHEMA.md:234`、`tables.md:503`） | `:282` 写 **「无资产取 `NONE`」** | L1 真值表与快照输入字段不匹配 |
| **快照紧急键名** | **`"emergency_action_required"`**（`BUSINESS-LOGIC.md:433`、`tables.md:511`、`mechanisms.md:204`） | `:150,:282,:326` 写 **`"emergency_handling_required"`** | E2E-01 Step 2 与 L1 快照断言必红 |
| **其它旧口径残留** | `SCHEMA.md:23,58-65` 为 **6 个部分唯一索引**（4 业务 + 2 配置）；`API.md:103-105` 仅 `GET/POST /business-rules`、`PATCH /business-rules/{id}` | `:151,:171` 仍写 **「`3` 个部分唯一索引」**；`:364` 仍写不存在的 **`PUT /api/v1/business-rules/{rule_code}`** | 迁移校验计数差 3 个；越权测试调错路由返回 `404`/`405` 而非 `403` |

### P0-2：`frontend-dev` 素材的 `response_deadline_at` / `repair_deadline_at` 生命周期仍为已废除的倒置旧口径 【需独立核实（由 Lead 复核）】

| 坐标 | 现文（错位旧口径） | 权威定稿（`D-11` 郑州 48h：`BUSINESS-LOGIC.md:464-472`、`SCHEMA.md:247-248`、`API.md:112,116,390`） | 阶段二直接后果 |
|---|---|---|---|
| `.team/notes/frontend-dev/stage1-frontend-spec-input.md:687` | 「响应超时看 `response_deadline_at`（**分级后即有值**）、修复超时看 `repair_deadline_at`（**首次接单后才有值，未接单时为 `null`**）」 | 郑州规章无分钟级响应时限 → **`response_deadline_at` 默认恒为 `NULL`**；**`repair_deadline_at` 于确认/建单瞬间即写入（`T_discover + 48h`）**，不等待接单 | 待派工/未接单工单在 UI 上不显示法定 48h 截止时间 |
| `.team/notes/frontend-dev/stage1-frontend-spec-input.md:926` | `workorder-detail-response-deadline`：「未分级时为 `null` → 渲染 `-`」（暗示分级后非空） | 即使分级后 `response_deadline_at` 默认仍为 `NULL` | 界面期待并把空值当异常态处理 |
| `.team/notes/frontend-dev/stage1-frontend-spec-input.md:927` | `workorder-detail-repair-deadline`：「**未接单时为 `null`** → 渲染『待接单后计算』」 | `AI_CANDIDATE_PENDING` 期间为 `null`；确认建单进 `PENDING_DISPATCH` 起即有值 | UI 错误展示「待接单后计算」，违反法定 `T_discover` 起算 |
| `.team/notes/frontend-dev/stage1-frontend-spec-input.md:1299` | 「双字段 `response_deadline_at` / `repair_deadline_at`（**后者未接单时 `null`**）」 | 同上 | 变更记录摘要继续误导实现者 |

### P1 发现（4 项：跨文档事实过时、残留旧文案或互相矛盾）

1. **P1-1 `docs/API.md:191` 残留已废除 `D-13` 阈值文案**：
   `422 | INVALID_RULE_PAYLOAD | 规则载荷语义校验失败（权重和 ≠ 1、阈值非递减等）`。其中「**阈值非递减**」系旧 `priority_thresholds` 遗留表述（同文 `:419` 已明文废除）。对比 `.team/notes/backend-dev/stage1-backend-spec-api.md:407` 已同步为「`grading_rules` 缺 `default_priority=='P4'` / 条件枚举非法、`sla_policy.statutory_deadline_hours <= 0`、`dispatch_scoring_weights` 权重和 ≠ 1 等」。
2. **P1-2 `docs/DECISION.md:50` 远端工具事实与实测相反**：
   仍写「`远端现为 Python 3.10.12、无 uv/conda；由 dev-ops 按官方指南安装 uv 与 Python 3.12 至 /root/autodl-tmp`」。实测已更正（`docs/RUNBOOK.md:173,223,279`、`.team/notes/dev-ops/uv-remote-setup-research.md:153-176`、`remote-gpu-probe.md:17`）：远端**已装 `/root/.local/bin/uv` `0.13.7` 与 `/root/miniconda3/bin/conda` `24.4.0`**，只是不在非交互 SSH 的 `PATH` 中，**无需安装 `uv`**。
3. **P1-3 `D-07` / `D-08` 在 `DECISION.md`、`TEAM.md` 与 `COMMIT-PROTOCOL.md` 之间双向过期**：
   - `docs/DECISION.md:176,189` 与 `docs/team/TEAM.md:502,515` 仍写「`COMMIT-PROTOCOL.md` 全文无分支命名规范… → `0 行`（该文件共 `318 行`）」「该文件需补…本轮未修改」。而 `task-28` 已将 `.team/notes/git-keeper/COMMIT-PROTOCOL.md` 更新至 `393` 行并补齐 §1.4 与 `R16`（`rg -n '分支|rebase|merge|冲突'` 现命中 **25 行**）。同步更新 `DECISION.md:176` 时须联动更新 `.team/notes/research/logs/task-18-verify.sh:277` 的 `grep -qF "0 行"` 检查。
   - `.team/notes/git-keeper/COMMIT-PROTOCOL.md:186` 仍写「`D-08` 用户尚未拍板，本条在 `DECISION.md` 中仍是 🔴 待裁决」，与 `docs/DECISION.md:195,215`（`✅ 团队已工程定案`）过时冲突。
4. **P1-4 `docs/DECISION.md` 附录自相矛盾 + `docs/TECH-STACK.md` 三处未同步**：
   - `docs/DECISION.md:604`（附录 A）仍写「`派单权重`在 `docs/` 全库无具体数值（仅 `REQUIREMENTS.md:129` 的 `FR-16` 提到该参数名，`TECH-STACK.md:607` 说明`未标定`）」，与同文件 `:630`（附录 B 写明 `dispatch_scoring_weights = (0.40, 0.35, 0.25)`）及现行 `TECH-STACK.md:607`（已无该说明）矛盾。
   - `docs/TECH-STACK.md:82` 仍写「`🔴 标注：🟡 待用户确认 … 已列入 §8 待裁决清单`」（与 `:584` 及 `D-05` `✅ 维持 <2.1` 矛盾）；
   - `docs/TECH-STACK.md:135` 仍写 PostgreSQL 版本为「`16 或 17 | 🟡 允许区间`」（与 `:20,28,605` 及 `D-10` `✅ 仅 17.12` 矛盾）；
   - `docs/TECH-STACK.md:348` 仍写「`docs/RUNBOOK.md:222` 相邻的 `F14` 违例未清…待 `dev-ops` 执行」（实际 `dev-ops` 已在 `RUNBOOK.md:336-350` 清账解决）。

### P2 发现（6 项：非阻断但可观测的小口径/文字残留）

1. **P2-1 `.env.example:62`**：注释仍含 `for the Track A checkpoint`（虽数值 `1024` 正确，但 `Track A` 与 `D-06` 清除多轨道表述不齐）。
2. **P2-2 `docs/RUNBOOK.md:390,392`**：权重上线门禁清单仍出现「**冒烟推理**」措辞（虽语义上是本地成品权重校验而非 `D-06` 取消的 `yolo26n` 本地训练前冒烟，建议改称「推理验证」以与 `.team/notes/dev-ops/remote-gpu-probe.md:37-38` 对齐）。
3. **P2-3 `README.md:25`**：导航表仍写 `128 个 data-testid 锚点（121 已冻结 + 7 灰区待裁决）`（`D-04` 已定案全 128 冻结；`README.md` 未列入 `task-29` `write_scopes`）。
4. **P2-4 `.team/notes/frontend-dev/stage1-frontend-spec-input.md:280,42,1111`**：`:280` 仍将 `P1` 界面标示写为「`紧急`」（`D-12` 终局显示名为「**`特急`**」）；`:42,1111` 仍写 `typescript 5.9.3` / `~5.9.3`（`docs/TECH-STACK.md:368` 已定 `6.0.3`）。
5. **P2-5 `.team/notes/backend-dev/stage1-backend-spec-package.md:14,200,228,356`**：未同步的旧版包结构素材仍保留 `backend/pyproject.toml` 独立包（vs `D-32` 单包）、`sqlalchemy[asyncio]>=2.1.9`（vs `D-05` `<2.1`）及 `docs/API.md §10.2`（现为 §8.2/§8.3）。
6. **P2-6 `.team/notes/dev-ops/stage1-infra-facts.md:81` & `task-18-verify.sh:340`**：前者 `:81` 留有早期记录 `Track A 训练分辨率`；后者 `:340` 描述文字写「12 段原话」（实际循环验证 `11` 段多字原话，因 `D-10`/`D-14..D-18` 为单字母 `A`）。

---

## 二、判据逐项核验结果

### 判据 1：原始 18 列需求 + 零无依据设计 / 零坏品味 + `task-31` 三点微差

| 检查项 | 结论 | 实测依据与坐标 |
|---|---|---|
| **18 列原始需求对齐** | ✅ 通过 | 承接 `audits/founding-alignment-audit.md` 并复核：3 角色、9 UC、29 FR、29 VC、17 业务表（排除 `alembic_version`）、4 状态机 / 29 态 / 36 边 / 11 终态、N1–N5 红线全部命中无偏离 |
| **无依据 SLA 分钟数清零** | ✅（`docs/` + `backend-dev`）/ ❌（`frontend-dev` 语义倒置、`tester` 键名错位） | `docs/`、`.env.example`、`backend-dev` 无 `15/30/120/480/4320` 作为默认值；所有 `2880`/`48h` 均锁定郑州市令第 152 号（2020 第 237 号二次修正）第 19 条第 3 款（`BUSINESS-LOGIC.md:307-342`、`SCHEMA.md:247-248`、`REQUIREMENTS.md:269-281`）；`response_deadline_at` 默认 `NULL`；`P4` 观察不免除 `48h` |
| **`100×100` Bug 与魔数权重清零** | ✅（`docs/` + `backend-dev`）/ ❌（`API.md:191` 残留一句、`tester` Schema 漂移） | `grading_rules` 取代浮点加权；`grading_score` 恒写 `NULL`；`facility_asset_id IS NULL` 时 `asset_importance=null` 不取 `35`；Python 独立验证 144 组合 `R1=94, R2=34, R3=14, R4=2`（`sum=144`、0 死规则） |
| **`task-31` 微差 (a) `?rule_code=`** | ✅ 已修复 | `docs/API.md:103` 已补齐 5 个：`grading_rules` / `sla_policy` / `dispatch_scoring_weights` / `rework_policy` / `ai_candidate_ttl_hours` |
| **`task-31` 微差 (b) SLA ② 扫描状态** | ✅ 已修复 | `docs/BUSINESS-LOGIC.md:492` 已含 `MANUAL_REVIEW`（`PENDING_DISPATCH`/`REWORK_PENDING_DISPATCH`/`DISPATCHED`/`IN_REPAIR`/`MANUAL_REVIEW`） |
| **`task-31` 微差 (c) `MUT-05` 引用对齐** | ✅（`docs/` 已修）/ ⚠️（`tester:502` 自身规则名错位） | `docs/BUSINESS-LOGIC.md:371,415,472,507` 与 `docs/team/TEAM.md:333,339,344,345` 均已对齐 `MUT-05a`/`MUT-05b`/`MUT-15`/`MUT-17` |

### 判据 2：反认知卸载核查 & `DECISION.md` 剩余 🔴 / 🟡 盘点

| 检查项 | 结论 | 细节 |
|---|---|---|
| 已收口不再上推用户的事项 | ✅（仅 `DECISION.md:50` 工具现状事实误记待改） | `/root/autodl-tmp/tomori/`（`RUNBOOK:176-205`）、`uv` 官方 env 全部带 Astral URL 出处（`RUNBOOK:207-221`、`uv-remote-setup-research.md:27-79`）、`D-03` 全链路 `1..10`、`D-04` `128` testid、`D-08` `mypy`、`D-11` 郑州 48h、`D-13` 确定性 `grading_rules` 均已在内部消化定案 |
| 剩余 🔴 项（仅 1 条） | ✅ 属外部法律边界 | **`D-29` 🔴**（`DECISION.md:507-513`）：公开数据集 D2 是否发函取得书面商用授权——属外部法务/版权授权，团队无权自决，保留 🔴 合理 |
| 剩余 🟡 项（仅 1 条） | ✅ 属产品界面偏好 | **`D-30` 🟡**（`DECISION.md:515-520`）：夜间正常亮灯 `streetlight_on` 是否作为参照框回传前端（`is_fault_candidate=false`），可不反对即按建议执行，非工程推锅 |

### 判据 3：跨文档一致性、用户原话、脚本门禁与 Serena Memories

| 检查项 | 结论 | 细节 |
|---|---|---|
| `D-01`（`13142`、`/root/autodl-tmp`、`uv` URL、实测更正） | ⚠️ 部分一致 | `RUNBOOK.md` 与 `.env.example:79-86` 已齐；唯 `DECISION.md:50` 仍写「无 `uv`/`conda`」（P1-2） |
| `D-02`（15 MiB） | ✅ 全仓一致 | `docs/API.md:201`、`docs/SCHEMA.md:343,410`、`docs/RUNBOOK.md:63`、`.env.example:43`、`frontend-dev:468,651`、`vision-dev:88`、`tester:399,764`、`COMMIT-PROTOCOL:280` 全部统一为 `15 MiB / 15,728,640 B` |
| `D-03`（全链路 `1..10`） | ✅ 全链路对齐 | `docs/API.md:111,120,279`、`vision-dev:88`、`frontend-dev:475,651,1127`、`tester:324,327` 全为 `1..10` |
| `D-04`（`128 = 121 + 7`） | ✅（`README.md:25` 待同步） | `docs/API.md:20,484-517`、`frontend-dev:831,1005-1028`、`tester:616-623`、`COMMIT-PROTOCOL:228` 统一 `128`；唯 `README.md:25` 留有 `7 灰区待裁决`（P2-3） |
| `D-06`（`yolo26s.pt`、`imgsz=1024`、远端开发训练、仅本地推理） | ✅ 已清零 | `docs/RUNBOOK.md:157,160,331,343`、`docs/DECISION.md:149-167,499-505`、`.env.example:62`（`Track A` 已改 `yolo26s.pt`）、`RUNBOOK:390,392`（「冒烟推理」已改「推理验证」）全部对齐；`yolo26n/m/l/x` 仅作为官方出处与「不使用」声明存在 |
| `D-07`（单人 `rebase-only` + 9 AI agent 域） | ✅ 已清零 | `docs/DECISION.md:176,189`、`docs/team/TEAM.md:502`、`.team/notes/git-keeper/COMMIT-PROTOCOL.md:34-81,104,320`（§1.4 + `R16`）与 `.team/notes/research/logs/task-18-verify.sh:277` 四方一致（`25 行` 命中，已取消 `393 行` 硬编码） |
| `D-10`（PG 17.12） | ✅ 已清零 | `compose.yaml:5`、`RUNBOOK.md:37`、`TECH-STACK.md:20,28,135,605` 全部为 17/17.12；`:135` 原「16 或 17」已改「**17（17.12）** ✅ 已定案」 |
| **用户原话 100% 保全** | ✅ 通过 | Python 独立检验 `D-01`–`D-18` 全部 17 项用户输入（含 11 段长原话与 6 条单字 `A`），一字未删且均位于各自 `D-xx` 块独立整行 |
| **`DECISION.md` 计数独立重算** | ✅ 通过 | `9 + 8 + 14 + 9 = 40`；`18✅ + 1🔴 + 1🟡 + 20🟢 = 40`；标题与 `**状态**` 行 **0 不一致** |
| **Serena Memories** | ✅ 一致且被忽略 | `.serena/memories/tomori/{context,open-items,repo-map,team-charter}.md` 与定稿口径一致；`git check-ignore -v` 全部命中 `.gitignore:47` |
| **校验脚本与 Markdown 格式门禁** | ✅ 通过 | `bash .team/notes/research/logs/task-18-verify.sh` 实跑 `PASS=67 FAIL=1`（`exit=0`，唯一 FAIL 为 `§X` 自证注入）；Python 独立核查 `docs/**/*.md` + `README.md`：**0 死链、0 裸竖线表格错列**（原 `BUSINESS-LOGIC:638` N2 与 `TEAM.md:441` ② 均已转义修复） |

---

## 三、阶段一收口建议（`task-32` 初版，**已于 `task-41` 全部验证清零**）

1. ~~**`tester`（修 P0-1）**~~ ✅ **已完成（`task-35`）**：`sla_policy` 键名已改 `statutory_deadline_hours` / `applies_to_priorities`（`:150,:212,:282,:1094`）；`grading_rules` 已改 4 条规则（`R1`–`R3` + 顶层 `default_priority=="P4"` / `default_rule_id` / 条件键 `"any"`），无资产已改 `null`，快照字段已改 `emergency_action_required`，`MUT-05b` 已对齐 `R1_SAFETY_OR_EXPRESS_CRITICAL`，`:151` 已改「6 个部分唯一索引」，`:282` 已改 `POST`/`PATCH /business-rules`。
2. ~~**`frontend-dev`（修 P0-2 + P2-4）**~~ ✅ **已完成（`task-36`）**：`:688-689`、`:928-929`、`:1301` 的 SLA 语义已改为 `response_deadline_at` 默认恒 `NULL` + `repair_deadline_at` 确认/建单即算 `T_discover + 48h`；`:42,:51,:1113` 已改 `typescript 6.0.3`；`:280` 已改「特急/加急/普通/观察」。
3. ~~**`docs-writer`（修 P1-1..P1-4 + P2-3）**~~ ✅ **已完成（`task-37`）**：`docs/API.md:191` 已清「阈值非递减」；`docs/DECISION.md:50,176,189,604` 已改实测基线；`docs/team/TEAM.md:502,515` 已改已完成口径；`docs/TECH-STACK.md:82,135,348` 已改定案状态；`README.md:25` 已改「128 个已冻结 `data-testid` 锚点（121 + 第 J 组 7 项，`D-04` 已定案）」；`task-18-verify.sh:277,340` 已同步（`:340` 已改「11 段多字原话（加 6 条单字 `A` 共 17 项）」）。
4. ~~**`git-keeper`（修 P1-3）**~~ ✅ **已完成（`task-38`）**：`.team/notes/git-keeper/COMMIT-PROTOCOL.md:186` 已改为「`D-08` ✅ 团队已工程定案…」并把 `pyright` 表述改为「`D-08` 已工程定案采用 `mypy`」。
5. ~~**`dev-ops`（修 P2-1/P2-2）**~~ ✅ **已完成（`task-39`）**：`.env.example:62` `Track A` 已改 `yolo26s.pt`；`docs/RUNBOOK.md:390,392`「冒烟推理」已改「推理验证」；`.team/notes/dev-ops/stage1-infra-facts.md:81` 的 `Track A` 亦已清除（`rg 'Track'` → 零命中）。
6. **`backend-dev`（修 P2-5）** ✅ **已完成（`task-40`）**：`stage1-backend-spec-package.md` 已改单包结构 + `sqlalchemy>=2.0.58,<2.1` + `docs/API.md §8.2/§8.3`；`stage1-backend-spec-api.md:181` 已改 `§8.2 / §8.3`。

---

## 四、`task-41` 收口复核（**结论：`0 P0 / 0 P1 / 0 P2`**，另报 2 项新发现）

### 4.1 12 项缺陷逐条实测复验（全部清零，无回归）

| 编号 | 原坐标 | 现状实测 | 结论 |
|---|---|---|---|
| **P0-1** | `tester:150,151,171,212,282,326,364,501,502,758,1094-1096` | `:150` 4 规则结构 + `emergency_action_required` + `statutory_deadline_hours`；`:151` 6 个部分唯一索引；`:171` 6 个部分唯一索引；`:212` `statutory_deadline_hours`/`response_minutes`/`emergency_notice_hours`/`applies_to_priorities`；`:282` `POST`+`PATCH`/4 规则/无资产 `null`/`emergency_action_required`；`:326` `emergency_action_required`；`:364` `POST`+`PATCH /business-rules`；`:501/:502` 4 规则 + `"any"` + `R1_SAFETY_OR_EXPRESS_CRITICAL`；`:758` 已同步校验口径；`:1094-1096` 三行终局核查记录（含 `task-35` 修复自述） | ✅ **清零** |
| **P0-2** | `frontend-dev:687-690,928-929,1301` | `:688` `response_deadline_at` 默认恒 `NULL`；`:689` `repair_deadline_at` 仅 `AI_CANDIDATE_PENDING` 期间 `null`、确认/建单即写 `T_discover + 48h`；`:928` 默认恒 `null`、与是否分级无关；`:929` 同上；`:1301` 变更记录已标注「P0-2 按 `D-11` 已更正」 | ✅ **清零** |
| **P1-1** | `docs/API.md:191` | 已改「`grading_rules` 缺 `default_priority=='P4'` / 条件枚举非法、`sla_policy.statutory_deadline_hours <= 0`、`dispatch_scoring_weights` 权重和 ≠ 1 等」；`rg '阈值非递减'` → 零命中 | ✅ **清零** |
| **P1-2** | `docs/DECISION.md:50` | 已改「已预装 `/root/.local/bin/uv` `0.13.7` 与 `/root/miniconda3/bin/conda` `24.4.0`（仅不在非交互 SSH 默认 `PATH`…**无需重装 `uv`**）」 | ✅ **清零** |
| **P1-3** | `docs/DECISION.md:176,189`；`docs/team/TEAM.md:502,515`；`COMMIT-PROTOCOL.md:186,198` | `DECISION:176,189` 与 `TEAM:502,515` 已同步完成态且改用「具体行数以该文件为准」（已于 `task-42` 移除 `393 行` 硬编码）；`COMMIT-PROTOCOL:186` = 「`D-08` ✅ 团队已工程定案」、`:198` = 「`D-08` 已工程定案采用 `mypy`」 | ✅ **清零** |
| **P1-4** | `docs/DECISION.md:604`；`docs/TECH-STACK.md:82,135,348` | `DECISION:604` 已改「第 5 项『派单权重』已同步澄清并定为 `dispatch_scoring_weights = (0.40, 0.35, 0.25)`」；`TECH-STACK:82` 已改「✅ 已裁定（`D-05`）」；`:135` 已改「**17（17.12）** ✅ 已定案」；`:348` 已改「✅ 历史记录已清账」 | ✅ **清零** |
| **P2-1** | `.env.example:62` | 已改「`1024` = this project's engineering initial value for **`yolo26s.pt`**」（`Track A` 已删） | ✅ **清零** |
| **P2-2** | `docs/RUNBOOK.md:390,392` | 已改「已知真值样例图**推理验证**返回 ≥1 个候选」「**推理验证**空结果」 | ✅ **清零** |
| **P2-3** | `README.md:25` | 已改「128 个**已冻结** `data-testid` 锚点（121 + 第 J 组 7 项，`D-04` 已定案）」 | ✅ **清零** |
| **P2-4** | `frontend-dev:42,51,280,1113` | `:42,:51` 已改 `6.0.3`（含 `docs/TECH-STACK.md:368` 交叉引用）、`:1113` `"typescript": "6.0.3"`；`:280` 已改「**「特急」/「加急」/「普通」/「观察」**」 | ✅ **清零** |
| **P2-5** | `backend-dev/...package.md` + `api.md:181` | `package.md:12-20` 已改单包（根 `pyproject.toml` 唯一、`backend/` 无独立包）；`:200` 已改 `sqlalchemy[asyncio]>=2.0.58,<2.1`；`:356` 已改 `docs/API.md §10.2`→ 现文件内 `:181` 已改 `§8.2 / §8.3` | ✅ **清零** |
| **P2-6** | `stage1-infra-facts.md:81`；`task-18-verify.sh:277,340` | `infra-facts` `rg 'Track'` → **零命中**；`verify.sh:340` 已改「**11 段**多字原话（加 6 条单字 `A` 共 17 项）」；`:277` 已于 `task-42` 收紧为 `grep -qF "25 行"`（去除 `393 行\|` OR 掩盖） | ✅ **清零** |

### 4.2 ~~新发现 `N-1`（P2 级，行数硬编码漂移）~~ → **已于 `task-42` 清零，详见 §五**

`task-41` 曾检出 `COMMIT-PROTOCOL.md` 实测 `392` 行而 `docs/DECISION.md:176,189`、`docs/team/TEAM.md:502` 硬编码 `393 行` 且 `task-18-verify.sh:277` 使用 `OR` 表达式的问题；现已由 `docs-writer`（`task-42`）全部清除（见 §五复核）。

### 4.3 新发现 `N-2`（P3 级，历史口径，**不属缺陷**）

`docs/DECISION.md:599,603,607,619` 写「`RUNBOOK §6.2` **10 行** GPU 参数」。实测：`HEAD:docs/RUNBOOK.md` §6.2 恰为 **10** 个数据行（`git show HEAD:docs/RUNBOOK.md` 统计），**该表述在写作时点完全正确**；`dev-ops` `task-27` 重建 §6.2 为「已实测确认」表后现为 **13** 行（SSH 4 + 硬件 2 + 存储 4 + 网络 3）。

**判定**：`DECISION.md` 该句历史语义为「`task-18` 收拢时点 §6.2 的 10 行参数已全部并入 `D-01`」—— 是**历史归因陈述**，非「现行 §6.2 有 10 行」的现状断言。因此**不计为缺陷**，仅在报告中登记口径变化，避免后续读者误判。

### 4.4 复跑全套独立验证（`task-41` 实测）

| 验证项 | 命令 | 实测结果 |
|---|---|---|
| 门禁脚本 | `bash .team/notes/research/logs/task-18-verify.sh` | **`PASS=67 FAIL=1`，`exit=0`**；唯一 `FAIL` 为 `:91` `[自证] 这个断言必须失败`（`§X` 故意注入），**自证有效** |
| `DECISION.md` 计数 | Python 独立解析 | 分档 `{1:9, 2:8, 3:14, 4:9}` = **40**；状态 `{'✅':18,'🔴':1,'🟡':1,'🟢':20}` = **40**；标题/状态不一致 **0**、缺号 **0**、重号 **0** |
| 用户原话保全 | Python 逐块精确行匹配 | **17/17**（11 段多字 + 6 条单字 `A`）全部命中 |
| `grading_rules` 144 组合 | Python 笛卡尔积 | `R1=94, R2=34, R3=14, R4=2`，**和 = 144**，0 死规则 |
| Markdown 死链 | Python 解析 `docs/**/*.md` + `README.md` | **0 死链** |
| 表格裸竖线错列 | Python 逐行统计未转义竖线数量 | **0 错列** |
| Serena Memories | `git check-ignore -v` | 4 文件全部命中 **`.gitignore:47`** |
| 计数类声明自洽 | Python 复算 | `SCHEMA` `76/41/22/37` 逐项求和 **全部 OK**；`BUSINESS-LOGIC` `A15+B6+C8+D7=36` OK；`API` 34 个 `error.code` OK；`tester` `MUT` 27 数据行 / 27 个唯一 ID OK |
| 残留模式扫描 | 24 组正则（`maintenance_deadline_minutes`/`deadline_applies_to_priorities`/`emergency_handling_required`/`R1_SAFETY_OR_HAZARD`/`any_of`/`all_of`/`is_default`/`取 NONE`/`8 条有序规则`/`3 个部分唯一索引`/`PUT /api/v1/business-rules`/`阈值非递减`/`16 或 17`/`5.9.3`/`灰区待裁决`/`10485760`/`PAYLOAD_TOO_LARGE` 等） | **全部 0 命中** |
| `IMAGE_TOO_LARGE` 2 命中 | 上下文核对 | 均为 `docs/API.md:238,394` 的**「绝不存在的错误码」纠正表**（正确用法，非残留） |
| `yolo26n/m/l/x` 命中 | 上下文核对 | 全部为**官方出处列举**或**「不使用」声明**（`TECH-STACK:340,585,592`、`SCHEMA:418-419`、`RUNBOOK:155,331-332,343`、`DECISION:651` 历史变更记录），**无现行选型冲突** |
| `10 MiB` 4 命中 | 上下文核对 | 全部为**废除说明/裁定问题陈述**（`.env.example:42` 注释理由、`DECISION:66,70` 的 `D-02` 问题与备选、`RUNBOOK:63` 说明「10 MiB 会误拒合规原图」），**非现行默认值** |

### 4.5 `task-41` 阶段结论

见下 **§五（`task-43` 终版证明收口）**。

---

## 五、`task-43` 终版证明收口（`N-1` 清零复核与最终全绿证词）

### 5.1 `N-1` 逐点实测清零（`.team/notes/auditor/logs/task43/n1-recheck.log`）

| 坐标 | 复核实测内容 | 结论 |
|---|---|---|
| `docs/DECISION.md:176` | 已移除 `393 行`，改为「`25 行` 命中（已含 **§1.4 分支拓扑与 REBASE-only 策略**与硬规则 **`R16 REBASE-only`**，具体总行数以 `.team/notes/git-keeper/COMMIT-PROTOCOL.md` 为准…）」 | ✅ **清零** |
| `docs/DECISION.md:189` | 已移除 `393 行`，改为「…`R16 REBASE-only`（**25 行**命中，具体总行数以该文件为准）」 | ✅ **清零** |
| `docs/team/TEAM.md:502` | 已移除 `393 行`，改为「…`rg -ni '分支\|rebase\|merge\|冲突'` 命中 **25 行**，具体行数以 `.team/notes/git-keeper/COMMIT-PROTOCOL.md` 为准」 | ✅ **清零** |
| `.team/notes/research/logs/task-18-verify.sh:277` | 已从 `grep -qE "393 行\|25 行"` 收紧为 `chk 'D-07 已有可复现出处（F14）' 'grep -qF "COMMIT-PROTOCOL.md" docs/DECISION.md && grep -qF "25 行" docs/DECISION.md'`（取消 OR 掩盖） | ✅ **清零** |
| `COMMIT-PROTOCOL.md` 实测对照 | 四法（`wc -l`/`splitlines`/`awk`/`grep`）一致为 **`392`** 行；`rg -cni '分支\|rebase\|merge\|冲突'` = **`25`** 行；全仓 `docs/`、`README.md`、`task-18-verify.sh` 搜索 `393` → **0 命中** | ✅ **全清** |

### 5.2 终版全套复跑实测（`.team/notes/auditor/logs/task43/`）

- `bash .team/notes/research/logs/task-18-verify.sh` → **`PASS=67 FAIL=1`，`exit=0`**；唯一 `FAIL` 为 `:91` `[自证] 这个断言必须失败`（`§X` 故意注入），自证有效。
- Python 独立复算：`DECISION.md` **40 条** / 分档 **`{1:9, 2:8, 3:14, 4:9}`** / 状态 **`18✅ / 1🔴 / 1🟡 / 20🟢`** / 标题与状态不一致 **0**；用户原话 **17/17** 逐块整行命中；`docs/**/*.md` + `README.md` **0 死链**、**0 表格错列**。

### 5.3 最终收口证明

**`0 P0 / 0 P1 / 0 P2`** —— 阶段一原 12 项缺陷及 `N-1` 行数硬编码漂移**全部实测清零，无回归、无遗留阻断项**（仅 `N-2` 为历史归因口径说明，非缺陷）。

- **可正式冻结**：`docs/` 正式设计文档、`.env.example`、`README.md`、`.team/notes/{backend-dev,frontend-dev,vision-dev,tester,git-keeper,dev-ops}/` 及 `.serena/memories/tomori/` 已达到契约与语义层面的完全一致性。
- **诚实边界（未变）**：阶段一**零行应用代码、零迁移、零测试被执行**；`backend/`、`web/`、`tests/`、`migrations/` **均不存在**；`vision/` 8 个 `.py` 为未运行验证草稿；27 行（30 实验）变异卡全部未执行；本证明全部结论为**文档与素材层的静态一致性**，不含任何运行时证据。
