# UltraScopeSimple 架构设计

本文档描述项目从"三个平铺脚本"重构为标准 Python 包的目标架构。
先读本文再动文件；文件整理按第 6 节的映射表执行。

---

## 1. 现状与问题

当前三个文件、1077 行：

| 文件 | 行数 | 承担的职责 |
|------|------|-----------|
| `ds1102e.py` | 339 | VISA 传输 + SCPI 指令 + 波形码值解码 + 单位格式化 + CSV 写盘 |
| `ds1102e_dump.py` | 141 | argparse CLI + 绘图导出 |
| `ds1102e_scope.py` | 597 | 工作线程 + 控件构建 + 事件处理 + 绘图 + 导出对话框 |

具体问题：

1. **不是包**。`import ds1102e` 依赖当前工作目录，只能在仓库根目录运行，无法 `pip install`，无法在别的项目里复用。
2. **`ds1102e.py` 混了四层职责**。VISA 会话管理、SCPI 语法、DS1000E 码值换算、CSV 序列化挤在一个模块里，任何一层想改都要读全文件。
3. **硬件魔数散落在方法体内**。`255 - data`、`- 130.0`、`/ 25.0`、`6 * scale`（12 格）写死在 `read_channel` / `capture` 里。这些是 **DS1000E 这一代固件特有的**假设，却没有集中记录，换型号时无从下手。
4. **`App` 是上帝类**（约 500 行）。控件布局、线程编排、状态回填、matplotlib 绘制、文件对话框全在一个类里，`_build_controls` 单个方法就有 110 行。
5. **分层被打穿**。GUI 的 connect 任务里直接写 `self.worker.scope = scope`——UI 代码在给工作线程的内部字段赋值。
6. **完全无法测试**。所有逻辑都吊在真实 VISA 会话上，连"码值 → 电压换算对不对"这种纯函数都没法验证，只能接着示波器手测。
7. **无打包元数据**：没有 `pyproject.toml`、没有版本号、没有命令入口，依赖只写在 README 里。

---

## 2. 设计目标

按重要性排序：

1. **可测试** —— 把 I/O 收敛到一个可替换的传输接口后，解码、触发子系统路由、CLI 参数处理都能脱离硬件做单元测试。这是本次重构最大的实际收益。
2. **硬件假设集中** —— DS1000E 的旧固件特性写在一处、可审计，将来加支持 `:WAV:PRE?` 的新机型只是多一份 profile。
3. **单向分层** —— 上层可以依赖下层，反之绝不允许。GUI 永远不碰传输层。
4. **模块可独立阅读** —— 单文件控制在 200 行以内。
5. **标准打包** —— `pip install -e .` 之后 `ultrascope-gui` / `ultrascope-dump` 直接可用。

**非目标**：不追求支持全系列 Rigol 机型；不引入 Qt 等重型 GUI 框架；不为了抽象而抽象——只有在上面五条能兑现时才拆分。

---

## 3. 分层

```
┌─────────────────────────────────────────────────┐
│  表现层    cli.py          gui/                 │
│            argparse        Tk 控件 + 工作线程    │
└──────────────────┬──────────────────────────────┘
                   │ 只调用 Scope 的公开方法
┌──────────────────▼──────────────────────────────┐
│  仪器层    scope.py                             │
│            Scope —— SCPI 指令门面                │
│            采集控制/垂直/水平/触发/测量/波形读取   │
└──────┬───────────────────────┬──────────────────┘
       │                       │
┌──────▼─────────┐   ┌─────────▼───────────────────┐
│ 领域层          │   │  profile.py                 │
│ waveform.py    │   │  DeviceProfile              │
│ Waveform 值对象 │◄──┤  码值换算/分格数/量程档位     │
│ 488.2 块解析    │   └─────────────────────────────┘
└──────┬─────────┘
       │
┌──────▼──────────────────────────────────────────┐
│  传输层    transport.py                          │
│            Transport 协议 + PyVisaTransport      │
│            + FakeTransport（测试用）              │
│            discovery.py —— VISA 资源枚举          │
└─────────────────────────────────────────────────┘

  横切：units.py（eng 格式化）  export.py（CSV/PNG 落盘）
```

依赖方向严格向下。`export.py` 只依赖 `waveform.py`，`units.py` 不依赖任何本项目模块。

---

## 4. 目标目录结构

```
pyproject.toml            打包元数据 + 依赖 + 命令入口
README.md                 用法文档（中文）
CLAUDE.md / AGENTS.md     给 AI 助手的仓库说明
docs/
  ARCHITECTURE.md         本文
  DS1000D_E_Manual_EN.pdf 官方 SCPI 手册（从仓库根目录移入）
src/
  ultrascope/
    __init__.py           公开 API 再导出 + __version__
    units.py              eng()
    transport.py          Transport 协议、PyVisaTransport、FakeTransport
    discovery.py          list_scopes()
    profile.py            DeviceProfile，DS1000E 的具体参数
    waveform.py           Waveform 值对象、488.2 块解析、码值解码
    scope.py              Scope 门面 + ScopeSettings 快照
    export.py             save_csv() / save_png()
    cli.py                argparse 命令行工具
    gui/
      __init__.py         main()
      worker.py           Worker 线程 + 任务/结果协议
      state.py            GUI 侧的设置快照与组合框选项表
      panels.py           各控制面板控件类
      plot.py             matplotlib 画布控件
      app.py              窗口装配与事件分发
tests/
  test_units.py
  test_waveform.py        用 FakeTransport 验证解码与时间轴
  test_scope.py           触发子系统路由、"未提供即不下发"语义
  test_cli.py             参数解析
```

---

## 5. 各模块职责

### `transport.py`
定义 `Transport` 协议：`write(cmd)` / `query(cmd)` / `read_raw()` / `timeout` / `close()`。
- `PyVisaTransport` —— 现有 pyvisa 实现，负责 `chunk_size`、超时切换。
- `FakeTransport` —— 由预置的 `{命令: 响应}` 表驱动，并可返回构造好的 488.2 数据块，供测试使用。

超时常量 `TIMEOUT_NORM_MS` / `TIMEOUT_RAW_MS` 移到此处，深存储读取时的超时切换用上下文管理器封装，替代现在散在 `read_channel` 里的 `try/finally`。

### `profile.py`
`DeviceProfile` 数据类，把第 1 节问题 3 的魔数全部收编：

| 字段 | 当前写死在 | 值 |
|------|-----------|-----|
| `code_inverted` | `read_channel` 的 `255 - data` | True |
| `code_center` | `read_channel` 的 `130.0` | 130.0 |
| `codes_per_div` | `read_channel` 的 `25.0` | 25.0 |
| `h_divisions` | `capture` 的 `6 * scale` | 12 |
| `has_preamble` | 注释里说明"无 `:WAV:PRE?`" | False |
| `volt_scales` / `time_scales` | 模块级常量 | 沿用 |
| `screen_points` | README 里的 600 | 600 |
| `trigger_subsystems` | `TRIG_SUBSYS` | 沿用 |

`DS1000E = DeviceProfile(...)`。将来支持带 preamble 的机型时，只需再加一个 profile 并让 `waveform.decode` 走 preamble 分支。

### `waveform.py`
- `parse_block(raw) -> bytes` —— IEEE 488.2 定长块头解析（现在内联在 `read_channel`）。
- `decode(payload, profile, volt_scale, volt_offset) -> np.ndarray` —— 纯函数，**这是最值得先写测试的地方**。
- `Waveform` 数据类：`t`、`channels: dict[int, np.ndarray]`、`timebase`、`time_offset`、`points_mode`、`captured_at`。

现在 `capture()` 返回裸元组 `(t, traces)`，导出层和 GUI 都要自己再查一遍时基。改为值对象后元数据随波形一起传递，CSV/PNG 不必再回头问仪器。

### `scope.py`
`Scope` 保留现有的方法分组（采集控制 / 采集设置 / 水平垂直 / 测量 / 波形），但：
- 构造函数接收 `Transport` 与 `DeviceProfile`，不再自己 `open_resource`；便捷构造器 `Scope.connect(resource=None)` 维持现在的易用性。
- 新增 `snapshot() -> ScopeSettings`。现在这段逻辑是 GUI 里的 `App._read_settings_job` 静态方法——它读的全是仪器状态，属于仪器层，不属于 UI。
- 保留 **"参数为 `None` 即不下发"** 的语义。这是本项目的核心约定：不显式传的设置一律保持示波器面板上的现状，`ds1102e_dump.py` 的安全性完全建立在这条上，必须在重构中原样保留并补测试。
- 保留 `close()` 发送 `:KEY:FORC` 交还面板控制权。

### `gui/`
拆解现在 597 行的单文件：

- **`worker.py`** —— `Worker` 线程。任务队列取 `func(scope)`，结果以 `(tag, "ok"|"error", payload)` 回投 `ui_queue`；队列空且 `streaming` 置位时自行采一帧（live 模式）。**新增 `connect(resource)` / `disconnect()` 任务**，由 Worker 自己持有并创建 `Scope`，消除现在 UI 回调里 `self.worker.scope = scope` 的越界赋值。
- **`state.py`** —— `ScopeSettings` 在 UI 侧的映射，以及组合框的 `(显示文本, 实际值)` 选项表。**替换掉现在的 `_parse_combo`**：那段代码把 `"20 mV"` 这类格式化字符串用 `eng()` 逐个反查回数值，一旦格式化改动就静默失配，改为直接存值即可。
- **`panels.py`** —— Connection / Acquisition / Channel / Horizontal / Trigger / Export 各一个控件类，对外暴露"当前值"和"变更回调"，不认识 `Scope`。
- **`plot.py`** —— matplotlib 画布控件，只接受 `Waveform` 并重画。
- **`app.py`** —— 装配窗口、`_drain` 排空结果队列并按 tag 分发、`_do` 的"暂停 live → 执行 → 恢复 live"编排。目标 200 行以内。

线程约定不变，且必须在重构中保持：**`Scope` 非线程安全，所有仪器 I/O 只在 Worker 线程串行执行，Tk 线程只碰控件。**

### `cli.py` / `export.py`
`cli.py` 保持现有参数不变（README 已发布这些选项）。现在内联在 `ds1102e_dump.py` 里的那段 matplotlib 绘图代码抽到 `export.save_png()`，与 GUI 的 PNG 导出共用。

---

## 6. 文件迁移映射

| 现位置 | 去向 |
|--------|------|
| `ds1102e.py` `eng()` | `src/ultrascope/units.py` |
| `ds1102e.py` `list_scopes()` | `src/ultrascope/discovery.py` |
| `ds1102e.py` `TIMEOUT_*`、pyvisa 会话 | `src/ultrascope/transport.py` |
| `ds1102e.py` `VOLT_SCALES`/`TIME_SCALES`/`TRIG_SUBSYS`/码值常量 | `src/ultrascope/profile.py` |
| `ds1102e.py` `read_channel` 的块解析与换算 | `src/ultrascope/waveform.py` |
| `ds1102e.py` `Scope`、`ScopeError`、`SOURCE_MAP` | `src/ultrascope/scope.py` |
| `ds1102e.py` `save_csv()` | `src/ultrascope/export.py` |
| `ds1102e_dump.py` 全部 | `src/ultrascope/cli.py`（绘图部分入 `export.py`） |
| `ds1102e_scope.py` `Worker` | `src/ultrascope/gui/worker.py` |
| `ds1102e_scope.py` `App._build_*` | `src/ultrascope/gui/panels.py` + `plot.py` |
| `ds1102e_scope.py` `App._read_settings_job` | `src/ultrascope/scope.py` 的 `Scope.snapshot()` |
| `ds1102e_scope.py` `App` 其余部分 | `src/ultrascope/gui/app.py` |
| `DS1000D_E_Manual_EN.pdf` | `docs/` |
| `waveform.png` | 删除（示例输出，非源码；`.gitignore` 已排除 `*.csv`，同步加 `*.png` 例外规则） |

不保留 `ds1102e.py` 的兼容 shim——仓库无外部使用者，留着只会让新旧两套入口并存。

---

## 7. 命令入口

`pyproject.toml` 声明：

```toml
[project.scripts]
ultrascope-dump = "ultrascope.cli:main"
ultrascope-gui  = "ultrascope.gui:main"

[project.optional-dependencies]
gui = ["matplotlib"]
```

`pyvisa`、`numpy` 为必需依赖；`matplotlib` 归入 `gui` extra，这样纯 CLI 的 CSV 导出不必装绘图库（现在 `ds1102e_dump.py` 已经是把 `import matplotlib` 延迟到 `--plot` 分支里，正好对应）。

重构后的运行方式：

```bash
pip install -e ".[gui]"
ultrascope-dump --single --mode raw --memdepth long --channels 1
ultrascope-gui
```

`python -m ultrascope.cli` 与 `python -m ultrascope.gui` 同样可用。

---

## 8. 硬件约束（重构中不得改变的行为）

以下均已在现有代码中验证，重构只搬位置、不改语义：

- DS1000E 使用 Rigol 旧版 SCPI 方言，**没有 `:WAV:PREamble`**，故码值换算是固定刻度：码值反相、以 130 为中心、每 25 码一个垂直分格。
- 时间轴恒为 12 个水平分格、以时间偏移为中心；仪器不提供逐点时标。
- 深存储（`points="raw"`）只能在 STOP 状态读取，需 120 s 超时，1M 点很慢；`normal` 为屏幕 600 点。
- `:TRIG:MODE?` 返回完整单词但指令子树是缩写，必须经 `trigger_subsys()` 路由；ALTERNATION 模式没有 sweep 设置。
- 测量值不可用时仪器返回 >1e37 的哨兵值，须映射为 `None`。
- 平均次数限 2–256。
- USB 驱动来自官方 UltraSigma；未安装时 VISA 枚举不到设备。
- 退出时发送 `:KEY:FORC` 交还前面板控制权。

---

## 9. 已知缺陷

本轮**以纯搬家为主**：只改结构不改行为，这样真机验证时任何行为变化都必定是搬家引入的，不必区分是搬错了还是修错了。

已修（属于结构调整本身，无法"不修"）：

2. ~~`_parse_combo` 通过格式化字符串反查数值，失配时静默返回 `None`（设置被悄悄丢弃）。~~
   已由 `gui/state.py` 的 `OptionTable` 取代：标签表只构建一次，回填时 `label_for()` 就近吸附到支持的档位，仪器报 `0.0200000001` 也能正确选中 `20 mV`。见 `tests/test_gui_state.py`。

待处理（纯行为改动，真机验证通过后另开一轮）：

1. `PlotCanvas.show` 的纵轴范围按 `max(abs(v))` 对称取值，忽略了通道垂直偏移；有直流偏置的信号会贴边显示。应改为按数据实际上下界加余量。
3. `Scope.single()` 在 ALTERNATION 之外的模式下会把 sweep 改成 SINGLE 且不恢复原值，调用后仪器状态与调用前不一致。应记录并恢复，或在文档中明确这是预期行为。
4. GUI 的单次采集超时硬编码为 `app.SINGLE_TIMEOUT_S = 30.0`，未像 CLI 的 `--trigger-timeout` 那样可配。
5. GUI 触发源组合框选项为 `CHAN1/CHAN2/...`，但仪器 `:TRIG:EDGE:SOUR?` 回的是 `CH1`，回填时会把选项表里没有的值塞进 readonly 组合框。下发时 `SOURCE_MAP` 会正确映射回 `CHAN1`，功能不受影响，只是显示不一致。重构前同样如此。

### 真机验证新发现（2026-08-15，DS1102E 固件 00.04.02.01.00）

6. **DS1000E 的 RAW 块头会谎报长度，`parse_block` 不校验实际字节数。**

   实测：`:WAV:POIN:MODE RAW` 下，**无论存储深度是 LONG 还是 NORMAL，块头一律
   声明 `#8 01048576`（1 M 字节）**，但仪器实际只发出一小段就断流：

   | 实测项 | 值 |
   |--------|-----|
   | 块头声明 payload | 1 048 576 字节 |
   | 实际送达 payload | 12 278 字节（另一次 16 384，随缓冲内容浮动） |
   | 断流耗时 | 0.5 s |

   由于 `raw[header_len:header_len + length]` 这个切片会被 Python 静默截断，
   **每一次 RAW 读取实际上都是一次残缺传输，却看起来完全像成功的短采集**。
   先前记为"成功读到 16384 点"的那次即是如此，并非仪器合法地只给 16384 点。

   `parse_block` 应比对声明长度与实际长度，不足即报错——否则调用方无从分辨
   "这段就是全部数据" 与 "数据断了一半"。此缺陷自初版即存在。

7. **1 M 点深存储读取从未成功过，且不是超时能解决的。**
   已用 git 历史中的重构前代码对拍，失败方式完全相同（138.3 s 后
   `VI_ERROR_TMO`），**不是重构引入的**。`transport.timeout()` 也已验证工作正常
   （10000 → 120000 → 还原 10000），超时确实生效。

   但由第 6 条可知，那 120 s 并非在传输数据，而是在等永远不会到达的字节——仪器
   发完约 12 K 就停了。**因此加长超时或分块读取都不会奏效，问题在于这一代固件
   根本不通过单次 `:WAV:DATA?` 交付完整深存储。** 若要支持，需要另找途径
   （查 `docs/DS1000D_E_Manual_EN.pdf` 是否有分段读取指令），而不是调参数。

   在此之前 README 不应宣称支持完整 1 M 点采集。

   附带观察：验证期间示波器扫描方式为 SINGLE，`:RUN` 之后状态仍为 STOP —— 无触发
   事件就不会重新采集。排查深存储问题时要先确认扫描方式，否则会误判为"采集没生效"。

---

## 10. 实施进度

- [x] 建 `pyproject.toml` 与 `src/ultrascope/` 骨架
- [x] 下移叶子模块：`units.py`、`transport.py`、`profile.py`、`waveform.py`、`discovery.py`
- [x] 迁 `scope.py`（含 `snapshot()`）
- [x] 迁 `export.py` 与 `cli.py`（CLI 选项与迁移前逐字一致）
- [x] 拆 GUI 五个模块：`worker.py` / `state.py` / `panels.py` / `plot.py` / `app.py`
- [x] 删除旧的三个平铺脚本，更新 README / CLAUDE.md / AGENTS.md
- [x] 离线测试 56 项通过；GUI 装配冒烟测试通过（建窗、启停控件、绘图、测量回显、设置回填）
- [x] **真机验证 CLI**（DS1102E 固件 00.04.02.01.00）
  - [x] 资源枚举、连接、`*IDN?`
  - [x] `snapshot()`（搬家风险点之一）
  - [x] `transport.timeout()` 上下文管理器（搬家风险点之二）：10000 → 120000 → 还原
  - [x] 默认导出：600 点、CH2 关闭被跳过、CSV 表头正确
  - [x] 时间轴：`(t[-1]-t[0])/timebase == 12.0`，精确等于 12 个水平分格
  - [x] **码值解码交叉验证**：仪器 `:MEAS:VMIN?` 报 −120 mV，`decode()` 独立算得
        −0.120 V，逐位吻合
  - [x] `--measure` 哨兵值：仪器返回 `99e36`，正确映射为 `None`
  - [x] `--plot` PNG 输出
  - [x] `close()` 发送 `:KEY:FORC` 交还面板
  - [x] 深存储 RAW 读取路径可走通（NORMAL 深度）——但见第 9 节缺陷 6，该次"成功"
        实为块截断
  - [ ] `--single` 等待触发（未验）
  - [ ] `--acquire average` 平均采集（未验）
- [ ] **接真机验证 GUI**：连接/断开、live 刷新、Run/Stop/Auto/Single/Force、通道与时基与触发改设、深存储采集、CSV/PNG 导出、关窗时面板控制权交还
- [ ] 处理第 9 节缺陷，建议顺序：先 6（静默截断，属正确性问题），再 1/3/4/5，最后 7（需分块读取，工作量最大）

GUI 部分必须接真实示波器手动点击——自动化测试只覆盖到仪器层以下，冒烟测试只证明控件能建起来、显示路径不报错，**不能证明 SCPI 交互正确**。
