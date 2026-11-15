#!/usr/bin/env python3
"""Install CIDINT OS in QEMU, then test A/B system updates on the installed
disk: update to the spare slot, trial start, commit, and rollback.

    python3 tests/qemu/update_test.py IMAGE.iso --ovmf-code F --ovmf-vars F

Only a disposable virtual disk file is written. Exit code 0 only if every
check passed.

The update that is installed is the same system version as the running
one (taken from the live medium). This tests the mechanism - writing the
spare slot, switching, confirming, going back - not a version change.

Sequence:
  live session  install; leave an update directory on the data volume
  start 1 (A)   a damaged update is refused and slot B stays empty;
                the update is written to B
  start 2 (B)   the trial slot runs, user data is still there, and the
                slot is committed automatically once the system is up
  start 3 (B)   B is still used. Automatic confirmation is put on hold
                and an update is written to A
  start 4 (A)   the trial runs but is not confirmed
  start 5 (B)   the unconfirmed trial was abandoned: B is used again
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from persistence_boot_test import Vm, system_state  # noqa: E402

DISK_BYTES = 4 * 1024**3 + 64 * 1024**2
BUNDLE = "/data/update-bundle"


def status(vm) -> dict:
    rc, out = vm.run("cidint-slot status")
    info = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    info["_rc"], info["_raw"] = rc, out
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--ovmf-code", type=Path, required=True)
    ap.add_argument("--ovmf-vars", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test/update"))
    args = ap.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    nvram = args.workdir / "OVMF_VARS.fd"
    shutil.copyfile(args.ovmf_vars, nvram)
    disk = args.workdir / "update-target.img"
    with open(disk, "wb") as f:
        f.truncate(DISK_BYTES)

    results = []

    def step(name: str, ok: bool, detail: str = ""):
        results.append({"test": name, "passed": bool(ok), "detail": str(detail)[-2000:]})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""),
              flush=True)

    def running_slot(vm) -> str:
        rc, out = vm.run("tr ' ' '\\n' < /proc/cmdline | sed -n 's/^cidint.slot=//p'")
        return out

    error = None
    log_path = args.workdir / "update-serial.log"
    with open(log_path, "w", encoding="utf-8") as log:
        vm = None
        try:
            # ---- live session: install, stage the update ----------------
            vm = Vm(args.image, args.ovmf_code, nvram, log, cdrom=True, extra_disk=disk)
            vm.login()
            rc, out = vm.run("cidint-install --target /dev/sda --confirm-erase /dev/sda", timeout=1500)
            step("installation completes", rc == 0 and "installation finished" in out, out)
            rc, out = vm.run(
                "mkdir -p /run/t-src /run/t-data && mount -o ro /dev/sr0 /run/t-src && "
                "mount /dev/sda4 /run/t-data && mkdir -p /run/t-data/update-bundle && "
                "cp /run/t-src/rootfs.img /run/t-src/bzImage /run/t-data/update-bundle/ && sync && "
                "umount /run/t-data /run/t-src && echo staged",
                timeout=600)
            step("an update directory is placed on the data volume", rc == 0 and "staged" in out, out)
            vm.send("sync; systemctl poweroff")
            vm.wait_exit(180)

            # ---- start 1: slot A ----------------------------------------
            vm = Vm(disk, args.ovmf_code, nvram, log)
            vm.login()
            st = status(vm)
            step("start 1: running and committed slot are A, no trial pending",
                 st["_rc"] == 0 and st.get("running slot", "").startswith("a ") and st.get("committed slot") == "a"
                 and st.get("trial slot") == "", st["_raw"])
            vm.run("echo 'kept across updates' > /home/root/update-kept.txt && sync")

            vm.run(f"cd {BUNDLE} && sha256sum rootfs.img bzImage > update.sha256 && "
                   f"cp update.sha256 good.sha256 && sed -i '1s/^[0-9a-f]*/{'0' * 64}/' update.sha256")
            rc, out = vm.run(f"cmp -s {BUNDLE}/update.sha256 {BUNDLE}/good.sha256; echo differs=$?")
            rc, out2 = vm.run(f"cidint-slot install {BUNDLE}", timeout=600)
            rc_b, type_b = vm.run("blkid -o value -s TYPE /dev/sda3")
            step("a damaged update is refused (exit 5) and slot B stays empty",
                 "differs=1" in out and rc == 5 and type_b == "", f"{out} rc={rc} {out2} sda3 type={type_b!r}")

            vm.run(f"cp {BUNDLE}/good.sha256 {BUNDLE}/update.sha256")
            rc, out = vm.run(f"cidint-slot install {BUNDLE}", timeout=900)
            st = status(vm)
            step("the update is written to slot B; trial pending, A still committed",
                 rc == 0 and st.get("trial slot") == "b" and st.get("committed slot") == "a", out + st["_raw"])
            vm.send("systemctl reboot")

            # ---- start 2: trial of slot B -------------------------------
            vm.login()
            slot = running_slot(vm)
            rc, rootarg = vm.run("readlink -f /dev/disk/by-partuuid/$(tr ' ' '\\n' < /proc/cmdline | "
                                 "sed -n 's/^root=PARTUUID=//p')")
            step("start 2: the trial slot B is running from partition 3",
                 slot == "b" and rootarg == "/dev/sda3", f"slot={slot} root={rootarg}")
            rc, out = vm.run("cat /home/root/update-kept.txt")
            step("user data is unchanged after the slot switch", rc == 0 and out == "kept across updates", out)
            st = {}
            for _ in range(40):
                st = status(vm)
                if st.get("committed slot") == "b":
                    break
                time.sleep(5)
            step("slot B is committed automatically once the system is up",
                 st.get("committed slot") == "b" and st.get("trial slot") == "", st.get("_raw", ""))
            ok, out = system_state(vm)
            step("no failed units on the updated system", ok, out)
            vm.send("systemctl reboot")

            # ---- start 3: B stays; prepare a trial that is not confirmed -
            vm.login()
            step("start 3: slot B is still used after a restart", running_slot(vm) == "b", running_slot(vm))
            vm.run("mkdir -p /data/config/cidint && touch /data/config/cidint/hold-update-confirm")
            rc, out = vm.run(f"cidint-slot install {BUNDLE}", timeout=900)
            st = status(vm)
            step("an update is written to slot A; trial pending, B still committed",
                 rc == 0 and st.get("trial slot") == "a" and st.get("committed slot") == "b", out + st["_raw"])
            vm.send("systemctl reboot")

            # ---- start 4: trial of A, deliberately not confirmed --------
            vm.login()
            slot = running_slot(vm)
            time.sleep(45)  # past the automatic check, which is on hold
            st = status(vm)
            step("start 4: trial slot A is running and stays unconfirmed",
                 slot == "a" and st.get("committed slot") == "b" and st.get("trial slot") == "a"
                 and st.get("trial started") == "yes", f"slot={slot} {st['_raw']}")
            vm.kill()  # power cut during an unconfirmed trial

            # ---- start 5: back on the committed slot --------------------
            vm = Vm(disk, args.ovmf_code, nvram, log)
            vm.login()
            slot = running_slot(vm)
            st = status(vm)
            step("start 5: the unconfirmed trial was abandoned, committed slot B is running",
                 slot == "b" and st.get("committed slot") == "b" and st.get("trial slot") == ""
                 and st.get("rolled back") == "yes", f"slot={slot} {st['_raw']}")
            rc, out = vm.run("cat /home/root/update-kept.txt")
            step("user data is unchanged after the rollback", rc == 0 and out == "kept across updates", out)
            ok, out = system_state(vm)
            step("no failed units after the rollback", ok, out)

            vm.send("systemctl poweroff")
            vm.wait_exit(180)
        except Exception as e:  # report, do not hide
            error = f"{type(e).__name__}: {e}"
            step("test run completed", False, error)
        finally:
            if vm and vm.proc.poll() is None:
                vm.kill()

    passed = all(r["passed"] for r in results)
    summary = {"image": str(args.image), "kvm": os.access("/dev/kvm", os.W_OK), "passed": passed,
               "results": results, "error": error}
    (args.workdir / "update-report.json").write_text(json.dumps(summary, indent=2))
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
