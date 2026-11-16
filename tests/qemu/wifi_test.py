#!/usr/bin/env python3
"""Wi-Fi on the Desktop Edition, end to end, inside QEMU.

    python3 tests/qemu/wifi_test.py DESKTOP.wic --ovmf-code F --ovmf-vars F

The kernel's mac80211_hwsim driver creates two simulated radios. iwd runs
an access point (WPA2) on one; the desktop user connects the other one
through cidint-wifi (the code Settings uses), gets an address from the
access point and reaches it. After a restart the saved network is joined
again without a password. This exercises the real Wi-Fi stack (cfg80211,
mac80211, iwd, WPA2 handshake, DHCP) but not radio hardware or drivers
for physical chips.
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
from persistence_boot_test import Vm, sparse_copy, system_state  # noqa: E402

SSID, PASS = "CidintTestNet", "cidint-wifi-pass9"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--ovmf-code", type=Path, required=True)
    ap.add_argument("--ovmf-vars", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test/wifi"))
    args = ap.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)
    disk, nvram = args.workdir / "disk.wic", args.workdir / "OVMF_VARS.fd"
    sparse_copy(args.image, disk)
    shutil.copyfile(args.ovmf_vars, nvram)

    results = []

    def step(name: str, ok: bool, detail: str = ""):
        results.append({"test": name, "passed": bool(ok), "detail": str(detail)[-2000:]})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""),
              flush=True)

    def as_user(cmd: str) -> str:
        return f"su -s /bin/sh cidint -c \"{cmd}\""

    def radios_and_ap(vm) -> str:
        vm.run("modprobe mac80211_hwsim radios=2")
        out = ""
        for _ in range(20):
            rc, out = vm.run("iwctl device list 2>&1 | sed 's/\\x1b\\[[0-9;]*m//g'")
            if "wlan0" in out and "wlan1" in out:
                break
            time.sleep(2)
        vm.run("iwctl device wlan0 set-property Mode ap")
        time.sleep(2)
        rc, out2 = vm.run(f"iwctl ap wlan0 start {SSID} {PASS} 2>&1; sleep 3; "
                          "iwctl ap wlan0 show 2>&1 | sed 's/\\x1b\\[[0-9;]*m//g'")
        return out + "\n" + out2

    error = None
    log_path = args.workdir / "wifi-serial.log"
    with open(log_path, "w", encoding="utf-8") as log:
        vm = None
        try:
            vm = Vm(disk, args.ovmf_code, nvram, log)
            vm.login()
            rc, out = vm.run("systemctl is-active iwd.service")
            step("iwd (Wi-Fi service) is running", out == "active", out)
            out = radios_and_ap(vm)
            step("two simulated radios appear; one runs a WPA2 access point",
                 "wlan0" in out and "wlan1" in out and SSID in out, out)

            rc, out = vm.run(as_user("cidint-wifi devices"))
            step("the desktop user (group netdev) sees the Wi-Fi device in station mode",
                 rc == 0 and "wlan1" in out and "station" in out, out)
            rc, out = vm.run(as_user("cidint-wifi scan wlan1"), timeout=60)
            step("a scan finds the access point", rc == 0 and SSID in out and "psk" in out, out)
            rc, out = vm.run(f"echo wrong-password-1 | " + as_user(f"cidint-wifi connect {SSID} wlan1"), timeout=120)
            step("a wrong password is refused", rc != 0, out)
            rc, out = vm.run(f"echo {PASS} | " + as_user(f"cidint-wifi connect {SSID} wlan1"), timeout=120)
            step("the right password connects (WPA2 handshake)", rc == 0 and "connected" in out, out)
            ok = False
            addr = ap_addr = ""
            for _ in range(20):
                rc, addr = vm.run("ip -4 -o addr show wlan1 | awk '{print $4}'")
                rc, ap_addr = vm.run("ip -4 -o addr show wlan0 | awk '{print $4}' | cut -d/ -f1")
                if addr and ap_addr:
                    ok = True
                    break
                time.sleep(2)
            rc, ping = vm.run(f"ping -c 2 -W 3 -I wlan1 {ap_addr}", timeout=30)
            step("the connection gets an address by DHCP and reaches the access point",
                 ok and rc == 0, f"station={addr} ap={ap_addr}\n{ping}")
            rc, out = vm.run(f"ls /data/config/iwd/ && stat -c %a /data/config/iwd")
            step("the saved network is kept on the data volume, readable by root only",
                 f"{SSID}.psk" in out and out.endswith("700"), out)

            vm.send("systemctl reboot")
            vm.login()
            radios_and_ap(vm)
            ok, out = False, ""
            for _ in range(30):
                rc, out = vm.run(as_user("cidint-wifi devices"))
                if f"connected={SSID}" in out:
                    ok = True
                    break
                time.sleep(3)
            step("after a restart the saved network is joined again without a password", ok, out)
            ok2, st = system_state(vm)
            step("no failed units", ok2, st)
            vm.send("systemctl poweroff")
            vm.wait_exit(180)
        except Exception as e:  # report, do not hide
            error = f"{type(e).__name__}: {e}"
            step("test run completed", False, error)
        finally:
            if vm and vm.proc.poll() is None:
                vm.kill()
            disk.unlink(missing_ok=True)

    passed = all(r["passed"] for r in results)
    summary = {"image": str(args.image), "kvm": os.access("/dev/kvm", os.W_OK), "passed": passed,
               "results": results, "error": error}
    (args.workdir / "wifi-report.json").write_text(json.dumps(summary, indent=2))
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
