> 状态：阶段二训练技术手册（**模型训练方案、EDA 实测与源码参数定稿；远端正式训练耗时与 `mAP` 待实际训练完成后回填**）

# 模型训练方案、数据集清洗与超参数设计（`TRAIN`）

> **文档归属**：`docs-writer`｜**写入域**：`docs/TRAIN.md`
> **依据与 `F14` 纪律**：本文所有配置、默认数值、数据集实测与算法细节均逐字核自项目源码（`src/tomori/vision/{dataset_prep,eda,trainer,config,mapping,metrics,client}.py`）、本地 `.venv` 锁定包 **`ultralytics==8.4.216`** 源码（`cfg/default.yaml`、`cfg/models/26/yolo26.yaml`、`engine/{exporter,model,trainer,validator}.py`、`models/yolo/detect/{train,val}.py`、`data/{augment,base,dataset}.py`、`utils/{autobatch,checks,loss,metrics,tal,torch_utils}.py`、`nn/modules/head.py`、`nn/tasks.py`）、`docs/DESIGN.md` §5.3、`dev-ops` `task-72` 远端实测日志（`.team/notes/dev-ops/logs/task72-*.log`）、只读 EDA 与基准实测记录（`.team/notes/vision-dev/dataset-eda-report.md`、`.team/notes/vision-dev/logs/research-1280-epochs500.md`、`.team/notes/vision-dev/logs/bench-1280-vs-1024.log`）、以及 `task-86` 头选择机制与契约核验日志（`.team/notes/vision-dev/logs/task86-{head-plumbing-full,probe-head-switch-proof,metrics-schema-sim}.log`）。**严禁虚构尚未跑出的正式训练耗时或 `mAP` 指标。**

---

## 0. 一览：为什么这套配置适合公共照明故障检测

| 维度 | 本项目方案 | 核心理由 |
|---|---|---|
| **基座与规模** | `yolo26s.pt`（`20,422,725` B，`10,009,784` 参数 / `22.8 GFLOPs` @ 640；`1280` fused `86.0 GFLOPs`） | 远端 `RTX 4080 SUPER`（`32,231 MiB` 可用显存）训练绰绰有余，导出 ONNX 后可在本地 CPU 顺畅推理；禁止更大 `m/l/x` 或更弱 `n`（`D-06`） |
| **端到端无 NMS 架构** | `YOLO26 Detect(end2end=True, reg_max=1)` + `E2ELoss` | 训练期双头协同（`one2many` 加速收敛 + `one2one` 端到端对齐），导出 `nms=False` 后直接输出 **`[1, 300, 6]`**，无需部署后处理 NMS |
| **分辨率** | **`imgsz=1280`**（`2×640`，P3/8 `160×160`，与 81.86% `1280×720` 原图 1:1 零下采样，四端锁死） | D1 全量 `8,428` 图中 `81.86%`（`6,899` 张）为 `1280×720`、`86.64%` 宽 `>= 1280`，`24,502` 标注框中 `97.46%` 面积 `< 0.5%`、COCO Tiny（`<32²`）占 `57.58%`；`1280`（`40×32` 步长整除）相比 `640` 面积 `4×`、相比 `1024`（`0.8×` 丢弃 `36%` 像素）保留 `100%` 原生像素，将缩后 `<2 P3 格(<16px)` 极小目标占比从 `14.72%` 降至 `5.81%`（救回 `2,182` 个极小灯头）（`D-28`） |
| **防泄漏切分** | `Union-Find` 按 `video_id` + `phash_cluster` + `content_sha256` 联合切分 `70%/15%/15%` | `D1` 为连续抽帧（仅 `10` 个独立 `video_id`，最大单组 `2,890` 帧）、`D7` 含 `3×` 预增强副本；必须整簇锁死同一 split 并做 9 项交集=0 硬守卫 |
| **领域增广约束** | **`flipud=0.0`**、**`hsv_v <= 0.25`**、**`hsv_s <= 0.4`**、适度 `degrees=5.0` / `mosaic=0.8` | 路灯杆不会倒立在天空（禁上下翻转）；默认 `hsv_s=0.7`/`hsv_v=0.4` 会把夜间灭灯洗成亮灯或混淆环境光，必须收紧亮度与饱和度扰动 |

---

## 1. 训练基座、硬件隔离与底模锁死（`D-01` / `D-06` / `D-28`）

### 1.1 远端 GPU 环境与目录隔离（实测基线）

开发、数据清洗与训练全部在远端 GPU 服务器执行，**本地只做业务代码开发与 ONNX CPU 推理**（禁止「本地 CPU 冒烟训练」，见 `docs/DESIGN.md` §5.3）。

| 项 | 远端实测值（`.team/notes/dev-ops/logs/task72-yolo-fix-and-weights.log`） |
|---|---|
| **GPU 与驱动环境** | `NVIDIA GeForce RTX 4080 SUPER`（`32,231 MiB` 实测可用显存 / `32,760 MiB` 总显存），`CUDA 13.2`，`torch-2.15.0+cu132`，`Python 3.12.3`（`/root/miniconda3` 提供） |
| **存储隔离目录** | 统一落盘数据盘 `/root/autodl-tmp/tomori/`（`weights/`、`datasets/`、`runs/`、`venv/`、`cache/`、`config/`、`repo/`）；根分区 `overlay` 仅余 `7.8 GiB`，**严禁写入根分区** |
| **包管理与依赖防污染** | 绝对路径 `/root/.local/bin/uv`（`0.13.7`）同步 `--extra vision`；根 `pyproject.toml` 含 `[tool.uv] override-dependencies = ["opencv-python; python_version < '0'"]` 防 `ultralytics` 拉 GUI `opencv-python` 覆盖 headless；环境变量锁 **`YOLO_AUTOINSTALL=False`**、`sync=false`（见 `docs/RUNBOOK.md` §6.1–§6.3） |

### 1.2 唯一底模与四端一致分辨率

1. **唯一底模：`yolo26s.pt`**（`D-06` + `src/tomori/vision/trainer.py:77-81` 硬校验，`base_model.name` 非 `yolo26s.pt` 直接 `ValueError`）：
   - 远端预拉盘 `/root/autodl-tmp/tomori/weights/yolo26s.pt` 实测 **`20,422,725` 字节**，`SHA256 = 646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b`，加载实测 `detect 80 classes`（COCO 预训练）。
   - **架构源码实核**（`.venv/.../ultralytics/cfg/models/26/yolo26.yaml:8-52`）：`end2end: True`、`reg_max: 1`（DFL-free 直接坐标回归）、`scales.s = [depth=0.50, width=0.50, max_channels=1024]`（260 层 / `10,009,784` 参数 / `22.8 GFLOPs` @ 640；注：`max_channels=1024` 为模型通道数上限，非输入图像分辨率）；Backbone 采用 `Conv` + `C3k2` + `SPPF` + `C2PSA`（层 0–10），Head 采用 PAN-FPN 多尺度特征融合（P3/8、P4/16、P5/32，层 11–22）接双头 `Detect`（层 23，分类分支采用 `DWConv` 深度可分离卷积，`nn/modules/head.py:130-137`；`ultralytics==8.4.216` 中不存在 `Detect26` 类，检测任务直接复用 `Detect` `head.py:38`）。
2. **唯一分辨率：`imgsz=1280`（`2×640`，`40×32` 步长整除）**（`D-28` + `config.py:19` + `trainer.py:47, 82-86` 硬校验）：
   - **训练（`1280`）→ 验证（`1280`）→ ONNX 导出（`imgsz=1280, dynamic=False`）→ 本地 `.env` 推理（`YOLO_IMG_SIZE=1280`）** 四端必须严格同值。
   - 导出后固化静态输入 **`[1, 3, 1280, 1280]`**、输出仍为 **`[1, 300, 6]`**（`max_det=300`，由 `Detect.postprocess` 的 `get_topk_index(..., max_det=300)` 固定，`head.py:83, 249-288`），本地 `onnxruntime.InferenceSession` 启动校验形状不符即抛 `INVALID_ARGUMENT`（`src/tomori/vision/client.py:289-308`）。
   - **从 `1024` 升级至 `1280` 的几何与官方基准依据**（详见 `.team/notes/vision-dev/logs/research-1280-epochs500.md` 与 `§2.2` 实测）：
     - **与 COCO `640` 的 `2×` 整数倍及 `stride=32` 对齐**：`1280 = 2 × 640 = 40 × 32`，严格满足 `BaseTrainer._setup_train` 调用 `check_imgsz(..., stride=32, max_dim=1)`（`utils/checks.py:230-294`）的 `stride=32` 整除要求；亦对齐 Ultralytics 官方小目标与高分辨率分块指南推荐档位（`2×640`；VisDrone 基准 `YOLO26s` 从 `imgsz=640` 升至 `1280` 提升 `+8.4 mAP`，且 Ultralytics Platform 标准高分上限即为 `1280`）。
     - **特征图密度与计算量**：P3/8 网格由 `128×128` 扩至 **`160×160`**（P4/16 `80×80`、P5/32 `40×40`），One-to-Many 总 Anchors 从 `21,504` 扩至 **`33,600`**（`1.5625×`），`yolo26s` fused 计算量由 `54.2 GFLOPs` 增至 **`86.0 GFLOPs`**（`nc=4`；`nc=3` 为 `54.147 → 86.020 GFLOPs`），同时导出侧 `end2end=True`（`nms=False`）输出维度 `[1, 300, 6]` 保持完全不变。
     - **对 `81.86%` 原生 `1280×720` 图像实现 1:1 零下采样**：`LetterBox`（`data/augment.py:1795-1843`）与训练加载 `BaseDataset.load_image`（`data/base.py:348-358`）在 `imgsz=1280` 下缩放率 **`r = 1.0`**，直接跳过 `cv2.resize`、无双线性插值模糊，保留 **`100%`** 原图像素（方形输入上下各补 `280px` 灰边 `114` 至 `1280×1280`；验证期 `set_rectangle` 为 `736×1312`），彻底挽回旧 `1024` 方案（`r = 0.8` 缩至 `1024×576`）直接丢弃 **`36%`** 像素的细节损失。

---

## 2. 数据集全景、EDA 定量实测与防泄漏清洗（`D-29` + `eda.py` + `dataset_prep.py`）

### 2.1 `D-29` 定案与 `D1–D7` + 辅助源完整台账

> **✅ `D-29` 用户最终裁决**：「所有数据集不用考虑合规性，直接使用。这是我做着玩的项目，所有代码都只会在本机。不用管。」
> 按此裁决，所有公开数据集均可直接用于阶段二远端 GPU 训练与本地研究；下表同时遵守 `F14` 如实保留各来源原始许可证元数据、已知质量缺陷与远端实盘现状（不虚构许可，也不虚构尚未拉通的数据）。

| 编号 | 来源 | 原始规模与标注结构 | 原始许可证元数据（`F14` 实测） | 远端实盘现状（`task72-*.log` + `eda.py`） | 训练角色与技术处置注意 |
|---|---|---|---|---|---|
| **D1** | `ngocleltt/lamp-state-detection`（HF） | **8,428** 张 JPG（`81.86%` `1280×720` / `13.36%` `854×480` / `4.78%` `1920×1080`）+ **8,428** 个 YOLO `.txt`（**24,502** 框）；原 `classes.txt` 为 `0: lamp_on, 1: lamp_off, 2: lamp_occluded`；含 `metadata.csv`（10 组 `video_id` / 帧序 / 坐标） | `cc-by-4.0`（保留署名） | **已全量拉盘并完成只读 EDA**：`/root/autodl-tmp/tomori/datasets/d1_lamp_state`（8,428 图 + 8,428 框文件全数可读，0 坏图，详见 `§2.2`） | **核心主训练集**（亮灭 + 遮挡检测框）。因含 `10` 组 `video_id` 连续抽帧，必须按 `video_id` 整组切分防泄漏；**破损类 0 个样本** |
| **D2** | `WHU-USI3DV/WHU-Infra3D`（GitHub） | 5,449 张 `8192×4096` 全景图 / 175,021 个 2D 框（含 `Street Light` `label_id=2` 及中文状态/破损属性） | 仓库无 `LICENSE`、README 未声明 | 需填写仓库问卷获取网盘下载链接（非直链） | 全景图畸变大、尺寸 `8K×4K`，若引入须先做视角裁切与自定义 JSON→YOLO 转换（当前 `dataset_prep.py` 未内置全景裁切器） |
| **D3** | Roboflow `jeong-yeonsu/streetlamppole` | 993 张图（仅 `Damaged` 单类检测框） | 页面未标注 License | 未认证直探 `HTTP 403`（Roboflow Universe 需浏览器或 API Key 下载） | 补充 `streetlight_damaged`（class `2`），无亮灭标注 |
| **D4** | Roboflow `godspeed-yqpeo/damaged-lights` | 619 张图（实际仅 `Working` / `Not Working` 两类） | 页面未标注 License | 未认证直探 `HTTP 403`（同上） | 名字含 `damaged`，**实为亮灭 2 类**；`SOURCE_CLASS_ALIASES` 已映射 `working→0`、`not working→1` |
| **D5** | `Team16Project/Street-Light-Dataset`（GitHub, `arXiv:2407.01117`） | EDA 实测共 **1,539** 张静态图 + **26** 个 `.mp4`（`Raw Dataset` 823 张 + 26 `.mp4` 含 `Flicker/` 与 `Working/` 混入视频；`Resized Dataset` 716 张全 `200×200`）；**0 个 YOLO `.txt` 框** | 仓库无 `LICENSE` | **已完整拉盘并完成只读 EDA**：`/root/autodl-tmp/tomori/datasets/d5_street_light`（`4.0 GiB`，`1,539` 图 + `26` `.mp4`） | **仅整图分类目录，无 YOLO 检测框**（不可直接送目标检测头）；且 `.mp4` 必须由 `dataset_prep.py` 跳过（红线 `N1`），`Raw` 高清图仅可人工补标后再接入 |
| **D6** | `AnantRajput415/Nighttime-Street-Light-Functional-Audit-...` | 仓库仅含 HTML + 2 Notebook，外链 Google Drive 未核验 | 未声明 | 未拉取数据（无库内图像） | 仅作低照度增强（CLAHE+Gamma+Unsharp）与按路段防泄漏方法论参考 |
| **D7** | Roboflow `poles-defect/damaged_pole` v2 | Train 2,400 / Valid 182 / Test 73（杆体缺陷检测框，含 **`3×` 预增强**及 `640×640` 拉伸） | 页面未标注 License | 未认证直探 `HTTP 403` / API `401 OAuthException`（需 Roboflow API Key） | 含 `3×` 预增强导致同一原图衍生 3 张高度相似图，**引入前必须经 64-bit pHash 去重聚簇**防跨 split 泄漏；`SOURCE_CLASS_ALIASES` 映射 `damaged_pole→2` |
| **辅助** | `hotosm/streetlevel-poles` / COCO 2017 | `hotosm` 3.5K `pole`/`tower`；COCO 含 `traffic light` 无路灯 | `cc-by-sa-4.0` / `CC-BY-4.0`（图像归 Flickr ToU） | `yolo26s.pt` 已自带 COCO 预训练权重 | 只作杆体/预训练基座参考，不直接混入路灯故障类标签 |

> **`D-18` 破损类（`FIXTURE_DAMAGED`）样本边界声明**：公开数据中 D1 经全量 EDA 实测含 **0 个破损类标注框**，D3/D7 需 Roboflow API Key 或浏览器下载且侧重杆体缺陷，D5 无检测框标注。因此若首轮远端训练仅基于 D1（加后续人工补标），破损类样本量远少于亮灭类——遵循 **`D-18` 定案**，阶段二不虚构/不承诺具体破损类检出率数字，交付与评估按真实落盘数据统计并优先补采/精标破损样本。

### 2.2 数据集定量分析（EDA 实测）

> **只读工具与报告溯源**：只读探索性分析工具已落盘于 **`src/tomori/vision/eda.py`**（784 行，仅读取文件头信息与 YOLO `.txt` 标注，亮度指标采用固定 `seed=42` 内存解码抽样，不修改任何数据集文件）；完整统计报告存 **`.team/notes/vision-dev/dataset-eda-report.md`**（配套结构化摘要 `dataset_eda.json`），延迟与显存基准日志存 **`.team/notes/vision-dev/logs/bench-1280-vs-1024.log`**。

#### 1. D1 全量 8,428 张分辨率实测（只读头信息，非抽样）

全量 `8,428` 张图片读头成功（失败 `0` 张，视频 `0` 个），长宽比全部为 `1.78`（16:9），文件体积 min `53,458` B / median `183,242` B / mean `198,271.35` B / p95 `344,890.55` B / max `1,046,618` B：

| 分辨率 | 张数 | 占比 | 关键阈值统计 |
|---|---:|---:|---|
| **`1280×720`** | **`6,899`** | **`81.86%`** | 与 `imgsz=1280` 实现 **1:1 零下采样**；宽 `>= 1280` 合计 **`86.64%`** |
| `854×480` | `1,126` | `13.36%` | 唯一 `<= 1024` 档（占比 `13.36%`）；宽 min `854.0` / 高 min `480.0` |
| `1920×1080` | `403` | `4.78%` | 高清 1080p 样本；宽 max `1920.0` / median `1280.0` / mean `1253.69` / p95 `1280.0` |

#### 2. D1 全量 24,502 标注框尺度实测

`8,428` 个标注文件共含 **`24,502`** 个标注框（每图框数 min `0.0` / median `3.0` / mean `2.91` / p95 `7.0` / max `14.0`）：

| 框几何维度 | min | median | mean | p95 | max | 相对面积 / COCO 尺度桶分布 |
|---|---:|---:|---:|---:|---:|---|
| **框宽 (px)** | `5.64` | **`30.51`** | `33.84` | `62.14` | `196.53` | 相对面积 **`< 0.1%` 占 `47.37%`**；**`< 0.5%` 占 `97.46%`**；`< 2%` 占 `99.99%` |
| **框高 (px)** | `6.78` | **`29.01`** | `32.34` | `58.71` | `160.39` | COCO **Tiny (`<32²`)**：**`14,108`** 框（**`57.58%`**） |
| **框面积 (px²)** | `86.18` | **`888.47`** | `1267.82` | `3581.03` | `31521.01` | COCO **Small (`32²..96²`)**：**`10,349`**｜**Medium**：**`45`**｜**Large：`0`** |

#### 3. 为什么选择 `1280`（旧 `1024` 方案 vs `imgsz=1280` 实测对比表）

依据 `ultralytics==8.4.216` `Detect(P3, P4, P5)` 三层特征图步长与 `LetterBox` 几何规则，逐框映射 D1 全量 `24,502` 个标注框缩后像素尺度：

| 对比指标 | 旧 `1024` 方案（`32×32`） | 现行 `imgsz=1280`（`2×640 = 40×32`） | 差异与物理影响 |
|---|---:|---:|---|
| 相对 COCO `640` 倍数 | `1.6×` | **`2.0×`** | 面积相对 `640` 扩大 `4.0×`（旧方案 `2.56×`） |
| `P3/8` / `P4/16` / `P5/32` 网格 | `128×128` / `64×64` / `32×32` | **`160×160`** / `80×80` / `40×40` | `P3/8` 空间分辨力提升 `1.5625×` |
| One-to-Many 总 Anchors | `21,504` | **`33,600`** | `+12,096`（`+56.25%` 候选密集锚点） |
| `yolo26s` fused GFLOPs（`nc=4` / `nc=3`） | `54.2` / `54.147` | **`86.0`** / `86.020` | 计算量约 `1.59×`，端到端输出仍为 **`[1, 300, 6]`** |
| `1280×720` LetterBox 缩放率 `r` | `0.8` | **`1.0`** | 落图 `1024×576` vs **`1280×720`**（跳过 `cv2.resize`） |
| 原图像素保留率 | `64.0%`（**丢弃 `36%` 像素**） | **`100.0%`**（**零下采样**） | 彻底保住高空远距灯头微弱亮斑与边框纹理 |
| 缩后短边 `< 1 P3 格 (<8px)` | `84`（`0.34%`） | **`29`（`0.12%`）** | 亚步长极端小目标减少 `65.5%` |
| 缩后短边 `< 2 P3 格 (<16px)` | **`3,606`（`14.72%`）** | **`1,424`（`5.81%`）** | **救回 `2,182` 个极小目标**（降幅 `-8.91 pct`） |
| 缩后短边 `< 3 P3 格 (<24px)` | `14,038`（`57.29%`） | **`7,798`（`31.83%`）** | 高占比小框跨过 3 格 P3 感知门槛 |
| 缩后 COCO Tiny（`<32×32 px²`） | `18,827`（`76.84%`） | **`14,108`（`57.58%`）** | 旧方案将 `4,719` 个 Small 目标压退化为 Tiny |

#### 4. 类别长尾、昼夜分布与 `video_id` 分组规模（D1）

- **类别极度长尾（`94.5:1`）**：`lamp_on = 22,207` 框（`5,940` 图）/ `lamp_off = 2,060` 框（`1,376` 图）/ `lamp_occluded = 235` 框（`213` 图）；**破损类（`streetlight_damaged`）`0` 个样本**（再次印证 `D-18`）。训练与验收必须单独紧盯 `lamp_off` 召回与 `lamp_occluded` 覆盖，不能只看整体 `mAP`。
- **昼夜分布（抽样解码 `1,200` 张，`seed=42`）**：`V` 通道均值 min `23.67` / median `101.99` / mean `100.66` / p95 `147.36` / max `172.19`，场景分布为 **`day=532 / twilight=376 / night=292`**（三类光照均有充分代表，支撑 `§4` 收紧 `hsv_v`/`hsv_s` 防昼夜混淆的领域设计）。
- **`video_id` 分组（`metadata.csv` `8,428` 行）**：共 **`10` 组**独立 `video_id`（`vid01`–`vid10`），每组帧数 min **`130`**（`vid02`）/ median **`554.5`** / mean `842.8` / p95 `2,096.2` / max **`2,890`**（`vid10`）。组规模跨度超过 `22×`，因此切分必须以 `video_id` 为并查集硬键防泄漏并审计各 split 的 `per_class` 样本均衡性。

#### 5. D5 实测（`d5_street_light`）

- 实盘扫描共 **`1,539` 张静态图 + `26` 个 `.mp4` 视频**，**`0` 个 YOLO `.txt` 检测框**（分类文件夹格式）：
  - **`Raw Dataset`**：**`823`** 张静态图 + `26` 个 `.mp4`（`Working/` 内亦混入 4 个 `.mp4`）；Top 分辨率含 `4000×3000` × **`326`** 张、`4000×1800` × **`234`** 张（宽度 `>= 1280` 占 `77.89%`，长宽比 `0.23–2.22`；全量 823 张昼夜 `day=164 / twilight=373 / night=286`）；
  - **`Resized Dataset`**：**`716`** 张静态图 + `0` 个视频，**全为 `200×200`**（`100.00%` 宽度 `<= 1024`；全量 716 张昼夜 `day=134 / twilight=320 / night=262`）。
- 结论：D5 不含任何目标检测框，不能直接并入 YOLO 检测训练；`Raw` 高清图仅作后续人工补标扩展池，接入前须经 `dataset_prep.py` 的 `N1` 守卫自动剔除全部 `26` 个 `.mp4`。

#### 6. 算力、显存与延迟实测（`.team/notes/vision-dev/logs/bench-1280-vs-1024.log`）

1. **本机 CPU `onnxruntime 1.32.0`**（`AMD Ryzen 9 8945H` 8C/16T，`CPUExecutionProvider`，`warmup=2, runs=10`）：
   - **合成代表图（`Conv+ReLU+Constant`, `671.0 KB`）**：`1280` **`19.12 ms`**（max `24.56 ms`）vs 旧 `1024` **`11.66 ms`**（max `14.68 ms`）；
   - **真实导出 `yolo26s` E2E `nc=4` ONNX（`nms=False, dynamic=False, simplify=True`, `384` 节点）**：
     - **`1280`（`36.82 MB`, `86.0 GFLOPs`）**：默认线程 **`median=383.40 ms / p95=458.85 ms / max=464.97 ms`**（占 `60s` 超时 `0.775%`，`129.0×` 余量），RSS **`810.83 MB`**；**单线程最坏 `median=870.61 ms / p95=1,026.80 ms / max=1,029.13 ms`**（占 `60s` `1.715%`，距 `VISION_TIMEOUT_SECONDS=60s` 仍有 **`58.3×` 余量**），RSS `811.80 MB`；
     - **旧 `1024`（`36.59 MB`, `54.2 GFLOPs`）**：默认线程 `median=232.13 ms / p95=271.42 ms / max=273.89 ms`，RSS **`534.78 MB`**；单线程最坏 `median=534.02 ms / max=572.71 ms`；
     - **前处理 `_letterbox_and_normalize`（`1920×1080` BGR）**：`1280` median `10.05 ms` vs `1024` `8.11 ms`。
2. **远端 `RTX 4080 SUPER`（`32,760 MiB` 总显存 / `32,230 MiB` 空闲，`torch 2.15.0+cu132`，`amp=True`）**：
   - *探测口径声明*：仅执行只读 `check_train_batch_size` 与 3 次 dummy forward+backward 探测（未启动 `yolo train`，`/root/autodl-tmp/tomori/runs` 始终为空，测后 `nvidia-smi` `used=1 MiB`）；
   - **`imgsz=1280`**：AutoBatch 探测 **`13`** → `floor_power_of_two` 锁死 **`batch=8`**；3 步 dummy 耗时 `mean=180.70 ms/iter`（`22.59 ms/img`），`nvidia-smi` 峰值 **`12,028 MiB / 11.75 GiB`**（另加测 `batch=16` 峰值 `23,396 MiB / 22.85 GiB`，虽未 OOM，但 `AutoBatch` 按 60% 显存上限自动锁 `batch=8` 为数据增广与验证留足安全余量）；
   - **对比旧 `1024`**：AutoBatch 探测 **`22`** → `floor_power_of_two` 锁死 **`batch=16`**；3 步 dummy 耗时 `mean=217.46 ms/iter`（`13.59 ms/img`），`nvidia-smi` 峰值 **`14,132 MiB / 13.80 GiB`**。

### 2.3 统一类别体系与业务语义映射（`D-30` / `V3`）

`src/tomori/vision/dataset_prep.py:35` 定义 `TARGET_NAMES`（源自 `config.RAW_CLASSES` `config.py:92-97`）：

| 训练/推理类别 ID | 标准类名（`TARGET_NAMES`） | `D1` 原 ID（`DEFAULT_D1_ID_MAP`） | `SOURCE_CLASS_ALIASES` 兼容别名（`dataset_prep.py:55-79`） | 后端业务映射与安全边界（`src/tomori/vision/mapping.py`） |
|---:|---|---|---|---|
| **`0`** | `streetlight_on` | `0`（`lamp_on`） | `streetlight_on`, `lamp_on`, `working`, `on`, `on_streetlight`, `lit` | **夜间/黄昏**：按 **`D-30` 定案**回传前端作参照框（`visual_signal_state="NORMAL_LIT"`, `is_fault_candidate=false`）；**白天**强亮疑似常亮→`OTHER`（`is_fault_candidate=true`），弱信号抑制 |
| **`1`** | `streetlight_off` | `1`（`lamp_off`） | `streetlight_off`, `lamp_off`, `not working`, `not_working`, `not-working`, `off`, `off_streetlight`, `unlit` | **夜间/黄昏**→`LAMP_OUT`（`is_fault_candidate=true`）；**白天**受昼夜物理边界抑制（`DAYTIME_UNPOWERED_EXPECTED`, `is_fault_candidate=false`） |
| **`2`** | `streetlight_damaged` | —（D1 无） | `streetlight_damaged`, `damaged`, `lamp_damaged`, `broken`, `pole_damaged`, `damaged_pole` | 昼/夜均映射为 `FIXTURE_DAMAGED`（`is_fault_candidate=true`） |
| **`3`** | `streetlight_occluded` | **`2`**（`lamp_occluded`） | `streetlight_occluded`, `lamp_occluded`, `occluded` | 昼/夜均映射为 `OCCLUDED`（`is_fault_candidate=true`） |

> **`V3` 训练标签防污染门禁**：静态图模型**永不定义也永不训练** `FLICKERING`（需多帧视频时序）与 `ELECTRIC_HAZARD`（需现场断电检测）。`mapping.py:51-58, 149-170` 与 `contract.py:49` 在输出与校验两层拦截这两类词汇并直接抛错。
>
> **诚实代码边界（`dataset_prep.py`）**：目前 `dataset_prep.py` 实现了针对 **YOLO 格式目录**（`data.yaml` / `classes.txt` / `--source name=/path[:idmap]` + `labels/*.txt`）的完整清洗、pHash 去重与切分管线；若后续接入非 YOLO 格式数据源（如 D2 全景裁切或 D5 分类图人工补标），需先将其转换为标准 `images/` + `labels/*.txt` 后再送入 `dataset_prep.py`。

### 2.4 `src/tomori/vision/dataset_prep.py` 四重防泄漏清洗流水线

```text
原始多源目录 (images + labels + metadata.csv)
   │
   ├─► [1] N1 视频红线守卫 (dataset_prep.py:37-52, 585-591)
   │       跳过 12 种视频/流扩展名 (.mp4/.mov/.avi/.mkv/.flv/.webm/.m4v/.ts/.mpg/.mpeg/.wmv/.m3u8)
   │       仅放行 {.jpg, .jpeg, .png, .webp}；坏图/空图记入 skipped_corrupt_or_empty
   │
   ├─► [2] 类别映射与坐标裁剪 (_parse_yolo_labels, :300-347)
   │       裁剪至 [0.0, 1.0]，丢弃 bw<=1e-6 或 bh<=1e-6 的退化框；无标签图默认跳过
   │
   ├─► [3] D7 64-bit 2D-DCT pHash 聚簇去重 (compute_phash64, :170-200, 369-435)
   │       灰度缩至 (32,32) INTER_AREA → float32 2D-DCT → 取低频 8×8 块 (64 维)
   │       → 用去直流后的 63 维 AC 中位数二值化打包为 64-bit 整数
   │       → 分块(512)计算向量化 Hamming 距离 ≤ 6 归为同簇；每簇保留框最多者，其余去重
   │
   ├─► [4] Union-Find 联合防泄漏切分 (_assign_group_splits, :442-528)
   │       三键同组并查集：
   │         ① {source}::video::{video_id}  (来自 D1 metadata.csv)
   │         ② phash_cluster                 (来自步骤 3，即便 --no-dedupe 仍同簇绑定)
   │         ③ content_sha256                (64KiB 分块哈希，防跨源完全相同图片跨 split)
   │       按 (-len(组), sha256(seed:key)) 贪心均衡分配 train(70%) / val(15%) / test(15%)
   │
   └─► [5] 9 项跨 split 交集=0 硬守卫 (:667-689) → 任一 > 0 直接 raise RuntimeError!
           输出：data.yaml + prep_manifest.json + images/{train,val,test} + labels/{train,val,test}
```

**9 项强制零交集校验键（`leakage_checks`，任一非 `0` 中断落盘）**：
`{video_id, phash_cluster, content_sha256}` × `{train_val_overlap, train_test_overlap, val_test_overlap}` = `9` 项全 `0`。

---

## 3. 超参数设计（逐项对照 `trainer.py` 与 `ultralytics==8.4.216` `default.yaml`）

下表严格区分「**`src/tomori/vision/trainer.py` 显式传入/锁定的值**」与「**未传参而沿用 `ultralytics==8.4.216` `cfg/default.yaml` 的默认值**」，并标明运行时真实行为（`trainer.py` 源码真实核实）：

| 超参数 | `trainer.py` 值（来源坐标） | `default.yaml` 原值 | 实际生效行为 & 设计依据 |
|---|---|---|---|
| **`imgsz`** | **`1280`**（`config.py:19`, `trainer.py:47, 82-86` 硬锁定） | `640`（`:16`） | **显式覆盖**。`2×640` 步长整除，对 D1 `81.86%` `1280×720` 原图实现 `1:1` 零下采样；`__post_init__` 强拒非 `1280`，守 `D-28` 四端一致 |
| **`batch`** | **`-1`**（`trainer.py:50, 183-220`；CPU/回退 `fallback_batch=16`） | `16`（`:15`） | **显式传解析后整数**。调 `autobatch.check_train_batch_size(..., fraction=0.6)` 按 ~60% 显存线性拟合后，由 `floor_power_of_two()`（`trainer.py:123-126`：`1 << (clamped.bit_length()-1)`，钳制 `[1, 64]`）**向下取 2 的幂**锁死（远端 `RTX 4080 SUPER @ 1280` 实测探测 `13` → 锁 **`batch=8`**，峰值 `12,028 MiB`） |
| **`epochs`** | **`500`**（`trainer.py:48, 390`） | `100`（`:12`） | **显式覆盖**。扩大训练上限并交由 `patience=20` 控制停止轮次；对 LR 退火、`close_mosaic` 与 `E2ELoss` 的耦合风险及代码对冲见 `§3.1` |
| **`patience`** | **`20`**（`trainer.py:49, 391`） | `100`（`:14`） | **显式覆盖**。连续 `20` 轮验证集 `mAP50-95` 未严格刷新历史最佳即触发 `EarlyStopping`（`utils/torch_utils.py:1120-1169`） |
| **`nms`** | **训练/权威评估/导出传 `False`；对照评估传 `None`**（主训 `:418`、精修 `:477`、权威 `val` `:504`、对照 `val` `:515`、对照 `test` `:528`、权威 `test` `:537`、导出 `:546`；写入 `metrics.json:581, 619`） | `None`（`:61`，`None`=外接 NMS `one2many`；`False`=无 NMS `one2one` 端到端；`True`=导出内嵌 NMS） | **显式锁定 `False`（使「早停判据头 == 部署头」）**。`default.yaml:61` 的 `None` 会令训练期 `BaseValidator.__call__`（`engine/validator.py:240-242`）设 `model.end2end = False`，使 `EarlyStopping` 全程监控 `one2many` 头而与 `export(..., nms=False)` 的 `one2one` 交付物错位；`trainer.py` 主训与权威 `val`/`test`/导出全链路显式锁 `nms=False`（`one2one` 端到端），仅对照 `val`/`test`（`:515, :528`）显式传 `nms=None` 作诊断参考（因 `_reset_ckpt_args` 会在加载 `.pt` 时丢弃 `nms`，见 `§3.1` 第 1 条） |
| **`optimizer`** | **`"auto"`**（`trainer.py:54, 396`） | `"auto"`（`:27`） | **需警惕 `epochs=500` 触发切分界**：`BaseTrainer.build_optimizer()`（`engine/trainer.py:1232-1241`）在 `optimizer="auto"` 时**忽略传入的 `lr0=0.01`**，按 `iterations = ceil(N_train / max(batch, 64)) * epochs > 10000` 切换 **`MuSGD(lr=0.01, momentum=0.9)`**（分类头 `cv3`/`one2one_cv3` 为 `0.03`），否则选 `AdamW(lr_fit = round(0.002*5/(4+nc), 6), momentum=0.9)`（`nc=4` 时 `0.00125`），`warmup_bias_lr` 置 `0.0` |
| **`lr0`** | `0.01`（`trainer.py:55, 397`） | `0.01`（`:95`） | 传入兼容字段（显式指定非 `auto` 优化器时生效）；`optimizer="auto"` 下由上述 `build_optimizer()` 接管；若开启 `--mosaic-polish-epochs` 则二阶段降为 `lr0 * 0.2` |
| **`lrf`** | **`0.01`**（`trainer.py:56, 398` **显式写入**） | `0.01`（`:96`） | **显式写入配置与诊断**。末期学习率比例 `lr_final = lr0 * lrf`；当 `epochs=500` 提前早停时用于 `early_stopping_diagnostics` 精确核算残余 LR |
| **`cos_lr`** | **`False`**（`trainer.py:57, 399` **显式写入**） | `False`（`:33`） | **显式锁定线性衰减**。在可能提前早停的 `epochs=500` 设定下禁止开 `cos_lr=True`（余弦前段极慢衰减，第 60/500 轮仍留 `96.64%` LR，加剧高学习率震荡，详见 `§3.1`） |
| **`mosaic_polish_epochs`** | **`0`**（`trainer.py:59, 444-488, 706`；可选 CLI `--mosaic-polish-epochs`） | *项目扩展* | **默认关闭（`0`），按需启用两阶段收尾**。若第一阶段在第 `491` 轮前早停导致 `close_mosaic=10` 未触发，显式传入 `--mosaic-polish-epochs N` 会基于 `best.pt` 执行 `N` 轮无 Mosaic（`mosaic=0.0, mixup=0.0, copy_paste=0.0, lr0*0.2, lrf=0.05, cos_lr=True, nms=False`）短微调 |
| **`warmup_epochs`** | *未传*（沿用默认） | **`3.0`**（`:99`） | 前 3 个 epoch 线性预热（`warmup_momentum=0.8`），缓解刚替换 4 类检测头初期的梯度冲击 |
| **`weight_decay`** | *未传*（沿用默认） | **`0.0005`**（`:98`） | 沿用 L2 正则默认强度，抑制稀疏故障类别过拟合 |
| **`amp`** | **`True`**（`trainer.py:53, 395`） | `True`（`:36`） | RTX 4080 SUPER（Ada Lovelace Tensor Core）原生高效支持 FP16 AMP 训练与显存压缩，保留开启；CLI 提供 `--no-amp` 应急开关 |
| **`box` / `cls` / `dfl`** | *未传*（沿用默认） | **`7.5` / `0.5` / `1.5`**（`:104-107`） | **YOLO26 特殊机制核实**（`tasks.py:651`、`loss.py:125,146-163,374-375,1324-1356`）：YOLO26 使用 **`E2ELoss`**（`o2m` `tal_topk=10` 权重从 `0.8` 线性降至 `0.1` + `o2o` `tal_topk=7→1` 权重从 `0.2` 升至 `0.9`）；虽 `reg_max=1` 旁路了 DFL 积分层（日志记 `l1_loss`），**`dfl=1.5` 仍真实加权归一化 `ltrb` L1 框距离损失** |
| **`nbs`** | *未传*（沿用默认） | **`64`**（`:115`） | 名义批大小；`batch=8` 时 Ultralytics 按 `round(64 / 8) = 8` 自动做梯度累积对齐优化步频 |
| **`seed` / `deterministic`** | **`42`** / **`True`**（`trainer.py:68-69, 409-410`） | `0` / `True`（`:29-30`） | 与 `dataset_prep.py`、`eda.py` 默认 `seed=42` 统一，确保数据切分、抽样与训练可复现 |
| **`workers`** | **`8`**（`trainer.py:70, 411`） | `8`（`:21`） | 远端高核 CPU 容器 `workers=8` 高效预取（`1280` 解码不卡 GPU） |

### 3.1 `epochs=500 + patience=20` 源码机制与早停风险对冲（`ultralytics==8.4.216`）

实读本地 `.venv` `ultralytics==8.4.216` 源码确认，`epochs=500` 与 `patience=20` 组合在内部存在 **5 项硬耦合机制**；项目已在 `trainer.py` 写入自动诊断与两阶段精修对冲手段：

1. **`EarlyStopping` 源码定位、早停判据与「早停判据头 == 部署头」完整源码机制链**：
   - **早停触发器与纯 `mAP50-95` 判据**：`utils/callbacks/base.py` 全文件仅为空回调桩，`EarlyStopping` 实际定义于 `utils/torch_utils.py:1120-1169` 并由 `BaseTrainer` 直接持有（`engine/trainer.py:470, 668, 681`）。每轮训练结束时 `engine/trainer.py:670` 执行 `self.metrics, self.fitness = self.validate()`（`validate()` 见 `:953-971`），随后 `:681` 执行 `self.stop |= self.stopper(epoch + 1, self.fitness) or final_epoch`。其中 `Metric.fitness()`（`utils/metrics.py:1015-1018`，由 `DetMetrics.fitness` 属性 `:1213-1216` 代理调用）权重固定为 `w = [0.0, 0.0, 0.0, 1.0]`，即**仅监控验证集 `mAP50-95`**；判定条件为严格 **`fitness > self.best_fitness`**（无 `min_delta` 阈值），连续 **`delta = epoch - self.best_epoch >= 20`** 轮未严格刷新即 `stop = True` 并 `break` 退出训练循环（`engine/trainer.py:709-710`）。
   - **「早停判据头 == 部署头」完整源码选择链路（`ultralytics==8.4.216`）**：
     - **`models/yolo/detect/train.py:175-179`**：`DetectionTrainer.get_validator()` 向 `DetectionValidator` 传入 `copy(self.args)`，训练入参 `nms` 由此直达验证器；
     - **头选择点 `engine/validator.py:240-242` 与 `:257-268`**（注：该文件顶部 L21-23 仅为模块 docstring 中的 CLI 格式示例，实际头选择逻辑位于 `BaseValidator.__call__` 的 `if self.training:` 分支 L240-242 与 `AutoBackend` L257-267）：训练期执行 `model.end2end = self.args.nms is False`（独立验证则在 `AutoBackend(..., end2end=self.args.nms is False)` L267 写入），随后 `DetectionValidator.init_metrics()`（`models/yolo/detect/val.py:136`）读取 `self.end2end = getattr(model, "end2end", False)` 并在 `postprocess()`（`models/yolo/detect/val.py:182`）传给 `non_max_suppression(..., end2end=self.end2end)`；
     - **`nn/modules/head.py:199-204`**：训练态（`self.training=True`）恒返回双头 `{"one2many": ..., "one2one": ...}`（双头同步接收梯度）；推理/验证态（`self.training=False`）按 `self.end2end` 选 `one2one`（经 `postprocess` 输出 `[B, 300, 6]`）或 `one2many`（输出 `[B, 4+nc, anchors]`）；
     - **`engine/exporter.py:795-797`**：导出时执行 `model.end2end = self.args.nms is False or (...)`，当 `nms=False` 时强制保留 `one2one` 端到端头；
     - **`cfg/default.yaml:61`（`DEFAULT_CFG_DICT["nms"] is None`）** → 若 `model.train()` 不传 `nms`，早停全程只监控 `one2many` 头，与 `export(nms=False)` 的 `one2one` 交付物错位；
     - **`engine/model.py:1150-1171` `_reset_ckpt_args` 只保留 `{"imgsz", "data", "task", "single_cls"}`**，`nms` 在加载 `.pt` 权重时被丢弃 → 独立 `best_model.val()` 必须显式传 `nms=`，否则回退 `default.yaml` 的 `None`（`one2many`）；
     - **`src/tomori/vision/trainer.py` 4 处新增 + 既有调用落地全坐标**：`trainer.py:418` 主训练 `model.train(..., nms=False)`、`:477` 精修 `polish_model.train(..., nms=False)`、`:504` 权威验证 `best_model.val(split="val", nms=False, plots=True)`、`:515` 对照验证 `best_model.val(split="val", nms=None, plots=False)`、既有 `:528`（`test` `nms=None`）/ `:537`（`test` `nms=False`）、`:546` `export(..., nms=False)`。由此实现训练早停判据、权威验证/测试评估与 `best_e2e.onnx` 交付物严格同走 **`one2one` 端到端头**。
2. **LR 退火滞后效应（`engine/trainer.py:270-276, 472, 503-508`）**：
   - `BaseTrainer._do_train` 在每轮 batch 前执行 `scheduler.step()`（`last_epoch` 从 `-1` 推进到 `epoch = ep - 1`），故 1-based 第 `ep` 轮实际生效 `LR/LR0 = self.lf(ep - 1)`。
   - 在 `epochs=500, lrf=0.01` 下，若于第 **`40 / 60 / 80 / 100 / 150`** 轮早停，线性衰减（`cos_lr=False`）剩余 LR 仍高达 **`92.28% / 88.32% / 84.36% / 80.40% / 70.50%`**；若开启 `cos_lr=True` 则更高达 **`98.52% / 96.64% / 94.03% / 90.73% / 79.85%`**（说明为何本项目锁死 `cos_lr=False` 且不宜依赖调小 `lrf`）。
3. **`close_mosaic=10` 触发条件（`engine/trainer.py:515-518`）**：
   - 源码条件为 `if not mosaic_closed and epoch >= self.epochs - self.args.close_mosaic:`，即 `epochs=500, close_mosaic=10` 时需走到 0-based `epoch >= 490`（**1-based 第 `491` 轮**）才会关闭 `mosaic`/`mixup`/`copy_paste`/`cutmix`（`data/dataset.py:392-402`）；
   - 一旦在 **`< 491` 轮**触发早停，`close_mosaic` **永不触发**，训练循环直接 `break` 结束。
4. **`E2ELoss` 渐进转移截断（`utils/loss.py:1324-1356` & `nn/tasks.py:653`）**：
   - `E2ELoss` 按 `epochs - 1 = 499` 将 `o2m` 权重从 `0.8` 线性降至 `0.1`（`o2o` 从 `0.2` 升至 `0.9`）：$\text{o2m}(x) = \max(1 - x/499, 0)\cdot 0.7 + 0.1$。
   - 若在第 **`50`** 轮早停，`o2m / o2o` 仅迁移到 **`0.730 / 0.270`**，导致下游导出部署使用的 `nms=False`（`one2one`）端到端头严重欠训（下表定量对照）。
5. **`optimizer="auto"` 在 `iterations > 10000` 静默切 `MuSGD`（`engine/trainer.py:330, 1232-1241`）**：
   - `iterations = ceil(N_train / max(batch, 64)) * epochs`；以 D1 `70%` train split（`~5,900` 张，`batch=8`、`nbs=64` → 每轮约 `93` 步）计，`epochs=500` 时 `iterations ≈ 46,500 >> 10,000`（即使 `N_train=2,000` 时 `500` 轮亦为 `16,000 > 10,000`），`build_optimizer()` 将忽略 `lr0` 并静默选用 **`MuSGD(lr=0.01, momentum=0.9)`**（分类头 `0.03`）。若高 LR 下早停，须关注 `one2one` 头与 `val` 曲线波动。

| 早停轮次（1-based `ep`） | 线性 `cos_lr=False` 剩余 `LR/LR0` | 余弦 `cos_lr=True` 剩余 `LR/LR0` | `E2ELoss` `o2m / o2o` 权重 | `close_mosaic=10` 是否触发（需 `>=491`） |
|---:|---:|---:|---:|---|
| **`40`** | **`92.28%`** | **`98.52%`** | `0.744 / 0.256` | ❌ 永不触发 |
| `50` | `90.30%` | `97.67%` | **`0.730 / 0.270`** | ❌ 永不触发 |
| **`60`** | **`88.32%`** | **`96.64%`** | `0.716 / 0.284` | ❌ 永不触发 |
| **`80`** | **`84.36%`** | **`94.03%`** | `0.688 / 0.312` | ❌ 永不触发 |
| **`100`** | **`80.40%`** | **`90.73%`** | `0.660 / 0.340` | ❌ 永不触发 |
| **`150`** | **`70.50%`** | **`79.85%`** | `0.590 / 0.410` | ❌ 永不触发 |
| `500`（跑满） | `1.20%` | `1.00%` | `0.100 / 0.900` | ✅ 第 `491–500` 轮触发 |

6. **项目已落地的代码级对冲（`src/tomori/vision/trainer.py`）**：
   - **自动调度诊断审计（`trainer.py:315-342, 636-642`）**：`_schedule_diagnostics()` 根据实际 `stopped_epoch` 精确计算并写入 `metrics.json` 的 **`early_stopping_diagnostics`** 字段（`stopped_epoch`、`early_stopped`、`lr_remaining_ratio`、`close_mosaic_trigger_epoch`、`close_mosaic_triggered`、`e2e_loss_o2m_weight`、`e2e_loss_o2o_weight`），供训后审计与双头对比决策；
   - **可选两阶段无 Mosaic 短微调（`trainer.py:59, 444-488, 706`）**：当启用 **`--mosaic-polish-epochs N`**（如 `10` 或 `15`）且第一阶段于第 `491` 轮之前早停（`(stopped_epoch_1based - 1) < (cfg.epochs - cfg.close_mosaic)`）时，自动加载第一阶段 `best.pt` 在 `{run_name}_polish` 执行 `N` 轮干净单图收尾（`patience=0`、`lr0 = cfg.lr0 * 0.2`、`lrf=0.05`、`cos_lr=True`、`mosaic=0.0`、`mixup=0.0`、`copy_paste=0.0`、`nms=False`（`:477`），保留 `flipud=0.0`/`hsv_v<=0.25`/`hsv_s<=0.4`/`fliplr`/`degrees`），随后以精修后的 `best.pt` 进入双头 `val`/`test` 评估与 ONNX 导出。

---

## 4. 数据增广方法与公共照明领域禁区（为什么这样配）

夜间/黄昏道路照明故障检测与通用 COCO 自然场景存在**三个物理刚性约束**（空间朝向固定、亮度/色温即故障核心信号、高空远距灯头极小）。`src/tomori/vision/trainer.py:60-67, 87-92` 对此实施硬编码守卫：

| 增强参数 | `trainer.py` 设定（守卫） | `default.yaml` 默认 | 设计依据与公共照明领域红线 |
|---|---|---|---|
| **`flipud`** | **`0.0`**（`__post_init__` **硬拦截 `!= 0.0` 报错**，`trainer.py:63, 87-88`） | `0.0`（`:124`） | **空间朝向硬禁区**：路灯灯杆垂直地面向上，灯头不可能倒挂于天空或路灯下方路面；`flipud > 0` 会造出物理上不存在的倒置灯杆/天空背景，干扰 `C2PSA` 上下文空间注意力 |
| **`hsv_v`** | **`0.25`**（**硬拦截 `<= 0.25`**，`trainer.py:66, 89-90`） | `0.4`（`:118`） | **亮度防洗白红线（关键覆写）**：EDA 抽样显示 D1 兼含 `day=532 / twilight=376 / night=292`；默认 `0.4`（±40% 明度乘法抖动）会把微弱反光的夜间灭灯 `streetlight_off` 提亮成发光假象，或把弱亮灯压暗成灭灯，直接注入矛盾监督信号；收紧至 **`0.25`** |
| **`hsv_s`** | **`0.4`**（**硬拦截 `<= 0.4`**，`trainer.py:67, 91-92`） | **`0.7`**（`:117`） | **色度/光源防失真（关键覆写）**：高压钠灯（暖黄橙高饱和）与 LED 白光灯（低饱和冷白）饱和度差异本身携带灯具信息；默认 `0.7` 过强会导致过饱和霓虹/杂光或灰白褪色混淆，收紧至 **`0.4`** |
| **`hsv_h`** | *未传*（沿用默认） | **`0.015`**（`:116`） | 微幅色相抖动覆盖高压钠灯/金卤灯/不同色温 LED 及相机白平衡差异，不致将绿植树冠误偏成暖黄灯光 |
| **`fliplr`** | **`0.5`**（`trainer.py:64`） | `0.5`（`:125`） | 街道左右对称，50% 水平翻转可无损翻倍杆体与灯臂角度多样性 |
| **`degrees`** | **`5.0`**（`trainer.py:65`） | `0.0`（`:119`） | **主动开启微旋转（±5°）**：手持手机/车载斜拍常见相机轻微横滚倾角，覆盖真实巡查姿态而不破坏天地方向 |
| **`translate` / `scale`** | *未传*（沿用默认） | **`0.1` / `0.5`**（`:120-121`） | 允许 ±10% 平移与 `[0.5, 1.5]` 尺度变化，模拟巡查员不同距离（近杆 vs 远距连排灯）拍摄 |
| **`shear` / `perspective` / `bgr`** | *未传*（沿用默认） | `0.0` / `0.0` / `0.0`（`:122-126`） | 暂不加剪切与透视畸变；禁用 `bgr` 通道反转以保留钠灯暖黄 vs 天空冷蓝色彩特征 |
| **`mosaic` / `close_mosaic`** | **`0.8`** / **`10`**（`trainer.py:58, 60, 400`） | `1.0` / `10`（`:34, 127`） | 由默认 `1.0` 适度降至 **`0.8`**（保留 `20%` 完整单图上下文学习整杆-天空尺度与昼夜全图光照分布）；`epochs=500` 下 `close_mosaic=10` 需第 `491` 轮触发，若早停未触发可通过 `--mosaic-polish-epochs`（`§3.1`）追加干净单图微调 |
| **`mixup`** | **`0.05`**（`trainer.py:61`） | `0.0`（`:128`） | 仅 **`5%`** 极轻度透明混合，少量提升背景复杂场景抗干扰，严禁高比例 MixUp 以免昼夜帧半透明叠加造成灯头亮灭语义模糊 |
| **`copy_paste`** | **`0.15`**（`trainer.py:62`） | `0.0`（`:130`） | 针对极稀缺故障（破损/遮挡）提升小目标同图出现频次（注：对纯 bbox 检测任务在部分 Ultralytics 版本仅在含 mask 分割标注时触发，配置保留作为策略声明） |
| **`val` / `test` 零增广** | 评估强制纯净输入 | — | 验证与测试阶段关闭一切合成增广，确保评估指标真实反映巡查原始图像表现 |
| **低照度增强参考（`D6`）** | 离线研究参考，不写死进推理前置 | — | 借鉴 D6 的 CLAHE + Gamma + Unsharp 思路仅作夜间极暗样本增强实验参考；线上推理须保留原始亮度分布供 `metrics.py` 准确判断 `day/twilight/night` |

---

## 5. ONNX 导出契约、评估指标与准入门禁

### 5.1 双头离线验证/测试、`metrics.json` 键契约与 ONNX 导出（`trainer.py:492-661` & `exporter.py:795-1296`）

1. **导出调用（必须显式传 `nms=False`）**：
   ```python
   best_model.export(
       format="onnx",
       imgsz=1280,      # D-28 静态输入（2×640）
       nms=False,       # 启用 YOLO26 原生 one2one 端到端无 NMS 头
       dynamic=False,   # 固化静态批/分辨率
       simplify=True,   # 调用 onnxslim>=0.1.82 精简算子图
   )
   ```
   - **为什么必须显式写 `nms=False`**：实读 `ultralytics/engine/exporter.py:795-798` 证实，`nms=None`（默认）时 `model.end2end` 会被置为 `False`（回落 `one2many` 需外部 NMS 的 `(B, 4+nc, anchors)` 输出）；**只有 `nms=False`** 才在 `Detect.fuse()` 保留 `one2one` 头（`head.py:290-295`），导出 **`[1, 3, 1280, 1280] -> [1, 300, 6]`**（输出仍为 `[1, 300, 6]`，`max_det=300`，末维 `[x1, y1, x2, y2, score, class_id]`）。
   - **不硬写 `opset=17`**：沿用 Ultralytics `best_onnx_opset(onnx)` 自动选择安全版本（`utils/export/engine.py:62-86`，Torch `>=2.4` 下默认选 `opset 18`），`pyproject.toml` 声明 `"onnx>=1.16.0,<2.0.0"` 与 `"onnxslim>=0.1.82"`，严格满足 `exporter.py:1207`（`"onnx>=1.12.0,<2.0.0"`）与 `:1217`（`"onnxslim>=0.1.82"`）检查要求（`YOLO_AUTOINSTALL=False` 下不报错）。
2. **双头离线验证与测试对照（`trainer.py:499-540`）**：
   - **`val` split 强制双头分路（`:499-518`）**：因 `engine/model.py:1150-1171` `_reset_ckpt_args` 在加载 `best.pt` 时仅保留 `{"imgsz", "data", "task", "single_cls"}` 而丢弃 `nms`，必须显式分两路重跑：`best_model.val(..., split="val", nms=False, plots=True)`（`:504`）走 **`one2one` 端到端头**（与早停判据头及部署头一致），写入权威 **`val_metrics_e2e_head`** 并绑定顶层 `val_mAP50` / `val_mAP50_95`；`best_model.val(..., split="val", nms=None, plots=False)`（`:515`）走 **`one2many` + 外接 NMS**，写入对照 **`val_metrics_default_head`**（仅诊断用，不参与模型选择与部署）。
   - **`test` split 双头对照（`:522-540`）**：若 `data.yaml` 含 `test` split，同样分别以 `nms=None`（`:528` → `test_metrics_default_head`）与 `nms=False`（`:537` → `test_metrics_e2e_head`）实跑两次作一致对照（重点排查若早停发生在第 491 轮前、`E2ELoss` 迁移未完成时两个头的差距）。
3. **产物体、静态 shape 门禁与 `metrics.json` 20 键契约**：
   - 同时保留原始 **`best.pt`** 与导出 **`best_e2e.onnx`**；
   - `_verify_exported_onnx()`（`trainer.py:283-301` → `client.py:262-345`）用本地同类 `onnxruntime.InferenceSession(..., providers=["CPUExecutionProvider"])` 强制断言输入 `== [1, 3, 1280, 1280]`、输出 `== [1, 300, 6]`；
   - 生成 **`metrics.json`**（顶层共 **20 个键**，`trainer.py:575-645`，详见下表）与 **`SHA256SUMS`**（`:653-661`，覆盖 `weights/best.pt`、`weights/best_e2e.onnx`、`weights/last.pt`、`results.csv`、`args.yaml`、`metrics.json`）。核心契约变更如下：
     - **彻底拆分验证指标（已移除旧键 `val_metrics`）**：拆分为 **`val_metrics_e2e_head`**（权威：早停判据头 == 部署头，`nms=False` / `one2one` 端到端）与 **`val_metrics_default_head`**（对照：`nms=None` / `one2many` + 外接 NMS，仅诊断用，不参与模型选择与部署）；顶层 **`val_mAP50`** / **`val_mAP50_95`** 明确绑定 **`val_metrics_e2e_head`**（`:599-600`）；
     - **新增顶层 `head_contract` 块（`:584-596`）**：包含 7 个结构化键 + `note`（`"deploy_head": "one2one_end2e"`、`"deploy_nms": false`、`"early_stopping_head": "one2one_end2e"`、`"early_stopping_nms": false`、`"comparison_head": "one2many_external_nms"`、`"comparison_nms": null`、`"deploy_artifact": "weights/best_e2e.onnx"`），显式锁死早停判据头与部署产物头一致；
     - **`train_config` 键数由 22 扩充至 28 键（`:606-635`）**：补齐 `lr0`, `lrf`, `cos_lr`, `nms`, `fliplr`, `fallback_batch`, `workers`, `exist_ok` 等全 28 键（`epochs`, `patience`, `requested_batch`, `probed_batch`, `resolved_batch`, `imgsz`, `device`, `amp`, `optimizer`, `lr0`, `lrf`, `cos_lr`, `nms`, `flipud`, `fliplr`, `hsv_v`, `hsv_s`, `degrees`, `mosaic`, `mixup`, `copy_paste`, `close_mosaic`, `mosaic_polish_epochs`, `fallback_batch`, `workers`, `exist_ok`, `seed`, `deterministic`），其中 **`"nms": false`** 显式标明早停判据头选择。

| 序号 | `metrics.json` 顶层键（共 20 键） | 契约取值 / 绑定语义（`trainer.py:575-645`） |
|---:|---|---|
| 1 | `generated_at` | UTC ISO-8601 生成时间戳 |
| 2 | `model_version` | 模型版本号（默认 `"yolo26s-streetlight-v1.0"`） |
| 3 | `base_model` | 底模文件名（锁死 `"yolo26s.pt"`） |
| 4 | `ultralytics_version` | 运行期实际 `ultralytics.__version__` |
| 5 | `imgsz` | `1280`（`client.py:312-341` 启动期强校验） |
| 6 | `nms` | `false`（声明主流程使用端到端无 NMS 头） |
| 7 | `end2end` | `true`（声明端到端 `one2one` 头启用） |
| 8 | `head_contract` | 7 键 + `note` 声明早停判据头、部署头、对照头与交付物路径 |
| 9 | `names` | `{"0": "streetlight_on", "1": "streetlight_off", "2": "streetlight_damaged", "3": "streetlight_occluded"}` |
| 10 | `val_mAP50` | 明确绑定 **`val_metrics_e2e_head["mAP50"]`**（`one2one` 端到端头） |
| 11 | `val_mAP50_95` | 明确绑定 **`val_metrics_e2e_head["mAP50_95"]`**（`one2one` 端到端头） |
| 12 | `val_metrics_e2e_head` | **权威**验证集指标（`nms=False` / `one2one`，含总体指标、`speed_ms`、`per_class`） |
| 13 | `val_metrics_default_head` | **对照**验证集指标（`nms=None` / `one2many` + 外接 NMS，仅诊断用） |
| 14 | `test_metrics_default_head` | **对照**测试集指标（`nms=None` / `one2many` + 外接 NMS；无 `test` split 时为 `{}`） |
| 15 | `test_metrics_e2e_head` | **权威**测试集指标（`nms=False` / `one2one` 端到端；无 `test` split 时为 `{}`） |
| 16 | `onnx_verification` | `input_name`, `output_name`, `input_shape` (`[1,3,1280,1280]`), `output_shape` (`[1,300,6]`), `custom_metadata_map` |
| 17 | `train_config` | 完整 **28 键**训练超参字典（含 `"nms": false` 显式标明早停判据头选择） |
| 18 | `early_stopping_diagnostics` | `_schedule_diagnostics()` 输出（`stopped_epoch`、残余 LR 比例、`close_mosaic` 与 `E2ELoss` 权重状态） |
| 19 | `sha256` | `best.pt`、`best_e2e.onnx`、`last.pt` 的 SHA-256 摘要映射 |
| 20 | `dataset_manifest` | `prep_manifest.json` 完整 JSON 摘要（未找到时为 `null`） |

### 5.2 评估口径与模型回传准入门禁

- **重点观察指标**：`val_mAP50`、`val_mAP50_95`（绑定 `val_metrics_e2e_head`）、**`val_metrics_e2e_head` vs `val_metrics_default_head`**、**`test_metrics_e2e_head` vs `test_metrics_default_head`** 与 `per_class`（尤其关注 `streetlight_off` 召回、`streetlight_occluded` 极少数类与 `streetlight_on` 误报率，防止漏报黑灯或误扰参照框），同时核对 `metrics.json` 中 `imgsz == 1280`、`nms == false`、`end2end == true`、`head_contract` 一致性与 `early_stopping_diagnostics`（若早停未触发 `close_mosaic` 且 `val_metrics_e2e_head` / `test_metrics_e2e_head` 明显滞后于对应 `*_default_head`，可追加 `--mosaic-polish-epochs` 精修对照）。
- **权重未达标 / 回传前降级基线**：在 `best_e2e.onnx` 未回传、`imgsz` 与 `.env` 的 `YOLO_IMG_SIZE=1280` 不一致或 `SHA256SUMS` 校验失败时（见 `docs/RUNBOOK.md` §6.6），`YOLO_INFERENCE_MODE=auto` 下走 `baseline_pretrained_opencv`，置信度受 DB `CHECK` 与 `config.BASELINE_CONFIDENCE_CAP` 硬封顶 **`0.65`**；`strict_custom` 模式一律返 `503 VISION_DISABLED`。

---

## 6. 远端一键复现命令与诚实状态声明

### 6.1 远端执行步骤（在远端 `/root/autodl-tmp/tomori/` 执行）

```bash
# 1) 加载隔离环境变量（绝不占用根分区 7.8G overlay）
source /root/autodl-tmp/tomori/env.sh
cd /root/autodl-tmp/tomori/repo

# 2) 数据清洗、64-bit pHash 去重与防泄漏 70/15/15 切分（以 D1 实拉目录为例）
/root/.local/bin/uv run python -m tomori.vision.dataset_prep \
  --source d1=/root/autodl-tmp/tomori/datasets/d1_lamp_state \
  --output-dir /root/autodl-tmp/tomori/datasets/prepared_v1 \
  --phash-threshold 6 --seed 42

# 3)（可选）无 GPU 干跑校验数据 YAML 与红线参数
/root/.local/bin/uv run python -m tomori.vision.trainer \
  --data /root/autodl-tmp/tomori/datasets/prepared_v1/data.yaml \
  --dry-run

# 4) RTX 4080 SUPER 正式训练 + 双头离线验证/测试 + best_e2e.onnx 导出 + SHA256SUMS 生成
#    可选补充：若训练在 491 轮前早停且需两阶段无 Mosaic 收尾精修，可追加 --mosaic-polish-epochs 10
/root/.local/bin/uv run python -m tomori.vision.trainer \
  --data /root/autodl-tmp/tomori/datasets/prepared_v1/data.yaml \
  --model /root/autodl-tmp/tomori/weights/yolo26s.pt \
  --project /root/autodl-tmp/tomori/runs \
  --name streetlight_v1 \
  --imgsz 1280 --epochs 500 --patience 20 --batch -1
```

### 6.2 诚实状态声明（`F14` 边界）

- **已证实与已落盘**：
  1. 清洗、EDA 与训练脚本 `src/tomori/vision/dataset_prep.py`（875 行）、`src/tomori/vision/eda.py`（784 行）、`src/tomori/vision/trainer.py`（**757 行**）已落地并经本地 `.venv` `ultralytics==8.4.216` 源码逐行核对（`config.py:19` 与 `trainer.py:47-74, 418, 477, 504, 515, 528, 537, 546, 575-645` 已同步锁定 `imgsz=1280`、`epochs=500`、`patience=20`、`lrf=0.01`、`cos_lr=False`、`mosaic_polish_epochs=0`、`nms=False`、「早停判据头 == 部署头」`head_contract` 与 `metrics.json` 20 键 / `train_config` 28 键契约）；
  2. 远端 `RTX 4080 SUPER`（`32,760 MiB` 总显存 / `32,230–32,231 MiB` 空闲、`CUDA 13.2`、`PyTorch 2.15.0+cu132`）、`/root/autodl-tmp/tomori/weights/yolo26s.pt`（`20,422,725 B`）、以及 **D1（8,428 图 / 24,502 框）与 D5（1,539 图 + 26 `.mp4` / 0 框）只读 EDA** 已实机核验落盘（`.team/notes/vision-dev/dataset-eda-report.md`）；
  3. 本地 CPU `onnxruntime 1.32.0`（`yolo26s` E2E `nc=4` @ `1280` 默认线程 `median=383.40ms / max=464.97ms`，单线程最坏 `max=1,029.13ms`，距 `60s` 超时余量 `58.3×`）与远端 `RTX 4080 SUPER` 只读 AutoBatch 探测（`imgsz=1280` 探 `13` → 锁死 `batch=8`，3 步 dummy 峰值 `12,028 MiB / 11.75 GiB`；测后 `runs/` 为空、`nvidia-smi` `used=1 MiB`）已完成实测记录（`.team/notes/vision-dev/logs/bench-1280-vs-1024.log`）；
  4. **「早停判据头 == 部署头」源码链路与离线契约实证已落盘**（`.team/notes/vision-dev/logs/task86-head-plumbing-full.log`、`task86-probe-head-switch-proof.log`、`task86-metrics-schema-sim.log`）：在代码审查级链路核验基础上，离线随机权重 `Detect` 前向实证确认 `nms=None` → `Detect.end2end=False` 输出 `(1, 84, 33600)` vs `nms=False` → `Detect.end2end=True` 输出 `(1, 300, 6)`（与 `best_e2e.onnx` 部署张量完全一致），且本地模拟 `metrics.json` 20 键 / `train_config` 28 键契约测试及 `client._load_metrics_metadata` / `_validate_metrics_names` 实跑全部通过（`SCHEMA OK`）。
- **未运行与待回填（严禁提前编造）**：
  1. **`dataset_prep.py` 正式切分执行**（70/15/15 切分后真实保留张数、pHash 去重数、9 项 `leakage_checks` 与 `prep_manifest.json`、以及是否经 API Key 补充 D3/D4/D7 或人工补标破损样本）与 **远端 `RTX 4080 SUPER` 正式训练**（全周期 `yolo train` 启动，`task-74` 尚未由用户放行）尚未完成；
  2. 因用户尚未放行远端全周期正式训练，**「早停头切换在真实训练下的 `mAP` 实际差异」目前无法实测**，上述头选择闭环属**代码审查级证据**（辅以离线随机权重 `Detect` 前向实证与本地模拟 `metrics.json` 契约测试，见 `.team/notes/vision-dev/logs/task86-probe-head-switch-proof.log` & `task86-metrics-schema-sim.log`），待 `task-74` 放行实训后由真实 `metrics.json` 的 **`val_metrics_e2e_head` vs `val_metrics_default_head`**（及 `test_metrics_e2e_head` vs `test_metrics_default_head`）实测闭环；
  3. 本文当前**不包含**真实数据集切分后样本计数、正式训练中的每 epoch/总训练耗时、真实 `stopped_epoch` 与 `early_stopping_diagnostics`、`val_mAP50`/`val_mAP50_95`、`val_metrics_e2e_head`/`val_metrics_default_head`、`per_class` AP、`test_metrics_default_head`/`test_metrics_e2e_head` 与本地回传 `best_e2e.onnx` 的真实 `SHA256`；上述实测值将在远端正式训练完成后按真实 `metrics.json` 与日志回填本节。
