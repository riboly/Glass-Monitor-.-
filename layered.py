"""
Windows 分层窗口：UpdateLayeredWindow 真 per-pixel alpha。

整个悬浮窗（外壳 + 卡片 + 文字 + 图表）都由一张 RGBA 位图推送，
不再使用 transparentcolor 色键 —— 这是浅色桌面下不产生锯齿的前提。

性能要点：
- RGBA → 预乘 BGRA 用 numpy 向量化（原逐像素 Python 循环 ~0.5s/帧）
- DIB / 内存 DC 按尺寸缓存复用，避免每帧 GDI 创建销毁
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Optional

from PIL import Image

try:
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

# ---- Win32 constants ----
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020
WS_EX_APPWINDOW = 0x00040000
GWL_EXSTYLE = -20
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
DIB_RGB_COLORS = 0
BI_RGB = 0
GA_ROOT = 2
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOOWNERZORDER = 0x0200


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


# 64-bit 安全的 Get/SetWindowLong
if ctypes.sizeof(ctypes.c_void_p) == 8:
    _GetWindowLong = user32.GetWindowLongPtrW
    _SetWindowLong = user32.SetWindowLongPtrW
    _GetWindowLong.restype = ctypes.c_longlong
    _SetWindowLong.restype = ctypes.c_longlong
    _SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
    _GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
else:
    _GetWindowLong = user32.GetWindowLongW
    _SetWindowLong = user32.SetWindowLongW

user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL


def resolve_hwnd(raw_id: int) -> int:
    """Tk winfo_id → 可用于 ULW 的顶层 HWND。"""
    hwnd = int(raw_id)
    if not hwnd or not user32.IsWindow(hwnd):
        return hwnd
    root = user32.GetAncestor(hwnd, GA_ROOT)
    if root and user32.IsWindow(root):
        return int(root)
    return hwnd


def enable_layered(hwnd: int) -> None:
    """确保窗口带 WS_EX_LAYERED 扩展样式。"""
    if not hwnd:
        return
    style = int(_GetWindowLong(hwnd, GWL_EXSTYLE) or 0)
    if not (style & WS_EX_LAYERED):
        _SetWindowLong(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)


def make_overlay_window(hwnd: int) -> int:
    """
    把 Tk 顶层窗变成「桌面挂件」：

    - WS_EX_LAYERED：per-pixel alpha（真抗锯齿的前提）
    - WS_EX_NOACTIVATE：点击 / 拖拽 / 双击都不夺取前台焦点，
      也就不会因为激活或 z-order 变动而整体变暗
    - WS_EX_TOOLWINDOW：不出现在 Alt-Tab / 任务栏

    分层窗的命中测试按位图 alpha 进行：alpha=0 的像素自动点击穿透，
    所以圆角外和卡片间隙会落到桌面上，无需额外的区域裁剪。
    """
    hwnd = resolve_hwnd(hwnd)
    if not hwnd:
        return 0
    style = int(_GetWindowLong(hwnd, GWL_EXSTYLE) or 0)
    style |= WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    style &= ~WS_EX_TRANSPARENT  # 必须可命中，否则收不到拖拽/右键
    style &= ~WS_EX_APPWINDOW
    _SetWindowLong(hwnd, GWL_EXSTYLE, style)
    return hwnd


def set_click_through(hwnd: int, enabled: bool) -> None:
    """切换整窗鼠标穿透，不改变其它分层窗口样式。"""
    if not hwnd:
        return
    style = int(_GetWindowLong(hwnd, GWL_EXSTYLE) or 0)
    if enabled:
        style |= WS_EX_TRANSPARENT
    else:
        style &= ~WS_EX_TRANSPARENT
    _SetWindowLong(hwnd, GWL_EXSTYLE, style)


# 兼容旧调用名
def make_background_click_through(hwnd: int) -> None:
    make_overlay_window(hwnd)


def rgba_to_premultiplied_bgra_bottomup(img: Image.Image) -> bytes:
    """PIL RGBA → 预乘 BGRA，bottom-up 行序（DIB 默认）。"""
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    if _np is not None:
        arr = _np.frombuffer(img.tobytes(), dtype=_np.uint8)
        arr = arr.reshape((img.height, img.width, 4))
        a = arr[:, :, 3].astype(_np.uint16)
        out = _np.empty_like(arr)
        # 预乘并交换 R/B（BGRA）
        for src, dst in ((2, 0), (1, 1), (0, 2)):
            out[:, :, dst] = ((arr[:, :, src].astype(_np.uint16) * a + 127) // 255).astype(
                _np.uint8
            )
        out[:, :, 3] = arr[:, :, 3]
        return out[::-1].tobytes()

    # numpy 缺失时的纯 Python 回退（慢，仅保底）
    w, h = img.size
    src = img.tobytes()
    stride = w * 4
    buf = bytearray(h * stride)
    for y in range(h):
        si = y * stride
        oi = (h - 1 - y) * stride
        for x in range(w):
            i = si + x * 4
            o = oi + x * 4
            r, g, b, a = src[i], src[i + 1], src[i + 2], src[i + 3]
            if a == 0:
                continue
            if a == 255:
                buf[o], buf[o + 1], buf[o + 2], buf[o + 3] = b, g, r, 255
            else:
                buf[o] = (b * a + 127) // 255
                buf[o + 1] = (g * a + 127) // 255
                buf[o + 2] = (r * a + 127) // 255
                buf[o + 3] = a
    return bytes(buf)


class _Surface:
    """按尺寸缓存的 DIB + 内存 DC，避免每帧创建/销毁 GDI 对象。"""

    __slots__ = ("w", "h", "hdc", "hbmp", "bits", "old", "nbytes")

    def __init__(self, hdc_screen: int, w: int, h: int):
        self.w, self.h = w, h
        self.nbytes = w * h * 4
        bmi = BITMAPINFO()
        ctypes.memset(ctypes.byref(bmi), 0, ctypes.sizeof(bmi))
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = h  # 正高度 = bottom-up
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        bmi.bmiHeader.biSizeImage = self.nbytes

        self.hdc = gdi32.CreateCompatibleDC(hdc_screen)
        self.bits = ctypes.c_void_p()
        self.hbmp = gdi32.CreateDIBSection(
            hdc_screen, ctypes.byref(bmi), DIB_RGB_COLORS,
            ctypes.byref(self.bits), None, 0,
        )
        self.old = gdi32.SelectObject(self.hdc, self.hbmp) if self.hbmp else None

    def ok(self) -> bool:
        return bool(self.hdc and self.hbmp and self.bits)

    def close(self) -> None:
        try:
            if self.hdc and self.old:
                gdi32.SelectObject(self.hdc, self.old)
            if self.hbmp:
                gdi32.DeleteObject(self.hbmp)
            if self.hdc:
                gdi32.DeleteDC(self.hdc)
        except Exception:
            pass
        self.hdc = self.hbmp = self.old = None
        self.bits = None


_surface: Optional[_Surface] = None


def _get_surface(hdc_screen: int, w: int, h: int) -> Optional[_Surface]:
    global _surface
    if _surface is not None and _surface.w == w and _surface.h == h and _surface.ok():
        return _surface
    if _surface is not None:
        _surface.close()
        _surface = None
    surf = _Surface(hdc_screen, w, h)
    if not surf.ok():
        surf.close()
        return None
    _surface = surf
    return surf


def release_surface() -> None:
    global _surface
    if _surface is not None:
        _surface.close()
        _surface = None


def update_layered_window(
    hwnd: int,
    img: Image.Image,
    *,
    x: Optional[int] = None,
    y: Optional[int] = None,
    constant_alpha: int = 255,
) -> bool:
    """用 RGBA 图更新分层窗口（真 per-pixel alpha）。同时可设定位置/尺寸。"""
    hwnd = resolve_hwnd(hwnd)
    if not hwnd or not user32.IsWindow(hwnd):
        return False

    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size
    if w <= 0 or h <= 0:
        return False

    enable_layered(hwnd)
    bgra = rgba_to_premultiplied_bgra_bottomup(img)

    hdc_screen = user32.GetDC(0)
    if not hdc_screen:
        return False
    ok = False
    try:
        surf = _get_surface(hdc_screen, w, h)
        if surf is None:
            return False
        ctypes.memmove(surf.bits, bgra, min(len(bgra), surf.nbytes))

        blend = BLENDFUNCTION()
        blend.BlendOp = AC_SRC_OVER
        blend.BlendFlags = 0
        blend.SourceConstantAlpha = max(0, min(255, int(constant_alpha)))
        blend.AlphaFormat = AC_SRC_ALPHA

        size = SIZE(w, h)
        pt_src = POINT(0, 0)
        ppt_dst = None
        pt_dst = POINT(0, 0)
        if x is not None and y is not None:
            pt_dst.x = int(x)
            pt_dst.y = int(y)
            ppt_dst = ctypes.byref(pt_dst)

        ok = bool(
            user32.UpdateLayeredWindow(
                wintypes.HWND(hwnd), hdc_screen, ppt_dst, ctypes.byref(size),
                surf.hdc, ctypes.byref(pt_src), 0, ctypes.byref(blend), ULW_ALPHA,
            )
        )
    finally:
        user32.ReleaseDC(0, hdc_screen)
    return ok


def scale_rgba_alpha(img: Image.Image, factor: float) -> Image.Image:
    """整体缩放 alpha 通道（用于窗口总透明度）。"""
    factor = max(0.0, min(1.0, float(factor)))
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    if factor >= 0.999:
        return img
    if factor <= 0.001:
        return Image.new("RGBA", img.size, (0, 0, 0, 0))
    r, g, b, a = img.split()
    a = a.point(lambda p, f=factor: int(p * f + 0.5))
    return Image.merge("RGBA", (r, g, b, a))


def move_window(hwnd: int, x: int, y: int) -> None:
    """仅移动分层窗口位置（不重传位图）——拖拽热路径。"""
    hwnd = resolve_hwnd(hwnd)
    if not hwnd:
        return
    user32.SetWindowPos(
        wintypes.HWND(hwnd), None, int(x), int(y), 0, 0,
        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
    )


def show_window(hwnd: int) -> None:
    hwnd = resolve_hwnd(hwnd)
    if not hwnd:
        return
    user32.SetWindowPos(
        wintypes.HWND(hwnd), None, 0, 0, 0, 0,
        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW,
    )


def window_rect(hwnd: int):
    """返回 (x, y, w, h)，读取真实窗口位置（Tk 在拖拽期间可能滞后）。"""
    hwnd = resolve_hwnd(hwnd)
    if not hwnd:
        return None
    rect = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
