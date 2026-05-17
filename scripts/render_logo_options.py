"""
Render four logo concepts for Mithril.

Generates docs/logo-options/{A,B,C,D}.png plus docs/logo-options/compare.png
which shows all four side by side at the same size so they're easy to choose
between.

Each option is constructed from primitives (lines, polygons, circles) — no
external fonts or assets — so the source of truth is this script. Edit it,
re-run, ship.
"""

from __future__ import annotations

from math import cos, pi, sin
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Mithril palette (same as dashboard + banner)
BG_0 = (7, 9, 13)
PANEL = (16, 20, 28)
HAIRLINE = (200, 215, 240, 28)
MITHRIL = (200, 215, 240)
MITHRIL_HI = (232, 237, 245)
MOON = (91, 154, 255)
MOON_HI = (138, 180, 255)
MUTED = (108, 119, 145)

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "logo-options"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def new_canvas(size: int = 480) -> Image.Image:
    img = Image.new("RGB", (size, size), BG_0)
    # Subtle radial glow from upper-left
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    cx, cy = size // 3, size // 3
    for r in range(size, 40, -10):
        alpha = max(0, int(6 - r // 80))
        gdraw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*MOON, alpha))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=40))
    img.paste(glow, (0, 0), glow)
    return img


def hex_points(cx: int, cy: int, r: int, rotation: float = -pi / 2) -> list[tuple[int, int]]:
    """Pointy-top hexagon (rotation -π/2 puts a vertex at the top)."""
    return [
        (cx + int(r * cos(rotation + (i / 6) * 2 * pi)),
         cy + int(r * sin(rotation + (i / 6) * 2 * pi)))
        for i in range(6)
    ]


def glow_pass(img: Image.Image, draw_fn, radius: int = 18, alpha: int = 90) -> None:
    """Render `draw_fn(canvas, layer_draw)` onto a blurred glow layer behind the main art."""
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    draw_fn(gdraw, alpha=alpha)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=radius))
    img.paste(glow, (0, 0), glow)


# ---------------------------------------------------------------------------
# Option A — bold custom "M" with shield contours
# ---------------------------------------------------------------------------
# A bold geometric M where the outer strokes flare into a shield silhouette.
# Reads as a letter at any size; suggests armor without being literal.

def logo_a(size: int = 480) -> Image.Image:
    img = new_canvas(size)
    cx, cy = size // 2, size // 2
    s = size * 0.62  # logo span
    w = int(s)
    h = int(s * 0.95)

    # Shield outline — pentagonal, pointed at the bottom.
    top = cy - h // 2
    bot = cy + h // 2
    left = cx - w // 2
    right = cx + w // 2
    shield = [
        (left, top + int(h * 0.18)),
        (cx, top),
        (right, top + int(h * 0.18)),
        (right, top + int(h * 0.55)),
        (cx, bot),
        (left, top + int(h * 0.55)),
    ]

    # Glow halo behind the M
    def _glow(d, alpha):
        d.polygon(shield, fill=(*MOON, alpha // 2))

    glow_pass(img, _glow, radius=28, alpha=80)

    # Shield outline
    draw = ImageDraw.Draw(img)
    draw.polygon(shield, fill=(MOON[0], MOON[1], MOON[2], 18), outline=None)
    draw.line(shield + [shield[0]], fill=MITHRIL, width=3)

    # Bold M — geometric, fills most of the shield
    m_w = int(w * 0.62)
    m_h = int(h * 0.50)
    mx_left = cx - m_w // 2
    mx_right = cx + m_w // 2
    my_top = cy - m_h // 2 + int(h * 0.04)
    my_bot = cy + m_h // 2 + int(h * 0.04)
    valley_y = my_top + int(m_h * 0.55)
    stroke = max(7, size // 36)
    # Outer left and right legs
    draw.line([(mx_left, my_bot), (mx_left, my_top)], fill=MITHRIL_HI, width=stroke)
    draw.line([(mx_right, my_bot), (mx_right, my_top)], fill=MITHRIL_HI, width=stroke)
    # Top inner diagonals down to valley
    draw.line([(mx_left, my_top), (cx, valley_y)], fill=MITHRIL_HI, width=stroke)
    draw.line([(mx_right, my_top), (cx, valley_y)], fill=MITHRIL_HI, width=stroke)

    return img


# ---------------------------------------------------------------------------
# Option B — chainmail hex cluster
# ---------------------------------------------------------------------------
# Three interlocked hexagons. Most on-brand for "mithril mail". Recognizable
# as a unique mark but can get busy at small sizes.

def logo_b(size: int = 480) -> Image.Image:
    img = new_canvas(size)
    cx, cy = size // 2, size // 2
    r = int(size * 0.20)
    # Honeycomb geometry: center + two side hexes offset by sqrt(3)*r horizontally
    # and r*1.5 vertically for proper tiling. We'll lay three.
    dx = int(r * 1.732)  # cos(30°) * 2r
    dy = int(r * 1.0)
    centers = [
        (cx, cy - int(r * 0.5)),       # top
        (cx - dx // 2, cy + dy),       # bottom-left
        (cx + dx // 2, cy + dy),       # bottom-right
    ]

    # Glow halos
    def _glow(d, alpha):
        for hx, hy in centers:
            pts = hex_points(hx, hy, r + 6)
            d.polygon(pts, fill=(*MOON, alpha // 2))

    glow_pass(img, _glow, radius=22, alpha=90)

    draw = ImageDraw.Draw(img)
    for idx, (hx, hy) in enumerate(centers):
        pts = hex_points(hx, hy, r)
        # Fill (slightly more on the top hex to read as foreground)
        fill = (MOON[0], MOON[1], MOON[2], 36 if idx == 0 else 22)
        draw.polygon(pts, fill=fill, outline=None)
        draw.line(pts + [pts[0]], fill=MITHRIL_HI if idx == 0 else MITHRIL, width=4)

    # Small inscribed M in the top hex for identity
    hx, hy = centers[0]
    mw = int(r * 0.85)
    mh = int(r * 0.65)
    pts = [
        (hx - mw // 2, hy + mh // 2),
        (hx - mw // 2, hy - mh // 2),
        (hx, hy + int(mh * 0.05)),
        (hx + mw // 2, hy - mh // 2),
        (hx + mw // 2, hy + mh // 2),
    ]
    stroke = max(4, size // 80)
    for a, b in zip(pts, pts[1:]):
        draw.line([a, b], fill=MITHRIL_HI, width=stroke)

    return img


# ---------------------------------------------------------------------------
# Option C — solid shield, negative-space M
# ---------------------------------------------------------------------------
# A filled shield with the M as cut-out negative space. Highest contrast,
# best legibility at every size including favicon.

def logo_c(size: int = 480) -> Image.Image:
    img = new_canvas(size)
    cx, cy = size // 2, size // 2
    s = int(size * 0.68)
    w = s
    h = int(s * 1.05)
    top = cy - h // 2
    bot = cy + h // 2
    left = cx - w // 2
    right = cx + w // 2
    shield = [
        (left, top + int(h * 0.12)),
        (cx, top),
        (right, top + int(h * 0.12)),
        (right, top + int(h * 0.58)),
        (cx, bot),
        (left, top + int(h * 0.58)),
    ]

    # Glow behind shield
    def _glow(d, alpha):
        d.polygon(shield, fill=(*MOON, alpha))

    glow_pass(img, _glow, radius=32, alpha=120)

    # Build the shield with a vertical silver→moon gradient
    shield_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    smask = Image.new("L", img.size, 0)
    ImageDraw.Draw(smask).polygon(shield, fill=255)

    grad = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gpx = grad.load()
    for y in range(img.size[1]):
        t = max(0.0, min(1.0, (y - top) / max(1, bot - top)))
        r = int(MITHRIL_HI[0] + (MOON[0] - MITHRIL_HI[0]) * t)
        g = int(MITHRIL_HI[1] + (MOON[1] - MITHRIL_HI[1]) * t)
        b = int(MITHRIL_HI[2] + (MOON[2] - MITHRIL_HI[2]) * t)
        for x in range(img.size[0]):
            gpx[x, y] = (r, g, b, 255)

    shield_layer.paste(grad, (0, 0), smask)
    img.paste(shield_layer, (0, 0), shield_layer)

    # Knock out the M as negative space
    cut = Image.new("L", img.size, 0)
    cdraw = ImageDraw.Draw(cut)
    m_w = int(w * 0.58)
    m_h = int(h * 0.46)
    mx_left = cx - m_w // 2
    mx_right = cx + m_w // 2
    my_top = cy - m_h // 2 + int(h * 0.03)
    my_bot = cy + m_h // 2 + int(h * 0.03)
    valley_y = my_top + int(m_h * 0.60)
    stroke = max(10, size // 26)
    cdraw.line([(mx_left, my_bot), (mx_left, my_top)], fill=255, width=stroke)
    cdraw.line([(mx_right, my_bot), (mx_right, my_top)], fill=255, width=stroke)
    cdraw.line([(mx_left, my_top), (cx, valley_y)], fill=255, width=stroke)
    cdraw.line([(mx_right, my_top), (cx, valley_y)], fill=255, width=stroke)

    # Apply the cut as transparency to the shield layer
    base = Image.new("RGB", img.size, BG_0)
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon(shield, fill=(*MOON, 60))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=32))
    base.paste(glow, (0, 0), glow)

    # Build masked shield
    shield_mask = smask.copy()
    # Subtract the cut from the mask
    shield_mask = Image.eval(shield_mask, lambda v: v)
    composite = Image.composite(shield_layer, Image.new("RGBA", img.size, (0, 0, 0, 0)), shield_mask)
    final_mask = Image.eval(shield_mask, lambda v: v) if False else shield_mask
    # Better: knock cut out of shield_mask
    cut_inv = Image.eval(cut, lambda v: 255 - v)
    shield_mask = Image.composite(shield_mask, Image.new("L", img.size, 0), cut_inv)

    final = base.copy()
    final.paste(shield_layer, (0, 0), shield_mask)

    return final


# ---------------------------------------------------------------------------
# Option D — refined version of the current hex+M (cleaner)
# ---------------------------------------------------------------------------
# Keeps the hexagonal shield theme but drops the inner ghost hex, makes the
# inscribed M bolder and more readable, adds a soft inner glow.

def logo_d(size: int = 480) -> Image.Image:
    img = new_canvas(size)
    cx, cy = size // 2, size // 2
    r = int(size * 0.32)

    hex_outer = hex_points(cx, cy, r)

    # Glow under hex
    def _glow(d, alpha):
        d.polygon(hex_outer, fill=(*MOON, alpha))

    glow_pass(img, _glow, radius=30, alpha=100)

    draw = ImageDraw.Draw(img)
    # Hex fill (subtle) + outline
    draw.polygon(hex_outer, fill=(MOON[0], MOON[1], MOON[2], 24), outline=None)
    draw.line(hex_outer + [hex_outer[0]], fill=MITHRIL_HI, width=5)

    # Bolder M centered inside hex — wider base, deeper valley
    m_w = int(r * 1.10)
    m_h = int(r * 0.85)
    mx_left = cx - m_w // 2
    mx_right = cx + m_w // 2
    my_top = cy - m_h // 2 + int(r * 0.05)
    my_bot = cy + m_h // 2 + int(r * 0.05)
    valley_y = my_top + int(m_h * 0.60)
    stroke = max(8, size // 32)

    draw.line([(mx_left, my_bot), (mx_left, my_top)], fill=MITHRIL_HI, width=stroke)
    draw.line([(mx_right, my_bot), (mx_right, my_top)], fill=MITHRIL_HI, width=stroke)
    draw.line([(mx_left, my_top), (cx, valley_y)], fill=MITHRIL_HI, width=stroke)
    draw.line([(mx_right, my_top), (cx, valley_y)], fill=MITHRIL_HI, width=stroke)

    return img


# ---------------------------------------------------------------------------
# Comparison sheet
# ---------------------------------------------------------------------------

def comparison(size: int = 480, cap_h: int = 56) -> Image.Image:
    options = {
        "A — Shield + bold M": logo_a(size),
        "B — Chainmail cluster": logo_b(size),
        "C — Solid shield, negative-space M": logo_c(size),
        "D — Hex + refined M (current direction)": logo_d(size),
    }
    pad = 32
    cols = 2
    rows = 2
    sheet_w = cols * size + (cols + 1) * pad
    sheet_h = rows * (size + cap_h) + (rows + 1) * pad

    sheet = Image.new("RGB", (sheet_w, sheet_h), BG_0)
    draw = ImageDraw.Draw(sheet)
    # Font for captions
    font = None
    for name in ("Inter-Medium.ttf", "Arial.ttf", "DejaVuSans.ttf"):
        try:
            font = ImageFont.truetype(name, 22)
            break
        except OSError:
            pass
    for path in [r"C:\Windows\Fonts\segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if font is None and Path(path).exists():
            try:
                font = ImageFont.truetype(path, 22)
            except OSError:
                pass
    if font is None:
        font = ImageFont.load_default()

    for idx, (label, img) in enumerate(options.items()):
        c = idx % cols
        r = idx // cols
        x = pad + c * (size + pad)
        y = pad + r * (size + cap_h + pad)
        sheet.paste(img, (x, y))
        draw.text((x + 8, y + size + 12), label, fill=MITHRIL, font=font)

    return sheet


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    logo_a(480).save(OUT_DIR / "A.png", optimize=True)
    logo_b(480).save(OUT_DIR / "B.png", optimize=True)
    logo_c(480).save(OUT_DIR / "C.png", optimize=True)
    logo_d(480).save(OUT_DIR / "D.png", optimize=True)

    sheet = comparison(size=420, cap_h=56)
    sheet.save(OUT_DIR / "compare.png", optimize=True)

    print("wrote:")
    for name in ("A", "B", "C", "D"):
        p = OUT_DIR / f"{name}.png"
        print(f"  {p}  ({p.stat().st_size / 1024:.1f} KB)")
    p = OUT_DIR / "compare.png"
    print(f"  {p}  ({p.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
