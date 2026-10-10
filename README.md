# Z30 Firmware Extensions

面向 Nikon Z30 C 1.20 的固件功能扩展与 patch 工具。

## 功能

- **直方图＋水平仪同屏**：扩展 DISP 可见性判断，保留原厂模式条件和显示顺序。
- **自定义电子快门声音**：从 SD 卡读取 WAV；提供声音开关、文件选择、预听和重新加载。选择页以 `*` 标记当前选项，按一次 OK 应用。
- **M 档 900 秒曝光**：启用固件已有的长曝光路径，保留原厂 M 档判断。

仅支持 Z30 C 1.20。这是实验性固件修改；离线检查的覆盖范围见[验证说明](docs/verification.md)，不能保证所有相机行为。

## 使用

第一次使用请看[简单 patch 指引](PATCH_GUIDE.md)。需要 Python 3.10+、完整项目源码和未经修改的 `Z_30_0120.bin`。

```powershell
python -m pip install '.[sound]'
New-Item -ItemType Directory -Force output | Out-Null
python patch.py --source "Z_30_0120.bin" --output "output/EG209020.BIN"
```

程序先检查原始包的 SHA-256，再解包并核对 EG 模块。随后按 offset 检查并替换原字节、追加功能代码、重建 CRC，输出 `EG209020.BIN` 和 JSON 构建报告。原始文件不会修改，已有输出不会覆盖。

默认包含全部功能；可以通过 `liveview-dual`、`shutter-sound` 或 `m900` 单独构建。具体输入哈希和实现方式见[构建说明](docs/build.md)。

## 自定义声音

设置菜单中的 `Shutter sound` 提供开关和声音文件选择。支持 SD 卡根目录 `SHUTTER.WAV`，以及 `SHUTTER/1.WAV` 至 `SHUTTER/4.WAV`。声音格式为 8000 Hz、单声道、16 位 PCM，最长 1.08 秒，详见[声音说明](docs/shutter-sound.md)。

**注意: SD卡根目录的SHUTTER.wav声音资源是必须的，否则无法正常启用该功能，SHUTTER目录及其目录内文件可以空缺。**

转换短 PCM WAV：

```powershell
python tools/prepare_wav.py input.wav SHUTTER.WAV
```

转换器使用线性插值；高采样率音频可先用带抗混叠滤波的重采样器处理。仓库不附第三方音频，脚本中的 `example(slot)` 可生成合成示例。

## 文档与许可

- [简单 patch 指引](PATCH_GUIDE.md)
- [构建说明](docs/build.md)
- [声音菜单与文件格式](docs/shutter-sound.md)
- [离线验证方法与限制](docs/verification.md)
- [许可证范围与第三方说明](THIRD_PARTY_NOTICES.md)

除声音实现的四个文件外，本项目原创代码与文档采用 [MIT](LICENSE)。声音实现采用 [Wonder Lantern Community License](licenses/Wonder-Lantern.txt)，包含免费分发等条件；使用或分发组合代码时须同时遵守相应条款。

本项目与 Nikon、Wonder-Lantern 无隶属关系。许可证不授予 Nikon 固件、原厂资源或商标的权利。

## Nikon Z7 C 3.80 support

The same container decoder and patch framework now also support the Nikon Z7
C 3.80 `Z_7_0380.bin` upgrade container.  The `liveview-dual`, `m900` and
`shutter-sound` presets are ported, and `full` combines all three.

See [docs/z7-port.md](docs/z7-port.md) for fingerprints, mappings, build
commands and verification details.  Z7 stores the WAV on its CFexpress/XQD
card using the firmware path `A:\SHUTTER.WAV` (or `A:\SHUTTER\N.WAV`).
