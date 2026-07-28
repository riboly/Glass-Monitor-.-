# Glass Monitor — 硬件 / 网络悬浮监控

低资源占用的 Windows 桌面悬浮监控工具，视觉为 **iOS 27 透明玻璃** 风格，默认贴在屏幕 **右侧**。

## 功能

卡片全部可开关、可调整上下顺序，窗口高度按勾选的卡片自动伸缩。

| 卡片 | 说明 | 默认 |
|------|------|:---:|
| **日期时钟** | 大号时间 + 日期 / 星期 / 第几周，周末星期标红 | 关 |
| 硬件监控 | CPU / 内存 / 显卡占用 **真 alpha 抗锯齿**圆环（6× 超采样掩膜）+ 温度 | 开 |
| 实时网速 | 上传 / 下载双胶囊 | 开 |
| 网速曲线 | 双色面积曲线 + 峰值 | 开 |
| **磁盘** | 全盘读 / 写速率 + 各分区占用条（阈值变色） | 关 |
| **进程 TOP** | CPU 占用前三的进程（名称 / 内存 / CPU%） | 关 |
| **系统信息** | 开机时长 · 显存占用 · 电池（无电池自动省略） | 关 |
| VPS 流量 | 横向进度条：&lt;50% 绿 / 50–70% 黄 / &gt;70% 红 | 开 |

| 其它 | 说明 |
|------|------|
| CPU / GPU 温度 | GPU 经 NVML；CPU 见下方「温度说明」 |
| 交互 | 拖拽移动 · **贴边吸附** · **锁定位置** · 双击自定义动作 · 右键菜单 · 折叠 · 位置记忆 |
| **全屏自动隐藏** | 前台是全屏应用（游戏 / 视频）时自动收起，退出全屏自动回来 |

### 托盘与设置

- **无右上角关闭按钮**；退出请用托盘右键 → 退出  
- 托盘右键：**设置 / 显示·隐藏（勾选）/ 置顶显示（勾选）/ 退出**  
- 悬浮窗右键：设置、显示/隐藏、置顶、折叠、退出  
- 所有选项写入程序目录的 `config.json`，可随程序目录一起迁移  

设置项：

| 项 | 说明 |
|----|------|
| 卡片显示与顺序 | 8 张卡片可分别勾选，并可拖动或用上下按钮调整位置；窗口高度自动伸缩 |
| 锁定位置 | 防止误拖 |
| 全屏自动隐藏 | 全屏应用前台时收起 |
| 贴边吸附距离 | 0–64 px，0 = 关闭 |
| 配色方案 | 15 套：午夜玻璃 / 深海静谧 / 极光薄荷 / 钴蓝暮光 …… |
| 窗口圆角半径 | 8–36，默认 22 |
| 窗口 / 卡片背景不透明度 | 横向滑块，拖动实时生效 |
| 文字暗光晕强度 | 0–100%，拖动实时生效；浅色桌面兜对比度，0=关闭 |
| 开机自启 | 写入用户 Startup 目录 `GlassMonitor.bat` |
| 启动时置顶 / 显示 | 与托盘勾选状态联动 |
| 双击卡片动作 | 8 张卡片各自绑定，40 个动作可选（见「双击动作」） |
| 布局预设 / 窗口宽度 | 紧凑、标准、宽松或 260–420 px 自定义宽度 |
| 鼠标穿透 | 整窗不拦截桌面鼠标，通过托盘菜单恢复 |
| 资源告警 | CPU/GPU 温度、内存、磁盘、VPS 流量阈值通知 |
| 运行诊断 | 采集耗时、传感器可用性、流量状态和用户配置路径 |
| 全局快捷键 | 默认 `ctrl+shift+m` 显示/隐藏；**快捷键显示时强制置顶** |
| 显示服务器流量 | 开关底部流量条 |
| 流量更新间隔 | 默认 **5 分钟**（1–120 可调） |
| 流量接口地址 / 代理 | 换 VPS 直接在这里改，保存即生效；详见 [`vps流量接口.md`](vps流量接口.md) |

**状态联动：**

- 快捷键 **显示** → 悬浮窗显示 + `always_on_top=true` + 托盘「置顶显示」勾选  
- 托盘状态与用户配置实时同步  
- 位置保存在程序目录的 `window_pos.json`

### 服务器流量 API

配置见用户配置 → `traffic`：

```json
"traffic": {
  "enabled": true,
  "url": "https://nosla-substore-traffic-api.../?key=...",
  "proxy": "http://127.0.0.1:7890",
  "interval_min": 5
}
```

- 优先经 mihomo 代理，失败再直连  
- 失败时**沿用上次成功数据**（界面可带「缓存」标记）  
- **连续 10 次**失败 → 显示「获取数据失败」  
- 使用量 = upload + download；百分比 = used / total

## 目录结构

```
jiankong/
├── monitor.py          # 启动入口
├── glass_ui.py         # 悬浮窗主体：布局 / 交互 / 每帧合成
├── app_storage.py      # 用户数据迁移 / 原子写入 / 本地备份
├── metrics_worker.py   # 后台指标采集与线程安全快照
├── alerting.py         # 阈值持续时间 / 冷却 / 迟滞状态机
├── win_display.py      # 多显示器工作区与位置约束
├── cards_meta.py       # 卡片注册表（顺序 / 显示名 / 默认开关）
├── actions.py          # 双击动作注册表（新增动作只改这里）
├── aa_draw.py          # 真 alpha 抗锯齿绘制（圆环 / 曲线 / 文字 / 进度条）
├── layered.py          # UpdateLayeredWindow 分层窗封装
├── metrics.py          # 指标采集（psutil / NVML / 可选 WMI 温度）
├── config.example.json # 可安全分享的默认配置，不含个人地址
├── requirements.txt    # 依赖
├── run.bat             # 一键启动
└── README.md           # 本文档
```

## 环境要求

- Windows 10/11
- Python **3.10+**（开发机使用 3.14）
- NVIDIA 显卡驱动（GPU 占用/温度依赖 NVML；无 NVIDIA 时 GPU 显示 N/A）

## 安装

```bat
cd C:\GROK\jiankong
py -3.14 -m pip install -r requirements.txt
```

## 启动

```bat
cd C:\GROK\jiankong
py -3.14 monitor.py
```

或双击 `run.bat`。

## 配置说明（程序目录 `config.json`）

### 分享安全

配置有意保存在程序目录，便于把当前布局、主题和卡片顺序一起交给朋友。流量接口和
代理也只保存在这份 `config.json`，源码中没有个人接口地址或兜底地址。分享前需要
手动把 `traffic.url` 和 `traffic.proxy` 清空，并避免附带旧的 `config.json.bak`；
`config.example.json` 本身不含个人地址，可用于核对默认配置。

| 字段 | 含义 | 默认 |
|------|------|------|
| `update_interval_ms` | 刷新周期（毫秒） | `1000` |
| `history_points` | 网速曲线历史点数 | `60` |
| `window.width` / `height` | 窗口宽度 / 按卡片动态计算的高度 | `300` / 动态 |
| `window.margin_right` / `margin_top` | 首次启动贴右侧边距 | `16` / `80` |
| `window.always_on_top` | 是否置顶 | `true` |
| `window.alpha` | 整体透明度 0~1 | `0.96` |
| `window.shell_opacity` | 窗口背景不透明度 0~100 | `100` |
| `window.card_opacity` | 卡片背景不透明度 10~100 | `100` |
| `window.click_through` | 整窗鼠标穿透 | `false` |
| `alerts.*` | 五类资源告警阈值、持续时间和冷却时间 | 默认关闭 |
| `text_halo` | 文字暗光晕强度 0~1（浅色桌面兜对比度，0=关） | `0.8` |
| `style.*` | 配色（CPU/MEM/GPU/上下行等） | iOS 风格色 |

直接修改配置后需 **重启程序**。窗口位置保存在同目录的 `window_pos.json`，删除它可恢复默认右侧布局。若旧版曾把运行数据放到 `%LOCALAPPDATA%\GlassMonitor`，程序目录缺少对应文件时会在首次启动时自动搬回。

## 温度说明

### GPU 温度

通过 `nvidia-ml-py`（NVML）读取。本机 RTX 4060 验证正常。

### CPU 温度

使用内置助手 `bin/cpu_temp_helper.exe`（基于 OpenHardwareMonitorLib，`serve` 常驻读传感器）：

- 源码：`vendor/cpu_temp_helper.cs`
- 依赖 DLL：`bin/OpenHardwareMonitorLib.dll`
- 仅在硬件卡或 CPU 温度告警需要时拉起；全部使用者关闭后自动结束

若显示 `—`：检查 `bin/cpu_temp_helper.exe` 是否存在；部分机器需管理员权限读 MSR。

重新编译助手（需 .NET Framework 4.x `csc`）：

```bat
C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /t:exe /out:bin\cpu_temp_helper.exe /r:vendor\ohm\OpenHardwareMonitor\OpenHardwareMonitorLib.dll vendor\cpu_temp_helper.cs
copy /Y vendor\ohm\OpenHardwareMonitor\OpenHardwareMonitorLib.dll bin\
```

## 低资源设计要点

1. **无 Electron / 无 PyQt**：仅用标准库 `tkinter` + `psutil` + NVML  
2. **1 秒刷新**，CPU 使用 `cpu_percent(interval=None)` 非阻塞  
3. **不在热路径起子进程**（避免每秒 `nvidia-smi` / 慢 WMI）  
4. 网络曲线只保留 `history_points` 个点，系统指标在后台线程采集  
5. GPU、CPU 温度及各类采集按启用卡片和告警延迟启动，共享依赖按需保留  

在本机目标功耗：空闲时额外 CPU 通常 **&lt; 1%**，内存大约 **30–50 MB** 量级（视 Python 运行时而定）。

## 交互

| 操作 | 行为 |
|------|------|
| 左键拖拽 | 移动窗口（松手写回 `window_pos.json`） |
| 双击硬件卡片 | **可自定义**，默认任务管理器 |
| 双击网速曲线卡片 | **可自定义**，默认「此电脑」 |
| 双击 VPS 流量卡片 | **可自定义**，默认显示 / 隐藏桌面图标 |
| 双击网速胶囊 / 卡片间隙 | 无反应 |

八张卡片的双击动作在 **设置 → 双击卡片动作** 里各自绑定，共 40 个可选动作
（系统工具 / 终端 / 文件夹 / 设置 / 桌面 / 本程序），见下方「双击动作」。
| 右键 | 设置 / 显示·隐藏 / 置顶切换 / 折叠 / 退出 |
| 托盘右键 | 设置 / 显示·隐藏（勾选）/ 置顶显示（勾选）/ 退出 |

## 双击动作

八张卡片各自绑定一个动作，存在用户配置中：

```jsonc
"cards": {                            // 显示哪些卡片，窗口高度随之伸缩
  "clock": false,
  "hw": true, "speed": true, "chart": true,
  "disk": false, "proc": false, "sys": false, "traffic": true
},
"card_order": [                       // 悬浮窗从上到下的卡片顺序
  "clock", "hw", "speed", "chart", "disk", "proc", "sys", "traffic"
],
"doubleclick": {                      // 每张卡片双击执行什么
  "clock":   "settings_datetime",
  "hw":      "taskmgr",
  "speed":   "ncpa",
  "chart":   "this_pc",
  "disk":    "diskmgmt",
  "proc":    "resmon",
  "sys":     "ms_settings",
  "traffic": "toggle_desktop_icons"
}
```

> 流量卡的开关**只看 `cards.traffic`**；`traffic.enabled` 是自动同步的镜像，
> 供外部读取，手改无效（早期只有 `traffic.enabled` 的配置会自动迁移一次）。

**八张卡片都能各自绑定**（默认按卡片内容配对应工具：时钟卡 → 日期和时间，
磁盘卡 → 磁盘管理，进程卡 → 资源监视器，网速卡 → 网络连接，系统卡 → Windows 设置）。

可选动作（`actions.py` 的 `ACTIONS`，共 40 项）：

| 分组 | 动作 |
|------|------|
| 无 | 不响应 |
| 系统 | 任务管理器 · 资源监视器 · 性能监视器 · 设备管理器 · 磁盘管理 · 服务 · 事件查看器 · 任务计划程序 · 注册表编辑器 · 系统配置 · 磁盘清理 |
| 终端 | PowerShell · PowerShell（管理员）· 命令提示符 |
| 工具 | 远程桌面 · 计算器 · 记事本 |
| 文件夹 | 此电脑 · 资源管理器 · 下载 · 回收站 · 本程序目录 |
| 设置 | 控制面板 · Windows 设置 · 网络和 Internet · 显示设置 · 日期和时间 · 网络连接（适配器）· 声音设置 |
| 桌面 | 显示/隐藏桌面图标 · 显示桌面（最小化全部）· 截图 · 静音开关 · 锁定屏幕 |
| 本程序 | 打开设置 · 切换置顶 · 折叠/展开 · 隐藏悬浮窗 · 立即刷新流量 |

**只收录非破坏性动作** —— 不放关机/睡眠/清空回收站这类：双击很容易误触，
误触代价必须足够低。

### 自己加一个动作

在 `actions.py` 的 `ACTIONS` 元组里加一行即可，设置窗下拉框会自动多出这项，
**不用改任何 UI 代码**：

```python
Action("my_app", "终端", "我的工具",
       lambda app: _popen([r"D:\tools\my.exe"], hide_console=False)),
```

现成的启动原语：`_popen`（exe）、`_msc`（.msc 管理单元）、`_control`（控制面板项）、
`_explorer`（目录 / `shell:` 路径）、`_uri`（`ms-settings:` 这类协议）、
`_shell_exec("runas", ...)`（提权）。处理函数签名统一是 `handler(app)`，
`app` 是 `GlassMonitorApp` 实例，本程序类动作靠它操作 UI。

## 修改指南（维护）

### 改刷新速度

编辑用户配置中的 `update_interval_ms`（建议 ≥ 500，过低会增加 CPU）。

### 改配色 / 玻璃感

编辑用户配置 → `style`：

- `glass_bg`：底板色  
- `accent_cpu` / `accent_mem` / `accent_gpu`：圆环色  
- `accent_up` / `accent_down`：网速曲线  

UI 圆角与整体布局在 `glass_ui.py` 的 `GlassMonitorApp._layout`（返回全部坐标）。

### 改圆环 / 曲线几何

- 布局坐标：`GlassMonitorApp._layout`
- 各区块绘制：`_draw_hardware` / `_draw_speed` / `_draw_chart` / `_draw_traffic`
- 绘制原语：`aa_draw.render_ring` / `render_series` / `render_progress_bar` / `draw_text`
- 采样结构：`metrics.Sample` / `MetricsCollector`

### 增加新传感器

1. 在 `metrics.py` 增加读取函数（保持非阻塞或低频）  
2. 扩展 `Sample` 字段  
3. 在 `glass_ui` 的对应 `_draw_*` 里用 `draw_text` / `aa_draw` 原语画出来  

> 注意：新增绘制**不要**直接 `ImageDraw.text` 到透明 RGBA 上，
> 请走 `aa_draw.draw_text`（掩膜上色），否则会在浅色桌面下出现黑边。

### 开机自启（可选）

将 `run.bat` 快捷方式放入：

`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`

## 故障排查

| 现象 | 处理 |
|------|------|
| 提示缺模块 | `py -3.14 -m pip install -r requirements.txt` |
| GPU 全 N/A | 确认 NVIDIA 驱动；`python -c "import pynvml; pynvml.nvmlInit()"` |
| CPU 温度 `--` | 见上文，运行 LibreHardwareMonitor + 可选 WMI |
| 卡片间隙点不中 / 拖不动 | 分层窗按 **alpha 命中**：alpha=0 处自动点击穿透。`shell_opacity=0` 时只有卡片可拖，调高窗口背景不透明度即可整窗可拖 |
| 浅色桌面下文字发灰 | 设置 → 提高「卡片背景不透明度」（最有效），或调高「文字暗光晕强度」 |
| 显示器布局改变后位置异常 | 删除程序目录中的 `window_pos.json` 后重启 |

## 渲染架构（单窗口 · 真 alpha）

整个界面由 Pillow 合成**一张 RGBA 位图**，经 `UpdateLayeredWindow` 与桌面做
per-pixel alpha 合成；不使用 `transparentcolor` 色键，也没有 Tk Canvas。

- `aa_draw.py` — 所有绘制返回带真 alpha 的 RGBA
  - 文字先画到 **L 掩膜**再上色（直接 `draw.text` 到透明 RGBA 会把 RGB 混向黑色，
    等于预乘一次，推给 ULW 再预乘一次就是黑边）
  - 图形超采样后**只缩放掩膜**，用 BOX 做精确面积平均（无 LANCZOS 振铃/渗色）
  - `text_halo`：文字底下垫一层高斯模糊的暗光晕，深色桌面几乎不可见，
    浅色桌面给浅色文字兜对比度
- `layered.py` — RGBA → 预乘 BGRA 用 numpy 向量化；DIB/内存 DC 按尺寸缓存复用
- `glass_ui.py` — 外壳+卡片底板缓存为静态底图，每帧只重画圆环/文字/曲线

单帧合成 ≈ 13 ms，位图转换 ≈ 3 ms（1 秒刷新一次）。

窗口带 `WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW`：
点击/拖拽/双击都不夺取前台焦点，也不出现在 Alt-Tab。

## 稳定性说明

若程序无故退出，查看程序目录中的 `crash.log`。

- 拖拽热路径只有一次 `SetWindowPos`，不碰 Tk 几何、不重传位图
- 拖拽期间跳过重绘，松手后才写回 `window_pos.json`
- 退出前撤销所有挂起的 `after`，避免销毁窗口后 Tcl 报 `invalid command name`
- CPU 温度默认不跑 `wmic` 子进程（可用环境变量 `GM_CPU_TEMP_ACPI=1` 打开）

## 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07-28 | 硬件圆环随布局宽度缩放并保持等距；环内改为大数值与右下角小百分号，整组内容垂直对齐 |
| 2026-07-28 | 配置保存在程序目录；圆环随布局预设缩放；采集进程和线程按卡片/告警共享需求启停；原子配置写入、单实例、后台采集、多屏定位、资源告警、鼠标穿透、布局预设、诊断与输入校验 |
| 2026-07-28 | 设置页新增卡片拖拽排序和上下移动按钮；顺序保存到 `card_order`，兼容旧配置 |
| 2026-07-27 | 新增日期时钟卡片（置顶，默认关闭）：大号时间 + 日期 / 星期 / 周次，周末标红；动作扩到 40 项 |
| 2026-07-27 | 新增磁盘 / 进程 TOP / 系统信息三张卡片；卡片可逐张开关且窗口高度自适应；贴边吸附、锁定位置、全屏应用自动隐藏；动作扩到 39 项 |
| 2026-07-26 | 双击动作可自定义：新增 `actions.py` 动作注册表（34 项），三张卡片在设置窗各自绑定；旧 `open_taskmgr_on_doubleclick` 自动迁移 |
| 2026-07-26 | 移除硬编码的兜底接口地址/代理（缺字段时会悄悄回落旧 VPS）；空地址明确显示「未配置接口地址」；设置窗新增接口地址/代理输入框 |
| 2026-07-26 | 双击改为按卡片分区：硬件→任务管理器 / 曲线→此电脑 / 流量→显示隐藏桌面图标；新增 `vps流量接口.md` |
| 2026-07-26 | 修设置窗滚轮路由：配色下拉弹开时滚的是外层页面而非下拉列表（`bind_all` 挂在 all 层，抢了控件自己的滚轮） |
| 2026-07-26 | 设置窗新增「文字暗光晕强度」滑块（实时生效）；双击分流落实为 硬件卡→任务管理器 / 其它→此电脑 |
| 2026-07-26 | **重写为单窗口真 alpha 渲染**：修复浅色桌面锯齿、双击变暗、拖动卡顿三个问题（详见「渲染架构」） |
| 2026-07-19 | 双窗口：背景半透明/内容不透明；8× 软圆角；run.bat 先杀旧进程再启动 |
| 2026-07-19 | 去掉透明度 Bayer 颗粒（窗口 alpha + 实色合成）；图例「下载」右对齐 |
| 2026-07-19 | 透明度改横向滑块并实时生效；网络图例改用 ● 字符抗锯齿 |
| 2026-07-19 | 窗口背景 / 卡片背景不透明度分别可调（合成 + Bayer 抖动透桌面） |
| 2026-07-19 | GPU 改回 NVML 独显（废弃错误偏低的 WDDM/typeperf）；双击硬件面板开任务管理器 |
| 2026-07-19 | 统一四边边距 + 设置可调；去掉背景全透明；圆环按占用阈值黄/红变色 |
| 2026-07-19 | GPU 改 WDDM 独显聚合（对齐任务管理器）；去 Monitor 标题 |
| 2026-07-19 | 设置页可滚动 + 底部固定按钮；配色下拉浅底深字；切换配色即时生效 |
| 2026-07-19 | 托盘显示/隐藏合一；全局快捷键（可自定义，显示时强制置顶）；圆角半径可调；配置全量持久化；AA 网速箭头 |
| 2026-07-19 | 抗锯齿真圆角外壳（chroma 镂空）；托盘增加置顶勾选；流量已用/总量移至进度条下方并加大间距 |
| 2026-07-19 | 托盘图标 + 设置（5 套配色 / 开机自启 / 流量开关与间隔）；去掉关闭按钮；流量默认 5 分钟、失败缓存、连败 10 次提示失败 |
| 2026-07-19 | 抗锯齿圆环（Pillow 4× 超采样）；去掉卡片顶部白线；底部服务器流量进度条（绿/黄/红）+ mihomo 代理 API |
| 2026-07-19 | UI 精修 v2：硬件卡片三环等分；CPU/MEM/GPU 副行；双胶囊网速；OHM CPU 温度 |
| 2026-07-19 | 稳定性修复：移除 transparentcolor/亚克力；曲线原地更新；crash.log |
| 2026-07-19 | 初版：tkinter 玻璃悬浮窗；CPU/MEM/GPU 环；GPU 温度 NVML；网速曲线 |

## 许可

本地工具脚本，可按需修改。
