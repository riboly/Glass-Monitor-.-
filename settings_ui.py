"""
程序设置窗口（由托盘右键打开）。

- 右侧滚动条，底部按钮栏固定可见
- 配色下拉：深字浅底，清晰可读
- 选择配色后立即应用到悬浮窗并写入配置
"""

from __future__ import annotations

import copy
import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Optional
from urllib.parse import urlparse

from actions import (
    DEFAULT_BINDINGS,
    LABELS as ACTION_LABELS,
    ZONES,
    id_for_label,
    label_for as action_label_for,
)
from cards_meta import CARD_NAMES, DEFAULT_CARDS, normalize_card_order
from hotkey_mgr import HotkeyManager
from themes import THEMES, theme_choices


class SettingsWindow:
    def __init__(
        self,
        master: tk.Tk,
        cfg: dict,
        on_save: Callable[[dict], None],
        *,
        on_theme_live: Optional[Callable[[str], None]] = None,
        on_opacity_live: Optional[Callable[[int, int], None]] = None,
        on_halo_live: Optional[Callable[[int], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
        diagnostics: Optional[Callable[[], dict]] = None,
        base_h: int = 440,
        traffic_h: int = 86,
        traffic_gap: int = 12,
    ):
        self.master = master
        self.cfg = cfg
        self.on_save = on_save
        self.on_theme_live = on_theme_live
        self.on_opacity_live = on_opacity_live
        self.on_halo_live = on_halo_live
        self.on_cancel = on_cancel
        self.diagnostics = diagnostics
        self.base_h = base_h
        self.traffic_h = traffic_h
        self.traffic_gap = traffic_gap
        self.win: Optional[tk.Toplevel] = None
        self._capturing = False

    def open(self) -> None:
        if self.win is not None and self.win.winfo_exists():
            self.win.lift()
            self.win.focus_force()
            return

        self._source_cfg = self.cfg
        self.cfg = copy.deepcopy(self._source_cfg)
        w = tk.Toplevel(self.master)
        self.win = w
        w.title("Glass Monitor 设置")
        w.geometry("460x560")
        w.minsize(420, 420)
        w.resizable(True, True)
        w.configure(bg="#1C1C1E")
        w.attributes("-topmost", True)

        style = ttk.Style(w)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # 通用深色
        style.configure("TFrame", background="#1C1C1E")
        style.configure("Card.TFrame", background="#1C1C1E")
        style.configure(
            "TLabel", background="#1C1C1E", foreground="#F5F5F7", font=("Segoe UI", 10)
        )
        style.configure(
            "Title.TLabel",
            background="#1C1C1E",
            foreground="#F5F5F7",
            font=("Segoe UI Semibold", 14),
        )
        style.configure(
            "Hint.TLabel",
            background="#1C1C1E",
            foreground="#8E8E93",
            font=("Segoe UI", 9),
        )
        style.configure(
            "TCheckbutton",
            background="#1C1C1E",
            foreground="#F5F5F7",
            font=("Segoe UI", 10),
        )
        style.map(
            "TCheckbutton",
            background=[("active", "#1C1C1E")],
            foreground=[("active", "#F5F5F7")],
        )
        style.configure("TButton", font=("Segoe UI", 10), padding=6)
        style.configure("CardOrder.TButton", font=("Segoe UI Symbol", 10), padding=(4, 2))
        # 配色下拉：浅底深字，清晰可读
        style.configure(
            "Theme.TCombobox",
            fieldbackground="#FFFFFF",
            background="#FFFFFF",
            foreground="#1C1C1E",
            arrowcolor="#1C1C1E",
            bordercolor="#3A3A3C",
            lightcolor="#FFFFFF",
            darkcolor="#FFFFFF",
            selectbackground="#0A84FF",
            selectforeground="#FFFFFF",
            padding=4,
        )
        style.map(
            "Theme.TCombobox",
            fieldbackground=[("readonly", "#FFFFFF"), ("disabled", "#E5E5EA")],
            foreground=[("readonly", "#1C1C1E"), ("disabled", "#8E8E93")],
            selectbackground=[("readonly", "#0A84FF")],
            selectforeground=[("readonly", "#FFFFFF")],
        )
        style.configure(
            "TEntry",
            fieldbackground="#FFFFFF",
            foreground="#1C1C1E",
            insertcolor="#1C1C1E",
        )
        style.configure(
            "TSpinbox",
            fieldbackground="#FFFFFF",
            foreground="#1C1C1E",
            arrowcolor="#1C1C1E",
        )
        style.configure(
            "Horizontal.TScale",
            background="#1C1C1E",
            troughcolor="#2C2C2E",
            bordercolor="#3A3A3C",
            lightcolor="#3A3A3C",
            darkcolor="#2C2C2E",
            sliderthickness=16,
        )

        # 外层：上方可滚动 + 底部固定按钮
        outer = ttk.Frame(w)
        outer.pack(fill=tk.BOTH, expand=True)

        # —— 滚动区域 ——
        scroll_wrap = ttk.Frame(outer)
        scroll_wrap.pack(fill=tk.BOTH, expand=True, padx=(8, 0), pady=(8, 0))

        self._canvas = tk.Canvas(
            scroll_wrap,
            bg="#1C1C1E",
            highlightthickness=0,
            bd=0,
        )
        self._vsb = ttk.Scrollbar(
            scroll_wrap, orient=tk.VERTICAL, command=self._canvas.yview
        )
        self._canvas.configure(yscrollcommand=self._vsb.set)
        self._vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._inner = ttk.Frame(self._canvas)
        self._inner_id = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")

        def _on_inner_configure(_evt=None):
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))

        def _on_canvas_configure(evt):
            self._canvas.itemconfigure(self._inner_id, width=evt.width)

        self._inner.bind("<Configure>", _on_inner_configure)
        self._canvas.bind("<Configure>", _on_canvas_configure)

        # 鼠标滚轮（Windows）：绑在**设置窗顶层**上，不要用 bind_all。
        #
        # bindtags 顺序是 widget → class → toplevel → all。bind_all 挂在 all 上，
        # 于是每个自带滚轮行为的控件（TCombobox / TSpinbox / Listbox 都有 class
        # 级 <MouseWheel>）都会先执行自己的动作、再被外层页面滚一次；配色下拉
        # 弹出时更明显——列表滚了，页面也滚，下拉框被滚走，看起来就是「滚的是
        # 外面的滚动条」。
        #
        # 绑在顶层则只覆盖本窗口的后代；配色下拉的 popdown 是**独立顶层**，
        # 不在这条链上，滚轮自然留给它自己的列表。
        w.bind("<MouseWheel>", self._wheel)

        root_f = self._inner
        pad_x = 20

        ttk.Label(root_f, text="设置", style="Title.TLabel").pack(
            anchor="w", padx=pad_x, pady=(12, 4)
        )
        ttk.Label(
            root_f,
            text="配色、透明度和光晕即时预览；保存后统一写入配置",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=pad_x)

        # —— 配色 ——
        ttk.Label(root_f, text="配色方案").pack(anchor="w", padx=pad_x, pady=(16, 4))
        choices = theme_choices()
        self._theme_ids = [c[0] for c in choices]
        self._theme_labels = [c[1] for c in choices]
        self.var_theme = tk.StringVar()
        cur = self.cfg.get("theme_id", "midnight")
        if cur not in self._theme_ids:
            cur = "midnight"
        self.var_theme.set(self._theme_labels[self._theme_ids.index(cur)])

        self.cmb_theme = ttk.Combobox(
            root_f,
            textvariable=self.var_theme,
            values=self._theme_labels,
            state="readonly",
            width=42,
            style="Theme.TCombobox",
        )
        self.cmb_theme.pack(anchor="w", padx=pad_x, fill=tk.X)
        # 下拉列表也用深字
        try:
            self.win.option_add("*TCombobox*Listbox.background", "#FFFFFF")
            self.win.option_add("*TCombobox*Listbox.foreground", "#1C1C1E")
            self.win.option_add("*TCombobox*Listbox.selectBackground", "#0A84FF")
            self.win.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
        except Exception:
            pass
        self.cmb_theme.bind("<<ComboboxSelected>>", self._on_theme_selected)
        # 折叠状态下滚轮滚页面，不要顺手把配色切走（切配色会实时重绘 + 写盘）
        self._claim_wheel(self.cmb_theme)

        self.preview = tk.Canvas(
            root_f, width=380, height=40, bg="#1C1C1E", highlightthickness=0
        )
        self.preview.pack(anchor="w", padx=pad_x, pady=10)
        self._draw_preview(cur)

        # —— 窗口外观 ——
        win_cfg = self.cfg.get("window", {})
        ttk.Label(root_f, text="布局预设").pack(
            anchor="w", padx=pad_x, pady=(10, 4)
        )
        self._layout_ids = ["compact", "standard", "spacious", "custom"]
        self._layout_labels = ["紧凑", "标准", "宽松", "自定义"]
        layout_id = str(win_cfg.get("layout_preset", "standard"))
        if layout_id not in self._layout_ids:
            layout_id = "custom"
        self.var_layout = tk.StringVar(
            value=self._layout_labels[self._layout_ids.index(layout_id)]
        )
        cmb_layout = ttk.Combobox(
            root_f, textvariable=self.var_layout, values=self._layout_labels,
            state="readonly", style="Theme.TCombobox", width=16,
        )
        cmb_layout.pack(anchor="w", padx=pad_x)
        cmb_layout.bind("<<ComboboxSelected>>", self._on_layout_selected)
        self._claim_wheel(cmb_layout)

        row_width = ttk.Frame(root_f)
        row_width.pack(anchor="w", padx=pad_x, pady=(8, 2), fill=tk.X)
        ttk.Label(row_width, text="窗口宽度（260–420）").pack(side=tk.LEFT)
        self.var_width = tk.IntVar(value=int(win_cfg.get("width", 300)))
        sp_width = ttk.Spinbox(
            row_width, from_=260, to=420, textvariable=self.var_width, width=7
        )
        sp_width.pack(side=tk.LEFT, padx=10)
        self._claim_wheel(sp_width)
        ttk.Label(root_f, text="窗口圆角半径（8–36）").pack(
            anchor="w", padx=pad_x, pady=(10, 4)
        )
        self.var_radius = tk.IntVar(value=int(win_cfg.get("corner_radius", 22)))
        sp_radius = ttk.Spinbox(
            root_f, from_=8, to=36, textvariable=self.var_radius, width=8
        )
        sp_radius.pack(anchor="w", padx=pad_x)
        self._claim_wheel(sp_radius)

        ttk.Label(root_f, text="内容边距（上下左右统一，8–36）").pack(
            anchor="w", padx=pad_x, pady=(12, 4)
        )
        self.var_margin = tk.IntVar(value=int(win_cfg.get("content_margin", 16)))
        sp_margin = ttk.Spinbox(
            root_f, from_=8, to=36, textvariable=self.var_margin, width=8
        )
        sp_margin.pack(anchor="w", padx=pad_x)
        self._claim_wheel(sp_margin)
        ttk.Label(
            root_f,
            text="控制悬浮窗外壳到内部模块的统一留白",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=pad_x)

        # —— 透明度横向滑块（拖动实时生效）——
        ttk.Label(root_f, text="窗口背景不透明度").pack(
            anchor="w", padx=pad_x, pady=(14, 4)
        )
        self.var_shell_op = tk.IntVar(value=int(win_cfg.get("shell_opacity", 100)))
        row_shell = ttk.Frame(root_f)
        row_shell.pack(fill=tk.X, padx=pad_x, pady=2)
        self.scale_shell = ttk.Scale(
            row_shell,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            command=self._on_shell_scale,
        )
        self.scale_shell.set(self.var_shell_op.get())
        self.scale_shell.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.lbl_shell_val = ttk.Label(row_shell, text=f"{self.var_shell_op.get()}%", width=5)
        self.lbl_shell_val.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Label(
            root_f,
            text="拖动即时生效 · 仅背景层 · 0=透出桌面 · 100=实色",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=pad_x)

        ttk.Label(root_f, text="卡片背景不透明度").pack(
            anchor="w", padx=pad_x, pady=(14, 4)
        )
        self.var_card_op = tk.IntVar(value=int(win_cfg.get("card_opacity", 100)))
        row_card = ttk.Frame(root_f)
        row_card.pack(fill=tk.X, padx=pad_x, pady=2)
        self.scale_card = ttk.Scale(
            row_card,
            from_=10,
            to=100,
            orient=tk.HORIZONTAL,
            command=self._on_card_scale,
        )
        self.scale_card.set(self.var_card_op.get())
        self.scale_card.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.lbl_card_val = ttk.Label(row_card, text=f"{self.var_card_op.get()}%", width=5)
        self.lbl_card_val.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Label(
            root_f,
            text="拖动即时生效 · 只改卡片底板，文字/圆环始终清晰不透明",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=pad_x)

        # —— 文字光晕（浅色桌面可读性）——
        ttk.Label(root_f, text="文字暗光晕强度").pack(
            anchor="w", padx=pad_x, pady=(14, 4)
        )
        self.var_halo = tk.IntVar(
            value=int(round(float(self.cfg.get("text_halo", 0.8)) * 100))
        )
        row_halo = ttk.Frame(root_f)
        row_halo.pack(fill=tk.X, padx=pad_x, pady=2)
        self.scale_halo = ttk.Scale(
            row_halo, from_=0, to=100, orient=tk.HORIZONTAL, command=self._on_halo_scale
        )
        self.scale_halo.set(self.var_halo.get())
        self.scale_halo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.lbl_halo_val = ttk.Label(row_halo, text=f"{self.var_halo.get()}%", width=5)
        self.lbl_halo_val.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Label(
            root_f,
            text="深色桌面几乎看不出 · 浅色桌面给文字兜对比度 · 0=关闭",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=pad_x)

        # —— 时间字体 ——
        clock_cfg = dict(self.cfg.get("clock", {}))
        ttk.Label(root_f, text="日期时钟字体粗细").pack(
            anchor="w", padx=pad_x, pady=(14, 4)
        )
        self.var_clock_stroke = tk.DoubleVar(
            value=float(clock_cfg.get("stroke", 1.4))
        )
        row_clock_stroke = ttk.Frame(root_f)
        row_clock_stroke.pack(fill=tk.X, padx=pad_x, pady=2)
        self.scale_clock_stroke = ttk.Scale(
            row_clock_stroke, from_=0, to=4, orient=tk.HORIZONTAL,
            command=self._on_clock_stroke_scale,
        )
        self.scale_clock_stroke.set(self.var_clock_stroke.get())
        self.scale_clock_stroke.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.lbl_clock_stroke_val = ttk.Label(
            row_clock_stroke, text=f"{self.var_clock_stroke.get():.1f}px", width=7
        )
        self.lbl_clock_stroke_val.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Label(
            root_f,
            text="时间字形外扩粗细 · 0=最细，4=最粗",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=pad_x)

        ttk.Label(root_f, text="时分间距").pack(
            anchor="w", padx=pad_x, pady=(10, 4)
        )
        self.var_clock_tracking = tk.DoubleVar(
            value=float(clock_cfg.get("tracking", 2.0))
        )
        row_clock_tracking = ttk.Frame(root_f)
        row_clock_tracking.pack(fill=tk.X, padx=pad_x, pady=2)
        self.scale_clock_tracking = ttk.Scale(
            row_clock_tracking, from_=0, to=6, orient=tk.HORIZONTAL,
            command=self._on_clock_tracking_scale,
        )
        self.scale_clock_tracking.set(self.var_clock_tracking.get())
        self.scale_clock_tracking.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.lbl_clock_tracking_val = ttk.Label(
            row_clock_tracking, text=f"{self.var_clock_tracking.get():.1f}px", width=7
        )
        self.lbl_clock_tracking_val.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Label(
            root_f,
            text="只增加冒号与时、分之间的间距，不改变数字内部间距",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=pad_x)

        # —— 启动相关 ——
        self.var_autostart = tk.BooleanVar(value=bool(self.cfg.get("autostart", False)))
        ttk.Checkbutton(
            root_f, text="开机自动启动", variable=self.var_autostart
        ).pack(anchor="w", padx=pad_x, pady=(14, 4))

        self.var_topmost = tk.BooleanVar(value=bool(win_cfg.get("always_on_top", True)))
        ttk.Checkbutton(
            root_f, text="启动时置顶显示", variable=self.var_topmost
        ).pack(anchor="w", padx=pad_x, pady=2)

        self.var_visible = tk.BooleanVar(value=bool(win_cfg.get("visible", True)))
        ttk.Checkbutton(
            root_f, text="启动时显示悬浮窗", variable=self.var_visible
        ).pack(anchor="w", padx=pad_x, pady=2)

        self.var_lock = tk.BooleanVar(value=bool(win_cfg.get("lock_pos", False)))
        ttk.Checkbutton(
            root_f, text="锁定位置（防止误拖）", variable=self.var_lock
        ).pack(anchor="w", padx=pad_x, pady=2)

        self.var_click_through = tk.BooleanVar(
            value=bool(win_cfg.get("click_through", False))
        )
        ttk.Checkbutton(
            root_f, text="鼠标穿透（通过托盘菜单恢复）",
            variable=self.var_click_through,
        ).pack(anchor="w", padx=pad_x, pady=2)

        self.var_fullscreen = tk.BooleanVar(
            value=bool(win_cfg.get("hide_on_fullscreen", True))
        )
        ttk.Checkbutton(
            root_f, text="全屏应用前台时自动隐藏（游戏 / 视频）",
            variable=self.var_fullscreen,
        ).pack(anchor="w", padx=pad_x, pady=2)

        row_snap = ttk.Frame(root_f)
        row_snap.pack(anchor="w", padx=pad_x, pady=(6, 2), fill=tk.X)
        ttk.Label(row_snap, text="贴边吸附距离（0 = 关闭）").pack(side=tk.LEFT)
        self.var_snap = tk.IntVar(value=int(win_cfg.get("snap_px", 16)))
        sp_snap = ttk.Spinbox(row_snap, from_=0, to=64, textvariable=self.var_snap, width=6)
        sp_snap.pack(side=tk.LEFT, padx=10)
        self._claim_wheel(sp_snap)

        # —— 卡片显示与顺序 ——
        ttk.Label(root_f, text="卡片显示与顺序").pack(
            anchor="w", padx=pad_x, pady=(16, 4)
        )
        ttk.Label(
            root_f,
            text="列表顺序即悬浮窗从上到下；全部取消会至少保留硬件监控",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=pad_x, pady=(0, 4))
        enabled = self.cfg.get("cards", {}) or {}
        self.var_cards: dict = {}
        self.card_order = normalize_card_order(self.cfg.get("card_order"))
        self._card_rows: dict = {}
        self._card_move_buttons: dict = {}
        self._card_drag_id: Optional[str] = None
        card_list = ttk.Frame(root_f)
        card_list.pack(fill=tk.X, padx=pad_x, pady=(0, 2))
        for cid in self.card_order:
            var = tk.BooleanVar(value=bool(enabled.get(cid, DEFAULT_CARDS[cid])))
            self.var_cards[cid] = var
            row = ttk.Frame(card_list)
            self._card_rows[cid] = row

            grip = ttk.Label(row, text="≡", style="Hint.TLabel", cursor="fleur", width=2)
            grip.pack(side=tk.LEFT, padx=(2, 2))
            grip.bind("<ButtonPress-1>", lambda evt, c=cid: self._start_card_drag(c))
            grip.bind("<B1-Motion>", self._drag_card)
            grip.bind("<ButtonRelease-1>", self._end_card_drag)

            ttk.Checkbutton(row, text=CARD_NAMES[cid], variable=var).pack(
                side=tk.LEFT, fill=tk.X, expand=True
            )
            btn_down = ttk.Button(
                row, text="↓", width=3, style="CardOrder.TButton",
                command=lambda c=cid: self._move_card(c, 1),
            )
            btn_down.pack(side=tk.RIGHT, padx=(3, 0))
            btn_up = ttk.Button(
                row, text="↑", width=3, style="CardOrder.TButton",
                command=lambda c=cid: self._move_card(c, -1),
            )
            btn_up.pack(side=tk.RIGHT)
            self._card_move_buttons[cid] = (btn_up, btn_down)
        self._repack_card_rows()

        # —— 资源告警 ——
        ttk.Label(root_f, text="资源告警").pack(
            anchor="w", padx=pad_x, pady=(16, 4)
        )
        alert_cfg = self.cfg.get("alerts", {}) or {}
        self.var_alert_enabled = tk.BooleanVar(
            value=bool(alert_cfg.get("enabled", False))
        )
        ttk.Checkbutton(
            root_f, text="超过阈值时发送 Windows 通知",
            variable=self.var_alert_enabled,
        ).pack(anchor="w", padx=pad_x, pady=(0, 3))
        self.var_alerts = {}
        for key, label, default, upper, unit in (
            ("cpu_temp", "CPU 温度", 85, 120, "°C"),
            ("gpu_temp", "GPU 温度", 85, 120, "°C"),
            ("memory", "内存占用", 90, 100, "%"),
            ("disk", "磁盘占用", 90, 100, "%"),
            ("traffic", "VPS 流量", 85, 100, "%"),
        ):
            row_alert = ttk.Frame(root_f)
            row_alert.pack(fill=tk.X, padx=pad_x, pady=2)
            ttk.Label(row_alert, text=label, width=14).pack(side=tk.LEFT)
            var = tk.IntVar(value=int(alert_cfg.get(key, default)))
            self.var_alerts[key] = var
            spin = ttk.Spinbox(
                row_alert, from_=0, to=upper, textvariable=var, width=7
            )
            spin.pack(side=tk.LEFT)
            ttk.Label(row_alert, text=unit).pack(side=tk.LEFT, padx=(5, 0))
            self._claim_wheel(spin)

        row_duration = ttk.Frame(root_f)
        row_duration.pack(fill=tk.X, padx=pad_x, pady=2)
        ttk.Label(row_duration, text="持续时间", width=14).pack(side=tk.LEFT)
        self.var_alert_duration = tk.IntVar(
            value=int(alert_cfg.get("duration_sec", 15))
        )
        sp_duration = ttk.Spinbox(
            row_duration, from_=0, to=600, textvariable=self.var_alert_duration, width=7
        )
        sp_duration.pack(side=tk.LEFT)
        ttk.Label(row_duration, text="秒").pack(side=tk.LEFT, padx=(5, 0))
        self._claim_wheel(sp_duration)

        row_cooldown = ttk.Frame(root_f)
        row_cooldown.pack(fill=tk.X, padx=pad_x, pady=2)
        ttk.Label(row_cooldown, text="通知冷却", width=14).pack(side=tk.LEFT)
        self.var_alert_cooldown = tk.IntVar(
            value=max(1, int(alert_cfg.get("cooldown_sec", 300)) // 60)
        )
        sp_cooldown = ttk.Spinbox(
            row_cooldown, from_=1, to=120,
            textvariable=self.var_alert_cooldown, width=7,
        )
        sp_cooldown.pack(side=tk.LEFT)
        ttk.Label(row_cooldown, text="分钟").pack(side=tk.LEFT, padx=(5, 0))
        self._claim_wheel(sp_cooldown)

        # —— 双击各卡片执行什么 ——
        ttk.Label(root_f, text="双击卡片动作").pack(
            anchor="w", padx=pad_x, pady=(16, 4)
        )
        ttk.Label(
            root_f,
            text="卡片之外（网速胶囊 / 卡片间隙）双击不响应",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=pad_x, pady=(0, 6))

        bindings = self.cfg.get("doubleclick", {}) or {}
        self.var_dbl: dict = {}
        for zone, zone_label in ZONES:
            row_z = ttk.Frame(root_f)
            row_z.pack(fill=tk.X, padx=pad_x, pady=3)
            ttk.Label(row_z, text=zone_label, width=17).pack(side=tk.LEFT)
            var = tk.StringVar(
                value=action_label_for(bindings.get(zone, DEFAULT_BINDINGS[zone]))
            )
            self.var_dbl[zone] = var
            cmb = ttk.Combobox(
                row_z, textvariable=var, values=ACTION_LABELS,
                state="readonly", style="Theme.TCombobox",
            )
            cmb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
            self._claim_wheel(cmb)

        # —— 全局快捷键 ——
        ttk.Label(root_f, text="显示/隐藏 全局快捷键").pack(
            anchor="w", padx=pad_x, pady=(16, 4)
        )
        hk = self.cfg.get("hotkey", {})
        self.var_hk_enabled = tk.BooleanVar(value=bool(hk.get("enabled", True)))
        ttk.Checkbutton(
            root_f, text="启用全局快捷键", variable=self.var_hk_enabled
        ).pack(anchor="w", padx=pad_x, pady=2)

        row_hk = ttk.Frame(root_f)
        row_hk.pack(anchor="w", padx=pad_x, pady=8, fill=tk.X)
        self.var_hotkey = tk.StringVar(
            value=str(hk.get("toggle_visible", "ctrl+shift+m"))
        )
        self.ent_hotkey = ttk.Entry(row_hk, textvariable=self.var_hotkey, width=28)
        self.ent_hotkey.pack(side=tk.LEFT)
        self.btn_capture = ttk.Button(
            row_hk, text="按下录入", command=self._capture_hotkey, width=10
        )
        self.btn_capture.pack(side=tk.LEFT, padx=8)
        ttk.Label(
            root_f,
            text="快捷键显示悬浮窗时，会自动开启置顶（托盘「置顶显示」同步勾选）",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=pad_x)

        # —— 流量 ——
        # 显示开关在上面「显示卡片」里，这里只放接口相关设置
        traf = self.cfg.get("traffic", {})
        ttk.Label(root_f, text="VPS 流量接口").pack(
            anchor="w", padx=pad_x, pady=(16, 4)
        )

        row = ttk.Frame(root_f)
        row.pack(anchor="w", padx=pad_x, pady=6, fill=tk.X)
        ttk.Label(row, text="流量更新间隔（分钟）").pack(side=tk.LEFT)
        interval = int(traf.get("interval_min", 5) or 5)
        interval = max(1, min(120, interval))
        self.var_interval = tk.IntVar(value=interval)
        sp_interval = ttk.Spinbox(
            row, from_=1, to=120, textvariable=self.var_interval, width=6
        )
        sp_interval.pack(side=tk.LEFT, padx=10)
        self._claim_wheel(sp_interval)

        # 换 VPS 就改这两栏（以前只能手改 config.json）
        ttk.Label(root_f, text="流量接口地址").pack(
            anchor="w", padx=pad_x, pady=(12, 4)
        )
        self.var_traffic_url = tk.StringVar(value=str(traf.get("url", "") or ""))
        self.ent_traffic_url = ttk.Entry(
            root_f, textvariable=self.var_traffic_url, show="•"
        )
        self.ent_traffic_url.pack(
            fill=tk.X, padx=pad_x
        )
        self.var_show_traffic_url = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            root_f, text="显示完整接口地址", variable=self.var_show_traffic_url,
            command=self._toggle_traffic_url,
        ).pack(anchor="w", padx=pad_x, pady=(2, 0))
        ttk.Label(
            root_f,
            text="留空不请求，卡片显示「未配置接口地址」",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=pad_x)

        ttk.Label(root_f, text="代理地址（留空 = 直连）").pack(
            anchor="w", padx=pad_x, pady=(10, 4)
        )
        self.var_traffic_proxy = tk.StringVar(value=str(traf.get("proxy", "") or ""))
        ttk.Entry(root_f, textvariable=self.var_traffic_proxy).pack(
            fill=tk.X, padx=pad_x
        )
        ttk.Label(
            root_f,
            text="先走代理，失败自动直连",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=pad_x)

        ttk.Label(
            root_f,
            text="失败沿用上次数据；连续 10 次失败显示「获取数据失败」",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=pad_x, pady=(10, 20))

        # —— 运行诊断 ——
        if self.diagnostics:
            try:
                diag = self.diagnostics() or {}
            except Exception:
                diag = {}
            ttk.Label(root_f, text="运行诊断").pack(
                anchor="w", padx=pad_x, pady=(4, 4)
            )
            status = (
                f"采集 {float(diag.get('sample_ms', 0)):.1f} ms · "
                f"GPU {'可用' if diag.get('gpu') else '不可用'} · "
                f"CPU 温度 {'可用' if diag.get('cpu_temp') else '不可用'} · "
                f"流量 {diag.get('traffic', 'pending')}"
            )
            if diag.get("sample_error"):
                status += " · 采集异常"
            if diag.get("hotkey_error"):
                status += " · 快捷键异常"
            ttk.Label(root_f, text=status, style="Hint.TLabel").pack(
                anchor="w", padx=pad_x
            )
            config_path = str(diag.get("config_path", ""))
            ttk.Label(
                root_f, text=f"配置：{config_path}", style="Hint.TLabel",
                wraplength=390,
            ).pack(anchor="w", padx=pad_x, pady=(2, 4))
            if config_path:
                ttk.Button(
                    root_f, text="打开用户数据目录",
                    command=lambda p=config_path: os.startfile(os.path.dirname(p)),
                ).pack(anchor="w", padx=pad_x)

        # —— 底部固定按钮栏 ——
        btn_bar = ttk.Frame(outer)
        btn_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=16, pady=12)
        # 分隔线
        tk.Frame(outer, bg="#2C2C2E", height=1).pack(
            fill=tk.X, side=tk.BOTTOM, before=btn_bar
        )
        ttk.Button(btn_bar, text="取消", command=self._cancel).pack(side=tk.RIGHT, padx=6)
        ttk.Button(btn_bar, text="保存并应用", command=self._save).pack(side=tk.RIGHT)

        w.protocol("WM_DELETE_WINDOW", self._cancel)
        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        ww, wh = 460, 560
        w.geometry(f"{ww}x{wh}+{(sw - ww) // 2}+{(sh - wh) // 3}")
        # 初始滚动区域
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    # 这些控件的 class 绑定自己会用滚轮（下拉选值 / 数字微调 / 列表滚动），
    # 页面滚动必须让位，否则一次滚轮触发两个动作。
    _WHEEL_OWNERS = frozenset({"TCombobox", "TSpinbox", "Listbox", "Text"})

    def _page_scroll(self, delta: int) -> None:
        try:
            if self._canvas.winfo_exists():
                self._canvas.yview_scroll(int(-1 * (delta / 120)), "units")
        except Exception:
            pass

    def _wheel(self, evt):
        """顶层滚轮 → 滚动设置页；落在自带滚轮行为的控件上时不抢。"""
        widget = evt.widget
        if isinstance(widget, str):
            # Tcl 侧创建的控件（如 combobox 的 popdown 列表）在 Python 没有对象
            try:
                widget = self.win.nametowidget(widget)
            except Exception:
                return
        try:
            if widget.winfo_class() in self._WHEEL_OWNERS:
                return
        except Exception:
            pass
        self._page_scroll(evt.delta)

    def _claim_wheel(self, widget) -> None:
        """
        在控件自身（bindtags 第一位）拦下滚轮：滚页面并 break，
        阻断 class 级的「改数值」。避免滚动设置页时把配色/圆角/间隔悄悄改掉。
        下拉列表**弹开后**是另一个顶层，不受影响，照常滚它自己的列表。
        """
        def handler(evt):
            self._page_scroll(evt.delta)
            return "break"

        widget.bind("<MouseWheel>", handler)

    def _on_layout_selected(self, _evt=None) -> None:
        label = self.var_layout.get()
        if label not in self._layout_labels:
            return
        layout_id = self._layout_ids[self._layout_labels.index(label)]
        presets = {
            "compact": (270, 10),
            "standard": (300, 12),
            "spacious": (340, 16),
        }
        if layout_id in presets:
            width, margin = presets[layout_id]
            self.var_width.set(width)
            self.var_margin.set(margin)

    def _toggle_traffic_url(self) -> None:
        self.ent_traffic_url.configure(
            show="" if self.var_show_traffic_url.get() else "•"
        )

    def _repack_card_rows(self) -> None:
        for index, cid in enumerate(self.card_order):
            row = self._card_rows[cid]
            row.pack_forget()
            row.pack(fill=tk.X, pady=1)
            up, down = self._card_move_buttons[cid]
            up.configure(state="disabled" if index == 0 else "normal")
            down.configure(
                state="disabled" if index == len(self.card_order) - 1 else "normal"
            )

    def _move_card(self, cid: str, offset: int) -> None:
        index = self.card_order.index(cid)
        target = max(0, min(len(self.card_order) - 1, index + offset))
        if target == index:
            return
        self.card_order[index], self.card_order[target] = (
            self.card_order[target],
            self.card_order[index],
        )
        self._repack_card_rows()

    def _start_card_drag(self, cid: str) -> None:
        self._card_drag_id = cid

    def _drag_card(self, evt) -> None:
        cid = self._card_drag_id
        if cid is None:
            return

        remaining = [item for item in self.card_order if item != cid]
        insert_at = len(remaining)
        for index, target in enumerate(remaining):
            row = self._card_rows[target]
            if evt.y_root < row.winfo_rooty() + row.winfo_height() / 2:
                insert_at = index
                break
        new_order = list(remaining)
        new_order.insert(insert_at, cid)
        if new_order != self.card_order:
            self.card_order = new_order
            self._repack_card_rows()

    def _end_card_drag(self, _evt=None) -> None:
        self._card_drag_id = None

    def _unbind_wheel(self) -> None:
        # 滚轮已改为绑定在设置窗顶层，随窗口销毁自动失效，无需全局解绑。
        pass

    def _close(self) -> None:
        self._unbind_wheel()
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None

    def _cancel(self) -> None:
        if self.on_cancel:
            try:
                self.on_cancel()
            except Exception:
                pass
        self._close()

    def _theme_id_from_label(self) -> str:
        label = self.var_theme.get()
        if label in self._theme_labels:
            return self._theme_ids[self._theme_labels.index(label)]
        return "midnight"

    def _on_theme_selected(self, _evt=None) -> None:
        tid = self._theme_id_from_label()
        self._draw_preview(tid)
        # 同步到本窗口持有的 cfg（保存时不会丢）
        self.cfg["theme_id"] = tid
        self.cfg["style"] = dict(THEMES[tid]["style"])
        # 立即应用到悬浮窗
        if self.on_theme_live:
            try:
                self.on_theme_live(tid)
            except Exception:
                pass

    def _on_shell_scale(self, _val=None) -> None:
        try:
            v = int(round(float(self.scale_shell.get())))
        except Exception:
            return
        v = max(0, min(100, v))
        self.var_shell_op.set(v)
        try:
            self.lbl_shell_val.configure(text=f"{v}%")
        except Exception:
            pass
        self._fire_opacity_live()

    def _on_card_scale(self, _val=None) -> None:
        try:
            v = int(round(float(self.scale_card.get())))
        except Exception:
            return
        v = max(10, min(100, v))
        self.var_card_op.set(v)
        try:
            self.lbl_card_val.configure(text=f"{v}%")
        except Exception:
            pass
        self._fire_opacity_live()

    def _on_clock_stroke_scale(self, _val=None) -> None:
        try:
            v = max(0.0, min(4.0, float(self.scale_clock_stroke.get())))
        except Exception:
            return
        self.var_clock_stroke.set(v)
        try:
            self.lbl_clock_stroke_val.configure(text=f"{v:.1f}px")
        except Exception:
            pass
        clock = dict(self.cfg.get("clock", {}))
        clock["stroke"] = v
        self.cfg["clock"] = clock

    def _on_clock_tracking_scale(self, _val=None) -> None:
        try:
            v = max(0.0, min(6.0, float(self.scale_clock_tracking.get())))
        except Exception:
            return
        self.var_clock_tracking.set(v)
        try:
            self.lbl_clock_tracking_val.configure(text=f"{v:.1f}px")
        except Exception:
            pass
        clock = dict(self.cfg.get("clock", {}))
        clock["tracking"] = v
        self.cfg["clock"] = clock

    def _on_halo_scale(self, _val=None) -> None:
        try:
            v = int(round(float(self.scale_halo.get())))
        except Exception:
            return
        v = max(0, min(100, v))
        self.var_halo.set(v)
        try:
            self.lbl_halo_val.configure(text=f"{v}%")
        except Exception:
            pass
        # 同步到设置窗 cfg，避免保存时被旧值覆盖
        self.cfg["text_halo"] = v / 100.0
        if self.on_halo_live:
            try:
                self.on_halo_live(v)
            except Exception:
                pass

    def _fire_opacity_live(self) -> None:
        if not self.on_opacity_live:
            return
        try:
            shell = max(0, min(100, int(self.var_shell_op.get())))
            card = max(10, min(100, int(self.var_card_op.get())))
            # 同步到设置窗 cfg，避免保存时被旧值覆盖
            win = dict(self.cfg.get("window", {}))
            win["shell_opacity"] = shell
            win["card_opacity"] = card
            self.cfg["window"] = win
            self.on_opacity_live(shell, card)
        except Exception:
            pass

    def _draw_preview(self, theme_id: str) -> None:
        self.preview.delete("all")
        st = THEMES.get(theme_id, THEMES["midnight"])["style"]
        colors = [
            st["accent_cpu"],
            st["accent_mem"],
            st["accent_gpu"],
            st["accent_up"],
            st["accent_down"],
            st["glass_bg"],
            st["glass_card"],
        ]
        x = 4
        for col in colors:
            self.preview.create_oval(x, 8, x + 24, 32, fill=col, outline="#3A3A3C")
            x += 32

    def _capture_hotkey(self) -> None:
        if self._capturing:
            return
        self._capturing = True
        self.btn_capture.configure(text="请按键…", state="disabled")
        self.var_hotkey.set("等待按键…")

        def worker():
            combo = HotkeyManager.capture_once()

            def done():
                self._capturing = False
                try:
                    self.btn_capture.configure(text="按下录入", state="normal")
                except Exception:
                    return
                if combo:
                    self.var_hotkey.set(combo)
                else:
                    self.var_hotkey.set(
                        str(
                            self.cfg.get("hotkey", {}).get(
                                "toggle_visible", "ctrl+shift+m"
                            )
                        )
                    )

            try:
                self.master.after(0, done)
            except Exception:
                pass

        threading.Thread(target=worker, name="hk-capture", daemon=True).start()

    def _save(self) -> None:
        try:
            theme_id = self._theme_id_from_label()
            interval = max(1, min(120, int(self.var_interval.get())))
            radius = max(8, min(36, int(self.var_radius.get())))
            margin = max(8, min(36, int(self.var_margin.get())))
            width = max(260, min(420, int(self.var_width.get())))
            shell_op = max(0, min(100, int(self.var_shell_op.get())))
            card_op = max(10, min(100, int(self.var_card_op.get())))
            halo = max(0, min(100, int(self.var_halo.get())))
            clock_stroke = max(0.0, min(4.0, float(self.var_clock_stroke.get())))
            clock_tracking = max(0.0, min(6.0, float(self.var_clock_tracking.get())))
            alert_values = {key: int(var.get()) for key, var in self.var_alerts.items()}
            alert_duration = max(0, min(600, int(self.var_alert_duration.get())))
            alert_cooldown = max(1, min(120, int(self.var_alert_cooldown.get())))
        except (TypeError, ValueError, tk.TclError):
            messagebox.showerror("无法保存", "请检查所有数字输入项。", parent=self.win)
            return
        hotkey = (self.var_hotkey.get() or "").strip()
        if hotkey in ("等待按键…",):
            hotkey = str(
                self.cfg.get("hotkey", {}).get("toggle_visible", "ctrl+shift+m")
            )
        if self.var_hk_enabled.get() and not hotkey:
            messagebox.showerror("无法保存", "启用快捷键时不能留空。", parent=self.win)
            return

        traffic_url = self.var_traffic_url.get().strip()
        traffic_proxy = self.var_traffic_proxy.get().strip()
        if traffic_url:
            parsed = urlparse(traffic_url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                messagebox.showerror(
                    "无法保存", "流量接口地址必须是完整的 http:// 或 https:// 地址。",
                    parent=self.win,
                )
                return
        if traffic_proxy:
            parsed = urlparse(traffic_proxy)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                messagebox.showerror(
                    "无法保存", "代理地址格式不正确。", parent=self.win
                )
                return

        new_cfg = dict(self.cfg)
        new_cfg["theme_id"] = theme_id
        new_cfg["autostart"] = bool(self.var_autostart.get())
        new_cfg["doubleclick"] = {
            zone: id_for_label(self.var_dbl[zone].get()) for zone, _ in ZONES
        }
        new_cfg.pop("open_taskmgr_on_doubleclick", None)
        new_cfg["text_halo"] = halo / 100.0
        new_cfg["clock"] = {
            **dict(new_cfg.get("clock", {})),
            "stroke": clock_stroke,
            "tracking": clock_tracking,
        }
        new_cfg["style"] = dict(THEMES[theme_id]["style"])

        new_cfg["hotkey"] = {
            "enabled": bool(self.var_hk_enabled.get()),
            "toggle_visible": hotkey,
        }

        cards = {cid: bool(v.get()) for cid, v in self.var_cards.items()}
        if not any(cards.values()):
            cards["hw"] = True
        new_cfg["cards"] = cards
        new_cfg["card_order"] = list(self.card_order)
        new_cfg["alerts"] = {
            "enabled": bool(self.var_alert_enabled.get()),
            **alert_values,
            "duration_sec": alert_duration,
            "cooldown_sec": alert_cooldown * 60,
            "hysteresis": int(self.cfg.get("alerts", {}).get("hysteresis", 3)),
        }

        traf = dict(new_cfg.get("traffic", {}))
        # 流量卡的显示开关就是采集开关，统一以「显示卡片」里的勾选为准
        traf["enabled"] = cards["traffic"]
        traf["interval_min"] = interval
        traf["interval_sec"] = interval * 60
        traf["url"] = traffic_url
        traf["proxy"] = traffic_proxy
        new_cfg["traffic"] = traf

        win = dict(new_cfg.get("window", {}))
        win["always_on_top"] = bool(self.var_topmost.get())
        win["visible"] = bool(self.var_visible.get())
        win["lock_pos"] = bool(self.var_lock.get())
        win["click_through"] = bool(self.var_click_through.get())
        win["hide_on_fullscreen"] = bool(self.var_fullscreen.get())
        win["snap_px"] = max(0, min(64, int(self.var_snap.get())))
        win["corner_radius"] = radius
        win["content_margin"] = margin
        win["width"] = width
        layout_label = self.var_layout.get()
        selected_layout = (
            self._layout_ids[self._layout_labels.index(layout_label)]
            if layout_label in self._layout_labels else "custom"
        )
        preset_values = {
            "compact": (270, 10),
            "standard": (300, 12),
            "spacious": (340, 16),
        }
        win["layout_preset"] = (
            selected_layout
            if preset_values.get(selected_layout) == (width, margin)
            else "custom"
        )
        win["shell_opacity"] = shell_op
        win["card_opacity"] = card_op
        win.pop("bg_transparent", None)
        # height 不在这里算：窗口高度由启用的卡片决定，交给 _compute_size
        win.pop("height", None)
        new_cfg["window"] = win

        if self.on_save(new_cfg) is False:
            messagebox.showerror(
                "保存失败", "无法写入用户配置，请查看用户数据目录中的 crash.log。",
                parent=self.win,
            )
            return
        self._close()
