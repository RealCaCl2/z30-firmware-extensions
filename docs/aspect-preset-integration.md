# 自定义画幅与半透明遮幅预设整合

2026-10-28：原有 `aspect-profiles` 预设已默认集成 [NEF 普通回放尺寸修复（v43）](aspect-raw-playback-v43.md)，`full` 自动包含；无需新增预设或开关。本次 XPAN RAW 花屏机身复测正常，其他组合验证范围见专题。以下构建摘要保留各自历史版本范围。


日期：2026-10-18；Nikon Z30 C1.20。

现在 `aspect-profiles` 默认同时构建照片裁切、内嵌预览、原厂内部网格与动态半透明遮幅；`full` 包含它，不增加第九项功能或独立遮幅预设。既有 v42 图形与回放修复保持不变。

## 行为与兼容

- 六种比例与输出尺寸不变，遮幅强度仍为 `128/255`，不因关闭网格而消失；关闭自定义比例清除遮幅。
- 完整组合按展开后的功能集合确定；`full`、显式全部八项和重复选择产生相同模块。
- 普通构建不记录或导出诊断日志。`build(..., aspect_trace=True)` 仍可生成 WMD1 定位版；开启诊断的 `full` 与已获用户本次回放正常反馈的 v42 逐字节一致。
- 研究 API `aspect_mask=False` 保留无遮幅对照；显式旧黄框/静态遮幅试验继续采用各自路径，不自动叠加动态遮幅。它们不是公共预设。
- 配置格式、菜单分类、声音和视频设置不变。构建器只生成独立 EG 模块，不重建外层更新包。

## 本地核对

| 构建 | SHA-256 | 字节数 |
|---|---|---|
| 默认 full | `a922de064319593a79e3ee19ecc0d0bba639b39d770349381eab3b0b147f5410` | 33,992,854 |
| 独立 aspect-profiles | `85febfc772f8abae3772c94f49705f4b9f074ce03d86332ad714012a538ae2b2` | 33,978,426 |
| full + 显式诊断 | `612d9d82c75b0409584fbbad483edb02f9c299d49f4868df9e4506051b6de1c3` | 33,995,498 |

六种构建组合 CRC 均为 0；默认 full、显式全部八项与重复选项逐字节一致。纳入本次提交范围的 53 项单元测试全部通过，无跳过。

默认 full 与独立画幅构建分别通过 24 组动态遮幅、23 个拒绝条件及原生照片条件检查；默认 full 另通过 63 组几何、36 组网格、10 个菜单动作，以及 13 条实机标量回放和 12 组目标筛选前清除检查。后两者使用明确 GPU 描述符/表面夹具，不是实际相册解码。

证据保存在忽略的 `build/research/publication-v42/`。既有 [v42](aspect-lifecycle-v42.md) 包含 QEMU 客体画面及用户机身反馈；本次普通构建的诊断关闭版与独立预设没有新增机身反馈，不扩大实机范围。普通构建的原厂缓存、全部电源/HDMI 生命周期仍未穷举。

## 复现

```powershell
python -m pip install '.[sound,verify]'
python patch.py full --source Z_30_0120.bin --output output/EG209020-full.BIN
python patch.py aspect-profiles --source Z_30_0120.bin --output output/EG209020-aspect.BIN
```

输出路径须尚不存在。报告 `features.aspect_profiles.soft_mask` 应存在，`diagnostic_trace` 为关闭。原厂固件、生成模块、个人照片、原始 EXIF、存储卡日志和完整本地审计不进入提交；文档只保留去标识化尺寸、地址、版本、摘要及验证范围。
