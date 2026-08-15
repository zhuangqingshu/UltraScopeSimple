# UltraScopeSimple

为 Rigol DS1102E / DS1000D-E 系列示波器编写的 Python 采集与绘图工具集，作为 UltraScope 的轻量替代。三个脚本共享同一通信层，支持命令行导出与实时 GUI 显示。

基于官方软件 **UltraSigma** 开发：本工具依赖 UltraSigma 安装的 USB 驱动程序（Rigol USBTMC）使示波器可被电脑识别为 VISA 仪器，开发过程中也参考了 UltraSigma 的 SCPI 命令交互方式。若示波器无法被识别，请先安装 UltraSigma（见 `官方软件/` 目录，未随仓库分发）。

## 文件说明

| 文件 | 作用 |
|------|------|
| `ds1102e.py` | 共享通信层，封装 DS1000E 旧版 SCPI 指令（波形码值转换、触发/垂直/水平/采集子系统、测量与 CSV 导出） |
| `ds1102e_scope.py` | Tkinter 实时 GUI：波形显示 + 面板控制 + CSV/PNG 导出，支持深存储（RAW）采集 |
| `ds1102e_dump.py` | 命令行采集导出工具，未传参数项保持示波器当前设置不变 |

## 依赖

- Python 3.8+
- `pyvisa`（含后端，如 NI-VISA 或 pyvisa-py）
- `numpy`
- `matplotlib`（GUI 与 `--plot` 需要）

```bash
pip install pyvisa numpy matplotlib
```

## GUI 使用

```bash
python ds1102e_scope.py
```

- **Connection**：刷新并选择 VISA 资源后 Connect（若无法识别，先用官方 UltraSigma 确认示波器可被电脑看到）。连接后会先读回示波器当前设置填入面板，不覆盖已手调的状态
- **Acquisition**：Run / Stop / Auto / Single / Force，Live 实时刷新，采集类型（普通/平均/峰值检测）、平均次数与存储深度
- **Channel 1/2**：显示开关、Active 单选（决定拖动作用于哪个通道）、探头衰减比、V/div、耦合（DC/AC/GND）、垂直偏移
- **Horizontal**：s/div 与水平位置
- **Trigger**：模式、源、斜率、扫描方式、耦合、电平、释抑
- **Export**：保存 CSV / PNG，Deep memory capture 读取完整 1M 点存储
- **Setup**：保存 / 加载完整配置（JSON），与命令行 `--save-setup` / `--load-setup` 格式通用

### 鼠标操作

| 操作 | 效果 |
|------|------|
| 拖动红色虚线 | 调整触发电平（松开鼠标才下发指令） |
| 在图上双击 | 触发电平直接跳到该处 |
| 滚轮 | 触发电平微调，每格 1/5 分度 |
| 在图上拖动 | 平移波形：水平改 `:TIM:OFFS`，垂直改 Active 通道的 `:CHAN<n>:OFFS` |
| 50% 按钮 | 触发电平设为信号 Vmax/Vmin 中点 |

触发电平超出 ±6 格会被自动夹住并提示（示波器对超范围值是静默忽略的，很难排查）。

所有仪器 I/O 由单一工作线程串行执行，Tk 主线程只操作控件。

## 命令行示例

```bash
# 导出当前屏幕显示的 600 点数据
python ds1102e_dump.py

# 1.5 V 下降沿单次触发，等待触发后导出
python ds1102e_dump.py --single --trigger-level 1.5 --trigger-slope neg

# CH1 单次深存储采集（1M 点）
python ds1102e_dump.py --single --mode raw --memdepth long --channels 1

# 16 次平均采集，同时输出 CSV 与 PNG
python ds1102e_dump.py --acquire average --average 16 --plot

# 打印各通道 Vpp/Vrms/频率等测量值
python ds1102e_dump.py --measure

# 保存当前示波器全部设置
python ds1102e_dump.py --save-setup setup.json

# 套用已保存的配置后再采集（命令行显式选项优先级高于配置文件）
python ds1102e_dump.py --load-setup setup.json --plot
```

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
| `--timebase` / `--position` | s/div 与水平位置 |
| `--probe` / `--offset` | 探头衰减比与垂直偏移，作用于 `--channels` 指定的通道 |
| `--trigger-holdoff` | 触发释抑，500 ns – 1.5 s |
| `--save-setup` / `--load-setup` | 读写 JSON 配置，与 GUI 的 Setup 面板通用 |

## 数据格式

CSV 首行 `Time(s),CH1(V),CH2(V)`，时间轴按屏幕 12 格、以时间偏移为中心构造。波形码值转换针对 DS1000E 旧固件（码值反相、以 130 为中心、每 25 码一格垂直分度）。

## 注意事项

- 深存储（RAW）采集必须处于 STOP 状态，且耗时较长（`TIMEOUT_RAW_MS` 为 120 s）
- 程序退出时会发送 `:KEY:FORC` 将面板控制权交还示波器
- 平均次数范围 2–256
