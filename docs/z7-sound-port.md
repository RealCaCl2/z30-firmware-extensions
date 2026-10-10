# Z7 C 3.80 shutter-sound 逆向进展

本文记录 Z30 `shutter-sound` 功能向 Z7 C 3.80 移植时已经确认的地址、数据结构和事件映射。
当前已经注册为可用 preset；Z7 的音频 config 调用被内联在更大的函数中，因此使用 call-site
trampoline + tail custom config 方案。

## 已完成确认的 Z7 数据结构

```text
page table base      0xA1833548   (Z30 0xC1832D24)
page 1 root          0xA1833574
SETUP_OBJECT         0xA1837984   (page 397; Z30 0xC1837160)
SETUP_META_SOURCE    0x11F10854   (Z30 0x11FF71AC; row count 0x23 = 35)
last dispatch event  0x113D91E0   (Z30 source event map 0x1140ED20)
```

Z7 的 `SETUP_META_SOURCE` 结构：

```text
[0] 0x1002
[1] 0x11441728
[2] 0x11392B30
[3] 0x113A2ED0   controls table
[4] 0x113DE640   dispatch table
[5] 0x080023     low16 = 35 stock setup rows
[6] 0x1108
```

Z7 页面表布局与 Z30 一致：44 字节/页。`page 0` 是空闲状态锚点，`page 397` 是 settings 页对象。

## 确认的 hook 站点

| 功能 | Z30 | Z7 | 备注 |
| --- | --- | --- | --- |
| row | 0x1045E33C | 0x1040389A | 函数入口 |
| getter | 0x104571FE | 0x103FCCC2 | 由 Z7 函数 0x103FCCB4 + 0xE 对齐 |
| shot | 0x10072598 | 0x10072730 | 函数入口候选 |
| data | 0x1006BE90 | 0x1006BFE6 | 函数入口，continuation 0x1006BFEF |
| config parser | 0x1006B5F4 | 0x1006B794 | Z7 config 逻辑被内联，调用点在 0x1006BD54 |
| route | 0x103CE804 | 0x1048B3FA | continuation 0x1048B403，激活目标 0x3A0 |

## 确认的 native / UI 函数映射

| Z30 | Z7 |
| --- | --- |
| 0x1006B5F4 | 0x1006B794 |
| 0x1006B7B2 | 0x1006B94A |
| 0x100725A1 | 0x10072739 |
| 0x10096CA0 | 0x100A2DA0 |
| 0x101993DE | 0x1016FEF2 |
| 0x10199BDC | 0x10170A1C |
| 0x10199C40 | 0x10170A80 |
| 0x101DB84F | 0x101BFA29 |
| 0x101DBA7C | 0x101BFC4A |
| 0x10211F4C | 0x1016FE38 |
| 0x10211F5E | 0x1016FE4A |
| 0x102C89EE | 0x1027BFCC |
| 0x1033829E | 0x102D8562 |
| 0x103382A2 | 0x102D8566 |
| 0x1033832E | 0x102D85F2 |
| 0x10338414 | 0x102D86D8 |
| 0x10454210 | 0x103FAEBE |
| 0x104546C6 | 0x103FB344 |
| 0x10453AC3 | 0x103FA771 |
| 0x10453FAC | 0x103FAC5A |
| 0x10454EFE | 0x103FBB5C |
| 0x1045720B | 0x103FCCCF |
| 0x1045745E | 0x103FCF0E |
| 0x104574A8 | 0x103FCF60 |
| 0x1045D54C | 0x10402BC2 |
| 0x1045F732 | 0x10404C44 |
| 0x1045FDA1 | 0x10405261 |

## 确认的全局指针

| Z30 | Z7 | 用途 |
| --- | --- | --- |
| 0xC21C0E28 | 0xA21AB994 | 音频选择状态 |
| 0xC1885A50 | 0xA188D1A0 | 音频文件/字段表 |
| 0xC2006594 | 0xA200DA5C | UI page/value 表 |
| 0xC1832D24 | 0xA1833548 | page table / state anchor |

## Z7 事件映射

Z7 最后一行 settings 的 dispatch event map 位于 0x113D91E0，前 55 条记录是一张完整的
event map。Z7 的导航回调集合为：

```text
0x10404F8B, 0x10404F8D, 0x10404FB3, 0x10404F6F,
0x10404F7B, 0x10404F87, 0x10404FEF
```

与 Z30 `0x104D0A47` 对应的 Z7 回调是 0x104C8BF7；在 Z7 event map 中它对应事件：

```text
0x239, 0x23E, 0x252, 0x25F
```

因此 Z7 的 activation events 应为上述四个，neutral destination 使用 0x3A0/0x3A1
（对应 Z30 的 0x3BB/0x3BC）。

## 下一步实现方案

1. 写 Z7 专用 `sound_layout_z7`，动态选择：
   - 3 个连续空闲 page index（page table 中有大量全零页，例如 0x1C5A..0x1C5F）；
   - 空闲 control ids 和 text id 段。
2. 复用 Z30 `sound_audio.py` 的算法，但替换 native 地址和全局指针；data hook 可直接 patch
   0x1006BFE6 入口，fallback 到 0x1006BFEF。
3. config hook 不能直接 patch 0x1006B794 入口，因为自定义 config 代码仍需调用原 parser。
   推荐把 0x1006BD54 的 `bl 0x1006B794` 改为一个近距离 trampoline，再跳到 tail 中的
   custom config；这样原 parser 保持可调用。
4. `sound_ui.py` 需要替换 activation events 0x239/0x23E/0x252/0x25F、目标 0x3A0，
   并将 header object id / text id 改成 Z7 动态值。
5. 最后用 Unicorn 验证：
   - WAV validate/convert
   - 自定义 config/data 返回结构
   - shot hook 调用路径
   - liveview gate 已有验证。

当前代码库已经完成容器、liveview-dual、m900 的 Z7 移植；shutter-sound 的地址和数据映射已经
推进到可以具体实现的程度。


## 已实现

Z7 `shutter-sound` 已经可以使用：

```powershell
python patch.py shutter-sound --source "Z_7_0380.bin" --output "output/EG16100380_sound.BIN"
python patch.py full --source "Z_7_0380.bin" --output "output/EG16100380_full.BIN"
```

实现内容包括：

- Z7 使用 CFexpress/XQD 卡，卡内路径仍为 `A:\SHUTTER.WAV` / `A:\SHUTTER\N.WAV`；
- 使用 Z7 的最后一行 settings event map 生成 entry/owned event map；
- 动态检查 control ID 冲突，并使用空闲高段 ID；
- data hook 适配 Z7 的 10 字节入口窗口；
- config 调用点 0x1006BD54 通过 code cave trampoline 跳到 tail 中的 custom config；
- route hook 适配 Z7 的 12 字节入口窗口和 0x3A0 destination；
- `validate` / `convert` 已用 Unicorn 对 PCM WAV 做离线执行验证。

仍未完成真实相机上的菜单显示、扬声器播放和 CFexpress/XQD 文件读取物理验证。
