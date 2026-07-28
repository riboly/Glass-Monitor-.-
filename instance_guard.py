"""Windows 单实例保护。"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    def __init__(self, name: str = "Local\\GlassMonitor.Desktop") -> None:
        self._handle = None
        self.acquired = True
        if os.name != "nt":
            return

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            self.acquired = False
            return
        self._handle = handle
        self.acquired = ctypes.get_last_error() != ERROR_ALREADY_EXISTS
        if not self.acquired:
            kernel32.CloseHandle(handle)
            self._handle = None

    def close(self) -> None:
        if self._handle is not None:
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None

