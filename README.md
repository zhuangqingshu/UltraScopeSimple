# UltraScopeSimple

为 Rigol DS1102E / DS1000D-E 系列示波器编写的 Python 采集与绘图工具集，作为 UltraScope 的轻量替代。提供命令行导出与实时 GUI 显示两个入口，共用同一套仪器通信层。

基于官方软件 **UltraSigma** 开发：本工具依赖 UltraSigma 安装的 USB 驱动程序（Rigol USBTMC）使示波器可被电脑识别为 VISA 仪器，开发过程中也参考了 UltraSigma 的 SCPI 命令交互方式。若示波器无法被识别，请先安装 UltraSigma（见 `官方软件/` 目录，未随仓库分发）。

## 安装

需要 Python 3.8+，以及一个 VISA 后端（NI-VISA 或 pyvisa-py）。

```bash
pip install -e ".[gui]"
```

只用命令行导出 CSV 的话可以省掉绘图依赖：`pip install -e .`（`--plot` 与 GUI 需要 `[gui]` 提供的 matplotlib）。

## 项目结构

```
src/ultrascope/
  transport.py   VISA 会话（唯一直接依赖 pyvisa 的模块）
  profile.py     机型参数：码值换算、分格数、量程档位、各触发模式的参数规格
  waveform.py    488.2 块解析、码值 → 电压、Waveform 值对象
  scope.py       Scope —— SCPI 指令门面
  export.py      CSV / PNG 落盘
  setup_file.py  配置文件读写
  analysis.py    本地分析：光标读数、插值取样、FFT 频谱、参数测量
  units.py       eng() 显示格式化、scpi_number() 下发格式化
  cli.py         命令行工具
  gui/           Tkinter 界面（worker / state / panels / plot / app）
tests/           不接硬件的单元测试
docs/            架构文档与官方 User's Guide
```

设计说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，
接手须知与验证状态见 [HANDOVER.md](HANDOVER.md)，功能规划见 [ROADMAP.md](ROADMAP.md)。

## GUI 使用

```bash
ultrascope-gui
```

- **Connection**：刷新并选择 VISA 资源后 Connect（若无法识别，先用官方 UltraSigma 确认示波器可被电脑看到）
- **Acquisition**：Run / Stop / Auto / Single / Force，Live 实时刷新，平均与深存储模式
- **Channel 1/2**：显示开关、Active 单选、探头衰减比、V/div、耦合、垂直位移
- **Horizontal / Trigger**：时基与水平位移；触发模式、源、斜率、扫描、耦合、电平、释抑。
  PULSE / SLOPE 模式下多出 Condition（六种条件）与 Width/Time 两栏，切回其他模式自动隐藏
- **波形图交互**：拖动红色虚线调触发电平（双击定位，滚轮按 1/5 分度微调）；
  在图上拖动可平移时基与垂直位移（作用于 Active 选中的通道）
- **Cursors**：Off / Time / Voltage 三档。图上拖动绿色虚线，读出两根光标位置与
  ΔT（附 1/ΔT，直接读频率）或 ΔV。**纯本地计算，不走 SCPI，断开连接也能测已有波形**
- **Spectrum (FFT)**：勾选后波形区切换为频谱，可选窗函数（矩形/汉宁/海明/布莱克曼）与通道，
  纵轴 dBV，读数给出峰值频率与幅度、频率分辨率、等效采样率。同样是本地计算，断开也能用
- **Measurements**：读数来源在「From scope」（仪器自动测量，更准，需连接）与「Local」
  （本地按采样点算 15 项：电平、上升/下降时间、周期/频率、脉宽、占空比、过冲）之间切换
- **Setup**：保存/加载完整配置（JSON），与 CLI 的 `--save-setup` / `--load-setup` 通用
- **Export**：保存 CSV / PNG，Deep memory capture 读取深存储（见下方「已知限制」）

所有仪器 I/O 由单一工作线程串行执行，Tk 主线程只操作控件。

## 命令行示例

```bash
# 导出当前屏幕显示的 600 点数据
ultrascope-dump

# 1.5 V 下降沿单次触发，等待触发后导出
ultrascope-dump --single --trigger-level 1.5 --trigger-slope neg

# CH1 单次深存储采集（1M 点）
ultrascope-dump --single --mode raw --memdepth long --channels 1

# 16 次平均采集，同时输出 CSV 与 PNG
ultrascope-dump --acquire average --average 16 --plot

# 打印各通道 Vpp/Vrms/频率等测量值
ultrascope-dump --measure

# 触发在宽度小于 100 ns 的正脉冲上（查丢失脉冲/异常窄脉冲）
ultrascope-dump --trigger-mode pulse --trigger-condition "+Width <" --trigger-width 100e-9

# 保存当前配置，之后随时还原
ultrascope-dump --save-setup bench.json
ultrascope-dump --load-setup bench.json
```

未安装到 PATH 时可以用 `python -m ultrascope.cli` 与 `python -m ultrascope.gui` 代替。

### 常用选项

| 选项 | 说明 |
|------|------|
| `--resource` | VISA 资源字符串，缺省自动探测 |
| `--channels` | 通道列表，如 `1` 或 `1,2` |
| `--mode normal/raw` | normal=600 点屏幕数据；raw=深存储（需先 STOP） |
| `--out` | CSV 输出路径 |
| `--plot` | 同时输出 PNG |
| `--measure` | 打印 Vpp/Vmax/Vmin/Vavg/Vrms/频率/周期 |
| `--single` | 单次触发并等待（`--trigger-timeout` 可设超时） |
| `--timebase` | 设置 s/div |
| `--probe` | 探头衰减比，1/5/10/50/100/500/1000 |
| `--offset` | 垂直位移（V），作用于 `--channels` |
| `--position` | 水平位移（s） |
| `--trigger-condition` | 脉宽/斜率条件，如 `"+Width <"`（仅 PULSE/SLOPE 模式） |
| `--trigger-width` | 脉宽或斜率时间，20 ns–10 s（仅 PULSE/SLOPE 模式） |
| `--save-setup` | 把当前完整状态写成 JSON 后退出 |
| `--load-setup` | 先套用 JSON 配置，再应用其他选项 |

**未传的参数一律不下发**，示波器保持面板上的现状——可以安全地在手工调好的设置上直接跑。

## 作为库使用

```python
from ultrascope import Scope, save_csv

with Scope.connect() as scope:
    wave = scope.capture(points="normal")
    save_csv("out.csv", wave)
```

## 数据格式

CSV 首行 `Time(s),CH1(V),CH2(V)`，时间轴按屏幕 12 格、以时间偏移为中心构造。波形码值转换针对 DS1000E 旧固件（码值反相、以 130 为中心、每 25 码一格垂直分度）。

## 测试

```bash
pip install -e ".[test]"
pytest
```

测试用 `FakeTransport` 回放预置的 SCPI 应答，覆盖码值解码、时间轴、触发子系统路由、"未传参即不下发"契约与 CLI 参数解析，**不需要接示波器**。仪器层以上（真实 VISA 通信、GUI 交互）仍需接真机手动验证。

## 注意事项

- 深存储（RAW）采集必须处于 STOP 状态
- 程序退出时会发送 `:KEY:FORC` 将面板控制权交还示波器
- 平均次数范围 2–256

## 已知限制

**数值参数一律以普通小数下发。** 示波器会接受 `:TIM:SCAL 5e-5` 这类指数写法然后
静默忽略——设置看起来"没反应"。因此所有浮点写入都经 `units.scpi_number()` 格式化。
修复前，2 ns/div 到 50 µs/div 共 14 个时基档位设不进去且不报错。


**PULSE / SLOPE 触发条件尚未在真机上验证。** 这两个模式的 SCPI 命令拼写来自项目早期
笔记，仓库里的手册是 User's Guide、不含命令参考，无从核对；而本机对拼错的命令是静默
忽略的。功能可用性待验，详见 [ROADMAP.md](ROADMAP.md) 第二阶段。

**FFT 的频率上限受屏幕数据限制。** 仪器不报逐点时标，等效采样率由时间轴反推
（600 点 / 12 格时基）。这是屏幕抽取后的数据，远低于真实采集率——频谱反映的是屏幕上
显示的这条波形，更高频的成分在到达本工具之前就已被仪器混叠。

**深存储采集拿不到完整 1M 点。** 实测（DS1102E 固件 00.04.02.01.00）：RAW 模式下
仪器的数据块头无论存储深度都声明 1 048 576 字节，但实际只发出约 12 K 就断流。
`--mode raw --memdepth long` 会在等待剩余数据时超时；即使读到数据，也是一段被截断的
块。这不是本工具的回归——重构前的代码行为完全相同，只是此前没有人试过 1M 采样。

现在深存储采集会**明确报错**（`truncated block: header declares ... only ... arrived`）
而不是返回残缺数据。请以 `--mode normal` 的 600 点数据为准。
详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 第 9 节缺陷 6、7。
