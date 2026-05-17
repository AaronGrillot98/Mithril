"""
Render docs/banner.png — Mithril hero banner.

Self-contained Pillow renderer. Matches the deep-night + mithril-silver +
moonlit-blue palette used by the dashboard, so the README's hero image
visually connects to the running app screenshot below it.

The logo on the left is "Option C" from `render_logo_options.py`: a solid
shield with a silver→moon gradient and the M cut out as negative space.

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

    line = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(line)
    ldraw.line([(0, HEIGHT - 1), (WIDTH, HEIGHT - 1)], fill=HAIRLINE, width=1)
    img.paste(line, (0, 0), line)


def draw_logo_c(img: Image.Image, cx: int, cy: int, size: int) -> None:
    """Solid shield, gradient fill, M as negative space — Option C from render_logo_options.py.

    Constructed in three passes:
      1. Outer halo (gaussian-blurred shield silhouette).
      2. Gradient-filled shield using a mask.
      3. The shield mask has the M strokes punched out so the deep-night
         background shows through where the M strokes would be.
    """
    w = size
    h = int(size * 1.05)
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

    # Halo
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon(shield, fill=(*MOON, 60))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=32))
    img.paste(glow, (0, 0), glow)

    # Shield mask
    shield_mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(shield_mask).polygon(shield, fill=255)

    # Silver → moon vertical gradient layer
    grad = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gpx = grad.load()
    for y in range(img.size[1]):
        if y < top or y > bot:
            for x in range(img.size[0]):
                gpx[x, y] = (0, 0, 0, 0)
            continue
        t = (y - top) / max(1, bot - top)
        r = int(MITHRIL_HI[0] + (MOON[0] - MITHRIL_HI[0]) * t)
        g = int(MITHRIL_HI[1] + (MOON[1] - MITHRIL_HI[1]) * t)
        b = int(MITHRIL_HI[2] + (MOON[2] - MITHRIL_HI[2]) * t)
        for x in range(img.size[0]):
            gpx[x, y] = (r, g, b, 255)

    # M-cutout mask (black on the M, white elsewhere) — we'll subtract this from the shield mask.
    cut = Image.new("L", img.size, 0)
    cdraw = ImageDraw.Draw(cut)
    m_w = int(w * 0.58)
    m_h = int(h * 0.46)
    mx_left = cx - m_w // 2
    mx_right = cx + m_w // 2
    my_top = cy - m_h // 2 + int(h * 0.03)
    my_bot = cy + m_h // 2 + int(h * 0.03)
    valley_y = my_top + int(m_h * 0.60)
    stroke = max(10, size // 14)
    cdraw.line([(mx_left, my_bot), (mx_left, my_top)], fill=255, width=stroke)
    cdraw.line([(mx_right, my_bot), (mx_right, my_top)], fill=255, width=stroke)
    cdraw.line([(mx_left, my_top), (cx, valley_y)], fill=255, width=stroke)
    cdraw.line([(mx_right, my_top), (cx, valley_y)], fill=255, width=stroke)

    # Final mask = shield AND NOT cut.
    cut_inv = Image.eval(cut, lambda v: 255 - v)
    final_mask = Image.composite(shield_mask, Image.new("L", img.size, 0), cut_inv)

    img.paste(grad, (0, 0), final_mask)


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

    mask = Image.new("L", (w + 4, h + bbox[1] + 4), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.text((-bbox[0], 0), text, fill=255, font=font)

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

    _gradient_text(
        img,
        "Mithril",
        (text_x, title_y),
        title_font,
        color_top=MITHRIL_HI,
        color_bottom=MOON,
    )

    draw = ImageDraw.Draw(img)
    draw.text(
        (text_x, title_y + 140),
        "A firewall for LLMs",
        fill=MITHRIL,
        font=tagline_font,
    )

    draw.text(
        (text_x, title_y + 200),
        "Block prompt injection, jailbreaks, and PII exfiltration at the proxy.",
        fill=MUTED,
        font=sub_font,
    )

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
    draw_logo_c(img, cx=300, cy=HEIGHT // 2, size=230)
    draw_text(img)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT_PATH, format="PNG", optimize=True)
    size = OUT_PATH.stat().st_size
    print(f"wrote {OUT_PATH} ({size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
