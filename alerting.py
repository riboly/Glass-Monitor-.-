"""资源阈值告警状态机。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass
class _State:
    exceeded_since: Optional[float] = None
    active: bool = False
    last_notified: float = -1e12


class AlertManager:
    LABELS = {
        "cpu_temp": ("CPU 温度", "°C"),
        "gpu_temp": ("GPU 温度", "°C"),
        "memory": ("内存占用", "%"),
        "disk": ("磁盘占用", "%"),
        "traffic": ("VPS 流量", "%"),
    }

    def __init__(self) -> None:
        self._states = {key: _State() for key in self.LABELS}

    def reset(self) -> None:
        self._states = {key: _State() for key in self.LABELS}

    def evaluate(
        self,
        values: Mapping[str, Optional[float]],
        config: Mapping[str, object],
        now: Optional[float] = None,
    ) -> list[str]:
        if not bool(config.get("enabled", False)):
            self.reset()
            return []
        current = time.monotonic() if now is None else float(now)
        duration = max(0.0, float(config.get("duration_sec", 15)))
        cooldown = max(30.0, float(config.get("cooldown_sec", 300)))
        hysteresis = max(0.0, float(config.get("hysteresis", 3)))
        messages: list[str] = []

        for key, (label, unit) in self.LABELS.items():
            state = self._states[key]
            value = values.get(key)
            threshold = float(config.get(key, 0) or 0)
            if value is None or threshold <= 0:
                state.exceeded_since = None
                state.active = False
                continue
            numeric = float(value)
            if numeric >= threshold:
                if state.exceeded_since is None:
                    state.exceeded_since = current
                ready = current - state.exceeded_since >= duration
                cooled = current - state.last_notified >= cooldown
                if ready and not state.active and cooled:
                    messages.append(
                        f"{label}达到 {numeric:.0f}{unit}，阈值为 {threshold:.0f}{unit}"
                    )
                    state.active = True
                    state.last_notified = current
            elif numeric <= threshold - hysteresis:
                state.exceeded_since = None
                state.active = False
        return messages
