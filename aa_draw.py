"""
真 alpha 抗锯齿绘制工具（Pillow）。

设计原则（浅色桌面锯齿问题的根治办法）
----------------------------------------
旧实现把文字/图形的抗锯齿软边**预先混到卡片底色**上，再用 transparentcolor
色键抠图。色键只有 1-bit alpha：软边像素被烤成了「深色卡片色」的不透明像素。
桌面是深色时刚好看不出来；桌面是浅色时，这些深色描边就变成了明显的锯齿/黑边。

现在所有绘制函数都返回**带 per-pixel alpha 的 RGBA**，由 UpdateLayeredWindow
与桌面真实合成，任何底色下软边都正确。

两条必须遵守的规则：
1. 文字先画到 L 掩膜，再上色 alpha_composite —— 不要直接 draw.text 到透明
   RGBA 上（Pillow 会把 RGB 向黑色混，等于预乘，之后再预乘一次就是黑边）。
2. 图形超采样后**只缩放掩膜**（L 模式），再上色；用 BOX 重采样做精确面积平均，
   既无 LANCZOS 振铃也无颜色渗透。
"""

from __future__ import annotations

import functools
import math
from typing import Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk

RGB = Tuple[int, int, int]
RGBA = Tuple[int, int, int, int]

BOX = Image.Resampling.BOX

# ---------------------------------------------------------------- 颜色


def hex_rgb(h: str) -> RGB:
    h = str(h).lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def hex_rgba(h: str, a: int = 255) -> RGBA:
    r, g, b = hex_rgb(h)
    return r, g, b, max(0, min(255, int(a)))


# 兼容旧名
_hex_rgb = hex_rgb
_hex_rgba = hex_rgba


def blend_hex(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = hex_rgb(c1)
    r2, g2, b2 = hex_rgb(c2)
    t = max(0.0, min(1.0, float(t)))
    return "#%02x%02x%02x" % (
        int(r1 + (r2 - r1) * t),
        int(g1 + (g2 - g1) * t),
        int(b1 + (b2 - b1) * t),
    )


# ---------------------------------------------------------------- 字体

_FONT_CANDIDATES = {
    # (是否含中日韩, 是否粗体)
    (False, False): (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"),
    (False, True): (
        r"C:\Windows\Fonts\seguisb.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
    ),
    (True, False): (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
    ),
    (True, True): (
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    ),
}


def _has_cjk(text: str) -> bool:
    for ch in text:
        o = ord(ch)
        if 0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0xFF00 <= o <= 0xFF60:
            return True
    return False


@functools.lru_cache(maxsize=96)
def _load_font(cjk: bool, bold: bool, size: int) -> ImageFont.FreeTypeFont:
    size = max(6, int(size))
    for path in _FONT_CANDIDATES[(cjk, bold)]:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


def font_for(text: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """中英文分别取最合适的字体；按 (脚本, 粗细, 字号) 缓存。"""
    return _load_font(_has_cjk(text), bool(bold), int(size))


_MEASURE = ImageDraw.Draw(Image.new("L", (1, 1)))


def text_bbox(text: str, size: int, bold: bool = False, anchor: str = "la"):
    font = font_for(text, size, bold)
    return _MEASURE.textbbox((0, 0), text, font=font, anchor=anchor)


def text_width(text: str, size: int, bold: bool = False) -> int:
    bb = text_bbox(text, size, bold)
    return int(math.ceil(bb[2] - bb[0]))


# ---------------------------------------------------------------- 合成原语


def composite_at(base: Image.Image, overlay: Image.Image, x: int, y: int) -> None:
    """把 RGBA 叠到 base 的 (x, y)，自动裁剪越界部分。"""
    x, y = int(x), int(y)
    if overlay.mode != "RGBA":
        overlay = overlay.convert("RGBA")
    if x < 0 or y < 0:
        sx, sy = max(0, -x), max(0, -y)
        if sx >= overlay.width or sy >= overlay.height:
            return
        overlay = overlay.crop((sx, sy, overlay.width, overlay.height))
        x, y = max(0, x), max(0, y)
    if x >= base.width or y >= base.height:
        return
    base.alpha_composite(overlay, (x, y))


def colorize(mask: Image.Image, color: str, alpha: int = 255) -> Image.Image:
    """L 掩膜 + 颜色 → RGBA（RGB 通道铺满，边缘不会渗黑）。"""
    if mask.mode != "L":
        mask = mask.convert("L")
    layer = Image.new("RGBA", mask.size, hex_rgb(color) + (0,))
    if alpha < 255:
        mask = mask.point(lambda p, a=alpha: (p * a + 127) // 255)
    layer.putalpha(mask)
    return layer


def downsample_mask(mask: Image.Image, w: int, h: int) -> Image.Image:
    """超采样掩膜 → 目标尺寸。BOX = 精确面积平均，无振铃。"""
    if mask.size == (w, h):
        return mask
    return mask.resize((max(1, w), max(1, h)), BOX)


# ---------------------------------------------------------------- 文字


def draw_text(
    img: Image.Image,
    xy: Tuple[float, float],
    text: str,
    *,
    size: int,
    color: str,
    bold: bool = False,
    anchor: str = "la",
    alpha: int = 255,
    halo: float = 0.0,
    halo_radius: Optional[float] = None,
) -> None:
    """
    在 RGBA 图上绘制抗锯齿文字（真 alpha）。

    halo>0 时先铺一层高斯模糊的黑色光晕：深色桌面上几乎看不出来，
    浅色桌面上给浅色文字兜出对比度 —— 顺带解决 B1 里「看不清」的问题。
    halo_radius 省略时按字号推导，小字不糊、大字够厚。
    """
    text = "" if text is None else str(text)
    if not text.strip():
        return
    if halo_radius is None:
        halo_radius = max(1.1, min(3.2, size * 0.135))
    font = font_for(text, size, bold)
    bb = _MEASURE.textbbox((0, 0), text, font=font, anchor=anchor)
    pad = int(math.ceil(halo_radius * 3)) + 2 if halo > 0 else 2
    x0 = int(math.floor(bb[0])) - pad
    y0 = int(math.floor(bb[1])) - pad
    w = int(math.ceil(bb[2])) + pad - x0
    h = int(math.ceil(bb[3])) + pad - y0
    if w <= 0 or h <= 0:
        return

    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((-x0, -y0), text, font=font, fill=255, anchor=anchor)

    dx = int(round(xy[0])) + x0
    dy = int(round(xy[1])) + y0

    if halo > 0:
        hm = mask.filter(ImageFilter.GaussianBlur(halo_radius))
        hm = hm.point(lambda p, f=halo: min(255, int(p * f)))
        shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        shadow.putalpha(hm)
        composite_at(img, shadow, dx, dy)

    composite_at(img, colorize(mask, color, alpha), dx, dy)


@functools.lru_cache(maxsize=16)
def _clock_font(size: int) -> ImageFont.FreeTypeFont:
    """日期时钟主时间使用 Bahnschrift，保留圆滑的仪表盘数字轮廓。"""
    for path in (
        r"C:\Windows\Fonts\bahnschrift.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
    ):
        try:
            return ImageFont.truetype(path, max(6, int(size)))
        except Exception:
            continue
    return ImageFont.load_default()


def render_clock_time(
    text: str,
    *,
    size: int,
    color: str,
    x_scale: float = 0.94,
    y_scale: float = 1.24,
    stroke: float = 1.8,
    tracking: float = 0.0,
    scale: int = 6,
) -> Image.Image:
    """绘制高瘦、超粗且圆滑的时间字形，返回带真 alpha 的 RGBA 图。"""
    text = "" if text is None else str(text)
    s = max(4, int(scale))
    font = _clock_font(int(size) * s)
    measure = ImageDraw.Draw(Image.new("L", (1, 1)))
    bb = measure.textbbox(
        (0, 0), text, font=font, anchor="la",
        stroke_width=max(0, int(round(float(stroke) * s))),
    )
    pad = max(4, int(round(4 * s)))
    w = max(1, bb[2] - bb[0] + pad * 2 + int(round(float(tracking) * s * 2)))
    h = max(1, bb[3] - bb[1] + pad * 2)
    mask_hi = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask_hi)
    sw = max(0, int(round(float(stroke) * s)))
    text_y = pad - bb[1]
    if float(tracking) == 0.0 or len(text) <= 1:
        d.text(
            (pad - bb[0], text_y), text, font=font, fill=255,
            anchor="la", stroke_width=sw, stroke_fill=255,
        )
    else:
        # 只把冒号与时/分两侧的间距拉开，不改变小时或分钟内部字距。
        cursor_x = pad - bb[0]
        extra = float(tracking) * s
        for index, char in enumerate(text):
            d.text(
                (cursor_x, text_y), char, font=font, fill=255,
                anchor="la", stroke_width=sw, stroke_fill=255,
            )
            cursor_x += measure.textlength(char, font=font)
            if char == ":" or (index + 1 < len(text) and text[index + 1] == ":"):
                cursor_x += extra

    visible = mask_hi.getbbox()
    if visible is None:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    mask_hi = mask_hi.crop(visible)
    target_w = max(1, int(round(mask_hi.width * float(x_scale) / s)))
    target_h = max(1, int(round(mask_hi.height * float(y_scale) / s)))
    mask = mask_hi.resize((target_w, target_h), Image.Resampling.LANCZOS)
    return colorize(mask, color)


# ---------------------------------------------------------------- 外壳 / 卡片


def compose_shell_and_cards(
    width: int,
    height: int,
    corner_radius: int,
    shell_fill: str,
    shell_opacity: float,
    card_fill: str,
    card_opacity: float,
    cards: Sequence[Tuple[int, int, int, int, int]],
    scale: int = 4,
) -> Image.Image:
    """
    外壳圆角矩形 + 卡片底板 → RGBA（软边 alpha）。
    cards: [(x, y, w, h, radius), ...]；opacity 取 0~1。
    """
    s = max(2, int(scale))
    W, H = int(width), int(height)
    hi = (W * s, H * s)
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    shell_op = max(0.0, min(1.0, float(shell_opacity)))
    card_op = max(0.0, min(1.0, float(card_opacity)))

    if shell_op > 0.001:
        m = Image.new("L", hi, 0)
        ImageDraw.Draw(m).rounded_rectangle(
            [0, 0, hi[0] - 1, hi[1] - 1], radius=max(1, int(corner_radius * s)), fill=255
        )
        base.alpha_composite(
            colorize(downsample_mask(m, W, H), shell_fill, int(255 * shell_op))
        )

    if card_op > 0.001 and cards:
        m = Image.new("L", hi, 0)
        d = ImageDraw.Draw(m)
        for (x, y, cw, ch, rad) in cards:
            x0, y0 = int(round(x * s)), int(round(y * s))
            x1, y1 = int(round((x + cw) * s)) - 1, int(round((y + ch) * s)) - 1
            if x1 > x0 and y1 > y0:
                d.rounded_rectangle(
                    [x0, y0, x1, y1], radius=max(1, int(round(rad * s))), fill=255
                )
        base.alpha_composite(
            colorize(downsample_mask(m, W, H), card_fill, int(255 * card_op))
        )

    return base


# ---------------------------------------------------------------- 圆环


def render_ring(
    pct: Optional[float],
    color: str,
    track: str,
    diameter: int,
    thickness: int,
    scale: int = 6,
    center_text: Optional[str] = None,
    center_color: Optional[str] = None,
    text_size: Optional[int] = None,
    center_suffix: Optional[str] = None,
    suffix_size: Optional[int] = None,
    halo: float = 0.75,
) -> Image.Image:
    """抗锯齿圆环（真 alpha）。进度弧带圆头端点。"""
    D = max(16, int(diameter))
    t = max(2, int(thickness))
    s = max(3, int(scale))
    hi = D * s
    inset = 0.5 * s
    box = [inset, inset, hi - 1 - inset, hi - 1 - inset]
    tw = max(1, int(round(t * s)))

    # 暗轨略提亮，在深色玻璃上仍可见
    tr, tg, tb = hex_rgb(track)
    track_hex = "#%02x%02x%02x" % (
        min(255, int(tr * 0.40 + 90)),
        min(255, int(tg * 0.40 + 90)),
        min(255, int(tb * 0.40 + 90)),
    )

    tm = Image.new("L", (hi, hi), 0)
    ImageDraw.Draw(tm).ellipse(box, outline=255, width=tw)
    out = colorize(downsample_mask(tm, D, D), track_hex)

    p = None if pct is None else max(0.0, min(100.0, float(pct)))
    if p is not None and p > 0.05:
        am = Image.new("L", (hi, hi), 0)
        ad = ImageDraw.Draw(am)
        start = -90.0
        end = -90.0 + p * 3.6
        if p >= 99.95:
            ad.ellipse(box, outline=255, width=tw)
        else:
            ad.arc(box, start, end, fill=255, width=tw)
            # 圆头端点
            cx = cy = (hi - 1) / 2.0
            r_mid = (hi - 1) / 2.0 - inset - tw / 2.0
            for ang in (start, end):
                ax = cx + r_mid * math.cos(math.radians(ang))
                ay = cy + r_mid * math.sin(math.radians(ang))
                ad.ellipse(
                    [ax - tw / 2.0, ay - tw / 2.0, ax + tw / 2.0, ay + tw / 2.0], fill=255
                )
        out.alpha_composite(colorize(downsample_mask(am, D, D), color))

    if center_text:
        value_size = text_size or max(12, int(D * 0.32))
        text_color = center_color or "#F5F5F7"
        if center_suffix:
            suffix_ratio = 0.42
            max_text_w = max(16, D - t * 2 - 5)
            while value_size > 14:
                small_size = suffix_size or max(8, int(round(value_size * suffix_ratio)))
                value_w = text_width(center_text, value_size, bold=True)
                suffix_w = text_width(center_suffix, small_size)
                if value_w + suffix_w + 1 <= max_text_w:
                    break
                value_size -= 1
            small_size = suffix_size or max(8, int(round(value_size * suffix_ratio)))
            value_w = text_width(center_text, value_size, bold=True)
            suffix_w = text_width(center_suffix, small_size)
            left = (D - value_w - suffix_w - 1) / 2.0
            value_y = D / 2.0 - 1
            draw_text(
                out, (left, value_y), center_text, size=value_size,
                color=text_color, bold=True, anchor="lm", halo=halo,
            )
            draw_text(
                out,
                (left + value_w + 1, value_y + value_size * 0.24),
                center_suffix,
                size=small_size,
                color=text_color,
                anchor="lm",
                halo=halo,
            )
        else:
            draw_text(
                out,
                (D / 2.0, D / 2.0),
                center_text,
                size=value_size,
                color=text_color,
                bold=True,
                anchor="mm",
                halo=halo,
            )
    return out


# ---------------------------------------------------------------- 进度条


def render_progress_bar(
    pct: float,
    width: int,
    height: int,
    fill: str,
    track: str,
    scale: int = 6,
    track_alpha: int = 190,
) -> Image.Image:
    s = max(2, int(scale))
    W, H = max(2, int(width)), max(2, int(height))
    hi = (W * s, H * s)
    r = hi[1] // 2

    tm = Image.new("L", hi, 0)
    ImageDraw.Draw(tm).rounded_rectangle([0, 0, hi[0] - 1, hi[1] - 1], radius=r, fill=255)
    out = colorize(downsample_mask(tm, W, H), track, track_alpha)

    p = max(0.0, min(100.0, float(pct)))
    if p > 0.2:
        fw = min(hi[0] - 1, max(hi[1], int((hi[0] - 1) * p / 100.0)))
        fm = Image.new("L", hi, 0)
        ImageDraw.Draw(fm).rounded_rectangle([0, 0, fw, hi[1] - 1], radius=r, fill=255)
        out.alpha_composite(colorize(downsample_mask(fm, W, H), fill))
    return out


@functools.lru_cache(maxsize=48)
def render_pill(
    width: int, height: int, color: str, alpha: int = 255, scale: int = 6
) -> Image.Image:
    """胶囊/圆角小条（强调下划线等），带真 alpha，可缓存。"""
    W, H = max(1, int(width)), max(1, int(height))
    s = max(2, int(scale))
    hi = (W * s, H * s)
    m = Image.new("L", hi, 0)
    ImageDraw.Draw(m).rounded_rectangle(
        [0, 0, hi[0] - 1, hi[1] - 1], radius=max(1, hi[1] // 2), fill=255
    )
    return colorize(downsample_mask(m, W, H), color, alpha)


# ---------------------------------------------------------------- 箭头徽标


@functools.lru_cache(maxsize=32)
def render_arrow_badge(
    direction: str, color: str, size: int = 20, scale: int = 8
) -> Image.Image:
    """圆形箭头徽标（真 alpha，可缓存）。"""
    S = max(8, int(size))
    s = max(3, int(scale))
    hi = S * s

    cm = Image.new("L", (hi, hi), 0)
    ImageDraw.Draw(cm).ellipse([s, s, hi - 1 - s, hi - 1 - s], fill=255)
    out = colorize(downsample_mask(cm, S, S), color)

    am = Image.new("L", (hi, hi), 0)
    cx = cy = hi / 2.0
    body = hi * 0.22
    if direction == "up":
        pts = [(cx, cy - body * 1.15), (cx - body, cy + body * 0.75), (cx + body, cy + body * 0.75)]
    else:
        pts = [(cx, cy + body * 1.15), (cx - body, cy - body * 0.75), (cx + body, cy - body * 0.75)]
    ImageDraw.Draw(am).polygon(pts, fill=255)
    out.alpha_composite(colorize(downsample_mask(am, S, S), "#141416"))
    return out


# ---------------------------------------------------------------- 曲线图


def render_series(
    width: int,
    height: int,
    series: Sequence[dict],
    scale: int = 3,
) -> Image.Image:
    """
    折线 + 面积填充（真 alpha）。

    series 每项：
      points     [(x, y), ...] 逻辑坐标（0..width / 0..height）
      color      线色
      width      线宽（逻辑像素）
      fill_alpha 面积填充 alpha（0 表示不填充）
    """
    W, H = max(2, int(width)), max(2, int(height))
    s = max(1, int(scale))
    hi = (W * s, H * s)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    for item in series:
        pts = item.get("points") or []
        if len(pts) < 2:
            continue
        hp = [(x * s, y * s) for (x, y) in pts]
        fa = int(item.get("fill_alpha", 0))
        if fa > 0:
            fm = Image.new("L", hi, 0)
            poly = hp + [(hi[0], hi[1]), (0, hi[1])]
            ImageDraw.Draw(fm).polygon(poly, fill=255)
            out.alpha_composite(colorize(downsample_mask(fm, W, H), item["color"], fa))
        lw = max(1, int(round(float(item.get("width", 2)) * s)))
        lm = Image.new("L", hi, 0)
        ImageDraw.Draw(lm).line(hp, fill=255, width=lw, joint="curve")
        out.alpha_composite(
            colorize(
                downsample_mask(lm, W, H),
                item["color"],
                int(item.get("line_alpha", 255)),
            )
        )
    return out


def draw_dashed_hline(
    img: Image.Image,
    x0: int,
    x1: int,
    y: int,
    color: str,
    alpha: int = 120,
    on: int = 2,
    off: int = 4,
) -> None:
    """1px 虚线（水平线无需抗锯齿，直接按段绘制）。"""
    w = max(1, int(x1 - x0))
    mask = Image.new("L", (w, 1), 0)
    d = ImageDraw.Draw(mask)
    x = 0
    while x < w:
        d.rectangle([x, 0, min(w - 1, x + on - 1), 0], fill=255)
        x += on + off
    composite_at(img, colorize(mask, color, alpha), int(x0), int(y))


# ---------------------------------------------------------------- Tk 互操作


def to_photo(img: Image.Image) -> ImageTk.PhotoImage:
    return ImageTk.PhotoImage(img)
