# 共享测试输入（tests/fixtures/）

本目录归拢跨 `unit`/`integration`/`e2e` 共用的**静态**测试输入（二进制媒体样图与负向媒体样本）。程序化生成器（运行时 `PIL` 出图）仍在 `tests/integration/conftest.py`，本目录放可审计的静态副本。

## 清单

| 文件 | 类型 | 用途 |
|---|---|---|
| `valid_static.png` | PNG 902B | `make_static_image` 等价的静态有效图（`160×160`，对角线调色板）——上传/媒体正路径通用输入 |
| `night_normal_lit.png` | PNG 1.4KB | `make_night_normal_lit_png` 等价：夜景亮灯参照（`streetlight_on → NORMAL_LIT`，`D-30` `is_fault_candidate=false`） |
| `occluded_fault.png` | PNG 1.4KB | `make_occluded_fault_png` 等价：绿植遮挡类故障输入（`streetlight_occluded`/`is_fault_candidate=true`） |
| `minimal_40mp.png` | PNG 67B | 仅含 `IHDR`/`IDAT`/`IEND` 的合法 PNG，`IHDR` 声明 `8000×5000`（40MP）——`make_minimal_png_with_dimensions` 等价的超像素上限守护输入 |
| `corrupt_truncated.jpg` | JPEG 头 36B | 截断 JPEG（仅 `FF D8 FF E0` + 垫零）——解码失败的负向媒体输入 |
| `sample.svg` | SVG 154B | 矢量非位图输入——扩展名/MIME 白名单负向用例（`ALLOWED_IMAGE_EXTENSIONS` 不含 svg） |
| `sample.mp4` | MP4 头 56B | `ftyp` 视频容器头——非图像扩展名负向用例（守 `N1`：不接受视频） |
| `d30_samples/` | JPG+txt | 远端 D1 实拉 3 张夜间 `lamp_on` 样图（`10_01751`/`10_01606`/`10_01453`，`mean_v=19–33`），供 `client.py` 实权重 `D-30` 契约验收 |

## 与程序化生成器的关系

- 本目录静态文件**只读**，供无需 `PIL` 上下文的测试或文档审计直接引用；
- 运行时生成器（`make_static_image`/`make_night_normal_lit_png`/`make_occluded_fault_png`/`make_animated_bytes`/`make_minimal_png_with_dimensions`）仍是集成/E2E 的权威输入，本目录文件与它们保持同口径；
- `d30_samples/` 为 `task-166` 实权重验收留存的样图副本（原始位于 `./vision/reports/streetlight_v1/d30_samples/`，gitignored 运行时产物；此处为固化副本）。

## 使用约定

- **新增共享输入请先落此目录并更新本表**，避免在 `conftest.py` 内嵌散件；
- 密钥/令牌类敏感值一律 `__PLACEHOLDER__`；`d30_samples/*.txt` 为配套 YOLO 标签（类 `0`=`lamp_on`）。
