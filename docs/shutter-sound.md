# Z7 自定义电子快门声音

构建包含该功能后，设置菜单增加 `Shutter sound`。主菜单为：

```text
Enabled          OFF / ON
Sound file       None / SHUTTER.WAV / 1.WAV..4.WAV
Preview sound
Reload sound
```

Enabled 打开 OFF / ON 选择页。Sound file 打开五个文件选择项。`*` 表示当前选项，
选中后按一次 OK 应用并返回主菜单。选择文件不自动开启声音，不自动播放。

加载失败时仍停留在选择页，在标题显示错误，保留之前有效的声音及开关。重新打开选择页
会清除旧错误标题。Preview sound 为显式预听，Reload sound 重新读取当前文件。

## 声音触发与文件

默认 OFF。需开启本功能并开启相机原厂静音拍摄，才在静音照片拍摄时触发自定义声音；
录像和非静音照片不触发。Z7 同时具备机械快门和电子快门，因此该功能依赖 Z7 原生
静音/电子快门回调；实际机械/电子快门组合需要在相机上验证。设置保存在 RAM，重开机
恢复 OFF / None。

Z7 使用 CFexpress/XQD 主卡，固件路径为：

```text
A:\SHUTTER.WAV
A:\SHUTTER\1.WAV
A:\SHUTTER\2.WAV
A:\SHUTTER\3.WAV
A:\SHUTTER\4.WAV
```

格式为标准 44 字节 WAV 头、8000 Hz、单声道、16 位 signed little-endian PCM，
非空、最长 1.08 秒，不包含额外 WAV 块。

已有声音文件的用户只更新固件即可，无需覆盖自己的 WAV。`tools/prepare_wav.py`
可规范化短 PCM 音频；脚本中的 `example()` 可生成原创示例。

## 菜单布局

- 使用普通子菜单 flags 及其标题/返回图标模板，模板从用户原始固件本地读取。
- 主菜单文件值栏宽度为 330 像素；Enabled 使用短标签，预听和重载标签保留整行宽度。
- 文件和开关选择页保留宽标签栏，用 `*` 替代易截断的 Selected。
- 三页标题使用动态文字，预留右上返回图标空间。
- 菜单控件 ID 从 Z7 原始固件扫描核对，避开已用 ID。原厂方向/返回处理保留；确认
  处理仅作用于三个私有声音页。
- Z7 header object id 使用原厂 `0x81A`，不是 Z30 的 `0xA18`。

声音菜单不注册 Camera Status 页面。声音状态使用原厂未使用的 page-0 RAM 指针位置
锚定私有分配，该槽没有注册可见状态页面。音频服务仍由 Z7 原厂队列和完成回调管理，
无新增线程或卡写入。

## 离线检查的限制

离线工具可检查 WAV 校验与 PCM 转换。实际显示、触摸命中、CFexpress/XQD 文件读取
和扬声器播放需要相机验证；方法与覆盖范围见 [验证说明](verification.md)。

## Z30

Z30 使用 SD 卡和 Z30 C 1.20 的原始 sound 布局，说明保留在
[Z30 支持说明](z30-support.md)。
