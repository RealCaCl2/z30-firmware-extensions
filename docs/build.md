# 从原始固件包构建 Z7 功能扩展

当前用户流程只需准备原始 Z7 C 3.80 固件包 `Z_7_0380.bin` 和本项目 patch 程序。
默认组合包含同屏、自定义电子快门声音和 M 档 900 秒曝光，不包含状态页面。

## 运行

需要 Python 3.10+。声音功能需要 Keystone，首次安装依赖；固件不需要手动解包：

```powershell
python -m pip install '.[sound]'
New-Item -ItemType Directory -Force output | Out-Null
python patch.py full --source path/to/Z_7_0380.bin --output output/EG16100380.BIN
```

可明确指定 `full`、`liveview-dual`、`shutter-sound`、`m900`；默认 full。单独同屏
和 M900 只需要标准库。输出和同名 JSON 必须不存在，程序拒绝覆盖。CLI 通过 Python 运行。

原始包大小为 34,472,614 字节，SHA-256 为：

```text
07cf19513ada5b770caec4cfc3531a6734c48aede28dcec037f0f8374ef441a2
```

固件由用户自行准备，仓库不附原始或修改版完整固件。

## 程序执行顺序

1. 检查整个原始包的大小和 SHA-256；不匹配则退出，不解码或生成文件。
2. 从 Z7 版本已知零填充位置恢复等价周期 XOR 掩码，内部解码；不附第三方 XOR 表。
3. 检查容器结构、模块边界和模块名，提取 `eg1610_038000.bi` 并再次检查 MAIN payload SHA-256。
4. 每个补丁包含 offset、expected bytes 和 replacement bytes；检查原字节、等长、边界和不重叠后替换。
5. 在原 EG 尾部 CRC 前追加钩子代码，修补跳转地址，重建 CRC。
6. 输出完整 EG 模块和报告，列出修改 offset、原字节、替换字节、追加地址和输入/输出摘要。

offset 使用**解包后的 EG 模块**坐标，不是编码外层包的同一位置。输出为独立 EG
安装候选，不重新封装 `Z_7_0380.bin`。

Z7 完整 EG 模块 `eg1610_038000.bi`：

```text
size   33052090
sha256 7f59d8993080203a12692cf6927a0a8218786d3f72230c04174f1563c506a6f6
crc    crc_hqx == 0
```

Z7 QEMU 使用的 MAIN payload 是完整模块去掉末尾 2 字节 CRC：

```text
size   33052088
sha256 e6df1764323fb1c287159dd3ce1c84ac93c72b96ba6eb09c070295c093fecbff
```

开发者可用 `--extracted-eg` 传入该完整模块，普通用户无需准备中间模块。

## 验证

普通测试不需要原厂固件：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m unittest discover -s tests -v
```

安装 Unicorn 后可执行 Z7 DISP 跳转窗口检查：

```powershell
python -m pip install '.[sound,verify]'
python tools/verify_liveview_gates.py --source path/to/Z_7_0380.bin --model z7 --preset full
```

同屏检查遍历全部 256 种 DISP，核对续接地址和栈、R4、LR；在原厂后续服务之前停止。
Z7 集成测试还会检查容器识别、MAIN 提取哈希、完整模块 CRC 以及
`liveview-dual`、`m900`、`shutter-sound`、`full` 的构建。执行方法与覆盖范围见
[验证说明](verification.md)。

## 输出与功能范围

输出为独立 EG 模块，不重建外层 `Z_7_0380.bin`。固件版本菜单保留原厂 3.80，
构建身份通过 JSON 报告的 SHA-256 核对。

默认组合包含同屏、自定义声音和 M900。900 秒补丁改变扩展标志读取，不写入其保存值；
原厂模式判断和曝光路径保留。视频快门窗口在构建后核对为原厂字节。

该工具不添加状态页、计数、CFW 版本标记、视频慢快门或卡模块宿主。离线检查不能保证
真实显示、声音、曝光或刷写行为。

## Z30 C 1.20

Z30 的构建说明和旧版输入哈希见 [Z30 支持说明](z30-support.md)。
