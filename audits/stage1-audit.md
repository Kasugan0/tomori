> 状态：阶段一独立只读审计报告 —— 提交 `Lead` 复核
> **审计员**：`auditor`（只读，未修改任何被审文件）
> **任务**：`task-8`（`status: in_progress`）
> **审计对象**：`docs/*.md` 7 份（`REQUIREMENTS` / `SCHEMA-DESIGN` / `BUSINESS-LOGIC` / `API-CONTRACT` / `TECH-STACK` / `TEAM-CHARTER` / `RUNBOOK`）+ 基础设施（`compose.yaml` / `.env.example` / `.gitignore` / `deploy/` / `pyproject.toml` / `uv.lock`）+ 上游 8 份素材
> **写入边界**：仅 `.team/notes/auditor/` 与 `audits/`；零 edit 被审文件；零 git 写命令
> **审计口径**：`docs/team/TEAM.md` §4.6 分层口径 + §3.2 坐标四级判据 + §3.1 形态 F 边界诚实

---

## 0. 结论摘要

| 判定 | 数量 | 含义 |
|---|---:|---|
| 🔴 P0 阻断 | **6** | 数字/断言与实测不符，或引用不存在的出处。**须 Lead 复核后裁决是否放行 `git-keeper` 提交** |
| 🟡 P1 缺陷 | **4** | 内部自相矛盾或前提已失效，不阻断提交但必须在阶段二开始前修 |
| 🟢 独立复核通过 | **9 组** | 我逐条重算/逐行重读，结论成立，可写入「已证实」 |

**最重要的一句**：**`docs/` 的绝大多数「硬数字」经得起独立复算**（17 表约束计数、121 冻结 `data-testid`、12 幂等端点、8 个 Element Plus 源码坐标、8 条上游禁用串），**但顶层 4 个「头条数字」中有 3 个无法由其自身文档内容复算出来**——端点 45、状态机边数 32/36、终态 10。这三者恰好都是 `task-7` 验收判据里被点名要对的数字。

---

## 1. 🔴 P0-1：REST 端点「45」无任何口径支撑（五算皆非 45）

### ① 原始输出
```
$ python3 .team/notes/auditor/logs/endpoint-count.log
口径 1  表格行数                = 57
口径 2  各小节标题声明之和        = 12+12+14+8+9 = 55
口径 3  (method,path) 展开       = 61
口径 4  distinct 原始路径         = 50
口径 5  (method, {id}归一化路径)  = 61
口径 6  distinct 归一化路径       = 50
声明值 45 —— 匹配口径: 无任何口径等于 45

按方法: {'GET': 24, 'POST': 28, 'PATCH': 8, 'DELETE': 1}

§2.5 标题声明 9 vs 实际行数: 11
```

逐小节（`logs/endpoint-subsections.log`）：
```
小节         标题声明     实际行    差
 2.1         12      12   +0
 2.2         12      12   +0
 2.3         14      14   +0
 2.4          8       8   +0
 2.5          9      11   +2  <<<
合计           55      57   +2
```

### ② 数字 + 测量命令
- **分子**：`docs/API-CONTRACT.md:68` 起 §2 表格内形如 `| \`GET\` | \`/x\` |` 的行 → **57 行**
- **分母**：`docs/API-CONTRACT.md` §2 全部路由行（`### 2.1` 至 `## 3.` 之间的表格行）
- **排除项**：`/health` 与静态资源挂载。**实测大幅表内 `/health` 出现次数 = 0**（`§0 :16` 声明排除它，但它根本不在表内，故该排除项对本计数无影响——排除项写了但对象不存在）
- 测量命令：`python3` 解析 6 种口径，落盘 `.team/notes/auditor/logs/endpoint-count.log`

**补充：我穷举了 16 种可能的排除假设，无一得 45**（落盘于本节命令）：
```
总展开 = 61
  仅 POST                            =  28
  仅读(GET)                          =  24
  排除 /sla/scan                     =  60
  排除 /audit-logs                   =  60
  排除 /notifications*               =  56
  排除 auth                          =  57
  排除 内部运维(sla+audit+notif)       =  54
  排除 内部运维+health                =  54
  仅 2.1+2.2+2.3                     =  39
  无路径参数的行(展开)                =  30
  collection GET only                =  17
  distinct path 无参数                =  22
  distinct path 全部                  =  50
  POST+DELETE+PATCH                  =  37
  POST+PATCH                         =  36
  GET+PATCH+DELETE                   =  33
结论：61 / 57 / 55 / 50 / 45 五值中，只有 45 无任何自然口径对应。
```

### ③ 边界声明
**决定性论证：文档自己写的分母就是 57。** `§0 :16` 该行的三列原文是：

```
| REST 端点数 | **45**（见 §2 逐条） | 本文件 §2 全部路由行 | `/health` 与静态资源挂载不计入业务端点 |
                ↑ 分子=45              ↑ 分母=「§2 全部路由行」
```

即：文档声明「**分子 45 / 分母 = §2 全部路由行**」，而 §2 全部路由行实测 **57**。按它**自己写的分母**，分子应为 57。这不是「口径缺失」，而是**分子与自报分母不符**——比原判断更硬。

- **已证实**：声明值 **45** 不等于该文档任何一种自然计数口径（行数 57 / 标题和 55 / 展开 61 / 路径 50），且在 16 种排除假设下仍无解。
- **已证实**：`§0 :16` 的**分母列**（「本文件 §2 全部路由行」）与**分子列**（45）自相矛盾——分母实测 57。
- **已证实**：`§2.5` 标题写「9 个」但表内 **11 行**（`/notifications/dead-letters`、`POST /sla/scan` 等在内），标题与内容差 2。
- **已证实**：上游 `.team/notes/backend-dev/stage1-backend-spec-api.md` 全文 **零命中 `45`**（`rg -n "\b45\b"` 无输出）→ **该数字不是从上游抄来的，来源不明**。
- **未证实 / 不适用**：不排除存在一种我没试过的口径（例如「按权限组合去重后」「排除内部运维端点后」）。但**文档未声明该口径**，而 `§0 :16` 自己给的排除项只有 `/health` 与静态资源——这两项都不改变 57。
- **失效条件**：若 Lead 能指出 45 的具体构成清单（哪 12 个被排除了），本项降级为「口径未写明」。

**`【需独立核实（由 Lead 复核）】`** —— 建议裁决：**改为 57**（表格行数，唯一自洽且可被 `tester` 直接数出来的口径），或补上 45 的完整排除清单。

---

## 2. 🔴 P0-2：状态机「终态 10 个」与转移表实测矛盾（实测 11 个）

### ① 原始输出
```
$ python3 .team/notes/auditor/logs/terminal-states.log
### 各机状态集（由转移表 from/to 反推）
  A: 9 个  B: 7 个  C: 8 个  D: 5 个          （合计 29 —— 与 §0 L16 一致 ✅）

### 每个状态出边数（0 = 汇点/终态）
  机A: 汇点 = ['CANCELLED', 'CLOSED']
  机B: 汇点 = ['CANCELLED', 'COMPLETED', 'ESCALATED', 'REJECTED', 'TIMEOUT_REASSIGNED']
  机C: 汇点 = ['CANCELLED', 'CLOSED_BY_REVIEW', 'PASSED']
  机D: 汇点 = ['SENT']

### 文档声明：终态 10 个 = 票 2 + 派工 5 + 返修 3；「通知机无终态」
### 实测汇点总数 = 11  vs 声明 10 -> 不一致
```

### ② 数字 + 测量命令
- **分子**：4 套状态机转移表中「出边数 = 0」的状态 → **11 个**
- **分母**：29 个状态全体
- **排除项**：无（`§0 :18` 排除项写「通知机无终态」）
- 命令：解析 `BUSINESS-LOGIC.md` §1–§4 全部 `| **X**n | from → to |` 行，求每个 `from` 状态的出边数

### ③ 边界声明
- **已证实**：`docs/BUSINESS-LOGIC.md:18` 声明「终态数量 **10**」，排除项理由为「通知机无终态（`SENT` 可因死信重试复位，非终态）」。
- **已证实**：`SENT` 在转移表中**零出边**（仅 D3 `PROCESSING → SENT`、D8 `DEAD_LETTER → SENT` 入边）。按文档自己对 A/B/C 三机使用的「出边 = 0 即终态」判据（`§1.3`、`§2.2`、`§3.3` 均按此判），`SENT` 就是**吸收态/终态**。
- **已证实该理由是混淆的**：「`SENT` 可因死信重试复位」描述的是 `DEAD_LETTER → PENDING`（D7），**与 `SENT` 的出边无关**；`SENT` 一旦到达就再不离开。理由与事实不对应。
- **已证实**：`§13 :569` 声称「0 死路（**逐状态穷举证明见 §1.3/§2.2/§3.3**）」——**故意不含 §4**。机器 D 没有「无死路证明」小节（A/B/C 都有）。故「0 死路」这一结论**对机器 D 无穷举证明支撑**。
- **未证实**：`SENT` 作为吸收态并不构成「死路」（它是成功终点，不是卡住），所以「0 死路」结论**很可能仍然成立**；错的只是**终态计数 10 → 应 11**。

**`【需独立核实（由 Lead 复核）】`** —— 建议裁决：终态改为 **11**，`§0 :18` 排除项改为「无」，并在 §4 补一小节「4.2 无死路证明（含 `SENT` 为吸收终态的说明）」；`§13 :569` 的证明指引补上 §4.2。

---

## 3. 🔴 P0-3：状态机边数「36」与「32」并存，且「去重规则」全库无定义

### ① 原始输出
```
$ python3 .team/notes/auditor/logs/statemachine-count.log
机     编号行    声明行    入口行    合法行    实际边
 A     17     17      2     15     18
 B      7      7      1      6      8
 C     10     10      2      8     10
 D      8      8      1      7      8
合计 编号行 42 / 声明的入口行 6 / 声明的合法边 36 / 实际扇出后合法边 38

扇出行（from/to 多值 => 一行展开成多条边）：
   A15: 源1 × 目标2 = 2 条  |  `MANUAL_REVIEW` → `PENDING_DISPATCH` / `REWORK_PENDING_DISPATCH`
   B7: 源2 × 目标1 = 2 条  |  `PENDING_ACCEPT`/`ACCEPTED` → `CANCELLED`

扇出后边总数 = 44
全局 distinct (from,to) 去重 = 40
```

### ② 数字 + 测量命令
三种口径并存：
| 口径 | 值 | 出处 |
|---|---:|---|
| 编号行总数 − 入口行 | 42 − 6 = **36** | `BUSINESS-LOGIC.md:17`（附完整算式） |
| 「去重后」 | **32** | `BUSINESS-LOGIC.md:569` 与 `:591` |
| 扇出展开后的真实边 | **44**（跨机）/ **40**（全局 (from,to) 去重） | 本次实测 |

### ③ 边界声明
- **已证实**：`:17` 的 36 **完全自洽**——`A 15 + B 6 + C 8 + D 7 = 36` 成立，且 `42 − 6 = 36` 的算式与编号行实测（17+7+10+8 = 42）一致，六个入口行 `A1/A2/B1/C1/C2/D1` 也确实是「（无）→ 初态」。
- **已证实**：`:569` 与 `:591` 的 **32** 与 36 直接冲突，且 **`rg 去重 docs/BUSINESS-LOGIC.md` 仅命中 `:569` 一处**——全文没有任何地方定义「去重」按什么规则去。
- **已证实**：36 也不是「扇出后的真实边数」（44），也不是「distinct (from,to)」（40）。**36 是「编号行」口径**，而 32 在任何口径下都算不出来（36−4=32 找不到对应的 4 条边）。
- **未证实**：无法排除 `32` 是某次早期草稿的残留数字。
- **上游佐证范围有限**：`.team/notes/backend-dev/stage1-backend-spec-state-machines.md:34` 只佐证了 A 机的 15。

**`【需独立核实（由 Lead 复核）】`** —— 建议裁决：**统一为 36**（唯一有算式、有编号行支撑的口径），删掉 `:569`/`:591` 的 32；若要保留「真实可执行边」概念，须显式定义扇出规则并给 44。

---

## 4. 🔴 P0-4：`§8.3` 标题「128 个」与分组实测 121 矛盾

### ① 原始输出
```
$ python3 .team/notes/auditor/logs/testid-count.log
组        实测    §8.3声明    差
 A        7         7   +0
 B       17        17   +0
 C        5         5   +0
 D       26        26   +0
 E       12        12   +0
 F        8         8   +0
 G       24        24   +0
 H       19        19   +0
 I        3         3   +0
合计      121       121   +0

全局 distinct = 121 / 总条目 = 121
重复名 = []
```

### ② 数字 + 测量命令
- **分子**：`docs/API-CONTRACT.md` §8.3 表 A–I 九组数量之和 → **121**
- **分母**：`§10.2` 冻结清单去重后的 `data-testid` 名字
- **排除项**：**7 个路由表独有的灰区锚点**（`dashboard-summary-card-${key}`、`region-row-*`、`point-row-*`、`asset-row-*`、`rule-row-*`、`scope-row-*`、`account-row-*`）——`§8.2 :431` 明确它们「未进冻结表」
- 交叉验证：直接解析上游 `.team/notes/frontend-dev/stage1-frontend-spec-input.md` §10.2（L835–985，151 行），**逐行提取反引号名字并按组计数，得 121，零重复**（`A7/B17/C5/D26/E12/F8/G24/H19/I3`，与 §8.3 九组数字**逐组精确一致**）

### ③ 边界声明
- **已证实（高置信）**：**121 这个数字是对的**，两个独立来源（API-CONTRACT §8.3 九组；frontend-dev §10.2 原始清单）逐组对上，且全局零重复。
- **已证实**：`§8.3 :438` 标题写「分组清单（**128** 个，按职责）」——**标题错**。128 = 121（已冻结）+ 7（`§8.2 :431` 明确「未进冻结表」的灰区）。把未冻结的 7 个算进「按职责分组清单」，与 `§8.2` 的三层口径直接冲突。
- **已证实**：`§8.2 :434-436` 自己也写明「**这 7 个等于未冻结**」。
- **注意（不是缺陷）**：`§0 :19` 的「128」**是对的**——它指的是「并集」（121 + 7），`§8.2` 三层表已分层写清。错的只有 `§8.3` 的标题。
- **未证实 / 不适用**：无。

**`【需独立核实（由 Lead 复核）】`** —— 建议裁决：`§8.3` 标题改为「分组清单（**121** 个已冻结，按职责）」，并在表下加一句「另有 7 个灰区锚点未冻结，见 §8.2」。

---

## 5. 🔴 P0-5：「五方一致」引用了不存在的出处（F14 无出处断言）

### ① 原始输出
```
$ rg -n "五方一致" docs/team/TEAM.md
  ZERO HIT in docs/team/TEAM.md
搜索模式: 五方一致  范围: docs/team/TEAM.md（全文 749 行）

$ rg -n "^## 5\.|^### 5\." docs/team/TEAM.md
651:## 5.1 六起 git 层事故
655:## 5.2 静默失败的形态
672:## 5.3 Lead 的两次错误
683:## 5.4 git-keeper 的两次错误
692:## 5.5 docs-writer 的错误
704:## 5.6 E2E 里手工种账务数据的三条独立代价（2026-11-14）
```

### ② 数字 + 测量命令
- `rg -n "五方一致" docs/` → **唯一命中在 `docs/TECH-STACK.md:436`**，即引用者自己。
- `docs/team/TEAM.md` 全文 749 行 → **零命中**。

### ③ 边界声明
- **已证实**：`docs/TECH-STACK.md:436` 写「**`docs/team/TEAM.md` §5 要求的「五方一致」**：`docs/TECH-STACK.md` / `docs/RUNBOOK.md` / `compose.yaml` / `.env.example` / `deploy/stack-guard.sh`」。
- **已证实**：`docs/team/TEAM.md` **既没有「五方一致」这个说法，其 §5 也不讲端口一致性**（§5.1–§5.6 全是历史事故复盘：git 层事故、静默失败、Lead 的错误等）。
- **这正是 `TEAM-CHARTER.md:391` 与 `LEAD.md` §4.1 自己明令禁止的形态**：「F14 断言必须带出处……没有代码坐标、官方文档 URL 或可复现命令，不得作为决策依据」。
- **未证实**：「五方一致」这个**要求本身是否合理**——我认为**要求是好的**（端口确实必须五方一致）。错的只是**归因**：把一条合理的工程要求挂在了一个不存在的出处处。
- **不影响**：端口值本身五方实测**完全一致**（见 §9-D），所以这条是「归因缺陷」不是「事实缺陷」。

**`【需独立核实（由 Lead 复核）】`** —— 建议裁决：把「`TEAM.md` §5 要求」改为「本项目自定的五方一致要求」（`docs-writer` 自主提出），或由 Lead 正式裁决后写入 `TEAM.md`。**不要**因为归因错就删掉这条要求。

---

## 6. 🔴 P0-6：`TEAM.md` 的 E2E 端口（18080/18081/19090/19092）与本项目实际端口冲突

### ① 原始输出
```
$ rg -n "18080|18081|19090|19092" docs/
docs/team/TEAM.md:64:| 5 | 端口 18099/18100 | **18080/18081** | 凭记忆 |
docs/team/TEAM.md:249:- ...E2E 需要独占 18080/18081/19090/19092，我只在端口全空后跑
docs/team/TEAM.md:275:- 需要 **E2E 窗口**（18080/18081/19090/19092 独占，`claim-e2e` / `release-e2e`）
docs/team/LEAD.md:379:| 5 | 端口 18099/18100 | **18080/18081** | 凭记忆 |

$ # 本项目五方实际端口
docs/TECH-STACK.md           25432:1 18141:1 15173:1 18142:1 15174:1
docs/RUNBOOK.md              25432:10 18141:2 15173:2 18142:5 15174:5
compose.yaml                 25432:1 18141:0 15173:0 18142:0 15174:0
.env.example                 25432:5 18141:1 15173:2 18142:1 15174:1
deploy/stack-guard.sh        25432:1 18141:1 15173:1 18142:2 15174:2
```

### ② 数字 + 测量命令
- 项目实际 E2E 端口：`E2E_BACKEND_PORT=18142`、`E2E_FRONTEND_PORT=15174`（`.env.example:30-31`；`stack-guard.sh:32-33` 默认值同）
- `TEAM.md:249/275` 声明的 E2E 独占端口：`18080/18081/19090/19092`（**四个，且全部不同**）

### ③ 边界声明
- **已证实**：`docs/TEAM-CHARTER.md:9` 已明确写「**`docs/team/TEAM.md` 记录的是 `new-new-api` 项目的 8 人团队……两者不得混用**」。
- **已证实**：`TEAM.md:249/275` 的端口正是 `new-new-api` 项目的（`TEAM.md:64` 自己承认上一轮「端口 18099/18100 凭记忆」，修正为 18080/18081）。
- **已证实**：`docs/TECH-STACK.md:436` 又把「`TEAM.md` §5 要求」当成现行约束引用 → **两个错误相乘**：引用了不存在的章节（P0-5），并把外项目端口当成现行要求。
- **未证实 / 不适用**：这些端口**未被任何本项目配置文件使用**（`compose.yaml` / `.env.example` / `stack-guard.sh` 均零命中 18080 等），所以**当前无实际危害**，属「文档卫生 + 未来误读风险」。
- **真实风险**：阶段二若有人照 `TEAM.md` 去找 18080/18081 的独占窗口，会找错端口。

**`【需独立核实（由 Lead 复核）】`** —— 建议裁决：`TECH-STACK.md:436` 改为引用本项目真实端口表；并在 `TEAM-CHARTER.md` 增一句「`TEAM.md` 的端口/E2E 窗口数字不适用于本项目」。

---

## 7. 🟡 P1 缺陷（4 项）

### P1-1：错误码「33」存在 33 vs 34 的口径歧义

```
$ python3 .team/notes/auditor/logs/errorcode-sections.log
§3 各小节：标题声明 vs 实际行数
  3.1: 标题 9 / 实际 9
  3.2: 标题 12 / 实际 12
  3.3: 标题 6 / 实际 6
  3.4: 标题 6 / 实际 6
  标题声明之和 = 33  实际行之和 = 33
=> 表格 distinct code = 33；正文另有 VISION_INFERENCE_FAILED => 真实 unique code = 34
```

- **已证实**：`§3` 表格 33 行、33 个 distinct `code`、**零重复**（满足 `§0 :21` 唯一性规则）。
- **已证实**：`§3.4 :214` 脚注「另有 `503 VISION_INFERENCE_FAILED`（推理抛异常）→ 合计 **33** 个 `code`」——**脚注在表格之外又引入 1 个 code，却说「合计 33」**。若该码真实存在，合计应为 34；若它已含在表内，则脚注的「另有」措辞错。
- **已证实**：`§0 :17` 排除项写「**无**」。
- **建议**：把 `VISION_INFERENCE_FAILED` 正式放进 §3.4 表格（变 7 行）、总数改 34；或删脚注。**这是 `tester` 写 E2E 断言 `data-code` 的直接依据，必须唯一。**

### P1-2：YOLO26 命名禁令被自身文档与其他文档大面积违反

```
$ rg -n "yolo26|YOLO26|YOLO 系列" docs/ .env.example deploy/
.env.example:46:# --- Ultralytics YOLO26 Vision Inference ---
.env.example:50:YOLO_FALLBACK_MODEL=yolo26s.pt
docs/SCHEMA-DESIGN.md:416:...Fallback 模型（`yolo26s.pt` 通用模型）...
docs/RUNBOOK.md:154:Ultralytics YOLO26 目标检测/故障分类
docs/RUNBOOK.md:159:在启动远端 YOLO26 训练前...
docs/RUNBOOK.md:210:     model=yolo26n.pt \
docs/RUNBOOK.md:222:> **32G–48G 显存调参建议**（依据 Ultralytics YOLO26 官方训练指南）
docs/RUNBOOK.md:224:...优先升级到 `yolo26m.pt` / `yolo26l.pt`
docs/TECH-STACK.md:20:...视觉 YOLO 系列（Ultralytics）+ OpenCV
docs/TECH-STACK.md:301:> ⚠️ **YOLO26 措辞注意事项**：...**不得在文档中断言「本项目使用 YOLO26」而不给版本出处**（F14）
```

- **已证实**：`TECH-STACK.md:301` 明令「**不得在文档中断言「本项目使用 YOLO26」而不给版本出处**」，并把该项列为待用户裁决 `TS-2`（`:531`）。
- **已证实**：同一禁令被 **`RUNBOOK.md` 4 处、`.env.example` 2 处、`SCHEMA-DESIGN.md` 1 处**违反，且 `RUNBOOK.md:222` 还声称「依据 Ultralytics YOLO26 官方训练指南」——**这就是一个无出处的 F14 断言**。
- **注意**：`TECH-STACK.md:20` 自身是合规的（写「YOLO 系列」）。
- **附带矛盾**：`.env.example:50` 的 `YOLO_FALLBACK_MODEL=yolo26s.pt` 与 `RUNBOOK.md:210` 的 `model=yolo26n.pt` 是**不同 checkpoint**（`s` vs `n`），二者用途不同（fallback vs 训练起点）但文档未说明，易误读。
- **建议**：`RUNBOOK.md` / `.env.example` / `SCHEMA-DESIGN.md` 统一改为「Ultralytics YOLO 系列」，具体 checkpoint 标注「待 `TS-2` 裁决」；`.env.example` 的变量值可保留（配置必须有个具体值），但注释须说明「占位，待 `TS-2`」。

### P1-3：`AC-5` 的「前端拦 10 MiB」其事实前提已失效

```
$ rg -n "10 MiB|10MB|10 MB|10485760" .team/notes/frontend-dev/ docs/
team/notes/frontend-dev/stage1-frontend-spec-input.md:457:| `413` | ... **取两者更严者：10 MiB** ...
.team/notes/frontend-dev/stage1-frontend-spec-input.md:639:| 2 | ... **取更严者：≤ 10 MiB**，`vision-dev` 白名单为 15 MiB、`.env.example` `MAX_UPLOAD_SIZE_MB=10` ...
docs/API-CONTRACT.md:575:| **AC-5** | `413` 阈值：15 MiB vs 前端拦 10 MiB | ... **三方不一致，须统一** ...
$ rg -n "MAX_UPLOAD_SIZE_MB" .env.example
43:MAX_UPLOAD_SIZE_MB=15
```

- **已证实**：前端取 10 MiB 的**算法**是「取 `vision-dev` 白名单 15 MiB 与 `.env.example` 二者更严者」，而它引用的 `.env.example` 值是 **10**。
- **已证实**：`.env.example` **现值为 15**（`:43`），且 `:41-42` 已写明「**10 MiB rejected compliant 8-14 MiB phone originals**」的改值理由。
- **结论**：前端不是「固执写 10」，而是**依据的是一个已经过期的 `.env.example` 值**。按它自己的算法，现在应取 `min(15, 15) = 15`。
- **建议**：`AC-5` 从「待裁决」改为「**已可裁决**」：统一 15 MiB，前端改文案与 `before-upload` 阈值；`tester` 侧 `413` 断言亦按 `15,728,640` B。

### P1-4：根 `pyproject.toml` 与 `TECH-STACK` §1.5/§1.6 的依赖声明不一致

```
$ cat pyproject.toml
name = "tomori" / version = "0.1.0" / description = "Add your description here"
requires-python = ">=3.12"
dependencies = []            # ← 空

$ rg -c '^\[\[package\]\]' uv.lock
1                            # 只有 tomori 自身（virtual）

$ cat main.py
def main(): print("Hello from tomori!")
```

- **已证实**：根 `pyproject.toml` 是 `uv init` 的**原始脚手架**（`description = "Add your description here"`、`main.py` 是 hello world、`uv.lock` 只有 1 个 package）。
- **已证实**：`TECH-STACK.md` §1.5/§1.6 列了 `fastapi 0.165.0`、`sqlalchemy[asyncio]`、`psycopg[binary] 3.3.9`、`pydantic 2.14.4`、`pwdlib[argon2] 0.4.2`、`pyjwt 2.15.1`、`ultralytics 8.4.216`、`opencv-python-headless 5.1.0.95` 等约 20 个依赖。
- **已证实**：`TECH-STACK.md` **未声明这些依赖将落在根 `pyproject.toml` 还是 `backend/pyproject.toml`**。
- **边界**：这**不是错误**——阶段一按用户裁决「先定稿设计再编码」，不该装依赖。但**阶段二第一件事就会撞上它**，且 `TEAM-CHARTER.md:20/58` 把 `pyproject.toml` 划给 `backend-dev` 写入域，暗示是根文件 → 与 `uv init` 脚手架冲突（是改造还是另建子包？）。
- **建议**：`TECH-STACK.md` §1.5 补一句「依赖落在根 `pyproject.toml`（Phase 2 由 `backend-dev` 重写，替换 `uv init` 脚手架）」，把该假设显式化。

---

## 8. 🟢 独立复核通过（9 组，可写入「已证实」）

> 这一节的意义：区分「我没查」和「我查了且对」。以下每一项我都**独立重算或逐行重读**，不采信作者自述。

### A. 17 张业务表的 5 个约束计数（76 / 41 / 22 / 37 / 6）—— 全部正确
```
$ python3（独立重解析 SCHEMA-DESIGN.md:196-213 的 17 行）
CHECK=76  FK cols=41  UNIQUE=22  non-unique idx=37  partial unique=6
表数 = 17  distinct = 17（无重名）
17 表: accounts, roles, account_roles, scope_authorizations, business_rules,
        notification_tasks, audit_logs, maintenance_regions, lighting_points,
        facility_assets, fault_tickets, fault_evidences, dispatch_records,
        repair_processes, material_usages, acceptance_records, rework_orders
```
且文档自带的四行逐项求和（`:216-219`）**每行都复算正确**。**这是全文档质量最高的一块**：给出逐表明细 + 逐项求和算式 + 排除项声明，任何人可复现。

### B. `alembic_version` 分层口径 —— 正确且必要
`SCHEMA-DESIGN.md:17/25-37` 明确「17 张业务表，`alembic_version` 不计入」，并写明「若不排除，`information_schema.tables` 数出来是 **18**，这个 1 之差会被误判成多建/少写一张表」。**这是真正的假绿预防**。

### C. Element Plus `data-testid` 透传 8 个源码坐标 —— 全部逐行命中
我从 `raw.githubusercontent.com/element-plus/element-plus/2.14.8/` 独立取源码（落盘 `.team/notes/auditor/logs/ep-src/`，全部 HTTP 200），逐条核对 `TECH-STACK.md:356-382` 与 `API-CONTRACT.md:495/501` 引用的坐标：

| 坐标 | 断言 | 我的复核 |
|---|---|---|
| `input.vue:240` | `inheritAttrs: false` | ✅ 命中 |
| `input.vue:245` | `const rawAttrs = useRawAttrs()` | ✅ 命中 |
| `input.vue:270` | `const attrs = useAttrs()` | ✅ 命中 |
| `input.vue:36` / `:134` | `<input>` / `<textarea>` 分支 `v-bind="attrs"` | ✅ 两处命中 |
| `use-attrs/index.ts:12` | `DEFAULT_EXCLUDE_KEYS = ['class','style']` | ✅ 命中 → `data-testid` 被转发 |
| `picker.vue:8` | `v-bind="$attrs"` 到 `<el-tooltip>` | ✅ 命中（只有 `class`/`style` 存活） |
| `date-picker.tsx:57-79` | 渲染 `<CommonPicker {...props}>` | ✅ 命中；**注意它 spread 的是 `props` 不是 `$attrs`** → C 类结论成立 |
| `popper.vue` | `rg "attrs"` 零命中 | ✅ **复现零命中**（文件仅 53 行，`defineOptions({inheritAttrs:false})` + `<template><slot/></template>`） |

**结论：A 类（`el-input`/`el-checkbox`）安全、C 类（tooltip/popover/date-picker/time-picker/cascader）静默丢弃，两者的判据都是真的。** 这是本项目**做得最好的一次独立复核**——`API-CONTRACT.md:606` 说的「独立复核推翻 `el-input` 落根 div 的断言」确有其事。

### D. 运行时落点实测确有录 — 不是转述
```
.team/notes/research/logs/ep-runtime-testid-probe-script.log   (77 行，jsdom 探针脚本，21 个用例)
.team/notes/research/logs/ep-runtime-testid-probe.log          (5,193 B，JSON 落点结果)
.team/notes/research/logs/ep-runtime-datepicker-probe.log      (469 B，单独复验)
```
实测输出要点（我逐条读过）：`input` → `<input class="el-input__inner">`（matches=1）；`date-picker`/`time-picker`/`cascader`/`tooltip`/`popover` → **matches=0，landedOn=[]**；datepicker 单独复验 `nodesWithTestidAttr: []`（**属性彻底消失，不是选择器写错**）。
**脚本与输出双双落盘，证据链完整。** `API-CONTRACT.md:501` 引用的测度是真的。

### E. 121 个冻结 `data-testid` —— 逐组精确（见 P0-4 的测量）
两路独立计数（API-CONTRACT §8.3 九组 / frontend-dev §10.2 原始清单）**逐组一致，全局零重复**。

### F. 12 个必需幂等端点 —— 精确
`API-CONTRACT.md` §6.4 code fence 内含 **恰好 12 个** distinct `POST` token，与 `§0 :18` 声明一致。
（附注：`POST /tickets/{id}/evidences` 与 `POST /notifications/{id}/read|retry|acknowledge` **不在**这 12 个里。这是**设计选择而非缺陷**，但 E2E 剧本必须知道——重复上传同一张图不会触发 `IDEMPOTENCY_KEY_REQUIRED`。）

### G. 8 条上游禁用串 —— 全部只出现在反例表内，零污染
```
IMAGE_TOO_LARGE(2) / DISPATCH_TECHNICIAN_OUT_OF_SCOPE(1) / 409 INVALID_STATE_TRANSITION(1)
422 UNSUPPORTED_IMAGE_FORMAT(1) / 422 MEDIA_VIDEO_STREAM_FORBIDDEN(1)
pole_id(1) / sla_deadline(1) / max_rework_count(1)
→ 全部位于 docs/API-CONTRACT.md §3.6（"绝不存在的错误码"）或 §7.1（"措辞纪律"）的反例表内
```
`§3.6 :231-237` 与 `§7.1 :383-389` 的「负向清单」写法执行得非常干净——**没有一处泄漏到正文**。

### H. 8 条跨表不变量 I1–I8 + 3 个非 CRUD 机制 + 29 状态 —— 数量对
```
状态总数 = 9+7+8+5 = 29          ✅ 与 §0 :16 一致
不变量编号 I1–I8 = 8 个           ✅ 与 §0 :20 一致
机制章标题 = 机制一/二/三 共 3 个   ✅ 与 §0 :19 一致
```

### I. 端口五方一致性（端口值本身）—— 完全一致
`25432 / 18141 / 15173 / 18142 / 15174` 在 `TECH-STACK`、`RUNBOOK`、`compose.yaml`、`.env.example`、`stack-guard.sh` **五方全部一致**，且 `stack-guard.sh:24-33` 的默认值与 `.env.example` 逐项相同。**注意：这只能验第 1–2 级（值存在且相同）；「运行时真的这样绑定」需阶段二实跑，属未验证。**

### 附：`deploy/stack-guard.sh`（359 行，本次新读完）—— 质量高，无缺陷
- `set -euo pipefail`（`:2`）
- 严格限定 `tomori-postgres` 单容器（`:15`），`docker ps --filter "name=^/${CONTAINER_NAME}$"`（`:60`）**精确锚定，不会误伤他项目容器**
- `claim-e2e` 独占锁 `.team/runtime/e2e.lock`（`:13-14`），`release-e2e` 校验端口已停监听再释放（防僵尸进程污染下一轮）
- `cmd_up` 前置检查 `init-dbs.sql` 的 world-readable 位（`:90-94`）——因为容器内 `postgres` 是 uid 70；**这是真实的踩坑修复，不是想象的风险**
- `verify_databases` 用 `docker exec … pg_isready` + `psql -v ON_ERROR_STOP=1` 双验 `tomori_dev`/`tomori_test`（`:75-84`）
- **与 `RUNBOOK.md` §4 和 `TECH-STACK.md:446` 的 `claim-e2e` 语义描述完全对得上**

---

## 9. 硬红线（N1–N4 / V1–V3）交叉核对 —— 全部守住

| 红线 | 要求 | 实测 | 结论 |
|---|---|---|---|
| **N1** 不接灯控设备 | 无 SQLAlchemy/IoT/远程控制依赖与端点 | `pyproject.toml dependencies = []`（零依赖，必然合规）；`docs/` 与 `deploy/` **零命中** `mqtt`/`modbus`/`lamp.?control`/`/control`/`relay` | ✅ 守住 |
| **N2** 不做能耗预测 | 无时序/能耗预测库与表 | 17 表**无**能耗/预测表（见 §8-A 表名清单）；依赖清单无 `prophet`/`statsmodels`/`torch-time-series` 类 | ✅ 守住 |
| **N3** 不接实时监控 | 无 RTSP/流媒体依赖，且**主动 `415` 拒绝** | `rg "rtsp|RTSP|stream"` 仅命中 `§3.6` 反例表；`API-CONTRACT.md:236` 明确 `415 MEDIA_VIDEO_STREAM_FORBIDDEN`（原 `422` 已改正） | ✅ 守住，且是**主动拒绝**不是被动忽略 |
| **N4** 电气安全检查不得被 AI 替代 | `replaces_electrical_safety_check` 恒 `false` | `BUSINESS-LOGIC.md:202` I2 + `§9` 三项互斥 `503` + `SCHEMA-DESIGN.md` 该列 CHECK 恒 false | ✅ 守住 |
| **V1** 静态图校验 | magic-byte + 帧数 | `SCHEMA-DESIGN.md:409` `file_size_bytes > 0 AND <= 15728640`；`API-CONTRACT.md:272-290` 五步上传序含 magic-byte | ✅ 设计在位（未运行验证） |
| **V2** 巡查员确认三层 | `422 AI_CANDIDATE_NOT_CONFIRMED` + DB CHECK | `BUSINESS-LOGIC.md:44`（A6 守卫①）+ `ck_fault_tickets_inspector_confirmation_serialized` + `API-CONTRACT.md:182` | ✅ 三层齐全 |
| **V3** DB 排除 AI 不可产出类型 | CHECK 排除 `FLICKERING`/`ELECTRIC_HAZARD` | `SCHEMA-DESIGN.md` `ck_fault_tickets_ai_candidate_type_check` | ✅ 守住 |

**「只处理静态巡查图片」这条最硬的边界，四份文档口径一致，且 `§3.6` 用「绝不存在的错误码」把流媒体的口子从契约层焊死。**

---

## 10. 403 / 409 / 幂等 / 异步一致性 / GPU checklist 五项专项

| 专项 | 结论 | 关键证据 |
|---|---|---|
| **`403` 可测性** | ✅ **可测且口径清晰** | `BUSINESS-LOGIC.md:248-256` 用一张表把 `403`（**请求者自己**不合格：`FORBIDDEN_ROLE`/`FORBIDDEN_REGION_SCOPE`/`ACCOUNT_DISABLED`）与 `422`（**被指派对象**不合格：`DISPATCH_TECHNICIAN_NOT_ELIGIBLE`/`TECHNICIAN_OVERLOADED`）切开，并写明「🔴 这条必须写死，否则阶段二极易被统一成 403」。`API-CONTRACT.md:563` 进一步要求 `403` 用例**同时断言四张相关表行数未变**——这才测得出「先写后拒」的脏写。**这是全文档最强的可测性设计之一。** |
| **`409` 可测性** | ✅ 可测 | `409 DUPLICATE_ACTIVE_TICKET` 带 `details.existing_ticket_id/no/status`（`API-CONTRACT.md:179`）；`409 IDEMPOTENCY_REQUEST_IN_PROGRESS`（`:180`）。两者语义不重（一为业务防重、一为幂等并发），`§4 :243-266` 明确 `details` 不属于契约但**此两者例外** |
| **`Idempotency-Key`** | ✅ 规则完整 | `§6.1 :321-328` 引 IETF `draft-ietf-httpapi-idempotency-key-header-08`；`§6.2 :331-337` 五条规则；`§6.3 :341-353` 项目扩展 `X-Idempotent-Replay` + `uq_audit_logs_idempotency WHERE response_status BETWEEN 200 AND 299`（**只缓存 2xx，4xx/5xx 不缓存**——这是正确的，失败不幂等） |
| **`postgresql+psycopg://` 异步一致性** | ✅ 三方一致 | `compose.yaml` 用 PG 17.12 镜像、健康检查 `pg_isready`；`.env.example:19-21` 三条 URL **全部** `postgresql+psycopg://…@127.0.0.1:25432/…`（`DATABASE_URL`/`TEST_DATABASE_URL`/`E2E_DATABASE_URL`）；`TECH-STACK.md:20` 写 `SQLAlchemy 2.0 Async + psycopg v3`。**驱动名 + 异步 + 端口三处无矛盾** |
| **远端 GPU 32G–48G onboarding checklist** | 🟡 **基本完整，见下** | 见 §11 |

---

## 11. GPU onboarding checklist 完整性（`RUNBOOK.md` §6.3–6.6）

**已覆盖（我逐条核过）**：
1. SSH 别名 `ssh tomori-gpu`、工作目录 `/workspace/tomori-vision`（`:159` 起的 6 参数收集表）
2. Python 环境 `uv venv --python 3.12`
3. 代码同步 `rsync`
4. 长任务保活 `tmux`
5. 训练命令 `yolo train model=yolo26n.pt epochs=120 imgsz=1024 batch=32 amp=True`（`:210`）
6. `imgsz` **四方一致**硬门禁（`:221`）
7. 32G / 48G 分档 batch 建议 + `batch=-1` AutoBatch（`:222-226`）
8. 权重回传 `sha256sum` + `SHA256SUMS` → `./vision/weights/best.pt`（§6.5）
9. 启用门禁：`imgsz` hard gate + `strict_custom` 4 项（含**反向测试**）+ `auto` 模式的诚实披露（`ai_is_baseline_mode=true` / `inference_mode=baseline_pretrained_opencv` / `CHECK (ai_is_baseline_mode = FALSE OR ai_max_confidence <= 0.65)`）

**缺口（🟡，不阻断但建议补）**：
- **缺显存不足（OOM）的降级阶梯**：给了 32G/48G 的初始 batch 建议，但未写「OOM 时按什么顺序降」（先降 `batch` 还是先降 `imgsz`？`imgsz` 是硬门禁不能随便降 → 应明确「只降 `batch`，`imgsz` 不动」）。
- **缺训练中断续跑**：`yolo train resume` 未提及，而 120 epochs 大概率会中断。
- **缺数据集上传的完整性校验**：`rsync` 上传 8,428 张图后如何确认无缺失（只有权重回传有 `sha256sum`，**数据上行没有对称校验**）。
- **`model=yolo26n.pt` 与 `.env.example` 的 `YOLO_FALLBACK_MODEL=yolo26s.pt` 是不同 checkpoint**，未说明为何训练起点用 `n`、fallback 用 `s`。

---

## 12. 待用户裁决清单汇总（5 组共 21 项，我逐组核过）

| 组 | 出处 | 项数 | 我的核查结论 |
|---|---|---:|---|
| `AC-1..AC-5` | `API-CONTRACT.md:571-575` | 5 | `AC-5` **前提已失效可立即裁决**（P1-3）；`AC-1` 的 7 个灰区**确认真实存在且确实未冻结**（我逐名验证：7 个名字在 §10.2 **零命中**、在路由表区各命中 1 次）；`AC-2` `el-option` 确实无出处，标「阶段二首验」是诚实的 |
| `SD-1..SD-4` | `SCHEMA-DESIGN.md` §10 | 4 | 均为设计取舍，无实测可推翻 |
| `BL-1..BL-5` | `BUSINESS-LOGIC.md:542-548` | 5 | 均为业务语义取舍，需用户/Lead 裁决 |
| `TC-1..TC-4` | `TEAM-CHARTER.md` §8 | 4 | 纪律层裁决 |
| `TS-1..TS-3` | `TECH-STACK.md:531` | 3 | `TS-2`（YOLO26 含义）是 P1-2 的根源，**优先级最高** |
| **合计** | | **21** | |

**21 项待裁决本身不是缺陷**——`docs/` 每份的 §「诚实边界」都主动列出了「没有确定的」，这是**加分项**（`REQUIREMENTS.md:342-348`、`SCHEMA-DESIGN.md:583-592`、`BUSINESS-LOGIC.md:566-578`、`API-CONTRACT.md:587-591`、`TECH-STACK.md:549-557`、`TEAM-CHARTER.md:395-399` 都有）。

---

## 13. 我会给 `git-keeper` 的提交建议

**不建议因 P0 而阻塞全部提交。** 理由：6 项 P0 中，**4 项是「头条数字算错」（P0-1/2/3/4），2 项是「出处引用错」（P0-5/6）**——都不影响文档的**设计实质**（17 表结构、状态机转移表、错误码映射、API 契约、Element Plus 判据全部经得起复核）。**阻塞提交的代价（阶段二无法开始）大于收益。**

**建议路径**：`Lead` 复核 P0-1..P0-6 后，指定 `docs-writer` 做一轮**纯数字修正提交**（不动设计内容），再放行 `git-keeper`。若时间紧，可先提交、在 commit message 或 `docs/` 顶部标注「已知 6 项 P0 数字待修」。

**P0 修正清单（给 `docs-writer` 的最小改动集）**：
1. `API-CONTRACT.md:16, 68, 582, 606`：45 → **57**（或补 45 的完整排除清单）；`§2.5` 标题 9 → **11**
2. `BUSINESS-LOGIC.md:18`：终态 10 → **11**，排除项改「无」；`§4` 补「4.2 无死路证明」；`§13 :569` 证明指引补 §4.2
3. `BUSINESS-LOGIC.md:569, 591`：32 → **36**（删掉无定义的「去重后」）
4. `API-CONTRACT.md:438`：`§8.3` 标题 128 → **121**（并加注「另 7 个灰区未冻结」）
5. `TECH-STACK.md:436`：「`TEAM.md` §5 要求」→ 改为本项目自定要求（或由 Lead 裁决后写入 `TEAM.md`）
6. `TECH-STACK.md:436` 引用的 `TEAM.md` 端口：删除或注明「不适用于本项目」

**P1 可并入同一轮**：`§3.4` 错误码 33/34 归一；YOLO26 措辞统一；`AC-5` 改判 15 MiB；`TECH-STACK` 补 `pyproject.toml` 落点声明。

---

## 14. 本文档的诚实边界（`docs/team/LEAD.md` §4.2 零命中声明格式）

**已证实（附证据等级）**：
- 🟢 **真实运行日志级**：`ep-runtime-testid-probe.log` 的 21 条 DOM 落点实测（我读了原始 JSON）；`docker images postgres` 实测本机已有 `postgres:17.12-alpine` 且 digest **恰为** `sha256:526f86d9…d3d84f32`，与 `compose.yaml:8` 完全一致。
- 🟢 **变异→复现级**：`popper.vue` 的 `rg "attrs"` 零命中我独立复现；8 个源码坐标全部独立 fetch 后逐行命中。
- 🟢 **静态枚举 + 独立重算级**：17 表 5 个计数、121 testid、12 幂等端点、57 端点行、11 汇点、29 状态、8 不变量、4 条 P0 算术矛盾。
- 🟢 **注册表实测级**：`@volar/typescript@2.4.31` 的 `peer: {"typescript": "<7"}` 独立取 npm registry 确认（解释并佐证了 TS6 pin）；`vue@3.5.46` **无 `engines` 字段**（与 `TECH-STACK.md:354` 的「fake peer floor」陷阱描述一致）；`typescript@6.0.3` / `vue-router@4.6.5` / `pinia@3.0.4` **均真实存在**（`jq '.versions|has("6.0.3")'` → `true`）。

**未证实 / 未运行（必须知道）**：
- **零行代码被运行过**：`backend/`、`vision/`、`web/`、`tests/`、`migrations/` 目录**均不存在**。本文档中所有「设计在位」的判断都是**静态阅读**，不是运行验证。
- **17 表 DDL 从未在 PostgreSQL 上执行**：76 个 CHECK 是**文本计数**，非 `information_schema` 实测。`SCHEMA-DESIGN.md:583-592` 自己已诚实披露这一点。
- **HTTP 状态码行为从未被真实调用**：45/57 个端点的实际响应体、`403`/`409`/`413`/`415` 的真实返回**全部未验证**。
- **jsdom ≠ Chromium**：playwright 落点实测跑在 jsdom，`el-dialog` 的 teleport、焦点陷阱、点击命中区**未验证**（`stack-frontend.md:730-731` 自己已披露）。
- **`mypy`/`pyright` 未预装**，静态门禁该项**从未运行**（`TEAM-CHARTER.md:398`）。
- **24 张变异卡全部未执行**。
- **N1/N2 的零命中是 `rg` 模式搜索，不是依赖树审计**——声明：搜索模式 `mqtt|modbus|lamp.?control|/control|relay` 与数据集表名清单，范围 `docs/` + `deploy/` + `compose.yaml` + `.env.example`；**排除项**：未扫 `uv.lock`（其只含 1 个 virtual package，实际无依赖可扫）。**语义盲区**：不排除某个依赖间接引入 IoT 能力（阶段一零依赖，此风险为零）。
- **GPU onboarding 的 9 项「覆盖」是文档存在性检查**，不是「照着做能跑通」的验证——**远端 GPU 服务器我从未连接**。

**本次审计的方法论边界**：
- 我只做**只读**审计：零修改被审文件、零 git 写命令、零 `npm install` / `docker run` / 网络写操作。唯一网络动作是 GitHub raw / npm registry / PyPI 的**只读 GET**。
- 我有 **1 次失误并已自查纠正**：最初用 `sed` 区间提取 `§10.2` 的 `data-testid` 名字时，把表头 `data-testid` 和以 ` / ` 合并多名的单元格（B 组 17 个、I 组 3 个）漏算，一度得出「合计 97，差 −24」的错误结论；改用「提取单元格内全部反引号名字」后得 **121**，与 `§8.3` 九组声明**逐组精确吻合**。**记录此失误是因为「差 −24」这个假发现若不追根，就会变成一个虚假的 P0。**
- **「过度自纠也是错」**：P0-1（45）与 P0-3（32/36）我尝试了 6 种与 4 种口径，均无法复算出声明值。我**不排除**存在文档未写明的第 7 种口径，因此把这两项标为「口径缺失/无支撑」而非「数字错误」——**真正该 Lead 裁决的是「口径要么写明要么删掉」，不是「哪个数对」**。

---

**审计员**：`auditor`｜**时间**：2026-11-21T08:2x +08:00｜**任务**：`task-8`｜**下一步**：等 `Lead` 复核 6 项 P0 并裁决提交路径
