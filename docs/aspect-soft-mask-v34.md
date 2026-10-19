# v34：更明显的裁切遮幅

日期：2026-10-18。

用户在机身确认 v31 的遮幅边界与成片一致、网格关闭后仍有遮幅、网格线及原厂图标正常；反馈遮幅偏浅，并在 25% 和 38% 的调整提议后均要求继续加深。v34 采用黑色不透明度 `128/255`，约 50%。

- 模块：`output/full-aspect-soft-mask-v34/EG209020.BIN`
- SHA-256：`9856deb90169035f8ab56935b6531fe01df021a823b51301f4984ffc8a24e29f`
- 大小：33,990,106 字节；CRC residue 0。
- 同目录 `EG209020.json` 为构建报告，`validation.json` 为最终验证记录。

## 变更范围

与机身反馈正常的 v31 相比，模块只差两个 `MOVT` 的不透明度立即数及 CRC 两字节：`0x3000` 改成 `0x8000`，即预乘黑字从 `0x30000000` 改为 `0x80000000`。照片裁切、投影、网格和控件保护路径均逐字节相同。填充颜色与识别已有遮幅的颜色共用 `DEFAULT_ALPHA`，确保重复绘制不累积加深。

`build/research/aspect-mask-v34/v31-byte-diff.json` 保存全部差异及独立反汇编。v32（25%）及 v33（38%）为中间强度构建，已由本版的较高不透明度替代。

## 验证与边界

`build/research/aspect-mask-v34/dynamic.json`：六个比例、两种源窗口、两种视口的 24 组 ARM 执行，以及 14 个失败关闭条件通过；还检查重复填充、透明区域修复和非透明原厂图形保护。

本版 5:4、XPAN、关闭三个代表档位的 QEMU 实际遮幅颜色及关闭恢复检查通过；两处原厂相机/信息图标前景在三帧中的六个采样保持基线值。记录在 `build/research/mask-firmware-v34-menu/`，由产物目录 `validation.json` 绑定 SHA。v31 的七档记录不冒充 v34 的七档验证。

完整 UI 基线比较没有全部通过：5:4 帧底部 `(598,452)` 的白色字段采样与基线不同；该点位于原厂视口 `(0,36,720,442)` 之外，也不在任何遮幅矩形内。原因尚未确定，不能宣称本轮所有 UI 采样通过。此差异和验证器失败均保留在清单；本版与用户实测正常的 v31 的功能代码差异仅为上述两个 alpha 立即数。v32 的另一轮关闭档位也曾观察到底部字段差异，v33 的三帧则全部九点匹配，不应把这种跨轮差异未经调查就归因于 alpha 值。

```bash
python3 tools/verify_mask_menu_qemu.py \
  --qemu-root ../z30-qemu \
  --image output/full-aspect-soft-mask-v34/EG209020.BIN \
  --report output/full-aspect-soft-mask-v34/EG209020.json \
  --fixture build/research/aspect-diagnostic-v6/sd-fixture.json \
  --output-dir build/research/mask-firmware-v34-menu \
  --profiles 2 6 0 --geometry-fixture

python3 tools/verify_aspect_mask_frames.py \
  --image output/full-aspect-soft-mask-v34/EG209020.BIN \
  --evidence build/research/mask-firmware-v34-menu --profiles 2 6 0
```

QEMU 使用明确的无传感器视口/源窗口夹具和模型色条，具体边界见 [v31 说明](aspect-soft-mask-v31.md)。v31 已获上述局部机身反馈，v34 的 50% 强度尚待机身观感确认；未新增拍照、回放、HDMI 或全部电源生命周期证明。开发侧没有写相机或存储卡，也未 commit/push。
