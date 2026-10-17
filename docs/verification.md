# 离线验证

2026-10-17 状态更新：用户确认当前全部八项功能均已在 Nikon Z30 C 1.20 实机检验通过，包括多画幅、全信息页、声音配置记忆和机内码率档位。下文旧版本的待实机表述保留当时的测试范围，以本条为当前功能状态；离线工具的验证字段仍仅描述工具自身证据，不表示穷举全部模式、组合或故障条件。

新增全信息 DISP 页的适配器差分、原厂控件回归和 QEMU 六页循环见[全信息 DISP 页](liveview-full-info.md)。

UI 标题、侧栏、返回按钮、双语文字、首次入口乱码，以及独立错误提示页和声音配置保存/恢复的回归见 [UI QEMU 验证](ui-qemu.md)。声音配置的文件服务测试使用明确模型，不能代替实机断电和 SD 写入验证。

机内码率档位通过 `tools/verify_bitrate_profiles.py` 检查三档 × 30 条原厂模式记录、完整记录字段、越界写入、首次读取恢复、配置损坏和写入失败；SD 服务为明确的指令测试模型。`tools/verify_bitrate_profiles_qemu.py` 在辅助 QEMU 中运行原厂模式索引、菜单和图形管线，SD 服务使用明确诊断覆盖。详细流程与实机缺口见[码率档位说明](bitrate-slowmo-uncap.md)。

## 单元测试

在项目根目录执行，无需原厂固件：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m unittest discover -s tests -v
```

覆盖输入身份、CRC、补丁边界与重叠、原字节校验、追加对齐、容量、Thumb 指令生成和合成输入的解码掩码恢复。

`video-rot` 单元测试使用合成 EG（仅替换测试中的身份常量），执行真实 Plan、预设构建和 CRC 路径，核对四份大端矩阵、两处钩子、追加区、共享矩阵不变、expected 不匹配拒绝及报告。构建测试需要 Keystone，未安装时明确跳过。预设/CLI 测试核对显式接入、重复选择、菜单第 7 项和默认范围不变。

## 自动视频方向离线检查

准备哈希匹配的原版固件，在项目根目录执行：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m pip install '.[video,verify]'
python tools/verify_video_rot.py --source path/to/Z_30_0120.bin
# 安装 .[sound,verify] 后可检查组合与原厂 tkhd 指令：
python tools/verify_video_rot.py --source path/to/Z_30_0120.bin --combinations --emulate
```

也支持 `--extracted-eg`。基础检查使用 Keystone 构建：共享 0° 矩阵不变，原厂矩阵等值替换逐字节还原 stock；`video-rot` 只有两处 10 字节钩子及矩阵/函数追加区，CRC 残差为零。`--combinations` 检查与同屏、M900、声音、MTP、full、modular 和 module-host 组合：撤销两个视频钩子和新增尾部后逐字节等于去掉视频功能的基线，其他功能的尾部不变，顺序和重复选择不影响输出。full 已包含视频方向，额外选择 video-rot 必须与 full 单独构建逐字节一致。

`--emulate` 使用 `video_rot_fixture.py` 和 Unicorn 执行原厂视频配置、原厂姿态 getter、memcpy 和完整视频 `tkhd` 构造器，不替换这些函数。测试四种方向、未知值 4、异常值 255、不可用回退、空参数和配置校验失败、配置后改变姿态、重复构造以及多个录像对象隔离；除对象的视频矩阵外，构造出的头数据逐字节与原厂一致，并检查保存寄存器和栈。姿态 RAM、参数和封装对象是明确的合成输入，不证明传感器、文件写入或机身播放；报告保留 `camera_verified: false`。

### 现有 Z30 QEMU 的构造器检查

已构建的独立 `video-rot` EG 可用 `tools/verify_video_rot_qemu.py` 在 Linux/WSL 测试。WSL 的 Python 也需要 Keystone；例如两个项目位于同一目录时，在 WSL 的扩展项目根目录运行：

```sh
python3 -m pip install '.[video]'
python3 tools/verify_video_rot_qemu.py \
  --qemu-root ../z30-qemu \
  --stock-eg ../z30-qemu/.local/firmware/eg2090_0120a0.bi \
  --candidate build/video-rot-stage2/EG209020.BIN \
  --output-dir build/video-rot-stage2-qemu
```

候选必须逐字节匹配由原厂 EG 构建的独立 `video-rot`，且 CRC 为零；不接受任意修改固件或 `full video-rot`。QEMU 保留原厂 BIOS 哈希检查，显式 debugger 写入并读回两个钩子及追加区/CRC；初始化 scatter RAM，提供合成姿态、视频参数、封装对象和调用上下文。为从复位状态调用原厂函数，额外提供正常、不可缓存的恒等映射页表及 CP15 初始化；这些是诊断输入，不是原厂启动证据。分别运行原厂与补丁，执行四种方向、未知值 4 和异常值 255 的配置及完整视频 `tkhd` 构造、录制期间改变姿态的快照保留、重复构造、不可用回退、空参数、原厂配置校验失败和对象隔离；逐字节对照头数据，仅允许视频矩阵变化。不提供配置、姿态或 memcpy 桩。

输出 `verification.json` 及两份 QEMU 日志。此测试不是自然开机或完整录像测试；现有模型不支持完整视频录制。机器加载方式和合成上下文在报告中明确记录，默认模拟器流程不变。

第一阶段的固定矩阵构建已收到实机反馈：横拍和竖拍视频均变为竖向播放。第二阶段随后已收到横拍、两侧竖拍方向正确及录制中方向保持锁定的实机反馈；原始视频尚未在本仓库逐字节检查实际文件矩阵。

2026-10-12 第二阶段本地验证：30 项单元测试、六种预设组合、Unicorn 原厂函数对照，以及现有 `z30-qemu` 的单核 TCG 原厂/补丁对照均通过。QEMU 发现对象内矩阵槽位为非对齐地址，因此包装函数使用原厂支持非对齐地址的 `0x10199CF0` 复制函数，不能使用第一阶段只复制到对齐栈缓冲的 `0x10199BDC`。最终独立构建 SHA-256 为 `0364ada38af88fd7ef67abdd4582b1a9761ab98077433ceede24b20d754c4eb8`，追加 280 字节，CRC 残差为零。本地完整报告为 `build/video_rot_stage2_verification.json` 和 `build/video-rot-stage2-qemu/verification.json`。下一步实机步骤见[自动方向测试说明](video-rotation.md)。

## 原厂指令执行检查

准备匹配的原版固件并安装可选依赖：

```powershell
python -m pip install '.[sound,verify]'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python tools/verify_liveview_gates.py --source "Z_30_0120.bin" --preset full
python tools/verify_extended_features.py --source "Z_30_0120.bin"
python tools/verify_mtp_fast.py --source "Z_30_0120.bin"
```

工具使用 Unicorn 执行选定的 ARM Thumb 指令路径，输出 JSON 结果；`passed: true` 表示相应断言通过。

| 检查器 | 覆盖内容 | 限制 |
| --- | --- | --- |
| `verify_liveview_gates.py` | 两个原厂与补丁 DISP 判断窗口、256 种值、续接地址、栈及保存寄存器 | 使用合成 RAM，在原厂后续绘制服务前停止 |
| `verify_mtp_fast.py` | `mtp-fast` 包装函数：32 种 handle 类型、取值与仅计数两种模式、builder 失败、记录获取失败、栈及保存寄存器 | 句柄转换、记录获取/释放和 builder 主体为桩，不涉及文件系统、USB 或并发 |
| `verify_extended_features.py` | M 档长曝光标志、其他属性对照、模式与上限、速度枚举及请求解析、WAV 校验与 PCM 转换 | 属性、可用列表和缓冲区为明确输入，不执行完整相机 |

构建器还核对匹配固件、修改前字节、窗口重叠、容量、CRC 和视频快门原厂窗口。JSON 构建报告记录输入身份、补丁 offset 和输出摘要。

这些检查不验证完整启动、刷写、屏幕像素、触摸、扬声器或传感器真实曝光。相机表现需要独立验证，不能由离线结果推出。

## 真机验证

维护者确认已在 Nikon Z30 C 1.20 上完成以下实机验证：

- 独立 EG 模块可通过机身更新流程安装。
- `modular` 构建的 SD 宿主可加载并运行 ZMOD 模块。
- 第二阶段独立 `video-rot` 构建：横拍、两侧竖拍方向正确，录制中转动机身方向保持锁定。用户还确认连续录像切换方向、重启、所用 4K/慢动作模式以及声画、播放、照片基本回归正常。未单独记录倒置横拍结果或原始 MP4 字节检查；不将独立预设反馈扩大为所有组合的实机覆盖。
- `mtp-fast` 构建（SHA-256 `281ecaae…0dde`）在 47 JPG + 47 NEF 的卡上与原厂对照：590 条 GetObjectPropList 响应逐字节一致；文件夹属性列表约 2920 ms → 680 ms，全卡约 3220 ms → 980 ms，单对象均值约 44 ms → 19 ms。耗时随卡内文件变化；GetObjectInfo 和 GetThumb 未改动。

该确认覆盖上述安装及模块宿主流程；各常规功能（同屏显示、快门声音、M 档长曝光）的实机结果尚未在本页逐项记录。离线检查与真机验证是不同的证据来源。

### MTP 属性列表对照

`mtp-fast` 的返回内容需要在同一张卡、同一批文件上对照原厂固件。`tools/ptp_timing.py` 通过 Windows 自带 MTP 驱动发送只读操作，需要 `python -m pip install '.[timing]'`（comtypes，仅 Windows）：

```powershell
python tools/ptp_timing.py --dump stock.json      # 原厂固件
python tools/ptp_timing.py --dump patched.json    # 刷入 mtp-fast 后
python tools/ptp_timing.py --compare stock.json patched.json
```

转储覆盖每个对象的全部属性与单属性请求、文件夹 depth 1、全卡各 depth 与格式过滤，以及不存在的文件、无效 handle、不支持的属性和 group code 等失败响应；`--compare` 逐字节比较并以非零退出码报告差异。不带参数运行则测量耗时。查询期间拍摄、删除、拔卡或 USB 中断时的行为不在该对照范围内，尚未验证。
