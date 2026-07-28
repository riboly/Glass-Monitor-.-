"""Windows 多显示器工作区查询与窗口位置约束。"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import NamedTuple


class WorkArea(NamedTuple):
    left: int
    top: int
    right: int
    bottom: int
    primary: bool = False


def work_areas() -> list[WorkArea]:
    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    result: list[WorkArea] = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )

    def callback(hmon, _hdc, _rect, _data):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            rect = info.rcWork
            result.append(
                WorkArea(rect.left, rect.top, rect.right, rect.bottom, bool(info.dwFlags & 1))
            )
        return True

    cb = callback_type(callback)
    user32.EnumDisplayMonitors(None, None, cb, 0)
    return result


def nearest_work_area(x: int, y: int, width: int, height: int) -> WorkArea | None:
    areas = work_areas()
    if not areas:
        return None
    cx, cy = x + width // 2, y + height // 2

    def distance(area: WorkArea) -> int:
        dx = max(area.left - cx, 0, cx - area.right)
        dy = max(area.top - cy, 0, cy - area.bottom)
        return dx * dx + dy * dy

    return min(areas, key=distance)


def position_visible(x: int, y: int, width: int, height: int) -> bool:
    for area in work_areas():
        overlap_w = max(0, min(x + width, area.right) - max(x, area.left))
        overlap_h = max(0, min(y + height, area.bottom) - max(y, area.top))
        if overlap_w >= min(80, width) and overlap_h >= min(40, height):
            return True
    return False


def clamp_to_work_area(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    area = nearest_work_area(x, y, width, height)
    if area is None:
        return x, y
    max_x = max(area.left, area.right - width)
    max_y = max(area.top, area.bottom - height)
    return max(area.left, min(x, max_x)), max(area.top, min(y, max_y))
