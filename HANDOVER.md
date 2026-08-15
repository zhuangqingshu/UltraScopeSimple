# 交接文档

面向接手本项目的人。`README.md` 讲怎么用，`CLAUDE.md` / `AGENTS.md` 讲代码约定，
`docs/ARCHITECTURE.md` 讲分层为什么这么切，`ROADMAP.md` 讲要做什么。
本文讲**为什么是现在这样**，以及有哪些坑和未验证项。

---

## 1. 项目背景

原始需求是从 Rigol DS1102E 导出波形数据。官方路线是 UltraScope，但在 Windows 11 上打开即报
`error code 0xBFFF0015`。该错误码是 VISA 的 `VI_ERROR_TMO`（超时）——USB 枚举成功、SCPI 层无响应。

排查结论：**示波器和 VISA 层本身是好的**。在 UltraSigma 的 SCPI Panel 中发 `*IDN?` 可正常返回：

```
Rigol Technologies,DS1102E,DS1ET173712783,00.04.02.01.00
```

固件 `00.04.02.01.00` 不算老，排除固件问题。UltraScope 侧的已知规避办法是从 UltraSigma 里
右键资源 → 选 `Ultra Scope` 启动（而非从开始菜单单独启动），并以管理员权限运行。

既然 SCPI 已通，就直接绕开 UltraScope 自行实现，于是有了本工具集。

## 2. 跑起来

```bash
pip install -e ".[gui,test]"
```

```bash
ultrascope-gui                   # GUI
ultrascope-dump --help           # CLI
pytest                           # 离线测试，不接硬件
```

入口脚本若不在 PATH（Windows 上常见），用 `python -m ultrascope.gui` /
`python -m ultrascope.cli` 代替。

**必须先装官方 UltraSigma**。不是为了用它，而是它装的 Rigol USBTMC 驱动和 VISA 运行库是
pyvisa 的后端来源；没有它 `pyvisa.ResourceManager().list_resources()` 找不到设备。
安装包在 `官方软件/`（未随仓库分发，见 `.gitignore`）。

开发机环境：Windows 11、Python 3.14。代码本身没用 3.14 特性，3.8+ 应该都能跑。

## 3. 必须知道的硬件/协议知识

这些是踩过的坑，不了解会写出看似正确但跑不通的代码。全部已在真机上验证。

**DS1000E 用的是 Rigol 旧版 SCPI 方言，没有 `:WAV:PREamble`。** 新款 Rigol（DS1000Z 等）
的示例代码在这台机器上不能用。码值到电压的换算是这一代固定的公式：

```
volts = (255 - raw_byte - 130 - offset/scale*25) / 25 * scale
```

即码值反相、以 130 为中心、每 25 码一个垂直分度。展开后等价于
`显示电压 = 原始电压 − 通道 offset`，这个关系在实现拖动平移时会用到。
这些常量现在集中在 `profile.py` 的 `DeviceProfile` 里，**不要再散回换算代码中**。

**时间轴靠计算而非读取。** 屏幕固定 12 个水平分度，以时间偏移为中心。
实测 `(t[-1]-t[0])/timebase` 精确等于 12.0。

### 静默失败的设置（写进去没反应也不报错，极难排查）

这是本机最大的一类坑。示波器对不合法的参数不报错，只是什么也不做。

- **数值参数必须写成普通小数，指数记法会被忽略。** `:TIM:SCAL 5e-5` 在链路层被正常接受，
  示波器却不动；同一个值写成 `0.00005` 就生效。Python 对 1e-5 以下的浮点默认渲染成指数
  形式，而时基档位表里 **2 ns/div 到 50 µs/div 共 14 档正落在该区间**——这 14 档曾长期
  设不进去且无提示。现在所有浮点写入都经 `units.scpi_number()` 强制转普通小数。
  **新增任何写入浮点的 SCPI 命令时，务必走 `scpi_number()`。**
- 触发电平超出 ±6 格 → 被忽略。`Scope.clamp_trigger_level()` 夹住，GUI 侧另有提示
- `:TRIG:HOLD` 超出 500 ns – 1.5 s → 被忽略。`set_holdoff()` 主动抛错
- 平均次数超出 2–256 → `set_acquire()` 主动抛错
- 探头衰减比改变会连带重算 V/div 和 offset，所以**写入顺序必须是 probe → coupling →
  scale → offset**。`Scope.restore()` 和 GUI 的 `_apply_channel()` 都遵守这个顺序，
  测试 `test_restore_writes_probe_before_scale_and_offset` 盯着它

> **排查这类问题的方法**：要验证一个设置是否真的生效，**必须先把参数停到另一个值上再写目标
> 值**。直接写入然后读回相同的值毫无意义——它可能本来就是那个值。这个陷阱在排查上面第一条时
> 真实误导过一次，一度得出错误结论。

**测量查询无效时返回 >1e37 的哨兵值**（实测返回 `99e36`），`Scope.measure()` 统一转成 `None`。
STOP 状态下测量普遍无效，属正常。

**远程模式会锁死前面板按键**，`Scope.close()` 发 `:KEY:FORC` 解锁。任何异常退出路径都要保证
这条命令发出去，否则用户得手动重启示波器。

**深存储（RAW）目前不可信**，见第 6 节。

## 4. 代码结构

单向分层，上层可依赖下层，反之绝不允许。详见 `docs/ARCHITECTURE.md`。

```
cli.py / gui/  →  scope.py  →  waveform.py + profile.py  →  transport.py
```

```
src/ultrascope/
  transport.py   唯一 import pyvisa 的模块。Transport 协议 + PyVisaTransport + FakeTransport
  profile.py     DeviceProfile：码值换算、分格数、量程/探头档位、各项范围上下限
  waveform.py    纯函数 parse_block / decode / time_axis + Waveform 值对象
  scope.py       Scope —— SCPI 门面；ScopeSettings —— 状态快照与配置文件格式
  export.py      CSV / PNG 落盘
  setup_file.py  配置文件读写
  units.py       eng() 显示格式化、scpi_number() 下发格式化
  cli.py         argparse 命令行
  gui/           worker / state / panels / plot / app
```

**`transport.py` 是唯一碰 pyvisa 的地方**，这条约束是整套测试能脱离硬件跑起来的前提：
`FakeTransport` 回放预置的 SCPI 应答和构造好的 488.2 数据块，于是码值解码、触发子系统路由、
"未传参即不下发"契约、配置文件往返都能离线验证。

**`profile.py` 收编所有机型相关的硬件常量**。将来支持带 `:WAV:PRE?` 的新机型，应该是加一份
profile，而不是去改 `waveform.decode()`。

`ScopeSettings` 一物两用：既是 GUI 面板的镜像来源，`to_dict()` / `from_dict()` 又是配置
文件的 JSON 格式。**那些键名是已发布的格式**，改名会让用户存过的配置读不回来，
`test_setup_file_keys_stay_stable` 盯着它。

**`Scope` 不是线程安全的。**

### GUI 的两条硬约束

**（一）所有仪器 I/O 都在唯一的 worker 线程上排队执行，Tk 主线程只碰控件。**

USBTMC 不能被两处同时使用，而实时刷新和用户点按钮天然会撞车。`Worker` 持有 `Scope` 对象和
任务队列，UI 通过 `App._do(func, tag)` 提交，结果按 tag 路由回 `App._on_<tag>`。
`_do()` 会在执行命令前暂停实时刷新、执行完再恢复。

连接也由 `Worker.connect()` 在工作线程内完成，**UI 侧不持有 `Scope` 引用**。

**新增功能时不要图省事直接在 Tk 回调里调 `Scope`**，会随机卡死或返回错乱数据。

**（二）鼠标拖动绝不能每个 motion 事件发一次 SCPI。**

触发电平拖拽和拖动平移都是：按下时记录起点 → 拖动过程中**只改本地状态和 matplotlib 视图** →
松开鼠标才下发一次命令。实时刷新本身已经把链路占得差不多，再叠几十条写入会直接卡死界面。

手势逻辑在 `gui/plot.py`，但**下发仍由 `app.py` 经 worker 完成**——`PlotCanvas` 通过
`on_level_commit` / `on_pan_commit` 两个钩子把结果交回去，自己不认识 `Scope`。

配套的一点：拖动进行中 `PlotCanvas.show()` 跳过坐标轴范围的重设（判断 `self.pan is not None`），
否则实时刷新每帧把视图拉回去，跟拖动预览互相打架。

`plot.py` 里还镜像了一份 `volt_scales` / `volt_offsets` / `time_offset`，供夹紧和滚轮步进
使用，避免手势进行中去查仪器。`app.py` 在 `_on_settings` 和 `_apply_channel` 里负责同步它们。

## 5. 进度

`ROADMAP.md` 第一阶段五项已完成。第二到五阶段未开始。

第二阶段（各触发类型的专属参数）是**当前最大的功能缺口**：切到 EDGE 以外的模式只能设
source/slope/level，PULSE 的脉宽条件、SLOPE 的时间条件等无处可设，等于那些模式不可用。
调数字电路的话 PULSE 优先级最高。

## 6. 验证状态

离线测试 113 项（`pytest`），覆盖码值解码、时间轴、SCPI 数值格式化、触发子系统路由、
各项范围校验、"未传参即不下发"契约、配置文件往返、CLI 参数解析。**这些不需要示波器**。

以下在真机（DS1102E，固件 00.04.02.01.00）上验证通过：

| 项目 | 结果 |
|------|------|
| **电压换算准确性** | 仪器 `:MEAS:VMIN?` 报 −120 mV，`decode()` 独立算得 −0.120 V，逐位吻合 |
| **时间轴** | `(t[-1]-t[0])/timebase` 精确等于 12.0 |
| **配置保存/加载往返** | 存档 → 改乱时基/探头/量程/电平 → 加载还原，13 个字段逐项精确复原 |
| **探头衰减比写入顺序** | probe 1X→10X 与 scale 0.5→1.0 同时正确，证明 probe 确实先写 |
| 时基 32 档 | 全部生效（修复指数记法问题后；修复前有 14 档静默失效） |
| 释抑 6 个取值 + 范围校验 | 全部生效 |
| 触发电平 ±6 格夹紧、50% 无信号报错 | 正确 |
| 采集/CSV/PNG 导出、测量哨兵值、`:KEY:FORC` | 正确 |

**尚未验证**，接手后应优先补：

1. **拖动平移的方向** —— 波形应当跟随光标移动。若表现为"越拖越远"，说明
   `PlotCanvas._pan_finish()` 里 offset 的符号反了。**这项只做过模拟事件的冒烟测试
   （数值方向正确），没有真人在真机上拖过。**
2. GUI 其余交互：连接/断开、live 刷新、Run/Stop/Auto/Single/Force、触发电平拖拽与滚轮
   微调、Save/Load setup、关窗是否交还面板
3. CLI 的 `--single`（等待触发）与 `--acquire average`

**已验证为损坏**：深存储 1M 点读取。见下。

### 深存储的真实情况

原以为"1M 点读取慢、需要 120 s 超时"，实测并非如此：

- RAW 模式下，**无论存储深度是 LONG 还是 NORMAL，块头一律声明 `#8 01048576`（1 M 字节）**
- 实际只发出约 12 K 字节就断流（0.5 s），剩下的永远不来
- 那 120 s 不是在传数据，是在等永远不会到达的字节

因此**加长超时或分块读取都不会奏效**，`ROADMAP.md` 第四阶段"深存储分段读取"那条的前提
（"一次性阻塞几十秒"）是错的，需要重新调研（查 `docs/DS1000D_E_Manual_EN.pdf` 是否有分段
读取指令）。

更要紧的是：`waveform.parse_block()` 按块头声明的长度切片，而 Python 切片会静默截断，
**于是每一次 RAW 读取都是残缺数据，却表现得像一次成功的短采集**。这条待修（`ROADMAP.md`
「待修缺陷」第 1 条）。在修好之前，深存储的结果不可信，请以 `--mode normal` 的 600 点为准。

### 可调的经验值

`gui/app.py` 的 `REFRESH_MS`（UI 刷新周期）、`gui/worker.py` 的 `MEAS_EVERY`（每 N 帧刷一次
测量值）、`gui/plot.py` 的 `GRAB_TOLERANCE`（电平线抓取容差 3%）与 `SCROLL_STEP_DIV`
（滚轮 1/5 格步进）。这些都是拍脑袋定的，按实际手感调。

## 7. 排障速查

| 现象 | 原因与处理 |
|------|-----------|
| `0xBFFF0015` / VISA 超时 | 先查示波器 `Utility → I/O 设置 → USB 设备` 是否为「计算机」而非 PictBridge；再查是否有别的程序占着资源 |
| 报找不到设备 | UltraSigma 没装，或驱动被 NI-VISA / Keysight IO Suite 抢走 |
| 连不上但 UltraSigma 能连 | **UltraSigma 的 SCPI Panel 没关**，USBTMC 资源被占用 |
| 波形不刷新、Status 一直 `WAIT` | 触发电平设在信号范围之外，没触发上。点 50% 按钮 |
| `:RUN` 之后状态仍是 `STOP` | 扫描方式是 SINGLE，无触发事件就不重新采集。改成 AUTO |
| 设了某个数值参数没反应 | 多半是指数记法被静默忽略，检查是否漏走 `scpi_number()`；触发电平还可能是超出 ±6 格 |
| 电压读数整体差 10 倍 | 探头衰减比设错 |
| 前面板按键失灵 | 程序异常退出没发出 `:KEY:FORC`，重启示波器 |
| 深存储读取超时 / 点数远少于预期 | 已知缺陷，非配置问题。见第 6 节 |
| `import ultrascope` 找不到 | 没 `pip install -e .`；装过之后在任何目录都能用 |

## 8. 存疑项

**屏幕截图功能。** `官方软件/` 里带了 `Ultra Sigma patch file for Screenshot`，说明 UltraSigma
原生截图有问题。DS1102E 是否支持可用的 `:DISP:DATA?` 未经验证。实际意义有限——已能拿到原始数据
自行绘图，分辨率远高于示波器 320×234 的屏幕。
