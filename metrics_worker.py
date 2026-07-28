"""后台指标采集，Tk 主线程只读取最近一次完整快照。"""

from __future__ import annotations

import copy
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Optional

from metrics import (
    MetricsCollector,
    Sample,
    battery,
    shutdown_cpu_temp,
    shutdown_gpu,
)


@dataclass
class MetricsSnapshot:
    sample: Optional[Sample] = None
    ups: list[float] = field(default_factory=list)
    downs: list[float] = field(default_factory=list)
    parts: list = field(default_factory=list)
    top_procs: list = field(default_factory=list)
    battery_info: object = None
    sampled_at: float = 0.0
    duration_ms: float = 0.0
    error: str = ""


class MetricsWorker:
    def __init__(
        self,
        history_points: int = 60,
        interval_ms: int = 1000,
        collector: Optional[MetricsCollector] = None,
    ) -> None:
        self.collector = collector or MetricsCollector(history_points=history_points)
        self._interval = max(0.5, int(interval_ms) / 1000.0)
        self._requirements: dict[str, bool] = {}
        self._snapshot = MetricsSnapshot()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lifecycle_lock = threading.Lock()
        self._restart_requested = False
        self._revision = 0
        self._battery_t = -1e9

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                enabled = any(self._requirements.values())
            if not enabled:
                return
            if self._thread and self._thread.is_alive():
                if self._stop.is_set():
                    self._restart_requested = True
                return
            self._restart_requested = False
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="system-metrics", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lifecycle_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def configure(self, requirements: dict[str, bool], interval_ms: int) -> None:
        with self._lock:
            self._requirements = {
                key: bool(value) for key, value in requirements.items()
            }
            self._interval = max(0.5, int(interval_ms) / 1000.0)
            self._revision += 1
        self._wake.set()

    def get(self) -> MetricsSnapshot:
        with self._lock:
            return copy.copy(self._snapshot)

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                with self._lock:
                    requirements = dict(self._requirements)
                    previous = self._snapshot
                    interval = self._interval
                    revision = self._revision
                self._release_unused(requirements)
                try:
                    sample = self.collector.sample(requirements)
                    if requirements.get("network"):
                        ups, downs = self.collector.net_history()
                    else:
                        ups, downs = [], []
                    parts = (
                        self.collector.disk_parts(3)
                        if requirements.get("disk_parts") else []
                    )
                    procs = (
                        self.collector.top_processes(3)
                        if requirements.get("processes") else []
                    )
                    battery_info = previous.battery_info
                    now = time.monotonic()
                    if requirements.get("battery"):
                        if now - self._battery_t >= 30.0:
                            battery_info = battery()
                            self._battery_t = now
                    else:
                        battery_info = None
                        self._battery_t = -1e9
                    snap = MetricsSnapshot(
                        sample=sample,
                        ups=list(ups),
                        downs=list(downs),
                        parts=list(parts),
                        top_procs=list(procs),
                        battery_info=battery_info,
                        sampled_at=now,
                        duration_ms=(now - started) * 1000.0,
                    )
                except Exception:
                    snap = copy.copy(previous)
                    snap.error = traceback.format_exc(limit=8)
                    snap.sampled_at = time.monotonic()
                    snap.duration_ms = (snap.sampled_at - started) * 1000.0
                with self._lock:
                    self._snapshot = snap

                elapsed = time.monotonic() - started
                self._wake.clear()
                with self._lock:
                    stale = revision != self._revision
                if self._stop.is_set() or stale:
                    continue
                self._wake.wait(max(0.05, interval - elapsed))
        finally:
            with self._lock:
                requirements = dict(self._requirements)
            self._release_unused(requirements)
            restart = False
            with self._lifecycle_lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                with self._lock:
                    enabled = any(self._requirements.values())
                restart = self._restart_requested and enabled
                self._restart_requested = False
            if restart:
                self.start()

    @staticmethod
    def _release_unused(requirements: dict[str, bool]) -> None:
        if not requirements.get("cpu_temp"):
            shutdown_cpu_temp()
        if not requirements.get("nvml"):
            shutdown_gpu()
