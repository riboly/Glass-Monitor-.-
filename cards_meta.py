"""
卡片注册表（元数据）。

单独成模块是为了打破循环导入：`glass_ui` 导入 `settings_ui`，
设置窗又要列出所有卡片，两边都从这里取。

注册顺序是旧配置和新安装的默认排列；用户顺序保存在 `card_order`。
高度不在这里定义 —— disk / sys 是内容驱动的（分区数、有没有电池），
见 `GlassMonitorApp._card_heights`。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# (id, 设置窗里的显示名, 圆角半径)
CARD_SPECS: Tuple[Tuple[str, str, int], ...] = (
    ("clock", "日期时钟（时间 / 日期 / 星期）", 16),
    ("hw", "硬件监控（CPU / 内存 / 显卡）", 16),
    ("speed", "实时网速", 12),
    ("chart", "网速曲线", 14),
    ("disk", "磁盘（读写 + 分区占用）", 14),
    ("proc", "进程 TOP（CPU 前三）", 14),
    ("sys", "系统信息（开机时长 / 显存 / 电池）", 14),
    ("traffic", "VPS 流量", 14),
)

CARD_IDS: Tuple[str, ...] = tuple(c[0] for c in CARD_SPECS)
CARD_NAMES: Dict[str, str] = {c[0]: c[1] for c in CARD_SPECS}
CARD_RADIUS: Dict[str, int] = {c[0]: c[2] for c in CARD_SPECS}


def normalize_card_order(value: object) -> List[str]:
    """返回完整、无重复的卡片顺序，并自动补入新卡片。"""
    if not isinstance(value, (list, tuple)):
        return list(CARD_IDS)

    order: List[str] = []
    seen = set()
    for raw_id in value:
        cid = str(raw_id)
        if cid in CARD_NAMES and cid not in seen:
            order.append(cid)
            seen.add(cid)
    order.extend(cid for cid in CARD_IDS if cid not in seen)
    return order

# 默认只开原有的四张；新卡片按需打开，否则窗口一上来就很高
DEFAULT_CARDS: Dict[str, bool] = {
    "clock": False,
    "hw": True,
    "speed": True,
    "chart": True,
    "disk": False,
    "proc": False,
    "sys": False,
    "traffic": True,
}

ROW_H = 22        # disk / proc / sys 卡片内每一行的高度
CARD_HEAD_H = 28  # 这些卡片标题行占用的高度
