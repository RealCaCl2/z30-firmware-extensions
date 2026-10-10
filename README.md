# Z7 Firmware Extensions

面向 Nikon Z7 C 3.80 的固件功能扩展与 patch 工具。项目最初以 Z30 C 1.20 为基础，
现已完成 Z7 C 3.80 的移植，并保留 Z30 支持。

## 功能

- **直方图＋水平仪同屏**：扩展 DISP 可见性判断，保留原厂模式条件和显示顺序。
- **自定义电子快门声音**：从 CFexpress/XQD 卡读取 WAV；提供声音开关、文件选择、预听和重新加载。选择页以 `*` 标记当前选项，按一次 OK 应用。
- **M 档 900 秒曝光**：启用固件已有的长曝光路径，保留原厂 M 档判断。

Z7 C 3.80 为当前主要目标。固件修改属于实验性质；离线检查的覆盖范围见
[验证说明](docs/verification.md)，不能保证所有相机行为。

## 使用

第一次使用请看[简单 patch 指引](PATCH_GUIDE.md)。需要 Python 3.10+、完整项目源码和
未经修改的 `Z_7_0380.bin`。

```powershell
python -m pip install '.[sound]'
New-Item -ItemType Directory -Force output | Out-Null
python patch.py full --source "Z_7_0380.bin" --output "output/EG16100380.BIN"
```

程序先检查原始包的 SHA-256，再解包并核对 `eg1610_038000.bi` 模块。随后按 offset
检查并替换原字节、追加功能代码、重建 CRC，输出完整 EG 模块和 JSON 构建报告。
原始文件不会修改，已有输出不会覆盖。

默认 `full` 包含同屏、自定义声音和 M900。可以通过 `liveview-dual`、
`shutter-sound` 或 `m900` 单独构建。具体输入哈希和实现方式见
[构建说明](docs/build.md)。

生成的是完整 `eg1610_038000.bi` 模块，末尾带 2 字节 CRC。若需要给 Z7 QEMU
使用，取输出前 33,052,088 字节作为 MAIN payload。

## 自定义声音

设置菜单中的 `Shutter sound` 提供开关和声音文件选择。Z7 使用 CFexpress/XQD
主卡，固件路径为 `A:\SHUTTER.WAV`，以及 `A:\SHUTTER\1.WAV` 至
`A:\SHUTTER\4.WAV`。声音格式为 8000 Hz、单声道、16 位 PCM，最长 1.08 秒，
详见[声音说明](docs/shutter-sound.md)。

**注意：CFexpress/XQD 卡根目录的 SHUTTER.WAV 声音资源是必须的，否则无法正常启用该功能；SHUTTER 目录及其目录内文件可以空缺。**

转换短 PCM WAV：

```powershell
python tools/prepare_wav.py input.wav SHUTTER.WAV
```

转换器使用线性插值；高采样率音频可先用带抗混叠滤波的重采样器处理。仓库不附第三方
音频，脚本中的 `example(slot)` 可生成合成示例。

## Z30 C 1.20 支持

Z30 代码路径与平台映射仍然保留。Z30 使用 SD 卡，输入为 `Z_30_0120.bin`：

```powershell
python patch.py full --source "Z_30_0120.bin" --output "output/EG209020.BIN"
```

Z30 详见[Z30 原始说明](docs/z30-support.md)。

## 文档与许可

- [简单 patch 指引](PATCH_GUIDE.md)
- [构建说明](docs/build.md)
- [Z7 移植说明](docs/z7-port.md)
- [Z7 与 Z30 差异审计](docs/z7-differences.md)
- [声音菜单与文件格式](docs/shutter-sound.md)
- [离线验证方法与限制](docs/verification.md)
- [许可证范围与第三方说明](THIRD_PARTY_NOTICES.md)

除声音实现的四个文件外，本项目原创代码与文档采用 [MIT](LICENSE)。声音实现采用
[Wonder Lantern Community License](licenses/Wonder-Lantern.txt)，包含免费分发等
条件；使用或分发组合代码时须同时遵守相应条款。

本项目与 Nikon、Wonder-Lantern 无隶属关系。许可证不授予 Nikon 固件、原厂资源或
商标的权利。
