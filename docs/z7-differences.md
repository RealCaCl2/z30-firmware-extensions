# Z7 与 Z30 差异审计

本文记录 Z7 C 3.80 移植过程中确认的 Z30/Z7 差异，以及代码中已做的对应调整。

## 容器与平台

| 项目 | Z30 C 1.20 | Z7 C 3.80 | 调整 |
| --- | --- | --- | --- |
| 外层容器大小 | 34,496,798 | 34,472,614 | `container.py` 按长度和 SHA-256 分流 |
| 外层容器 SHA-256 | 1a5449... | 07cf19... | 分别校验 |
| 模块数 | 3 | 4 | 分别解析 |
| 主模块名 | eg2090_0120a0.bi | eg1610_038000.bi | 分别提取 |
| 主模块 CRC | 末尾 2 字节，crc_hqx 为 0 | 末尾 2 字节，crc_hqx 为 0 | 均保留 |
| BASE | 0x10020000 | 0x10020000 | 相同 |
| CAPACITY | 0x2940000 | 0x2000000 | `z7_380.py` 单独设置 |
| SCATTER dest | 0xC1800000 | 0xA1800000 | 单独映射 |
| SCATTER source | 0x11F81C94 | 0x11E9C214 | 单独映射 |
| 零字节观测表 | z30_120_zero_samples.json | z7_380_zero_samples.json | 单独生成 |

## liveview-dual

| 项目 | Z30 | Z7 | 调整 |
| --- | --- | --- | --- |
| 门控 A | 0x1044FF70 | 0x103EBB6C | `SPECS['z7']` |
| 门控 B | 0x1036E8B4 | 0x1036D58C | `SPECS['z7']` |
| snapshot | 0xC2016A68 | 0xA201D4D0 | 单独设置 |
| 状态偏移 | +0x196 | +0x18A | `ldrb_cmp` 改为 `90f88a01 0228` |
| 直方图描述符 | 0xC1826714 | 0xA1827404 | 单独设置 |
| 描述符绑定项 | 0x1073F8AC | 0x1064D68C | 单独设置 |
| 原始掩码 | 0x444 x 9 | 0x444 x 6 + 0x440 x 3 | 单独设置 |
| 视频窗口 | 0x1016E150 / 0x1016E09E | 0x10150770 / 0x101506A6 | 单独设置 |

## m900

| 项目 | Z30 | Z7 | 调整 |
| --- | --- | --- | --- |
| 属性读取入口 | 0x100F303C | 0x10070234 | `SPECS['z7']` |
| 属性键 | 0x11A3 | 0x11A3 | 相同 |
| 原始前缀 | 38 b5 0c 46 00 21 21 60 | 同左 | 相同 |

## shutter-sound

| 项目 | Z30 | Z7 | 调整 |
| --- | --- | --- | --- |
| settings 行数 | 38 | 35 | 动态读取 `setup_header[5]` |
| SETUP_META_SOURCE | 0x11FF71AC | 0x11F10854 | 单独映射 |
| SETUP_OBJECT | 0xC1837160 | 0xA1837984 | 单独映射 |
| STATE_ANCHOR | 0xC1832D24 | 0xA1833548 | 单独映射 |
| event map | 0x1140ED20 | 0x113D91E0 | 动态取最后一行 dispatch |
| activation events | 0x256, 0x25B, 0x26F, 0x27E | 0x239, 0x23E, 0x252, 0x25F | `ACTIVATION_EVENTS` |
| neutral destination | 0x3BB / 0x3BC | 0x3A0 / 0x3A1 | 路由逻辑 |
| data hook 入口 | 8 字节窗口 | 10 字节窗口，continuation 0x1006BFF1 | 单独适配 |
| config | 独立函数入口 0x1006BEDE | 内联在更大的函数中 | 调用点 0x1006BD54 经 cave trampoline 进入 custom config |
| route hook | 8 字节窗口，destination 0x3BB | 12 字节窗口，destination 0x3A0，continuation 0x1048B407 | 单独适配 |
| data parser | 0x1006B7B2 | 0x1006B94A | 单独映射 |
| config parser | 0x1006B5F4 | 0x1006B794 | 单独映射 |
| 文件成功码 | 0x04370080 | 0x04390080 | `SUCCESS` 单独设置 |
| header object id | 0xA18 | 0x81A | 从 stock layout 读取 |
| 存储卡 | SD | CFexpress/XQD | 文档和 report 修正 |
| 卡路径 | A:\ | A:\ | 保持不变，A: 是主卡 |

## 已修正的容易遗漏的映射

以下地址在自动模糊匹配时曾落在函数内部或错误函数，审计后已修正：

```text
0x10096CA0 -> 0x100A2D78   (原候选 0x100A2DA0，错误)
0x101DBA7C -> 0x101BFC1C   (原候选 0x101BFC4A，错误)
0x104574A8 -> 0x103FCF5C   (原候选 0x103FCF60，错误)
0x1045F732 -> 0x10404C42   (原候选 0x10404C44，错误)
0x101DB84F -> 0x101BFA29   (odd Thumb 回调地址，审计确认正确)
0x10453AC3 -> 0x103FA771   (odd Thumb 回调地址，审计确认正确)
0x1045FDA1 -> 0x10405261   (odd Thumb 回调地址，审计确认正确)
```

## 结论

Z7 与 Z30 的差异已经在容器、平台、DISP 门控、直方图描述符、m900 属性键、
sound hook 窗口、sound 事件映射、成功码、config 内联调用、header object id 和
存储卡类型上做了对应调整。当前 16 个单元测试和 Z7 full 构建均通过。


## 硬件配置差异

| 硬件 | Z30 | Z7 | 对移植的影响与处理 |
| --- | --- | --- | --- |
| CMOS | DX / 约 2090 万像素 | FX / 约 4570 万像素 | 声音、菜单和 DISP 门控不直接操作 CMOS。本次移植不修改 ISP/RAW/CMOS 参数；图像管线相关功能不在范围内。 |
| 存储卡 | SD | CFexpress/XQD Type B | 主卡路径仍为 `A:\`；文件 API 成功码从 0x04370080 改为 0x04390080；report 中 `card_type` 已改为 CFexpress/XQD。 |
| 快门 | Z30 以电子快门为主 | Z7 同时有机械快门和电子快门 | shot 回调在 Z7 中整体后移且多一条指令：Z30 `0x10072598` -> Z7 `0x10072728`，continuation 为 `0x10072731`；已修正。 |
| 音频/扬声器 | Z30 硬件音频路径 | Z7 硬件音频路径 | 使用 Z7 自己的 native 音频函数映射；尚未在真实 Z7 扬声器上验证。 |
| 显示屏/UI 几何 | Z30 屏幕布局 | Z7 更高分辨率屏幕 | 已检查 Z7 native geometry tables：2/4/5 行页面仍存在 72..702 等同类矩形，硬编码几何可复用。 |
| CPU/内存/中断 | EXPEED 6 平台 | EXPEED 6 平台但布局不同 | 已通过 Z7 page table、SCATTER、native 函数映射适配。 |

## 与硬件相关的验证边界

当前离线验证覆盖：

- Z7 liveview gate：1024 次 Unicorn 执行；
- Z7 WAV validate/convert：PCM 校验和转换；
- 完整固件构建、CRC、hook 字节边界。

尚未覆盖：

- 真实 Z7 CMOS/ISP 出图和直方图；
- CFexpress 卡上的实际文件读取；
- Z7 机械/电子快门切换下的自定义声音触发；
- Z7 扬声器实际播放。
