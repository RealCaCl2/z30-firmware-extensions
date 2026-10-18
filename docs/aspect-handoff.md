# Z30 多画幅与半透明遮幅：最新交接

更新：2026-10-18。机型 Nikon Z30 C1.20。仓库 `z30-firmware-extensions`。

## 当前预设与发布整理

2026-10-18 用户要求同一预设包含自定义画幅与半透明遮幅：`aspect-profiles` 默认启用 v42 动态遮幅，`full` 包含它；不新增遮幅预设。普通构建不启用 WMD1，诊断版仍可显式开启。旧黄框、静态遮幅和显式关闭的研究对照保持独立。此前“普通构建不启用遮幅”和未提交等说明属于历史阶段；本轮按用户授权整理文档、脱敏并提交推送本次相关更改。无线/预拍摄研究不在提交范围内。

## 先读这段

**最新：[v42](aspect-lifecycle-v42.md) 增加原厂取景阶段限制，并在绘制目标筛选之前清除旧遮幅。用户在本次回放修复复测后反馈“正常了”，记录为本次问题获机身确认；未提供逐档或完整生命周期覆盖。v37、v40 的失败及 v41 定位记录保留。**

用户最近明确选择此样式，不要再默认继续黄框、四角标记或细白框路线，也不要再次询问已选择的样式。用户要求本交接文档，由下一位 agent 继续；本轮没有创建新聊天或给其他 agent 发消息。

早期突破（历史）：在 QEMU 原厂图形工作线程内调用矩形填充，暗块真实进入 LCD 合成帧，而非主机重画。当时重复混合会累积变暗、停用也有残留；后续已改为受控下层填充，当前补缝与分类菜单以 v36 记录为准。不要把早期探针或该段当作最新实现。

- 现有成片、预览与取景比例修复要保留。
- 参考范围沿用已获实机确认的成片投影，不要重新按整块 LCD 居中算一套边界。
- 仅压暗投影范围外；范围内和原厂内部网格保留，状态控件、对焦框不应被擦掉或压暗。
- 淡黑遮幅初始建议不透明度 `48/255`，只是设计起点。当前成功探针用的是 `64/255`，且有重复混合，不能据其截图选择最终强度。
- QEMU 与真机证据严格分开；不要因为函数返回、寄存器提交或菜单测试通过就声称屏幕已修复。

旧的 v8 交接已归档到 [aspect-handoff-v8-archive.md](aspect-handoff-v8-archive.md)，只用于历史追溯，其“仍需解决 4:3/5:4 成片挤压”等状态不能当作最新任务。

## 工作区与权限边界

本次重新核对：分支 `main`，HEAD `f663058`。**工作区有大量修改和未跟踪文件，绝不是干净 checkout。最新实现主要在未提交工作区，不能只切回 HEAD 继续。**

- 不要 reset、清理未跟踪文件或覆盖现有改动。
- `wireless`、`camera_wireless`、`precapture` 相关文件属于其他工作，不要混入本任务或清理。
- 本轮没有 commit、push、写入相机或物理存储卡。
- 用户历史上要求文档脱敏；公共文档不要写用户名、绝对个人路径、盘符照片路径、邮箱或设备原始日志。原始照片、指针转储和完整 QEMU 审计只留在忽略的 `build/research/`。
- 本机是 Windows / PowerShell，QEMU 使用旁目录 `../z30-qemu` 的 WSL 环境。该目录本轮只读，没有改 QEMU 源码。写操作保留在本项目中。
- 本轮已知的 QEMU 探针均已结束，`finally: s.close()` 会关闭实例，没有本轮特意留下的常驻服务。

## 实机反馈优先级与版本沿革

用户报告的版本只说明以下范围，不要扩大为整机或所有场景已验收：

| 版本 | 反馈 / 结论 |
|---|---|
| v16 | 此前 4:3/5:4 间歇取景挤压问题，用户反馈“正常了” |
| v17 | 用户确认 4:3/5:4 边界与成片一致，3×3/4×4 网格与边界共存正常 |
| v18 | 独立黄色边框没显示，关闭网格后也没有框 |
| v19 | 只加阶段计数；日志指向原厂矩形通道未启用 |
| v20 | 加临时通道启用/恢复后，用户照片出现黄框，但每边为双线；不能凭开启网格的照片推断所有网格关闭/生命周期也已通过 |
| v21 | 长边与端部笔画均为 8，用户确认单线，但端部/角落有突出，且要求稍细 |
| v22 | 长边 6、端部 2，用户反馈又出现双线；减细路线失败 |
| v23 | 长边恢复 8，端部 2，用户仍反馈小突出；不能称端帽问题已解决 |
| v24 | 本地零端帽编码试验，**没有交付给用户或获得实机验证**。用户随后选择半透明遮幅，源码已恢复 v23 参数，v24 文件已标记不用于安装 |

用户也曾误报回放直方图问题，后来明确更正“直方图正常”；不要把它重新列为待修缺陷。

### 产物身份

以下均为原加载流程用的 EG 模块，不是重建的 Nikon 外层更新容器。交接时重新计算过 SHA-256：

| 本地文件 | SHA-256 |
|---|---|
| `output/full-aspect-border-channel-v20-final/EG209020.BIN` | `8d7d72d864a282d4d340f4655e0fd7be28b44ee701170894ae2ce55a1d0eb1e2` |
| `output/full-aspect-border-solid-v21/EG209020.BIN` | `fa5c9eac38d763a6bfd59879d30654141f09a3a65bb147f764abc0441a0ab1c6` |
| `output/full-aspect-border-fine-v22/EG209020.BIN` | `83f64f714f2d5fb60eae0df34c86cf198494ae7345bbb34073e03f0e4995370b` |
| `output/full-aspect-border-caps-v23/EG209020.BIN` | `f0faaea9a881653bbed32531276acbf26951d44ef8d9c8874ebde885fd967ae6` |
| `output/full-aspect-border-zero-caps-v24/EG209020.BIN`，未交付 | `992b31d4d39232585f27eb730734fcda569992dda372c140cd906eedda3d19c0` |

v24 的 `NOT-FOR-INSTALLATION.md` 是明确撤出交付路线的标记。不要误把这个目录称作“最新可刷版”。

## 早期源码地图（黄框交接时记录，以最新段落为准）

| 文件 | 当前用途 |
|---|---|
| `src/z30_extensions/features/aspect_geometry.py` | 既有画幅、成片/预览相关几何 |
| `src/z30_extensions/features/aspect_ui.py` | 菜单、状态、接入与元数据；独立边框仍显式启用；当前边框参数为 v23 的 8/2 |
| `src/z30_extensions/features/aspect_frame.py` | 已确认的投影与网格轴生成，遮幅应复用其成片投影 |
| `src/z30_extensions/features/aspect_border.py` | 旧黄色矩形路线；当前保留 v23 参数，不是新遮幅实现 |
| `src/z30_extensions/features/aspect_border_lease.py` | 原厂矩形通道临时占用/恢复与原厂接管保护 |
| `src/z30_extensions/features/aspect_mask.py` | **新增的纯几何模块，未接入固件**；`regions(photo, viewport)` 生成四块不重叠遮幅，`DEFAULT_ALPHA=48` |
| `tests/test_aspect_mask.py` | 四项几何测试；交接时运行通过 |
| `tools/probe_aspect_mask_context_qemu.py` | **最新有效 QEMU 探针**；观察原厂绘制，或通过 GDB 暂停工作线程后执行原生绘制，不安装 ROM 钩子 |
| `tools/build_aspect_border_trial.py` | 旧边框试验构建器，不会生成新遮幅固件 |
| `tools/read_aspect_trace.py`、`tests/test_aspect_trace_border.py` | WBD1 尾部解码与回归 |

普通默认构建仍走 v17 路线，没有自动启用独立黄框或新遮幅。不要把 `aspect_mask.py` 的存在当作功能已集成。

## 最重要的 QEMU 证据

### 成功目录（首先打开）

`build/research/aspect-mask-alpha-worker-c-qemu/`

- `context.json`：记录 **10 次** pre-copy 注入，原厂裁剪恢复信息、调用观察与帧清单。
- `session.json`：辅助启动、原生调用与工作线程审计。
- `devices.log`：模型 LCD 合成和 GPU 提交。
- `02-refresh-1.png`：未注入基线。
- `03-pre-copy-native-alpha.png`：**暗块真实出现**，矩形的 LCD 范围是 x `[240,480)`、y `[60,120)`。
- `04-with-injection-disabled.png`：停止注入并请求原厂刷新后，仍有残留，**不能当作清除成功**。

交接时直接读取 PNG 像素核对（没有修改图片）：

| 帧 | 暗块内部 `(300,80)` | 外部同色条 `(300,130)` |
|---|---|---|
| 基线 | `[0,255,1]` | `[0,255,1]` |
| 注入后 | `[0,19,0]` | `[0,255,1]` |
| 停用并刷新后 | `[0,191,1]` | `[0,255,1]` |

10 次是总注入数，**不是已证明同一个 buffer 被混合 10 次**。但暗块明显深于单次 64/255，且停用后留下一次约 25% 的压暗，足以说明重复混合/多 bank 生命周期没有解决。必须追踪具体目标与 bank，不能只减小 alpha 掩盖问题。

`context.json` 的 `mask_drawn=false` 表示没有完整画幅遮幅，`single_rectangle_probe=null` 表示没有使用 `--draw-probe` 分支；它们不否定 `pre_copy_diagnostic_injections` 中的矩形。这里成功的是小矩形探针，不是完整功能。

### 为什么旧 QEMU 检查没看见黄框缺陷

旁目录 `qemu/nikon-z30-display.c` 当前 LCD 模型读取原厂图像与 YYUV422A/alpha overlay，能显示菜单和部分取景合成。它**没有实现黄色边框所用的 42 记录矩形硬件光栅**。旧测试只是原生代码、寄存器、启用位和菜单回归，不能验证双线、端帽或实际线宽。

因此不要为了“QEMU 看着对”按自己的猜测补画黄框再说是真机硬件验证。新探针显示的暗块经过原厂 OpenVG 绘制与现有模型合成；模型的 alpha、色彩转换和硬件语义本身仍有假设。

## 最新探针怎么工作的

1. 导入 `../z30-qemu/tools/display_session.py` 和 `liveview_session.py`，按原来的辅助取景流程启动。
2. 使用 `Z30_LCD_SCANOUT_TEST=1` 和 `Z30_PREVIEW_PATTERN=1`；背景是模拟器色条，不是传感器。
3. 观察 `vgClear`、`vgDrawPath`、`vgFinish`、`eglSwapBuffers`。
4. 只在自然运行的工作线程停到 `vgFinish`，且 LR 为 `0x10477d6d` 时注入。该 LR 对应原厂 `0x10477d68` 的四字节 BL，**不是 `0x10477d69`**。
5. 使用暂停包的实际 CPU/thread ID，保存寄存器和临时栈，读取并保存 scissor，调用原生填充体，然后恢复 scissor，再让原来的 Finish/拷贝流程继续。
6. 不直接写帧缓冲、不在主机上加工相机画面，没有持久化的固件钩子。

### 原生接口速查

| 地址 | 目前掌握的用途 |
|---|---|
| `0x1050aad6` | `vgClear` |
| `0x1050acf6` | `vgDrawPath` |
| `0x1050a306` | `vgFinish` |
| `0x10559c34` | `eglSwapBuffers`；当前成功点不靠它 |
| `0x10477528` | 队列化填充矩形包装，R0 指向 28 字节参数 |
| `0x1047743c` | 原厂填充体，**成功探针在正确工作线程直接调用它** |
| `0x104779f4` | 原厂队列化清除包装；独立调用已返回，但没有证明正确清除屏幕遮幅 |
| `0x10476ff4` | 队列化 scissor 设置；传 0 使用完整画布 |
| `0x10476f92` | scissor 设置体，工作线程里直接用；传 0 使用完整画布 |
| `0x1050a47c` / `0x1050a4d0` | `vgGeti` / `vgGetiv` |
| `0x1050a406` / `0x1050a442` | `vgSeti` / `vgSetiv` |
| `0x10476ed2` | 探针独立调用过的原厂队列化 Finish 包装；本地曾校验入口半字 `10b5` |

本次参数布局：`struct.pack('<6I2B2x', x, y, width, height, rgba, radius, 0, 0)`，实际探针为 `(240,360,240,60,0x00000040,0,0,0)`。颜色低字节是 alpha；OpenVG y 与 LCD y 相反，故屏幕暗块落在 y 60..120。不要把此固定探针坐标当作真正画幅投影。

scissor 状态使用 `VG_SCISSORING=0x1131`、`VG_SCISSOR_RECTS=0x1106`；成功样本原始范围为 `(0,0,720,480)`，scissoring 为 1。**目前记录了保存值并调用恢复 API，尚未独立回读全部状态，不等于矩阵、paint、blend、surface/bank 和所有资源生命周期均已完整恢复。**

## 失败尝试与不要重踩的坑

- `aspect-mask-context-qemu`：第一份基础观察，最后广泛读取显示 MMIO 导致 `E14`；不要把未建模寄存器整块读一遍。后续已删掉宽泛 MMIO 读取。
- `aspect-mask-alpha-qemu`：队列填充与清除都返回，但暗块没进入截图。
- `aspect-mask-alpha-clip-qemu`：扩大裁剪仍没出现；**“只是裁剪挡住”未成立**。
- `aspect-mask-alpha-finish-qemu`：补原厂 Finish 仍没出现；单独 API 正常返回不等于提交成功。
- `aspect-mask-alpha-copy-qemu`：返回地址误写为 d69，根本没有注入；已改为 d6d。
- `aspect-mask-alpha-copy-b-qemu`：在 `input()` 的检查点观察里嵌套调用，会与另一 CPU 上的 InitTask 检查点/调用 sentinel 冲突。改为只在 `run_for()` 自然工作线程观察时注入。
- `aspect-mask-alpha-worker-qemu`：采样上限误用全会话累计计数，启动后立即耗尽，零注入；已改为每次 `run_for()` 独立计数。
- `aspect-mask-alpha-worker-b-qemu`：使用 `qC` 选择 CPU，嵌套原生绘制超时，未恢复；审计有失败现场。**不能据此认定原厂绘制体不可重入或必然死锁**，后续使用 stop packet 的 CPU 成功。
- `aspect-mask-alpha-worker-c-qemu`：上述修正后的成功基点，优先从这里继续。

最后一个失败不能简单归咎于资源锁；已知改正是 CPU 选择，之后相同绘制体实际成功。不要再使用 `qC` 代替暂停包提供的线程身份。

## 复现

在项目根目录执行；QEMU 使用 WSL 时需将仓库与旁目录转换为本机相应 WSL 路径，**输出目录使用项目内绝对路径**，避免被 sibling Session 的 `.local` 相对根规则带到旁目录。

```text
python3 tools/probe_aspect_mask_context_qemu.py \
  --qemu-root <absolute-path-to-z30-qemu> \
  --output-dir <absolute-path-inside-this-project>/build/research/mask-next \
  --at-copy
```

默认仅观察；`--draw-probe` 是独立队列调用实验，不是最后成功点。当前源文件包含已修正的 CPU 选择、每次观察计数和绘制完成输出文案。最后成功运行之后，只改过输出文案与文档，不应再退回旧探针实现。

几何检查：

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -p test_aspect_mask.py
```

交接时此四项测试通过。没有重新执行整仓库所有测试，也没有为遮幅生成可交付 EG。旧边框各版本的通过项必须绑定对应 `build/research/aspect-border-v*/` 与二进制 SHA，不能算作遮幅验收。

## 下一位 agent 的优先顺序

1. **先阅读成功帧和审计，不要重做黄框。** 将探针调用点、实际绘制目标、front/back bank 及后续拷贝消费者串起来。
2. **解决重复混合。** 目前每次拷贝前都 SRC_OVER 叠黑，不具备幂等性；需要确定每帧重建、独立遮幅表面/图层，或其他不会覆盖原厂控件的方案。不能直接全画布 clear 或改 SRC 覆盖来“修复”，那可能擦掉图标与参考线。
3. **解决关闭与状态切换后的清除。** 停止调用不是擦除。至少要在 QEMU 客体帧中证明关闭、切换比例和原厂刷新后基线恢复，并核对多个 bank。
4. **保护图形合同。** 当前只显式保留 scissor 和 CPU/临时栈；还需核对矩阵、blend、paint、表面身份、锁、资源销毁、绘制顺序及晚到回调。探针在 GPU worker 暂停后调用，不是现成安全生产回调。
5. **接入真正成片投影。** 复用 `aspect_frame.py` 的现有投影，调用 `aspect_mask.regions`；不要用固定 720×480 或固定中心矩形代替 4:3/5:4 的源窗口映射。几何模块的四矩形互不重叠，边角不能压暗两次。
6. **保留原厂网格，移除旧框线。** 还要区分 v17 在网格通道里的外边线与内部线，不能只停用黄色独立边框就认为网格样式完全符合新要求。网格关闭时遮幅仍应可用，网格开启时内部线不被遮幅覆盖。
7. 完成最小可清除遮幅后，再集成显式实验开关与诊断，建立与固定构建 SHA 绑定的执行/QEMU 证据；原厂父菜单生命周期和失败关闭要回归。
8. 最后再交付机身试验，检查首次设置、切换 4:3/5:4/宽幅、DISP、放大、对焦、回放、待机/唤醒、重启和 HDMI，并验证成片/机内预览没变。没有相应证据前不要宣称完成。

## 相关文档

- [半透明遮幅当前研究](aspect-soft-mask.md)
- [原遮幅可行性与历史限制](aspect-mask-feasibility.md)
- [v17 参考框/网格](aspect-frame-grid.md)
- [v20 通道管理与 v19 日志解释](aspect-border-channel.md)
- [v21 双线调整](aspect-border-stroke.md)
- [v22 减细失败](aspect-border-fine.md)
- [v23 小端帽仍突出](aspect-border-caps.md)

v19 日志的有效尾部曾出现序号 37、头计数 36 的快照不一致；相关 73 次通道关闭计数来自单独保留的 `excluded_menu_samples`。不要把它当作已经解决的导出一致性问题，也不要向公共文档搬运完整指针日志。

## 2026-10-18 v25 进展（本交接之后新增）

上面的优先顺序 1–3 已在 QEMU 显示模型内用真实构建产物走通，4 的图形合同以“GPU submit 入口钩子”形式落地，5 的动态投影尚未接线验证：

- 遮幅改为填充原厂取景 RGBA 图像表面（显示状态槽 `0xC18200C0`），不再画在 UI 画布或 UI renderer 时点。显示管线每周期先合成该表面再叠 UI 画布，因此图标、对焦括号与内部网格位于遮幅之上；UI 画布内填充与 renderer 时点填充均已实测否决（会破坏控件 / 被后续管线擦除）。
- 幂等：每周期写预乘黑字 + 逐矩形首末字比较跳过，重复提交不累积；停用只做一次清零填充即可回基线。
- 钩子：`0x1055D112` 入口 10 字节窗口跳到追加的 submit 函数，执行 mask 后复刻原 prologue 并回跳 `0x1055D11c`。fill/submit 由 `src/z30_extensions/features/aspect_mask.py` 生成。
- 构建：`tools/build_aspect_mask_trial.py --trial`；v25 trial 产物 size `33,989,722`、crc `0`、sha256 `2380cd0946bfbdae617a6f727f506031d9ed2b93e85edb0104e4692b51fb53d1`。
- QEMU 端到端：机型会校验 `-bios` 整文件哈希，构建产物不能直接启动；用 `tools/run_built_firmware_qemu.py` 收原厂镜像启动后经 gdbstub 回放差字节与追加尾部，回读 0 差异再跑取景。证据 `build/research/mask-firmware-v25-replay/`：inactive 基线 → active 带区 `[0,207,1]`（=255×(1−48/255)）而图标/括号/成片区不变 → erased 恢复基线。
- 仍未完成：动态模式（`aspect_mask=True`，网格钩子按 `aspect_frame` 投影写 state）未在 QEMU/相机验证；运行时写追加区在相机上是否可写未确认；真机 alpha、缓存一致性、休眠/回放/HDMI/放大等生命周期；真机网格开启/关闭与遮幅共存。下一次优先做动态模式接线与 QEMU 触发路径，再做真机验证。
- 旧的失败探针请勿重跑：`build/research/aspect-mask-context-qemu/` 与 `aspect-mask-alpha-worker-c-qemu/` 只保留为历史证据；现行入口是上面的 trial 构建 + replay。

相关新证据目录：`build/research/mask-build/aspect-mask-v25-trial/`、`build/research/mask-firmware-v25-replay/`、`build/research/mask-inplace3/`（层与生命周期等价验证）。

## 2026-10-18 v27 进展（本交接最新状态；交接时任务未完成）

v27 把动态模式改成“无 RAM 状态”：原计划在 GPU submit 钩子里读原厂网格记录 `0xC201A7F4` 现算 4 个成片外矩形。静态 trial 模式仍保留追加区 ROM state，仅供 QEMU 回放使用。

### 已确认（QEMU 显示模型，证据可复跑）

1. **静态 trial 生命周期在 v27 仍通过**：`tools/run_built_firmware_qemu.py` 回放 v27 trial（sha256 `b61710121d396a97c70beda9aaa4bce74ce29a0ca6561954d416d381ce0791d6`，size 33,988,826，crc 0，82 edits）：inactive 基线 `[0,255,1]` → active 带区 `[0,207,1]` 且成片区/图标/括号不变 → erased 恢复基线。证据 `build/research/mask-firmware-v27-trial-replay/`。
2. **`aspect_get` 在 live view 可用（关键新事实）**：`tools/probe_aspect_get_live_qemu.py` 证据 `build/research/aspect-get-live/context.json`：live-before-menu `state=0x0/anchor=0x0`（块在第一次打开扩展菜单时才分配）；menu-open 与 **live-after-menu** 均为 `state=0xc2c68138 / anchor=0xc2c31080` → 关闭扩展菜单回到取景后，扩展状态块仍可经 `aspect_get` 解析。**此前“退出菜单锚点归零、块丢失”的结论作废**；静态分析（`build/research/render-context/mask-layer-notes.md`，analyst）也未找到清 anchor 的写者（未清的 GAP 需写监视点复核）。副作用：重启后第一次打开扩展菜单前 `aspect_get` 返回 0，遮幅不会工作（真机需确认 `aspect_restore` 是否会更早建立块）。
3. **grid record 路线在 QEMU 完全不激活，菜单验证是空洞通过**：QEMU 取景 fixture 没有打开原厂网格显示，`0x10454D46` 的 RMW 网格钩子不运行，`0xC201A7F4` 始终为 0。`build/research/mask-firmware-v27-menu/` 虽然打印 “PASS 4 native aspect menu switches; 4 live-view mask checks”，但 `mask-observations.json` 中所有 profile 的 `grid_record` 都是 `[0,0,0,0]`、`expected_regions` 为空 → 实际只做了“基线未变”的断言，**不能作为动态遮幅已验证的证据**（工具 `tools/verify_mask_menu_qemu.py` 已改为读取 grid record 做像素断言，故目前会空洞通过，需按下一步改造）。
4. v27 dynamic 构建成功：sha256 `96500acac62dd48a1518330dc738a5c3fcd76e48c55ed48661b2f2f71e76272f`，size 33,988,698，crc 0，82 edits；`soft_mask.state='grid-record+0xc201a7f4'`，fill=`0x12088149`，submit=`0x12088349`。单元测试 12 项通过（含 `grid_regions` 4 项）。

### 交接时卡在哪

- **动态几何来源**：grid record 依赖“原厂网格显示启用”，不满足“网格关闭时遮幅仍可用”（本文件优先顺序第 6 条），且 QEMU 下无法触发。**task 未完成的核心就是这个**。
- **真机全部未验证**：alpha 观感、缓存一致性、休眠/回放/HDMI/放大/DISP、网格开/关共存、首次设置与异常关闭生命周期。

### 下一步建议（按顺序）

1. **改用 profile 表 + `aspect_get`（不依赖 grid record、不需要 RAM 写）**：
   - `aspect_ui.prepare` 的 mask 段：按 aspect 报告的 `profiles` 的 `ratio` 预计算 7 档 photo 矩形（profile 0 = 满幅；其余按 720×480 居中 letterbox、两端偶数对齐，公式与旧 `verify_mask_menu_qemu.expected_rects` 相同），`plan.alloc` 生成 ROM 表（每档 4×u32: x0,y0,x1,y1，7 档=112B），地址传给 `aspect_mask.source_codes`。
   - 动态 submit 改为：`invoke(f['aspect_get'])`（只读、无锁、无分配，可在 GPU submit 钩子内安全调用）→ 返回 0 不填；`ldr profile=[r0]`；`profile==0 或 >6` 不填；查表得 photo rect → 用现有“外区 4 矩形”构造（同 `grid_regions` 语义）→ 填 image surface。失败关闭：任何一步非法都不填，旧暗带由显示管线每周期重写 image surface 清除（已在 QEMU 验证该清除机制）。
   - 注意：沿用 `aspect_get` 返回的 state 指针前不要写它；本方案只读。
2. **改造 `tools/verify_mask_menu_qemu.py`**：恢复按 profile ratio 计算的 `expected_rects` 断言（mask 区像素≈`round(baseline*(1-48/255))`，成片区/图标不变，profile 0 回基线），并断言观测非空（禁止再空洞通过）。
3. **重跑** dynamic 构建 + QEMU 菜单验证（命令见下），取得非空洞证据后更新 `docs/aspect-soft-mask.md` 与本交接。
4. **真机边界**交给有相机的人：alpha 校准、休眠/回放/HDMI、网格开关共存、首次设置/菜单关闭/重启回归；没有证据前不要宣称完成。

### 文件、工具、命令

- 源码：`src/z30_extensions/features/aspect_mask.py`（v27：`GRID_RECORD`、`grid_regions()`、`trial_state()`、`source_codes(fill/submit)`；动态 submit 目前读 grid record，待改 profile 表）；`src/z30_extensions/features/aspect_ui.py`（mask 段、`soft_mask` 报告 version=2；mask 与 trace/border extras 互斥）。
- 构建：`python3 tools/build_aspect_mask_trial.py --source Z_30_0120.bin --output <dir>/EG209020.BIN --deps build/linux-verify-deps --trial`（或 `--dynamic`）。
- trial QEMU 回放：`/usr/bin/python3 tools/run_built_firmware_qemu.py --report <json> --firmware <BIN> --stock-firmware ../z30-qemu/.local/firmware/eg2090_0120a0.bi --output-dir build/research/mask-firmware-v27-trial-replay`。
- 动态 QEMU 菜单验证：`/usr/bin/python3 tools/verify_mask_menu_qemu.py --qemu-root ../z30-qemu --image <dynamic BIN> --report <json> --fixture build/research/aspect-diagnostic-v6/sd-fixture.json --output-dir build/research/mask-firmware-v27-menu --language zh-Hans --profiles 1 4 6 0`。
- 层/生命周期探针：`tools/probe_mask_inplace_qemu.py`（等价验证）、`tools/probe_aspect_get_live_qemu.py`（本页第 2 条证据）、`tools/probe_rom_write_qemu.py`。
- 测试：`PYTHONPATH=src:build/linux-verify-deps:build/verify-deps /usr/bin/python3 -m unittest discover -s tests -p "test_aspect*.py"`（12 项）。
- QEMU 回放顺序（漏任何一步都会在开菜单时 `Stock API did not return`，如 0x1207f6c0）：写 ROM 差字节（并收集 scatter 列表）→ 写尾部（`0x10020000+stock_size-2`）→ `s.input(0x102355ee,[0xc182e688,1])` + `run_for(2)` → 写 scatter RAM 副本（`0xC1800000+(address-0x11f81c94)`）→ fixture services/config → 语言/字体调用 → `start()`。
- 环境：WSL + keystone（`build/linux-verify-deps`）；QEMU 机型对 `-bios` 整文件 sha256/长度有指纹闸门（构建产物不能直接启动），`../z30-qemu` 本轮只读；回放 gdbstub 可写 ROM 别名。

### 不要做的事

- 不要把 `build/research/mask-firmware-v27-menu/` 的 PASS 当作动态模式已验证（空洞通过，原因见上）。
- 不要重跑旧的失败探针 `build/research/aspect-mask-context-qemu/`、`aspect-mask-alpha-worker-c-qemu/`；它们只是历史证据。
- 不要写相机/存储卡；不要把 QEMU 显示模型证据说成真机证据。

## 2026-10-18 v31 最新进展（以本节为准）

用户已要求读取本交接继续实现。动态遮幅现已具备可供机身试验的固定 EG 候选，尚无物理相机验收；本轮没有写相机/存储卡、commit 或 push。

### 最终候选与验证

- **仅试验 v31**：`output/full-aspect-soft-mask-v31/EG209020.BIN`，SHA-256 `814b9ffaebd12d15522a3319c1d8642c5e1f1376afb1f4d3d23bbe027aa67b3a`，大小 33,990,106，CRC residue 0。同目录有构建报告和 `validation.json`。完整说明、复跑命令、机身验收见 [v31 说明](aspect-soft-mask-v31.md)。
- 动态几何：`aspect_get` + 原厂视口快照 `0xC237C7B4+8`、源窗口 `0xC237C3D2`，复用 `aspect_frame` 成片投影。**不要按 v27 的旧建议使用固定 LCD 比例表，也不再依赖网格记录。** 读取前后检查状态/几何一致，动态状态仅在私有栈。
- 绘制：保留 `0x1055D112` 钩子，但只在 `0xA03` 命令、720×480、E 区叠加 bank 目标的合成前边界填充；其他绘图提交立刻续接原代码。透明像素或旧 alpha-48 黑色遮幅可写，已有非透明原厂图形保留。v17 网格通道仅保留内部线，移除其外边线；黄色框关闭。
- `build/research/aspect-mask-v31/`：24 组动态 ARM 执行、4 个独立预期投影、14 个失败关闭条件；36 轴、120 投影网格组合与 6 个边界条件；63 原厂几何和 36 网格组合；JPEG/预览选择路径检查；默认 trace 构建与 v17 逐字节相同。14 项 aspect 单元测试通过。
- `build/research/mask-firmware-v31-menu/`：从真实构建回放字节，走原厂焦点/Enter/父页面导航，七档 `[1,2,3,4,5,6,0]` 的实际 LCD 像素检查通过。每个启用档位有非空变暗区域，关闭恢复基线；`ui-frame-checks.json` 的 21 个原厂图标/对焦括号前景采样通过。

### 本轮发现并修正的验证问题

- 旧菜单工具没有进入实际 LCD 取景，且空区域也能通过。本轮接入 `liveview_session` 的设备选项与 `start()`，要求 720×480 实际帧、非空区域和明亮采样点确实变暗；窄暗带也有独立采样。
- 七档背景遮幅通过仍不足以保证 UI：v29 的控件检查失败；v30 仅保留非透明像素仍有异常。无遮幅 submit 对照可显示控件，因此没有交付这两版。v31 进一步过滤提交边界后才通过七档控件检查。底层线程/缓存问题的全部物理原因尚未证明，不要扩大结论。
- `tools/verify_aspect_mask_frames.py` 读取原始客体 PNG，检查不透明控件前景；半透明 tile 背景可随下方画面正常变暗。工具也保留失败报告，禁止只拿背景 PASS 宣称完整 UI 成功。

### 证据边界与下一步

- **QEMU 视口为显式夹具**：辅助无传感器启动后的视口/源窗口全零，故 `--geometry-fixture` 注入 `(0,36,720,442)` 和 `(0,292,5568,3420)`，均记录原值。未注入遮幅状态/网格记录；网格属性为关闭。原厂照片投影有 ARM 检查，真实传感器填充这些字段尚未证明。
- 模型没有原厂网格硬件光栅；内部线只完成原生 ARM 几何验证。**不能说 QEMU 已看见网格开启后的实际白线**，真机应测试网格开/关与遮幅共存。
- 下一步按现有 EG 加载流程进行机身试验：首次进入 Photo aspect 选择档位，核对 4:3/5:4 边界、宽幅切换、关闭恢复、原厂图标/参考线，随后测菜单/回放、拍照、DISP、放大对焦、待机唤醒、重启和 HDMI。首次开菜单前状态块/自动恢复、真实表面所有权、缓存与性能仍未验证。
- 用户选定的淡黑遮幅样式和既有成片/回放修复继续保留；不要回到黄框路线。v29/v30 为失败历史，不应写卡。无关无线与预拍摄工作区改动仍应保留。


## 2026-10-18 机身反馈与 v34 强度调整

用户已在机身确认 v31：遮幅边界与成片一致，关闭网格后仍有遮幅，网格线及原厂图标正常。此反馈覆盖这些功能，不能扩大为全部电源、回放、HDMI 或缓存合同已验证。

用户随后连续要求加深颜色。v32（25%）和 v33（38%）是中间强度，最终本次交付 [v34](aspect-soft-mask-v34.md)，alpha `128/255`，约 50%。产物 `output/full-aspect-soft-mask-v34/EG209020.BIN`，SHA-256 `9856deb90169035f8ab56935b6531fe01df021a823b51301f4984ffc8a24e29f`。与已获机身反馈的 v31 仅有两个 MOVT 强度立即数和末尾 CRC 不同，独立反汇编和完整字节差异见 `build/research/aspect-mask-v34/v31-byte-diff.json`。

本版 24 组动态执行、14 个失败关闭条件及 14 项 aspect 单元测试通过；QEMU 5:4、XPAN、关闭三档遮幅颜色及基线恢复通过，两处原厂相机/信息图标共六个前景采样正常。完整九点 UI 比较仍有一个失败：5:4 的底部 `(598,452)` 字段与基线不同，位于视口及所有遮幅矩形之外；原因未确定，不能称所有 UI 检查通过。`validation.json` 保留失败和对应 SHA；不能忽略该差异或直接归因于强度。v32 的关闭档位也出现过该字段差异，v33 的三档九点则全部匹配。

源码的 `DEFAULT_ALPHA` 已统一驱动填充字及识别旧遮幅的字，不再保留 alpha-48 硬编码；构建默认仍不启用遮幅。开发侧没有写相机/存储卡、commit 或 push。接下来等待 v34 机身深浅观感反馈；如继续做生命周期或底部字段调查，应另取对应证据，不能借已有局部反馈宣称全部完成。


## 2026-10-18 v36：遮幅补缝与菜单分类（历史）

用户在 v34 后报告顶部、边缘中间有小缝；对菜单澄清为“主要是选项顺序或分类”。本轮最终候选为 [v36](aspect-mask-menu-v36.md)：`output/full-aspect-mask-menu-v36/EG209020.BIN`，SHA-256 `6ef172da19c761b3267a516f7c6e909b079f61eb78ad33eb9220d6d8f792461b`，大小 33,991,294，CRC 0。保持 alpha 128（约 50%）。同目录 `validation.json` 绑定证据，附两张原始客体菜单预览。

1. 遮幅实际内侧投影不变。外侧延伸至 LCD 边缘；被原厂视口裁住的成片边沿沿该方向延伸，防止宽幅上下亮条、窄幅侧边端部空段。
2. **动态遮幅不再跳过下层非透明像素。** v35 扩边后仍保留逐像素跳过，会保留下层残留。v36 仅在既有 bank-clear 合成前边界完整填充下层，原厂 UI 画布后合成。不要把 v31 的“像素跳过”当成必须保留的控件保护；真正约束是正确表面和提交时点。历史未 gated trial 独立保留原保护，不算动态验收。
3. 根菜单顺序：照片画幅、视频码率、快门声音、SD 卡功能。声音四项移入私有 page 962；文件/开关页父级为声音页，确认与错误关闭回正确声音上级。仅声音预设保持直接四项。布局报告的 `menu_categories` 提供角色/行/控制 ID；验证工具不再假定最后一行就是画幅/码率。
4. 原生执行：24 组动态、14 失败关闭条件、10 菜单动作、8 功能组合共 67 个动作、15 声音记忆及 96 码率案例通过；36 轴/120 投影/6 边界与 63 几何/36 网格检查通过；31 个相关单元测试通过。
5. QEMU LCD：七档、21 原厂控件前景、3736 个边缘像素通过。三种语言的独立 GPU 菜单会话通过，并检查了实际子页、侧栏和返回。LCD 辅助扫描会覆盖菜单 GPU 输出，故不要用 LCD 会话里的旧父页面图作为子菜单视觉证明；菜单捕获等待标题绑定并取最新帧。

证据：`build/research/aspect-mask-menu-v36/`、`mask-menu-v36-lcd/`、`menu-v36-{hans,hant,en}-gpu/`。QEMU 使用显式无传感器视口/源窗口，未实现原厂网格硬件光栅。本版机身小缝、网格及新菜单体验仍需用户确认；未新增实拍、HDMI、全部电源生命周期或缓存所有权证明。

保留此前已获机身确认的裁切/成片路径及网格投影。本轮没有写相机/存储卡、commit 或 push，也没有处理无线/预拍摄等其他工作区修改。下一步按现有 EG 加载流程检查四周亮缝、网格开关与声音子菜单返回；若仍有具体缺口，需要对应比例和画面证据定位，不能简单移动成片边界或重新覆盖 UI 画布。

## 2026-10-18 v37：当前拍摄画幅污染旧照片普通回放

用户明确新问题为“普通回放变形，放大后正常”。最新候选 [v37](aspect-playback-v37.md)：`output/full-aspect-playback-v37/EG209020.BIN`，SHA-256 `c1724f771dc5d6b5a8348794f94d013456ec68f8bb29aae5b7dec16d2379fa9b`，33,991,510 字节，CRC 0。保持 v36 的菜单与遮幅；机身回放待复测。

- `PREVIEW_CALLERS` 原先误含共享 `0x10221AE2` 的两个预览分支和 `0x1038FA8C` 的小预览查询。本轮移除；后者缩放的分子、分母都须隔离，不能只改一边。
- 新 `aspect_file_guard` 按 25 个精确原厂返回地址，让已定位文件/元数据/布局查询保留原厂指针，覆盖主图输出、内部、缩减尺寸。直接拍摄预览生成、裁切和现有取景守卫保持。报告 `playback_isolation_version=1`。
- `tools/verify_aspect_playback_isolation.py`：240 组文件记录/当前选择组合通过，复现旧 v36 的 84 组污染；56 组按文件实际宽高拟合通过，25 个真实 BL 守卫逐个执行。是合成文件记录下的原厂 ARM 函数证据，不是完整 JPEG 解码或机内相册截图。
- `build/research/aspect-playback-v37/`：63 几何/36 网格、360 独立预览生成、42 组完整裁切参数与 v36 等价、24 遮幅/14 守卫、10 菜单动作、31 项单元测试通过。十二块只读成片表及遮幅 fill/project 代码与 v36 逐字节相同。
- QEMU 七档取景和 3736 边缘像素通过。原始首轮控件检查有一个失败：5:4 的 `(598,452)` 为蓝色，基线为白色；保留 `ui-frame-checks.json`，不能写成全部控件通过。对照与复查结论见本版说明及候选 `validation.json`。

补充对照：无遮幅 5:4 保留上述像素；v36 同序列 4:3→5:4 对照与 v37 同序列复跑均为 6/6 前景采样通过，证据 `build/research/playback-v36-ui-control/` 和 `playback-v37-ui-repeat/`。首轮差异未定因，保留全部失败与后续对照，不得据复跑抹去首轮结果。

优先用同一组旧照片切换当前画幅再普通回放/放大对照，不需要先重拍。随后新拍 4:3、5:4、XPAN，核对保存及内嵌预览，防止共享元数据隔离引入新问题。文件内已经生成错误的预览不会自动修复。如果旧照片仍异常，继续定位实际文件拟合及遮幅的应用上下文；GPU 目标尺寸/命令类型守卫并不等于已证明完整应用生命周期隔离。没有物理证据前不宣称相册修复验收。

## v37 实机复测失败：下一步只做遮幅入口 A/B

用户确认 v37 仍有问题，随后澄清物体比例正常但边缘不透明；不能直接判断是半透明遮幅误入。交付[遮幅关闭对照](aspect-playback-mask-control.md)：`output/full-aspect-no-mask-control-v37/EG209020.BIN`，SHA-256 `e3e041543f4d6891cdf439475588c0e03b2585d30f124771cc08026a4a431798`。与 v37 只有原厂 GPU submit 入口 10 字节窗口和 CRC 不同，菜单、成片、尺寸钩子全部保留。取景新增遮幅也会停用，是有意的定位副作用。

待机身用同一张旧照片比较 4:3 与 XPAN 设置下的普通回放。若取景遮幅已消失但不透明回放边缘仍变化，追原厂布局；若回放边缘也消失，再定位遮幅真实会话范围及清除。没有这个物理对照前不要继续声称根因已确定。

本地 v38 曾尝试显示源状态 `0xC18200DC == 1` 守卫：24 动态/16 守卫的合成原生执行通过，但实际 QEMU 普通取景 XPAN 遮幅检查失败，不能交付。源码已逐项撤回，重新构建 SHA 与 v37 完全一致。不要把显示源位、记住的拍摄后端或目标大小误当成已经证明的相册状态；失败产物标记 `rejected_do_not_load`，报告留在本地研究目录。本轮未操作相机/存储卡，未 commit/push。

## 2026-10-18 对照实机正常；v40 恢复取景遮幅候选

用户对上述停用入口对照明确反馈“这下正常了”。该物理 A/B 仅停用 submit 入口，确认新增遮幅入口参与故障。不能把该反馈写成 v40 已获实机验收，也不能凭透明度倒推出全部合成机制。

最新产物与完整实现/验证：[aspect-photo-scope-v40.md](aspect-photo-scope-v40.md)。模块 `output/full-aspect-photo-scope-v40/EG209020.BIN`，SHA-256 `329576d1048d7df5436e1654ee37342d6f3076c874ef1d7983fab22745111729`，33,992,566 字节，CRC 0。保持既有成片、分类菜单、alpha 128 和 v37 文件查询隔离，v40 实机复测待完成。

- 原厂照片叠加条件 `0x104BE4A0` / `0x10306328` 的照片子集在投影前后检查；不读取网格偏好，不在 GPU 回调中调用快照锁。显示源标志和 DSID 3500/3600/3700 均不是已证明的拍摄/回放应用状态，后者是 HDMI/MIC/Speaker 激活属性。
- `aspect_mask.py` 现已接入动态固件，`DEFAULT_ALPHA=128`；旧源码地图是黄框阶段历史。私有历史位于 `aspect_get+16`，占 128 字节，后续菜单结构顺延。bounded try-lock 保护自定义记录。
- v39 增加条件后仍有旧像素，QEMU 明确失败；v40 在同一当前表面上，只清理此前矩形内仍匹配本版遮幅颜色的像素，原厂已替换像素保留，替换过的旧表面不解引用。不要回退到“仅停止绘制即可”的 v39。
- 本地原生：24 动态、19 拒绝条件、1,116 条件对照、六档生命周期、六次比例切换、63 几何/36 网格、240 文件查询/56 拟合、42 拍摄参数等价、10 菜单动作、31 单元测试通过。
- QEMU 七档、21 控件前景、3,736 外缘像素和 4:3/XPAN 的清除/恢复客体帧通过。视口、源窗口与照片条件是显式夹具，**没有进入真实相册或解码真实旧照片**。真实条件变化、缓存和性能仍须机身复测。

下一步优先测试旧照片普通回放与放大、回到取景恢复、功能关闭清除及网格开关。保留停用入口对照作为已获反馈的退路。没有新的机身结果前，不宣称回放问题已经最终修复。本轮无写卡、commit、push；保留其他任务工作区改动。

## 2026-10-18 v40 实机仍失败；v41 采集实际回放状态

用户反馈：“照片回放还是会被裁切成设置的画幅”。v40 的 `validation.json` 已标记 `camera_retest_failed_issue_unresolved`，保留本地检查及边界。不能只重复改视图种类/就绪位夹具继续称修复。停用入口对照的正常反馈仍有效，但不证明具体的不透明边缘合成机制。

当前 [v41 定位模块与操作](aspect-context-trace-v41.md)：`output/full-aspect-context-trace-v41-final/EG209020.BIN`，SHA-256 `19425d7f007171a7f783ec20b0002f1c1a62bdab83073f0aa78258eaf833be0e`，33,995,210 字节，CRC 0。**仍预期重现问题，不是修复版。**

- 新 `aspect_mask_trace.py` 在已有遮幅锁内采样 20 个原厂标量字段和 v40 条件结果，变化才记录，64 槽；没有图片或任意 RAM 转储。原厂外层 `1044FCF6`、UI 属性 `7E`、服务阶段等也记录，但尚不能推断它们一定区分实际回放。
- 通过现有显式 `aspect_trace=True` + `aspect_mask=True` 构建选择，动态遮幅诊断走新 WMD1 格式。128 字节遮幅状态保持在 `aspect_get+16`，日志在 `+144`，后续菜单随总分配移动；旧拍摄日志路径独立保留。
- 文件服务只在画幅子菜单导出时执行；先冻结并检查生产者锁，锁忙不等待。短写/关闭失败均解除冻结。`tools/read_aspect_trace.py` 兼容 WMD1；期望文件 8,216 字节。
- 记录/导出原生验证通过；未开诊断的正常构建 SHA 仍等于 v40。QEMU 首轮脚本变量名 `start` 遮蔽启动函数，在第二档失败；脚本改为 `trace_base` 并另目录重跑，固件未因该脚本失败变动。

需要用户保持 4:3：取景三秒 → 旧 DX 照片普通回放 → 放大再缩回 → 返回取景 → 进入画幅子菜单导出（可退出再进一次），交回新的 `WL-ASPECT.DBG`。不需要原始照片；不要额外堆叠比例/重启操作覆盖状态。收到后以真实字段变化决定修复，不靠假定的 UI 状态枚举。本轮无写卡、commit、push。

## 2026-10-18 收到 v41 机身记录；v42 生命周期候选

用户提供的 WMD1 文件为 8,216 字节、13 条完整状态，原件/解析留在忽略的 `build/research/aspect-camera-context-v41/`。非关闭档位的六条阶段 4 记录仍让旧照片条件返回 1，其余原守卫字段没有区分作用。记录未逐条标注用户操作，而且采样受旧目标筛选限制，不能把阶段 4 直接定义成相册枚举。

独立执行原厂 `1044E4C8` 快照等待分支，阶段 0/3/4 不进入等待查询，1/2 进入；这支持加入原厂渲染阶段限制。另一个确定的代码缺口是旧清除发生在 A03/尺寸/目标筛选之后，其他绘制路径会直接返回，导致之前的暗区无机会清除。

最新候选见 [aspect-lifecycle-v42.md](aspect-lifecycle-v42.md)：`output/full-aspect-lifecycle-v42/EG209020.BIN`，SHA-256 `612d9d82c75b0409584fbbad483edb02f9c299d49f4868df9e4506051b6de1c3`，33,995,498 字节，CRC 0。**随后用户反馈“正常了”，本次回放问题已获机身复测确认；未提供逐档覆盖。**

- `aspect_mask_photo_context` 附加阶段 1/2；`aspect_mask_retire` 在 submit 目标筛选前处理失效阶段，已有有界锁/颜色/矩形/当前表面检查保留。原厂改写像素和替换表面不清除。
- 保留 WMD1 日志，在提前清除路径的锁内也采样，后续可观察旧版被目标筛选挡住的状态。设置文件、成片几何、预览尺寸、菜单分类、遮幅强度和网格不改。
- 实机 13 组标量的原生回放重现旧版六组阶段泄漏；12 组后续非取景提交重现旧残留并验证新清除。GPU 描述符/表面是明确夹具，不称为实际回放命令。
- 24 动态/23 拒绝条件/1,116 照片条件、原厂阶段分支、63 几何/36 网格、10 菜单动作、31 单元测试、日志环/导出均通过。十二块成片表及预览尺寸与 v41 相同。
- QEMU 阶段 4 清除/2 恢复的客体画面通过；完整取景与菜单证据见 `build/research/mask-lifecycle-v42-lcd/` 和产物 `validation.json`。阶段 setter、视口/源窗口和 SD 服务为显式刺激/夹具，未进入真实相册或解码照片。

此前提出旧 DX 照片普通回放、放大和返回取景，以及 5:4/XPAN/关闭/网格复测；其后的用户反馈见下节，未提供逐项覆盖。若后续再现异常，导出本版新的 WMD1 文件；新的提前清除采样范围比 v41 更广。不要回退到重复调照片后端/就绪位或先过目标筛选才清除的路线。本轮仅只读原日志，未写卡、commit/push，也未改其他任务文件。

## 2026-10-18 v42 用户实机反馈正常

用户在 v42 回放修复复测后反馈：“正常了”。此前复测上下文为旧 DX 照片普通回放与返回拍摄取景；用户未逐项说明比例或操作覆盖，不据此扩大为全部档位、缓存并发及电源/HDMI 生命周期已验证。v42 模块保持不变，产物哈希及本地验证见 [aspect-lifecycle-v42.md](aspect-lifecycle-v42.md)。v40 失败记录继续保留。
