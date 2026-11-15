#!/usr/bin/env python3
"""Boot the CIDINT Desktop Edition image in QEMU and check the graphical
session by looking at the actual screen contents.

    python3 tests/qemu/desktop_test.py IMAGE.wic --ovmf-code F --ovmf-vars F

The image is copied first; the build output is never modified. Exit code
0 only if every check passed. Screenshots are saved as PNG evidence.

Checks:
  - the session service is active and the compositor does not run as root
  - the Wayland socket of the session exists
  - the screen (QEMU screen dump) shows the configured desktop background
  - starting a terminal changes what is on the screen
"""
import argparse
import json
import os
import re
import shutil
import socket
import struct
import sys
import time
import zlib
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from persistence_boot_test import Vm, system_state  # noqa: E402

SAMPLES = json.loads((HERE / "wallpaper-samples.json").read_text())["points"]


class Qmp:
    def __init__(self, path: str):
        self.sock = socket.socket(socket.AF_UNIX)
        for _ in range(50):
            try:
                self.sock.connect(path)
                break
            except OSError:
                time.sleep(0.2)
        self.file = self.sock.makefile("rw")
        self.file.readline()  # greeting
        self.cmd("qmp_capabilities")

    def cmd(self, name: str, **arguments):
        self.file.write(json.dumps({"execute": name, "arguments": arguments}) + "\n")
        self.file.flush()
        while True:
            reply = json.loads(self.file.readline())
            if "return" in reply or "error" in reply:
                return reply


def read_ppm(path: Path):
    data = path.read_bytes()
    m = re.match(rb"P6\s+(\d+)\s+(\d+)\s+(\d+)\s", data)
    if not m:
        raise ValueError("not a binary PPM")
    w, h = int(m.group(1)), int(m.group(2))
    return w, h, data[m.end():m.end() + w * h * 3]


def write_png(path: Path, w: int, h: int, rgb: bytes):
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))
    rows = b"".join(b"\x00" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def near(a, b, tol=6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--ovmf-code", type=Path, required=True)
    ap.add_argument("--ovmf-vars", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test/desktop"))
    ap.add_argument("--cdrom", action="store_true", help="the image is a live ISO; boot it as a CD-ROM")
    args = ap.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    work = args.workdir.resolve()
    disk = work / "disk.wic"
    nvram = work / "OVMF_VARS.fd"
    if not args.cdrom:
        shutil.copyfile(args.image, disk)
    shutil.copyfile(args.ovmf_vars, nvram)
    qmp_path = f"/tmp/cidint-desktop-qmp-{os.getpid()}.sock"

    results = []

    def step(name: str, ok: bool, detail: str = ""):
        results.append({"test": name, "passed": bool(ok), "detail": str(detail)[-2500:]})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""),
              flush=True)

    def screenshot(qmp, name: str):
        ppm = work / f"{name}.ppm"
        ppm.unlink(missing_ok=True)
        qmp.cmd("screendump", filename=str(ppm))
        for _ in range(50):
            if ppm.exists() and ppm.stat().st_size > 100:
                break
            time.sleep(0.2)
        time.sleep(0.5)
        w, h, rgb = read_ppm(ppm)
        write_png(work / f"{name}.png", w, h, rgb)
        ppm.unlink()
        return w, h, rgb

    def wallpaper_match(w: int, h: int, rgb: bytes) -> float:
        """Share of reference points (below the panel) where the screen
        shows the wallpaper's colour. The wallpaper is stretched to the
        screen, so relative positions correspond."""
        hit = total = 0
        for p in SAMPLES:
            if p["v"] < 0.08:
                continue
            x, y = min(w - 1, int(p["u"] * w)), min(h - 1, int(p["v"] * h))
            i = (y * w + x) * 3
            total += 1
            hit += near(tuple(rgb[i:i + 3]), tuple(p["rgb"]), tol=16)
        return hit / total if total else 0.0

    def diagnostics(vm) -> str:
        rc, out = vm.run("journalctl -b -u cidint-desktop.service --no-pager -o cat | grep -v '^$' | tail -n 30; "
                         "journalctl -b -u seatd.service --no-pager -o cat | tail -n 6; ls /dev/dri 2>&1; "
                         "id cidint; "
                         "dmesg | grep -i -E 'drm|bochs|virtio_gpu|simple-framebuffer' | tail -n 8")
        return out

    error = None
    log_path = work / "desktop-serial.log"
    with open(log_path, "w", encoding="utf-8") as log:
        vm = None
        try:
            vm = Vm(args.image if args.cdrom else disk, args.ovmf_code, nvram, log, cdrom=args.cdrom,
                    nic=["-nic", "none", "-qmp", f"unix:{qmp_path},server=on,wait=off"])
            qmp = Qmp(qmp_path)
            vm.login()
            step("boot: desktop image reaches a login prompt on the serial console", True)

            state = ""
            for _ in range(20):
                rc, state = vm.run("systemctl is-active cidint-desktop.service")
                if state == "active":
                    break
                time.sleep(3)
            step("graphical session service is active", state == "active",
                 state + "\n" + (diagnostics(vm) if state != "active" else ""))

            rc, out = vm.run("for p in $(pidof weston); do stat -c %U /proc/$p; done | sort -u")
            step("the compositor runs as the unprivileged user 'cidint', not root", out.strip() == "cidint", out)
            sock = ""
            for _ in range(10):
                rc, sock = vm.run("ls /run/cidint-session/ | grep -E '^wayland-[0-9]+$' | head -n 1")
                if sock:
                    break
                time.sleep(2)
            step("the session's Wayland socket exists", sock.startswith("wayland-"), sock or diagnostics(vm))

            time.sleep(5)
            w, h, before = screenshot(qmp, "desktop")
            bg = wallpaper_match(w, h, before)
            colours = len(Counter(before[i:i + 3] for i in range(0, len(before), 3 * 11)))
            step("screen: the CIDINT wallpaper is displayed", bg > 0.85 and colours > 50,
                 f"{w}x{h}, {bg:.2f} of reference points match, {colours} colours\n"
                 + (diagnostics(vm) if bg <= 0.85 else ""))

            vm.run(f"su -s /bin/sh cidint -c 'XDG_RUNTIME_DIR=/run/cidint-session WAYLAND_DISPLAY={sock} "
                   "weston-terminal >/dev/null 2>&1 &'")
            time.sleep(10)
            w, h, after = screenshot(qmp, "desktop-terminal")
            changed = sum(1 for i in range(0, min(len(before), len(after)), 3 * 7)
                          if before[i:i + 3] != after[i:i + 3]) / max(1, len(before) // 21)
            step("screen: a terminal window appears when started in the session", changed > 0.03,
                 f"{changed:.3f} of sampled pixels changed")

            ok, out = system_state(vm)
            step("no failed units", ok, out)

            vm.send("systemctl poweroff")
            vm.wait_exit(180)
        except Exception as e:  # report, do not hide
            error = f"{type(e).__name__}: {e}"
            step("test run completed", False, error)
        finally:
            if vm and vm.proc.poll() is None:
                vm.kill()
            try:
                os.unlink(qmp_path)
            except OSError:
                pass

    passed = all(r["passed"] for r in results)
    summary = {"image": str(args.image), "kvm": os.access("/dev/kvm", os.W_OK), "passed": passed,
               "results": results, "error": error}
    (work / "desktop-report.json").write_text(json.dumps(summary, indent=2))
    if not passed:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
        clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\r", "", raw)
        lines = [ln for ln in clean.splitlines() if ln.strip()]
        print("--- serial console, last 40 lines ---")
        for ln in lines[-40:]:
            print(ln[:300])
    print(f"\n{sum(r['passed'] for r in results)}/{len(results)} checks passed")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
