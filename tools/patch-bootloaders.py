#!/usr/bin/env python3
"""Brand the boot menus and add an 'encrypted persistence' entry.

Runs from a live-build *binary* hook, i.e. on the finished boot files just
before the ISO is assembled, so it works on real menu entries rather than on
live-build's internal templates:
  * "Live system" is renamed to "CIDINT OS (amnesic)".
  * The default live entry is duplicated with
    `persistence persistence-encryption=luks` appended to the kernel command
    line, so an encrypted persistence volume is only ever unlocked when the
    user explicitly picks that entry.
  * GRUB (UEFI) gets a 5 second auto-boot timeout.
  * The GRUB theme title is rebranded.

Defensive by design: a file that does not look as expected is left untouched
and the build continues.

usage: patch-bootloaders.py <binary dir>
"""
import re
import sys
from pathlib import Path

PERSIST = "persistence persistence-encryption=luks"
MARK = "encrypted persistence"
LIVE = "boot=live"


def _append(line: str) -> str:
    return line.rstrip() + " " + PERSIST if LIVE in line else line


def patch_grub(text: str) -> str:
    if MARK in text:
        return text
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not re.match(r"\s*menuentry\b.*\{\s*$", line):
            continue
        end = next((j for j in range(i + 1, len(lines)) if lines[j].strip() == "}"), None)
        if end is None:
            return text
        block = lines[i:end + 1]
        if not any(LIVE in b for b in block):
            continue
        dup = [_append(b) for b in block]
        dup[0] = re.sub(r'"[^"]*"', f'"CIDINT OS ({MARK})"', dup[0], count=1)
        dup[0] = re.sub(r"\s--hotkey=\S+", "", dup[0])
        out = lines[:i] + ["set timeout=5", "set default=0"] + block + dup + lines[end + 1:]
        return "\n".join(out)
    return text


def patch_syslinux(text: str) -> str:
    if MARK in text:
        return text
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not re.match(r"\s*label\s+\S+", line, re.I):
            continue
        end = i + 1
        while end < len(lines) and lines[end].strip() and not re.match(r"\s*label\s", lines[end], re.I):
            end += 1
        block = lines[i:end]
        if not any(LIVE in b for b in block):
            continue
        dup = []
        for b in block:
            if re.match(r"\s*menu\s+default", b, re.I):
                continue
            if re.match(r"\s*label\s", b, re.I):
                b = b.rstrip() + "-persist"
            elif re.match(r"\s*menu\s+label", b, re.I):
                indent = b[:len(b) - len(b.lstrip())]
                b = f"{indent}menu label CIDINT OS ({MARK})"
            dup.append(_append(b))
        return "\n".join(lines[:end] + [""] + dup + lines[end:])
    return text


def rebrand(text: str) -> str:
    text = text.replace("Live system", "CIDINT OS (amnesic)")
    return re.sub(r"Debian GNU/Linux[^\"'\n]*", "CIDINT OS 0.1", text)


def make_splash(width: int, height: int) -> bytes:
    """A CIDINT boot background as PNG bytes: dark gradient with a teal glow. No dependencies."""
    import struct
    import zlib

    cx, cy, radius = width * 0.74, height * 0.36, max(width, height) * 0.55
    raw = bytearray()
    for y in range(height):
        raw.append(0)                                  # PNG filter type: none
        fy = y / max(1, height - 1)
        for x in range(width):
            fx = x / max(1, width - 1)
            t = (fx + fy) / 2                          # diagonal base gradient
            r, g, b = 5 + 10 * t, 8 + 19 * t, 15 + 36 * t
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / radius
            glow = max(0.0, 1 - d) ** 2 * 0.30
            raw += bytes((int(r + (53 - r) * glow), int(g + (224 - g) * glow), int(b + (200 - b) * glow)))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def replace_splashes(root: Path) -> int:
    """Swap the distribution boot artwork for the CIDINT background, keeping each image's size."""
    import struct
    count = 0
    for f in sorted(root.rglob("splash*.png")):
        try:
            head = f.read_bytes()[:24]
            if head[:8] != b"\x89PNG\r\n\x1a\n":
                continue
            width, height = struct.unpack(">II", head[16:24])
            if not (64 <= width <= 4096 and 64 <= height <= 4096):
                continue
            f.write_bytes(make_splash(width, height))
            count += 1
            print(f"patch-bootloaders: replaced {f.relative_to(root).as_posix()} ({width}x{height})")
        except (OSError, struct.error):
            continue
    return count


def main() -> int:
    root = Path(sys.argv[1])
    changed = replace_splashes(root)
    for f in sorted(root.rglob("*")):
        if f.is_symlink() or not f.is_file() or f.suffix not in (".cfg", ".txt"):
            continue
        rel = f.relative_to(root).as_posix()
        if not rel.startswith(("boot/grub", "isolinux", "syslinux", "EFI")):
            continue
        try:
            old = f.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        new = old
        if f.suffix == ".cfg":
            new = patch_grub(new) if "menuentry" in new else patch_syslinux(new)
        new = rebrand(new)
        if new != old:
            f.write_text(new)
            changed += 1
            print(f"patch-bootloaders: patched {rel}")
    if not changed:
        print("patch-bootloaders: nothing matched; boot menus left stock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
