> 历史归档：2026-10-17 的 v8 状态，已被后续实机反馈与实现替代。最新交接见 [aspect-handoff.md](aspect-handoff.md)。

# 交接文档：多画幅（aspect-profiles）4:3 / 5:4 实机失真问题

- 日期：2026-10-17
- 仓库：`z30-firmware-extensions`，`main` @ `f663058`（工作区干净；另有一批与本任务无关的 wireless / precapture 未跟踪文件，见 §7）
- 状态：**功能未完成**。v8 在实机上 4:3 / 5:4 仍然失真（两侧裁切未生效，整幅横向压缩）；用户已选择"全分辨率"路线（B），本任务在实验固件完成前被要求暂停。
- 本次会话没有修改任何 `src/`、`tests/`、`docs/` 文件；两个后台研究子代理已中断，只在 `build/research/aspect-vehicle-tests/` 留下下载的 Unicorn/Capstone 依赖缓存（该目录被 gitignore，可整体删除）。

## 1. 交接摘要

- `aspect-profiles` 预设提供自定义菜单 7 档画幅（OFF / 4:3 / 5:4 / 2:1 / 2.35:1 / 2.39:1 / 65:24）+ 取景参考框，通过切换原厂"图像区域"实现。
- 实机结论（2026-10-17 用户反馈，优先于所有旧文档）：
  - 4:3、5:4 拍出来/预览都是**整幅横向压缩**，两侧裁切没有发生；
  - 其余 4 个宽比例（2:1、2.35、2.39、65:24）正常；
  - 用户试过多组画幅状态组合（原厂 16:9 / DX / 1:1 下反复切换）**全部失败**；
  - 用户明确表示 "v8 也不正常，依旧变形，有时候正常有时候不正常"。
- 根因判断：v8 只是让模拟器里的原厂配置函数 `0x1014237A` 算出横向裁切字段（trim x = 308 / 464），但**实机拍摄管线不消费这个字段**，于是把 5568 宽的整幅画面直接缩放到 4952 / 4640 宽。
- 关键技术线索：原厂三个原生画幅里，只有 1:1（area 4）的配置带横向裁切（trim x=928）；16:9（area 2）和 DX（area 0）的配置 trim x 恒为 0。"横向裁切"能力只出现在 1:1 的原生窗口路径上，v8 在 area 2 分支里"教"出来的裁边实机不认。
- 用户决策：**走 B（全分辨率）**——不要降分辨率的 A 方案（1:1 车架，横向上限 3712）。
- 最便宜的判定实验：stock C1.20 + 仅表补丁，让 **DX 原生窗口**的配置输出 4952×3712 / 4640×3712（trim x = 308 / 464），实机拍 DX 档 L 照片，看是"裁切"还是又一次"挤压"。一次刷机即可判定横向裁切到底能不能被配置字段驱动。

## 2. 版本与产物

| 项 | 值 |
|---|---|
| 原版固件 | `Z_30_0120.bin`，SHA-256 `1a5449ef505f1f347ed4e066a6a3f588ea542240680e83f4ebcae3b16da3434a` |
| 当前 v8 产物 | `output/full-aspect-profiles-area-sync-v8/EG209020.BIN`，SHA-256 `3439408272dd452d81d5bbd10614ccff747ace91ae81e49a4d11dd8aa727d5a8`（构建 `full,aspect-profiles`） |
| 版本历史产物 | `output/full-aspect-profiles-*`：v2 jpeg-fix、v3/v4 preview-fix、v5 source-fix、v6/v7 diagnostic、v8 area-sync（均 build 产物，不随仓库分发） |
| 构建入口 | `python patch.py <preset> --source Z_30_0120.bin --output <路径>`（如 `python patch.py aspect-profiles ...`、`python patch.py full ...`） |
| 预设定义 | `src/z30_extensions/build.py`：`PRESETS` 含 `aspect-profiles`；`full` = `FEATURE_ORDER` 全 8 项；`aspect-profiles` 会自动带上 `shutter-sound`；`aspect_trace` 只能与 `aspect-profiles` 搭配 |
| 单元测试 | `$env:PYTHONPATH = (Join-Path (Get-Location) 'src'); python -m unittest discover -s tests -v`（共 59 项） |

**文档中已被实机推翻的结论（必须修正）**：`docs/aspect-profiles.md` 第 98 行"2026-10-17 用户进一步确认当前全部功能均已通过实机检验"、第 112–113 行"2026-10-17 用户测试 v8 后反馈'正常了'"、`docs/verification.md` 第 3 行同类表述，以及 `docs/aspect-profiles.md` 第 106 行"原厂设为 16:9 时 4:3/5:4 正常；DX 时整幅横向压缩"——后者与本次"所有组合都不正常"的反馈冲突，需要重新向用户确认或直接作废。

## 3. 已确认的技术事实

### 3.1 原厂画幅与配置函数

原生图像区域（用户属性 `0x501`）：

| area | 画幅 | 父区域 | 起点 | 原生读出 | 配置 trim | 配置 trimmed |
|---|---|---|---|---|---|---|
| 0 | DX | 5568×3712 | (16,8) | 5568×3712 | (0,0) | 5568×3712 |
| 2 | 16:9 | 5568×3128 | (16,300) | 5568×3128 | (0,0…4) | 5568×312x |
| 4 | 1:1 | 5568×3712 | (16,8) | 3712×3712 | **(928,0)** | 3712×3712 |

- 以上为 stock 实测值（`build/research/aspect-trials/native-verification.json` 的 `baseline_cases`，L 尺寸）。配置结构由 `0x1014237A(ctx, info, branch)` 生成，字段：`+0x22` parent wh、`+0x26` origin xy、`+0x38` trim xy、`+0x3C` trimmed wh、`+0x56` output wh；满足 `trimmed = parent - 2*trim`。
- 相关函数：`0x10142BEA` 输出缓冲区、`0x101D5B36` descriptor、`0x1020BFC8` internal selector；测量时在 `info+0x208` 写 area 字节、`info+0x229` 写 size 字节（`tools/verify_aspect_trials.py` 的 `measure()` 是现成模板）。
- `0x1014237A` 读取的按 area 划分的表地址（来自 `config_reads`，同一 PC 不同 area 读不同表）：
  - trim 表：area0 `0x11571E18`，area2 `0x11571E30`，area4 `0x11571E48`（各 2 个 u16：x, y；步距 0x18）
  - internal/region 对：area0 `0x115A4DA8`，area2 `0x115A4DB4`，area4 `0x115A4DD8`
  - 共享 source-crop 宽 `0x115A4E20`（=5568），各 area 高度：area0 `0x115A4E22`（3712）、area2 `0x115A4E3A`（3128）、area4 `0x115A4E82`（3712）
  - edge/offset：area0 `0x115A4E98`=16 / `0x115A4D4E`=0 / `0x115A4E9A`=8；area2 `0x115A4EB0`=16 / `0x115A4D56`=292 / `0x115A4EB2`=8；area4 `0x115A4EF8`=16 / `0x115A4D5E`=0 / `0x115A4EFA`=8
  - 输出尺寸表：area2 已确认 `0x115A4DB4 + i*4` 为 (w,h) 对（ASPL 几何研究脚本即按此偏移写入）；**area0 的 M/S 输出地址尚未逐项确认**（L 对 `0x115A4DA8/AA` 已由读取点 0x10142972/0x101429C6 证实）；上手第一步应先 dump area0 size 1/2 的 `config_reads`。
- 尺寸三元组表 `0x10750908`（9 组 w,h,index：1:1 L/M/S、16:9 L/M/S、DX L/M/S）；字面量 `0x10750908` 位于 `0x105971B4`（包含它的函数约 `0x10597120`）。`docs/aspect-profiles-research.md` 明确警告：字面量引用不能当"三元组已被直接消费"的证据；**这张表的真实消费者从未被追踪**——这是 B 路线逆向的核心入口。
- 属性关系：`0x501` = 用户图像区域；`0x189e` = 派生拍摄区域。QEMU 探针实测（`build/research/aspect-jpeg-fix/property-probe/session.json` 的 `native_area_probe`）：直接写 `0x501`（0/2/4）时 `0x189e` **不跟随**；直接写 `0x189e=2` 时 `0x501` 也不变。原厂菜单切换画幅的完整路径（可能同时写两者并通知管线）尚未定位，扩展目前只写 `0x501`。

### 3.2 扩展侧现有实现（v8）

- `src/z30_extensions/features/aspect_ui.py`
  - `PAGE_INDEX = 961`（aspect_ui.py:9），页面挂在"照片画幅"父菜单；`aspect_sync` 在初始化恢复/打开子菜单时把用户区域写成 2（16:9）。
  - 菜单 7 项：`OFF (stock)` / 4:3 / 5:4 / 2:1 / 2.35:1 / 2.39:1 / 65:24 XPAN；当前项用 `*` 标记；配置持久化到 `A:\WL-ASPECT.CFG`（16 字节，magic `WLA2`，含 magic/启用位/选择位/校验）。
  - 运行时用的是**只读影子表**：`SPANS = ((0x11571E18, 60), (0x115A4D4C, 0x1E0))`，按选中档位把读该区间的表值映射到对应候选值；不写 ROM。9 个 selector 调用点：`0x1020C02E` 小预览、`0x1020C038` 大预览、`0x1020BFDA` output、`0x1020BFC8` internal、`0x1020C05C` reduced、`0x1020C072` offset、`0x1020C07C` crop、`0x1020C0D2` edges、`0x1020C128` desc edges；另有预览直写 hook、参考框 hook `0x10454D46`（替换原厂网格绘制，需开启网格）。
  - 关掉（OFF）时恢复进入前用户区域（DX/16:9/1:1），不改变原厂菜单显示。
- `src/z30_extensions/features/aspect_geometry.py`：research 专用，用 **area 2 车架**生成 6 个独立 trial；关键逻辑 `narrow = p*2 < q*3`（4:3、5:4 为 narrow）；编辑点：`0x115A4D54` internal offset、`0x115A4DB4+i*4` 输出尺寸、`0x115A4D78/0x115A4DF0` internal 尺寸、`0x115A4E38+…` crop variants、`0x115A4EB0+…` crop edges、`0x11571E30+i*4` 横向 trim（行 76–95）。状态字段 `status='emulator_only_not_camera_ready'`。
- 验证工具链（都只证明模拟器/原厂函数，不证明实机管线）：
  - `tools/verify_aspect_trials.py`（63 项几何 + 6 比例 trial，`--deps build/verify-deps`）
  - `tools/verify_aspect_profiles.py`（菜单/持久化/area，伪 Fixture）
  - `tools/verify_aspect_menu_qemu.py`（QEMU 原厂菜单生命周期、原厂 area 设置 API）
  - `tools/verify_aspect_grid_device.py`、`tools/verify_aspect_preview_producers.py`、`tools/verify_aspect_grid_device.py`
  - 诊断：`tools/build_aspect_diagnostic.py`、`tools/read_aspect_trace.py`（trace 文件 `A:\WL-ASPECT.DBG`，格式 WAD1；v7 把 config 快照钉在 slots 0–6，v8 常规版不含 trace）
  - 注意：`docs/aspect-profiles.md:86` 等处的 `--deps ../tools/vendor` 在本仓库不存在；本仓库的依赖在 `build/verify-deps`（Unicorn 2.1.4 / Capstone 5.0.6）。系统 Python 没装这两个包，必须把目录加入 `sys.path` 或走 `--deps`。

## 4. 已否定 / 不要重复的路径

1. **v5 前级源窗口重写**（把 parent 改成 324/480 等窄宽）→ 实机无效，v8 已撤回（aspect-profiles.md:102、110）。
2. **v8 的 area sync + 只读影子表**不足以产生横向裁切（本次实机结论）。
3. **"复用其它比例的实现"**不成立：其它 4 个比例只改高度（宽度始终 5568），管线里的"垂直裁切"一直有效；它们从不需要横向裁切。
4. **在 area 2 分支里改 config trim/output** 已验证实机不生效（挤压）。
5. 直接裸写 `0x501` 不等于原厂菜单切换画幅（`0x189e` 不跟随；实机内部窗口可能仍是别的状态）。这可能是"有时正常/有时不正常"的线索，但尚未证实。
6. 不要把"模拟器里 config 函数算对了"当作实机可用的证据——这是 v8 犯的错误，也是本文档最重要的教训。

## 5. 下一步（用户已选 B：全分辨率 4952×3712 / 4640×3712）

### 5.1 快速判定实验（优先级最高，1–2 次刷机）

目标：判定"配置函数算出的横向裁切字段"在 **DX 原生窗口**下是否能被实机管线消费。

- 构建 stock C1.20 + 仅表补丁的完整固件（不是 extension 预设；不动菜单）：
  - 4:3 L：area0 parent (5568,3712) 不变，trim `0x11571E18 = (308,0)`，输出尺寸 `4952×3712`（area0 的 L 输出对在 `0x115A4DA8/AA` 已证实；M/S 地址先 dump area0 size1/2 `config_reads` 再改，优先只测 L）。
  - 5:4 L：同法 trim `(464,0)`，输出 `4640×3712`（5568−2×464=4640，精确 5:4）。
  - 判据推导：这两组数在模拟器里必须让 `0x1014237A` 输出 `trimmed = parent − 2*trim` 且 output 与 target 一致（可用 `measure()` 逐步断言），但**模拟器通过不等于实机有效**。
- 刷机后操作：原厂菜单 → 图像区域设为 **DX**；拍 L 各一张。判定标准：
  - 照片分辨率为 4952×3712 / 4640×3712，且画面是**两侧被裁掉**（构图变窄）→ 横向裁切可被驱动，B 路线可行，进入 v9 集成。
  - 仍然是整幅横向压缩（4:3 图像里东西被压扁）→ 配置字段不是真实裁切驱动，进入 5.2。
- 工程提示：research EG (`research.eg.bin`) 是裸 EG 模块，**不能直接用于机身更新**（docs/aspect-profiles-research.md 明确写不要安装）。要产出与 `output/full-*/EG209020.BIN` 相同的可刷固件，需参考 `src/z30_extensions/assembly.py` / `build.py` 的封装路径，或复制现有 `Plan.finish()` → 容器封装的步骤；确保只做 in-place 表编辑、EG CRC 残差为零、文件长度/容器校验不变。

### 5.2 逆向（若 5.1 失败，B 路线的关键路径）

1. **定位 `0x10750908` 的真实消费者**：扫描 EG 中指向该地址（及 `0x105971B4` 字面量）的引用；EG 装载基址 `BASE=0x10000000`，VA = EG 内偏移 + BASE；用 Capstone Thumb-2 反汇编（`build/verify-deps` 提供），找出读取三元组的函数、调用者，以及它是否用于传感器/ISP 读出窗口或 JPEG 编码参数。
2. **追 `0x1014237A` 的调用者与 config 结构消费者**：确认 parent/origin/trim/trimmed/output 各字段分别进入哪条 capture/JPEG 路径；找出"读窗/裁切矩形"真正的设置点（对比 DX / 16:9 / 1:1 三种原生窗口的选择逻辑）。
3. **找原厂画幅菜单 action**：原厂切换画幅时如何同时设置 `0x501`、`0x189e` 并通知管线；把扩展的裸写改成调用原厂 setter，可能顺带解决"有时正常/有时不正常"。
4. **判定可行性**：如果管线能对任意原生窗口做列裁切 → 按原生窗口方式实现 4952/4640 全分辨率（最好接进 v9 菜单/预览/参考框/持久化）。如果架构上只能在原生窗口内裁切、无法自定义列窗口 → 全分辨率不可行，回退 A 方案（见 5.4）并向用户解释限制。
5. 如果 5.1 失败，优先使用诊断 trace 取证：`aspect_trace.py` 可记录 config 输出（WAD1 格式、64 条 ×160B），`build_aspect_diagnostic.py` 产带诊断固件，`read_aspect_trace.py` 读 `A:\WL-ASPECT.DBG`。v3/v4 的预览修复经验（预览尺寸表 `0x115A4CFC/0x115A4D10/0x115A4D24/0x1157223C`、固定 160×120 缩略图）可参考，但预览正确不等于主图正确。

### 5.3 文档修正（与代码工作同步做）

把"实机已确认正常/v8 解决"改为"4:3、5:4 实机仍失真（2026-10-17 用户反馈，两侧裁切未生效）"，并保留"其余 4 个比例正常"的既有结论。涉及：`docs/aspect-profiles.md`（3 行、94–120 行）、`docs/verification.md`（3 行）、`docs/aspect-diagnostic.md` 若引用 v8 结论也一并核对。

### 5.4 备选 A 方案（降分辨率兜底，用户当前不选）

1:1（area 4）原生窗口已横向裁切到 3712，只需把该分支输出高度改小做垂直裁切（垂直裁切已被 2:1 等实机验证有效）：
- 4:3 L = 3712×2784（3712×3/4 精确）；5:4 L = 3712×2968（用 `aspect_geometry.nearest8` 量化）；M/S 按 stock 1:1 尺寸等比。
- 代价：横向分辨率上限 3712（L 约 1030 万像素），无法回到 4952/4640。
- 需要先 dump area4 size0/1/2 的 `config_reads` 定位 area4 输出尺寸表，并验证 1:1 分支的 parent/trim 语义（stock area4：parent 5568×3712、trim (928,0)、trimmed 3712×3712）。

## 6. 环境与常用命令

```powershell
# 单元测试（59 项，不需要原厂固件）
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m unittest discover -s tests -v

# 构建
python patch.py aspect-profiles --source Z_30_0120.bin --output build/research/aspect-menu/local-trial.eg.bin
python patch.py full --source Z_30_0120.bin --output build/research/aspect-menu/local-combined.eg.bin

# 几何验证（需要 build/verify-deps 里的 Unicorn/Capstone；系统 Python 默认没有）
python tools/verify_aspect_trials.py --deps build/verify-deps
python tools/verify_aspect_profiles.py --deps build/verify-deps --source Z_30_0120.bin \
  --image build/research/aspect-menu/aspect-menu.eg.bin \
  --report build/research/aspect-menu/aspect-menu.json \
  --output build/research/aspect-menu/native-verification.json
```

- QEMU 会话需要独立的 `z30-qemu` 研究工程（父目录 `<local-private-path>`，本机已有），工具见 `tools/verify_*_qemu.py`。
- `build/`、`output/` 均被 gitignore；`output/full-*/EG209020.BIN` 是机身更新固件的产物格式。
- `tools/native_runtime.py` 提供 Unicorn fixture（`Context`、`RAM=0x50000000`、`call(address,args)`、`STOP=0x600FF000`），做原厂函数级实验时优先复用。

## 7. 文件与环境索引

- 功能源码：`src/z30_extensions/features/aspect_ui.py`（菜单/运行时/持久化）、`aspect_geometry.py`（research trial 生成）、`aspect_trace.py`（诊断）、`src/z30_extensions/build.py`、`cli.py`、`plan.py`、`container.py`、`assembly.py`、`platform/z30_120.py`
- 文档：`docs/aspect-profiles.md`（主文档，含错误验收结论）、`docs/aspect-profiles-research.md`（独立几何研究，63 例）、`docs/aspect-diagnostic.md`（v6/v7 trace 历史）、`docs/aspect-seven-samples.md`（v4 样片分析）、`docs/aspect-playback-investigation.md`（预览/主图修复历史）、`docs/xpan-feasibility.md`（尺寸表与探针）、`docs/aspect-mask-feasibility.md`（黑色遮幅不可行的结论）
- 证据/产物：`build/research/aspect-trials/native-verification.json`（stock 分 area 配置值）、`build/research/aspect-area-sync-v8/`（v8 回归报告）、`build/research/aspect-jpeg-fix/property-probe/session.json`（`0x501`/`0x189e` 探针）、`build/research/aspect-menu/`、`build/research/aspect-diagnostic-v6|v7/`、`output/full-aspect-profiles-area-sync-v8/EG209020.BIN`
- 本仓库当前**未跟踪**且与本任务无关的文件（不要混入 aspect 相关提交）：`docs/precapture-feasibility.md`、`docs/wireless-*.md`、`tests/test_camera_wireless_*.py`、`tools/*wireless*`、`tools/inspect_precapture.py` 等。

## 8. 未决问题（接手时优先向用户确认）

1. v8 实机测试时，机身/菜单实际处于哪种图像区域状态？用户说"几种情况都试了都不行"，需要具体组合与照片分辨率。
2. 宽比例（2:1、2.35 等）是"真裁切"还是"缩放"？外观正常不等于逐像素裁切，建议用样片核对素材内容边界（垂直方向）。
3. 原厂菜单切换画幅的完整调用路径（是否写 `0x189e`、是否通知管线）未知——这是 5.2 的关键。
4. 用户已选 B（全分辨率），但若 5.2 证明硬件/管线无法自定义列窗口，是否接受 A 方案（3712 宽）需要用户拍板。
5. `0x10750908` 尺寸三元组表、config 结构的真实消费者均未定位，是本任务最大的未知。
