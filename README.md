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
  profile.py     机型参数：码值换算、分格数、量程档位
  waveform.py    488.2 块解析、码值 → 电压、Waveform 值对象
  scope.py       Scope —— SCPI 指令门面
  export.py      CSV / PNG 落盘
  cli.py         命令行工具
  gui/           Tkinter 界面（worker / state / panels / plot / app）
tests/           不接硬件的单元测试
docs/            架构文档与官方 SCPI 手册
```

设计说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## GUI 使用

```bash
ultrascope-gui
```

- **Connection**：刷新并选择 VISA 资源后 Connect（若无法识别，先用官方 UltraSigma 确认示波器可被电脑看到）
- **Acquisition**：Run / Stop / Auto / Single / Force，Live 实时刷新，平均与深存储模式
- **Channel 1/2**：显示开关、V/div、耦合（DC/AC/GND）
- **Horizontal / Trigger**：时基与触发（模式、源、斜率、扫描、耦合、电平）
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

**深存储采集拿不到完整 1M 点。** 实测（DS1102E 固件 00.04.02.01.00）：RAW 模式下
仪器的数据块头无论存储深度都声明 1 048 576 字节，但实际只发出约 12 K 就断流。
`--mode raw --memdepth long` 会在等待剩余数据时超时；即使读到数据，也是一段被截断的
块。这不是本工具的回归——重构前的代码行为完全相同，只是此前没有人试过 1M 采样。

在修复之前，深存储采集的结果**不可信**，请以 `--mode normal` 的 600 点数据为准。
详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 第 9 节缺陷 6、7。
