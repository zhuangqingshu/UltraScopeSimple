# 功能规划

按"是否解决现有痛点"分阶段，不是按功能表罗列。已完成项打勾。

## 待修缺陷（优先于新功能）

真机验证中发现，按建议顺序排列。详见 `docs/ARCHITECTURE.md` 第 9 节。

1. ~~**RAW 读取静默截断数据**~~ — 已修。`parse_block()` 现在比对块头声明长度与实际到达
   字节数，不足即抛 `WaveformError` 并报出两个数字。**副作用是深存储采集现在会明确报错
   而不是返回残缺数据**——这正是目的：调用方此前无从分辨"这就是全部数据"和"数据断了
   一半"。深存储本身仍不可用，见第四阶段。
2. ~~**纵轴范围忽略垂直偏移**~~ — 已修。`PlotCanvas.y_limits()` 改为跟随数据实际上下界，
   波形挪到屏幕任何位置都不会再被实时刷新拉回对称范围。平坦波形回退到 ±1 V 窗口，
   触发电平线在视野外时会把窗口撑开。见 `tests/test_gui_plot.py`。
3. **`Scope.single()` 改了 sweep 不恢复**（待真机验证后再动） — 在 ALTERNATION 之外的模式下会把 sweep 设成
   SINGLE 且不还原，调用后仪器状态与调用前不一致。应记录并恢复，或在文档中明确这是预期行为。
   （**注意**：这是剩下的唯一一条会改变仪器状态的缺陷，其余都只影响显示。）
   动手前需先在真机上回答一个问题：**STOP 状态下改写 sweep 会不会重启采集**——若会，
   恢复 sweep 就会冲掉刚采到的单次数据，那就得换做法。
4. ~~**GUI 单次采集超时硬编码**~~ — 已修。Acquisition 面板新增 Single wait (s) 输入框，
   默认 30 s，填非法值回退默认值。
5. ~~**触发源组合框显示不一致**~~ — 已修。`gui/state.normalise_trigger_source()` 把仪器
   回的 `CH1` 映射成面板选项 `CHAN1`。

## 第一阶段：补齐明显缺失（已完成）

- [x] **探头衰减比** `:CHAN<n>:PROB` — 通道面板 Probe 下拉框，1X–1000X。设错会导致电压读数整体偏差 10 倍，所以放在 V/div 上方常驻可见。写入顺序上必须先于 V/div 和 offset，因为改衰减比会连带重算这两个值
- [x] **垂直位移** `:CHAN<n>:OFFS` — 面板 Offset 输入框，以及在波形图上竖直拖动。作用于 Active 单选钮选中的通道
- [x] **水平位移** `:TIM:OFFS` — 面板 Position 输入框，以及在波形图上水平拖动
- [x] **触发释抑** `:TRIG:HOLD` — 触发面板 Holdoff 输入框，范围 500 ns – 1.5 s，超范围报错而非静默忽略
- [x] **保存/加载配置** — `Scope.snapshot()` / `Scope.restore()` 读写完整状态，GUI 的 Setup 面板与 CLI 的 `--save-setup` / `--load-setup` 共用同一 JSON 格式

实现要点：

- 拖动期间只移动 matplotlib 的视图范围，**松开鼠标才写一次 SCPI**。若在 `motion_notify_event` 里实时下发，每次拖动会产生几十条 USBTMC 写入并卡死链路。触发电平拖拽同理
- 拖动进行中不再重设坐标轴范围，否则实时刷新会与拖动预览互相打架
- 显示电压 = 原始电压 − 通道 offset，所以把波形向上拖 Δ 对应 offset 减少 Δ
- `restore()` 单项失败不中断整体恢复，收集为 warning 列表返回并在 GUI 弹窗提示
- 配置文件记录 `idn`，加载到不同序列号的机器上会先弹窗确认

重构后的落位（原实现在三个平铺脚本里，现已分层）：

- 手势逻辑在 `gui/plot.py`，但下发仍由 `gui/app.py` 经 worker 完成，`PlotCanvas` 不认识 `Scope`
- 配置的读写是 `ScopeSettings.to_dict()` / `from_dict()` + `setup_file.py`，
  JSON 键名是已发布格式，有测试盯着
- 探头档位、释抑上下限、±6 格窗口都收进了 `profile.py`

## 第二阶段：各触发类型的专属参数

- [x] **PULSE** 脉宽条件（正/负 × >、<、= 六种）+ 脉宽 20 ns ~ 10 s
- [x] **SLOPE** 边沿时间条件（同样六种）+ 时间 20 ns ~ 10 s
- [ ] **VIDEO** 制式（NTSC/PAL/SECAM）、行场、极性、行号（NTSC 1~525，PAL/SECAM 1~625）
- [ ] **PATTERN**（DS1000D）两通道电平组合
- [ ] **DURATION**（DS1000D）电平组合 + 持续时间条件

PULSE 与 SLOPE 结构相同（六种条件 + 一个时间参数），故合为一套实现：
`profile.TimedTriggerSpec` 描述某个模式的子树、条件表、时间参数叶子与范围，
`Scope.set_trigger_condition()` 按它组装命令，GUI 的条件控件组随 Mode 切换显示。
再加 VIDEO 等模式时应优先考虑能否同样用一个 spec 描述，而不是在 `scope.py` 里堆分支。

CLI：`--trigger-condition "+Width <" --trigger-width 100e-9`。
GUI：Trigger 面板在 PULSE/SLOPE 下多出 Condition 与 Width/Time 两栏，切回 EDGE 自动隐藏并清空。

> **⚠ 命令拼写未经核实，且尚未接真机验证。**
> 仓库里的 `docs/DS1000D_E_Manual_EN.pdf` 是 User's Guide，只讲前面板操作，全文没有
> 任何 SCPI 命令；`:TRIG:PULS:MODE` / `:TRIG:PULS:WIDT` 等名字来自本项目早期笔记。
> 本机对拼错的命令是**静默忽略**的，因此这批代码有"看着对但什么也不做"的可能。
>
> 为此所有拼写集中在 `profile.py` 的 `DS1000E_PULSE_TRIGGER` / `DS1000E_SLOPE_TRIGGER`
> 两个对象里，其余代码只从中读取；测试断言的是"命令如何由 spec 组装"而非具体拼写。
> **拿到 Rigol 的 *DS1000E/D Series Programming Guide* 后，核对这两处即可，无需改动别处。**
>
> 真机验证要点：接一个脉冲信号源，设好条件与脉宽后确认确实只在满足条件时触发；
> 并按 `HANDOVER.md` 第 3 节的方法（**先把参数停到另一个值再写目标值**）确认写入真的生效。

条件与脉宽已纳入配置文件格式（`trigger.condition` / `trigger.condition_time`），
EDGE 等模式下不写入这两个键，旧配置文件照常加载。

## 第三阶段：分析能力

数据已经在手里，本地用 numpy 算普遍比示波器自身强。

- [x] **光标测量** — 图上可拖的虚线，显示两根光标位置、ΔT/ΔV，时间模式下另给 1/ΔT。
  计算在 `analysis.py`（纯函数，无仪器），交互复用 `gui/plot.py` 的拖拽骨架。
  **不走 SCPI，因此断开连接时照样可用**——`App.ALWAYS_ENABLED` 让光标面板不随连接状态禁用。
  光标抓取优先于触发电平拖拽与平移，且松手不提交任何命令
- **FFT 频谱**（下一个，同样是纯本地计算，无验证债） — numpy 直接算，可选窗函数、dB 纵轴、峰值标注，强于 DS1102E 自带 FFT
- **更多自动测量** — 机器支持 22 种，当前只取了 7 种（`Scope.MEASUREMENTS`）。可补上升/下降时间、正负占空比、脉宽、过冲等
- **参考波形对比** — 存一条波形作基准叠加显示，对比"改动前后的区别"。`Waveform` 值对象已带时基与偏移元数据，适合直接存取
- **余辉显示** — 多次采集半透明叠加，暴露抖动与偶发异常

## 第四阶段：采集与自动化

- **连续记录到文件** — 数据记录仪模式：定时或每次触发存一个 CSV，长时间无人值守监测
- **深存储读取** — ~~改为分块读取 + 进度条~~ **前提已被推翻**：实测仪器只发约 12 K 就断流，
  不是"传得慢"而是"根本不发"，加长超时和分块读取都无效。需要先查手册确认这一代固件到底
  支持何种方式读取完整采集存储，再决定做法。见待修缺陷第 1 条与 `HANDOVER.md` 第 6 节
- **等效采样开关** `:ACQ:MODE RTIM/ETIM` — 重复信号下等效采样率可达 25 GSa/s
- **延迟扫描** `:TIM:MODE DELAYED` — 放大波形局部细节
- **带宽限制 / 反相** `:CHAN<n>:BWL`、`:CHAN<n>:INV` — 两个开关
- **数学运算** `:MATH:OPER` — A+B / A−B / A×B。双通道数据都在本地，用 numpy 算更灵活

## 第五阶段：工程化

- **打包 exe**（PyInstaller）— 免装 Python 即可运行。已有 `pyproject.toml` 与命令入口，起点比之前好
- **本地缩放平移** — matplotlib 工具栏，框选放大查看细节，不改示波器设置
- **快捷键** — 空格 Run/Stop、S 单次、Ctrl+S 存盘
- **暗色主题** — 目前仅绘图区为深色，控制面板仍是系统配色
- **CI** — 仓库已有 113 项离线测试且不需要硬件，接一个 GitHub Actions 跑 pytest 的成本很低

## 存疑项

**屏幕截图**。仓库外的 `官方软件/` 里有 `Ultra Sigma patch file for Screenshot`，说明 UltraSigma
原生截图有问题。DS1102E 是否支持可用的 `:DISP:DATA?` 未经实机验证。
实际意义也有限——已能拿到原始数据自行绘图，分辨率远高于示波器 320×234 的屏幕。
