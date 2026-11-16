#!/usr/bin/env python3
"""UEFI Secure Boot of a CIDINT OS disk image, inside QEMU.

    python3 tests/qemu/secureboot_test.py BASE.wic --keys KEYDIR \
        --ovmf-code OVMF_CODE_4M.secboot.fd --ovmf-vars OVMF_VARS_4M.fd \
        --ovmf-vars-ms OVMF_VARS_4M.ms.fd

KEYDIR is the boot key directory the image was built with
(scripts/make-keys.sh: PK, KEK and db certificates). The test enrolls them
in a copy of the firmware's variable store with virt-fw-vars (package
virt-firmware) and turns Secure Boot on. It checks:
  1. the signed image starts with Secure Boot on, and the system sees it;
  2. a kernel changed after signing is refused by GRUB;
  3. firmware that does not trust the CIDINT key (only Microsoft's keys)
     refuses to start the boot loader at all.
This is QEMU's firmware (OVMF/EDK II); real firmware differs in how keys
are enrolled.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from persistence_boot_test import Vm, sparse_copy  # noqa: E402

OWNER = "a2bcc8ec-6a2f-4b3c-9b84-7c5b9d3f0c11"  # signature owner GUID of the test keys
MENU = r"CIDINT OS \(system slot A\)"


def wait_text(vm, pattern: str, timeout: float) -> bool:
    try:
        vm.expect(pattern, timeout)
        return True
    except (TimeoutError, RuntimeError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--keys", type=Path, required=True)
    ap.add_argument("--ovmf-code", type=Path, required=True, help="OVMF built with Secure Boot (secboot)")
    ap.add_argument("--ovmf-vars", type=Path, required=True, help="empty variable store")
    ap.add_argument("--ovmf-vars-ms", type=Path, required=True, help="variable store with Microsoft keys only")
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test/secureboot"))
    args = ap.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)
    results = []

    def step(name: str, ok: bool, detail: str = ""):
        results.append({"test": name, "passed": bool(ok), "detail": str(detail)[-2000:]})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""),
              flush=True)

    disk, nvram = args.workdir / "disk.wic", args.workdir / "OVMF_VARS.sb.fd"
    error = None
    r = subprocess.run(["virt-fw-vars", "--input", str(args.ovmf_vars), "--output", str(nvram),
                        "--set-pk", OWNER, str(args.keys / "PK.crt"),
                        "--add-kek", OWNER, str(args.keys / "KEK.crt"),
                        "--add-db", OWNER, str(args.keys / "db.crt"), "--secure-boot"],
                       capture_output=True, text=True)
    step("the CIDINT test keys are enrolled in the firmware and Secure Boot is switched on",
         r.returncode == 0 and nvram.exists(), r.stdout[-800:] + r.stderr[-800:])

    log_path = args.workdir / "secureboot-serial.log"
    with open(log_path, "w", encoding="utf-8") as log:
        vm = None
        try:
            if r.returncode != 0:
                raise RuntimeError("no variable store with the test keys")
            sparse_copy(args.image, disk)
            vm = Vm(disk, args.ovmf_code, nvram, log, secure_boot=True)
            vm.login()
            step("the signed image starts with Secure Boot on (firmware -> GRUB -> kernel)", True)
            rc, sb = vm.run("od -An -t u1 -j 4 -N 1 "
                            "/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c")
            step("the running system sees Secure Boot enabled", sb.strip() == "1", sb)
            rc, st = vm.run("cidint-secureboot status")
            step("cidint-secureboot reports Secure Boot on and signed boot files",
                 "secure boot     : on" in st and "boot files      : signed" in st, st)
            rc, out = vm.run("mount -o remount,rw /boot 2>/dev/null; printf X >> /boot/bzImage-a && sync && echo changed")
            step("(set-up) one byte is added to the slot A kernel", out.endswith("changed"), out)
            vm.send("systemctl reboot")
            refused = wait_text(vm, r"(bad signature|verification requested|invalid signature)", 600)
            step("GRUB refuses the changed kernel", refused,
                 "no signature error on the console after the restart")
            booted = wait_text(vm, r"(login: |Choose a password)", 60)
            step("the changed kernel is not started", not booted)
            vm.kill()
            vm = None

            # Firmware that trusts only Microsoft's keys: GRUB must not start.
            sparse_copy(args.image, disk)
            ms = args.workdir / "OVMF_VARS.ms.fd"
            shutil.copyfile(args.ovmf_vars_ms, ms)
            vm = Vm(disk, args.ovmf_code, ms, log, secure_boot=True)
            started = wait_text(vm, MENU, 180)
            step("firmware without the CIDINT key refuses to start the boot loader", not started)
            vm.kill()
            vm = None
        except Exception as e:  # report, do not hide
            error = f"{type(e).__name__}: {e}"
            step("test run completed", False, error)
        finally:
            if vm and vm.proc.poll() is None:
                vm.kill()
            disk.unlink(missing_ok=True)

    passed = all(x["passed"] for x in results)
    (args.workdir / "secureboot-report.json").write_text(json.dumps(
        {"image": str(args.image), "kvm": os.access("/dev/kvm", os.W_OK), "passed": passed,
         "results": results, "error": error}, indent=2))
    if not passed:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
        clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\r", "", raw)
        print("--- serial console, last 40 lines ---")
        for ln in [x for x in clean.splitlines() if x.strip()][-40:]:
            print(ln[:300])
    print(f"\n{sum(x['passed'] for x in results)}/{len(results)} checks passed")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
