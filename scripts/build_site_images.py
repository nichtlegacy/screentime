"""Render the landing page's images into site/ from the demo screenshots.

Everything here comes from .github/images (synthetic data from
scripts/demo_data.py) and site/favicon.svg, so no real usage ever reaches the
page. The PNG captures are lossless and heavy; this writes WebP at the sizes
the layout uses, the panel crops for the Grafana cards, the PNG icons for
browsers that ignore SVG favicons, and the 1200x630 social preview.

Run it again whenever a screenshot or the logo changes and commit the output;
scripts/build_site.sh only copies, so the Pages runner needs no image tooling.

    uv run --with pillow --with playwright python scripts/build_site_images.py
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / ".github" / "images"
SITE = ROOT / "site"
SHOTS = SITE / "screenshots"


# output, source, crop box in source pixels (None = whole image), width, quality.
# The dashboard boxes are Grafana panels including their 1px border.
JOBS = [
    ("dashboard.webp", "dashboard.png", (0, 0, 1600, 1890), 1600, 80),
    ("categories.webp", "dashboard.png", (1067, 635, 1584, 969), 1034, 84),
    ("top-apps.webp", "dashboard.png", (16, 1015, 599, 1501), 1166, 84),
    ("typical-day.webp", "dashboard.png", (16, 2459, 927, 2831), 1200, 84),
    ("night.webp", "night.png", None, 1600, 82),
    ("summary.webp", "summary.png", None, 1200, 84),
]


def screenshots() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    for out, name, box, width, quality in JOBS:
        image = Image.open(SRC / name).convert("RGB")
        if box:
            image = image.crop(box)
        if image.width > width:
            image = image.resize(
                (width, round(image.height * width / image.width)), Image.LANCZOS
            )
        target = SHOTS / out
        image.save(target, "WEBP", quality=quality, method=6)
        print(
            f"{out:18} {image.width}x{image.height}  {target.stat().st_size / 1024:6.1f} KiB"
        )


def mark(size: int) -> Image.Image:
    """site/favicon.svg as a bitmap: same 64-unit geometry, drawn at 4x."""
    s = size * 4 / 64
    n = round(64 * s)

    # Tile gradient along (8,0) -> (56,64): #3a2f8f, #1b1748 at 55 %, #0c0b1f.
    stops = [(0.0, (58, 47, 143)), (0.55, (27, 23, 72)), (1.0, (12, 11, 31))]

    def color(t: float) -> tuple[int, int, int]:
        for (t0, c0), (t1, c1) in zip(stops, stops[1:], strict=False):
            if t <= t1:
                k = (t - t0) / (t1 - t0)
                return tuple(
                    round(a + (b - a) * k) for a, b in zip(c0, c1, strict=True)
                )  # type: ignore[return-value]
        return stops[-1][1]

    tile = Image.new("RGB", (n, n))
    px = tile.load()
    dx, dy = 48, 64
    for y in range(n):
        for x in range(n):
            t = ((x / s - 8) * dx + (y / s) * dy) / (dx * dx + dy * dy)
            px[x, y] = color(min(max(t, 0.0), 1.0))

    glyph = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(glyph)
    r, w = 18 * s, round(5 * s)
    c = 32 * s
    ring = (c - r - w / 2, c - r - w / 2, c + r + w / 2, c + r + w / 2)
    d.ellipse(ring, outline=(255, 255, 255, 56), width=w)
    d.arc(ring, -90, 0, fill=(200, 188, 255, 255), width=w)
    for ex, ey in ((c, c - r), (c + r, c)):  # round caps
        d.ellipse(
            (ex - w / 2, ey - w / 2, ex + w / 2, ey + w / 2), fill=(200, 188, 255, 255)
        )
    for x, top, h in ((22.75, 31, 10), (29.75, 23, 18), (36.75, 28, 13)):
        d.rounded_rectangle(
            (x * s, top * s, (x + 4.5) * s, (top + h) * s),
            radius=2.25 * s,
            fill="white",
        )

    out = tile.convert("RGBA")
    out.alpha_composite(glyph)
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, n - 1, n - 1), radius=15 * s, fill=255
    )
    out.putalpha(mask)
    return out.resize((size, size), Image.LANCZOS)


def icons() -> None:
    mark(32).save(SITE / "icon-32.png", optimize=True)
    # iOS rounds the corners itself and shows transparency as black: flat tile.
    touch = Image.new("RGB", (180, 180), (12, 11, 31))
    big = mark(180)
    touch.paste(big, (0, 0), big)
    touch.save(SITE / "apple-touch-icon.png", optimize=True)
    for size in (192, 512):  # web app manifest
        tile = Image.new("RGB", (size, size), (12, 11, 31))
        art = mark(size)
        tile.paste(art, (0, 0), art)
        tile.save(SITE / f"icon-{size}.png", optimize=True)


def og() -> None:
    """Render scripts/og.html at 2x in Chromium and downsample.

    Writes site/og.png (1200x630, Open Graph) and
    .github/images/social-preview.png (1280x640, upload it by hand under the
    GitHub repo's Settings → General → Social preview; there is no API).
    Needs Playwright's Chromium (``playwright install chromium``) or a
    Chromium binary in PLAYWRIGHT_CHROMIUM.
    """
    import os

    from playwright.sync_api import sync_playwright

    targets = [(SITE / "og.png", 1200, 630), (SRC / "social-preview.png", 1280, 640)]
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM") or None
        )
        for target, w, h in targets:
            page = browser.new_page(
                viewport={"width": w, "height": h}, device_scale_factor=2
            )
            page.goto(f"{(ROOT / 'scripts' / 'og.html').as_uri()}#{w}x{h}")
            page.wait_for_load_state("networkidle")
            page.evaluate("document.fonts.ready")
            shot = page.screenshot()
            Image.open(io.BytesIO(shot)).convert("RGB").resize(
                (w, h), Image.LANCZOS
            ).save(target, optimize=True)
            size = target.stat().st_size / 1024
            print(f"{target.name:18} {w}x{h}  {size:6.1f} KiB")
        browser.close()


if __name__ == "__main__":
    screenshots()
    icons()
    og()
