#!/usr/bin/env python3
"""Install CIDINT OS from the live ISO onto a blank virtual disk in QEMU,
then run the persistence acceptance tests on the installed disk.

    python3 tests/qemu/install_test.py IMAGE.iso --ovmf-code F --ovmf-vars F

Only a disposable virtual disk file is written. Exit code 0 only if every
check passed, including the persistence run on the installed system.

Sequence:
  live session   installer lists disks; dry run changes nothing; an
                 unconfirmed run and a run against the live medium are
                 refused; a confirmed run installs; a second run on the
                 now-populated disk is refused without --erase-existing
  installed disk tests/qemu/persistence_boot_test.py (boot, reboot,
                 power cut) with no ISO attached
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
from persistence_boot_test import Vm  # noqa: E402

DISK_BYTES = 4 * 1024**3 + 64 * 1024**2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--ovmf-code", type=Path, required=True)
    ap.add_argument("--ovmf-vars", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test"))
    args = ap.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    nvram = args.workdir / "OVMF_VARS.install.fd"
    shutil.copyfile(args.ovmf_vars, nvram)
    disk = args.workdir / "install-target.img"
    with open(disk, "wb") as f:
        f.truncate(DISK_BYTES)

    results = []

    def step(name: str, ok: bool, detail: str = ""):
        results.append({"test": name, "passed": bool(ok), "detail": detail[-2000:]})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""),
              flush=True)

    def partitions(vm) -> str:
        return vm.run("ls /sys/block/sda | grep -c '^sda[0-9]' || true")[1]

    error = None
    log_path = args.workdir / "install-serial.log"
    installed = False
    with open(log_path, "w", encoding="utf-8") as log:
        vm = None
        try:
            vm = Vm(args.image, args.ovmf_code, nvram, log, cdrom=True, extra_disk=disk)
            vm.login()
            step("boot: live ISO with a blank disk attached reaches a login prompt", True)

            rc, out = vm.run("cidint-install --list")
            step("installer lists the candidate disks", rc == 0 and "/dev/sda" in out, out)

            rc, out = vm.run("cidint-install --target /dev/sda --dry-run")
            step("dry run passes its checks", rc == 0 and "nothing was changed" in out, out)
            step("dry run left the disk untouched", partitions(vm) == "0", partitions(vm))

            rc, out = vm.run("cidint-install --target /dev/sda </dev/null")
            step("refuses to erase without confirmation (exit 7)", rc == 7 and partitions(vm) == "0", out)

            rc, out = vm.run("cidint-install --target /dev/sda --confirm-erase /dev/sdb")
            step("refuses a confirmation that names another disk (exit 7)", rc == 7 and partitions(vm) == "0", out)

            rc, out = vm.run("cidint-install --target /dev/sr0 --confirm-erase /dev/sr0")
            step("refuses the live medium as a target (exit 7)", rc == 7, out)

            rc, out = vm.run("cidint-install --target /dev/sda --confirm-erase /dev/sda", timeout=900)
            step("confirmed installation completes", rc == 0 and "installation finished" in out, out)
            step("the disk has the four CIDINT partitions", partitions(vm) == "4", partitions(vm))

            rc, out = vm.run("cidint-install --target /dev/sda --confirm-erase /dev/sda")
            step("refuses a disk that already holds data without --erase-existing (exit 7)", rc == 7, out)

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
        sub = args.workdir / "installed"
        proc = subprocess.run(
            [sys.executable, str(HERE / "persistence_boot_test.py"), str(disk),
             "--ovmf-code", str(args.ovmf_code), "--ovmf-vars", str(args.ovmf_vars),
             "--workdir", str(sub)],
            capture_output=True, text=True)
        print(proc.stdout, end="")
        print(proc.stderr, end="", file=sys.stderr)
        step("installed system passes the boot, reboot and power-loss tests", proc.returncode == 0,
             proc.stdout[-1500:])

    passed = all(r["passed"] for r in results)
    summary = {"image": str(args.image), "passed": passed, "results": results, "error": error}
    (args.workdir / "install-report.json").write_text(json.dumps(summary, indent=2))
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
