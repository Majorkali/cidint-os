#!/usr/bin/env python3
"""Install the CIDINT Desktop Edition from its live ISO onto a blank
virtual disk in QEMU, then test the installed system: the graphical
session (tests/qemu/desktop_test.py) and boot, reboot and power loss
(tests/qemu/persistence_boot_test.py).

    python3 tests/qemu/desktop_install_test.py DESKTOP-LIVE.iso --ovmf-code F --ovmf-vars F

Only a disposable virtual disk file is written. Exit code 0 only if every
check passed.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from persistence_boot_test import TEST_ROOT_PASSWORD, Vm  # noqa: E402

DISK_BYTES = 4 * 1024**3 + 64 * 1024**2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--ovmf-code", type=Path, required=True)
    ap.add_argument("--ovmf-vars", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test/desktop-install"))
    args = ap.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    nvram = args.workdir / "OVMF_VARS.fd"
    shutil.copyfile(args.ovmf_vars, nvram)
    disk = args.workdir / "desktop-target.img"
    with open(disk, "wb") as f:
        f.truncate(DISK_BYTES)

    results = []

    def step(name: str, ok: bool, detail: str = ""):
        results.append({"test": name, "passed": bool(ok), "detail": str(detail)[-2000:]})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""),
              flush=True)

    error = None
    installed = False
    log_path = args.workdir / "desktop-install-serial.log"
    with open(log_path, "w", encoding="utf-8") as log:
        vm = None
        try:
            vm = Vm(args.image, args.ovmf_code, nvram, log, cdrom=True, extra_disk=disk)
            vm.login()
            step("boot: Desktop Edition live ISO with a blank disk reaches a shell", vm.autologin)
            rc, out = vm.run("systemctl is-active cidint-desktop@cidint.service")
            step("the live session's desktop is running while installing", out == "active", out)
            vm.run(f"umask 077; echo '{TEST_ROOT_PASSWORD}' > /run/t-root")
            rc, out = vm.run("cidint-install --target /dev/sda --confirm-erase /dev/sda "
                             "--root-password-file /run/t-root </dev/null")
            step("refuses to install the Desktop Edition without a desktop user password (exit 7)",
                 rc == 7 and "desktop user" in out, out)
            rc, out = vm.run("cidint-install --target /dev/sda --confirm-erase /dev/sda "
                             "--root-password-file /run/t-root --user-password-file /run/t-root", timeout=1800)
            step("installation from the desktop live ISO completes", rc == 0 and "installation finished" in out, out)
            rc, out = vm.run("mkdir -p /run/t-root-a && mount -o ro /dev/sda2 /run/t-root-a && "
                             "ls /run/t-root-a/usr/bin/weston /run/t-root-a/usr/share/cidint/wallpaper.png; "
                             "umount /run/t-root-a")
            step("the installed system slot holds the desktop", rc == 0 and "weston" in out, out)
            vm.send("sync; systemctl poweroff")
            vm.wait_exit(180)
            installed = True
        except Exception as e:  # report, do not hide
            error = f"{type(e).__name__}: {e}"
            step("installer run completed", False, error)
        finally:
            if vm and vm.proc.poll() is None:
                vm.kill()

    if installed and all(r["passed"] for r in results):
        for name, script, extra in (
                ("installed desktop: graphical session and screen contents", "desktop_test.py", []),
                ("installed desktop: boot, reboot and power-loss tests", "persistence_boot_test.py",
                 ["--root-password", TEST_ROOT_PASSWORD])):
            sub = args.workdir / ("installed-" + script.split("_")[0])
            proc = subprocess.run(
                [sys.executable, str(HERE / script), str(disk),
                 "--ovmf-code", str(args.ovmf_code), "--ovmf-vars", str(args.ovmf_vars),
                 "--workdir", str(sub)] + extra,
                capture_output=True, text=True)
            print(proc.stdout, end="")
            print(proc.stderr, end="", file=sys.stderr)
            step(name, proc.returncode == 0, proc.stdout[-1500:])
            # Disk copies are several GB; keep only logs, reports, screenshots.
            for big in list(sub.glob("*.wic")) + list(sub.glob("*.img")):
                big.unlink()
        disk.unlink()

    passed = all(r["passed"] for r in results)
    summary = {"image": str(args.image), "passed": passed, "results": results, "error": error}
    (args.workdir / "desktop-install-report.json").write_text(json.dumps(summary, indent=2))
    if not passed and error:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
        clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\r", "", raw)
        lines = [ln for ln in clean.splitlines() if ln.strip()]
        print("--- serial console, last 30 lines ---")
        for ln in lines[-30:]:
            print(ln[:300])
    print(f"\n{sum(r['passed'] for r in results)}/{len(results)} checks passed")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
