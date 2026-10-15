"""Логотип Джарвиса — пять вертикальных полосок разной высоты.

Геометрия снята с исходного рисунка (рамка 85×112). В покое логотип точно повторяет рисунок,
а когда ассистент слушает или говорит, полоски «играют», как эквалайзер, в такт голосу.
Один и тот же рисунок используется для значка в углу экрана, иконки программы и окна.
"""

import math

from PIL import Image, ImageDraw, ImageFilter

# (x, ширина, верх, низ) в координатах рамки 85×112
BARS = (
    (0, 11, 74, 103),
    (18, 11, 45, 112),
    (37, 11, 48, 99),
    (55, 11, 0, 84),
    (73, 12, 55, 84),
)
BOX_W, BOX_H = 85, 112
PHASES = (0.0, 1.7, 3.1, 0.9, 2.4)  # у каждой полоски своя фаза, чтобы движение выглядело живым

LIGHT = (242, 244, 247)
GLASS = (24, 25, 28)


def bar_rects(t: float = 0.0, level: float = 0.0, state: str = "idle") -> list[tuple[float, float, float, float]]:
    """Прямоугольники полосок (x0, y0, x1, y1) в рамке 85×112 для момента t."""
    level = max(0.0, min(1.0, level))
    rects = []
    for (x, w, top, bottom), phase in zip(BARS, PHASES, strict=True):
        height = bottom - top
        center = (top + bottom) / 2
        if state in ("listening", "speaking"):
            wave = math.sin(t * 9.0 + phase) * 0.6 + math.sin(t * 5.3 + phase * 1.9) * 0.4
            scale = 1.0 + (0.12 + 0.55 * level) * wave
        elif state == "thinking":  # волна бежит слева направо
            scale = 1.0 + 0.28 * math.sin(t * 6.0 - x / 14.0)
        else:  # спокойное «дыхание», почти незаметное
            scale = 1.0 + 0.03 * math.sin(t * 1.6 + phase)
        half = max(3.0, height * scale / 2)
        top_y = max(-14.0, center - half)
        bottom_y = min(BOX_H + 14.0, center + half)
        rects.append((float(x), top_y, float(x + w), bottom_y))
    return rects


_PLATES: dict[tuple[int, float], Image.Image] = {}


def _plate(s: int, opacity: float) -> Image.Image:
    """Стеклянная подложка (кэшируется: на каждый кадр анимации её не перерисовываем)."""
    key = (s, round(opacity, 2))
    if key not in _PLATES:
        image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        plate = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(plate)
        radius = int(s * 0.27)
        d.rounded_rectangle((0, 0, s - 1, s - 1), radius=radius, fill=GLASS + (int(205 * opacity),))
        d.rounded_rectangle((0, 0, s - 1, s - 1), radius=radius, outline=(255, 255, 255, int(40 * opacity)),
                            width=max(2, s // 90))
        image = Image.alpha_composite(image, plate)
        sheen = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(sheen).rounded_rectangle((s * 0.06, s * 0.04, s * 0.94, s * 0.42), radius=int(s * 0.2),
                                                fill=(255, 255, 255, int(14 * opacity)))
        _PLATES[key] = Image.alpha_composite(image, sheen.filter(ImageFilter.GaussianBlur(s * 0.04)))
    return _PLATES[key]


def render(
    size: int,
    t: float = 0.0,
    state: str = "idle",
    level: float = 0.0,
    color: tuple[int, int, int] = LIGHT,
    opacity: float = 0.9,
    glass: bool = True,
    fill: float = 0.56,
) -> Image.Image:
    """Кадр логотипа size×size (RGBA) на тёмной стеклянной подложке со скруглёнными углами."""
    ss = 2  # суперсэмплинг для ровных краёв
    s = size * ss
    image = _plate(s, opacity).copy() if glass else Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Логотип занимает долю fill высоты подложки и стоит по центру.
    scale = s * fill / BOX_H
    off_x = (s - BOX_W * scale) / 2
    off_y = (s - BOX_H * scale) / 2
    bars = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(bars)
    for x0, y0, x1, y1 in bar_rects(t, level, state):
        d.rectangle(
            (off_x + x0 * scale, off_y + y0 * scale, off_x + x1 * scale, off_y + y1 * scale),
            fill=color + (int(255 * opacity),),
        )
    image = Image.alpha_composite(image, bars)
    return image.resize((size, size), Image.LANCZOS)


def svg(color: str = "currentColor") -> str:
    """Логотип в SVG (для окна программы)."""
    rects = "".join(
        f'<rect class="bar bar{i}" x="{x}" y="{top}" width="{w}" height="{bottom - top}"/>'
        for i, (x, w, top, bottom) in enumerate(BARS)
    )
    return f'<svg viewBox="0 0 {BOX_W} {BOX_H}" xmlns="http://www.w3.org/2000/svg" fill="{color}">{rects}</svg>'


def _pixel_icon(size: int) -> Image.Image:
    """Иконки 16 и 24 px: полоски по пиксельной сетке, без сглаживания (иначе сливаются)."""
    width, gap = (2, 1) if size <= 16 else (3, 1)
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=max(2, size // 5), fill=GLASS + (255,))
    total = len(BARS) * width + (len(BARS) - 1) * gap
    left = (size - total) // 2
    pad = 2 if size <= 16 else 3
    k = (size - 2 * pad) / BOX_H
    for i, (_x, _w, top, bottom) in enumerate(BARS):
        x0 = left + i * (width + gap)
        d.rectangle((x0, pad + round(top * k), x0 + width - 1, pad + round(bottom * k) - 1), fill=LIGHT + (255,))
    return image


def save_icon(path: str) -> None:
    """Иконка программы (.ico) всех стандартных размеров."""
    sizes = [16, 24, 32, 48, 64, 128, 256]
    # На маленьких размерах логотип крупнее, иначе полоски сливаются.
    images = [_pixel_icon(n) if n <= 24 else render(n, opacity=1.0, fill=0.74 if n <= 32 else 0.62) for n in sizes]
    images[-1].save(path, format="ICO", sizes=[(n, n) for n in sizes], append_images=images[:-1])
