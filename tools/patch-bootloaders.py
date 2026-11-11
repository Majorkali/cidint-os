#!/usr/bin/env python3
"""Brand the boot menus and add an 'encrypted persistence' entry.

live-build uses config/bootloaders/<name>/ instead of its own templates when
that directory exists, so we copy the stock templates and patch the copies:
  * "Live system" is renamed to "CIDINT OS (amnesic)".
  * The default entry is duplicated with
    `persistence persistence-encryption=luks` appended to the kernel command
    line, so an encrypted persistence volume is only ever unlocked when the
    user explicitly picks that entry.

The script is defensive: if a template does not look as expected it is left
untouched and the build continues (the option can still be typed at the boot
prompt).

usage: patch-bootloaders.py <live-build config dir>
"""
import re
import shutil
import sys
from pathlib import Path

STOCK = Path("/usr/share/live/build/bootloaders")
PERSIST = "persistence persistence-encryption=luks"
MARK = "encrypted persistence"


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
        if not any("@APPEND_LIVE@" in b for b in block):
            continue
        dup = [b.replace("@APPEND_LIVE@", "@APPEND_LIVE@ " + PERSIST) for b in block]
        dup[0] = re.sub(r'"[^"]*"', f'"CIDINT OS ({MARK})"', dup[0], count=1)
        dup[0] = re.sub(r"\s--hotkey=\S+", "", dup[0])
        return "\n".join(lines[:end + 1] + dup + lines[end + 1:])
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
        if not any("@APPEND_LIVE@" in b for b in block):
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
            dup.append(b.replace("@APPEND_LIVE@", "@APPEND_LIVE@ " + PERSIST))
        return "\n".join(lines[:end] + [""] + dup + lines[end:])
    return text


def main() -> int:
    config = Path(sys.argv[1])
    dest = config / "bootloaders"
    if not STOCK.is_dir():
        print(f"patch-bootloaders: {STOCK} not found, leaving boot menus stock")
        return 0
    if not dest.exists():
        shutil.copytree(STOCK, dest, symlinks=True)
    changed = 0
    for f in dest.rglob("*"):
        if f.is_symlink() or not f.is_file() or f.suffix not in (".cfg", ".in"):
            continue
        try:
            old = f.read_text()
        except UnicodeDecodeError:
            continue
        new = patch_grub(old) if "menuentry" in old else patch_syslinux(old)
        new = new.replace("Live system", "CIDINT OS (amnesic)")
        if new != old:
            f.write_text(new)
            changed += 1
            print(f"patch-bootloaders: patched {f.relative_to(dest)}")
    if not changed:
        print("patch-bootloaders: no template matched; boot menus left stock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
