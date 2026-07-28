"""
低开销硬件 / 网络指标采集。

- CPU / 内存: psutil（非阻塞）
- GPU: NVML
- CPU 温度: 内置 OpenHardwareMonitor 小助手（常驻 serve，读 stdout）
- 网速: net_io_counters 差分
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Optional, Tuple

import psutil

ROOT = Path(__file__).resolve().parent
CPU_TEMP_HELPER = ROOT / "bin" / "cpu_temp_helper.exe"

# ---------------------------------------------------------------------------
# GPU：NVML（与 nvidia-smi 同源）读独显占用 + 温度
#
# 说明（深度结论）：
# 1) 任务管理器 GPU% 来自 WDDM 引擎计数器；NVML 是 GPU SM 时间占比。
# 2) 本机实测 typeperf 聚合曾出现 8% vs 任务管理器 31% 的严重偏低，不可用。
# 3) NVML 与 nvidia-smi 数值一致，且只枚举 NVIDIA 设备 → 一定是独显不是核显。
# 4) 与任务管理器仍可能有数个百分点差异（采样窗口/算法不同），属正常。
# ---------------------------------------------------------------------------
_nvml_ok = False
_nvml_handle = None
_nvml_name = ""
_nvml_module = None
_nvml_attempted = False
_nvml_lock = threading.Lock()
_gpu_ema: Optional[float] = None
_GPU_EMA_ALPHA = 0.45  # 轻微平滑，避免跳变过大


def _pick_discrete_nvml_handle(pynvml):
    """在多块 NVIDIA 中优先选 GeForce/RTX 等独显。"""
    count = int(pynvml.nvmlDeviceGetCount())
    best_i, best_score = 0, -1
    best_name = ""
    for i in range(count):
        h = pynvml.nvmlDeviceGetHandleByIndex(i)
        name = pynvml.nvmlDeviceGetName(h)
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="ignore")
        mem = int(pynvml.nvmlDeviceGetMemoryInfo(h).total)
        low = name.lower()
        score = mem // (1024 * 1024)
        if any(
            k in low
            for k in ("geforce", "rtx", "gtx", "quadro", "tesla", "titan", "rtx a")
        ):
            score += 10_000_000
        if "nvidia" in low:
            score += 1_000_000
        if any(k in low for k in ("virtual", "microsoft", "basic", "mumu")):
            score -= 5_000_000
        if score > best_score:
            best_score, best_i, best_name = score, i, name
    return pynvml.nvmlDeviceGetHandleByIndex(best_i), best_name


def _ensure_nvml() -> bool:
    global _nvml_ok, _nvml_handle, _nvml_name, _nvml_module, _nvml_attempted
    if _nvml_ok and _nvml_handle is not None:
        return True
    with _nvml_lock:
        if _nvml_ok and _nvml_handle is not None:
            return True
        if _nvml_attempted:
            return False
        _nvml_attempted = True
        try:
            import pynvml

            pynvml.nvmlInit()
            _nvml_handle, _nvml_name = _pick_discrete_nvml_handle(pynvml)
            _nvml_module = pynvml
            _nvml_ok = True
            return True
        except Exception:
            _nvml_ok = False
            _nvml_handle = None
            _nvml_name = ""
            _nvml_module = None
            return False


def gpu_memory() -> Tuple[Optional[float], Optional[float]]:
    """返回 (显存已用 GB, 显存总量 GB)。"""
    if not _ensure_nvml() or _nvml_handle is None or _nvml_module is None:
        return None, None
    try:
        info = _nvml_module.nvmlDeviceGetMemoryInfo(_nvml_handle)
        g = 1024.0 ** 3
        return float(info.used) / g, float(info.total) / g
    except Exception:
        return None, None


def gpu_name() -> str:
    return _nvml_name or ""


def _gpu_stats() -> Tuple[Optional[float], Optional[float]]:
    """返回 (独显占用%, 温度°C) — NVML / 等同 nvidia-smi。"""
    global _gpu_ema
    if not _ensure_nvml() or _nvml_handle is None or _nvml_module is None:
        return None, None
    try:
        util = float(_nvml_module.nvmlDeviceGetUtilizationRates(_nvml_handle).gpu)
        temp = float(
            _nvml_module.nvmlDeviceGetTemperature(
                _nvml_handle, _nvml_module.NVML_TEMPERATURE_GPU
            )
        )
        # 轻微 EMA，减轻 1Hz 刷新时的锯齿跳变（不大幅偏离真实值）
        if _gpu_ema is None:
            _gpu_ema = util
        else:
            _gpu_ema = _GPU_EMA_ALPHA * util + (1.0 - _GPU_EMA_ALPHA) * _gpu_ema
        return max(0.0, min(100.0, _gpu_ema)), temp
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# CPU 温度：cpu_temp_helper.exe serve
# ---------------------------------------------------------------------------
_cpu_temp_cache: Optional[float] = None
_cpu_temp_lock = threading.Lock()
_cpu_temp_proc: Optional[subprocess.Popen] = None
_cpu_temp_thread_started = False


def _start_cpu_temp_helper() -> None:
    """启动常驻助手；失败则静默（UI 显示 --）。"""
    global _cpu_temp_proc, _cpu_temp_cache
    if not CPU_TEMP_HELPER.is_file():
        return
    try:
        # CREATE_NO_WINDOW = 0x08000000
        creation = 0x08000000 if os.name == "nt" else 0
        _cpu_temp_proc = subprocess.Popen(
            [str(CPU_TEMP_HELPER), "serve"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(CPU_TEMP_HELPER.parent),
            text=True,
            encoding="utf-8",
            errors="ignore",
            bufsize=1,
            creationflags=creation,
        )
    except Exception:
        _cpu_temp_proc = None


def _cpu_temp_reader() -> None:
    global _cpu_temp_cache
    _start_cpu_temp_helper()
    proc = _cpu_temp_proc
    if proc is None or proc.stdout is None:
        return
    try:
        for line in proc.stdout:
            line = (line or "").strip()
            if not line or line.upper() == "NA":
                continue
            try:
                val = float(line)
            except ValueError:
                continue
            if 0 < val <= 125:
                with _cpu_temp_lock:
                    _cpu_temp_cache = val
    except Exception:
        pass


def _ensure_cpu_temp_thread() -> None:
    global _cpu_temp_thread_started
    if _cpu_temp_thread_started:
        return
    _cpu_temp_thread_started = True
    t = threading.Thread(target=_cpu_temp_reader, name="cpu-temp", daemon=True)
    t.start()


def get_cpu_temp(force: bool = False) -> Optional[float]:
    _ensure_cpu_temp_thread()
    with _cpu_temp_lock:
        return _cpu_temp_cache


def shutdown_cpu_temp() -> None:
    global _cpu_temp_proc, _cpu_temp_thread_started, _cpu_temp_cache
    proc = _cpu_temp_proc
    _cpu_temp_proc = None
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=1.5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    with _cpu_temp_lock:
        _cpu_temp_cache = None
    _cpu_temp_thread_started = False


# ---------------------------------------------------------------------------
# 采样
# ---------------------------------------------------------------------------
@dataclass
class Sample:
    ts: float
    cpu: float
    mem: float
    mem_used_gb: float
    mem_total_gb: float
    gpu: Optional[float]
    cpu_temp: Optional[float]
    gpu_temp: Optional[float]
    net_up_bps: float
    net_down_bps: float
    disk_read_bps: float = 0.0
    disk_write_bps: float = 0.0


@dataclass
class PartInfo:
    """一个磁盘分区。"""
    label: str          # "C:"
    percent: float
    used_gb: float
    total_gb: float


@dataclass
class ProcInfo:
    """一个进程（按 CPU 排序取前几名）。"""
    pid: int
    name: str
    cpu: float          # 占**整机** CPU 的百分比（与任务管理器口径一致）
    mem_mb: float


@dataclass
class BatteryInfo:
    percent: float
    plugged: bool
    secs_left: Optional[int]


def uptime_seconds() -> float:
    try:
        return max(0.0, time.time() - psutil.boot_time())
    except Exception:
        return 0.0


def format_uptime(sec: float) -> str:
    sec = int(max(0, sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}天{h}小时"
    if h:
        return f"{h}小时{m}分"
    return f"{m}分钟"


def battery() -> Optional[BatteryInfo]:
    """台式机返回 None（卡片会自动省掉这一行）。"""
    try:
        b = psutil.sensors_battery()
    except Exception:
        return None
    if b is None:
        return None
    secs = None
    if b.secsleft is not None and b.secsleft >= 0:
        secs = int(b.secsleft)
    return BatteryInfo(float(b.percent), bool(b.power_plugged), secs)


@dataclass
class MetricsCollector:
    history_points: int = 60
    _last_net: Optional[Tuple[int, int]] = None
    _last_net_t: float = 0.0
    _net_active: bool = False
    history: Deque[Sample] = field(default_factory=deque)
    # 曲线展示用 EMA，减少毛刺
    _ema_up: float = 0.0
    _ema_down: float = 0.0
    _ema_alpha: float = 0.35
    history_smooth: Deque[Tuple[float, float]] = field(default_factory=deque)
    # 磁盘 I/O 差分
    _last_disk: Optional[Tuple[int, int]] = None
    _last_disk_t: float = 0.0
    _disk_active: bool = False
    # 低频缓存：分区列表 / 进程 TOP（避免每秒扫全盘、扫全部进程）
    _parts_cache: list = field(default_factory=list)
    _parts_t: float = -1e9
    _parts_interval: float = 60.0
    _procs: dict = field(default_factory=dict)   # pid -> (Process, name)
    _proc_cache: list = field(default_factory=list)
    _proc_t: float = -1e9
    _proc_interval: float = 3.0

    def __post_init__(self) -> None:
        self.history = deque(maxlen=self.history_points)
        self.history_smooth = deque(maxlen=self.history_points)

    # ---------------- 磁盘分区（低频，60s） ----------------
    def disk_parts(self, limit: int = 3) -> list:
        """
        返回占用率最高的前 limit 个**本地固定**分区。

        每 60 秒才真正扫一次：disk_usage 对每个分区都是一次系统调用，
        光驱/未挂载盘还可能卡住，不能放进 1Hz 热路径。
        """
        now = time.monotonic()
        if now - self._parts_t < self._parts_interval and self._parts_cache:
            return self._parts_cache[:limit]
        self._parts_t = now
        out = []
        try:
            for p in psutil.disk_partitions(all=False):
                opts = (p.opts or "").lower()
                if "cdrom" in opts or "removable" in opts or not p.fstype:
                    continue
                try:
                    u = psutil.disk_usage(p.mountpoint)
                except Exception:
                    continue
                g = 1024.0 ** 3
                label = p.mountpoint.rstrip("\\/") or p.mountpoint
                out.append(PartInfo(label, float(u.percent),
                                    u.used / g, u.total / g))
        except Exception:
            pass
        out.sort(key=lambda x: x.percent, reverse=True)
        self._parts_cache = out
        return out[:limit]

    # ---------------- 进程 TOP（低频，3s） ----------------
    def top_processes(self, limit: int = 3) -> list:
        """
        按 CPU 占用排序的前 limit 个进程。

        两点性能考虑：
        1) 必须复用同一批 Process 对象——psutil 的 cpu_percent 是「距上次调用」
           的增量，每次新建对象只会得到 0.0。
        2) 先只取 CPU 排序，**排完序才对前几名查内存**；memory_info 每个进程
           一次系统调用，全量查 300 个进程纯属浪费。
        """
        now = time.monotonic()
        if now - self._proc_t < self._proc_interval and self._proc_cache:
            return self._proc_cache[:limit]
        first_run = self._proc_t < -1e8
        self._proc_t = now

        ncpu = psutil.cpu_count() or 1
        seen = set()
        rows = []
        for p in psutil.process_iter(["pid", "name"]):
            pid = p.info.get("pid")
            if pid is None or pid == 0:
                continue
            seen.add(pid)
            entry = self._procs.get(pid)
            if entry is None:
                # 新进程：先建基线，下一轮才有有效读数
                self._procs[pid] = (p, p.info.get("name") or str(pid))
                try:
                    p.cpu_percent(None)
                except Exception:
                    self._procs.pop(pid, None)
                continue
            proc, name = entry
            try:
                rows.append([proc.cpu_percent(None) / ncpu, pid, name, proc])
            except Exception:
                self._procs.pop(pid, None)
        for pid in [k for k in self._procs if k not in seen]:
            self._procs.pop(pid, None)

        rows.sort(key=lambda r: r[0], reverse=True)
        top = []
        for cpu, pid, name, proc in rows[: max(limit, 1)]:
            try:
                mem_mb = proc.memory_info().rss / 1048576.0
            except Exception:
                mem_mb = 0.0
            top.append(ProcInfo(pid, name, max(0.0, cpu), mem_mb))
        self._proc_cache = top
        if first_run:
            # 首轮只建立了基线，让下一次 tick 就能刷新，而不用等满 3 秒
            self._proc_t = now - self._proc_interval + 1.0
        return top[:limit]

    def sample(self, requirements: Optional[dict] = None) -> Sample:
        needs = requirements if requirements is not None else {
            "basic": True,
            "gpu_stats": True,
            "cpu_temp": True,
            "network": True,
            "disk_io": True,
        }
        now = time.monotonic()
        if needs.get("basic"):
            cpu = float(psutil.cpu_percent(interval=None))
            vm = psutil.virtual_memory()
            mem = float(vm.percent)
            mem_used_gb = float(vm.used) / (1024.0 ** 3)
            mem_total_gb = float(vm.total) / (1024.0 ** 3)
        else:
            cpu = mem = mem_used_gb = mem_total_gb = 0.0
        gpu, gpu_temp = _gpu_stats() if needs.get("gpu_stats") else (None, None)
        cpu_temp = get_cpu_temp() if needs.get("cpu_temp") else None

        up_bps = down_bps = 0.0
        if needs.get("network"):
            io = psutil.net_io_counters()
            sent, recv = io.bytes_sent, io.bytes_recv
            if self._net_active and self._last_net is not None:
                dt = max(now - self._last_net_t, 1e-6)
                up_bps = max(0.0, (sent - self._last_net[0]) / dt)
                down_bps = max(0.0, (recv - self._last_net[1]) / dt)
            else:
                self.history_smooth.clear()
                self._ema_up = self._ema_down = 0.0
            self._last_net = (sent, recv)
            self._last_net_t = now
            self._net_active = True
            a = self._ema_alpha
            self._ema_up = a * up_bps + (1 - a) * self._ema_up
            self._ema_down = a * down_bps + (1 - a) * self._ema_down
            self.history_smooth.append((self._ema_up, self._ema_down))
        else:
            self._net_active = False
            self._last_net = None

        # 磁盘 I/O 速率（全盘聚合，差分）
        read_bps = write_bps = 0.0
        if needs.get("disk_io"):
            try:
                d = psutil.disk_io_counters()
                if d is not None:
                    if self._disk_active and self._last_disk is not None:
                        dt = max(now - self._last_disk_t, 1e-6)
                        read_bps = max(0.0, (d.read_bytes - self._last_disk[0]) / dt)
                        write_bps = max(0.0, (d.write_bytes - self._last_disk[1]) / dt)
                    self._last_disk = (d.read_bytes, d.write_bytes)
                    self._last_disk_t = now
                    self._disk_active = True
            except Exception:
                pass
        else:
            self._disk_active = False
            self._last_disk = None

        s = Sample(
            ts=now,
            cpu=cpu,
            mem=mem,
            mem_used_gb=mem_used_gb,
            mem_total_gb=mem_total_gb,
            gpu=gpu,
            cpu_temp=cpu_temp,
            gpu_temp=gpu_temp,
            net_up_bps=up_bps,
            net_down_bps=down_bps,
            disk_read_bps=read_bps,
            disk_write_bps=write_bps,
        )
        self.history.append(s)
        return s

    def net_history(self) -> Tuple[list, list]:
        if self.history_smooth:
            ups = [u for u, _ in self.history_smooth]
            downs = [d for _, d in self.history_smooth]
            return ups, downs
        ups = [x.net_up_bps for x in self.history]
        downs = [x.net_down_bps for x in self.history]
        return ups, downs


def format_speed(bps: float) -> str:
    if bps < 1024:
        return f"{bps:.0f} B/s"
    kb = bps / 1024.0
    if kb < 1024:
        return f"{kb:.1f} KB/s"
    mb = kb / 1024.0
    if mb < 1024:
        return f"{mb:.2f} MB/s"
    return f"{mb / 1024.0:.2f} GB/s"


def format_temp(t: Optional[float]) -> str:
    if t is None:
        return "—"
    return f"{t:.0f}°"


def shutdown_gpu() -> None:
    global _nvml_ok, _nvml_handle, _nvml_name, _nvml_module, _nvml_attempted, _gpu_ema
    with _nvml_lock:
        if _nvml_ok and _nvml_module is not None:
            try:
                _nvml_module.nvmlShutdown()
            except Exception:
                pass
        _nvml_ok = False
        _nvml_handle = None
        _nvml_name = ""
        _nvml_module = None
        _nvml_attempted = False
        _gpu_ema = None


def shutdown_nvml() -> None:
    shutdown_gpu()
    try:
        from gpu_wddm import shutdown_wddm_gpu

        shutdown_wddm_gpu()
    except Exception:
        pass
    shutdown_cpu_temp()
