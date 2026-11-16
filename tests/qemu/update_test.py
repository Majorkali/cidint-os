#!/usr/bin/env python3
"""Install CIDINT OS in QEMU, then test A/B system updates on the installed
disk: update to the spare slot, trial start, commit, and rollback.

    python3 tests/qemu/update_test.py IMAGE.iso --signing-key KEY.pem --ovmf-code F --ovmf-vars F

KEY.pem is the private Ed25519 key whose public half was built into the
image (CI makes a throwaway one per run). It stays on the host: the guest
only receives the signature.

Only a disposable virtual disk file is written. Exit code 0 only if every
check passed.

The update that is installed is the same system version as the running
one (taken from the live medium). This tests the mechanism - writing the
spare slot, switching, confirming, going back - not a version change.

Sequence:
  live session  install; leave an update directory on the data volume
  start 1 (A)   an unsigned update, one signed by an untrusted key, one
                whose signed list was altered and one with a damaged
                system image are all refused and slot B stays empty;
                the correctly signed update is written to B
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
import base64
import hashlib
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from persistence_boot_test import TEST_ROOT_PASSWORD, Vm, system_state  # noqa: E402

DISK_BYTES = 8 * 1024**3 + 64 * 1024**2
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


def sign(key: Path, data: bytes) -> str:
    """Ed25519 signature of data with the host's key, base64."""
    with tempfile.TemporaryDirectory() as tmp:
        msg, sig = Path(tmp) / "m", Path(tmp) / "s"
        msg.write_bytes(data)
        subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(key), "-rawin",
                        "-in", str(msg), "-out", str(sig)], check=True)
        return base64.b64encode(sig.read_bytes()).decode()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--ovmf-code", type=Path, required=True)
    ap.add_argument("--ovmf-vars", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test/update"))
    ap.add_argument("--signing-key", type=Path, required=True,
                    help="private Ed25519 key (PEM) trusted by the image")
    args = ap.parse_args()
    # A second key the image does not trust.
    rogue = Path(tempfile.mkdtemp()) / "rogue.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(rogue)], check=True)

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
            vm.run(f"umask 077; echo '{TEST_ROOT_PASSWORD}' > /run/t-root")
            rc, out = vm.run("cidint-install --target /dev/sda --confirm-erase /dev/sda "
                             "--root-password-file /run/t-root", timeout=1500)
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

            rc, out = vm.run("cidint-slot status | grep 'trusted keys'")
            step("the image trusts exactly one update key", out.endswith(": 1"), out)

            # The list of checksums is made in the guest, signed on the host.
            vm.run(f"cd {BUNDLE} && sha256sum rootfs.img bzImage > update.sha256", timeout=300)
            rc, listing = vm.run(f"cat {BUNDLE}/update.sha256")
            rc, guest_hash = vm.run(f"sha256sum {BUNDLE}/update.sha256 | cut -d' ' -f1")
            data = (listing + "\n").encode()
            step("the checksum list was read back exactly", hashlib.sha256(data).hexdigest() == guest_hash,
                 f"{guest_hash} {listing!r}")
            good_sig = sign(args.signing_key, data)

            def put_sig(b64: str):
                vm.run(f"echo '{b64}' | openssl base64 -d -A > {BUNDLE}/update.sha256.sig")

            def refused(name: str, setup: str = "", undo: str = ""):
                if setup:
                    vm.run(setup, timeout=120)
                rc, out = vm.run(f"cidint-slot install {BUNDLE}", timeout=900)
                if undo:
                    vm.run(undo, timeout=120)
                rc_b, type_b = vm.run("blkid -o value -s TYPE /dev/sda3")
                step(f"{name}: refused (exit 5), slot B untouched", rc == 5 and type_b == "",
                     f"rc={rc} {out} sda3 type={type_b!r}")

            refused("an unsigned update", f"rm -f {BUNDLE}/update.sha256.sig")
            put_sig(sign(rogue, data))
            refused("an update signed by an untrusted key")
            put_sig(good_sig)
            refused("a signed checksum list that was altered afterwards",
                    f"cp {BUNDLE}/update.sha256 /run/t-list && sed -i '1s/^/0/' {BUNDLE}/update.sha256",
                    f"cp /run/t-list {BUNDLE}/update.sha256")
            refused("a correctly signed update whose system image was damaged",
                    f"dd if={BUNDLE}/rootfs.img of=/run/t-byte bs=1 skip=600 count=1 2>/dev/null && "
                    f"printf X | dd of={BUNDLE}/rootfs.img bs=1 seek=600 conv=notrunc 2>/dev/null",
                    f"dd if=/run/t-byte of={BUNDLE}/rootfs.img bs=1 seek=600 conv=notrunc 2>/dev/null")
            rc, out = vm.run(f"cidint-slot verify {BUNDLE}", timeout=600)
            step("the restored, correctly signed update verifies", rc == 0 and "signature valid" in out, out)

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
