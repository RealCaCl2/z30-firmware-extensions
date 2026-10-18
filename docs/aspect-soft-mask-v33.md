# v33：更明显的裁切遮幅

日期：2026-10-18。**中间强度，用户再次要求加深后已由 [v34](aspect-soft-mask-v34.md) 替代，本次不交付。**

用户在机身确认 v31 的遮幅边界与成片一致、网格关闭后仍有遮幅、网格线及原厂图标正常；反馈遮幅偏浅，并在 25% 的调整提议后要求继续加深。v33 采用黑色不透明度 `96/255`，约 38%。

- 模块：`output/full-aspect-soft-mask-v33/EG209020.BIN`
- SHA-256：`9d81cb00002c48473d40b31b4f263a15383589ce95268f7d1527632e97d735eb`
- 大小：33,990,106 字节；CRC residue 0。
- 同目录 `EG209020.json` 为构建报告；本中间强度不生成最终交付清单。

## 变更范围

与机身反馈正常的 v31 相比，模块只差两个 `MOVT` 的不透明度立即数及 CRC 两字节：`0x3000` 改成 `0x6000`，即预乘黑字从 `0x30000000` 改为 `0x60000000`。照片裁切、投影、网格和控件保护路径均逐字节相同。填充颜色与识别已有遮幅的颜色共用 `DEFAULT_ALPHA`，确保重复绘制不累积加深。

`build/research/aspect-mask-v33/v31-byte-diff.json` 保存全部差异及独立反汇编。v32 是 25% 的中间构建，已被本版替代；本次不交付 v32。

## 验证与边界

`build/research/aspect-mask-v33/dynamic.json`：六个比例、两种源窗口、两种视口的 24 组 ARM 执行，以及 14 个失败关闭条件通过；还检查重复填充、透明区域修复和非透明原厂图形保护。

本版 5:4、XPAN、关闭三个代表档位的 QEMU 实际 LCD 像素及 9 个原厂控件前景采样检查通过，结果在 `build/research/mask-firmware-v33-menu/`。因已被用户新要求取代，不生成本次最终交付清单。v31 的七档记录不冒充 v33 的七档验证。

```bash
python3 tools/verify_mask_menu_qemu.py \
  --qemu-root ../z30-qemu \
  --image output/full-aspect-soft-mask-v33/EG209020.BIN \
  --report output/full-aspect-soft-mask-v33/EG209020.json \
  --fixture build/research/aspect-diagnostic-v6/sd-fixture.json \
  --output-dir build/research/mask-firmware-v33-menu \
  --profiles 2 6 0 --geometry-fixture

python3 tools/verify_aspect_mask_frames.py \
  --image output/full-aspect-soft-mask-v33/EG209020.BIN \
  --evidence build/research/mask-firmware-v33-menu --profiles 2 6 0
```

QEMU 使用明确的无传感器视口/源窗口夹具和模型色条，具体边界见 [v31 说明](aspect-soft-mask-v31.md)。v31 已获上述局部机身反馈，v33 的 38% 强度尚待机身观感确认；未新增拍照、回放、HDMI 或全部电源生命周期证明。开发侧没有写相机或存储卡，也未 commit/push。
