# Wonder Lantern Z30 离线构建指引

目前只支持 **Z30 原版 C 1.20 固件**。默认 full 生成包含直方图＋水平仪同屏、自定义快门声音、M 档 900 秒曝光、MTP 加速、实验性 SD 模块宿主、视频自动方向、高码率与慢动作扩展和多画幅的完整功能独立 EG 模块产物。

构建生成独立 EG 模块，不会重建完整 Nikon 更新包。维护者已确认该模块可在 Z30 C 1.20 机身安装，并已实机测试 SD 宿主与 ZMOD 加载、运行；详细验证范围见[验证说明](docs/verification.md)。

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

第一条命令安装依赖，只需首次执行。运行 patch 后会显示预设菜单，可自由组合：

| 编号 | 预设 | 内容 |
| --- | --- | --- |
| 1 | `full` | 全部八项：同屏＋快门声音＋M900＋MTP 加速＋实验性 SD 模块宿主＋视频方向＋高码率与慢动作扩展＋多画幅 |
| 2 | `liveview-dual` | 直方图与水平仪同屏，新增全信息 DISP 页 |
| 3 | `shutter-sound` | 自定义电子快门声音 |
| 4 | `m900` | M 档 900 秒曝光 |
| 5 | `mtp-fast` | MTP 属性列表加速 |
| 6 | `module-host` | 实验性 SD 模块宿主，自动包含快门声音框架 |
| 7 | `video-rot` | 实验性自动视频方向元数据，在新录像对象配置时锁定姿态 |
| 8 | `bitrate-slowmo-uncap` | 提高 1080p 高画质码率；慢动作录制上限 25 分钟，支持分段或大文件 |
| 9 | `aspect-profiles` | 多画幅菜单和网格参考框；包含在 full 中，已获用户实机确认 |

例如输入 `2 4 5` 或 `liveview-dual,m900,mtp-fast`；输入 `1` 或直接回车选完整功能 full，重复功能自动去重。

菜单包含完整组合和八个功能入口。输入 `1` 或直接回车启用全部八项功能；第 9 项可单独选择多画幅，使用说明见[多画幅说明](docs/aspect-profiles.md)。输入 `6` 仅启用实验性 SD 模块宿主及其必需的快门声音框架。旧名称 `modular` 仍可手动输入，保留原来的四项组合，但不再占用菜单编号。

`full` 已包含 `video-rot`，旧别名 `modular` 不包含它。第二阶段读取原厂姿态，为每个新录像封装对象锁定方向；不改像素或已有文件，未知姿态回退横向。播放器需支持方向元数据；用户已反馈横拍、两侧竖拍及录制期间方向锁定实机正常。单独使用先安装 `python -m pip install '.[video]'`；原有 `.[sound]` 也包含所需汇编依赖。

选择 `full` 或菜单第 8 项也会改变录像行为：1080p 60/50p 高画质平均码率提高到 90 Mbps，30/25/24p 提高到 60 Mbps；慢动作录制上限从 3 分钟延长到 25 分钟，FAT32 最多生成 8 个文件，exFAT 使用单个大文件。码率菜单及设置保存需要 Keystone，可通过 `.[sound]` 安装。作者报告的实机测试使用包含该功能的 `full`；独立预设、普通模式 MP4、MP4/FAT32 分段和其他码率尚未单独验证，详见[视频码率与慢动作限制](docs/bitrate-slowmo-uncap.md)。

也可跳过菜单，在命令里写出所需组合：

```powershell
python patch.py full --source "Z_30_0120.bin" --output "output/EG209020.BIN"
```

脚本调用或重定向输入时，省略预设默认 full；加 `--interactive` 可强制显示菜单。程序会检查原版固件哈希并生成 EG 模块，原始文件不会被修改。

## 3. 找到生成的固件

看到 `Built` 和输出文件路径，表示 EG 模块构建完成。该产物的机身安装与模块宿主/ZMOD 加载运行已有实机验证；其他功能覆盖见[验证说明](docs/verification.md)。

打开 `output` 文件夹：

- **EG209020.BIN**：独立 EG 模块，可在 Z30 C 1.20 上安装；不是完整 Nikon 更新包。
- **EG209020.json**：构建信息，无需复制到相机。

## 常见问题

- **找不到 python**：确认已安装 Python。Windows 下也可以把命令里的 `python` 换成 `py`。
- **SHA-256 mismatch**：固件不匹配，请使用未经修改的原版 Z30 C 1.20。
- **Output or report already exists**：移走 output 中之前生成的 BIN 和 JSON，再运行；程序不会覆盖旧文件。

这是实验性固件修改，离线检查不保证所有相机行为。
