"""
Windows 开机自启：写入用户 Startup 目录下的启动脚本。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "GlassMonitor"
STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / (
    r"Microsoft\Windows\Start Menu\Programs\Startup"
)
STARTUP_BAT = STARTUP_DIR / f"{APP_NAME}.bat"


def _launch_command() -> str:
    root = Path(__file__).resolve().parent
    monitor = root / "monitor.py"
    # 优先 pythonw，无黑框
    pyw = Path(sys.executable)
    if pyw.name.lower() == "python.exe":
        cand = pyw.with_name("pythonw.exe")
        if cand.is_file():
            pyw = cand
    return f'@echo off\r\nstart "" "{pyw}" "{monitor}"\r\n'


def is_autostart_enabled() -> bool:
    return STARTUP_BAT.is_file()


def set_autostart(enabled: bool) -> None:
    STARTUP_DIR.mkdir(parents=True, exist_ok=True)
    if enabled:
        STARTUP_BAT.write_text(_launch_command(), encoding="utf-8")
    else:
        if STARTUP_BAT.is_file():
            STARTUP_BAT.unlink()
