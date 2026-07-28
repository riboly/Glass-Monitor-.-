"""
全局快捷键管理（keyboard 库）。
在后台线程注册，回调投递到 UI 线程由调用方负责。
"""

from __future__ import annotations

import threading
from typing import Callable, Optional


class HotkeyManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Optional[str] = None
        self._callback: Optional[Callable[[], None]] = None
        self._ok = False
        try:
            import keyboard  # noqa: F401

            self._ok = True
        except Exception:
            self._ok = False

    @property
    def available(self) -> bool:
        return self._ok

    def set_hotkey(self, combo: Optional[str], callback: Callable[[], None]) -> str:
        """
        注册热键。combo 为空则清除。
        返回状态信息（空字符串表示成功）。
        """
        if not self._ok:
            return "未安装 keyboard 库"
        import keyboard

        with self._lock:
            try:
                keyboard.clear_all_hotkeys()
            except Exception:
                pass
            self._current = None
            self._callback = callback
            combo = (combo or "").strip()
            if not combo:
                return ""
            try:
                keyboard.add_hotkey(
                    combo,
                    lambda: self._fire(),
                    suppress=False,
                    trigger_on_release=False,
                )
                self._current = combo
                return ""
            except Exception as e:
                return f"快捷键无效: {e}"

    def _fire(self) -> None:
        cb = self._callback
        if cb:
            try:
                cb()
            except Exception:
                pass

    def clear(self) -> None:
        if not self._ok:
            return
        import keyboard

        with self._lock:
            try:
                keyboard.clear_all_hotkeys()
            except Exception:
                pass
            self._current = None

    @staticmethod
    def capture_once(timeout: float = 8.0) -> Optional[str]:
        """阻塞读取一次组合键（在工作线程调用）。"""
        try:
            import keyboard

            return keyboard.read_hotkey(suppress=False)
        except Exception:
            return None
