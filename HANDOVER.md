# 交接文档

面向接手本项目的人。`README.md` 讲怎么用，`AGENTS.md` 讲代码约定，`ROADMAP.md` 讲要做什么，
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
pip install pyvisa numpy matplotlib
```

```bash
python ds1102e_scope.py          # GUI
python ds1102e_dump.py --help    # CLI
```

**必须先装官方 UltraSigma**。不是为了用它，而是它装的 Rigol USBTMC 驱动和 VISA 运行库是
pyvisa 的后端来源；没有它 `pyvisa.ResourceManager().list_resources()` 找不到设备。
安装包在 `官方软件/`（未随仓库分发，见 `.gitignore`）。

开发机环境：Windows 11、Python 3.14。代码本身没用 3.14 特性，3.8+ 应该都能跑。

## 3. 必须知道的硬件/协议知识

这些是踩过的坑，不了解会写出看似正确但跑不通的代码。

**DS1000E 用的是 Rigol 旧版 SCPI 方言，没有 `:WAV:PREamble`。** 新款 Rigol（DS1000Z 等）
的示例代码在这台机器上不能用。码值到电压的换算是这一代固定的公式：

```
volts = (255 - raw_byte - 130 - offset/scale*25) / 25 * scale
```

即码值反相、以 130 为中心、每 25 码一个垂直分度。展开后等价于
`显示电压 = 原始电压 − 通道 offset`，这个关系在实现拖动平移时会用到。

**时间轴靠计算而非读取。** 屏幕固定 12 个水平分度，以时间偏移为中心：
`np.linspace(offset - 6*scale, offset + 6*scale, npts)`。

**深存储（RAW）只能在 STOP 状态读**，1M 点耗时几十秒，超时要放到 120 s（`TIMEOUT_RAW_MS`）。
双通道同时开时每通道只有 512k 点，要读满 1M 必须只开一路且存储深度设为 LONG。

**几个静默失败的设置**，写进去没反应也不报错，极难排查：

- 触发电平超出 ±6 格 → 被忽略。代码里用 `Scope.clamp_trigger_level()` 夹住并提示
- `:TRIG:HOLD` 超出 500 ns – 1.5 s → 被忽略。`set_holdoff()` 会主动抛错
- 探头衰减比改变会连带重算 V/div 和 offset，所以**写入顺序必须是 probe → coupling → scale → offset**

**测量查询无效时返回 >1e37 的哨兵值**（如平坦波形的频率），`Scope.measure()` 统一转成 `None`。

**远程模式会锁死前面板按键**，`Scope.close()` 发 `:KEY:FORC` 解锁。任何异常退出路径都要保证
这条命令发出去，否则用户得手动重启示波器。

## 4. 代码结构

```
ds1102e.py         通信层，Scope 类。另外两个脚本 import 它，所以要在仓库根目录运行
ds1102e_dump.py    argparse CLI
ds1102e_scope.py   Tkinter GUI
```

### 通信层

`Scope` 类封装触发/垂直/水平/采集四个子系统 + 波形读取 + 自动测量。
`snapshot()` / `restore()` 是完整状态的读写，也就是 JSON 配置文件的格式，GUI 的 Setup 面板和
CLI 的 `--save-setup` / `--load-setup` 共用。`restore()` 单项失败不中断，收集成 warning 列表返回。

**`Scope` 不是线程安全的。**

### GUI 的两条硬约束

**（一）所有仪器 I/O 都在唯一的 worker 线程上排队执行，Tk 主线程只碰控件。**

USBTMC 不能被两处同时使用，而实时刷新和用户点按钮天然会撞车。`Worker` 类持有 `Scope` 对象和一个
任务队列，UI 通过 `App._do(func, tag)` 提交，结果按 tag 路由回 `App._on_<tag>`。
`_do()` 会在执行命令前暂停实时刷新、执行完再恢复。

**新增功能时不要图省事直接在 Tk 回调里调 `Scope`**，会随机卡死或返回错乱数据。

**（二）鼠标拖动绝不能每个 motion 事件发一次 SCPI。**

触发电平拖拽和拖动平移都是：按下时记录起点 → 拖动过程中**只改本地状态和 matplotlib 视图** →
松开鼠标才下发一次命令。实时刷新本身已经把链路占得差不多，再叠几十条写入会直接卡死界面。

配套的一点：拖动进行中 `_on_trace()` 跳过坐标轴范围的重设（判断 `self.pan is not None`），
否则实时刷新每帧把视图拉回去，跟拖动预览互相打架。

## 5. 进度

第一阶段五项已完成，详见 `ROADMAP.md`。第二到五阶段未开始。

`ROADMAP.md` 里第二阶段（各触发类型的专属参数）是**当前最大的功能缺口**：切到 EDGE 以外的模式
只能设 source/slope/level，PULSE 的脉宽条件、SLOPE 的时间条件等无处可设，等于那些模式不可用。
调数字电路的话 PULSE 优先级最高。

## 6. 验证状态（重要）

**代码只做过语法检查（`python -m py_compile`），未经完整实机验证。** 本仓库没有测试、lint 和 CI，
唯一的验证方式是连真机手动跑。

已知曾实际跑通的部分：早期版本的 CSV 导出路径产出过 `waveform-normal.csv` / `waveform-over.csv`
（未纳入版本控制），说明连接、采集、导出这条主链路是通的。

**接手后应优先验证的项**，按风险排序：

1. **电压换算准确性** — 拿导出 CSV 的 Vpp 跟示波器屏幕读数对比。换算公式是按 DS1000E 旧固件的
   固定刻度写的，若对不上，问题在 `Scope.read_channel()` 那一行
2. **拖动平移的方向** — 波形应当跟随光标移动。若表现为"越拖越远"，说明 `_pan_finish()` 里
   offset 的符号反了
3. **探头衰减比切换后 V/div 是否被正确重设** — 涉及写入顺序
4. **配置的保存/加载往返** — 存一份、手动改乱示波器、再加载回来，看是否完全复原
5. **深存储 1M 点读取** — 耗时长，且会阻塞界面（改进项见 ROADMAP 第四阶段）

几个可调的经验值：`ds1102e_scope.py` 顶部的 `REFRESH_MS`（UI 刷新周期）、`MEAS_EVERY`
（每 N 帧刷一次测量值）、`_near_level()` 里 3% 的电平线抓取容差、滚轮 1/5 格的步进。
这些都是拍脑袋定的，按实际手感调。

## 7. 排障速查

| 现象 | 原因与处理 |
|------|-----------|
| `0xBFFF0015` / VISA 超时 | 先查示波器 `Utility → I/O 设置 → USB 设备` 是否为「计算机」而非 PictBridge；再查是否有别的程序占着资源 |
| 脚本报找不到设备 | UltraSigma 没装，或驱动被 NI-VISA / Keysight IO Suite 抢走 |
| 脚本连不上但 UltraSigma 能连 | **UltraSigma 的 SCPI Panel 没关**，USBTMC 资源被占用 |
| 波形不刷新、Status 一直 `WAIT` | 触发电平设在信号范围之外，没触发上。点 50% 按钮 |
| 设了触发电平没反应 | 超出 ±6 格被静默忽略（现在会提示，老版本不会） |
| 电压读数整体差 10 倍 | 探头衰减比设错 |
| 前面板按键失灵 | 程序异常退出没发出 `:KEY:FORC`，重启示波器 |
| 深存储读取报超时 | 没先 STOP，或存储深度不是 LONG |

## 8. 存疑项

**屏幕截图功能。** `官方软件/` 里带了 `Ultra Sigma patch file for Screenshot`，说明 UltraSigma
原生截图有问题。DS1102E 是否支持可用的 `:DISP:DATA?` 未经验证。实际意义有限——已能拿到原始数据
自行绘图，分辨率远高于示波器 320×234 的屏幕。
