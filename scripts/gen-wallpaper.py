#!/usr/bin/env python3
"""Generate the CIDINT OS default wallpaper (original artwork, procedural).

    python3 scripts/gen-wallpaper.py

Writes meta-cidint/recipes-cidint/cidint-desktop/files/wallpaper.png and
tests/qemu/wallpaper-samples.json (reference colours the desktop test
compares the real screen against). Needs numpy and Pillow; the build and
the tests do not.
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image

W, H = 1920, 1080
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "meta-cidint/recipes-cidint/cidint-desktop/files/wallpaper.png"
SAMPLES = ROOT / "tests/qemu/wallpaper-samples.json"

y, x = np.mgrid[0:H, 0:W].astype(np.float32)
u, v = x / W, y / H


def mix(a, b, t):
    t = np.clip(t, 0, 1)[..., None]
    return a * (1 - t) + b * t


def glow(cx, cy, radius, power=2.0):
    d = np.sqrt(((u - cx) * W / H) ** 2 + (v - cy) ** 2) / radius
    return np.clip(1 - d, 0, 1) ** power


navy = np.array([7, 16, 34], np.float32)
deep = np.array([10, 38, 66], np.float32)
teal = np.array([16, 110, 122], np.float32)
cyan = np.array([64, 196, 208], np.float32)
violet = np.array([86, 62, 160], np.float32)

# Base: diagonal night gradient.
img = mix(navy, deep, (u * 0.55 + v * 0.75))

# Soft light sources.
img = mix(img, teal, 0.75 * glow(0.80, 0.86, 0.95, 1.6))
img = mix(img, violet, 0.42 * glow(0.12, 0.10, 0.80, 2.0))
img = mix(img, cyan, 0.30 * glow(0.86, 0.92, 0.45, 2.4))

# Flowing bands: smooth sine ribbons that follow a curve.
for k, (amp, freq, phase, offset, width, strength) in enumerate([
        (0.10, 1.3, 0.2, 0.62, 0.050, 0.20),
        (0.13, 1.0, 1.1, 0.70, 0.070, 0.16),
        (0.08, 1.7, 2.3, 0.78, 0.035, 0.22),
        (0.15, 0.8, 3.0, 0.86, 0.090, 0.12)]):
    curve = offset + amp * np.sin(2 * np.pi * (u * freq + phase / 6.28)) - 0.18 * u
    band = np.exp(-((v - curve) / width) ** 2)
    img = mix(img, cyan if k % 2 == 0 else teal, strength * band * (0.35 + 0.65 * u))

# Fine arcs centred off-screen, like instrument rings.
r = np.sqrt(((u - 1.05) * W / H) ** 2 + (v - 1.10) ** 2)
for radius in (0.55, 0.75, 0.95, 1.15, 1.35):
    line = np.exp(-((r - radius) / 0.0016) ** 2)
    img = mix(img, cyan, 0.16 * line)

# Vignette, then ordered dither so the gradients do not band.
img *= (1 - 0.35 * np.clip(np.sqrt((u - 0.5) ** 2 + (v - 0.5) ** 2) * 1.25, 0, 1) ** 2)[..., None]
bayer = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], np.float32) / 16 - 0.47
img += np.tile(bayer, (H // 4, W // 4))[..., None]

rgb = np.clip(img, 0, 255).astype(np.uint8)
OUT.parent.mkdir(parents=True, exist_ok=True)
Image.fromarray(rgb, "RGB").save(OUT, optimize=True)

# Reference points on a coarse grid, in relative coordinates.
points = []
for gy in range(1, 12):
    for gx in range(1, 16):
        px, py = int(gx * W / 16), int(gy * H / 12)
        block = rgb[py - 2:py + 3, px - 2:px + 3].reshape(-1, 3).mean(axis=0)
        points.append({"u": round(px / W, 5), "v": round(py / H, 5), "rgb": [int(round(c)) for c in block]})
SAMPLES.write_text(json.dumps({"width": W, "height": H, "points": points}, indent=1) + "\n")
print(OUT, OUT.stat().st_size, "bytes;", len(points), "reference points")
