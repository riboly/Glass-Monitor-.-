"""
GPU 占用：已废弃 WDDM/typeperf 路径。

历史问题：
- typeperf 聚合结果经常远低于任务管理器（例如 8% vs 31%）
- 进程级引擎计数器解析/LUID 选择不稳定，还常驻 typeperf 吃资源

现统一由 metrics.py 通过 NVML（与 nvidia-smi 同源）读取独显占用。
本文件仅保留空实现，避免旧 import 报错。
"""

from __future__ import annotations

from typing import Optional


def get_wddm_gpu_util() -> Optional[float]:
    return None


def shutdown_wddm_gpu() -> None:
    return
