"""
服务器流量 API 采集（后台线程，不阻塞 UI）。

策略:
- 默认每 N 分钟拉取一次（设置可改）
- 失败则沿用上次成功数据
- 连续失败 10 次 → 状态 failed，界面显示「获取数据失败」
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone, timedelta
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TrafficInfo:
    upload: int = 0
    download: int = 0
    total: int = 0
    expire: int = 0
    ok: bool = False
    error: str = ""
    # ok | cached | failed | pending | disabled | unconfigured
    status: str = "pending"

    @property
    def used(self) -> int:
        return max(0, self.upload + self.download)

    @property
    def percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return min(100.0, self.used * 100.0 / self.total)


def parse_traffic_body(text: str) -> TrafficInfo:
    data = {}
    for part in (text or "").replace("\n", "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip().lower(), v.strip()
        try:
            data[k] = int(float(v))
        except ValueError:
            continue
    info = TrafficInfo(
        upload=int(data.get("upload", 0)),
        download=int(data.get("download", 0)),
        total=int(data.get("total", 0)),
        expire=int(data.get("expire", 0)),
        ok=True,
        status="ok",
    )
    if info.total <= 0:
        info.ok = False
        info.status = "failed"
        info.error = "invalid total"
    return info


def format_reset_days(expire: int) -> str:
    """用北京时间计算距流量重置日还有几天。expire 为 Unix 秒（兼容毫秒）。"""
    try:
        ts = float(expire)
        if ts <= 0:
            return "—"
        if ts > 10_000_000_000:
            ts /= 1000.0
        bj = timezone(timedelta(hours=8))
        reset_date = datetime.fromtimestamp(ts, timezone.utc).astimezone(bj).date()
        today = datetime.now(bj).date()
        return f"{max(0, (reset_date - today).days)}天后重置"
    except (TypeError, ValueError, OverflowError, OSError):
        return "—"


def fetch_traffic(
    url: str,
    proxy: Optional[str] = None,
    timeout: float = 15.0,
) -> TrafficInfo:
    """先走代理，失败再直连。url 为空时直接返回 unconfigured，不发请求。"""
    if not (url or "").strip():
        return TrafficInfo(
            ok=False, status="unconfigured", error="traffic.url 未配置"
        )

    proxies_try = []
    if proxy:
        proxies_try.append({"http": proxy, "https": proxy})
    proxies_try.append({})

    last_err = ""
    for px in proxies_try:
        try:
            if px:
                handler = urllib.request.ProxyHandler(px)
            else:
                handler = urllib.request.ProxyHandler({})
            opener = urllib.request.build_opener(handler)
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 GlassMonitor/1.1",
                    "Accept": "*/*",
                },
            )
            with opener.open(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            return parse_traffic_body(body)
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            continue
    return TrafficInfo(ok=False, status="failed", error=last_err)


class TrafficCollector:
    """低频轮询 + 失败缓存。"""

    def __init__(
        self,
        url: str,
        proxy: Optional[str] = None,
        interval_min: float = 5.0,
        max_fails: int = 10,
        enabled: bool = True,
    ):
        self.url = url
        self.proxy = proxy
        self.interval_min = max(1.0, float(interval_min))
        self.max_fails = max(1, int(max_fails))
        self.enabled = enabled
        self._lock = threading.Lock()
        self._info = TrafficInfo(ok=False, status="pending", error="pending")
        self._last_ok: Optional[TrafficInfo] = None
        self._fail_count = 0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lifecycle_lock = threading.Lock()
        self._restart_requested = False
        self._generation = 0

    @property
    def interval_sec(self) -> float:
        return self.interval_min * 60.0

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                enabled = self.enabled
            if not enabled:
                return
            if self._thread and self._thread.is_alive():
                if self._stop.is_set():
                    self._restart_requested = True
                return
            self._restart_requested = False
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="traffic-api", daemon=True
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

    def configure(
        self,
        *,
        url: Optional[str] = None,
        proxy: Optional[str] = None,
        interval_min: Optional[float] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        with self._lock:
            changed = False
            if url is not None:
                changed = changed or self.url != url
                self.url = url
            if proxy is not None:
                changed = changed or self.proxy != (proxy or None)
                self.proxy = proxy or None
            if interval_min is not None:
                new_interval = max(1.0, float(interval_min))
                changed = changed or self.interval_min != new_interval
                self.interval_min = new_interval
            if enabled is not None:
                changed = changed or self.enabled != bool(enabled)
                self.enabled = bool(enabled)
                if not self.enabled:
                    self._info = TrafficInfo(ok=False, status="disabled", error="disabled")
            if changed:
                self._generation += 1
        # 立即唤醒重拉
        self._wake.set()

    def get(self) -> TrafficInfo:
        with self._lock:
            return copy.copy(self._info)

    def _apply_result(self, info: TrafficInfo, generation: int) -> None:
        with self._lock:
            if not self.enabled:
                self._info = TrafficInfo(ok=False, status="disabled", error="disabled")
                return
            if generation != self._generation:
                return
            if info.ok:
                self._fail_count = 0
                self._last_ok = copy.copy(info)
                info.status = "ok"
                self._info = info
            else:
                self._fail_count += 1
                if self._last_ok is not None and self._fail_count < self.max_fails:
                    cached = copy.copy(self._last_ok)
                    cached.status = "cached"
                    cached.error = info.error
                    # ok 仍为 True，界面继续显示上次数据
                    cached.ok = True
                    self._info = cached
                else:
                    self._info = TrafficInfo(
                        ok=False,
                        status="failed",
                        error="获取数据失败",
                    )

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                with self._lock:
                    enabled = self.enabled
                    url, proxy = self.url, self.proxy
                    wait_sec = self.interval_sec
                    generation = self._generation

                if not enabled:
                    with self._lock:
                        self._info = TrafficInfo(
                            ok=False, status="disabled", error="disabled"
                        )
                elif not (url or "").strip():
                    # 没配地址就别去请求空 URL，也不要走失败计数。
                    with self._lock:
                        self._info = TrafficInfo(
                            ok=False, status="unconfigured", error="traffic.url 未配置"
                        )
                else:
                    info = fetch_traffic(url, proxy)
                    self._apply_result(info, generation)

                self._wake.clear()
                with self._lock:
                    stale = generation != self._generation
                if self._stop.is_set() or stale:
                    continue
                # 可被 configure 提前唤醒
                self._wake.wait(timeout=wait_sec)
        finally:
            restart = False
            with self._lifecycle_lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                with self._lock:
                    enabled = self.enabled
                restart = self._restart_requested and enabled
                self._restart_requested = False
            if restart:
                self.start()


def format_bytes(n: int) -> str:
    n = float(max(0, n))
    for unit, div in (("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.2f} {unit}"
    return f"{int(n)} B"


def traffic_bar_color(percent: float, style: Optional[dict] = None) -> str:
    """<50% 绿, 50–70% 黄, >70% 红。"""
    style = style or {}
    if percent < 50:
        return style.get("traffic_green", "#32D74B")
    if percent <= 70:
        return style.get("traffic_yellow", "#FFD60A")
    return style.get("traffic_red", "#FF453A")
