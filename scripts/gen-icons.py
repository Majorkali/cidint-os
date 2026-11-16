#!/usr/bin/env python3
"""Draw the CIDINT desktop launcher icons (original artwork, 32x32 PNG for the panel).

    python3 scripts/gen-icons.py meta-cidint/recipes-cidint/cidint-desktop/files/icons
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

S = 4  # supersampling
N = 48 * S
CYAN, NAVY, WHITE, GREEN, AMBER = (43, 212, 224), (14, 34, 56), (236, 244, 250), (126, 226, 168), (255, 196, 92)


def base(top, bottom):
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    grad = Image.new("RGBA", (N, N))
    gd = ImageDraw.Draw(grad)
    for y in range(N):
        t = y / (N - 1)
        gd.line([(0, y), (N, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle([2 * S, 2 * S, N - 2 * S, N - 2 * S], radius=11 * S, fill=255)
    img.paste(grad, (0, 0), mask)
    ImageDraw.Draw(img).rounded_rectangle([2 * S, 2 * S, N - 2 * S, N - 2 * S], radius=11 * S,
                                          outline=CYAN + (170,), width=S)
    return img, ImageDraw.Draw(img)


def files():
    img, d = base((24, 64, 96), (12, 34, 58))
    d.rounded_rectangle([10 * S, 14 * S, 24 * S, 22 * S], radius=2 * S, fill=AMBER)
    d.rounded_rectangle([10 * S, 18 * S, 38 * S, 36 * S], radius=3 * S, fill=AMBER)
    d.rectangle([10 * S, 20 * S, 38 * S, 22 * S], fill=(230, 160, 60))
    return img


def terminal():
    img, d = base((22, 30, 44), (8, 14, 24))
    d.line([(12 * S, 17 * S), (20 * S, 24 * S), (12 * S, 31 * S)], fill=GREEN, width=3 * S, joint="curve")
    d.line([(23 * S, 32 * S), (35 * S, 32 * S)], fill=WHITE, width=3 * S)
    return img


def assistant():
    img, d = base((20, 80, 100), (10, 36, 60))
    d.ellipse([11 * S, 11 * S, 37 * S, 37 * S], outline=CYAN, width=3 * S)
    for (x, y) in ((24, 16), (17, 28), (31, 28)):
        d.ellipse([(x - 3) * S, (y - 3) * S, (x + 3) * S, (y + 3) * S], fill=WHITE)
    for a, b in (((24, 16), (17, 28)), ((24, 16), (31, 28)), ((17, 28), (31, 28))):
        d.line([(a[0] * S, a[1] * S), (b[0] * S, b[1] * S)], fill=WHITE, width=2 * S)
    return img


def settings():
    import math
    img, d = base((40, 60, 90), (16, 28, 48))
    cx = cy = 24 * S
    pts = []
    for i in range(16):
        r = (15 if i % 2 == 0 else 11) * S
        a = i * math.pi / 8
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    d.polygon(pts, fill=WHITE)
    d.ellipse([cx - 12 * S, cy - 12 * S, cx + 12 * S, cy + 12 * S], fill=WHITE)
    d.ellipse([cx - 5 * S, cy - 5 * S, cx + 5 * S, cy + 5 * S], fill=NAVY)
    return img


def small(kind):
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if kind == "folder":
        d.rounded_rectangle([4 * S, 9 * S, 22 * S, 18 * S], radius=3 * S, fill=AMBER)
        d.rounded_rectangle([4 * S, 14 * S, 44 * S, 40 * S], radius=4 * S, fill=AMBER)
    else:
        d.polygon([(10 * S, 4 * S), (30 * S, 4 * S), (40 * S, 14 * S), (40 * S, 44 * S), (10 * S, 44 * S)],
                  fill=WHITE)
        d.polygon([(30 * S, 4 * S), (30 * S, 14 * S), (40 * S, 14 * S)], fill=(170, 190, 210))
        for y in (22, 28, 34):
            d.line([(16 * S, y * S), (34 * S, y * S)], fill=(120, 140, 160), width=2 * S)
    return img


def main() -> int:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for name, make in (("files", files), ("terminal", terminal), ("assistant", assistant), ("settings", settings)):
        make().resize((32, 32), Image.LANCZOS).save(out / f"{name}.png", optimize=True)
        print(out / f"{name}.png")
    for kind in ("folder", "file"):
        small(kind).resize((20, 20), Image.LANCZOS).save(out / f"{kind}-small.png", optimize=True)
        print(out / f"{kind}-small.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
