#!/usr/bin/env python3
"""Desktop Edition: sound, media player, web browser, software store and
the OpenGL renderer, tested in QEMU with a virtual sound card (its output
is recorded to a WAV file on the host) and a network card.

    python3 tests/qemu/desktop_apps_test.py DESKTOP.wic --app-repo DIR --ovmf-code F --ovmf-vars F

--app-repo is a signed software source built by scripts/build-app-repo.py
with the key the image trusts. The host serves it, and a test web page,
over QEMU's user network. Exit code 0 only if every check passed.

What this does NOT show: hardware-accelerated graphics (QEMU's display
has no GPU; the OpenGL check runs Mesa's software OpenGL), real sound
chips, or web sites other than the local test page.
"""
import argparse
import functools
import http.server
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from desktop_test import SAMPLES, Qmp, near, read_ppm, type_text, write_png  # noqa: E402
from persistence_boot_test import TEST_ROOT_PASSWORD, Vm, sparse_copy, system_state  # noqa: E402

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>CIDINT browser test</title>
<style>html,body{margin:0;height:100%;background:#ff00ff;color:#000;font:bold 64px sans-serif}</style>
</head><body><p style="padding:40px">CIDINT browser test page</p></body></html>
"""


def wav_peak(path: Path, start: int) -> int:
    """Largest absolute 16-bit sample written after byte offset start."""
    data = path.read_bytes()[max(44, start):]
    data = data[: len(data) // 2 * 2]
    if not data:
        return 0
    samples = struct.unpack(f"<{len(data) // 2}h", data)
    return max(abs(s) for s in samples)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--app-repo", type=Path, required=True)
    ap.add_argument("--ovmf-code", type=Path, required=True)
    ap.add_argument("--ovmf-vars", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test/desktop-apps"))
    args = ap.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    work = args.workdir.resolve()
    disk, nvram, wav = work / "disk.wic", work / "OVMF_VARS.fd", work / "sound-output.wav"
    sparse_copy(args.image, disk)
    shutil.copyfile(args.ovmf_vars, nvram)
    qmp_path = f"/tmp/cidint-apps-qmp-{os.getpid()}.sock"

    # Web root: the test page, the signed repository and a tampered copy.
    web = Path(tempfile.mkdtemp())
    (web / "cidint-test.html").write_text(PAGE)
    shutil.copytree(args.app_repo, web / "repo")
    shutil.copytree(args.app_repo, web / "badrepo")
    idx = web / "badrepo" / "index.json"
    idx.write_bytes(idx.read_bytes().replace(b"Calculator", b"Calculat0r"))
    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(web)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://10.0.2.2:{httpd.server_address[1]}"

    results = []

    def step(name: str, ok: bool, detail: str = ""):
        results.append({"test": name, "passed": bool(ok), "detail": str(detail)[-2500:]})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""),
              flush=True)

    def screenshot(qmp, name):
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

    def share(rgb, test) -> float:
        px = [rgb[i:i + 3] for i in range(0, len(rgb), 3 * 17)]
        return sum(1 for p in px if test(*p)) / len(px)

    def wallpaper_match(w, h, rgb) -> float:
        hit = total = 0
        for p in SAMPLES:
            if p["v"] < 0.08:
                continue
            x, y = min(w - 1, int(p["u"] * w)), min(h - 1, int(p["v"] * h))
            i = (y * w + x) * 3
            total += 1
            hit += near(tuple(rgb[i:i + 3]), tuple(p["rgb"]), tol=16)
        return hit / total if total else 0.0

    user, run_dir = "cidint", "/run/cidint-session-cidint"

    def as_user(cmd: str) -> str:
        return f"su -s /bin/sh {user} -c \"{cmd}\""

    def wait_for(vm, command, want, tries=30, delay=2):
        out = ""
        for _ in range(tries):
            rc, out = vm.run(command)
            if want(out):
                return True, out
            time.sleep(delay)
        return False, out

    def log_in(vm, qmp):
        ok, out = wait_for(vm, "systemctl is-active cidint-greeter.service", lambda o: o == "active")
        time.sleep(8)
        type_text(qmp, TEST_ROOT_PASSWORD + "\n")
        ok, out = wait_for(vm, f"systemctl is-active cidint-desktop@{user}.service", lambda o: o == "active", 40)
        sock = ""
        if ok:
            ok2, sock = wait_for(vm, f"ls {run_dir}/ | grep -E '^wayland-[0-9]+$' | head -n 1", lambda o: bool(o))
        time.sleep(6)
        return ok, sock

    error = None
    log_path = work / "desktop-apps-serial.log"
    with open(log_path, "w", encoding="utf-8") as log:
        vm = None
        try:
            vm = Vm(disk, args.ovmf_code, nvram, log, nic=[
                "-nic", "user,model=e1000", "-qmp", f"unix:{qmp_path},server=on,wait=off",
                "-audiodev", f"wav,id=snd0,path={wav}", "-device", "intel-hda", "-device", "hda-duplex,audiodev=snd0"])
            qmp = Qmp(qmp_path)
            vm.login()
            ok, sock = log_in(vm, qmp)
            step("desktop session started from the login screen", ok and sock.startswith("wayland-"), sock)
            env = (f"XDG_RUNTIME_DIR={run_dir} WAYLAND_DISPLAY={sock} HOME=/home/{user} USER={user} "
                   "GDK_BACKEND=wayland")
            w, h, desk = screenshot(qmp, "apps-desktop")

            # ---- sound -----------------------------------------------------
            rc, cards = vm.run("cat /proc/asound/cards")
            step("sound: the virtual HDA sound card has a driver", rc == 0 and "HDA" in cards.upper(), cards)
            rc, ctl = vm.run(as_user("amixer scontrols"))
            step("sound: the mixer has volume controls the desktop user may change", rc == 0 and "'" in ctl, ctl)
            before = wav.stat().st_size if wav.exists() else 0
            rc, out = vm.run(as_user("aplay -q /usr/share/cidint/sounds/test-chime.wav"), timeout=60)
            time.sleep(2)
            peak = wav_peak(wav, before) if wav.exists() else 0
            step("sound: the test sound comes out of the sound card (recorded on the host)",
                 rc == 0 and peak > 1000, f"rc={rc} {out} peak={peak}")

            # ---- media player ----------------------------------------------
            rc, out = vm.run(as_user(
                "mkdir -p /home/cidint/Videos && gst-launch-1.0 -q videotestsrc num-buffers=300 pattern=smpte "
                "! video/x-raw,width=640,height=360,framerate=30/1 ! vp8enc deadline=1 ! webmmux name=m "
                "! filesink location=/home/cidint/Videos/test.webm audiotestsrc num-buffers=470 freq=660 "
                "! audioconvert ! vorbisenc ! m."), timeout=600)
            rc2, size = vm.run("stat -c %s /home/cidint/Videos/test.webm")
            step("media: a WebM (VP8 + Vorbis) file is made with GStreamer", rc == 0 and size.isdigit()
                 and int(size) > 10000, f"{out} size={size}")
            before = wav.stat().st_size
            vm.run(as_user(f"{env} cidint-media /home/cidint/Videos/test.webm >/tmp/media.log 2>&1 &"))
            bars = 0.0
            for _ in range(10):
                time.sleep(3)
                w, h, shot = screenshot(qmp, "app-media-playing")
                # SMPTE bars: saturated yellow, cyan and magenta areas.
                bars = (share(shot, lambda r, g, b: r > 170 and g > 170 and b < 60)
                        + share(shot, lambda r, g, b: r < 60 and g > 170 and b > 170)
                        + share(shot, lambda r, g, b: r > 170 and g < 60 and b > 170))
                if bars > 0.03:
                    break
            time.sleep(3)
            peak = wav_peak(wav, before)
            rc, mlog = vm.run("tail -n 10 /tmp/media.log")
            step("media: the player shows the video", bars > 0.03, f"colour-bar share {bars:.3f}\n{mlog}")
            step("media: the player's sound reaches the sound card", peak > 1000, f"peak={peak}\n{mlog}")
            vm.run("pkill -f cidint-media; sleep 2")

            # ---- web browser -----------------------------------------------
            vm.run(as_user(f"{env} cidint-browser {base}/cidint-test.html >/tmp/browser.log 2>&1 &"))
            ok, blog = wait_for(vm, "grep -c 'loaded http://10.0.2.2' /tmp/browser.log || true",
                                lambda o: o not in ("", "0"), 45, 3)
            time.sleep(4)
            w, h, shot = screenshot(qmp, "app-browser-page")
            magenta = share(shot, lambda r, g, b: r > 200 and g < 60 and b > 200)
            rc, blog = vm.run("tail -n 12 /tmp/browser.log")
            step("browser: loads a web page over the network and reports it", ok and "CIDINT browser test" in blog,
                 blog)
            step("browser: the page is drawn on the screen", magenta > 0.25, f"page colour share {magenta:.2f}")
            vm.run("pkill -f cidint-browser; pkill -f WebKit; sleep 2")

            # ---- software store --------------------------------------------
            rc, out = vm.run(f"cidint-store source {base}/badrepo && " + as_user("cidint-authctl store-refresh"))
            step("store: a catalogue altered after signing is refused", rc != 0 and "signature" in out, out)
            rc, out = vm.run(f"cidint-store source {base}/repo && " + as_user("cidint-authctl store-refresh"),
                             timeout=120)
            step("store: the signed catalogue is verified and lists the apps",
                 rc == 0 and out.count("installed=") == 3, out)
            rc, out = vm.run(as_user("cidint-authctl store-install org.cidint.calculator"), timeout=180)
            rc2, calc = vm.run(as_user("/data/apps/store/org.cidint.calculator/bin/calculator --eval '6*7'"))
            rc3, owner = vm.run("stat -c %U:%a /data/apps/store/org.cidint.calculator")
            step("store: a desktop user installs an app; it runs and is not writable by the user",
                 rc == 0 and calc == "42" and owner == "root:755", f"{out} calc={calc} owner={owner}")
            vm.run(as_user(f"{env} cidint-software >/tmp/software.log 2>&1 &"))
            time.sleep(12)
            w, h, shot = screenshot(qmp, "app-software")
            diff = sum(1 for i in range(0, min(len(desk), len(shot)), 3 * 7)
                       if desk[i:i + 3] != shot[i:i + 3]) / max(1, len(desk) // 21)
            rc, slog = vm.run("pgrep -f cidint-software >/dev/null && echo running; tail -n 5 /tmp/software.log")
            step("store: the Software app opens", diff > 0.03 and "running" in slog and "Traceback" not in slog,
                 f"{diff:.3f} changed\n{slog}")
            vm.run("pkill -f cidint-software")
            rc, out = vm.run(as_user("cidint-authctl store-remove org.cidint.calculator") +
                             "; test -e /data/apps/store/org.cidint.calculator && echo still-there || echo gone")
            step("store: the app is removed again", out.endswith("gone"), out)

            # ---- OpenGL renderer (Mesa software OpenGL in QEMU) ------------
            vm.run(as_user("mkdir -p /home/cidint/.config/cidint && echo gl > /home/cidint/.config/cidint/renderer"))
            vm.run(as_user("cidint-authctl logout"))
            ok, sock = log_in(vm, qmp)
            rc, why = vm.run(f"cat {run_dir}/cidint-renderer")
            rc, wlog = vm.run(f"journalctl -b -u cidint-desktop@{user}.service --no-pager -o cat | "
                              "grep -i -E 'renderer|EGL|GL_RENDERER|llvmpipe' | tail -n 8")
            w, h, shot = screenshot(qmp, "desktop-gl-renderer")
            bg = wallpaper_match(w, h, shot)
            step("graphics: the session runs with Weston's OpenGL renderer (Mesa; software OpenGL in QEMU)",
                 ok and "OpenGL" in why and bg > 0.85, f"{why}\n{wlog}\nwallpaper={bg:.2f}")
            results[-1]["weston_gl_log"] = wlog

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
            httpd.shutdown()
            shutil.rmtree(web, ignore_errors=True)
            try:
                os.unlink(qmp_path)
            except OSError:
                pass
            disk.unlink(missing_ok=True)

    passed = all(r["passed"] for r in results)
    summary = {"image": str(args.image), "kvm": os.access("/dev/kvm", os.W_OK), "passed": passed,
               "results": results, "error": error}
    (work / "desktop-apps-report.json").write_text(json.dumps(summary, indent=2))
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
