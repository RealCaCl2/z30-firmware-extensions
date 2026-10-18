# v36：遮幅补缝与菜单分类

日期：2026-10-18。用户报告顶部、边缘中间有小缝，并明确希望按选项顺序/分类优化自定义菜单。本版保留 `128/255`（约 50%）遮幅强度。

## 固定产物

- 模块：`output/full-aspect-mask-menu-v36/EG209020.BIN`
- SHA-256：`6ef172da19c761b3267a516f7c6e909b079f61eb78ad33eb9220d6d8f792461b`
- 大小：33,991,294 字节；CRC residue 0。
- 同目录附构建报告、`validation.json` 及原始客体菜单预览。
- 这是与既有加载流程相同的 EG 模块，不是外层 Nikon 更新包；开发侧没有写相机/存储卡、commit 或 push。

## 遮幅改动

成片投影的内侧边界保持原计算；仅将遮幅外侧延伸到 LCD 边缘。若成片边界只是被原厂视口裁住，则沿该方向延伸到画布边缘，不把视口边沿误当成新的成片边沿。因此宽幅上下遮幅从屏幕顶/底开始，4:3/5:4 的侧边遮幅覆盖完整高度，避免外端留亮条。

原来的像素保护会跳过下层表面已有的非透明像素，可能形成局部缺口。动态模式现只在原厂叠加 bank 的清除/合成前边界执行，完整替换成片外的下层像素；原厂 UI 画布在其上合成。没有清除 UI 画布，没有恢复旧黄框，也没有重新移动成片边界。历史 ungated trial 仍保留像素保护，不作为本版显示证据。

## 菜单改动

`full` 的自定义菜单首页按用途排列：

1. 照片画幅，显示当前比例。
2. 视频码率，显示当前档位。
3. 快门声音，显示开关；其子菜单收纳启用声音、声音文件、播放声音、重新加载。
4. SD 卡功能，显示模块状态。

声音文件和开关选择完成后回到“快门声音”，错误确认也返回对应的声音页面；返回箭头逐级回到分类首页和原厂设置。仅声音预设继续直接显示四项声音操作，不增加空分类。各可选功能组合只出现自身具备的分类。

分类首页、声音子页和各选择页保留原厂侧栏、标题、返回图标与选中标记；中英文标签及当前值使用独立列。声音、码率、画幅配置文件与恢复逻辑保持分离。

## 验证

证据绑定同目录 `validation.json` 的固定 SHA：

- `build/research/aspect-mask-menu-v36/dynamic.json`：24 组动态 ARM 遮幅执行、14 个失败关闭条件；透明/非透明残留均被完整填充，重复提交不累积。
- `frame.json` / `native.json`：36 个轴、120 组投影网格组合和 6 个边界条件；63 组原厂几何及 36 组网格检查，包含成片/预览选择路径。
- `menu-actions.json`：10 个实际 ARM 菜单动作、正确父页面、声音文件加载/保存、开关与错误返回案例。
- `menu-variants.json`：声音及三项可选功能的全部 8 种组合；仅声音保持原结构，其他组合按同一分类顺序组织。
- `sound-settings.json` / `bitrate.json`：15 个声音记忆案例、96 个码率记录/设置案例。18 项 aspect 单元测试通过。
- `build/research/mask-menu-v36-lcd/`：七档 `[1,2,3,4,5,6,0]` 的原厂菜单选择、实际 LCD 遮幅像素与关闭恢复通过；21 个原厂控件前景采样通过。`edge-continuity.json` 检查 3736 个客体边缘像素，核对外侧连续性。
- `build/research/menu-v36-{hans,hant,en}-gpu/`：独立原生 GPU 菜单会话，验证简体、繁体、英文分类、声音子页、文件/开关返回及原厂设置恢复，并检查实际客体帧。

菜单图像和取景图像分别验证：辅助 LCD 取景模型持续扫描会覆盖菜单 GPU 输出，因此不能拿该模式下的旧父页面截图判断子页面。菜单会话不启用该取景扫描模型；捕获前等待原厂标题控件绑定，并使用最新帧。所有预览均为客体绘制，没有主机重画。

QEMU 取景仍采用明确的无传感器视口 `(0,36,720,442)` / 源窗口 `(0,292,5568,3420)` 夹具和模型色条。它不实现原厂网格硬件光栅：内部网格有 ARM 几何证据，v31 曾获用户机身确认；本版补缝及菜单分类的机身效果尚待确认。未新增照片拍摄、HDMI、全部电源生命周期或缓存所有权证明。

## 复跑

```bash
python3 tools/verify_mask_menu_qemu.py \
  --qemu-root ../z30-qemu \
  --image output/full-aspect-mask-menu-v36/EG209020.BIN \
  --report output/full-aspect-mask-menu-v36/EG209020.json \
  --fixture build/research/aspect-diagnostic-v6/sd-fixture.json \
  --output-dir build/research/mask-menu-v36-lcd \
  --profiles 1 2 3 4 5 6 0 --geometry-fixture

python3 tools/verify_mask_menu_qemu.py \
  --qemu-root ../z30-qemu \
  --image output/full-aspect-mask-menu-v36/EG209020.BIN \
  --report output/full-aspect-mask-menu-v36/EG209020.json \
  --fixture build/research/aspect-diagnostic-v6/sd-fixture.json \
  --output-dir build/research/menu-v36-hans-gpu \
  --verify-menu-categories --menus-only --language zh-Hans
```

试用时重点检查：4:3/5:4 的侧边上下端、各宽幅的顶部/底部及边缘中部是否仍有亮缝；原厂网格/图标是否正常；声音子菜单的确认、取消、错误提示是否回到正确上级。普通 `full` 构建也采用新分类菜单；遮幅仍由显式试验开关启用。
