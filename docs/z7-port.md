# Nikon Z7 C 3.80 移植说明

## 当前状态

本仓库的通用补丁框架、容器解码和以下 Z7 C 3.80 功能已经移植：

| 功能 | 状态 | 说明 |
| --- | --- | --- |
| 固件容器解码 / MAIN 提取 | 已完成 | 使用 Z7 专属零字节观测表恢复 XOR 掩码，不捆绑固件字节。 |
| liveview-dual（直方图 + 水平仪同屏） | 已完成 | 两个原生 DISP 门控窗口和 9 个可见性掩码。 |
| m900（M 档 900 秒长曝光） | 已完成 | 复用原生属性读取钩子，属性键 0x11A3。 |
| full（上述两项组合） | 已完成 | 输出完整 EG 模块（含 2 字节 CRC）。 |
| shutter-sound（自定义快门声音与菜单） | 已完成 | 已启用 `shutter-sound` 和 `full` preset。 |

所有结果均未在真实相机上做物理验证。

## 输入指纹

`Z_7_0380.bin` 原始升级容器：

```text
size   34472614
sha256 07cf19513ada5b770caec4cfc3531a6734c48aede28dcec037f0f8374ef441a2
```

容器内提取的 `eg1610_038000.bi` 完整模块：

```text
size   33052090
sha256 7f59d8993080203a12692cf6927a0a8218786d3f72230c04174f1563c506a6f6
crc    crc_hqx == 0
```

QEMU 等研究工具需要的 MAIN payload 是完整模块去掉末尾 2 字节 CRC：

```text
size   33052088
sha256 e6df1764323fb1c287159dd3ce1c84ac93c72b96ba6eb09c070295c093fecbff
```

## Z7 平台参数

```text
BASE     0x10020000
CAPACITY 0x02000000
SCATTER  source=0x11E9C214 dest=0xA1800000 length=0x101200 entry=0x1002006C
```

`StockImage` 通过 `SCATTER` 把初始化数据虚拟地址映射回完整 EG 模块中的文件偏移。

Z7 的存储卡是 CFexpress/XQD，固件主卡路径同样使用 `A:\`；快门声音文件应放在
`A:\SHUTTER.WAV` 或 `A:\SHUTTER\1.WAV` .. `A:\SHUTTER\4.WAV`。

## liveview-dual 地址

| 项目 | Z30 C 1.20 | Z7 C 3.80 |
| --- | --- | --- |
| 原生门控函数 A | 0x1044FF70 | 0x103EBB6C |
| 原生门控函数 B | 0x1036E8B4 | 0x1036D58C |
| 快照全局指针 | 0xC2016A68 | 0xA201D4D0 |
| 状态偏移 | +0x196 | +0x18A |
| 直方图描述符 | 0xC1826714 | 0xA1827404 |
| 描述符绑定表项 | 0x1073F8AC | 0x1064D68C |
| 原始 9 个可见性掩码 | 0x444 x 9 | 0x444 x 6 + 0x440 x 3 |
| 补丁后掩码 | 0xCCC x 9 | 0xCCC x 9 |
| 视频快门校验窗口 1 | 0x1016E150 | 0x10150770 |
| 视频快门校验窗口 2 | 0x1016E09E | 0x101506A6 |

`gate_code()` 会按机型写入正确的状态偏移和快照指针；接受 2 或 3，其余值仍走原生拒绝路径。

## m900 地址

| 项目 | Z30 C 1.20 | Z7 C 3.80 |
| --- | --- | --- |
| 属性读取函数入口 | 0x100F303C | 0x10070234 |
| 偏移属性键 | 0x11A3 | 0x11A3 |
| 原始前缀 | 38 b5 0c 46 00 21 21 60 | 同左 |
钩子仍然保持"只覆盖读取值、不写入持久设置"的策略。

## 构建

在项目根目录执行：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')

# 只做同屏
python patch.py liveview-dual --source "Z_7_0380.bin" --output "output/EG16100380_liveview.BIN"

# 只做 M900
python patch.py m900 --source "Z_7_0380.bin" --output "output/EG16100380_m900.BIN"

# 自定义快门声音
python patch.py shutter-sound --source "Z_7_0380.bin" --output "output/EG16100380_sound.BIN"

# 组合功能
python patch.py full --source "Z_7_0380.bin" --output "output/EG16100380_full.BIN"
```

输出是完整 eg1610_038000.bi 模块（33,052,090 字节，末尾 2 字节为 CCITT CRC）。如果需要给 Z7 QEMU 使用，取输出的前 33,052,088 字节：

```powershell
$bytes = [IO.File]::ReadAllBytes('output/EG16100380_full.BIN')
[IO.File]::WriteAllBytes('output/z7-main-payload.bin', $bytes[0..($bytes.Length - 3)])
```

## 验证

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m unittest discover -s tests -v
python tools/verify_liveview_gates.py --source "Z_7_0380.bin" --model z7 --preset liveview-dual
```

集成测试会在本地存在 Z_7_0380.bin 时检查：

- 容器指纹和 MAIN 提取哈希；
- 完整模块 CRC；
- liveview-dual、m900、shutter-sound、full 均能重新构建且 CRC 为 0；
- Report 中 model/firmware/preset 正确。

verify_liveview_gates.py 使用 Unicorn 执行两个门控窗口的全部 256 个 DISP 值，验证原厂行为和补丁行为。
