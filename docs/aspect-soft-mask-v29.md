# v29：动态淡黑遮幅试验

日期：2026-10-18。**历史失败候选，不供机身试验。** 七档遮幅背景像素检查通过，但追加的控件检查发现部分原厂图标消失，不能作为完整显示通过。后续改进见主交接最新章节。

目标是以裁切区外轻微压暗取代黄色粗框，保留原厂内部网格与状态控件。本版是待机身验证的 EG 模块，QEMU 结果不代表相机验证。

## 固定产物

- 模块：`output/full-aspect-soft-mask-v29/EG209020.BIN`
- 构建报告：同目录 `EG209020.json`
- SHA-256：`58d8df18e1311998b7f5a57991e3e9aa70dfb5d3831b51fd1a921ec46d381557`
- 大小：33,990,106 字节；EG CRC residue：0。
- 基于 `full` 显式启用 `aspect_mask=True`；未启用黄色边框或诊断 trace。普通构建保持原路径。这不是 Nikon 外层更新包。

## 实现变化

1. GPU submit 入口 `0x1055D112` 读取 `aspect_get`，使用原厂视口快照 `0xC237C7B4+8` 和源窗口 `0xC237C3D2`。复用 `aspect_frame.projection_instructions()`，不采用固定 LCD 比例表，也不依赖网格记录 `0xC201A7F4`。
2. 在原厂可见视口内计算成片外的至多四个不重叠矩形。4:3、5:4 沿用现有源窗口映射，宽幅沿用居中投影。范围外不写；没有有效状态、视口非法或读取期间参数改变时不绘制。
3. 在独立取景 RGBA 表面写预乘黑 `0x30000000`，alpha 为 48/255。UI 在其上合成；不向 UI 画布叠黑。动态模式只使用私有栈，不向追加 ROM 区写状态，不调用带锁的原厂快照接口。
4. 每次覆盖完整遮幅矩形，避免重复混合加深，也去掉“首末像素相同则跳过”的不可靠优化。关闭/切换后的清除依靠原厂图像表面刷新；需继续验证真实相机生命周期。
5. 遮幅构建关闭 v17 网格通道中的成片外边线，仅保留内部三等分/四等分参考线。网格关闭时遮幅仍独立工作。旧黄色边框不启用。

## 验证及复跑

构建产物已经固定；修改源码后应重新构建并把证据绑定到新 SHA，不能沿用本版结果。

```powershell
python tools/build_aspect_mask_trial.py --source Z_30_0120.bin `
  --output output/full-aspect-soft-mask-v29/EG209020.BIN `
  --deps build/verify-deps --dynamic
```

本版 ARM 执行与几何检查结果在 `build/research/aspect-mask-v29/`：

- `dynamic.json`：24 组动态遮幅、4 个独立预期投影、9 个失败关闭条件；网格记录全零；重复填充不累积，角点不变但内部被改写时仍能修复。执行真实 submit 钩子并检查原 prologue/续接。
- `frame.json`：36 个轴、120 个源窗口/视口/档位/网格组合和 6 个边界条件；遮幅模式仅保留内部线。
- `native.json`：63 个原厂几何组合、36 个网格组合及 JPEG/预览选择路径检查。
- `default-regression.json`：未开启遮幅的 trace 构建与原 v17 产物逐字节相同，SHA 为 `74cef769a20e9efbecb22835f7a343b5299713dd76c1ad3e64b2449467cca9b4`。
- `test_aspect*.py`：14 项单元测试通过。没有运行全部无关功能测试。

QEMU 使用原厂镜像启动后，回放候选模块差字节和追加区；机型的原厂镜像指纹闸门使候选不能直接作为 `-bios` 启动。菜单选择通过原厂焦点/Enter 和父页面返回路径完成。实际 LCD 色条为模型图像，不是传感器输出。

```bash
python3 tools/verify_mask_menu_qemu.py \
  --qemu-root ../z30-qemu \
  --image output/full-aspect-soft-mask-v29/EG209020.BIN \
  --report output/full-aspect-soft-mask-v29/EG209020.json \
  --fixture build/research/aspect-diagnostic-v6/sd-fixture.json \
  --output-dir build/research/mask-firmware-v29-menu-final \
  --profiles 1 2 3 4 5 6 0 --geometry-fixture

python3 tools/verify_aspect_mask_frames.py \
  --image output/full-aspect-soft-mask-v29/EG209020.BIN \
  --evidence build/research/mask-firmware-v29-menu-final
```

`--geometry-fixture` 必须明确记录：辅助无传感器启动后的原厂视口/源窗口全零，因此测试注入视口 `(0,36,720,442)` 与源窗口 `(0,292,5568,3420)`。该输入不是遮幅状态或网格记录，测试不证明真实传感器能正确填入这些字段。原厂网格属性保持关闭；每个启用档位必须有非空遮幅区和实际变暗的亮像素，关闭后必须恢复基线，避免 v27 的空洞通过。

最终 QEMU 结果见本目录配套验证清单及 `build/research/mask-firmware-v29-menu-final/`，须以完整通过记录为准。网格硬件光栅不由模型实现：内部线有 ARM 几何证据，不能宣称已从 QEMU 截图验证原厂网格像素。

## 机身验收与剩余边界

按既有 EG 加载流程试验，首先进入扩展菜单选择 Photo aspect；首次打开菜单之前状态块可能尚未建立，自动恢复尚未证明。依次检查 4:3、5:4、2:1、2.35:1、2.39:1、XPAN、关闭：

- 外区轻微变暗，成片内亮度不变，没有黄框、双线或端帽；左右/上下边界与成片一致。
- 原厂网格关闭后仍有遮幅；开启 3×3/4×4 后只有内部线并与遮幅共存，图标和对焦框清晰。
- 首次设置、切换比例、DISP、菜单/回放往返、拍照、放大对焦、待机唤醒及重启后不残留暗带或挤压；必要时再测 HDMI。
- JPEG 主图、机内普通/放大回放保持正确比例；本轮没有新拍摄验证。

alpha 观感、真实表面所有权/缓存一致性、性能和上述生命周期均未由物理相机验证。表面指针只检查高位地址区间，不能视为完整所有权证明。本轮没有写相机或存储卡。
