# Z7 离线验证

## 单元测试

在项目根目录执行，无需原厂固件：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m unittest discover -s tests -v
```

覆盖输入身份、CRC、补丁边界与重叠、原字节校验、追加对齐、容量、Thumb 指令生成、
Z7 容器识别、Z7 MAIN 提取和 Z7 各 preset 构建。

## Z7 原厂指令执行检查

准备匹配的 Z7 C 3.80 原版固件并安装可选依赖：

```powershell
python -m pip install '.[sound,verify]'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python tools/verify_liveview_gates.py --source "Z_7_0380.bin" --model z7 --preset full
```

工具使用 Unicorn 执行选定的 ARM Thumb 指令路径，输出 JSON 结果；`passed: true`
表示相应断言通过。

| 检查器 | 覆盖内容 | 限制 |
| --- | --- | --- |
| `verify_liveview_gates.py --model z7` | 两个原厂与补丁 DISP 判断窗口、256 种值、续接地址、栈及保存寄存器 | 使用合成 RAM，在原厂后续绘制服务前停止 |
| `tests/test_z7_integration.py` | 容器 SHA-256、MAIN payload 哈希、完整 EG CRC、`full`/各 preset 构建 | 需要本地存在 `Z_7_0380.bin`；不执行相机硬件 |
| WAV `validate` / `convert` | Z7 sound 自定义函数的 PCM 校验和转换 | 当前通过开发期 Unicorn 检查，未打包成独立 CLI 工具 |

构建器还核对匹配固件、修改前字节、窗口重叠、容量、CRC 和视频快门原厂窗口。JSON
构建报告记录输入身份、补丁 offset 和输出摘要。

## 未覆盖范围

这些检查不验证完整启动、刷写、屏幕像素、触摸、扬声器或传感器真实曝光。Z7 的
CMOS/ISP 图像管线、CFexpress/XQD 实际文件读取、机械/电子快门切换和扬声器播放
都需要独立验证，不能由离线结果推出。

Z30 的 `tools/verify_extended_features.py` 仍然只面向 Z30，不用于 Z7 验证。
