"""Visual QA：启动 App，检查单窗口分层渲染是否正常，然后退出。

新架构（单窗口 + UpdateLayeredWindow 真 alpha）下要验证的是：
- 只有一个可见顶层窗，且带 WS_EX_LAYERED | WS_EX_NOACTIVATE
- 合成帧的圆角/文字边缘是**渐变 alpha**（不是 0/255 硬边）→ 任何底色下都不锯齿
- 卡片区域按 card_opacity 半透明，间隙 alpha=0（点击穿透）
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parent

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000


def analyze_frame(img) -> dict:
    """统计 alpha 分布：软边像素越多，说明抗锯齿是真 alpha 而非色键。"""
    alpha = img.getchannel("A")
    hist = alpha.histogram()
    total = sum(hist)
    transparent = hist[0]
    opaque = hist[255]
    soft = total - transparent - opaque
    return {
        "size": img.size,
        "transparent_ratio": round(transparent / total, 4),
        "opaque_ratio": round(opaque / total, 4),
        "soft_edge_ratio": round(soft / total, 4),
        "corner_alpha": alpha.getpixel((0, 0)),
    }


def main() -> int:
    log = ROOT / "verify_log.txt"
    lines = []

    def logp(msg: str) -> None:
        lines.append(str(msg))
        print(msg, flush=True)

    try:
        from glass_ui import GlassMonitorApp

        app = GlassMonitorApp()
        app.root.update_idletasks()
        app.root.update()

        logp(f"created W={app.W} H={app.H} visible={app._visible}")
        logp(f"shell_op={app.shell_opacity} card_op={app.card_opacity} "
             f"margin={app.content_margin} halo={app.halo}")
        logp(f"cards={app._layout()['cards']}")

        ok = True

        # 1) 扩展样式
        user32 = ctypes.windll.user32
        user32.GetWindowLongPtrW.restype = ctypes.c_longlong
        ex = int(user32.GetWindowLongPtrW(wt.HWND(app._hwnd), GWL_EXSTYLE)) & 0xFFFFFFFF
        logp(f"hwnd={app._hwnd} exstyle=0x{ex:08X} "
             f"layered={bool(ex & WS_EX_LAYERED)} noactivate={bool(ex & WS_EX_NOACTIVATE)}")
        if not (ex & WS_EX_LAYERED and ex & WS_EX_NOACTIVATE):
            logp("FAIL 缺少 WS_EX_LAYERED / WS_EX_NOACTIVATE")
            ok = False
        else:
            logp("PASS 扩展样式正确（分层 + 不可激活）")

        # 2) 合成帧的 alpha 分布
        frame = app._compose()
        stats = analyze_frame(frame)
        logp(f"stats={stats}")
        if stats["soft_edge_ratio"] < 0.01:
            logp(f"FAIL 软边像素过少 {stats['soft_edge_ratio']}，可能退化成硬边色键")
            ok = False
        else:
            logp(f"PASS 软边 alpha 占比 {stats['soft_edge_ratio']}")
        if stats["corner_alpha"] != 0:
            logp(f"FAIL 左上角 alpha={stats['corner_alpha']}，圆角外应完全透明")
            ok = False
        else:
            logp("PASS 圆角外 alpha=0（点击穿透）")

        frame.save(ROOT / "verify_frame.png")
        logp("saved verify_frame.png")

        app.root.after(200, app.close)
        app.run()
        logp("closed ok" if ok else "closed with FAIL")
        log.write_text("\n".join(lines), encoding="utf-8")
        return 0 if ok else 2
    except Exception:
        logp(traceback.format_exc())
        log.write_text("\n".join(lines), encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
