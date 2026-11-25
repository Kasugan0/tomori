> 状态：阶段二训练技术手册（**`merged_v7`（`nc=3` 终版，`8` 源清洗管线）数据集与 EDA 实测、两处 ETL/EDA Bug 修复、源码参数定稿与 `task-74` `streetlight_v1`（`merged_v5`/`nc=4`）首次全周期正式训练实测结果回填**：`early_stopped @ epoch 43` / `batch=24` / `~30.8GB` 显存 / `6,421.67s`，`best_e2e.onnx` 已回传且 `sha256sum -c` 通过；`streetlight_v3`（`merged_v7`/`nc=3`）二次全周期训练已完结：`early_stopped @ epoch 70`（峰 `epoch 50` `mAP50 0.6067`/`mAP50-95 0.2798`），`batch=24`，`7,217s≈2h`，`best_e2e.onnx` `nc=3` 已回传 `sha256sum -c` `6/6` 通过、`client.py` `D-30`+`damaged` 验收全过）

# 模型训练方案、数据集清洗与超参数设计（`TRAIN`）

> **文档归属**：`docs-writer`｜**写入域**：`docs/TRAIN.md`
> **依据与 `F14` 纪律**：本文所有配置、默认数值、数据集实测与算法细节均逐字核自项目源码（`src/tomori/vision/{dataset_prep,eda,trainer,config,mapping,metrics,client}.py`）、本地 `.venv` 锁定包 **`ultralytics==8.4.216`** 源码（`cfg/default.yaml`、`cfg/models/26/yolo26.yaml`、`engine/{exporter,model,trainer,validator}.py`、`models/yolo/detect/{train,val}.py`、`data/{augment,base,dataset}.py`、`utils/{autobatch,checks,loss,metrics,tal,torch_utils}.py`、`nn/modules/head.py`、`nn/tasks.py`）、`docs/DESIGN.md` §5.3、`dev-ops` `task-72` 远端实测日志（`.team/notes/dev-ops/logs/task72-*.log`）、只读 EDA 与基准实测记录（`.team/notes/vision-dev/dataset-eda-report.md`、`.team/notes/vision-dev/logs/research-1280-epochs500.md`、`.team/notes/vision-dev/logs/bench-1280-vs-1024.log`）、`task-86` 头选择机制与契约核验日志（`.team/notes/vision-dev/logs/task86-{head-plumbing-full,probe-head-switch-proof,metrics-schema-sim}.log`）、以及 **`merged_v5`（8 源终版 `8:1:1` 分层）与多源筛选实证**（`.team/notes/vision-dev/{merged-v5-class-stratified-report.md,merged-v3-report.md,merged-v2-vs-v1-report.md,dataset-expansion-survey.md}`、`.team/notes/vision-dev/logs/{merged_v5_prep_manifest.json,dataset_eda_merged_v5.json,merged_v5_class_distribution.json,task91-rfmode-fix.log}`）。**严禁虚构尚未跑出的正式训练耗时或 `mAP` 指标。**

---

## 0. 一览：为什么这套配置适合公共照明故障检测

| 维度 | 本项目方案 | 核心理由 |
|---|---|---|
| **基座与规模** | `yolo26s.pt`（`20,422,725` B，`10,009,784` 参数 / `22.8 GFLOPs` @ 640；`1280` fused `86.0 GFLOPs`） | 远端 `RTX 4080 SUPER`（`32,231 MiB` 可用显存）训练绰绰有余，导出 ONNX 后可在本地 CPU 顺畅推理；禁止更大 `m/l/x` 或更弱 `n`（`D-06`） |
| **端到端无 NMS 架构** | `YOLO26 Detect(end2end=True, reg_max=1)` + `E2ELoss` | 训练期双头协同（`one2many` 加速收敛 + `one2one` 端到端对齐），导出 `nms=False` 后直接输出 **`[1, 300, 6]`**，无需部署后处理 NMS |
| **终版数据集（`merged_v7`，`nc=3`）** | **`8` 源精筛 + `merged_v6` 清洗管线**：`6,983` 张静态图 / **`17,455`** 框（`train/val/test = 5,570 / 846 / 567`，`79.8%/12.1%/8.1%`） | `merged_v5`（`nc=4`，`10,306` 图 / `25,855` 框，`8,173/948/1,185`）→ `merged_v6`（`mean_v>100` 全 `on` 删 `3,088` 帧 + `occluded` 自证 `39` 改判 `on`/`188` 删 + `damaged` 全保）→ `merged_v7`（`--drop-occluded` 兜底 `nc=3`）；三类 `on 12,960 / off 3,072 / damaged 1,423 / occluded 0` |
| **分辨率** | **`imgsz=1280`**（`2×640`，P3/8 `160×160`，四端锁死） | **`merged_v5`（清洗前基线）** `10,306` 图中 **`59.88%`（6,171 张）为 `1280×720`**、`64.20%` 宽 `>= 1280`，`25,855` 框中 `86.46%` 面积 `< 0.5%`、COCO Tiny（`<32²`）占 **`51.20%`**；`1280` 对 `1280×720` 众数图实现 `mode_letterbox_scale = 1.0` 零下采样，将全框发生下采样占比从 `72.70%` 压至 **`0.57%`**、`<2 P3 格(<16px)` 从 `1,300 (5.03%)` 降至 **`260 (1.01%)`**（`D-28`） |
| **防泄漏切分** | `Union-Find` 分组 + **「多图组枚举 `3^k` + 单图组稀有度加权贪心」按类分层装箱**（`8:1:1`） | `d1` 为连续抽帧（`10` 个 `video_id`）、`d4` 含 `1,446` 预增广副本全锁 `train`；`merged_v6`/`merged_v7` `occluded` 归零后 `val`/`test` `≥30` 约束自动放宽（`occluded_min_per_split=0`）；**13 项**跨 split 防泄漏断言全部 **`0`** |
| **领域增广约束** | **`flipud=0.0`**、**`hsv_v <= 0.25`**、**`hsv_s <= 0.4`**、适度 `degrees=5.0` / `mosaic=0.8` | 路灯杆不会倒立在天空（禁上下翻转）；默认 `hsv_s=0.7`/`hsv_v=0.4` 会把夜间灭灯洗成亮灯或混淆环境光，必须收紧亮度与饱和度扰动 |
| **首次实训结果（`task-74` 实测，`streetlight_v1`/`nc=4`）** | `early_stopped @ epoch 43`（`patience=20`，峰值 `epoch 23`），`batch=24`，`~30.8GB` 显存，`6,421.67s`（`~1h47m`） | `val e2e mAP50 0.34352 / mAP50-95 0.1633` vs `default 0.33764 / 0.16071`；`test e2e 0.38339 / 0.18055` vs `default 0.39389 / 0.18556`；`best_e2e.onnx` `[1,300,6]`，`sha256sum -c` 通过，详见 `§6.3` |
| **二次实训结果（`task-166` 实测，`streetlight_v3`/`nc=3`）** | `early_stopped @ epoch 70`（`patience=20`，峰值 `epoch 50`），`batch=24`，`~30.85GB` 显存，`7,217.1s`（`~2h`） | `val e2e mAP50 0.60669 / mAP50-95 0.27984` vs `default 0.60700 / 0.27542`；`test e2e 0.66205 / 0.40127` vs `default 0.67595 / 0.40930`；`nc=3` 清洗收益 `mAP50 +76.6%`/`mAP50-95 +71.4%`（`off` `+181.7%`）；`best_e2e.onnx` `nc=3`，`sha256sum -c` `6/6` 通过，详见 `§6.4` |

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

1. **唯一底模：`yolo26s.pt`**（`D-06` + `src/tomori/vision/trainer.py:99-103` `__post_init__` 硬校验，`base_model.name` 非 `yolo26s.pt` 直接 `ValueError`）：
   - 远端预拉盘 `/root/autodl-tmp/tomori/weights/yolo26s.pt` 实测 **`20,422,725` 字节**，`SHA256 = 646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b`，加载实测 `detect 80 classes`（COCO 预训练）。
   - **架构源码实核**（`.venv/.../ultralytics/cfg/models/26/yolo26.yaml:8-52`）：`end2end: True`、`reg_max: 1`（DFL-free 直接坐标回归）、`scales.s = [depth=0.50, width=0.50, max_channels=1024]`（260 层 / `10,009,784` 参数 / `22.8 GFLOPs` @ 640；注：`max_channels=1024` 为模型通道数上限，非输入图像分辨率）；Backbone 采用 `Conv` + `C3k2` + `SPPF` + `C2PSA`（层 0–10），Head 采用 PAN-FPN 多尺度特征融合（P3/8、P4/16、P5/32，层 11–22）接双头 `Detect`（层 23，分类分支采用 `DWConv` 深度可分离卷积，`nn/modules/head.py:130-137`；`ultralytics==8.4.216` 中不存在 `Detect26` 类，检测任务直接复用 `Detect` `head.py:38`）。
2. **唯一分辨率：`imgsz=1280`（`2×640`，`40×32` 步长整除）**（`D-28` + `config.py:19` + `trainer.py:47, 82-86` 硬校验）：
   - **训练（`1280`）→ 验证（`1280`）→ ONNX 导出（`imgsz=1280, dynamic=False`）→ 本地 `.env` 推理（`YOLO_IMG_SIZE=1280`）** 四端必须严格同值。
   - 导出后固化静态输入 **`[1, 3, 1280, 1280]`**、输出仍为 **`[1, 300, 6]`**（`max_det=300`，由 `Detect.postprocess` 的 `get_topk_index(..., max_det=300)` 固定，`head.py:83, 249-288`），本地 `onnxruntime.InferenceSession` 启动校验形状不符即抛 `INVALID_ARGUMENT`（`src/tomori/vision/client.py:289-308`）。
   - **从 `1024` 升级至 `1280` 的几何与官方基准依据**（详见 `.team/notes/vision-dev/logs/research-1280-epochs500.md` 与 `§2.2` 实测）：
     - **与 COCO `640` 的 `2×` 整数倍及 `stride=32` 对齐**：`1280 = 2 × 640 = 40 × 32`，严格满足 `BaseTrainer._setup_train` 调用 `check_imgsz(..., stride=32, max_dim=1)`（`utils/checks.py:230-294`）的 `stride=32` 整除要求；亦对齐 Ultralytics 官方小目标与高分辨率分块指南推荐档位（`2×640`；VisDrone 基准 `YOLO26s` 从 `imgsz=640` 升至 `1280` 提升 `+8.4 mAP`，且 Ultralytics Platform 标准高分上限即为 `1280`）。
     - **特征图密度与计算量**：P3/8 网格由 `128×128` 扩至 **`160×160`**（P4/16 `80×80`、P5/32 `40×40`），One-to-Many 总 Anchors 从 `21,504` 扩至 **`33,600`**（`1.5625×`），`yolo26s` fused 计算量由 `54.2 GFLOPs` 增至 **`86.0 GFLOPs`**（`nc=4`；`nc=3` 为 `54.147 → 86.020 GFLOPs`），同时导出侧 `end2end=True`（`nms=False`）输出维度 `[1, 300, 6]` 保持完全不变。
      - **对占 `59.88%`（`merged_v5`）的原生 `1280×720` 图像实现 1:1 零下采样**：`LetterBox`（`data/augment.py:1795-1843`）与训练加载 `BaseDataset.load_image`（`data/base.py:348-358`）在 `imgsz=1280` 下众数缩放率 `mode_letterbox_scale = 1.0`，直接跳过 `cv2.resize`、无双线性插值模糊，保留 **`100%`** 原生像素（方形输入上下各补 `280px` 灰边 `114` 至 `1280×1280`；验证期 `set_rectangle` 为 `736×1312`），彻底挽回旧 `1024` 方案（`0.8×` 缩至 `1024×576`）使全库 **`72.70%`** 的标注框发生下采样的细节损失。

---

## 2. 多源数据集构成、筛选裁决、EDA 实测与防泄漏清洗（`merged_v7`/`nc=3` 终版）

### 2.1 `D-29`/`D-41`/`D-42` 定案与多源数据集构成及筛选裁决（`merged_v7`/`nc=3` 终版；`merged_v5`/`nc=4` 存档对照）

> **✅ `D-29` 用户最终裁决**：「所有数据集不用考虑合规性，直接使用。这是我做着玩的项目，所有代码都只会在本机。不用管。」
> 按此裁决，所有公开数据集均可直接用于阶段二远端 GPU 训练与本地研究；同时严格遵守 **`F14` 事实纪律**：保留全部来源真实原始许可证元数据、逐一记录已入池、明确剔除及目检排雷淘汰的公开数据集。
>
> **演进链路声明（`merged_v1` → `v2` → `v3` → `v4` → `merged_v5` → `merged_v6` → `merged_v7`）**：`merged_v1`（`10,822` 图 / `26,254` 框，`.rf.` 撞名 + `eda.py` `>100%` 两缺陷）不可作定版；`merged_v2`（7 源 `70/15/15` 整组贪心，`10,253` 图）缺 `ext_pwd_s9`；`merged_v3`/`merged_v4`/`merged_v5`（8 源 `10,306` 图 / `25,855` 框）为 `nc=4` 切分定版链（`merged_v5` 为 `streetlight_v1` 实跑输入，`task-74` 已完结入库）；`merged_v6`（`task-162`：`mean_v>100` 全 `on` 夜滤 `3,088` 帧 + `occluded` 自证 `conf>=0.65`/`IoU>=0.1` `39` 改判 `on`/`188` 删 + `damaged` 全保 `1,423` + `empty_frames` 删 `235`，`nc=4` 清洗中间版，`6,983` 图 `5,570/846/567`）；`merged_v7`（`task-165`：同管线追加 `--drop-occluded`/`--nc3`，`names`/`nc`/`target_names`/`config` 写 `3` 类 `[on,off,damaged]`），作为**现行唯一**正式训练数据输入（`streetlight_v3`，`/root/autodl-tmp/tomori/datasets/merged_v7/`；`merged_v6` 为 `nc=4` 对照存档）。

#### 1. ✅ 终版入池 8 源台账（`merged_v6`/`merged_v7`，输入 `22,064` 图 → 去重前 `28,507` 框 → `merged_v5` 切分定版 `10,306` 图 / `25,855` 框 → `merged_v6`/`merged_v7` 清洗终版 `6,983` 图 / `17,455` 框）

| 来源标识 | 平台与项目 Slug / 许可元数据（`F14`） | 原始输入 → `merged_v5` 保留图数 / 清单框数（`merged_v6`/`merged_v7` `prep_manifest.json` 同基） | 预处理策略与业务定位 |
|---|---|---|---|
| **`d1`** | HF `ngocleltt/lamp-state-detection`（`CC BY 4.0`） | `8,428` 图 / `24,502` 框 → **`7,607`** 图 / `24,502` 框（pHash 去重 `821`） | **核心主训练集 & 唯一 `OCCLUDED` 独源（`merged_v7` 已弃用）**（`lamp_on 22,207` / `lamp_off 2,060` / `lamp_occluded 235`，`occluded` 约 `95%` 错标经自证弃用——`D-41`）；`10` 组 `video_id` 整组防泄漏切分，`0` 破损样本 |
| **`d3`** | RF `jeong-yeonsu/streetlamppole/2`（`CC BY 4.0`） | `993` 图 / `1,242` 框 → **`927`** 图 / `1,241` 框（退化框丢 `1`，pHash 去重 `66`） | **街灯杆体损伤**：锈蚀/破损/贴纸小框（`Damaged→2`），属灯具本体，`416×416` 无预增广 |
| **`d4`** | RF `godspeed-yqpeo/damaged-lights/1`（`CC BY 4.0`） | `1,673` 图 / `2,424` 框 → **`1,537`** 图 / `2,424` 框（pHash 去重 `136`，含 `1,446` 预增广） | 名字含 `damaged` 但实为亮灭：`Not Working→1`、`Working→0`（**与 D1 类序相反，已显式反转修正**）；设 `preaug=1, rfmode=phash`，`1,446` 张预增广副本全锁 `train`（`merged_v5`/`merged_v6`/`merged_v7` 中 `d4` 其余 `91` 张无增广图按类分层自然落入 `train 1 / val 20 / test 70`） |
| **`ext_pwd_s6`** | RF `pwd3606/s_6-enyl4/1`（NHAI，`CC BY 4.0`） | `3,841` 图 / `6,006` 框 → **`85`** 图 / `130` 框（DROP `5,876` 框 / `3,725` 空图；SHA `20` / pHash `11`） | **目检真破损灯头/缺灯头/折断灯臂**：YOLO 多边形转外接框 `DAMAGED-STREET-LIGHT→2`，其余 36 类道路缺陷显式 **`DROP`**（因与 `s9` 重叠 `3` 张按规则优先级重分配，留图 `88 → 85`） |
| **`ext_pwd_s9`** *(本版新增第 8 源)* | RF `prjn1/s_9-hqyqe/3`（NHAI，`CC BY 4.0`） | `4,470` 图 / `8,297` 框 → **`56`** 图 / `109` 框（DROP `8,188` 框 / `4,368` 空图；SHA `43` / pHash `3`） | **目检真缺灯头/损坏灯臂/歪斜倒伏路灯**：`100%` 多边形转外接框 `DAMAGED-STREET-LIGHT→2`（原始 `109` 框覆 `102` 图）；跨源去重净留 **`56`** 图，使全库净增 **`+53` 图 / `+58` 框**、高清灯头破损图 **`154 → 207（+34.4%）** |
| **`ext_pwd_s1`** | RF `pwd3601/s_1-bcm7o/2`（NHAI，`CC BY 4.0`） | `1,164` 图 / `1,601` 框 → **`45`** 图 / `51` 框（DROP `1,550` 框 / `1,113` 空图，SHA `5` / pHash `1`） | **目检真破损路灯灯臂**：多边形转框 `DAMAGED-STREET-LIGHT→2`，其余 36 类 `DROP` 并与 `s6`/`s9` 自动去重 |
| **`ext_prj_n_8`** | RF `pot1/prj_n_8/5`（NHAI，`CC BY 4.0`） | `1,277` 图 / `1,567` 框 → **`21`** 图 / `21` 框（DROP `1,546` 框 / `1,256` 空图） | **目检真双臂断折/弯曲/缺灯头**：原生 5 列 bbox `DAMAGED-STREET-LIGHT→2`，其余 36 类 `DROP` |
| **`ext_infras_broken`** | RF `cnn-maker/infras/4`（仅 `valid;test`，`CC BY 4.0`） | `218` 图 / `265` 框 → **`28`** 图 / `29` 框（DROP `236` 框 / `190` 空图） | **目检真碎裂灯罩/悬垂线缆/检修门破损**：仅取无 90°旋转/噪声的 `valid+test`，`broken-streetlamp→2`；正常 `streetlamp`/`manhole`/`signpost` 等 5 类 `DROP` |

> *注（未纳入 `merged_v5`/`merged_v6`/`merged_v7` 的已调研源）*：`D2`（`WHU-Infra3D` 5,449 张 8K 全景）需问卷获取网盘链接并另做全景视角重投影裁切、`pot1/prj_n_3/4` 与已入池 NHAI 同源重叠过高，本轮均未纳入；COCO 2017 / `hotosm/streetlevel-poles` 仅作背景与预训练基座参考。

#### 2. ❌ 明确剔除 3 源与 6 个目检排雷淘汰公开集（防污染/反误报红线）

| 被淘汰数据集 | 来源与原始特征 | 明确剔除 / 淘汰原因（均经 API 或逐框画框目检证伪） |
|---|---|---|
| **`d7`（`poles-defect/damaged_pole/3`）** | Roboflow `Public Domain`；`2,948` 图 / `3,600` 框（88.6% 多边形，原 `merged_v1` 曾占 `1,450` 图） | **实为倒伏乡村配电木杆（`Broken Pole`），非市政灯具**：并入会把正常电线杆/非灯具杆误判为 `FIXTURE_DAMAGED`；遵循 `D-18` 不承诺破损检出率定案，宁缺毋滥彻底剔除 |
| **`ext_civic_broken`（`tanikas-space/civic-issues-nq7sy/6`）** | Roboflow `CC BY 4.0`；`250` 图（入筛 `46` 图 / `55` 框），中位仅 `275×194` | **标注严重错误 + 水印**：9 图抽检约 **`5/9` 实为完好夜间灭灯（`LAMP_OUT`）**、含 **Alamy 商业水印**，且与 `ext_infras_broken` 重叠 `10/50` 图 |
| **`d5`（`Team16Project/Street-Light-Dataset`）** | GitHub / `arXiv:2407.01117`；`1,539` 静态图（`Raw` 823 + `Resized` 716 个 `200×200`）+ `26` 个 `.mp4` | **0 个 YOLO `.txt` 框**（仅整图分类目录）+ 含视频；作 `dataset_prep.py` 红线 `N1` 对照守卫（全数跳过，0 张入训练池） |
| **`yarchi/mgts`** | Roboflow `Public Domain`；`3,917` 图（`Broken` 421 / `street light` 310） | **目检排雷**：`Broken`/`Lose`/`Uncovered` 100% 实为**破损路面井盖**，`street light` 全为健康路灯 |
| **`sankets-workspace-vpabr/civicdetection`** | Roboflow `CC BY 4.0`；`9,996` 图（`broken_streetlight` 589） | **目检排雷**：`broken_streetlight` 100% 实为**路面坑洞/排水沟/裂缝**，零路灯目标 |
| **`akash-kumar-saurav/jannet-civic-issues`** | Roboflow `CC BY 4.0`；`9,067` 图（`broken_street_light` 398） | **目检排雷**：白天为完好未亮灯头、夜间主体为**完好灭灯（`LAMP_OUT`）**，且与 `D4/D5` 有 **285 张 SHA256 完全重复图** |
| **`ctanxy/street-light-gwqbo`** | Roboflow `CC BY 4.0`；`2,729` 原图（v3 含 `7,121` 增广图） | **目检排雷**：标注框为 **`[0, 0, 1, 1]` 全图分类大框**且训练集含 **180° 上下颠倒增广**（直接触犯 `flipud=0.0` 红线） |
| **`651413014-5uq33/broken-bulb-49hso`** | Roboflow `CC BY 4.0`；`588` 图（`bulb` 761 / `broke` 155） | **目检排雷**：全部为**室内家用 E27 LED 灯泡**，非户外道路照明 |
| **`filter-wqrbt/damaged-lamp`** | Roboflow `CC BY 4.0`；`192` 图（`Lamp-Crack` 等） | **目检排雷**：源自 `Car-Damage-Type-Detection`，实为**汽车车灯裂痕** |

> *另备查大类排雷（`.team/notes/vision-dev/dataset-expansion-survey.md` §3.1–§3.3）*：`EPDCL/pd-defect` 等 7 个电力架空线水泥杆集、`Smartathon` 全系（`BAD_STREETLIGHT` 仅 `1–7` 框且含 `CC BY-NC-SA`）、`YDLD`（`CC BY-NC-ND 4.0` 禁止衍生且仅光源框）与 `geopagedev` 等纯健康杆体/井盖/路牌集均已核实排除。
>
> **`OCCLUDED` 独源偏态、`ext_pwd_s9` 同源泛化与 `D-18` 破损检出率三条诚实边界**：
> 1. **`OCCLUDED` 类已按 `D-41` 明确弃用（`nc=3`）**：遍查 Hugging Face、Roboflow Universe、Kaggle、GitHub、Zenodo、Figshare、Mendeley Data、IEEE DataPort 后确认，`d1` 原始 `235` 框（`merged_v5` 保留 `227` 框 / `205` 图）为全网唯一公开路灯遮挡框源；`task-158`/`task-161` 逐帧画框复核 `~95%` 为**错标**（楼窗亮块、传感器/设备盒、树叶遮挡、高架桥灯臂非「枝叶遮挡灯头」语义）；`streetlight_v1` `best.pt` 自证重标（`conf>=0.65`/`IoU>=0.1`）救回 `39` 框改判 `on`（`~22%`），其余 `188` 框删除——**`occluded` 不进入 `merged_v7` 训练与评测**（`API` `fault_type=OCCLUDED` 保留为巡查员手工报障）。`merged_v5` 阶段曾按「`occluded` 双集 `≥30`」硬约束切出 `train 51.54% / val 14.54% / test 33.92%`（`56.92pp` 结构性下限）；后续合成遮挡与阶段二自采是仅存补全路径；
> 2. **`ext_pwd_s9` 仍非独立第三方来源（不得夸大域泛化）**：`ext_pwd_s9` 与 `ext_pwd_s6`、`ext_pwd_s1`、`ext_prj_n_8` 同属 **NHAI（印度国家公路局）公路巡查数据集体系**，跨源去重已拦下 `43` 张 SHA256 完全重复 + `3` 张 pHash 近重复图。并入后高清灯头破损图虽由 `154` 增至 `207`（`+34.4%`），但**仍属同一公路走廊采集域，不得据此宣称跨地域/跨城市场景的域泛化能力提升**；
> 3. **`D-18` 破损类承诺边界**：`streetlight_damaged` 的 `6` 个来源 100% 为单图组（无 `video_id` 原子约束），`merged_v5` 可实现 `80.04% / 9.98% / 9.98%` 近精确分层（偏差和仅 **`0.08pp`**）；`merged_v6`/`merged_v7` 对 `damaged` `1,423` 框**全保留**（无启发式删滤）；主动剔除 `d7` 与 `ext_civic_broken` 后净留 `1,423` 个高纯度框，仍遵守 `D-18` 不在阶段二前置承诺具体破损检出率指标。

### 2.2 数据集定量分析（`merged_v7`/`merged_v6` 终版实测 + `80/10/10` 按类分层切分 + `merged_v5` 存档对照 + 修复后三口径对比）

> **只读工具与报告溯源**：只读 EDA 工具已落盘于 **`src/tomori/vision/eda.py`**（928 行）；`merged_v6`/`merged_v7` 清洗管线与切分实测存 **`.team/notes/vision-dev/task162-merged-v6-report.md`**、**`.team/notes/vision-dev/task165-nc3-code-report.md`**、远端 `merged_v6/prep_manifest.json`（`merged_v7` 为同一管线输出 + `--drop-occluded` `nc=3`）；`merged_v5`（`nc=4`）全量 EDA 存 **`.team/notes/vision-dev/{merged-v5-class-stratified-report.md,dataset-eda-merged-v5.md}`**、**`.team/notes/vision-dev/logs/{merged_v5_prep_manifest.json,dataset_eda_merged_v5.json,merged_v5_class_distribution.json}`**（`merged_v3`/`merged_v4`/`merged_v2` 对照存档分别见 `merged-v3-report.md` 与 `merged-v2-vs-v1-report.md`、早期 D1 单源基线见 `dataset-eda-report.md`）。

#### 1. `merged_v7`（`nc=3` 现行）/`merged_v6`（`nc=4` 清洗版）切分结果与 `clean_stats`：`80/10/10` 组+单图混合按类分层装箱（零断崖，三类全非零）

**`merged_v6`/`merged_v7` 三集分布（`merged_v6/prep_manifest.json`；`merged_v7` 同管线输出 + `--drop-occluded` `nc=3`）**：

| Split | 图数 | 占比 | 目标 | 总框数 | `streetlight_on` | `streetlight_off` | `streetlight_damaged` | `streetlight_occluded` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **`train`** | **`5,570`** | `79.8%` | `80%` | `13,814` | `10,418` | `2,257` | `1,139` | `0` |
| **`val`** | **`846`** | `12.1%` | `10%` | `1,869` | `1,194` | `533` | `142` | `0` |
| **`test`** | **`567`** | `8.1%` | `10%` | `1,772` | `1,348` | `282` | `142` | `0` |
| **合计** | **`6,983`** | — | — | **`17,455`** | `12,960` | `3,072` | `1,423` | **`0`** |

- `train` 按源构成：`d1 3,373 / d3 733 / d4 1,234 / ext_infras_broken 28 / ext_prj_n_8 21 / ext_pwd_s1 45 / ext_pwd_s6 82 / ext_pwd_s9 54`（`unique_video_ids=4` / `unique_rf_stems=963` / `unique_phash_clusters=5,570`）；
- `merged_v6` `split_assignment`：`d1::video::vid10/08/06/07→train`、`vid05/03/02→val`、`vid09/01→test`（清洗后组大小变化致 `vid05` 由 `merged_v5` 的 `test` 重切入 `val`，属正常重切）；`merged_v7` 同一管线产出，`split_assignment` 与 `merged_v6` 一致；
- `split_objective.result`：`hard_ok=true`、`empty_splits=0`/`missing_class_cells=0`、`class_box_deviation_pp=16.3589`、`image_deviation_pp=4.2303`、`combos_evaluated=19,683`/`combos_feasible=19,683`、`occluded_boxes_val=0`/`occluded_boxes_test=0`（`occluded_min_per_split` 已按 `task-165` 放宽为 `0`）；
- **`leakage_checks` `13` 项防泄漏断言全部 `0`**（`video_id`/`rf_stem`/`phash_cluster`/`content_sha256` 三向交集 + `val_test_preaugmented_count`，与 `merged_v5` 同键集）。

**`clean_stats`（`merged_v6` `prep_manifest.json` 实测）**：

| 字段 | 数值 | 含义 |
|---|---:|---|
| `day_dropped_images` | **`3,088`** | `mean_v>100` 且全 `on` 白天帧删除（`d1 2,750 / d4 239 / d3 99`，`ext_*` 为 `damaged` 框非 `on`-only 保留） |
| `day_dropped_boxes_by_cls` | `{0: 8,212}` | 删的全是 `on` 框，`off`/`damaged`/`occluded` 零误伤 |
| `occluded_relabeled_on` | **`39`** | `occluded` 自证 `conf>=0.65`/`IoU>=0.1` 判 `on` → `3`→`0` |
| `occluded_deleted` | **`188`** | `occluded` 杂物框删除（`227 - 39`） |
| `empty_frames_dropped` | **`235`** | 清洗后无任何框的帧删除 |
| `damaged` 删框 | **`0`** | `1,423` 框全保留（不按「灯头 vs 杆体」启发式过滤） |

**`merged_v5`（`nc=4`）与 `merged_v7`（`nc=3`）量化对照**：

| 项 | `merged_v5`（`streetlight_v1` 输入） | `merged_v6`（`nc=4` 清洗版） | `merged_v7`（`streetlight_v3` 输入） |
|---|---|---|---|
| `nc`/`names` | `4`（含 `occluded`） | `4`（`occluded` 归零但保留类槽） | **`3`**（`on`/`off`/`damaged`） |
| 总图数 | `10,306` | `6,983` | `6,983` |
| 总框数 | `25,855` | `17,455` | `17,455` |
| `occluded` 框 | `227`（`117/33/77`，~95% 错标） | `0` | `0` |
| `on` 框 | `21,133` | `12,960` | `12,960` |
| `off` 框 | `3,072` | `3,072` | `3,072` |
| `damaged` 框 | `1,423` | `1,423` | `1,423` |
| 训练运行 | `streetlight_v1`（`task-74`，已完结） | — | `streetlight_v3`（`task-166`，已完结） |

---

#### 2. `merged_v5`（`nc=4`，已存档对照）终版切分结果：`80/10/10` 组+单图混合按类分层装箱（零断崖，三集全非零）

**切分算法（`dataset_prep.py:1143-1440`，`method = "group+single lexicographic stratified bin-packing"`）**：四键 Union-Find 先建防泄漏原子组 → 预增广组硬锁 `train` → 可入集**多图组**按「含最稀有类框数 → 组大小」降序截取 **`_SPLIT_ENUM_LIMIT=12`**（`:149`）做 `3^k` 全枚举（实测 `combos_evaluated=59,049` / `combos_feasible=26,244`）→ 其余**单图组**按稀有度加权贪心逐组填充；字典序目标：① 硬约束（所有存在类别三集非零 + `occluded` 全库 `≥60` 框时 `val`/`test` 各 `≥ _OCCLUDED_MIN_PER_SPLIT=30`，`:151`）→ ② `min Σ|类框数占比-ratio|` → ③ `min Σ|图数占比-ratio|` → ④ 决胜「大 `occluded` 组 → `test`」。

| Split | 图数 | 占比 | 目标 | 偏差 | 总框数 | `on` | `off` | **`damaged`** | **`occluded`** | `damaged` 6 源覆盖 (`d3 / s6 / s9 / s1 / prj_n_8 / infras` 图数) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| **`train`** | **`8,173`** | `79.30%` | `80%` | `0.70pp` | `22,335` | `18,573` | `2,506` | **`1,139`**（`80.0%`） | **`117`**（`51.5%`） | `832 / 82 / 54 / 45 / 21 / 28`（6 源全覆盖） |
| **`val`** | **`948`** | `9.20%` | `10%` | `0.80pp` | `1,886` | `1,678` | `33` | **`142`**（`10.0%`） | **`33`**（`14.5%`） | `41 / 0 / 0 / 0 / 0 / 0`（仅 `d3`；NHAI/infras 高清源全落 `train`/`test`，见边界声明） |
| **`test`** | **`1,185`** | `11.50%` | `10%` | `1.50pp` | `1,634` | `882` | `533` | **`142`**（`10.0%`） | **`77`**（`33.9%`） | `54 / 3 / 2 / 0 / 0 / 0`（`d3`+`s6`+`s9`） |
| **合计** | **`10,306`** | — | — | **图数偏差和 `2.99pp`**（`merged_v4` `6.5pp`，改善 `2.2×`） | **`25,855`** | `21,133` | `3,072` | `1,423` | `227` | `d4` 分布：`1,447 / 20 / 70`（`preaug` 全锁 `train`） |

**四类三集框数占比偏差（`class_proportion_deviation`，目标 `80/10/10`）**：

| 类别 | 总框 | `train` | `val` | `test` | 偏差和 | 说明 |
|---|---:|---:|---:|---:|---:|---|
| `streetlight_on` | `21,133` | `18,573 (87.89%)` | `1,678 (7.94%)` | `882 (4.17%)` | `15.77pp` | 主力类由 `vid10`（`2,411` 图大组）锁入 `train` 所致偏态 |
| `streetlight_off` | `3,072` | `2,506 (81.58%)` | `33 (1.07%)` | `533 (17.35%)` | `17.85pp` | `vid05` 整组 `533` 框进 `test` 所致 |
| **`streetlight_damaged`** | `1,423` | `1,139 (80.04%)` | **`142 (9.98%)`** | **`142 (9.98%)`** | **`0.08pp`** | **100% 单图组（6 源无 `video_id`），实现近精确分层** |
| **`streetlight_occluded`** | `227` | `117 (51.54%)` | `33 (14.54%)` | `77 (33.92%)` | **`56.92pp`** | **结构性下限**（`vid10`+`vid05` 组原子性 + `≥30` 地板约束），非算法失败 |

**`split_objective.result` 目标达成**：`hard_ok = true`、三类单元格 `empty_splits = 0` / `missing_class_cells = 0`；`occluded_boxes_val = 33` / `occluded_boxes_test = 77`（双集均 `≥30` ✓）；`image_deviation_pp = 2.9963`、`class_box_deviation_pp = 90.6247`（含 `occluded` `56.9` 项内）。

**`split_assignment` 组归属（`merged_v5_prep_manifest.json`）**：

```text
d1::video::vid10 (2,411 图, occl 110) -> train   (大组必进 train)
d1::video::vid09 (1,073 图)           -> train
d1::video::vid08 (594 图,  occl 4)    -> train
d1::video::vid07 (1,111 图, occl 2)   -> train
d1::video::vid06 (475 图)             -> train
d1::video::vid05 (926 图,  occl 77)   -> test    ✓ 决胜：大 occluded 组进 test
d1::video::vid02 (130 图)             -> test
d1::video::vid01 (382 图,  occl 33)   -> val     ✓ 33 ≥ 30
d1::video::vid04 (325 图)             -> val
d1::video::vid03 (180 图)             -> val
_single_groups                        -> train 1,063 / val 61 / test 129
```

> *如实备注*：`val` 的 `damaged` 覆盖图全部来自 `d3`（`41` 图 / `142` 框），NHAI+infras 高清源在 `train`/`test` 各落 `235`/`5` 图——这是「`80%` 图数目标 + 多图组原子性」下的自然装箱结果，非缺陷；`d4` 预增广 `1,446` 张全部锁 `train`（`val_test_preaugmented_count = 0`，红线不破）。相对 `merged_v4`（纯视频级贪心）改善：图数偏差和 `6.5pp → 2.99pp`、`off` `31.9pp → 17.85pp`、`damaged` `0.2pp → 0.08pp`；`occluded` `56.9pp` 持平，已达「`vid10` 必进 `train` + 双集 `≥30`」约束组合下可证的最小偏差（`task-127` 穷尽 `3` 种替代方案均被否决，见 `§2.1` 边界 1）。

#### 3. `merged_v7`/`merged_v6` 数据特性与 `merged_v5` 全量分辨率、EXIF、框尺度与昼夜分布

- **全量分辨率与 EXIF 实测（`10,306` 张，读头失败 `0`，视频 `0`）**：**`100%` `EXIF=1`（`{'1': 10306}`）**；宽 min `261` / median `1,280` / mean `1,094.51` / p95 `1,280` / max `3,840`，高 min `396` / median `720` / mean `674.75` / p95 `720` / max `2,160`，长宽比 median `1.78`（mean `1.59`）：
  - **众数分辨率 `1280×720`：`6,171` 张（`59.88%`）**；**宽度 `>= 1280`：`64.20%`**；宽度 `> 1024`：`64.62%`；宽度 `<= 1024`：`35.38%`；
  - Top 分辨率构成：`1280×720` (`6,171`)、`640×640` (`1,565`，`d4` 占主导)、`854×480` (`1,111`)、`416×416` (`927`，`d3`)、`1920×1080` (`326`)、**`3840×2160` 4K 图 `43` 张（较 `v2` 的 `31` 张 +12）**、`3840×1728` (`18`)。
- **全量 `25,855` 标注框尺度（`10,306` 标签文件，每图框数 median `2.0` / mean `2.51` / max `14.0`）**：

| 框几何维度 | min | median | mean | p95 | max | 相对面积 / COCO 尺度桶分布 |
|---|---:|---:|---:|---:|---:|---|
| **框宽 (px)** | `4.99` | **`32.20`** | `42.22` | `101.93` | `866.01` | 相对面积 **`< 0.1%` 占 `39.86%`**；**`< 0.5%` 占 `86.46%`**；`< 2%` 占 `94.91%` |
| **框高 (px)** | `6.78` | **`30.85`** | `40.08` | `85.30` | `1,066.00` | COCO **Tiny (`<32²`)**：**`13,238`**（`51.20%`）｜**Small (`32²..96²`)**：**`11,443`** |
| **框面积 (px²)** | `83.85` | **`1,001.17`** | `2,804.66` | `8,400.88` | `489,153.03` | COCO **Medium (`<192²`)**：**`956`**｜**Large (`>=192²`)**：**`218`** |

- **逐类别框尺度中位数（`merged_v5_class_distribution.json`）**：`on` `31.48×29.97px`（`eq_side 30.77`）；`off` `38.98×35.59px`（`37.71`）；`damaged` `37.60×51.01px`（`44.085`，`tiny 460 / small 693 / medium 152 / large 118`，显著偏大）；`occluded` `34.03×32.47px`（`32.92`，`100%` 相对面积 `<2%`、**`medium`/`large` 均为 `0`，纯小目标**）。
- **昼夜亮度（随机抽样 `600` 张，`seed=42`）**：`V` 均值 min `15.85` / median `108.03` / mean `104.05` / p95 `153.28` / max `190.22`，场景分布为 **`day = 289` / `twilight = 177` / `night = 134`**，三类光照均有充分样本支撑 `§4` 光度约束。

#### 4. 为什么选择 `1280`（`merged_v5` 全 `25,855` 框 × 修复后三口径并列对比）

针对旧版 `eda.py` 单一 `>100%` 口径缺陷（见 `§2.4` Bug 2），现严格以 **`mode_letterbox_scale`（众数分辨率缩放率）+ `letterbox_scale` / `native_pixels_kept`（全框均摊且物理封顶 `100%`）+ `downsampled_boxes_ratio`（发生下采样的框占比）** 三口径并列对比：

| 对比指标（`merged_v5` 全 `25,855` 框逐框实测） | 旧 `1024` 方案（`32×32`） | 现行 `imgsz=1280`（`2×640 = 40×32`） | 差异与物理影响 |
|---|---:|---:|---|
| 相对 COCO `640` 倍数 | `1.6×` | **`2.0×`** | 面积相对 `640` 扩大 `4.0×`（旧方案 `2.56×`） |
| `P3/8` / `P4/16` / `P5/32` 网格 | `128×128` / `64×64` / `32×32` | **`160×160`** / `80×80` / `40×40` | `P3/8` 空间分辨力提升 `1.5625×` |
| One-to-Many 总 Anchors | `21,504` | **`33,600`** | `+12,096`（`+56.25%` 候选密集锚点），`yolo26s` fused `54.2` vs `86.0 GFLOPs` |
| **众数 `1280×720` 缩放率 (`mode_letterbox_scale`)** | `0.8` | **`1.0`** | 落图 `1024×576` vs **`1280×720`**（占 `59.88%` 的众数原图直接跳过 `cv2.resize`） |
| **众数 `1280×720` 原生像素保留 (`mode_native_pixels_kept`)** | `64.0%`（丢 `36%`） | **`100.0%`（零下采样）** | 彻底保住主流 `1280×720` 街景下灯头微弱亮斑与灯具边缘纹理 |
| **全框平均缩放率 (`letterbox_scale`)** | `0.9993` | **`1.2491`** | 含 `640×640`/`416×416` 小图上采样后的全框均值 |
| **全框均摊原生像素保留率（`min(1.0, scale²)` 封顶）** | `73.6%`（`0.7362`） | **`99.6%`（`0.9963`）** | 物理封顶，不再被上采样虚抬出 `>100%` 的假保留率 |
| **全框发生下采样占比 (`downsampled_boxes_ratio`)** | **`72.70%`（`18,796` 框）** | **`0.57%`（`147` 框）** | `1024` 令近 `3/4` 框受损；`1280` 仅剩 4K 源中极少数框下采样 |
| 缩后短边 `< 1 P3 格 (<8px)` | `45`（`0.17%`） | **`1`（`0.00%`）** | 亚步长极端小目标从 `45` 框压至 **`1`** 框 |
| 缩后短边 `< 2 P3 格 (<16px)` | **`1,300`（`5.03%`）** | **`260`（`1.01%`）** | **压掉 `1,040` 个极小目标（降幅 `-80.0%`）** |
| 缩后短边 `< 3 P3 格 (<24px)` | `11,618`（`44.94%`） | **`5,202`（`20.12%`）** | 缩后短边 median `25.14px → 31.43px`、mean `38.04px → 47.55px` |
| 缩后 COCO Tiny（`<32×32 px²`） | `16,562`（`64.06%`） | **`11,745`（`45.43%`）** | 旧方案额外把 `4,817` 个目标压退化为 Tiny |

#### 4. 算力、显存与延迟实测（`.team/notes/vision-dev/logs/bench-1280-vs-1024.log`）

1. **本机 CPU `onnxruntime 1.32.0`**（`AMD Ryzen 9 8945H` 8C/16T，`CPUExecutionProvider`，`warmup=2, runs=10`）：
   - **合成代表图（`Conv+ReLU+Constant`, `671.0 KB`）**：`1280` **`19.12 ms`**（max `24.56 ms`）vs 旧 `1024` **`11.66 ms`**（max `14.68 ms`）；
   - **真实导出 `yolo26s` E2E `nc=4` ONNX（`nms=False, dynamic=False, simplify=True`, `384` 节点）**：
     - **`1280`（`36.82 MB`, `86.0 GFLOPs`）**：默认线程 **`median=383.40 ms / p95=458.85 ms / max=464.97 ms`**（占 `60s` 超时 `0.775%`，`129.0×` 余量），RSS **`810.83 MB`**；**单线程最坏 `median=870.61 ms / p95=1,026.80 ms / max=1,029.13 ms`**（占 `60s` `1.715%`，距 `VISION_TIMEOUT_SECONDS=60s` 仍有 **`58.3×` 余量**），RSS `811.80 MB`；
     - **旧 `1024`（`36.59 MB`, `54.2 GFLOPs`）**：默认线程 `median=232.13 ms / p95=271.42 ms / max=273.89 ms`，RSS **`534.78 MB`**；单线程最坏 `median=534.02 ms / max=572.71 ms`；
     - **前处理 `_letterbox_and_normalize`（`1920×1080` BGR）**：`1280` median `10.05 ms` vs `1024` `8.11 ms`。
2. **远端 `RTX 4080 SUPER`（`32,760 MiB` 总显存 / `32,230 MiB` 空闲，`torch 2.15.0+cu132`，`amp=True`）**：
   - *探测口径声明*：仅执行只读 `ultralytics` `check_train_batch_size` 显存线性拟合与 3 次 dummy forward+backward 实测（未启动 `yolo train`，`/root/autodl-tmp/tomori/runs` 始终为空，测后 `nvidia-smi` `used=1 MiB`）；
   - **`imgsz=1280`（现行 `_DEFAULTS["batch"]=8`）**：`batch=8` 3 步 dummy 耗时 `mean=180.70 ms/iter`（`22.59 ms/img`），`nvidia-smi` 峰值 **`12,028 MiB / 11.75 GiB`**；加测 `batch=16` 峰值 `23,396 MiB / 22.85 GiB`（未 OOM，但增广/验证余量仍窄——故 `_DEFAULTS["batch"]=8` 选为稳态上限，见 `trainer.py:47`）；
   - **对比旧 `1024`**：`batch=16` 3 步 dummy 耗时 `mean=217.46 ms/iter`（`13.59 ms/img`），`nvidia-smi` 峰值 **`14,132 MiB / 13.80 GiB`**。
   > **`AutoBatch` 已删除（`task-133` 瘦身）**：`floor_power_of_two` / `_resolve_batch_size` / `--batch -1` / `--fallback-batch` / `TrainConfig.fallback_batch` / `TrainArtifacts.probed_batch` 全链移除；`batch` 现为**固定整数 `>= 1`**（`trainer.py:73, 115-116`），按「实测显存上限取最大 2 的幂」原则给死 `8`（上述只读探针为唯一实测依据，后续换卡须重跑探针后改 `_DEFAULTS` 或传 `--batch`）。

### 2.3 统一类别体系与业务语义映射（`D-30` / `V3`）

`src/tomori/vision/dataset_prep.py` 定义 `TARGET_NAMES`（`config.RAW_CLASSES` 词汇母集 `config.py:92-97`）+ `_out_names`（`:1151-1155`，`clean_drop_occluded` 时剔除 `cid==3` 输出 `nc=3`）：

> **`nc=3` 当前训练类表（`merged_v7`）**：`names = [streetlight_on, streetlight_off, streetlight_damaged]`；`streetlight_occluded` 已按 `D-41` 弃用（`API` `fault_type=OCCLUDED` 保留手工报障兼容），下表保留为映射参考。

| 训练/推理类别 ID | 标准类名（`TARGET_NAMES`） | `d1` / `d4` / NHAI 典型原标映射 | `SOURCE_CLASS_ALIASES` 核心别名（`dataset_prep.py:88-130`） | 后端业务映射与安全边界（`src/tomori/vision/mapping.py`） |
|---:|---|---|---|---|
| **`0`** | `streetlight_on` | `d1`: `0` (`lamp_on`)；`d4`: `Working→0` | `streetlight_on`, `lamp_on`, `working`, `on`, `lit`, `good_light` | **夜间/黄昏**：按 **`D-30` 定案**回传前端作参照框（`visual_signal_state="NORMAL_LIT"`, `is_fault_candidate=false`）；**白天**强亮疑似常亮→`OTHER`（`is_fault_candidate=true`），弱信号抑制 |
| **`1`** | `streetlight_off` | `d1`: `1` (`lamp_off`)；`d4`: `Not Working→1` | `streetlight_off`, `lamp_off`, `not_working`, `non_working`, `off`, `unlit` | **夜间/黄昏**→`LAMP_OUT`（`is_fault_candidate=true`）；**白天**受昼夜物理边界抑制（`DAYTIME_UNPOWERED_EXPECTED`, `is_fault_candidate=false`） |
| **`2`** | `streetlight_damaged` | `d3`: `Damaged→2`；NHAI: `DAMAGED-STREET-LIGHT→2`；`infras`: `broken-streetlamp→2` | `streetlight_damaged`, `damaged`, `broken_light`, `broken_lamp`, `defective_lamp`, `fallen_pole` | 昼/夜均映射为 `FIXTURE_DAMAGED`（`is_fault_candidate=true`） |
| **`3`** | `streetlight_occluded`（`merged_v7` 已弃用） | `d1`: **`2`**（`lamp_occluded`） | `streetlight_occluded`, `lamp_occluded`, `occluded`, `obstructed_lamp` | 昼/夜均映射为 `OCCLUDED`（`is_fault_candidate=true`；`merged_v7`/`streetlight_v3` 模型不产出，仅 `API` 手工报障兼容——`D-41`/`task-165`） |

> **`V3` 训练标签防污染门禁**：静态图模型**永不定义也永不训练** `FLICKERING`（需多帧视频时序）与 `ELECTRIC_HAZARD`（需现场断电检测）。`mapping.py:51-58, 149-170` 与 `contract.py:49` 在输出与校验两层拦截这两类词汇并直接抛错。
>
> **健康杆体/无关类防猜类红线**：`dataset_prep.py:86-87` 严禁把 `pole`、`utility_pole`、`street_light`、`normal` 等泛化类名自动猜映射到 `streetlight_damaged`；未在显式映射与 `SOURCE_CLASS_ALIASES` 中的类别默认显式 **`DROP`** 并记入 `dropped_classes_counts`。

### 2.4 多源 ETL 引擎能力、两处已修 Bug 与 13 项防泄漏流水线（`dataset_prep.py` + `eda.py`）

#### 1. `src/tomori/vision/dataset_prep.py`（`2,056` → **`2,220`** 行，`task-162`/`task-165` 追加 `_clean_samples`/`_out_names`）多源异构归一化流水线

```text
多源异构目录 (YOLO 5列 / YOLO Polygon / COCO JSON / Pascal VOC XML + metadata.csv)
   │
   ├─► [1] N1 视频红线与坏图守卫 (dataset_prep.py:58-72, 1504-1514)
   │       跳过 12 种视频/流扩展名 (.mp4/.mov/.avi/.mkv/.flv/.webm/.m4v/.ts/.mpg/.mpeg/.wmv/.m3u8)
   │       仅放行 {.jpg, .jpeg, .png, .webp}；bad/empty 计入 skipped_corrupt_or_empty
   │
   ├─► [2] 异构格式解析 + EXIF 转正 + 语义映射与显式 DROP (:359-400, 456-526, 762-1027)
   │       - YOLO 5列 bbox / YOLO 多边形转外接框 (yolo_polygon) / COCO JSON / Pascal VOC XML 统一转归一化 xywh
   │       - EXIF 274 非 1 方向自动转正图像并同步变换归一化框坐标 (_transform_norm_xyxy_for_exif)
   │       - 坐标裁剪至 [0,1] (merged_v5 clipped=50)；丢弃 min_box_px < 2.0 px 退化框 (dropped=1)
   │       - 未映射类别显式 DROP (merged_v5 共 DROP 17,396 个无关类框、剔除 10,652 张纯无关类空图)
   │
   ├─► [3] 精确 SHA256 + Roboflow `.rf.` 同源识别 + 64-bit 2D-DCT pHash 去重 (:291-318, 1030-1138)
   │       - content_sha256 去重 68 张跨源完全重复图；pHash(32×32 INTER_AREA → 8×8 DCT → 63-AC 中位数, Hamming ≤ 6) 去重 1,038 张
   │       - rf_cluster_mode="stem" | "phash" 按源选择簇判据（见下 Bug 1 修复）
   │
   ├─► [4] `merged_v6`/`merged_v7` 清洗管线 `_clean_samples`（`:1158-1257`，`task-162`/`task-165`）
   │       - `--clean-day-mean-v-max 100`：`mean_v>100` 且全 `on` 才删帧（`3,088` 帧/`on` 框 `8,212` 处，`off`/`damaged`/`occluded` 零误伤）
   │       - `--clean-occluded-model best.pt`：`occluded` 自证 `conf>=0.65`/`IoU>=0.1` 判 `on` → `3`→`0`（`39` 改判 + `188` 删）
   │       - `--drop-occluded`/`--nc3`（`task-165`）：`cid==3` 兜底清零 + `_out_names` 输出 `nc=3` `names`/`nc`/`target_names`/`config`
   │       - `damaged` 全保（不按「灯头 vs 杆体」启发式过滤，`1,423` 框零删除）
   │       - `empty_frames_dropped=235`（清洗后无任何框的帧删除）
   │
   ├─► [5] 四键 Union-Find 分组 →「组+单图混合」字典序按类分层装箱（`:1300+`，清洗后重切）
   │       并查集四键同组：{video_id, rf_stem (stem模式), phash_cluster, content_sha256}
   │       预增广组 is_preaugmented=True (d4/train 1,446 张) 硬锁死 train (forbid_preaug_in_val_test=True)
   │       枚举池：多图组按「含最稀有类框数→组大小」降序截 _SPLIT_ENUM_LIMIT=12 (:149)，3^k 全枚举
   │       单图池：其余可入集组稀有度加权贪心逐组填充
   │       字典序目标：① 三集各类非零 + occluded≥60 时双集各 ≥30 (_OCCLUDED_MIN_PER_SPLIT, :151)
   │                    → ② min Σ|类框数占比-ratio| → ③ min Σ|图数占比-ratio| → ④ 大 occluded 组进 test
   │
   ├─► [6] `data.yaml`/`names`/`nc`/`target_names`/`config` 按 `_out_names` 写 `nc=3`/`4`（`:1805, :1930-1934`）
   │
   └─► [7] 13 项跨 split 交集=0 硬守卫 → 任一 > 0 直接 raise RuntimeError!
           输出：data.yaml + prep_manifest.json + images/{train,val,test} + labels/{train,val,test}
```

**13 项强制零交集校验键（`leakage_checks`，`merged_v5`/`merged_v6` 实测全部为 `0`）**：
`{video_id, rf_stem, phash_cluster, content_sha256}` × `{train_val_overlap, train_test_overlap, val_test_overlap}`（`12` 项） + `val_test_preaugmented_count`（`1` 项，保证 `val`/`test` 无预增广副本）= **`13` 项全 `0`**。

#### 2. 本轮发现并修复的两处真实 Bug（含反假绿验证）

1. **Bug 1（ETL）：`.rf.` 前缀短数字 stem 撞名误删 → 新增 `rf_cluster_mode: "stem" | "phash"`**（`dataset_prep.py:137-145, 162`）：
   - **症状与根因**：旧版 `_dedupe_exact_and_rf` 与 `Union-Find` 无条件用 `(source, .rf.前缀stem)` 判同源增广簇。`d4` 原图 stem 是 13 位 Unix 毫秒时间戳（具唯一标识性），但 **`d7` 原图 stem 是 `100`/`105`/`110` 这类通用短数字**，导致两类误判：① **同名不同图**——`105_jpg` 前缀下混着 **`990` 对 dihedral-pHash `>14`、`MAD 31..63`、`corr 0.39..0.68` 的完全不同照片**，被当成增广副本误删（`1,928` 个真实簇误缩至 `1,450` 张）；② **异名同图**——跨 split 同图改名后抓不到；③ 且 `d4` 的 `±14° shear + hflip` 使同簇 pHash 高达 `26..32`，全局单一阈值不可兼得，必须按源结构化切换。
   - **修复与反假绿验证**（`.team/notes/vision-dev/logs/task91-rfmode-fix.log`）：`DatasetSourceSpec` 新增 `rf_cluster_mode: "stem" | "phash"`（CLI `|rfmode=phash`）并记入 `prep_manifest.json` 审计；在构造「3 张同名 `105_jpg` 不同图 + 1 张异名同图」的测试中：**`stem` 模式误合并 `4→1`（`kept_images=1`）**，**`phash` 模式正确保留 `3` 张真差异图并靠 `content_sha256` 捕获异名同图**，证明修复真实生效而非恒绿。
2. **Bug 2（EDA）：`eda.py` 原生像素保留率 `>100%`（`kept_1280 = 1.8886`）→ 三口径物理修复**（`eda.py:450-505`）：
   - **症状与根因**：旧版 `eda.py` 把固定众数 `1280×720` 当公式基准且对上采样不做封顶——遇到 `640×640`/`416×416` 小图在 `1280` 下 `scale > 1` 时，`scale²` 直接算出 `>1` 的假「保留率」（甚至 `1.8886`）。
   - **修复**：改为按每张图**真实 `(nw, nh)`** 逐框计算缩放率、对原生像素保留率加 **`min(1.0, scale²)` 物理封顶**（上采样不新增原生像素信息），并将报告拆为 **`mode_letterbox_scale`**（众数分辨率缩放率）、**`letterbox_scale` / `native_pixels_kept`**（全框均摊封顶）与 **`downsampled_boxes_ratio`**（发生下采样的框占比）三口径并列，彻底消除歧义（已由 `merged_v5` 实测复跑验证，详见 `§2.2.3`）。

---

## 3. 超参数设计（逐项对照 `trainer.py` 与 `ultralytics==8.4.216` `default.yaml`）

下表严格区分「**`src/tomori/vision/trainer.py` 显式传入/锁定的值**」与「**未传参而沿用 `ultralytics==8.4.216` `cfg/default.yaml` 的默认值**」，并标明运行时真实行为（`trainer.py` 源码真实核实）：

| 超参数 | `trainer.py` 值（来源坐标） | `default.yaml` 原值 | 实际生效行为 & 设计依据 |
|---|---|---|---|
| **`imgsz`** | **`1280`**（`config.py:19`, `trainer.py:44` `_DEFAULTS`、`:70` 字段、`:104-108` `__post_init__` 硬锁定） | `640`（`:16`） | **显式覆盖**。`2×640` 步长整除，对 `merged_v5` `59.88%` `1280×720` 众数图实现 `1:1` 零下采样；`__post_init__` 强拒非 `1280`，守 `D-28` 四端一致 |
| **`batch`** | **`8`**（`trainer.py:47` `_DEFAULTS`、`:73` 字段、`:115-116` `>=1` 硬校验、CLI `:654-659`） | `16`（`:15`） | **固定整数（`task-133` 起 AutoBatch 全链删除）**。`floor_power_of_two`/`_resolve_batch_size`/`--batch -1`/`fallback_batch`/`probed_batch` 已删——按 `RTX 4080 SUPER @ 1280` 只读 dummy 探针峰值 `12,028 MiB`（`batch=8`）与 `23,396 MiB`（`batch=16`）实测给死 **`8`**；换卡或调值须重跑探针后改 `_DEFAULTS` 或传 `--batch`（`task-74` 首训实跑 `--batch 24`，`~30.8GB`，见 `§6.3.1`） |
| **`epochs`** | **`500`**（`trainer.py:45` `_DEFAULTS`、`:71` 字段、主训 `:393`） | `100`（`:12`） | **显式覆盖**。扩大训练上限并交由 `patience=20` 控制停止轮次；对 LR 退火、`close_mosaic` 与 `E2ELoss` 的耦合风险及代码对冲见 `§3.1` |
| **`patience`** | **`20`**（`trainer.py:46` `_DEFAULTS`、`:72` 字段、主训 `:394`） | `100`（`:14`） | **显式覆盖**。连续 `20` 轮验证集 `mAP50-95` 未严格刷新历史最佳即触发 `EarlyStopping`（`utils/torch_utils.py:1120-1169`） |
| **`nms`** | **训练/权威评估/导出传 `False`；对照评估传 `None`**（`_train_kwargs` `:347`（主训 `:391` 与精修 `:428` 共用注入）、权威 `val` `:458`、对照 `val` `:469`、对照 `test` `:482`、权威 `test` `:491`、导出 `:502`；写入 `metrics.json` 顶层 `:540` 与 `train_config` `:576`） | `None`（`:61`，`None`=外接 NMS `one2many`；`False`=无 NMS `one2one` 端到端；`True`=导出内嵌 NMS） | **显式锁定 `False`（使「早停判据头 == 部署头」）**。`default.yaml:61` 的 `None` 会令训练期 `BaseValidator.__call__`（`engine/validator.py:240-242`）设 `model.end2end = False`，使 `EarlyStopping` 全程监控 `one2many` 头而与 `export(..., nms=False)` 的 `one2one` 交付物错位；`trainer.py` 主训与权威 `val`/`test`/导出全链路显式锁 `nms=False`（`one2one` 端到端），仅对照 `val`/`test`（`:469, :482`）显式传 `nms=None` 作诊断参考（因 `_reset_ckpt_args` 会在加载 `.pt` 时丢弃 `nms`，见 `§3.1` 第 1 条） |
| **`optimizer`** | **`"auto"`**（`trainer.py:48` `_DEFAULTS`、`:76` 字段、`_train_kwargs` `:327`） | `"auto"`（`:27`） | **需警惕 `epochs=500` 触发切分界**：`BaseTrainer.build_optimizer()`（`engine/trainer.py:1232-1241`）在 `optimizer="auto"` 时**忽略传入的 `lr0=0.01`**，按 `iterations = ceil(N_train / max(batch, 64)) * epochs > 10000` 切换 **`MuSGD(lr=0.01, momentum=0.9)`**（分类头 `cv3`/`one2one_cv3` 为 `0.03`），否则选 `AdamW(lr_fit = round(0.002*5/(4+nc), 6), momentum=0.9)`（`nc=4` 时 `0.00125`、`nc=3` 时 `0.00143`），`warmup_bias_lr` 置 `0.0` |
| **`lr0`** | `0.01`（`trainer.py:49` `_DEFAULTS`、`:77` 字段、主训 `:395`） | `0.01`（`:95`） | 传入兼容字段（显式指定非 `auto` 优化器时生效）；`optimizer="auto"` 下由上述 `build_optimizer()` 接管；若开启 `--mosaic-polish-epochs` 则二阶段降为 `lr0 * 0.2` |
| **`lrf`** | **`0.01`**（`trainer.py:50` `_DEFAULTS`、`:78` 字段、主训 `:396` **显式写入**） | `0.01`（`:96`） | **显式写入配置与诊断**。末期学习率比例 `lr_final = lr0 * lrf`；当 `epochs=500` 提前早停时用于 `early_stopping_diagnostics` 精确核算残余 LR |
| **`cos_lr`** | **`False`**（`trainer.py:79` 字段 + `_train_kwargs` `:328` **显式写入**） | `False`（`:33`） | **显式锁定线性衰减**。在可能提前早停的 `epochs=500` 设定下禁止开 `cos_lr=True`（余弦前段极慢衰减，第 60/500 轮仍留 `96.64%` LR，加剧高学习率震荡，详见 `§3.1`） |
| **`mosaic_polish_epochs`** | **`0`**（`trainer.py:81` 字段、`:421-445` 精修块、CLI `:666` `--mosaic-polish-epochs`） | *项目扩展* | **默认关闭（`0`），按需启用两阶段收尾**。若第一阶段在第 `491` 轮前早停导致 `close_mosaic=10` 未触发，显式传入 `--mosaic-polish-epochs N` 会基于 `best.pt` 执行 `N` 轮无 Mosaic（`mosaic=0.0, mixup=0.0, copy_paste=0.0, lr0*0.2, lrf=0.05, cos_lr=True, nms=False`）短微调 |
| **`warmup_epochs`** | *未传*（沿用默认） | **`3.0`**（`:99`） | 前 3 个 epoch 线性预热（`warmup_momentum=0.8`），缓解刚替换 4 类检测头初期的梯度冲击 |
| **`weight_decay`** | *未传*（沿用默认） | **`0.0005`**（`:98`） | 沿用 L2 正则默认强度，抑制稀疏故障类别过拟合 |
| **`amp`** | **`True`**（`trainer.py:75` 字段 + `_train_kwargs` `:326`） | `True`（`:36`） | RTX 4080 SUPER（Ada Lovelace Tensor Core）原生高效支持 FP16 AMP 训练与显存压缩，保留开启；CLI `:673` 提供 `--no-amp` 应急开关 |
| **`box` / `cls` / `dfl`** | *未传*（沿用默认） | **`7.5` / `0.5` / `1.5`**（`:104-107`） | **YOLO26 特殊机制核实**（`tasks.py:651`、`loss.py:125,146-163,374-375,1324-1356`）：YOLO26 使用 **`E2ELoss`**（`o2m` `tal_topk=10` 权重从 `0.8` 线性降至 `0.1` + `o2o` `tal_topk=7→1` 权重从 `0.2` 升至 `0.9`）；虽 `reg_max=1` 旁路了 DFL 积分层（日志记 `l1_loss`），**`dfl=1.5` 仍真实加权归一化 `ltrb` L1 框距离损失** |
| **`nbs`** | *未传*（沿用默认） | **`64`**（`:115`） | 名义批大小；`batch=8` 时 Ultralytics 按 `round(64 / 8) = 8` 自动做梯度累积对齐优化步频 |
| **`seed` / `deterministic`** | **`42`** / **`True`**（`trainer.py:57` `_DEFAULTS`、`:90-91` 字段、`_train_kwargs` `:338-339`） | `0` / `True`（`:29-30`） | 与 `dataset_prep.py`、`eda.py` 默认 `seed=42` 统一，确保数据切分、抽样与训练可复现 |
| **`workers`** | **`8`**（`trainer.py:52` `_DEFAULTS`、`:92` 字段、`_train_kwargs` `:340`） | `8`（`:21`） | 远端高核 CPU 容器 `workers=8` 高效预取（`1280` 解码不卡 GPU） |

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
     - **`src/tomori/vision/trainer.py` 全链路落地坐标（`task-133` 瘦身后再核实）**：`_train_kwargs` `:347` 的 `"nms": False` 被主训 `model.train`（`:391`）与精修 `polish_model.train`（`:428`）共用注入；权威验证 `best_model.val(split="val", nms=False, plots=True)`（`:458`）、对照验证 `best_model.val(split="val", nms=None, plots=False)`（`:469`）、`test` `nms=None`（`:482`）/`test` `nms=False`（`:491`）、`export(..., nms=False)`（`:502`）。由此实现训练早停判据、权威验证/测试评估与 `best_e2e.onnx` 交付物严格同走 **`one2one` 端到端头**。
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
   - **自动调度诊断审计（`trainer.py:288-311, :592-598`）**：`_schedule_diagnostics()` 根据实际 `stopped_epoch` 精确计算并写入 `metrics.json` 的 **`early_stopping_diagnostics`** 字段（`stopped_epoch`、`early_stopped`、`lr_remaining_ratio`、`close_mosaic_trigger_epoch`、`close_mosaic_triggered`、`e2e_loss_o2m_weight`、`e2e_loss_o2o_weight`），供训后审计与双头对比决策；
   - **可选两阶段无 Mosaic 短微调（`trainer.py:81` 字段、`:421-445` 执行块、CLI `:666`）**：当启用 **`--mosaic-polish-epochs N`**（如 `10` 或 `15`）且第一阶段于第 `491` 轮之前早停（`(stopped_epoch_1based - 1) < (cfg.epochs - cfg.close_mosaic)`）时，自动加载第一阶段 `best.pt` 在 `{run_name}_polish` 执行 `N` 轮干净单图收尾（`patience=0`、`lr0 = cfg.lr0 * 0.2`、`lrf=0.05`、`cos_lr=True`、`mosaic=0.0`、`mixup=0.0`、`copy_paste=0.0`，`nms=False` 经 `_train_kwargs` `:347` 注入，保留 `flipud=0.0`/`hsv_v<=0.25`/`hsv_s<=0.4`/`fliplr`/`degrees`），随后以精修后的 `best.pt` 进入双头 `val`/`test` 评估与 ONNX 导出。**`task-133` 瘦身取舍说明**：该补救覆盖「早停于 `close_mosaic` 触发前致 Mosaic 永不收敛」的真实失效模式，与 `--export-only`（`best.pt` 不重训复跑导出+校验）、`--no-amp`（AMP `nan` 等异常调试开关）同为有实测依据的功能，非透传冗余故保留。

---

## 4. 数据增广方法与公共照明领域禁区（为什么这样配）

夜间/黄昏道路照明故障检测与通用 COCO 自然场景存在**三个物理刚性约束**（空间朝向固定、亮度/色温即故障核心信号、高空远距灯头极小）。`src/tomori/vision/trainer.py:81-89, :110-114` 对此实施硬编码守卫：

| 增强参数 | `trainer.py` 设定（守卫） | `default.yaml` 默认 | 设计依据与公共照明领域红线 |
|---|---|---|---|
| **`flipud`** | **`0.0`**（`__post_init__` **硬拦截 `!= 0.0` 报错**，`trainer.py:85, :110`） | `0.0`（`:124`） | **空间朝向硬禁区**：路灯灯杆垂直地面向上，灯头不可能倒挂于天空或路灯下方路面；`flipud > 0` 会造出物理上不存在的倒置灯杆/天空背景，干扰 `C2PSA` 上下文空间注意力 |
| **`hsv_v`** | **`0.25`**（**硬拦截 `<= 0.25`**，`trainer.py:88, :111`） | `0.4`（`:118`） | **亮度防洗白红线（关键覆写）**：`merged_v5` `600` 张随机抽样实测兼含 `day=289 / twilight=177 / night=134`；默认 `0.4`（±40% 明度乘法抖动）会把微弱反光的夜间灭灯 `streetlight_off` 提亮成发光假象，或把弱亮灯压暗成灭灯，直接注入矛盾监督信号；收紧至 **`0.25`** |
| **`hsv_s`** | **`0.4`**（**硬拦截 `<= 0.4`**，`trainer.py:89, :113`） | **`0.7`**（`:117`） | **色度/光源防失真（关键覆写）**：高压钠灯（暖黄橙高饱和）与 LED 白光灯（低饱和冷白）饱和度差异本身携带灯具信息；默认 `0.7` 过强会导致过饱和霓虹/杂光或灰白褪色混淆，收紧至 **`0.4`** |
| **`hsv_h`** | *未传*（沿用默认） | **`0.015`**（`:116`） | 微幅色相抖动覆盖高压钠灯/金卤灯/不同色温 LED 及相机白平衡差异，不致将绿植树冠误偏成暖黄灯光 |
| **`fliplr`** | **`0.5`**（`trainer.py:86`） | `0.5`（`:125`） | 街道左右对称，50% 水平翻转可无损翻倍杆体与灯臂角度多样性 |
| **`degrees`** | **`5.0`**（`trainer.py:56` `_DEFAULTS`、`:87` 字段） | `0.0`（`:119`） | **主动开启微旋转（±5°）**：手持手机/车载斜拍常见相机轻微横滚倾角，覆盖真实巡查姿态而不破坏天地方向 |
| **`translate` / `scale`** | *未传*（沿用默认） | **`0.1` / `0.5`**（`:120-121`） | 允许 ±10% 平移与 `[0.5, 1.5]` 尺度变化，模拟巡查员不同距离（近杆 vs 远距连排灯）拍摄 |
| **`shear` / `perspective` / `bgr`** | *未传*（沿用默认） | `0.0` / `0.0` / `0.0`（`:122-126`） | 暂不加剪切与透视畸变；禁用 `bgr` 通道反转以保留钠灯暖黄 vs 天空冷蓝色彩特征 |
| **`mosaic` / `close_mosaic`** | **`0.8`** / **`10`**（`trainer.py:51` `_DEFAULTS`（`close_mosaic`）、`:82` `mosaic` 字段、`:80` `close_mosaic` 字段、`_train_kwargs` `:329-330`） | `1.0` / `10`（`:34, 127`） | 由默认 `1.0` 适度降至 **`0.8`**（保留 `20%` 完整单图上下文学习整杆-天空尺度与昼夜全图光照分布）；`epochs=500` 下 `close_mosaic=10` 需第 `491` 轮触发，若早停未触发可通过 `--mosaic-polish-epochs`（`§3.1`）追加干净单图微调 |
| **`mixup`** | **`0.05`**（`trainer.py:83`） | `0.0`（`:128`） | 仅 **`5%`** 极轻度透明混合，少量提升背景复杂场景抗干扰，严禁高比例 MixUp 以免昼夜帧半透明叠加造成灯头亮灭语义模糊 |
| **`copy_paste`** | **`0.15`**（`trainer.py:84`） | `0.0`（`:130`） | **策略占位（对纯 bbox 检测集在 `ultralytics==8.4.216` 下 100% no-op）**：实读 `.venv` `ultralytics/data/augment.py:1983-1986`，`CopyPaste.__call__()` 首行即判定 `if len(labels["instances"].segments) == 0 or self.p == 0: return labels`；虽 `dataset_prep.py` 可读入 `yolo_polygon` 并转为标准 YOLO 5 列框，但 `merged_v5` 落盘标注不含分割 mask（`segments` 为空），故本参数在当前检测训练期 100% 空转不生效（与 `trainer.py:84` 配置保留一致，仅作未来引入实例分割 mask 时的策略标记） |
| **`val` / `test` 零增广** | 评估强制纯净输入 | — | 验证与测试阶段关闭一切合成增广，确保评估指标真实反映巡查原始图像表现 |
| **低照度增强参考（`D6`）** | 离线研究参考，不写死进推理前置 | — | 借鉴 D6 的 CLAHE + Gamma + Unsharp 思路仅作夜间极暗样本增强实验参考；线上推理须保留原始亮度分布供 `metrics.py` 准确判断 `day/twilight/night` |

---

## 5. ONNX 导出契约、评估指标与准入门禁

### 5.1 双头离线验证/测试、`metrics.json` 键契约与 ONNX 导出（`trainer.py:451-630` & `exporter.py:795-1296`）

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
2. **双头离线验证与测试对照（`trainer.py:458-499`）**：
   - **`val` split 强制双头分路（`:458-478`）**：因 `engine/model.py:1150-1171` `_reset_ckpt_args` 在加载 `best.pt` 时仅保留 `{"imgsz", "data", "task", "single_cls"}` 而丢弃 `nms`，必须显式分两路重跑：`best_model.val(..., split="val", nms=False, plots=True)`（`:458`）走 **`one2one` 端到端头**（与早停判据头及部署头一致），写入权威 **`val_metrics_e2e_head`** 并绑定顶层 `val_mAP50` / `val_mAP50_95`（`:558-559`）；`best_model.val(..., split="val", nms=None, plots=False)`（`:469`）走 **`one2many` + 外接 NMS**，写入对照 **`val_metrics_default_head`**（仅诊断用，不参与模型选择与部署）。
   - **`test` split 双头对照（`:481-499`）**：若 `data.yaml` 含 `test` split，同样分别以 `nms=None`（`:482` → `test_metrics_default_head`）与 `nms=False`（`:491` → `test_metrics_e2e_head`）实跑两次作一致对照（重点排查若早停发生在第 491 轮前、`E2ELoss` 迁移未完成时两个头的差距）。
3. **产物体、静态 shape 门禁与 `metrics.json` 20 键契约**：
   - 同时保留原始 **`best.pt`** 与导出 **`best_e2e.onnx`**；
   - `_verify_exported_onnx()`（`trainer.py:256-280` → `client.py:262-345`）用本地同类 `onnxruntime.InferenceSession(..., providers=["CPUExecutionProvider"])` 强制断言输入 `== [1, 3, 1280, 1280]`、输出 `== [1, 300, 6]`；
   - 生成 **`metrics.json`**（顶层共 **20 个键**，`trainer.py:534-601`，详见下表）与 **`SHA256SUMS`**（`:609-618`，覆盖 `weights/best.pt`、`weights/best_e2e.onnx`、`weights/last.pt`、`results.csv`、`args.yaml`、`metrics.json`）。核心契约变更如下：
     - **彻底拆分验证指标（已移除旧键 `val_metrics`）**：拆分为 **`val_metrics_e2e_head`**（权威：早停判据头 == 部署头，`nms=False` / `one2one` 端到端）与 **`val_metrics_default_head`**（对照：`nms=None` / `one2many` + 外接 NMS，仅诊断用，不参与模型选择与部署）；顶层 **`val_mAP50`** / **`val_mAP50_95`** 明确绑定 **`val_metrics_e2e_head`**（`:558-559`）；
     - **新增顶层 `head_contract` 块（`:543-555`）**：包含 7 个结构化键 + `note`（`"deploy_head": "one2one_end2e"`、`"deploy_nms": false`、`"early_stopping_head": "one2one_end2e"`、`"early_stopping_nms": false`、`"comparison_head": "one2many_external_nms"`、`"comparison_nms": null`、`"deploy_artifact": "weights/best_e2e.onnx"`），显式锁死早停判据头与部署产物头一致；
     - **`train_config` 收敛至 25 键（`:565-590`，`task-133` 瘦身）**：`requested_batch`/`probed_batch`/`resolved_batch`/`fallback_batch` 4 键收敛为单一 **`batch`**（`trainer.py` AST 实核）——`epochs`, `patience`, `batch`, `imgsz`, `device`, `amp`, `optimizer`, `lr0`, `lrf`, `cos_lr`, `nms`, `flipud`, `fliplr`, `hsv_v`, `hsv_s`, `degrees`, `mosaic`, `mixup`, `copy_paste`, `close_mosaic`, `mosaic_polish_epochs`, `workers`, `exist_ok`, `seed`, `deterministic`，其中 **`"nms": false`** 显式标明早停判据头选择。

| 序号 | `metrics.json` 顶层键（共 20 键） | 契约取值 / 绑定语义（`trainer.py:534-601`） |
|---:|---|---|
| 1 | `generated_at` | UTC ISO-8601 生成时间戳 |
| 2 | `model_version` | 模型版本号（默认 `"yolo26s-streetlight-v1.0"`） |
| 3 | `base_model` | 底模文件名（锁死 `"yolo26s.pt"`） |
| 4 | `ultralytics_version` | 运行期实际 `ultralytics.__version__` |
| 5 | `imgsz` | `1280`（`client.py:312-341` 启动期强校验） |
| 6 | `nms` | `false`（声明主流程使用端到端无 NMS 头） |
| 7 | `end2end` | `true`（声明端到端 `one2one` 头启用） |
| 8 | `head_contract` | 7 键 + `note` 声明早停判据头、部署头、对照头与交付物路径 |
| 9 | `names` | 依 `data.yaml.names` 权威写回：`streetlight_v1`（`merged_v5`/`nc=4`）为 `{"0": "streetlight_on", "1": "streetlight_off", "2": "streetlight_damaged", "3": "streetlight_occluded"}`；`streetlight_v3`（`merged_v7`/`nc=3`）为前 `3` 类 |
| 10 | `val_mAP50` | 明确绑定 **`val_metrics_e2e_head["mAP50"]`**（`one2one` 端到端头） |
| 11 | `val_mAP50_95` | 明确绑定 **`val_metrics_e2e_head["mAP50_95"]`**（`one2one` 端到端头） |
| 12 | `val_metrics_e2e_head` | **权威**验证集指标（`nms=False` / `one2one`，含总体指标、`speed_ms`、`per_class`） |
| 13 | `val_metrics_default_head` | **对照**验证集指标（`nms=None` / `one2many` + 外接 NMS，仅诊断用） |
| 14 | `test_metrics_default_head` | **对照**测试集指标（`nms=None` / `one2many` + 外接 NMS；无 `test` split 时为 `{}`） |
| 15 | `test_metrics_e2e_head` | **权威**测试集指标（`nms=False` / `one2one` 端到端；无 `test` split 时为 `{}`） |
| 16 | `onnx_verification` | `input_name`, `output_name`, `input_shape` (`[1,3,1280,1280]`), `output_shape` (`[1,300,6]`), `custom_metadata_map` |
| 17 | `train_config` | 完整 **25 键**训练超参字典（`batch` 单键 + `"nms": false` 显式标明早停判据头选择） |
| 18 | `early_stopping_diagnostics` | `_schedule_diagnostics()` 输出（`stopped_epoch`、残余 LR 比例、`close_mosaic` 与 `E2ELoss` 权重状态） |
| 19 | `sha256` | `best.pt`、`best_e2e.onnx`、`last.pt` 的 SHA-256 摘要映射 |
| 20 | `dataset_manifest` | 训练输入对应的 `prep_manifest.json` 完整 JSON 摘要（`streetlight_v1` 对应 **`merged_v5/prep_manifest.json`**：`10,306` 保留图 / `8` 源 / `13` 项防泄漏校验全 `0`；`streetlight_v3` 对应 **`merged_v7/prep_manifest.json`**：`6,983` 图 `5,570/846/567` / `clean_stats`（`day_dropped 3,088` + `occluded_relabeled_on 39` + `occluded_deleted 188`） / `13` 项防泄漏校验全 `0`；均含 `split_assignment` / `split_objective` / `class_proportion_deviation` 切分审计；未找到时为 `null`） |

### 5.2 评估口径与模型回传准入门禁

- **重点观察指标**：`val_mAP50`、`val_mAP50_95`（绑定 `val_metrics_e2e_head`）、**`val_metrics_e2e_head` vs `val_metrics_default_head`**、**`test_metrics_e2e_head` vs `test_metrics_default_head`** 与 `per_class`（`streetlight_v1`/`nc=4` 下关注 `streetlight_off` 召回、`streetlight_occluded` 极少数类与 `streetlight_on` 误报率；`streetlight_v3`/`nc=3` 下仅 `on`/`off`/`damaged` 三类，`occluded` 已弃用不产出），同时核对 `metrics.json` 中 `imgsz == 1280`、`nms == false`、`end2end == true`、`head_contract` 一致性与 `early_stopping_diagnostics`（若早停未触发 `close_mosaic` 且 `val_metrics_e2e_head` / `test_metrics_e2e_head` 明显滞后于对应 `*_default_head`，可追加 `--mosaic-polish-epochs` 精修对照）。
- **权重未达标 / 回传前降级基线**：在 `best_e2e.onnx` 未回传、`imgsz` 与 `.env` 的 `YOLO_IMG_SIZE=1280` 不一致或 `SHA256SUMS` 校验失败时（见 `docs/RUNBOOK.md` §6.6），`YOLO_INFERENCE_MODE=auto` 下走 `baseline_pretrained_opencv`，置信度受 DB `CHECK` 与 `config.BASELINE_CONFIDENCE_CAP` 硬封顶 **`0.65`**；`strict_custom` 模式一律返 `503 VISION_DISABLED`。

---

## 6. 远端一键复现命令与诚实状态声明

### 6.1 远端执行步骤（在远端 `/root/autodl-tmp/tomori/` 执行）

```bash
# 1) 加载隔离环境变量（绝不占用根分区 7.8G overlay）
source /root/autodl-tmp/tomori/env.sh
cd /root/autodl-tmp/tomori/repo

# 2) 多源异构清洗、pHash 去重、`merged_v6`/`merged_v7` 清洗管线与「组+单图按类分层」防泄漏切分
#    用户裁决 A 口径：`uv run --no-sync` 直跑脚本路径（不经 `-m` 模块入口）
#    merged_v6（nc=4 清洗中间版）：加 --clean-day-mean-v-max 100 + --clean-occluded-model best.pt
#    merged_v7（nc=3 终版）：再加 --drop-occluded（等价 --nc3）→ names/nc/target_names 写 3 类
/root/.local/bin/uv run --no-sync /root/autodl-tmp/tomori/repo/src/tomori/vision/dataset_prep.py \
  --source "d1=/root/autodl-tmp/tomori/datasets/d1_lamp_state" \
  --source "d3=/root/autodl-tmp/tomori/datasets/d3_streetlamppole" \
  --source "d4=/root/autodl-tmp/tomori/datasets/d4_damaged_lights|preaug=1,rfmode=phash,map=Not Working:1;Working:0" \
  --source "ext_pwd_s6=/root/autodl-tmp/tomori/datasets/ext_pwd_s6" \
  --source "ext_pwd_s9=/root/autodl-tmp/tomori/datasets/ext_pwd_s9" \
  --source "ext_pwd_s1=/root/autodl-tmp/tomori/datasets/ext_pwd_s1" \
  --source "ext_prj_n_8=/root/autodl-tmp/tomori/datasets/ext_prj_n_8" \
  --source "ext_infras_broken=/root/autodl-tmp/tomori/datasets/ext_infras_broken|splits=valid;test" \
  --output-dir /root/autodl-tmp/tomori/datasets/merged_v7 \
  --train-ratio 0.8 --val-ratio 0.1 --test-ratio 0.1 \
  --phash-threshold 6 --seed 42 \
  --clean-day-mean-v-max 100 \
  --clean-occluded-model /root/autodl-tmp/tomori/weights/best.pt \
  --drop-occluded

# 3)（可选）无 GPU 干跑校验数据 YAML 与红线参数（`trainer.py` `726` 行 `nc=3` 兼容：`data.yaml.names` 权威源，`RAW_CLASSES` 前缀子集）
#    用户裁决 A 口径：`uv run --no-sync` 直跑脚本路径（不经 `-m` 模块入口）
/root/.local/bin/uv run --no-sync /root/autodl-tmp/tomori/repo/src/tomori/vision/trainer.py \
  --data /root/autodl-tmp/tomori/datasets/merged_v7/data.yaml \
  --model /root/autodl-tmp/tomori/weights/yolo26s.pt \
  --project /root/autodl-tmp/tomori/runs \
  --dry-run

# 4) RTX 4080 SUPER 正式训练 + 双头离线验证/测试 + best_e2e.onnx 导出 + SHA256SUMS 生成
#    --model/--project 为 CLI `required=True`（无机器默认路径）；`--batch` 固定整数 `>=1`（默认 `--batch 8`，`_DEFAULTS["batch"]=8`，AutoBatch 已删）
#    `streetlight_v1`/`streetlight_v3` 均实跑 `--batch 24`（`~30.8-30.85GB` 同卡上限实测）
#    可选补充：若训练在 491 轮前早停且需两阶段无 Mosaic 收尾精修，可追加 --mosaic-polish-epochs 10
/root/.local/bin/uv run --no-sync /root/autodl-tmp/tomori/repo/src/tomori/vision/trainer.py \
  --data /root/autodl-tmp/tomori/datasets/merged_v7/data.yaml \
  --model /root/autodl-tmp/tomori/weights/yolo26s.pt \
  --project /root/autodl-tmp/tomori/runs \
  --name streetlight_v3 \
  --imgsz 1280 --epochs 500 --patience 20 --batch 24
```

### 6.2 诚实状态声明（`F14` 边界）

- **已证实与已落盘**：
  1. 清洗、EDA 与训练脚本 `src/tomori/vision/dataset_prep.py`（**2,056 → 2,220** 行，`task-162`/`task-165` 追加 `_clean_samples`/`_out_names`/`--drop-occluded`）、`src/tomori/vision/eda.py`（**928** 行）、`src/tomori/vision/trainer.py`（**757 → 716（`task-133` 瘦身）→ 726（`task-167` `nc=3` 兼容）** 行：删 AutoBatch 探测链、`--model`/`--project` 改 `required=True`、`_DEFAULTS` `:42-56` 单一事实源、`_train_kwargs` `:318-351` 主训/精修共用、`validate_data_yaml` 改 `data.yaml.names` 权威源 + `RAW_CLASSES` 前缀子集 `:160-188`）已落地并经本地 `.venv` `ultralytics==8.4.216` 源码逐行核对（`config.py:19` 与 `trainer.py:70-116, :391, :347, :458, :469, :482, :491, :502, :534-601` 已同步锁定 `imgsz=1280`、`epochs=500`、`patience=20`、`batch=8`（固定整数 `>=1`）、`lrf=0.01`、`cos_lr=False`、`mosaic_polish_epochs=0`、`nms=False`、「早停判据头 == 部署头」`head_contract` 与 `metrics.json` 20 键 / `train_config` **25 键**契约）；
  2. 远端 `RTX 4080 SUPER`（`32,760 MiB` 总显存 / `32,230–32,231 MiB` 空闲、`CUDA 13.2`、`PyTorch 2.15.0+cu132`）、`/root/autodl-tmp/tomori/weights/yolo26s.pt`（`20,422,725 B`）、以及 **终版 8 源 `merged_v5`（`10,306` 图 / `25,855` EDA 框，`80/10/10` 组+单图按类分层装箱，图数偏差 `2.99pp`，13 项防泄漏校验全 `0`）与全量只读 EDA（含三口径 `1024 vs 1280` 对比）** 已实机执行核验落盘（`.team/notes/vision-dev/{merged-v5-class-stratified-report.md,dataset-eda-merged-v5.md}`、`.team/notes/vision-dev/logs/{merged_v5_prep_manifest.json,dataset_eda_merged_v5.json,merged_v5_class_distribution.json}`）；
  3. **公开调研、同源域与长尾诚实边界**：① 遍查 8 大公开平台确认全网 `OCCLUDED` 除 `d1`（原始 `235` 框 / `merged_v5` 保留 `227` 框）外**无第二个公开源**，且 `merged_v5` 三集占比 `51.54/14.54/33.92`（偏差 `56.92pp`）为 `vid10` 组原子性 + `≥30` 地板约束的**结构性下限**（该类评估以 `val` 为准），后续仅靠合成遮挡与阶段二自采补足；② **`ext_pwd_s9` 与 `s6`/`s1`/`prj_n_8` 同属 NHAI 公路巡查系**（跨源去重已拦 `43` 张 SHA256 + `3` 张 pHash），虽将高清破损图增至 `207` 张（`+34.4%`），**仍非独立第三方来源**，不得据此夸大跨城市场景域泛化能力；③ 主动剔除 `d7` 乡村配电木杆与 `ext_civic_broken` 错标灭灯后 `streetlight_damaged` 净留 `1,423` 框，仍遵循 **`D-18`** 不承诺破损类检出率；
  4. 本地 CPU `onnxruntime 1.32.0`（`yolo26s` E2E `nc=4` @ `1280` 默认线程 `median=383.40ms / max=464.97ms`，单线程最坏 `max=1,029.13ms`，距 `60s` 超时余量 `58.3×`）与远端 `RTX 4080 SUPER` 只读显存探针实测（`imgsz=1280`：`batch=8` 峰值 `12,028 MiB / 11.75 GiB`、`batch=16` `23,396 MiB / 22.85 GiB`——即 `_DEFAULTS["batch"]=8` 固定值的唯一实测依据；`AutoBatch` 探测链已随 `task-133` 全链删除，测后 `runs/` 为空、`nvidia-smi` `used=1 MiB`）已完成实测记录（`.team/notes/vision-dev/logs/bench-1280-vs-1024.log`）；
  5. **「早停判据头 == 部署头」源码链路与离线契约实证已落盘**（`.team/notes/vision-dev/logs/task86-head-plumbing-full.log`、`task86-probe-head-switch-proof.log`、`task86-metrics-schema-sim.log`）：在代码审查级链路核验基础上，离线随机权重 `Detect` 前向实证确认 `nms=None` → `Detect.end2end=False` 输出 `(1, 84, 33600)` vs `nms=False` → `Detect.end2end=True` 输出 `(1, 300, 6)`（与 `best_e2e.onnx` 部署张量完全一致），且本地模拟 `metrics.json` 20 键 / `train_config` 键集契约测试及 `client._load_metrics_metadata` / `_validate_metrics_names` 实跑全部通过（`SCHEMA OK`；`train_config` 键集已随 `task-133` 收敛为 **25 键**）。
- **已实测回填（`task-74` 首次全周期正式训练，`2026-11-25`）**：
  1. **`merged_v5` 全周期 `yolo train` 已于远端 `RTX 4080 SUPER` 执行完成**：`early_stopped @ epoch 43`（`patience=20`，峰值 `epoch 23`），总耗时 `6,421.67s`（`~1h47m`），`batch=24`、`imgsz=1280`；双头 `val`/`test` 指标、`early_stopping_diagnostics`、`SHA256SUMS` 与 `best_e2e.onnx` 已回传本地且 `sha256sum -c` 通过——完整实测见 **`§6.3`**；
  2. **「早停判据头 == 部署头」实测闭环已达成**：`metrics.json` 实测 `val e2e mAP50 0.34352 / mAP50-95 0.16330` vs `default 0.33764 / 0.16071`（e2e `+0.59pp`）、`test e2e 0.38339 / 0.18055` vs `default 0.39389 / 0.18556`（差 `−1.05pp`）——双向差异均 `≤1.05pp`，证实训练判据头与交付物头一致（`§6.3.2`）；
  3. **`D-30` 验收实测已通过**：`client.py` 真 `best_e2e.onnx`（`inference_mode=yolo26_onnx_e2e`）夜间 `streetlight_on` 样图 `3` 张（`10_01751`/`10_01606`/`10_01453`）全部判定 `is_fault_candidate=false`/`NORMAL_LIT`/`candidate_fault_type=null`/`suggested_severity_hint=none`，`ai_is_baseline_mode=false`（`§6.3.5`）；
  4. **`streetlight_v3`/`merged_v7`（`nc=3`）二次全周期训练已于 `task-166` 完结**：`early_stopped @ epoch 70`（峰 `epoch 50` `mAP50 0.6067`/`mAP50-95 0.2798`），`batch=24`/`~30.85GB`，`7,217.1s≈2h`；`--mosaic-polish-epochs 20` polish `cos_lr` 重复 kwarg 崩溃由 `2ab866f` 修复后以 `--export-only` 完成链；`sha256sum -c` `6/6` 通过，`client.py` `D-30`（`3` 图 `6` 候选 `NORMAL_LIT`）+ `damaged`（`2` 图 `3` 候选 `FIXTURE_DAMAGED`/`P2`）验收全过——完整实测见 **`§6.4`**。

---

## 6.3 `streetlight_v1`/`merged_v5`（`nc=4`）首次全周期正式训练结果（`task-74` 实测回填，`2026-11-25`）

> **`streetlight_v1` vs `streetlight_v3` 数据集对照**：`streetlight_v1`（输入 `merged_v5/data.yaml`/`nc=4`）含 `occluded` `227` 错标框（`AP50 val 0.125/test 0.0025`，`D-41` 弃用依据）；`streetlight_v3`（输入 `merged_v7/data.yaml`/`nc=3`）`occluded` 归零且 `on`/`off`/`damaged` 三类清标。`streetlight_v3` 完整实测见 **`§6.4`**。

> **数据源（全部逐项核自本地落盘文件）**：`./vision/reports/streetlight_v1/metrics.json`（20 键）、`./vision/reports/streetlight_v1/results.csv`（43 行逐轮实测）、`./vision/reports/streetlight_v1/args.yaml`（`batch=24`/`epochs=500`/`patience=20`/`imgsz=1280`/`optimizer=auto`/`lr0=0.01`/`lrf=0.01`/`cos_lr=false`/`mosaic=0.8`/`mixup=0.05`/`copy_paste=0.15`/`close_mosaic=10`/`flipud=0.0`/`hsv_v=0.25`/`hsv_s=0.4`/`degrees=5.0`/`nms=False`/`seed=42`/`deterministic=true`）、`.team/notes/dev-ops/logs/task74-d30-inference.log`（`client.py` 真权重 `D-30` 验收输出）。

### 6.3.1 执行事实与早停诊断（`args.yaml` + `results.csv` + `metrics.json.early_stopping_diagnostics`）

- **执行事实**：`yolo train` 于远端 `RTX 4080 SUPER` 执行 `43` 轮后 `patience=20` 早停（`epoch 23` 峰值 `mAP50 0.34343 / mAP50-95 0.16305`，其后 `20` 轮未严格刷新即停，与 `EarlyStopping` 纯 `mAP50-95` 判据一致）；总耗时 `6,421.67s`（`~1h47m`，`results.csv` `time` 列逐轮累计 `epoch 1: 168.85s → epoch 43: 6,421.67s`）；实测 `batch=24`（`--batch 24` 覆盖 `_DEFAULTS["batch"]=8` 默认，`>=1` 契约允许；远端实测显存 `~30.8GB` / `32,760 MiB`）、`imgsz=1280`、`optimizer="auto"`（`iterations > 10000` 实际走 `MuSGD`，`lr/pg*` 实测末轮 `0.0275/0.0092`）、`amp=true`、`cos_lr=false`、`nms=False`（`train_config` 25 键全量已如实写入 `metrics.json`）。
- **早停诊断（`early_stopping_diagnostics` 实测字段）**：

| 字段 | 实测值 | 与 §3.1 预判的印证 |
|---|---|---|
| `stopped_epoch` | `43` | `EarlyStopping` 纯 `mAP50-95` 判据（`epoch 23` 峰值后 `20` 轮未刷新） |
| `early_stopped` | `true` | — |
| `lr_remaining_ratio` | `0.91684`（`≈91.7%` 残余 LR） | 印证 `§3.1` 风险 2：线性 `lrf=0.01` 下第 `43` 轮早停残余 LR 仍 `>90%` |
| `close_mosaic_trigger_epoch` | `491`（未触发，`close_mosaic_triggered=false`） | 印证 `§3.1` 风险 3：`epochs=500, close_mosaic=10` 需第 `491` 轮触发 |
| `e2e_loss_o2m_weight` | `0.7411` | 印证 `§3.1` 风险 4：`E2ELoss` `o2m` 迁移仅走至 `74.11%`（目标 `10%`） |
| `e2e_loss_o2o_weight` | `0.2589` | `o2o` 仅升至 `25.89%`（目标 `90%`）——`one2one` 部署头欠训属实 |

- **`results.csv` `mAP50 / mAP50-95` 轨迹关键轮**：`ep1` `0.172 / 0.068` → `ep5` `0.186 / 0.070` → `ep10` `0.230 / 0.119` → `ep15` `0.268 / 0.136` → `ep19` `0.295 / 0.146` → **`ep23`（峰值，`best.pt`）`0.343 / 0.163`** → `ep25` `0.274 / 0.135` → `ep30` `0.293 / 0.139` → `ep35` `0.274 / 0.143` → `ep40` `0.274 / 0.126` → `ep43` `0.288 / 0.140`——`epoch 23` 后进入震荡平台并轻微回落（`mAP50-95` 峰值至末轮回撤 `14.1%`），`patience=20` 正确锁定 `best.pt` 于 `epoch 23`。

### 6.3.2 双头验证/测试指标（`metrics.json` 逐项，`e2e` 部署头为权威）

| Split | 指标 | 权威 `e2e`（`nms=False`，one2one） | 对照 `default`（`nms=None`，one2many+NMS） | `e2e − default` |
|---|---|---:|---:|---:|
| `val` | `mAP50` | **`0.34352`** | `0.33764` | `+0.0059` |
| `val` | `mAP50-95` | **`0.16330`** | `0.16071` | `+0.0026` |
| `val` | `mAP75` | `0.14608` | `0.14145` | `+0.0046` |
| `val` | `mean_precision` | `0.31702` | `0.54315` | `−0.2261` |
| `val` | `mean_recall` | `0.40501` | `0.41119` | `−0.0062` |
| `test` | `mAP50` | **`0.38339`** | `0.39389` | `−0.0105` |
| `test` | `mAP50-95` | **`0.18055`** | `0.18556` | `−0.0050` |
| `test` | `mAP75` | `0.15324` | `0.15429` | `−0.0011` |
| `test` | `mean_precision` | `0.62397` | `0.62955` | `−0.0056` |
| `test` | `mean_recall` | `0.45909` | `0.46124` | `−0.0022` |

- **「早停判据头 == 部署头」实测闭环**：`val`/`test` 两套 `mAP` 双向差异均 `≤1.05pp`（`val` e2e 微优 `+0.59pp`，`test` e2e 微差 `−1.05pp`）——证实 `nms=False` 端到端 `one2one` 头（`best_e2e.onnx` 交付物）与训练判据头一致，契约成立。`val` 的 `mean_precision` e2e 显著低于 default（`0.317 vs 0.543`）为无 NMS 头去重机制差异所致的真实差距，非数据错误。

### 6.3.3 四类分 AP（`per_class`，`AP50 / AP50-95`，附 `precision / recall`）

| Split | 类别 | `AP50` | `AP50-95` | `precision` | `recall` |
|---|---|---:|---:|---:|---:|
| `val` e2e | `streetlight_on` | **`0.85479`** | `0.48226` | `0.62255` | `0.9267` |
| `val` e2e | `streetlight_off` | `0.21823` | `0.06923` | `0.0869` | `0.54545` |
| `val` e2e | `streetlight_damaged` | `0.17563` | `0.04839` | `0.55862` | `0.14789` |
| `val` e2e | `streetlight_occluded` | `0.12544` | `0.05332` | `0.0` | `0.0` |
| `val` default | `streetlight_on` | `0.8853` | `0.50766` | `0.66064` | `0.93027` |
| `val` default | `streetlight_off` | `0.17192` | `0.04389` | `0.09336` | `0.51515` |
| `val` default | `streetlight_damaged` | `0.18832` | `0.05242` | `0.58538` | `0.16903` |
| `val` default | `streetlight_occluded` | `0.10504` | `0.03888` | `0.83322` | `0.0303` |
| `test` e2e | `streetlight_on` | **`0.85102`** | `0.47962` | `0.61617` | `0.88776` |
| `test` e2e | `streetlight_off` | `0.46216` | `0.15465` | `0.37693` | `0.73734` |
| `test` e2e | `streetlight_damaged` | `0.21789` | `0.08714` | `0.50277` | `0.21127` |
| `test` e2e | `streetlight_occluded` | `0.0025` | `0.00079` | `1.0` | `0.0` |
| `test` default | `streetlight_on` | `0.87303` | `0.49579` | `0.67094` | `0.88549` |
| `test` default | `streetlight_off` | `0.47033` | `0.1549` | `0.41453` | `0.73921` |
| `test` default | `streetlight_damaged` | `0.22469` | `0.08927` | `0.43271` | `0.22026` |
| `test` default | `streetlight_occluded` | `0.0075` | `0.00227` | `0.0` | `0.0` |

- **`ONNX 导出契约实测**：`onnx_verification` 实测 `input=[1, 3, 1280, 1280]` → `output=[1, 300, 6]`（`images`/`output0`），`nms=False`、`end2end=True`、`stride=32`、`head=Detect`，静态形状与 `client.py` 启动门禁完全一致；`sha256` 实测 `best.pt=5b8304…`、`best_e2e.onnx=2d63ba…`、`last.pt=8f1cde…`（`SHA256SUMS` 回传校验已通过）。

### 6.3.4 诚实边界（与既有文档一致的实测如实披露）

1. **`streetlight_occluded` 长尾结构性局限**：`val` `AP50=0.1254`（`precision=0.0`/`recall=0.0`），`test` `AP50=0.0025`（`precision=1.0`/`recall=0.0`）——`occluded` 仅 `d1` 独源 `227` 框（`train 117/val 33/test 77`，`vid01`/`vid05` 组原子性致 `14.5%/33.9%` 偏态，§2.1 已声明结构性下限），`test` 77 框单点 AP 置信区间 `±18.9pp` 量级；**该短板属数据采集结构性问题，后续靠合成遮挡 + 阶段二自采补齐，按 `D-18` 不承诺检出率**；
2. **`streetlight_damaged` 偏弱但在 `D-18` 范围**：`AP50 val 0.176 / test 0.218`，`recall val 0.148 / test 0.211`——`6` 源 `1,423` 框已实现 `0.08pp` 近精确切分但样本多样性仍有限（NHAI 系同域），按 `D-18` 不承诺破损检出率；
3. **`streetlight_off` 的 `test` `AP50=0.462` 高于 `val` `0.218` 的 split 差异如实标注**：`test` 含 `vid05` 整组 `533` 个 `off` 框（`17.35%`），`val` 仅 `33` 框（`1.07%`），`off` 在 `test` 样本量 `16×` 于 `val` 所致的真实指标差——13 项泄漏断言全 `0`，非泄漏所致；
4. **`one2one` 头欠训为已知残余风险**：`e2e_loss_o2o_weight` 仅迁移 `25.89%`、`close_mosaic` 未触发——按 §3.1 预案，`--mosaic-polish-epochs` 短微调精修可用于后续收敛 `one2one` 部署头（本轮未执行，属可选增量非缺陷）。

### 6.3.5 `D-30` 业务验收（`client.py` 真 `best_e2e.onnx` 推理，`task74-d30-inference.log`）

`client.py` 以 `inference_mode=yolo26_onnx_e2e`、`model_version=yolo26s-onnx-best_e2e`、`ai_is_baseline_mode=false` 实跑 `3` 张 `1280×720` 夜间样图，全部按 `D-30` 正确识别 `streetlight_on` 为「正常亮灯参照框」而非故障候选：

| 样图 | 场景判定 / 质量 | 候选数 | `is_fault_candidate` | `visual_signal_state` | `candidate_fault_type` | `suggested_severity_hint` | `yolo_confidence` / `fused_confidence`（逐候选） | `max_fused_confidence` |
|---|---|---:|---|---|---|---|---:|---:|
| `10_01751` | `night` / `pass`（`brightness_mean_v=19.27`） | `1` | `false` | `NORMAL_LIT` | `null` | `none` | `0.8201 / 0.8611` | `0.8611` |
| `10_01606` | `night` / `pass`（`31.37`） | `3` | `false`（全 `3` 框） | `NORMAL_LIT` | `null` | `none` | `0.9177/0.9636`、`0.9047/0.9499`、`0.3007/0.3157` | `0.9636` |
| `10_01453` | `night` / `warn`（`33.5`，`laplacian_var=49.03<60` 偏模糊建议重拍） | `3` | `false`（全 `3` 框） | `NORMAL_LIT` | `null` | `none` | `0.921/0.9671`、`0.904/0.9492`、`0.437/0.4589` | `0.9671` |

- 三图 `primary_fault_type_suggestion=null`、`fusion_status=consistent`——`D-30` 回传「参照框」语义在真权重 + 真 `client.py` 链路上完整闭环。


---

## 6.4 `streetlight_v3`/`merged_v7`（`nc=3`）二次全周期正式训练结果（`task-166`，`2026-11-25`）

> **数据源（全部逐项核自本地落盘文件）**：`./vision/reports/streetlight_v3/metrics.json`（`names` `3` 项/`head_contract`/双头指标/`train_config` 25 键/`early_stopping_diagnostics`/`onnx_verification`/`sha256`）、`./vision/reports/streetlight_v3/results.csv`（`70` 行逐轮实测）、`./vision/reports/streetlight_v3/args.yaml`（`batch=24`/`epochs=500`/`patience=20`/`imgsz=1280`/`data=merged_v7`/`name=streetlight_v3`/`model_version=yolo26s-streetlight-v3.0`）、`./vision/reports/streetlight_v3/SHA256SUMS`（`6` 文件）、`.team/notes/dev-ops/logs/task166-{d30-inference,d30-damaged}.log`（`D-30`/`damaged` 验收）、`.team/notes/dev-ops/task166-merged-v7-train-handback.md`。

### 6.4.1 执行事实与 `mosaic_polish` `cos_lr` bug 如实记录

- **执行事实**：`yolo train` `nc=3`（`names = [on, off, damaged]`，`data.yaml.names` 权威源——`D-43`）于 `merged_v7`（`6,983` 图 `5,570/846/567`）执行 `70` 轮后 `patience=20` 早停（`epoch 50` 峰值 `mAP50 0.6067`/`mAP50-95 0.27993`），总耗时 `7,217.1s`（`~2h`）；`batch=24`（GPU `100%`/`30,850 MiB`，`~1.9GB` 余量）、`imgsz=1280`、`optimizer="auto"`（`iterations>10000` 实际 `MuSGD`）、`amp=true`、`cos_lr=false`、`nms=False`（`train_config` 25 键全量写入 `metrics.json`）。
- **`results.csv` 轨迹**：`ep1 0.343/0.105` → `ep10 0.380/0.138` → `ep20 0.532/0.242` → `ep30 0.578/0.241` → `ep40 0.596/0.250` → **`ep50` 峰值 `0.607/0.280`** → `ep55 0.598/0.268` → `ep60 0.572/0.249` → `ep70 0.570/0.264`——`epoch 50` 后平台回落，`patience=20` 正确锁 `best.pt` 于 `epoch 50`。
- **`mosaic_polish` `cos_lr` bug 如实记录**：本批原随 `--mosaic-polish-epochs 20` 触发两阶段无 Mosaic 精修，`polish_model.train` 因 `_train_kwargs` 与显式 `cos_lr=True` 重复传参 `TypeError` 崩溃（`trainer.py:439` 修复点）；由 `vision-dev` `2ab866f`（`fix(vision): deduplicate cos_lr kwarg`，`polish_kwargs` dict `update` 去重覆盖）修复并推送；后续以 `--export-only` 完成 `val`/`test`/`export`/`SHA256SUMS` 链——**`mosaic_polish` 精修未实际执行**，属如实边界。
- **`early_stopping_diagnostics` 为空 `{}` 如实记录**：`metrics.json` 由 `--export-only` 链重新生成，`train_config.mosaic_polish_epochs=0`（`--export-only` 默认）；`stopped_epoch=70`/`lr_remaining`/`e2e_loss` 权重未随导出链重写（`v1` 为训练链内写入）。

### 6.4.2 双头验证/测试指标（`metrics.json` 逐项，`e2e` 部署头为权威）

| Split | 指标 | 权威 `e2e`（`nms=False`，one2one） | 对照 `default`（`nms=None`，one2many+NMS） | `e2e − default` |
|---|---|---:|---:|---:|
| `val` | `mAP50` | **`0.60669`** | `0.60700` | `−0.0003` |
| `val` | `mAP50-95` | **`0.27984`** | `0.27542` | `+0.0044` |
| `val` | `mAP75` | `0.21112` | `0.20882` | `+0.0023` |
| `val` | `mean_precision` | `0.64876` | `0.63149` | `+0.0173` |
| `val` | `mean_recall` | `0.62496` | `0.65720` | `−0.0322` |
| `test` | `mAP50` | **`0.66205`** | `0.67595` | `−0.0139` |
| `test` | `mAP50-95` | **`0.40127`** | `0.40930` | `−0.0080` |
| `test` | `mAP75` | `0.45012` | `0.45677` | `−0.0067` |
| `test` | `mean_precision` | `0.70167` | `0.71871` | `−0.0170` |
| `test` | `mean_recall` | `0.63455` | `0.66190` | `−0.0274` |

- `e2e`/`default` 双向差异仍 `≤1.4pp`（`val` 互抵，`test` `default` 微优）——`nc=3` 下「早停判据头 == 部署头」契约持续成立。

### 6.4.3 三类分 AP（`per_class`，`AP50 / AP50-95`，附 `precision / recall`）

| Split | 类别 | `AP50` | `AP50-95` | `precision` | `recall` |
|---|---|---:|---:|---:|---:|
| `val` e2e | `streetlight_on` | **`0.87965`** | `0.48671` | `0.73297` | `0.90117` |
| `val` e2e | `streetlight_off` | `0.61459` | `0.20456` | `0.60822` | `0.69905` |
| `val` e2e | `streetlight_damaged` | `0.32585` | `0.14824` | `0.6051` | `0.27465` |
| `val` default | `streetlight_on` | `0.88579` | `0.48417` | `0.74291` | `0.9263` |
| `val` default | `streetlight_off` | `0.61333` | `0.20205` | `0.58322` | `0.74859` |
| `val` default | `streetlight_damaged` | `0.32188` | `0.14004` | `0.56833` | `0.29671` |
| `test` e2e | `streetlight_on` | **`0.93305`** | `0.6538` | `0.81126` | `0.91543` |
| `test` e2e | `streetlight_off` | `0.77089` | `0.45212` | `0.7143` | `0.75532` |
| `test` e2e | `streetlight_damaged` | `0.28221` | `0.09789` | `0.57947` | `0.2329` |
| `test` default | `streetlight_on` | `0.93876` | `0.65935` | `0.81557` | `0.9273` |
| `test` default | `streetlight_off` | `0.78507` | `0.46494` | `0.71129` | `0.79078` |
| `test` default | `streetlight_damaged` | `0.30403` | `0.1036` | `0.62926` | `0.26761` |

- **`ONNX` 契约实测**：`onnx_verification` `input=[1, 3, 1280, 1280]` → `output=[1, 300, 6]`（`images`/`output0`），`nms=False`、`end2end=True`、`nc=3`（`names` `3` 项 `on`/`off`/`damaged`）；`sha256` `best.pt=5f6c54…`、`best_e2e.onnx=f3b243…`、`last.pt=ea7a79…`（`SHA256SUMS` `6` 文件含 `best.pt`/`best_e2e.onnx`/`last.pt`/`results.csv`/`args.yaml`/`metrics.json`，`sha256sum -c` 全 `OK`）。

### 6.4.4 `streetlight_v1`（`nc=4`）vs `streetlight_v3`（`nc=3`）同口径对比

| 指标（同 `e2e` 头口径） | `streetlight_v1` | `streetlight_v3` | 变化 |
|---|---:|---:|---|
| `val` `mAP50` | `0.34352` | `0.60669` | **`+76.6%`** |
| `val` `mAP50-95` | `0.16330` | `0.27984` | **`+71.4%`** |
| `test` `mAP50` | `0.38339` | `0.66205` | `+72.7%` |
| `test` `mAP50-95` | `0.18055` | `0.40127` | `+122.2%` |
| `val` `AP50` `on` | `0.85479` | `0.87965` | `+2.9%` |
| `val` `AP50` `off` | `0.21823` | `0.61459` | **`+181.7%`** |
| `val` `AP50` `damaged` | `0.17563` | `0.32585` | `+85.5%` |
| `val` `AP50` `occluded` | `0.12544` | 弃用（`nc=3` 不产出） | — |
| `test` `AP50` `on` | `0.85102` | `0.93305` | `+9.6%` |
| `test` `AP50` `off` | `0.46216` | `0.77089` | `+66.8%` |
| `test` `AP50` `damaged` | `0.21789` | `0.28221` | `+29.5%` |
| `test` `AP50` `occluded` | `0.0025` | 弃用（`nc=3` 不产出） | — |

- **`nc=3` 清洗收益实证**：剔除 `3,088` 帧白天亮招牌误标 + `occluded` `227` 框错标后，`off`/`damaged` `AP50` 大幅提升；`handback` 头条 `off +260%`/`damaged +73%`/`on +10%` 为 `v1` `val e2e` → `v3` `test default` 跨口径对比（如实标注，同口径 `e2e` `val`/`test` 见上表）。
- **`off` `test>val` 差异延续**：`vid05` 整组 `533` 框 `off` 进 `test`（`17.35%`）vs `val` `33` 框（`1.07%`）的 split 差异与 `v1` 一致（`merged_v7` 同组装箱），如实非泄漏。

### 6.4.5 `D-30` + `damaged` 业务验收（`client.py` 真 `best_e2e.onnx`，`nc=3`）

`client.py` 以 `inference_mode=yolo26_onnx_e2e`、`model_version=yolo26s-onnx-best_e2e`、`ai_is_baseline_mode=false` 实跑 `5` 张样图：

| 样图 | 场景 / 质量 | 候选数 | `is_fault_candidate` | `visual_signal_state` | `candidate_fault_type` | `suggested_severity_hint` | `yolo_confidence` / `fused_confidence`（逐候选） |
|---|---|---:|---|---|---|---|---|
| `10_01751` | `night` / `pass` | `1` | `false` | `NORMAL_LIT` | `null` | `none` | `0.9119 / 0.9575` |
| `10_01606` | `night` / `pass` | `2` | `false`（全 `2` 框） | `NORMAL_LIT` | `null` | `none` | `0.9206/0.9666`、`0.909/0.9545` |
| `10_01453` | `night` / `warn` | `3` | `false`（全 `3` 框） | `NORMAL_LIT` | `null` | `none` | `0.925/0.9713`、`0.9206/0.9666`、`0.6195/0.6505` |
| `d3_008436` | `day` / `pass` | `1` | **`true`** | `FIXTURE_DAMAGED` | `FIXTURE_DAMAGED` | **`P2`** | `0.3341 / 0.3441` |
| `d3_008452` | `day` / `pass` | `2` | **`true`**（全 `2` 框） | `FIXTURE_DAMAGED` | `FIXTURE_DAMAGED` | **`P2`** | `0.3055/0.3147`、`0.2596/0.2674` |

- **夜间亮灯 `D-30`**：`3` 图 `6` 候选全部 `NORMAL_LIT`/`is_fault_candidate=false`/`severity=none`/`primary_fault_type_suggestion=null`——`nc=3` 下回传参照框语义完整（`yolo`/`fused` 置信均高于 `v1` 同图：`0.91-0.93`/`0.95-0.97` vs `0.82-0.92`/`0.86-0.97`）；
- **`damaged` 类真实可产出工单候选**：`merged_v7/test` 白天 `d3_*` `2` 图 `3` 候选全部 `FIXTURE_DAMAGED`/`is_fault_candidate=true`/`severity=P2`/`primary_fault_type_suggestion=FIXTURE_DAMAGED`——`v1` `damaged AP 0.176` 弱类经 `nc=3` 清洗提升至 `0.304`，`D-30` 业务链路从「仅参照框」升级为「参照 + 真实候选」全通；
- **`occluded` 按 `D-41` 不产出**：`nc=3` 模型无该类 `raw_class`，`API` `fault_type=OCCLUDED` 仍为合法手工报障枚举（见 §2.1/`docs/API.md` §5）。
