#!/usr/bin/env python3
"""Boot the CIDINT Desktop Edition image in QEMU and check the graphical
session by looking at the actual screen contents.

    python3 tests/qemu/desktop_test.py IMAGE.wic --ovmf-code F --ovmf-vars F

The image is copied first; the build output is never modified. Exit code
0 only if every check passed. Screenshots are saved as PNG evidence.

Checks (installed image):
  - first start asks for the root and desktop-user passwords
  - the login screen runs unprivileged and is on the screen
  - a wrong password typed on the (virtual) keyboard is refused, the
    right one starts the session as the unprivileged user
  - wallpaper and panel on screen; Terminal, Files, Settings and
    Assistant each open a window and keep running
  - privileged requests are checked by caller: status, login refused for
    users, password change (wrong/right), assistant answer, logout back
    to the login screen
Live ISO (--cdrom): the session starts without a login screen; the same
screen and application checks.
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
from persistence_boot_test import TEST_ROOT_PASSWORD, Vm, system_state  # noqa: E402

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


KEYS = {"-": "minus", " ": "spc", "\n": "ret", ".": "dot", "/": "slash", "_": ("shift", "minus")}


def type_text(qmp, text: str):
    """Type on the virtual machine's keyboard (QMP send-key)."""
    for ch in text:
        if ch.isdigit() or ("a" <= ch <= "z"):
            keys = [ch]
        elif "A" <= ch <= "Z":
            keys = ["shift", ch.lower()]
        else:
            k = KEYS[ch]
            keys = list(k) if isinstance(k, tuple) else [k]
        qmp.cmd("send-key", keys=[{"type": "qcode", "data": k} for k in keys])
        time.sleep(0.08)


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
    live = args.cdrom
    user = "cidint"
    run_dir = f"/run/cidint-session-{user}"
    session_unit = f"cidint-desktop@{user}.service"

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
        shows the wallpaper's colour."""
        hit = total = 0
        for p in SAMPLES:
            if p["v"] < 0.08:
                continue
            x, y = min(w - 1, int(p["u"] * w)), min(h - 1, int(p["v"] * h))
            i = (y * w + x) * 3
            total += 1
            hit += near(tuple(rgb[i:i + 3]), tuple(p["rgb"]), tol=16)
        return hit / total if total else 0.0

    def changed(a: bytes, b: bytes) -> float:
        return sum(1 for i in range(0, min(len(a), len(b)), 3 * 7)
                   if a[i:i + 3] != b[i:i + 3]) / max(1, len(a) // 21)

    def login_screen_look(rgb: bytes):
        px = [rgb[i:i + 3] for i in range(0, len(rgb), 3 * 13)]
        dark = sum(1 for r, g, b in px if r < 50 and g < 80 and b < 100) / len(px)
        bright = sum(1 for r, g, b in px if r > 200 and g > 200 and b > 200) / len(px)
        return dark, bright

    def diagnostics(vm) -> str:
        rc, out = vm.run(f"journalctl -b -u {session_unit} -u cidint-greeter.service -u cidint-authd.service "
                         "--no-pager -o cat | grep -v '^$' | tail -n 40; "
                         "journalctl -b -u seatd.service --no-pager -o cat | tail -n 6; ls /dev/dri 2>&1; "
                         f"id {user}; dmesg | grep -i -E 'drm|bochs|virtio_gpu|simple-framebuffer' | tail -n 6")
        return out

    def wait_active(vm, unit: str, tries: int = 30) -> str:
        state = ""
        for _ in range(tries):
            rc, state = vm.run(f"systemctl is-active {unit}")
            if state == "active":
                break
            time.sleep(2)
        return state

    def as_user(cmd: str) -> str:
        return f"su -s /bin/sh {user} -c \"{cmd}\""

    error = None
    log_path = work / "desktop-serial.log"
    with open(log_path, "w", encoding="utf-8") as log:
        vm = None
        try:
            vm = Vm(args.image if args.cdrom else disk, args.ovmf_code, nvram, log, cdrom=args.cdrom,
                    nic=["-nic", "none", "-qmp", f"unix:{qmp_path},server=on,wait=off"])
            qmp = Qmp(qmp_path)
            vm.login()
            step("boot: desktop image reaches a shell on the serial console", True)
            if not live and vm.first_start_prompts:
                # A fresh disk image (an installed disk got its passwords from
                # the installer and is not asked).
                step("first start: passwords were asked for root and the desktop user",
                     vm.first_start_prompts == 2, f"prompts={vm.first_start_prompts}")

            rc, out = vm.run("systemctl is-active cidint-authd.service")
            step("the login service (cidint-authd) is running", out == "active", out)

            if not live:
                # ---- login screen -------------------------------------------
                state = wait_active(vm, "cidint-greeter.service")
                step("the login screen service is active", state == "active", state + "\n" + diagnostics(vm))
                rc, out = vm.run("for p in $(pidof weston); do stat -c %U /proc/$p; done | sort -u")
                step("the login screen runs as the unprivileged user 'cidint-greeter'", out == "cidint-greeter", out)
                time.sleep(12)
                w, h, shot = screenshot(qmp, "login-screen")
                dark, bright = login_screen_look(shot)
                step("screen: the login screen is displayed (dark background, light text, no desktop)",
                     dark > 0.5 and bright > 0.001 and wallpaper_match(w, h, shot) < 0.5,
                     f"dark={dark:.2f} bright={bright:.4f} wallpaper={wallpaper_match(w, h, shot):.2f}\n"
                     + diagnostics(vm))

                type_text(qmp, "wrongpassword\n")
                time.sleep(8)
                rc, s1 = vm.run(f"systemctl is-active {session_unit}")
                rc, s2 = vm.run("systemctl is-active cidint-greeter.service")
                rc, j = vm.run("journalctl -b -u cidint-authd.service --no-pager -o cat | grep -c 'login failed'")
                step("a wrong password typed at the login screen is refused",
                     s1 != "active" and s2 == "active" and j not in ("", "0"), f"session={s1} greeter={s2} failed={j}")

                type_text(qmp, TEST_ROOT_PASSWORD + "\n")
                state = wait_active(vm, session_unit, 40)
                rc, g = vm.run("systemctl is-active cidint-greeter.service")
                step("the right password starts the desktop session; the login screen closes",
                     state == "active" and g != "active", f"session={state} greeter={g}\n"
                     + ("" if state == "active" else diagnostics(vm)))
            else:
                state = wait_active(vm, session_unit)
                rc, g = vm.run("systemctl is-active cidint-greeter.service")
                step("live medium: the desktop session starts without a login screen",
                     state == "active" and g != "active", f"session={state} greeter={g}\n" + diagnostics(vm))

            rc, out = vm.run("for p in $(pidof weston); do stat -c %U /proc/$p; done | sort -u")
            step(f"the compositor runs as the unprivileged user '{user}', not root", out == user, out)
            sock = ""
            for _ in range(15):
                rc, sock = vm.run(f"ls {run_dir}/ | grep -E '^wayland-[0-9]+$' | head -n 1")
                if sock:
                    break
                time.sleep(2)
            step("the session's Wayland socket exists", sock.startswith("wayland-"), sock or diagnostics(vm))

            time.sleep(6)
            w, h, base = screenshot(qmp, "desktop")
            bg = wallpaper_match(w, h, base)
            colours = len(Counter(base[i:i + 3] for i in range(0, len(base), 3 * 11)))
            step("screen: the CIDINT wallpaper and panel are displayed", bg > 0.85 and colours > 50,
                 f"{w}x{h}, {bg:.2f} of reference points match, {colours} colours")

            env = (f"XDG_RUNTIME_DIR={run_dir} WAYLAND_DISPLAY={sock} HOME=/home/{user} USER={user} "
                   "GDK_BACKEND=wayland")
            for app, args_, label in (("weston-terminal", "", "Terminal"), ("cidint-files", f"/home/{user}", "Files"),
                                      ("cidint-settings", "", "Settings"), ("cidint-assistant", "", "Assistant")):
                vm.run(as_user(f"{env} {app} {args_} >/tmp/{app}.log 2>&1 &"))
                diff, shot = 0.0, base
                for _ in range(12):
                    time.sleep(4)
                    w, h, shot = screenshot(qmp, f"app-{app}")
                    diff = changed(base, shot)
                    if diff > 0.03:
                        time.sleep(3)
                        w, h, shot = screenshot(qmp, f"app-{app}")
                        break
                rc, alive = vm.run(f"pgrep -f '{app}' >/dev/null && echo running || echo stopped")
                rc, applog = vm.run(f"tail -n 15 /tmp/{app}.log")
                step(f"app: {label} opens a window and keeps running",
                     diff > 0.03 and alive == "running" and "Traceback" not in applog,
                     f"{diff:.3f} of sampled pixels changed; {alive}\n{applog}")
                vm.run(f"pkill -f '{app}'; sleep 2")

            if not live:
                # ---- privileged requests, checked by caller identity ----------
                rc, out = vm.run(as_user("cidint-authctl status"))
                step("cidint-authd reports the running session", rc == 0 and f'"session_user": "{user}"' in out, out)
                rc, out = vm.run(as_user("echo x | cidint-authctl login cidint"))
                step("only the login screen may start sessions (a user's request is refused)",
                     rc != 0 and "login screen" in out, out)
                rc, before = vm.run(f"grep '^{user}:' /etc/shadow | cut -d: -f2")
                rc, out = vm.run(f"printf 'not-the-password\\nnewpassword-42\\n' | " + as_user("cidint-authctl passwd"))
                step("password change with a wrong current password is refused", rc != 0, out)
                rc, out = vm.run(f"printf '{TEST_ROOT_PASSWORD}\\nnewpassword-42\\n' | " + as_user("cidint-authctl passwd"))
                rc2, after = vm.run(f"grep '^{user}:' /etc/shadow | cut -d: -f2")
                rc3, ro = vm.run("awk '$2 == \"/\" { print $4 }' /proc/mounts | tail -n 1 | cut -d, -f1")
                step("password change works; new hash stored, system slot read-only again",
                     rc == 0 and after.startswith("$6$") and after != before and ro == "ro",
                     f"{out} changed={after != before} root={ro}")
                vm.run(f"printf 'newpassword-42\\n{TEST_ROOT_PASSWORD}\\n' | " + as_user("cidint-authctl passwd"))

                rc, out = vm.run(as_user("cidint-authctl ask 'What is two plus two? Answer with a number.'"),
                                 timeout=900)
                step("assistant: the session user gets an answer from the local AI engine",
                     rc == 0 and len(out.strip()) > 0, out[-500:])
                results[-1]["answer"] = out[-1000:]

                rc, out = vm.run(as_user("cidint-authctl logout"))
                state = wait_active(vm, "cidint-greeter.service", 30)
                rc, s = vm.run(f"systemctl is-active {session_unit}")
                time.sleep(10)
                w, h, shot = screenshot(qmp, "login-screen-after-logout")
                dark, bright = login_screen_look(shot)
                step("logout ends the session and the login screen returns",
                     state == "active" and s != "active" and dark > 0.5, f"greeter={state} session={s} dark={dark:.2f}")

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
