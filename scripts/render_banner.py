"""
Render docs/banner.png — Mithril hero banner.

Self-contained Pillow renderer. Matches the deep-night + mithril-silver +
moonlit-blue palette used by the dashboard, so the README's hero image
visually connects to the running app screenshot below it.

Usage:
    python scripts/render_banner.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Canvas
WIDTH, HEIGHT = 1200, 400

# Mithril palette — same hex values the dashboard uses
BG_0 = (7, 9, 13)
BG_1 = (14, 19, 28)
HAIRLINE = (200, 215, 240, 22)
MITHRIL = (200, 215, 240)
MITHRIL_HI = (232, 237, 245)
MOON = (91, 154, 255)
MOON_HI = (138, 180, 255)
MUTED = (108, 119, 145)
SHADOW = (3, 5, 8)

OUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "banner.png"


def load_font(size: int, *, weight: str = "regular") -> ImageFont.FreeTypeFont:
    candidates = []
    if weight == "bold":
        candidates += [
            "Inter-Bold.ttf",
            "InterDisplay-Bold.ttf",
            "Helvetica-Bold.ttf",
            "Arial-Bold.ttf",
            "arialbd.ttf",
        ]
    elif weight == "medium":
        candidates += [
            "Inter-Medium.ttf",
            "InterDisplay-Medium.ttf",
            "Helvetica.ttf",
            "Arial.ttf",
            "arial.ttf",
        ]
    candidates += [
        "Inter-Regular.ttf",
        "InterDisplay-Regular.ttf",
        "SegoeUI.ttf",
        "Helvetica.ttf",
        "Arial.ttf",
        "arial.ttf",
        "DejaVuSans.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    # Common Windows / Linux / macOS absolute paths.
    for path in [
        r"C:\Windows\Fonts\segoeuib.ttf" if weight == "bold" else r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if weight == "bold" else r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if weight == "bold"
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
    return ImageFont.load_default()


def draw_background(img: Image.Image) -> None:
    """Solid base + a soft radial glow on the left where the logo will sit."""
    img.paste(BG_0, [0, 0, WIDTH, HEIGHT])

    glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    cx, cy = 320, HEIGHT // 2
    for r in range(380, 40, -10):
        alpha = max(0, int(8 - (r - 40) / 50))
        gdraw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*MOON, alpha))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=30))
    img.paste(glow, (0, 0), glow)

    # Subtle hairline at the bottom for a "card-on-page" feel.
    line = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(line)
    ldraw.line([(0, HEIGHT - 1), (WIDTH, HEIGHT - 1)], fill=HAIRLINE, width=1)
    img.paste(line, (0, 0), line)


def draw_logo(img: Image.Image, cx: int, cy: int, size: int) -> None:
    """Hexagonal shield with an inscribed M-rune. Matches the dashboard logo."""

    # Hex vertices (pointy-top), normalized.
    def hex_points(cx: int, cy: int, r: int) -> list[tuple[int, int]]:
        from math import cos, pi, sin

        return [
            (cx + int(r * cos((i / 6) * 2 * pi - pi / 2)),
             cy + int(r * sin((i / 6) * 2 * pi - pi / 2)))
            for i in range(6)
        ]

    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(layer)

    # Outer hex stroke with subtle inner fill.
    outer = hex_points(cx, cy, size // 2)
    ldraw.polygon(outer, fill=(MOON[0], MOON[1], MOON[2], 24), outline=None)
    ldraw.line(outer + [outer[0]], fill=MITHRIL, width=3)

    # Inner hex (smaller, ghosted).
    inner = hex_points(cx, cy, int(size * 0.38))
    ldraw.line(inner + [inner[0]], fill=(*MITHRIL, 110), width=1)

    # M-rune across the middle. Geometric: descending → peak in → trough → peak out → descending.
    w = int(size * 0.55)
    h = int(size * 0.38)
    left = cx - w // 2
    right = cx + w // 2
    top = cy - h // 2
    bot = cy + h // 2
    mid_y = cy - 2
    runic = [
        (left, bot),
        (left + w // 6, top),
        (cx, mid_y),
        (right - w // 6, top),
        (right, bot),
    ]
    ldraw.line(runic, fill=MITHRIL_HI, width=5, joint="curve")

    # Soft outer glow.
    glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.line(outer + [outer[0]], fill=(*MOON, 100), width=10)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=18))
    img.paste(glow, (0, 0), glow)

    img.paste(layer, (0, 0), layer)


def _gradient_text(
    img: Image.Image,
    text: str,
    pos: tuple[int, int],
    font: ImageFont.FreeTypeFont,
    color_top: tuple[int, int, int],
    color_bottom: tuple[int, int, int],
) -> int:
    """Draw text with a top-to-bottom linear gradient. Returns the rendered width."""
    bbox = font.getbbox(text)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]

    # Make a mask of the text.
    mask = Image.new("L", (w + 4, h + bbox[1] + 4), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.text((-bbox[0], 0), text, fill=255, font=font)

    # Vertical gradient layer.
    grad = Image.new("RGBA", (w + 4, h + bbox[1] + 4), (0, 0, 0, 0))
    gpx = grad.load()
    total = h + bbox[1] + 4
    for y in range(total):
        t = y / max(1, total - 1)
        r = int(color_top[0] + (color_bottom[0] - color_top[0]) * t)
        g = int(color_top[1] + (color_bottom[1] - color_top[1]) * t)
        b = int(color_top[2] + (color_bottom[2] - color_top[2]) * t)
        for x in range(w + 4):
            gpx[x, y] = (r, g, b, 255)

    img.paste(grad, pos, mask)
    return w


def draw_text(img: Image.Image) -> None:
    title_font = load_font(120, weight="bold")
    tagline_font = load_font(38, weight="medium")
    sub_font = load_font(22)
    micro_font = load_font(18)

    text_x = 520
    title_y = 95

    # "Mithril" with vertical gradient (silver → moonlit blue).
    _gradient_text(
        img,
        "Mithril",
        (text_x, title_y),
        title_font,
        color_top=MITHRIL_HI,
        color_bottom=MOON,
    )

    # Tagline.
    draw = ImageDraw.Draw(img)
    draw.text(
        (text_x, title_y + 140),
        "A firewall for LLMs",
        fill=MITHRIL,
        font=tagline_font,
    )

    # One-liner — the pitch.
    draw.text(
        (text_x, title_y + 200),
        "Block prompt injection, jailbreaks, and PII exfiltration at the proxy.",
        fill=MUTED,
        font=sub_font,
    )

    # Bottom-right corner: small, quiet positioning line.
    label = "Open source · Self-hosted · OpenAI-compatible"
    bbox = micro_font.getbbox(label)
    lw = bbox[2] - bbox[0]
    draw.text(
        (WIDTH - lw - 32, HEIGHT - 32),
        label,
        fill=MUTED,
        font=micro_font,
    )


def main() -> int:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_0)
    draw_background(img)
    draw_logo(img, cx=300, cy=HEIGHT // 2, size=240)
    draw_text(img)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT_PATH, format="PNG", optimize=True)
    size = OUT_PATH.stat().st_size
    print(f"wrote {OUT_PATH} ({size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
