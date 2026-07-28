"""
双击卡片可绑定的动作注册表。

设计
----
三张卡片（硬件 / 曲线 / VPS 流量）各自绑定一个动作 id，存在
`config.json` 的 `doubleclick` 里：

    "doubleclick": {"hw": "taskmgr", "chart": "this_pc", "traffic": "toggle_desktop_icons"}

**新增动作只要在 ACTIONS 里加一行**，设置窗的下拉框会自动多出这一项，
不用改 UI 代码。

处理函数签名统一为 `handler(app) -> None`，`app` 是 GlassMonitorApp 实例；
纯系统类动作用不到它，忽略即可。

只收录**非破坏性**动作：不放关机/睡眠/清空回收站这类——双击是很容易误触的
交互，误触代价必须足够低。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

ROOT = Path(__file__).resolve().parent
SYSROOT = os.environ.get("SystemRoot", r"C:\Windows")
SYS32 = os.path.join(SYSROOT, "System32")
CREATE_NO_WINDOW = 0x08000000


# ---------------------------------------------------------------- 启动原语

def _sys(name: str) -> str:
    return os.path.join(SYS32, name)


def _popen(args: List[str], *, hide_console: bool = True) -> None:
    """启动进程。hide_console=False 用于 cmd/powershell 这种要显示窗口的。"""
    subprocess.Popen(
        args,
        close_fds=True,
        creationflags=CREATE_NO_WINDOW if (hide_console and os.name == "nt") else 0,
    )


def _explorer(target: Optional[str] = None) -> None:
    _popen(["explorer.exe"] + ([target] if target else []))


def _msc(name: str) -> None:
    """管理单元（.msc）统一用 mmc 打开，比依赖文件关联稳。"""
    _popen([_sys("mmc.exe"), _sys(name)])


def _control(applet: Optional[str] = None) -> None:
    _popen([_sys("control.exe")] + ([applet] if applet else []))


def _uri(uri: str) -> None:
    """ms-settings: / ms-screenclip: 这类协议交给 shell。"""
    os.startfile(uri)  # noqa: S606 - 固定常量，非用户输入


def _shell_exec(verb: str, target: str) -> None:
    ctypes.windll.shell32.ShellExecuteW(None, verb, target, None, None, 1)


# ---------------------------------------------------------------- 桌面相关

_WM_COMMAND = 0x0111
# SHELLDLL_DefView 的「显示桌面图标」命令 ID（桌面右键 → 查看 → 显示桌面图标）
_CMD_TOGGLE_DESKTOP_ICONS = 0x7402
# Shell_TrayWnd 的「显示桌面」命令 ID（等同 Win+D）
_CMD_SHOW_DESKTOP = 419
_SMTO_ABORTIFHUNG = 0x0002
_VK_VOLUME_MUTE = 0xAD


def _find_desktop_defview() -> int:
    """
    找到承载桌面图标的 SHELLDLL_DefView。

    常规情况它挂在 Progman 下；但开启壁纸幻灯片/多显示器时，资源管理器会
    另建一个 WorkerW 并把 DefView 移过去，所以 Progman 找不到时要兜底枚举。
    """
    u = ctypes.windll.user32
    u.FindWindowW.restype = wintypes.HWND
    u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    u.FindWindowExW.restype = wintypes.HWND
    u.FindWindowExW.argtypes = [
        wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR
    ]

    progman = u.FindWindowW("Progman", None)
    if progman:
        dv = u.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
        if dv:
            return int(dv)

    found: List[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        dv = u.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        if dv:
            found.append(int(dv))
            return False
        return True

    u.EnumWindows(_cb, 0)
    return found[0] if found else 0


def toggle_desktop_icons() -> bool:
    """
    切换桌面图标显示/隐藏，返回是否找到桌面窗口。

    用 WM_COMMAND 0x7402 而不是直接 ShowWindow 隐藏 SysListView32：
    前者走 shell 自己的逻辑并把状态持久化，刷新桌面/切分辨率后不会复原。
    SendMessageTimeout 避免资源管理器卡住时把 UI 线程一起拖死。
    """
    dv = _find_desktop_defview()
    if not dv:
        return False
    u = ctypes.windll.user32
    out = ctypes.c_ulong(0)
    u.SendMessageTimeoutW(
        wintypes.HWND(dv), _WM_COMMAND,
        wintypes.WPARAM(_CMD_TOGGLE_DESKTOP_ICONS), wintypes.LPARAM(0),
        _SMTO_ABORTIFHUNG, 2000, ctypes.byref(out),
    )
    return True


def _show_desktop() -> None:
    """最小化全部窗口（等同 Win+D，再触发一次会还原）。"""
    u = ctypes.windll.user32
    u.FindWindowW.restype = wintypes.HWND
    u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    tray = u.FindWindowW("Shell_TrayWnd", None)
    if tray:
        u.SendMessageW(tray, _WM_COMMAND, _CMD_SHOW_DESKTOP, 0)


def _toggle_mute() -> None:
    u = ctypes.windll.user32
    u.keybd_event(_VK_VOLUME_MUTE, 0, 0, 0)
    u.keybd_event(_VK_VOLUME_MUTE, 0, 2, 0)


# ---------------------------------------------------------------- 注册表

class Action(NamedTuple):
    id: str
    group: str
    name: str
    run: Callable[[object], None]

    @property
    def label(self) -> str:
        return f"{self.group} · {self.name}"


def _widget_refresh_traffic(app) -> None:
    """立刻重新拉一次流量（configure 不传参 = 只唤醒轮询线程）。"""
    app.traffic.configure()


ACTIONS: Tuple[Action, ...] = (
    # —— 无 ——
    Action("none", "无", "不响应", lambda app: None),

    # —— 系统工具 ——
    Action("taskmgr", "系统", "任务管理器", lambda app: _popen([_sys("taskmgr.exe")], hide_console=False)),
    Action("resmon", "系统", "资源监视器", lambda app: _popen([_sys("resmon.exe")])),
    Action("perfmon", "系统", "性能监视器", lambda app: _popen([_sys("perfmon.exe")])),
    Action("devmgmt", "系统", "设备管理器", lambda app: _msc("devmgmt.msc")),
    Action("diskmgmt", "系统", "磁盘管理", lambda app: _msc("diskmgmt.msc")),
    Action("services", "系统", "服务", lambda app: _msc("services.msc")),
    Action("eventvwr", "系统", "事件查看器", lambda app: _msc("eventvwr.msc")),
    Action("taskschd", "系统", "任务计划程序", lambda app: _msc("taskschd.msc")),
    Action("regedit", "系统", "注册表编辑器", lambda app: _popen([os.path.join(SYSROOT, "regedit.exe")])),
    Action("msconfig", "系统", "系统配置", lambda app: _popen([_sys("msconfig.exe")])),
    Action("cleanmgr", "系统", "磁盘清理", lambda app: _popen([_sys("cleanmgr.exe")])),

    # —— 工具 ——
    Action("mstsc", "工具", "远程桌面", lambda app: _popen([_sys("mstsc.exe")])),
    Action("calc", "工具", "计算器", lambda app: _popen([_sys("calc.exe")])),
    Action("notepad", "工具", "记事本", lambda app: _popen([_sys("notepad.exe")], hide_console=False)),

    # —— 终端 ——
    Action("powershell", "终端", "PowerShell",
           lambda app: _popen([_sys(r"WindowsPowerShell\v1.0\powershell.exe")], hide_console=False)),
    Action("powershell_admin", "终端", "PowerShell（管理员）",
           lambda app: _shell_exec("runas", _sys(r"WindowsPowerShell\v1.0\powershell.exe"))),
    Action("cmd", "终端", "命令提示符", lambda app: _popen([_sys("cmd.exe")], hide_console=False)),

    # —— 文件夹 ——
    Action("this_pc", "文件夹", "此电脑", lambda app: _explorer("shell:MyComputerFolder")),
    Action("explorer", "文件夹", "资源管理器", lambda app: _explorer()),
    Action("downloads", "文件夹", "下载", lambda app: _explorer("shell:Downloads")),
    Action("recycle_bin", "文件夹", "回收站", lambda app: _explorer("shell:RecycleBinFolder")),
    Action("app_folder", "文件夹", "本程序目录", lambda app: _explorer(str(ROOT))),

    # —— 设置 ——
    Action("control_panel", "设置", "控制面板", lambda app: _control()),
    Action("ms_settings", "设置", "Windows 设置", lambda app: _uri("ms-settings:")),
    Action("settings_network", "设置", "网络和 Internet", lambda app: _uri("ms-settings:network-status")),
    Action("settings_display", "设置", "显示设置", lambda app: _uri("ms-settings:display")),
    Action("settings_datetime", "设置", "日期和时间", lambda app: _uri("ms-settings:dateandtime")),
    Action("ncpa", "设置", "网络连接（适配器）", lambda app: _control("ncpa.cpl")),
    Action("mmsys", "设置", "声音设置", lambda app: _control("mmsys.cpl")),

    # —— 桌面 ——
    Action("toggle_desktop_icons", "桌面", "显示/隐藏桌面图标", lambda app: toggle_desktop_icons()),
    Action("show_desktop", "桌面", "显示桌面（最小化全部）", lambda app: _show_desktop()),
    Action("screenclip", "桌面", "截图", lambda app: _uri("ms-screenclip:")),
    Action("toggle_mute", "桌面", "静音开关", lambda app: _toggle_mute()),
    Action("lock", "桌面", "锁定屏幕", lambda app: ctypes.windll.user32.LockWorkStation()),

    # —— 本程序 ——
    Action("widget_settings", "本程序", "打开设置", lambda app: app.open_settings()),
    Action("widget_topmost", "本程序", "切换置顶", lambda app: app._toggle_topmost()),
    Action("widget_compact", "本程序", "折叠/展开", lambda app: app._toggle_compact()),
    Action("widget_hide", "本程序", "隐藏悬浮窗", lambda app: app._set_visible(False)),
    Action("refresh_traffic", "本程序", "立即刷新流量", _widget_refresh_traffic),
)

BY_ID: Dict[str, Action] = {a.id: a for a in ACTIONS}
LABELS: List[str] = [a.label for a in ACTIONS]
BY_LABEL: Dict[str, Action] = {a.label: a for a in ACTIONS}

# 可绑定的卡片（key 与 GlassMonitorApp._zone_at 的返回值 / CARD_SPECS 一致）
ZONES: Tuple[Tuple[str, str], ...] = (
    ("clock", "日期时钟卡片"),
    ("hw", "硬件监控卡片"),
    ("speed", "实时网速卡片"),
    ("chart", "网速曲线卡片"),
    ("disk", "磁盘卡片"),
    ("proc", "进程 TOP 卡片"),
    ("sys", "系统信息卡片"),
    ("traffic", "VPS 流量卡片"),
)

# 默认绑定按「卡片内容 → 对应系统工具」配，点哪张卡就去哪
DEFAULT_BINDINGS: Dict[str, str] = {
    "clock": "settings_datetime",
    "hw": "taskmgr",
    "speed": "ncpa",
    "chart": "this_pc",
    "disk": "diskmgmt",
    "proc": "resmon",
    "sys": "ms_settings",
    "traffic": "toggle_desktop_icons",
}


def valid_id(action_id: str) -> bool:
    return action_id in BY_ID


def label_for(action_id: str) -> str:
    a = BY_ID.get(action_id)
    return a.label if a else BY_ID["none"].label


def id_for_label(label: str) -> str:
    a = BY_LABEL.get(label)
    return a.id if a else "none"


def run_action(action_id: str, app) -> Optional[str]:
    """执行动作。返回 None 表示成功，否则返回错误描述（调用方负责记日志）。"""
    action = BY_ID.get(action_id)
    if action is None:
        return f"未知动作 id: {action_id!r}"
    if action.id == "none":
        return None
    try:
        action.run(app)
    except Exception as e:
        return f"{action.id} 执行失败: {type(e).__name__}: {e}"
    return None
