"""
Render the canonical Mithril logo (Option C) at multiple sizes.

Outputs:
  docs/logo.png          — 512x512, transparent background. The canonical mark.
  docs/favicon.png       — 256x256, transparent background. Browser favicon.
  docs/favicon-32.png    — 32x32, transparent background.
  docs/favicon-192.png   — 192x192, transparent background.

The deep-night background versions live in `docs/banner.png` (hero banner)
and `docs/logo-options/*.png` (concept comparison sheet). This script
produces only transparent-background marks suitable for any context.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

# Mithril palette
MITHRIL = (200, 215, 240)
MITHRIL_HI = (232, 237, 245)
MOON = (91, 154, 255)
MOON_HI = (138, 180, 255)

OUT_DIR = Path(__file__).resolve().parent.parent / "docs"


def render_logo(size: int = 512, *, halo: bool = True) -> Image.Image:
    """Solid shield, gradient fill, M as negative space. Transparent background."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    cx, cy = size // 2, size // 2
    w = int(size * 0.85)
    h = int(size * 0.90)
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

    if halo:
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).polygon(shield, fill=(*MOON, 70))
        glow = glow.filter(ImageFilter.GaussianBlur(radius=max(20, size // 20)))
        img.alpha_composite(glow)

    # Shield mask
    shield_mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(shield_mask).polygon(shield, fill=255)

    # Vertical silver→moon gradient
    grad = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gpx = grad.load()
    for y in range(img.size[1]):
        if y < top or y > bot:
            continue
        t = (y - top) / max(1, bot - top)
        r = int(MITHRIL_HI[0] + (MOON[0] - MITHRIL_HI[0]) * t)
        g = int(MITHRIL_HI[1] + (MOON[1] - MITHRIL_HI[1]) * t)
        b = int(MITHRIL_HI[2] + (MOON[2] - MITHRIL_HI[2]) * t)
        for x in range(img.size[0]):
            gpx[x, y] = (r, g, b, 255)

    # M cutout
    cut = Image.new("L", img.size, 0)
    cdraw = ImageDraw.Draw(cut)
    m_w = int(w * 0.58)
    m_h = int(h * 0.46)
    mx_left = cx - m_w // 2
    mx_right = cx + m_w // 2
    my_top = cy - m_h // 2 + int(h * 0.03)
    my_bot = cy + m_h // 2 + int(h * 0.03)
    valley_y = my_top + int(m_h * 0.60)
    stroke = max(int(size * 0.06), 8)
    cdraw.line([(mx_left, my_bot), (mx_left, my_top)], fill=255, width=stroke)
    cdraw.line([(mx_right, my_bot), (mx_right, my_top)], fill=255, width=stroke)
    cdraw.line([(mx_left, my_top), (cx, valley_y)], fill=255, width=stroke)
    cdraw.line([(mx_right, my_top), (cx, valley_y)], fill=255, width=stroke)

    cut_inv = Image.eval(cut, lambda v: 255 - v)
    final_mask = Image.composite(shield_mask, Image.new("L", img.size, 0), cut_inv)

    masked = Image.new("RGBA", img.size, (0, 0, 0, 0))
    masked.paste(grad, (0, 0), final_mask)
    img.alpha_composite(masked)

    return img


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    outputs = {
        "logo.png": render_logo(512, halo=True),
        "favicon.png": render_logo(256, halo=False),
        "favicon-32.png": render_logo(32, halo=False),
        "favicon-192.png": render_logo(192, halo=False),
    }
    for name, img in outputs.items():
        path = OUT_DIR / name
        img.save(path, format="PNG", optimize=True)
        print(f"wrote {path}  ({path.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
