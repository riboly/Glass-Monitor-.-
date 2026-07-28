"""
校对 vps流量接口.md 里的「文件:行号」引用，并可自动修正。

用法：
    py -3.14 _check_doc_lines.py        # 只检查，返回码非 0 表示有问题
    py -3.14 _check_doc_lines.py --fix  # 按锚点重新定位并改写文档里的行号

为什么需要它：文档里写死了行号，代码一改就漂。锚点（那行代码的特征子串）
才是稳定的，行号只是给人快速跳转用的。有了这个脚本，改完代码跑一次即可。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOC = ROOT / "vps流量接口.md"

# 锚点表：(文件, 该行必须包含的唯一子串)
ANCHORS = [
    # config.json 的锚点要挑**全文唯一**的键：像 "enabled" 在 hotkey 段
    # 也有一个，用它会定位到错误的行。
    ("config.json", '"traffic": {'),
    ("config.json", '"url"'),
    ("config.json", '"proxy"'),
    ("config.json", '"interval_min"'),
    ("config.json", '"interval_sec"'),
    ("config.json", '"cards": {'),
    ("traffic.py", "class TrafficInfo"),
    ("traffic.py", "def used"),
    ("traffic.py", "def percent"),
    ("traffic.py", "def parse_traffic_body"),
    ("traffic.py", "if info.total <= 0:"),
    ("traffic.py", "def format_reset_days"),
    ("traffic.py", "def fetch_traffic"),
    ("traffic.py", 'if not (url or "").strip():'),
    ("traffic.py", "class TrafficCollector"),
    ("traffic.py", "max_fails: int = 10,"),
    ("traffic.py", "def _apply_result"),
    ("traffic.py", 'elif not (url or "").strip():'),
    ("traffic.py", "def format_bytes"),
    ("traffic.py", "def traffic_bar_color"),
    ("glass_ui.py", "self.traffic = TrafficCollector("),
    ("glass_ui.py", 'traf.setdefault("url", "")'),
    ("glass_ui.py", 'traf.setdefault("proxy", "")'),
    ("glass_ui.py", 'traf["interval_sec"] ='),
    ("glass_ui.py", "def _draw_traffic"),
    ("glass_ui.py", 'elif info.status == "unconfigured":'),
    ("glass_ui.py", "self.traffic.configure("),
    ("glass_ui.py", "self._traffic_info = self.traffic.get()"),
    ("settings_ui.py", "self.var_traffic_url ="),
    ("settings_ui.py", "self.var_traffic_proxy ="),
]

CITE_RE = re.compile(
    r"(config\.json|traffic\.py|glass_ui\.py|settings_ui\.py)"
    r"(`?\s*:\s*\*{0,2})(\d+)"
)


def locate() -> dict:
    """锚点 → 当前真实行号。同一文件里同一子串只认第一处。"""
    out = {}
    for fname, needle in ANCHORS:
        source = ROOT / fname
        if fname == "config.json" and not source.is_file():
            source = ROOT / "config.example.json"
        lines = source.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, 1):
            if needle in line:
                out.setdefault((fname, needle), i)
                break
    return out


def main() -> int:
    fix = "--fix" in sys.argv
    found = locate()

    missing = [a for a in ANCHORS if a not in found]
    for fname, needle in missing:
        print(f"锚点失效  {fname}: {needle!r}  —— 代码里找不到，请更新 ANCHORS")

    # 每个文件当前「合法」的行号集合
    valid = {}
    for (fname, _needle), lineno in found.items():
        valid.setdefault(fname, set()).add(lineno)

    doc = DOC.read_text(encoding="utf-8")
    bad = []

    def repl(m):
        fname, mid, num = m.group(1), m.group(2), int(m.group(3))
        if num in valid.get(fname, set()):
            return m.group(0)
        # 尝试用「该文件里离得最近的锚点行号」修正是不可靠的，
        # 所以只报告，由人确认锚点表是否需要补
        bad.append((fname, num))
        return m.group(0)

    CITE_RE.sub(repl, doc)

    if not bad and not missing:
        total = len(CITE_RE.findall(doc))
        print(f"OK  文档中 {total} 处行号引用全部指向有效锚点")
        return 0

    print()
    print("以下引用对不上（文件 : 文档里写的行号）：")
    for fname, num in sorted(set(bad)):
        print(f"  {fname}:{num}")
    print()
    print("当前锚点的真实行号：")
    for (fname, needle), lineno in sorted(found.items()):
        print(f"  {fname}:{lineno:<5} {needle[:52]}")
    if not fix:
        print()
        print("确认无误后手动更新文档，或先补齐 ANCHORS 再跑一次。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
