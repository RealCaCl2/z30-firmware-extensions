# v32：加深遮幅

日期：2026-10-18。**中间构建，已被 [v33](aspect-soft-mask-v33.md) 替代，不作为本次交付。** 用户在 25% 的调整提议后要求继续加深。

用户已在机身确认 v31：遮幅边界与成片一致，关闭网格后仍有遮幅，网格线与原厂图标正常；同时反馈遮幅偏浅。v32 将黑色不透明度从 `48/255`（约 19%）提高到 `64/255`（约 25%），供进一步试用。

## 产物及范围

- 模块：`output/full-aspect-soft-mask-v32/EG209020.BIN`，同目录附构建报告和验证清单。
- SHA-256：`751190124655c8306956c9b06b8b0f1514b0ae14fda83a3f4f98e3b732ecd9f4`
- 大小：33,990,106 字节，CRC residue 0。
- 二进制与 v31 只差两个 `MOVT` 的 alpha 常量及末尾 CRC 两字节；成片、投影、网格、控件保护和提交边界代码均相同。
- 填充颜色和识别既有遮幅的颜色都从同一 `DEFAULT_ALPHA` 生成，避免调整强度后无法识别自身像素。重复绘制仍为替换，不累积变黑。

v31 的具体实现、机身验收项及 QEMU 夹具边界见 [v31 说明](aspect-soft-mask-v31.md)。本轮不向相机或存储卡写入，也不做 commit/push。

## 验证

`build/research/aspect-mask-v32/v31-byte-diff.json` 记录全部四个字节差异，并由独立反汇编确认两个指令仅将高半字从 `0x3000` 改为 `0x4000`。`dynamic.json` 检查全部六个比例、两种源窗口和两种视口，共 24 组 ARM 执行，包含 14 个失败关闭条件、重复填充、透明区域修复与非透明原厂图形保护。14 项 aspect 单元测试通过。

QEMU 的 5:4、XPAN 和关闭三个代表档位背景遮幅及基线恢复检查通过；补充控件采样中，关闭档位的一处对焦括号像素与基线不同，故该轮不作为控件完整通过的证据。v32 已被用户新要求取代，不生成最终交付清单。v31 的七档记录属于 v31，不能冒充 v32 的七档验证。

复跑显示检查：

```bash
python3 tools/verify_mask_menu_qemu.py \
  --qemu-root ../z30-qemu \
  --image output/full-aspect-soft-mask-v32/EG209020.BIN \
  --report output/full-aspect-soft-mask-v32/EG209020.json \
  --fixture build/research/aspect-diagnostic-v6/sd-fixture.json \
  --output-dir build/research/mask-firmware-v32-menu \
  --profiles 2 6 0 --geometry-fixture

python3 tools/verify_aspect_mask_frames.py \
  --image output/full-aspect-soft-mask-v32/EG209020.BIN \
  --evidence build/research/mask-firmware-v32-menu --profiles 2 6 0
```

QEMU 仍使用明确的无传感器视口/源窗口夹具及模型色条。v31 的机身反馈证明上述边界、网格开关与控件行为；v32 的新压暗强度尚待机身观感确认。未新增拍照、回放、HDMI 或全部电源生命周期的实机证明。
