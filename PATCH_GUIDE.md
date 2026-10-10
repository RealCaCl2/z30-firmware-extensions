# Z30 固件 patch 简单指引

目前只支持 **Z30 原版 C 1.20 固件**。默认加入直方图＋水平仪同屏、自定义快门声音和 M 档 900 秒曝光。

## 1. 准备文件

安装 Python 3.10 或更新版本，下载并解压本项目的完整源码。

把原版固件 **Z_30_0120.bin** 放进项目文件夹，与 `patch.py` 放在一起。不要只下载 patch.py，项目中的 `src` 文件夹也需要保留。

## 2. 运行命令

在项目文件夹里打开 PowerShell，依次执行：

```powershell
python -m pip install '.[sound]'
New-Item -ItemType Directory -Force output | Out-Null
python patch.py --source "Z_30_0120.bin" --output "output/EG209020.BIN"
```

第一条命令安装依赖，只需首次执行。程序会自动检查原版固件的哈希并完成 patch，原始文件不会被修改。

## 3. 找到生成的固件

看到 `Built` 和输出文件路径，就表示 patch 成功。

打开 `output` 文件夹：

- **EG209020.BIN**：生成的自定义固件。
- **EG209020.json**：构建信息，无需复制到相机。

## 常见问题

- **找不到 python**：确认已安装 Python。Windows 下也可以把命令里的 `python` 换成 `py`。
- **SHA-256 mismatch**：固件不匹配，请使用未经修改的原版 Z30 C 1.20。
- **Output or report already exists**：移走 output 中之前生成的 BIN 和 JSON，再运行；程序不会覆盖旧文件。

这是实验性固件修改，离线检查不保证所有相机行为。

## Nikon Z7 C 3.80

Z7 C 3.80 (`Z_7_0380.bin`) is also supported for `liveview-dual`, `m900`,
`shutter-sound` and `full`.  See [docs/z7-port.md](docs/z7-port.md) for the
exact commands and output notes.
