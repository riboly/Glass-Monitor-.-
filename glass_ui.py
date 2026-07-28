"""
iOS 风格玻璃悬浮监控 UI —— 单窗口 · 真 per-pixel alpha。

架构（2026-07-26 重写，修掉三个老问题）
--------------------------------------
旧版是「双窗口」：
  - fg(root)  普通 Tk 窗 + Canvas，靠 -transparentcolor 色键抠背景
  - bg        UpdateLayeredWindow 分层窗，只画外壳/卡片底板

由此产生三个互相纠缠的缺陷：

1. 浅色桌面锯齿严重
   色键只有 1-bit alpha，所有文字/图标的抗锯齿软边必须**预先混到深色卡片色**
   再抠图。深色桌面下刚好对得上；一旦桌面偏亮（卡片不透明度 <100 时桌面会透
   上来），这些烤死的深色软边就变成明显锯齿和黑边。

2. 双击变暗
   色键区域是点击穿透的，点击落到下面那层 bg 分层窗上，Windows 把它在同一
   置顶层内提到前面 —— 半透明深色卡片层于是盖在内容层之上，整体发暗。

3. 拖动卡顿
   每个 <B1-Motion> 都要跑一次 Tk 的 wm geometry（对带几十个 item 的 Canvas
   是完整重排），再额外给 bg 窗 SetWindowPos，两个窗口互相追。

现在只有一个分层窗：整屏（外壳 + 卡片 + 圆环 + 文字 + 曲线）由 Pillow 合成一张
RGBA，交给 UpdateLayeredWindow 与桌面真实混合。于是
  - 任何底色下软边都正确 → 锯齿消失
  - 只有一个窗、且 WS_EX_NOACTIVATE → 点击/双击不夺焦点、不变暗
  - 拖拽热路径只有一次 SetWindowPos → 跟手
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
import traceback
import tkinter as tk
from tkinter import messagebox
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


def _enable_per_monitor_dpi() -> None:
    """Prevent Windows from bitmap-scaling the window and its small text."""
    if os.name != "nt":
        return
    try:
        user32 = ctypes.windll.user32
        # PROCESS_PER_MONITOR_DPI_AWARE_V2 = -4
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


_enable_per_monitor_dpi()

from PIL import Image

from actions import DEFAULT_BINDINGS, ZONES, run_action, valid_id
from alerting import AlertManager
from aa_draw import (
    blend_hex,
    compose_shell_and_cards,
    composite_at,
    draw_dashed_hline,
    draw_text,
    render_arrow_badge,
    render_clock_time,
    render_pill,
    render_progress_bar,
    render_ring,
    render_series,
    text_width,
)
from autostart import is_autostart_enabled, set_autostart
from app_storage import (
    CONFIG_PATH,
    CRASH_LOG,
    DEFAULT_CONFIG_PATH,
    POS_PATH,
    append_crash_log,
    atomic_save_json,
    load_json,
    migrate_legacy_data,
)
from cards_meta import (
    CARD_HEAD_H,
    CARD_IDS,
    CARD_RADIUS,
    DEFAULT_CARDS,
    ROW_H,
    normalize_card_order,
)
from hotkey_mgr import HotkeyManager
from layered import (
    make_overlay_window,
    move_window,
    release_surface,
    set_click_through,
    update_layered_window,
    window_rect,
)
from metrics import (
    Sample,
    format_speed,
    format_temp,
    format_uptime,
    gpu_memory,
    shutdown_nvml,
    uptime_seconds,
)
from metrics_worker import MetricsWorker
from settings_ui import SettingsWindow
from themes import style_for
from traffic import (
    TrafficCollector,
    TrafficInfo,
    format_bytes,
    format_reset_days,
    traffic_bar_color,
)
from win_display import clamp_to_work_area, nearest_work_area, position_visible, work_areas

ROOT = Path(__file__).resolve().parent


def _load_json(path: Path, default: dict) -> dict:
    try:
        return load_json(path, default)
    except Exception as exc:
        append_crash_log(f"[config.load] {exc}")
        return default.copy()


def _save_json(path: Path, data: dict) -> bool:
    try:
        atomic_save_json(path, data)
        return True
    except Exception as exc:
        append_crash_log(f"[config.save] {exc}")
        return False


def _log_crash(msg: str) -> None:
    append_crash_log(msg)


def _hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _foreground_is_fullscreen() -> bool:
    """
    前台窗口是否占满了它所在的整块屏幕（全屏游戏 / 全屏视频 / 幻灯片）。

    刻意排除桌面本身（Progman / WorkerW）和任务栏 —— 它们也"占满屏幕"，
    但那正是我们想显示悬浮窗的时候。
    """
    try:
        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = ctypes.c_void_p
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return False
        buf = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(ctypes.c_void_p(hwnd), buf, 64)
        if buf.value in ("Progman", "WorkerW", "Shell_TrayWnd", "WindowsDashboard"):
            return False

        rect = wintypes.RECT()
        if not u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
            return False
        win_w = rect.right - rect.left
        win_h = rect.bottom - rect.top

        # 用窗口所在显示器的完整尺寸比对（多屏下不能拿主屏尺寸去比）
        MONITOR_DEFAULTTONEAREST = 2
        u.MonitorFromWindow.restype = ctypes.c_void_p
        mon = u.MonitorFromWindow(ctypes.c_void_p(hwnd), MONITOR_DEFAULTTONEAREST)

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not u.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(mi)):
            return False
        mw = mi.rcMonitor.right - mi.rcMonitor.left
        mh = mi.rcMonitor.bottom - mi.rcMonitor.top
        # 容差 2px：有些全屏窗口会比屏幕大一两像素
        return win_w >= mw - 2 and win_h >= mh - 2
    except Exception:
        return False


def ring_arc_color(pct: Optional[float], theme_color: str) -> str:
    """圆环进度色：<60% 主题色 / 60–79% 黄 / >=80% 红。"""
    if pct is None:
        return theme_color
    p = float(pct)
    if p >= 80:
        return "#FF453A"
    if p >= 60:
        return "#FFD60A"
    return theme_color


class GlassMonitorApp:
    CLOCK_H = 74
    SPEED_H = 52
    TRAFFIC_H = 86
    CHART_H = 190
    HW_CARD_H = 152
    PROC_ROWS = 3
    COMPACT_H = 52
    DEFAULT_CORNER_R = 22
    DEFAULT_CONTENT_MARGIN = 16
    LAYOUT_PRESETS = {
        "compact": (270, 10),
        "standard": (300, 12),
        "spacious": (340, 16),
    }

    # 兼容 settings_ui 的旧签名（它只拿来算高度提示，现已不用）
    BASE_H = 442
    TRAFFIC_GAP = 12

    def __init__(self) -> None:
        # 绘制缓存先建好：_apply_style_from_cfg / _compute_size 会失效它们
        self._base_img: Optional[Image.Image] = None
        self._base_key = None
        self._layout_cache: dict = {}
        self._ring_cache: dict = {}
        # 内容驱动高度所需（磁盘分区数 / 有无电池），必须早于 _compute_size
        self._parts: list = []
        self._battery = None
        self._top_procs: list = []
        self._last_heights: dict = {}

        migrate_legacy_data()
        defaults = _load_json(DEFAULT_CONFIG_PATH, {})
        self.cfg = self._normalize_cfg(_load_json(CONFIG_PATH, defaults))
        self.cfg["autostart"] = is_autostart_enabled()

        self.interval = max(500, int(self.cfg.get("update_interval_ms", 1000)))
        self.history_n = max(10, int(self.cfg.get("history_points", 60)))
        self._apply_style_from_cfg()

        self._compute_size()
        self._last_heights = self._card_heights()
        self.metrics_worker = MetricsWorker(
            history_points=self.history_n, interval_ms=self.interval
        )
        self._sync_metrics_worker()
        traf = self.cfg.get("traffic", {})
        self.traffic = TrafficCollector(
            url=str(traf.get("url", "")),
            proxy=traf.get("proxy") or None,
            interval_min=float(traf.get("interval_min", 5)),
            max_fails=10,
            enabled=self._traffic_collection_enabled(),
        )
        self._sync_traffic_collector()

        # -------- 运行时状态 --------
        self._sample: Optional[Sample] = None
        self._ups: List[float] = []
        self._downs: List[float] = []
        self._traffic_info: Optional[TrafficInfo] = None
        self._compact = False
        self._auto_hidden = False   # 因全屏应用临时隐藏（不影响用户的显示设置）
        self._dragging = False
        self._drag_moved = False
        self._drag_dx = self._drag_dy = 0
        self._tick_errors = 0
        self._last_metrics_t = 0.0
        self.alerts = AlertManager()
        self._paint_after_id = None
        self._opacity_after_id = None
        self._halo_after_id = None
        self._tick_after_id = None
        self._closing = False
        self._tray_icon = None
        self._settings: Optional[SettingsWindow] = None
        self._hotkeys = HotkeyManager()
        self._last_dbl = 0.0

        # -------- 窗口 --------
        # root 只做 Tk 管道（mainloop / after / 菜单 / 设置窗父级），不显示。
        self.root = tk.Tk()
        self.root.title("Glass Monitor")
        self.root.withdraw()

        # win 是唯一可见窗：分层 + 不可激活，内容全部由 ULW 位图提供。
        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.configure(bg="black")
        self.win.attributes("-topmost", self.always_on_top)
        self._win_x, self._win_y = self._initial_pos()
        self.win.geometry(f"{self.W}x{self.H}+{self._win_x}+{self._win_y}")
        try:
            self.win.update_idletasks()
        except Exception:
            pass
        self._hwnd = make_overlay_window(self.win.winfo_id())
        set_click_through(self._hwnd, self.click_through)

        self._bind()
        self.root.protocol("WM_DELETE_WINDOW", self._on_wm_close)
        self.win.protocol("WM_DELETE_WINDOW", self._on_wm_close)
        self._start_tray()
        self._register_hotkey()

        if not self._visible:
            try:
                self.win.withdraw()
            except Exception:
                pass
        else:
            self._paint()
        self._tick_after_id = self.root.after(60, self._tick)

    # ------------------------------------------------------------ config
    def _normalize_cfg(self, cfg: dict) -> dict:
        cfg = dict(cfg)
        theme_id = cfg.get("theme_id", "midnight")
        cfg["theme_id"] = theme_id
        if "style" not in cfg or cfg.get("theme_id"):
            cfg["style"] = style_for(theme_id)
        traf = dict(cfg.get("traffic", {}))
        if "interval_min" not in traf:
            sec = int(traf.get("interval_sec", 300))
            traf["interval_min"] = max(1, sec // 60) if sec >= 60 else 5
        traf.setdefault("enabled", True)
        # 不再硬编码兜底接口地址/代理。
        # 以前这里 setdefault 了一份旧 VPS 的 url+key，config.json 里一旦缺字段
        # 就会悄悄回落到上一台机器：界面照常显示数字，但那是别人的流量，
        # 极难排查。现在留空 → 采集器直接给出 unconfigured 状态，卡片明说
        # 「未配置接口地址」。
        traf.setdefault("url", "")
        traf.setdefault("proxy", "")
        traf["interval_sec"] = int(traf["interval_min"]) * 60

        # 卡片显示开关。流量卡是特例：它同时决定**采集线程要不要跑**，
        # 所以两边保持同步，以 cards["traffic"] 为准。
        raw_cards = cfg.get("cards") or {}
        cards = {cid: bool(raw_cards.get(cid, DEFAULT_CARDS[cid]))
                 for cid in CARD_IDS}
        # 流量卡的唯一开关是 cards.traffic。
        # 老配置（没有 cards 段）才从 traffic.enabled 迁移过来一次；此后
        # traffic.enabled 只是**自动同步的镜像**，供外部读取，手改无效。
        # 不做「两个开关取与」——那会把 AND 结果写回 cards.traffic 变成粘滞状态：
        # 关得掉却开不回来。
        if "traffic" not in raw_cards:
            cards["traffic"] = bool(traf.get("enabled", True))
        if not any(cards.values()):
            cards["hw"] = True  # 全关会剩一个空壳，至少留一张
        cfg["cards"] = cards
        cfg["card_order"] = normalize_card_order(cfg.get("card_order"))
        traf["enabled"] = cards["traffic"]

        cfg["traffic"] = traf
        cfg.setdefault("autostart", False)

        # 时间主字形默认略微外扩；设置窗口可在 0–4px 内调整粗细。
        clock = dict(cfg.get("clock", {}))
        try:
            clock["stroke"] = max(0.0, min(4.0, float(clock.get("stroke", 1.4))))
        except Exception:
            clock["stroke"] = 1.4
        try:
            clock["tracking"] = max(0.0, min(6.0, float(clock.get("tracking", 2.0))))
        except Exception:
            clock["tracking"] = 2.0
        cfg["clock"] = clock

        win = dict(cfg.get("window", {}))
        win.setdefault("width", 300)
        win.setdefault("margin_right", 20)
        win.setdefault("margin_top", 72)
        win.setdefault("always_on_top", True)
        win.setdefault("visible", True)
        win.setdefault("corner_radius", self.DEFAULT_CORNER_R)
        win.setdefault("content_margin", self.DEFAULT_CONTENT_MARGIN)
        win.setdefault("shell_opacity", 100)
        win.setdefault("card_opacity", 100)
        win.setdefault("alpha", 0.96)
        win.setdefault("lock_pos", False)          # 锁定位置，防误拖
        win.setdefault("click_through", False)     # 整窗鼠标穿透，靠托盘恢复
        win.setdefault("layout_preset", "standard")
        win.setdefault("snap_px", 16)              # 贴边吸附距离，0=关
        win.setdefault("hide_on_fullscreen", True)  # 全屏应用前台时自动隐藏
        try:
            win["snap_px"] = max(0, min(64, int(win["snap_px"])))
        except Exception:
            win["snap_px"] = 16
        try:
            win["corner_radius"] = max(8, min(36, int(win["corner_radius"])))
        except Exception:
            win["corner_radius"] = self.DEFAULT_CORNER_R
        try:
            win["content_margin"] = max(8, min(36, int(win["content_margin"])))
        except Exception:
            win["content_margin"] = self.DEFAULT_CONTENT_MARGIN
        try:
            win["width"] = max(260, min(420, int(win["width"])))
        except Exception:
            win["width"] = 300
        try:
            win["shell_opacity"] = max(0, min(100, int(win["shell_opacity"])))
        except Exception:
            win["shell_opacity"] = 100
        try:
            win["card_opacity"] = max(10, min(100, int(win["card_opacity"])))
        except Exception:
            win["card_opacity"] = 100
        win.pop("bg_transparent", None)
        win["height"] = (
            self.BASE_H + self.TRAFFIC_H + self.TRAFFIC_GAP
            if traf.get("enabled", True)
            else self.BASE_H
        )
        cfg["window"] = win

        alerts = dict(cfg.get("alerts", {}))
        alerts.setdefault("enabled", False)
        defaults = {
            "cpu_temp": 85,
            "gpu_temp": 85,
            "memory": 90,
            "disk": 90,
            "traffic": 85,
            "duration_sec": 15,
            "cooldown_sec": 300,
            "hysteresis": 3,
        }
        for key, default in defaults.items():
            try:
                alerts[key] = max(0, int(alerts.get(key, default)))
            except Exception:
                alerts[key] = default
        cfg["alerts"] = alerts

        hk = dict(cfg.get("hotkey", {}))
        hk.setdefault("enabled", True)
        hk.setdefault("toggle_visible", "ctrl+shift+m")
        cfg["hotkey"] = hk

        # 双击各卡片绑定的动作（动作清单见 actions.py）
        dbl = dict(cfg.get("doubleclick", {}))
        if "hw" not in dbl:
            # 兼容旧配置项 open_taskmgr_on_doubleclick（布尔开关）
            legacy = cfg.get("open_taskmgr_on_doubleclick")
            dbl["hw"] = "taskmgr" if legacy is None or bool(legacy) else "none"
        for zone, _label in ZONES:
            if not valid_id(str(dbl.get(zone, ""))):
                dbl[zone] = DEFAULT_BINDINGS[zone]
        # 丢弃已不存在的卡片键，避免配置越积越脏
        cfg["doubleclick"] = {z: dbl[z] for z, _ in ZONES}
        cfg.pop("open_taskmgr_on_doubleclick", None)
        # 文字暗色光晕强度：浅色桌面下给浅色文字兜对比度，深色桌面几乎不可见。
        # 0 = 关闭；卡片不透明度越低、桌面越亮，越需要它。
        try:
            cfg["text_halo"] = max(0.0, min(1.0, float(cfg.get("text_halo", 0.8))))
        except Exception:
            cfg["text_halo"] = 0.8
        return cfg

    def _apply_style_from_cfg(self) -> None:
        style = self.cfg.get("style") or style_for(self.cfg.get("theme_id", "midnight"))
        win = self.cfg.get("window", {})
        self.alpha = float(win.get("alpha", 0.96))
        self.always_on_top = bool(win.get("always_on_top", True))
        self._visible = bool(win.get("visible", True))
        self.corner_radius = int(win.get("corner_radius", self.DEFAULT_CORNER_R))
        self.content_margin = int(win.get("content_margin", self.DEFAULT_CONTENT_MARGIN))
        self.shell_opacity = int(win.get("shell_opacity", 100))
        self.card_opacity = int(win.get("card_opacity", 100))
        self.lock_pos = bool(win.get("lock_pos", False))
        self.click_through = bool(win.get("click_through", False))
        self.snap_px = int(win.get("snap_px", 16))
        self.hide_on_fullscreen = bool(win.get("hide_on_fullscreen", True))
        self.cards = {cid: bool(self.cfg.get("cards", {}).get(cid, DEFAULT_CARDS[cid]))
                      for cid in CARD_IDS}
        self.card_order = normalize_card_order(self.cfg.get("card_order"))
        self.show_traffic = bool(self.cards.get("traffic", True))
        self.halo = float(self.cfg.get("text_halo", 0.8))
        self.style = style
        self.c = {
            "cpu": style.get("accent_cpu", "#64D2FF"),
            "mem": style.get("accent_mem", "#BF5AF2"),
            "gpu": style.get("accent_gpu", "#32D74B"),
            "up": style.get("accent_up", "#FF9F0A"),
            "down": style.get("accent_down", "#0A84FF"),
            "text": style.get("text_primary", "#F5F5F7"),
            "sub": style.get("text_secondary", "#8E8E93"),
            "muted": style.get("text_tertiary", "#636366"),
            "bg": style.get("glass_bg", "#121214"),
            "card": style.get("glass_card", "#1C1C1E"),
            "elev": style.get("glass_elevated", "#2C2C2E"),
            "border": style.get("glass_border", "#3A3A3C"),
            "sep": style.get("separator", "#2C2C2E"),
        }
        self._base_img = None
        self._base_key = None
        self._ring_cache.clear()
        self._layout_cache = {}

    # ------------------------------------------------------------ 卡片
    def _metric_requirements(self) -> dict[str, bool]:
        alert_cfg = self.cfg.get("alerts", {}) or {}
        alerts_enabled = bool(alert_cfg.get("enabled", False))

        def alert_active(key: str) -> bool:
            try:
                return alerts_enabled and int(alert_cfg.get(key, 0)) > 0
            except (TypeError, ValueError):
                return False

        hw = bool(self.cards.get("hw", False))
        disk = bool(self.cards.get("disk", False))
        sys_info = bool(self.cards.get("sys", False))
        return {
            "basic": hw or alert_active("memory"),
            "gpu_stats": hw or alert_active("gpu_temp"),
            "nvml": hw or sys_info or alert_active("gpu_temp"),
            "cpu_temp": hw or alert_active("cpu_temp"),
            "network": bool(
                self.cards.get("speed", False) or self.cards.get("chart", False)
            ),
            "disk_io": disk,
            "disk_parts": disk or alert_active("disk"),
            "processes": bool(self.cards.get("proc", False)),
            "battery": sys_info,
        }

    def _sync_metrics_worker(self) -> None:
        requirements = self._metric_requirements()
        self.metrics_worker.configure(requirements, self.interval)
        if any(requirements.values()):
            self.metrics_worker.start()
        else:
            self.metrics_worker.stop()

    def _traffic_collection_enabled(self) -> bool:
        alert_cfg = self.cfg.get("alerts", {}) or {}
        alert_enabled = (
            bool(alert_cfg.get("enabled", False))
            and int(alert_cfg.get("traffic", 0)) > 0
        )
        return bool(self.cards.get("traffic", False) or alert_enabled)

    def _sync_traffic_collector(self) -> None:
        enabled = self._traffic_collection_enabled()
        traf = self.cfg.get("traffic", {}) or {}
        self.traffic.configure(
            url=str(traf.get("url", "")),
            proxy=str(traf.get("proxy", "")),
            interval_min=float(traf.get("interval_min", 5)),
            enabled=enabled,
        )
        if enabled:
            self.traffic.start()
        else:
            self.traffic.stop()

    def _enabled_cards(self) -> List[str]:
        out = [c for c in self.card_order if self.cards.get(c)]
        return out or ["hw"]  # 兜底：全关掉会得到一个 24px 高的空壳

    def _card_heights(self) -> dict:
        """
        各卡片高度。disk / sys 是**内容驱动**的：
        分区数、有没有电池都会改变行数，所以不能写死。
        """
        n_parts = max(1, min(3, len(self._parts)))
        n_sys = 1 + (1 if self._battery is not None else 0)
        return {
            "clock": self.CLOCK_H,
            "hw": self.HW_CARD_H,
            "speed": self.SPEED_H,
            "chart": self.CHART_H,
            "disk": CARD_HEAD_H + n_parts * ROW_H,
            "proc": CARD_HEAD_H + self.PROC_ROWS * ROW_H,
            "sys": CARD_HEAD_H + n_sys * ROW_H,
            "traffic": self.TRAFFIC_H,
        }

    def _compute_size(self) -> None:
        win = self.cfg.get("window", {})
        self.W = int(win.get("width", 300))
        pad = int(self.content_margin)
        section = max(10, min(16, pad))
        order = self._enabled_cards()
        hs = self._card_heights()
        self.H = pad * 2 + sum(hs[c] for c in order) + section * (len(order) - 1)
        win["height"] = self.H
        self.cfg["window"] = win
        self._base_img = None
        self._layout_cache = {}

    def _initial_pos(self) -> Tuple[int, int]:
        win = self.cfg.get("window", {})
        areas = work_areas()
        primary = next((area for area in areas if area.primary), areas[0] if areas else None)
        pos = _load_json(POS_PATH, {})
        if "x" in pos and "y" in pos:
            x, y = int(pos["x"]), int(pos["y"])
        else:
            right = primary.right if primary else self.root.winfo_screenwidth()
            top = primary.top if primary else 0
            x = right - self.W - int(win.get("margin_right", 20))
            y = top + int(win.get("margin_top", 72))
        if areas and not position_visible(x, y, self.W, self.H):
            x = primary.right - self.W - int(win.get("margin_right", 20))
            y = primary.top + int(win.get("margin_top", 72))
        x, y = clamp_to_work_area(x, y, self.W, self.H)
        return int(x), int(y)

    # ------------------------------------------------------------ 布局
    def _layout(self) -> dict:
        order = self._enabled_cards()
        hs = self._card_heights()
        key = (self.W, self.H, self.content_margin, tuple(order),
               tuple(hs[c] for c in order))
        if self._layout_cache.get("_key") == key:
            return self._layout_cache

        W, H = self.W, self.H
        pad = int(self.content_margin)
        section = max(10, min(16, pad))
        usable = W - pad * 2
        speed_gap = 8
        cap_w = (usable - speed_gap) // 2

        # 顺序堆叠：每张卡一个 box，背景板矩形同步生成
        boxes: dict = {c: None for c in CARD_IDS}
        rects: List[tuple] = []
        y = pad
        for cid in order:
            h = hs[cid]
            boxes[cid] = (pad, y, usable, h)
            if cid == "speed":
                # 网速是左右两张独立小卡，中间留缝
                rects.append((pad, y, cap_w, h, CARD_RADIUS[cid]))
                rects.append((pad + cap_w + speed_gap, y, cap_w, h, CARD_RADIUS[cid]))
            else:
                rects.append((pad, y, usable, h, CARD_RADIUS[cid]))
            y += h + section

        lay = {
            "_key": key,
            "W": W, "H": H, "pad": pad, "section": section, "usable": usable,
            "order": order, "cards": rects, "boxes": boxes,
            "speed_cap_w": cap_w, "speed_gap": speed_gap,
            "row_h": ROW_H, "head_h": CARD_HEAD_H,
        }
        # 兼容旧取法 lay["hw"] / lay["chart"] / lay["traffic"]
        for cid in CARD_IDS:
            lay[cid] = boxes[cid]

        if boxes["hw"]:
            hx, hy, hw_, hh = boxes["hw"]
            inner_l, inner_r = hx + 8, hx + hw_ - 8
            col = (inner_r - inner_l) / 3.0
            # 每列至少留 10px 间隔；宽布局放大，紧凑布局同步缩小。
            ring_d = max(60, min(86, int(round(col - 10))))
            # 圆环、名称和副值作为一个整体垂直居中；小环不会贴在卡片顶部。
            ring_top = hy + max(12, int(round((hh - (ring_d + 35)) / 2.0)))
            ring_cy = ring_top + ring_d / 2.0
            card_cx = hx + hw_ / 2.0
            ring_centers = [
                int(round(card_cx - col)),
                int(round(card_cx)),
                int(round(card_cx + col)),
            ]
            ring_bottom = ring_cy + ring_d / 2.0
            lay.update({
                "ring_cy": ring_cy,
                "ring_centers": ring_centers,
                "ring_d": ring_d,
                "ring_thickness": max(5, min(8, int(round(ring_d * 0.09)))),
                "ring_label_y": int(round(ring_bottom + 12)),
                "ring_meta_y": int(round(ring_bottom + 29)),
            })
        if boxes["speed"]:
            sx, sy, _sw, sh = boxes["speed"]
            lay.update({"speed_y": sy, "speed_h": sh,
                        "speed_xs": (sx, sx + cap_w + speed_gap)})
        if boxes["chart"]:
            cx, cy, cw, ch = boxes["chart"]
            cp = 10
            lay["chart_inner"] = (cx + cp, cy + cp, cw - cp * 2, ch - cp * 2)

        self._layout_cache = lay
        return lay

    # ------------------------------------------------------------ 绘制
    def _base_image(self, lay: dict) -> Image.Image:
        """外壳 + 卡片底板（很少变化，缓存）。"""
        key = (
            lay["_key"], self.corner_radius, self.c["bg"], self.c["card"],
            self.shell_opacity, self.card_opacity,
        )
        if self._base_img is not None and self._base_key == key:
            return self._base_img
        img = compose_shell_and_cards(
            lay["W"], lay["H"], self.corner_radius,
            self.c["bg"], self.shell_opacity / 100.0,
            self.c["card"], self.card_opacity / 100.0,
            lay["cards"], scale=4,
        )
        self._base_img = img
        self._base_key = key
        return img

    def _ring_image(
        self, pct: Optional[float], accent: str, d: int, thickness: int
    ) -> Image.Image:
        q = -1 if pct is None else int(round(max(0.0, min(100.0, float(pct)))))
        arc = ring_arc_color(pct, accent)
        text = "—" if pct is None else str(q)
        suffix = None if pct is None else "%"
        key = (q, arc, self.c["elev"], self.c["text"], d, thickness)
        img = self._ring_cache.get(key)
        if img is None:
            if len(self._ring_cache) > 96:
                self._ring_cache.clear()
            img = render_ring(
                None if pct is None else float(q),
                arc,
                self.c["elev"],
                diameter=d,
                thickness=thickness,
                scale=6,
                center_text=text,
                center_suffix=suffix,
                center_color=self.c["text"],
                text_size=max(20, int(round(d * 0.36))),
                suffix_size=max(8, int(round(d * 0.14))),
                halo=min(1.0, self.halo + 0.2),
            )
            self._ring_cache[key] = img
        return img

    # ---------- 通用小构件（disk / proc / sys 三张卡共用） ----------
    CARD_PAD_X = 14

    def _ellipsize(self, text: str, size: int, max_w: int, bold: bool = False) -> str:
        """按像素宽裁字符串，超出部分用省略号 —— 中英文混排也不会算歪。"""
        text = str(text)
        if max_w <= 0:
            return ""
        if text_width(text, size, bold) <= max_w:
            return text
        while text and text_width(text + "…", size, bold) > max_w:
            text = text[:-1]
        return (text + "…") if text else ""

    def _draw_card_head(self, img, box, title: str, right: str) -> None:
        x, y, w, _h = box
        p = self.CARD_PAD_X
        draw_text(img, (x + p, y + 15), title, size=12, color=self.c["sub"],
                  anchor="lm", halo=self.halo)
        if right:
            draw_text(img, (x + w - p, y + 15), right, size=11, bold=True,
                      color=self.c["text"], anchor="rm", halo=self.halo)

    def _draw_bar_row(self, img, x: int, y: int, w: int, label: str,
                      pct: float, right: str, color: str,
                      label_w: int = 30, right_w: int = 66) -> None:
        """一行：左标签 · 中进度条 · 右数值。disk / sys 都用它。"""
        p = self.CARD_PAD_X
        draw_text(img, (x + p, y), label, size=10, color=self.c["sub"],
                  anchor="lm", halo=self.halo)
        bar_x = x + p + label_w
        bar_w = w - p * 2 - label_w - right_w
        if bar_w > 8:
            composite_at(
                img,
                render_progress_bar(pct, bar_w, 7, fill=color, track=self.c["elev"]),
                bar_x, y - 3,
            )
        draw_text(img, (x + w - p, y), right, size=10, bold=True,
                  color=self.c["text"], anchor="rm", halo=self.halo)

    def _usage_color(self, pct: float) -> str:
        """占用越高越警示，和流量条一个语义。"""
        if pct >= 90:
            return self.style.get("traffic_red", "#FF453A")
        if pct >= 70:
            return self.style.get("traffic_yellow", "#FFD60A")
        return self.style.get("traffic_green", "#30D158")

    # ---------- 各卡片 ----------
    WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

    def _draw_clock(self, img: Image.Image, lay: dict) -> None:
        """左侧高瘦粗圆的时间，右侧分行显示英文星期与日期。"""
        box = lay["clock"]
        if box is None:
            return
        x, y, w, h = box
        p = self.CARD_PAD_X
        now = datetime.now()

        # 时间采用独立高分辨率字形：Bahnschrift + 统一外扩 + 纵向拉伸。
        # 图像按可见包围盒定位，避免 Pillow 字体上行/下行空白影响对齐。
        time_img = render_clock_time(
            now.strftime("%H:%M"), size=43, color=self.c["text"],
            x_scale=0.92, y_scale=1.22,
            stroke=float(self.cfg.get("clock", {}).get("stroke", 1.4)),
            tracking=float(self.cfg.get("clock", {}).get("tracking", 2.0)),
            scale=6,
        )
        composite_at(
            img, time_img,
            x + p, int(round(y + (h - time_img.height) / 2)),
        )

        # 右侧固定为独立两行：星期在上、日期在下；共享同一右边界。
        right_x = x + w - p
        weekday_y = y + 23
        date_y = y + 53
        draw_text(img, (right_x, weekday_y), self.WEEKDAYS[now.weekday()],
                  size=15, bold=True, color=self.c["down"], anchor="rm", halo=self.halo)
        draw_text(img, (right_x, date_y),
                  f"{now.year}年{now.month}月{now.day}日", size=11,
                  color=self.c["sub"], anchor="rm", halo=self.halo)

    def _draw_hw(self, img: Image.Image, lay: dict) -> None:
        s = self._sample
        d = lay["ring_d"]
        thickness = lay["ring_thickness"]
        cy = lay["ring_cy"]
        rings = (
            ("CPU", None if s is None else s.cpu, self.c["cpu"],
             "—" if s is None else format_temp(s.cpu_temp)),
            ("MEM", None if s is None else s.mem, self.c["mem"],
             "—" if s is None else f"{s.mem_used_gb:.1f}G"),
            ("GPU", None if s is None else s.gpu, self.c["gpu"],
             "—" if s is None else format_temp(s.gpu_temp)),
        )
        for cx, (label, pct, accent, meta) in zip(lay["ring_centers"], rings):
            composite_at(
                img, self._ring_image(pct, accent, d, thickness),
                int(round(cx - d / 2.0)), int(round(cy - d / 2.0)),
            )
            draw_text(
                img, (cx, lay["ring_label_y"]), label, size=11, bold=True,
                color=self.c["sub"], anchor="mm", halo=self.halo,
            )
            draw_text(
                img, (cx, lay["ring_meta_y"]), meta, size=12, bold=True,
                color=accent, anchor="mm", halo=self.halo,
            )

    def _draw_speed(self, img: Image.Image, lay: dict) -> None:
        s = self._sample
        up = self._ups[-1] if self._ups else (s.net_up_bps if s else 0.0)
        down = self._downs[-1] if self._downs else (s.net_down_bps if s else 0.0)
        y = lay["speed_y"]
        h = lay["speed_h"]
        cap_w = lay["speed_cap_w"]
        badge = max(16, int(h * 0.44))
        halves = (
            (lay["speed_xs"][0], self.c["up"], "上传", format_speed(up), "up"),
            (lay["speed_xs"][1], self.c["down"], "下载", format_speed(down), "down"),
        )
        for x, accent, label, value, direction in halves:
            composite_at(
                img, render_arrow_badge(direction, accent, badge, 8),
                x + 10, y + (h - badge) // 2,
            )
            tx = x + 10 + badge + 8
            draw_text(
                img, (tx, y + 7), label, size=11, color=self.c["sub"],
                anchor="la", halo=self.halo,
            )
            draw_text(
                img, (tx, y + 21), value, size=15, bold=True, color=accent,
                anchor="la", halo=self.halo,
            )
            bar_w = max(8, cap_w - 24)
            composite_at(img, render_pill(bar_w, 2, accent, 235), x + 12, y + h - 9)

    def _draw_chart(self, img: Image.Image, lay: dict) -> None:
        cx, cy, cw, ch = lay["chart"]
        ix, iy, iw, ih = lay["chart_inner"]
        if iw < 8 or ih < 8:
            return

        for i in (1, 2):
            draw_dashed_hline(
                img, ix, ix + iw, iy + int(ih * i / 3.0), self.c["sep"], alpha=135
            )

        ups = list(self._ups)[-self.history_n:]
        downs = list(self._downs)[-self.history_n:]
        peak = 1.0
        if ups:
            peak = max(peak, max(ups))
        if downs:
            peak = max(peak, max(downs))
        scaled_peak = peak * 1.08

        def pts(series: Sequence[float]) -> List[Tuple[float, float]]:
            data = list(series)
            if len(data) < 2:
                return []
            m = len(data)
            out = []
            for i, v in enumerate(data):
                px = i * iw / (m - 1)
                ratio = max(0.0, min(1.0, float(v) / scaled_peak))
                out.append((px, ih - ratio * ih))
            return out

        series = [
            {"points": pts(downs), "color": self.c["down"], "width": 1.8, "fill_alpha": 62},
            {"points": pts(ups), "color": self.c["up"], "width": 1.8, "fill_alpha": 62},
        ]
        if any(s["points"] for s in series):
            composite_at(img, render_series(iw, ih, series, scale=3), ix, iy)

        draw_text(
            img, (cx + cw - 12, cy + 12), f"峰值 {format_speed(peak)}",
            size=10, color=blend_hex(self.c["down"], "#FFFFFF", 0.72),
            anchor="ra", halo=self.halo,
        )

    def _draw_disk(self, img: Image.Image, lay: dict) -> None:
        box = lay["disk"]
        if box is None:
            return
        x, y, w, _h = box
        s = self._sample
        rd = format_speed(s.disk_read_bps) if s else "—"
        wr = format_speed(s.disk_write_bps) if s else "—"
        self._draw_card_head(img, box, "磁盘", "")
        # 读/写并排放右侧，各自带汉字前缀——只靠颜色区分方向是认不出来的
        p = self.CARD_PAD_X
        wr_txt, rd_txt = f"写 {wr}", f"读 {rd}"
        draw_text(img, (x + w - p, y + 15), wr_txt, size=10, bold=True,
                  color=self.c["up"], anchor="rm", halo=self.halo)
        draw_text(img, (x + w - p - text_width(wr_txt, 10, True) - 10, y + 15),
                  rd_txt, size=10, bold=True, color=self.c["down"], anchor="rm",
                  halo=self.halo)

        row_y = y + CARD_HEAD_H + ROW_H // 2 - 2
        for part in self._parts[:3]:
            self._draw_bar_row(
                img, x, row_y, w, part.label, part.percent,
                f"{part.used_gb:.0f}/{part.total_gb:.0f}G",
                self._usage_color(part.percent),
                label_w=28, right_w=70,
            )
            row_y += ROW_H
        if not self._parts:
            draw_text(img, (x + w // 2, row_y), "无可用分区", size=10,
                      color=self.c["muted"], anchor="mm", halo=self.halo)

    def _draw_proc(self, img: Image.Image, lay: dict) -> None:
        box = lay["proc"]
        if box is None:
            return
        x, y, w, _h = box
        p = self.CARD_PAD_X
        self._draw_card_head(img, box, "进程 TOP", "CPU")

        rows = list(self._top_procs)[: self.PROC_ROWS]
        row_y = y + CARD_HEAD_H + ROW_H // 2 - 2
        if not rows:
            draw_text(img, (x + w // 2, row_y), "采样中…", size=10,
                      color=self.c["muted"], anchor="mm", halo=self.halo)
            return
        name_max = w - p * 2 - 108
        for r in rows:
            draw_text(img, (x + p, row_y), self._ellipsize(r.name, 11, name_max),
                      size=11, color=self.c["text"], anchor="lm", halo=self.halo)
            draw_text(img, (x + w - p - 46, row_y), f"{r.mem_mb:.0f}M", size=9,
                      color=self.c["muted"], anchor="rm", halo=self.halo)
            draw_text(img, (x + w - p, row_y), f"{r.cpu:.1f}%", size=11, bold=True,
                      color=ring_arc_color(r.cpu, self.c["cpu"]), anchor="rm",
                      halo=self.halo)
            row_y += ROW_H

    def _draw_sys(self, img: Image.Image, lay: dict) -> None:
        box = lay["sys"]
        if box is None:
            return
        x, y, w, _h = box
        self._draw_card_head(img, box, "系统", format_uptime(uptime_seconds()))

        row_y = y + CARD_HEAD_H + ROW_H // 2 - 2
        used, total = gpu_memory()
        if used is not None and total:
            pct = max(0.0, min(100.0, used / total * 100.0))
            self._draw_bar_row(img, x, row_y, w, "显存", pct,
                               f"{used:.1f}/{total:.1f}G", self._usage_color(pct))
        else:
            self._draw_bar_row(img, x, row_y, w, "显存", 0.0, "—", self.c["elev"])
        row_y += ROW_H

        b = self._battery
        if b is not None:
            if b.plugged:
                right = f"{b.percent:.0f}% 充电中"
            elif b.secs_left:
                right = f"{b.percent:.0f}% 剩{b.secs_left // 3600}时"
            else:
                right = f"{b.percent:.0f}%"
            color = (self.style.get("traffic_green", "#30D158") if b.plugged
                     else self._usage_color(100.0 - b.percent))
            self._draw_bar_row(img, x, row_y, w, "电池", b.percent, right, color)

    def _draw_traffic(self, img: Image.Image, lay: dict) -> None:
        box = lay["traffic"]
        if box is None:
            return
        x, y, w, h = box
        pad_x = 14
        info = self._traffic_info
        bar_x, bar_y, bar_w, bar_h = x + pad_x, y + 36, w - pad_x * 2, 9
        label_y = bar_y + bar_h + 12
        header_y = y + 16

        if info is None or info.status in ("pending", "disabled"):
            title, pct_txt, used, total, hint = "—", "—", "—", "—", ""
            pct, color = 0.0, self.c["elev"]
        elif info.status == "unconfigured":
            # 与「获取数据失败」区分开：这是没填地址，不是网络/接口问题
            title, pct_txt, used, total = "—", "—", "—", "—"
            hint = "未配置接口地址"
            pct, color = 0.0, self.c["elev"]
        elif info.status == "failed" or not info.ok:
            title, pct_txt, used, total = "—", "—", "—", "—"
            hint = "获取数据失败"
            pct, color = 0.0, self.c["elev"]
        else:
            pct = float(info.percent)
            color = traffic_bar_color(pct, self.style)
            title = format_reset_days(info.expire)
            pct_txt = f"{pct:.1f}%"
            used = format_bytes(info.used)
            total = format_bytes(info.total)
            hint = "缓存" if info.status == "cached" else ""

        draw_text(img, (x + pad_x, header_y), title, size=12, color=self.c["text"],
                  anchor="lm", halo=self.halo)
        draw_text(img, (x + w - pad_x, header_y), pct_txt, size=13, bold=True,
                  color=self.c["text"], anchor="rm", halo=self.halo)
        composite_at(
            img,
            render_progress_bar(pct, bar_w, bar_h, fill=color, track=self.c["elev"]),
            bar_x, bar_y,
        )
        # 已用/总量用 secondary 而非 tertiary：tertiary 接近中灰，
        # 卡片半透明叠在浅色桌面上时会和底色糊成一片（B1 里就看不清）。
        draw_text(img, (x + pad_x, label_y), used, size=10, color=self.c["sub"],
                  anchor="la", halo=self.halo)
        draw_text(img, (x + w - pad_x, label_y), total, size=10, color=self.c["sub"],
                  anchor="ra", halo=self.halo)
        if hint:
            draw_text(img, (x + w // 2, label_y), hint, size=9, color=self.c["muted"],
                      anchor="ma", halo=self.halo)

    def _compose(self) -> Image.Image:
        lay = self._layout()
        img = self._base_image(lay).copy()
        for cid in lay["order"]:
            fn = getattr(self, f"_draw_{cid}", None)
            if fn is None:
                continue
            try:
                fn(img, lay)
            except Exception as e:
                _log_crash(f"[draw {cid}] {e}\n{traceback.format_exc()}")
        return img

    def _compose_compact(self) -> Image.Image:
        """折叠态：一条圆角胶囊 + CPU/MEM/GPU 摘要。"""
        W, H = self.W, self.COMPACT_H
        img = compose_shell_and_cards(
            W, H, min(self.corner_radius, H // 2),
            self.c["bg"], self.shell_opacity / 100.0,
            self.c["card"], self.card_opacity / 100.0,
            [(6, 6, W - 12, H - 12, min(14, (H - 12) // 2))],
            scale=4,
        )
        s = self._sample
        items = (
            ("CPU", None if s is None else s.cpu, self.c["cpu"]),
            ("MEM", None if s is None else s.mem, self.c["mem"]),
            ("GPU", None if s is None else s.gpu, self.c["gpu"]),
        )
        col = (W - 24) / 3.0
        for i, (label, pct, accent) in enumerate(items):
            cx = 12 + col * (i + 0.5)
            txt = "—" if pct is None else f"{int(round(pct))}%"
            draw_text(img, (cx, H / 2 - 7), label, size=9, color=self.c["sub"],
                      anchor="mm", halo=self.halo)
            draw_text(img, (cx, H / 2 + 7), txt, size=13, bold=True,
                      color=ring_arc_color(pct, accent), anchor="mm", halo=self.halo)
        return img

    # ------------------------------------------------------------ 推送
    def _cur_h(self) -> int:
        return self.COMPACT_H if self._compact else self.H

    def _paint(self) -> None:
        self._paint_after_id = None
        if not self._visible or self._closing or self._auto_hidden:
            return
        try:
            img = self._compose_compact() if self._compact else self._compose()
            update_layered_window(
                self._hwnd, img, x=self._win_x, y=self._win_y,
                constant_alpha=int(round(max(0.45, min(1.0, self.alpha)) * 255)),
            )
        except Exception as e:
            _log_crash(f"[paint] {e}\n{traceback.format_exc()}")

    def _schedule_paint(self, delay: int = 16) -> None:
        """合并同一帧内的多次重绘请求。拖拽期间不重绘（只移动窗口）。"""
        if self._dragging or not self._visible or self._closing:
            return
        if self._paint_after_id is not None:
            return
        try:
            self._paint_after_id = self.root.after(delay, self._paint)
        except Exception:
            self._paint_after_id = None

    def _ensure_hwnd(self) -> int:
        """重新解析 HWND 并确认扩展样式（Tk 重映射窗口后句柄可能变）。"""
        try:
            hwnd = make_overlay_window(self.win.winfo_id())
            if hwnd:
                self._hwnd = hwnd
                set_click_through(self._hwnd, self.click_through)
        except Exception as e:
            _log_crash(f"[ensure_hwnd] {e}")
        return self._hwnd

    def _sync_geometry(self) -> None:
        """把 Tk 的几何认知与真实分层窗对齐（拖拽结束 / 折叠切换时调用）。"""
        try:
            self.win.geometry(f"{self.W}x{self._cur_h()}+{self._win_x}+{self._win_y}")
        except Exception:
            pass

    # ------------------------------------------------------------ 事件
    def _bind(self) -> None:
        w = self.win
        w.bind("<ButtonPress-1>", self._on_press)
        w.bind("<B1-Motion>", self._on_drag)
        w.bind("<ButtonRelease-1>", self._on_release)
        w.bind("<Double-Button-1>", self._on_double)
        w.bind("<Button-3>", self._popup)

        self.menu = tk.Menu(
            self.win, tearoff=0, bg="#1C1C1E", fg="#F5F5F7",
            activebackground="#2C2C2E", activeforeground="#FFFFFF", bd=0,
        )
        self.menu.add_command(label="设置…", command=self.open_settings)
        self.menu.add_command(label="显示/隐藏", command=self._toggle_visible)
        self.menu.add_command(label="置顶切换", command=self._toggle_topmost)
        self.menu.add_command(label="鼠标穿透", command=self._toggle_click_through)
        self.menu.add_command(label="折叠 / 展开", command=self._toggle_compact)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.close)

    def _popup(self, event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self.menu.grab_release()
            except Exception:
                pass

    def _snap(self, x: int, y: int) -> Tuple[int, int]:
        """
        贴边吸附：离屏幕边缘 <= snap_px 就吸上去。

        用 winfo_vroot* 拿的是**整个虚拟桌面**，多显示器时不会把窗口
        硬拽回主屏；吸附判定按窗口所在那块屏的工作区做。
        """
        px = int(self.snap_px)
        if px <= 0:
            return x, y
        w, h = self.W, self._cur_h()
        area = nearest_work_area(x, y, w, h)
        if area is None:
            return x, y
        left, top, right, bottom = area[:4]
        if abs(x - left) <= px:
            x = left
        elif abs((x + w) - right) <= px:
            x = right - w
        if abs(y - top) <= px:
            y = top
        elif abs((y + h) - bottom) <= px:
            y = bottom - h
        return x, y

    def _on_press(self, event) -> None:
        if self.lock_pos:
            return
        rect = window_rect(self._hwnd)
        if rect:
            self._win_x, self._win_y = rect[0], rect[1]
        self._drag_dx = event.x_root - self._win_x
        self._drag_dy = event.y_root - self._win_y
        self._dragging = True
        self._drag_moved = False

    def _on_drag(self, event) -> None:
        # 热路径：只有一次 SetWindowPos，不碰 Tk 几何、不重绘位图。
        if not self._dragging or self.lock_pos:
            return
        x = event.x_root - self._drag_dx
        y = event.y_root - self._drag_dy
        if x == self._win_x and y == self._win_y:
            return
        self._win_x, self._win_y = x, y
        self._drag_moved = True
        move_window(self._hwnd, x, y)

    def _on_release(self, event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        if self._drag_moved:
            sx, sy = self._snap(self._win_x, self._win_y)
            if (sx, sy) != (self._win_x, self._win_y):
                self._win_x, self._win_y = sx, sy
                move_window(self._hwnd, sx, sy)
            self._sync_geometry()
            self._persist_window_state(pos=True)
        self._drag_moved = False

    def _zone_at(self, x: int, y: int) -> Optional[str]:
        """窗口内坐标命中哪张卡片。折叠态不分区；卡片间隙返回 None。"""
        if self._compact:
            return None
        lay = self._layout()
        for name in lay["order"]:
            box = lay["boxes"].get(name)
            if not box:
                continue
            bx, by, bw, bh = box
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return name
        return None

    def _on_double(self, event) -> None:
        """
        双击 → 执行该卡片绑定的动作（设置窗可改，动作清单见 actions.py）。
        卡片之外（网速胶囊 / 间隙 / 折叠态）不响应。

        单窗口 + NOACTIVATE，因此不会有 z-order 翻转导致的整体变暗。
        """
        now = time.monotonic()
        if now - self._last_dbl < 0.35:
            return "break"
        self._last_dbl = now

        zone = self._zone_at(event.x, event.y)
        if zone is None:
            return "break"
        bindings = self.cfg.get("doubleclick", {}) or {}
        action_id = str(bindings.get(zone) or DEFAULT_BINDINGS.get(zone, "none"))
        err = run_action(action_id, self)
        if err:
            _log_crash(f"[dblclick {zone}] {err}")
        return "break"

    def _on_wm_close(self) -> None:
        self._set_visible(False)

    # ------------------------------------------------------------ 状态
    def _persist_window_state(self, *, pos: bool = False) -> None:
        win = dict(self.cfg.get("window", {}))
        win["always_on_top"] = bool(self.always_on_top)
        win["visible"] = bool(self._visible)
        win["corner_radius"] = int(self.corner_radius)
        win["content_margin"] = int(self.content_margin)
        win["shell_opacity"] = int(self.shell_opacity)
        win["card_opacity"] = int(self.card_opacity)
        win["lock_pos"] = bool(self.lock_pos)
        win["click_through"] = bool(self.click_through)
        win["snap_px"] = int(self.snap_px)
        win["hide_on_fullscreen"] = bool(self.hide_on_fullscreen)
        win.pop("bg_transparent", None)
        win["width"] = self.W
        win["height"] = self.H
        if pos:
            _save_json(POS_PATH, {"x": int(self._win_x), "y": int(self._win_y)})
        self.cfg["window"] = win
        _save_json(CONFIG_PATH, self.cfg)

    def _refresh_tray_menu(self) -> None:
        if self._tray_icon is not None:
            try:
                self._tray_icon.update_menu()
            except Exception:
                pass

    def _set_topmost(self, enabled: bool, *, persist: bool = True) -> None:
        self.always_on_top = bool(enabled)
        try:
            self.win.attributes("-topmost", self.always_on_top)
        except tk.TclError:
            pass
        # -topmost 会走 SetWindowPos，重新确认扩展样式并重推位图
        self._ensure_hwnd()
        self._paint()
        if persist:
            self._persist_window_state()
        self._refresh_tray_menu()

    def _toggle_topmost(self) -> None:
        self._set_topmost(not self.always_on_top)

    def _set_click_through(self, enabled: bool, *, persist: bool = True) -> None:
        self.click_through = bool(enabled)
        set_click_through(self._hwnd, self.click_through)
        if persist:
            self._persist_window_state()
        self._refresh_tray_menu()

    def _toggle_click_through(self) -> None:
        self._set_click_through(not self.click_through)

    def _is_click_through(self) -> bool:
        return bool(self.click_through)

    def _is_topmost(self) -> bool:
        return bool(self.always_on_top)

    def _set_visible(self, visible: bool, *, force_topmost: bool = False,
                     persist: bool = True) -> None:
        self._visible = bool(visible)
        self._auto_hidden = False  # 手动显示/隐藏优先于全屏自动隐藏
        if self._visible:
            if force_topmost:
                self.always_on_top = True
            try:
                self.win.deiconify()
                self.win.attributes("-topmost", self.always_on_top)
            except tk.TclError:
                pass
            # withdraw 后分层位图会丢，且 Tk 重映射可能带回默认扩展样式
            self._ensure_hwnd()
            self._sync_geometry()
            self._paint()
        else:
            try:
                self.win.withdraw()
            except tk.TclError:
                pass
        if persist:
            self._persist_window_state()
        self._refresh_tray_menu()

    def _update_auto_hide(self) -> None:
        """
        前台是全屏应用时把悬浮窗收起来（打游戏/看片不挡道），退出全屏再放出来。

        用独立的 _auto_hidden 标记，**不碰** _visible —— 否则会把用户在托盘里
        选的显示/隐藏状态写坏，下次开机就不对了。
        """
        if not self._visible:
            return
        want_hidden = self.hide_on_fullscreen and _foreground_is_fullscreen()
        if want_hidden == self._auto_hidden:
            return
        self._auto_hidden = want_hidden
        try:
            if want_hidden:
                self.win.withdraw()
            else:
                self.win.deiconify()
                self.win.attributes("-topmost", self.always_on_top)
                self._ensure_hwnd()
                self._sync_geometry()
                self._paint()
        except tk.TclError:
            pass

    def _toggle_visible(self) -> None:
        self._set_visible(not self._visible)

    def _hotkey_toggle_visible(self) -> None:
        def run():
            if self._visible:
                self._set_visible(False)
            else:
                self._set_visible(True, force_topmost=True)

        try:
            self.root.after(0, run)
        except Exception:
            run()

    def _is_visible(self) -> bool:
        return bool(self._visible)

    def _toggle_compact(self, event=None) -> None:
        self._compact = not self._compact
        self._sync_geometry()
        self._paint()

    # ------------------------------------------------------------ 设置 / 托盘
    def open_settings(self) -> None:
        if self._settings is None:
            self._settings = SettingsWindow(
                self.root,
                self.cfg,
                self.apply_settings,
                on_theme_live=self.apply_theme_live,
                on_opacity_live=self.apply_opacity_live,
                on_halo_live=self.apply_halo_live,
                on_cancel=self.cancel_settings_preview,
                diagnostics=self.get_diagnostics,
                base_h=self.BASE_H,
                traffic_h=self.TRAFFIC_H,
                traffic_gap=self.TRAFFIC_GAP,
            )
        else:
            self._settings.cfg = self.cfg
            self._settings.on_theme_live = self.apply_theme_live
            self._settings.on_opacity_live = self.apply_opacity_live
            self._settings.on_halo_live = self.apply_halo_live
            self._settings.on_cancel = self.cancel_settings_preview
            self._settings.diagnostics = self.get_diagnostics
        self._settings.open()

    def apply_halo_live(self, halo_pct: int) -> None:
        """设置里拖动光晕滑块：立即重绘。"""
        self.halo = max(0.0, min(1.0, int(halo_pct) / 100.0))
        # 圆环中心的百分比文字连同光晕一起烘进了缓存位图，必须失效
        self._ring_cache.clear()
        self._paint()

    def apply_opacity_live(self, shell_op: int, card_op: int) -> None:
        self.shell_opacity = max(0, min(100, int(shell_op)))
        self.card_opacity = max(10, min(100, int(card_op)))
        self._base_img = None
        self._paint()

    def apply_theme_live(self, theme_id: str) -> None:
        from themes import style_for as _style_for

        shell_opacity = self.shell_opacity
        card_opacity = self.card_opacity
        halo = self.halo
        stored_cfg = self.cfg
        preview_cfg = dict(stored_cfg)
        preview_cfg["theme_id"] = theme_id
        preview_cfg["style"] = _style_for(theme_id)
        self.cfg = preview_cfg
        self._apply_style_from_cfg()
        self.cfg = stored_cfg
        self.shell_opacity = shell_opacity
        self.card_opacity = card_opacity
        self.halo = halo
        self._base_img = None
        self._paint()
        if self._tray_icon is not None:
            try:
                self._tray_icon.icon = self._make_tray_image()
            except Exception:
                pass
        self._refresh_tray_menu()

    def cancel_settings_preview(self) -> None:
        self._apply_style_from_cfg()
        self._ensure_hwnd()
        self._paint()
        if self._tray_icon is not None:
            try:
                self._tray_icon.icon = self._make_tray_image()
            except Exception:
                pass

    def get_diagnostics(self) -> dict:
        snap = self.metrics_worker.get()
        return {
            "config_path": str(CONFIG_PATH),
            "sample_ms": snap.duration_ms,
            "sample_error": bool(snap.error),
            "hotkey_error": getattr(self, "_last_hotkey_error", ""),
            "gpu": bool(snap.sample and snap.sample.gpu is not None),
            "cpu_temp": bool(snap.sample and snap.sample.cpu_temp is not None),
            "traffic": getattr(self._traffic_info, "status", "pending"),
        }

    def apply_settings(self, new_cfg: dict) -> bool:
        normalized = self._normalize_cfg(new_cfg)
        if not _save_json(CONFIG_PATH, normalized):
            return False
        self.cfg = normalized
        warnings = []
        try:
            set_autostart(bool(self.cfg.get("autostart", False)))
        except Exception as e:
            _log_crash(f"[autostart] {e}")
            warnings.append(f"开机自启设置失败：{e}")

        self._apply_style_from_cfg()
        self.interval = max(500, int(self.cfg.get("update_interval_ms", 1000)))
        self._sync_metrics_worker()
        self._sync_traffic_collector()
        self.alerts.reset()
        self._compute_size()
        self._compact = False
        try:
            self.win.attributes("-topmost", self.always_on_top)
        except tk.TclError:
            pass
        self._ensure_hwnd()
        self._sync_geometry()
        self._set_visible(self._visible, persist=True)
        if self._visible:
            self._paint()
        hotkey_error = self._register_hotkey()
        if hotkey_error:
            warnings.append(f"全局快捷键注册失败：{hotkey_error}")
        self._refresh_tray_menu()
        if warnings:
            try:
                messagebox.showwarning("部分设置未生效", "\n".join(warnings), parent=self.win)
            except Exception:
                pass
        return True

    def _register_hotkey(self) -> Optional[str]:
        hk = self.cfg.get("hotkey", {})
        if not bool(hk.get("enabled", True)):
            self._hotkeys.clear()
            self._last_hotkey_error = ""
            return None
        combo = str(hk.get("toggle_visible", "ctrl+shift+m") or "").strip()
        err = self._hotkeys.set_hotkey(combo, self._hotkey_toggle_visible)
        self._last_hotkey_error = err or ""
        if err:
            _log_crash(f"[hotkey] {err} combo={combo}")
        return err

    def _make_tray_image(self):
        from PIL import ImageDraw

        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([4, 4, 60, 60], fill=(28, 28, 30, 240))
        d.ellipse([14, 22, 28, 36], fill=_hex_to_rgb(self.c["cpu"]) + (255,))
        d.ellipse([26, 22, 40, 36], fill=_hex_to_rgb(self.c["mem"]) + (255,))
        d.ellipse([38, 22, 52, 36], fill=_hex_to_rgb(self.c["gpu"]) + (255,))
        return img

    def _start_tray(self) -> None:
        try:
            import pystray
            from pystray import MenuItem as Item
        except ImportError:
            _log_crash("[tray] pystray not installed")
            return

        def ui(fn):
            return lambda icon=None, item=None: self.root.after(0, fn)

        menu = pystray.Menu(
            Item("设置…", ui(self.open_settings), default=True),
            Item("显示/隐藏", ui(self._toggle_visible),
                 checked=lambda item: self._is_visible()),
            Item("置顶显示", ui(self._toggle_topmost),
                 checked=lambda item: self._is_topmost()),
            Item("鼠标穿透", ui(self._toggle_click_through),
                 checked=lambda item: self._is_click_through()),
            pystray.Menu.SEPARATOR,
            Item("退出", ui(self.close)),
        )
        self._tray_icon = pystray.Icon(
            "GlassMonitor", self._make_tray_image(), "Glass Monitor", menu
        )

        def _run():
            try:
                self._tray_icon.run()
            except Exception as e:
                _log_crash(f"[tray.run] {e}")

        threading.Thread(target=_run, name="tray", daemon=True).start()

    def _evaluate_alerts(self, metrics) -> None:
        fresh_metrics = (
            metrics.sample is not None and metrics.sampled_at > self._last_metrics_t
        )
        if fresh_metrics:
            self._last_metrics_t = metrics.sampled_at
            if metrics.error:
                _log_crash(f"[metrics.worker] {metrics.error}")
        sample = metrics.sample
        traffic_value = None
        if self._traffic_info and self._traffic_info.status in ("ok", "cached"):
            traffic_value = self._traffic_info.percent
        values = {
            "cpu_temp": sample.cpu_temp if sample is not None else None,
            "gpu_temp": sample.gpu_temp if sample is not None else None,
            "memory": sample.mem if sample is not None else None,
            "disk": max((part.percent for part in metrics.parts), default=None),
            "traffic": traffic_value,
        }
        for message in self.alerts.evaluate(values, self.cfg.get("alerts", {})):
            try:
                if self._tray_icon is not None:
                    self._tray_icon.notify(message, "Glass Monitor 告警")
            except Exception as exc:
                _log_crash(f"[alert.notify] {exc}")

    # ------------------------------------------------------------ tick
    def _tick(self) -> None:
        self._tick_after_id = None
        if self._closing:
            return
        try:
            metrics = self.metrics_worker.get()
            if metrics.sample is not None:
                self._sample = metrics.sample
                self._ups, self._downs = metrics.ups, metrics.downs
                self._parts = metrics.parts
                self._top_procs = metrics.top_procs
                self._battery = metrics.battery_info
            self._traffic_info = self.traffic.get()

            # 分区插拔 / 电池出现消失会改变卡片行数 → 窗口高度要跟着变
            heights = self._card_heights()
            if heights != self._last_heights:
                self._last_heights = heights
                self._compute_size()
                self._sync_geometry()

            self._update_auto_hide()
            self._evaluate_alerts(metrics)
            self._tick_errors = 0
            self._schedule_paint(0)
        except Exception:
            self._tick_errors += 1
            _log_crash(f"[tick] {traceback.format_exc()}")
            if self._tick_errors >= 20:
                self.close()
                return
        try:
            self._tick_after_id = self.root.after(self.interval, self._tick)
        except tk.TclError:
            pass

    def close(self) -> None:
        try:
            self._persist_window_state(pos=True)
        except Exception:
            pass
        # 先撤掉挂起的 after，否则销毁窗口后 Tcl 会报 invalid command name
        for attr in ("_paint_after_id", "_opacity_after_id", "_halo_after_id",
                     "_tick_after_id"):
            aid = getattr(self, attr, None)
            if aid is not None:
                try:
                    self.root.after_cancel(aid)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._closing = True
        self.metrics_worker.stop()
        for fn in (self._hotkeys.clear, self.traffic.stop):
            try:
                fn()
            except Exception:
                pass
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        shutdown_nvml()
        try:
            release_surface()
        except Exception:
            pass
        for w in (self.win, self.root):
            try:
                w.destroy()
            except Exception:
                pass

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    try:
        GlassMonitorApp().run()
    except Exception:
        _log_crash(f"[main] {traceback.format_exc()}")
        raise


if __name__ == "__main__":
    main()
