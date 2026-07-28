#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Glass Monitor — 硬件 / 网络流量悬浮监控入口。

用法:
  python monitor.py
  或双击 run.bat

详见 README.md
"""

from __future__ import annotations

import os
import sys
import traceback

# 保证同目录模块可导入（被其它 cwd 启动时）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    from instance_guard import SingleInstance

    instance = SingleInstance()
    if not instance.acquired:
        return 0
    try:
        from glass_ui import GlassMonitorApp, _log_crash
    except ImportError as e:
        print("依赖缺失，请先执行: pip install -r requirements.txt")
        print(e)
        instance.close()
        return 1
    try:
        app = GlassMonitorApp()
        app.run()
        return 0
    except Exception:
        _log_crash(f"[monitor.main] {traceback.format_exc()}")
        traceback.print_exc()
        return 1
    finally:
        instance.close()


if __name__ == "__main__":
    raise SystemExit(main())
