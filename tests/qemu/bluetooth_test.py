#!/usr/bin/env python3
"""Bluetooth on the Desktop Edition with virtual controllers, inside QEMU.

    python3 tests/qemu/bluetooth_test.py DESKTOP.wic --ovmf-code F --ovmf-vars F

QEMU has no Bluetooth radio. BlueZ's emulator (btvirt) creates two virtual
controllers through the kernel's hci_vhci driver; they behave like two
nearby Bluetooth devices. One plays a device in pairing mode; the desktop
user, through cidint-bluetooth (the code Settings uses), switches the other
on, finds it, pairs with it and forgets it again. This exercises the kernel
Bluetooth stack, BlueZ, CIDINT's D-Bus policy and pairing agent, but not
radio hardware, drivers of real adapters, or audio over Bluetooth.
"""
import argparse
import base64
import zlib
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

# Runs as root in the guest: makes the second controller discoverable and
# accepts pairing on it, like a headset in pairing mode.
PEER = r'''
import sys, gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib
b = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
A = "/org/bluez/" + sys.argv[1]
def s(p, v):
    b.call_sync("org.bluez", A, "org.freedesktop.DBus.Properties", "Set",
                GLib.Variant("(ssv)", ("org.bluez.Adapter1", p, v)), None, 0, -1, None)
s("Powered", GLib.Variant("b", True)); s("Pairable", GLib.Variant("b", True))
s("Discoverable", GLib.Variant("b", True))
x = Gio.DBusNodeInfo.new_for_xml("<node><interface name='org.bluez.Agent1'><method name='Release'/>"
    "<method name='RequestAuthorization'><arg type='o' direction='in'/></method>"
    "<method name='RequestConfirmation'><arg type='o' direction='in'/><arg type='u' direction='in'/></method>"
    "<method name='AuthorizeService'><arg type='o' direction='in'/><arg type='s' direction='in'/></method>"
    "<method name='Cancel'/></interface></node>")
b.register_object("/test/peer", x.interfaces[0], lambda *a: a[-1].return_value(None), None, None)
m = "org.bluez.AgentManager1"
b.call_sync("org.bluez", "/org/bluez", m, "RegisterAgent", GLib.Variant("(os)", ("/test/peer", "NoInputNoOutput")), None, 0, -1, None)
b.call_sync("org.bluez", "/org/bluez", m, "RequestDefaultAgent", GLib.Variant("(o)", ("/test/peer",)), None, 0, -1, None)
print("peer ready", flush=True)
GLib.MainLoop().run()
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--ovmf-code", type=Path, required=True)
    ap.add_argument("--ovmf-vars", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test/bluetooth"))
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

    error = None
    log_path = args.workdir / "bluetooth-serial.log"
    with open(log_path, "w", encoding="utf-8") as log:
        vm = None
        try:
            vm = Vm(disk, args.ovmf_code, nvram, log)
            vm.login()
            rc, out = vm.run("systemctl is-active bluetooth.service")
            step("the Bluetooth service (bluetoothd) is running", out == "active", out)
            rc, out = vm.run(as_user("cidint-bluetooth adapters"))
            step("without a controller, cidint-bluetooth says so (exit 0, nothing listed)", rc == 0 and not out, out)

            vm.run("modprobe hci_vhci && (btvirt -l2 >/tmp/btvirt.log 2>&1 &) ; sleep 5")
            rc, hcis = vm.run("ls /sys/class/bluetooth")
            step("two virtual controllers appear (btvirt through hci_vhci)",
                 "hci0" in hcis and "hci1" in hcis, hcis + "\n" + vm.run("cat /tmp/btvirt.log")[1])
            ok, out = False, ""
            for _ in range(15):
                rc, out = vm.run(as_user("cidint-bluetooth adapters"))
                if out.count("/org/bluez/hci") >= 2:
                    ok = True
                    break
                time.sleep(2)
            step("the desktop user (group bluetooth) sees both controllers through BlueZ", ok, out)
            peer_addr = ""
            m = re.search(r"/org/bluez/hci1 ([0-9A-F:]{17})", out)
            if m:
                peer_addr = m.group(1)

            code = base64.b64encode(zlib.compress(PEER.encode(), 9)).decode()
            vm.run("python3 -c \"import zlib, base64, sys; open('/tmp/peer.py', 'w').write("
                   f"zlib.decompress(base64.b64decode(sys.argv[1])).decode())\" {code}"
                   " && (python3 /tmp/peer.py hci1 >/tmp/peer.log 2>&1 &) ; sleep 4; cat /tmp/peer.log")
            rc, out = vm.run(as_user("cidint-bluetooth power on"))
            step("the desktop user switches the controller on", rc == 0 and "powered on" in out, out)
            rc, out = vm.run(as_user("cidint-bluetooth scan 12"), timeout=90)
            step("a search finds the other device", bool(peer_addr) and peer_addr in out,
                 f"peer={peer_addr}\n{out}")
            rc, out = vm.run(f"yes y | " + as_user(f"cidint-bluetooth pair {peer_addr}"), timeout=120)
            step("pairing succeeds (the user is asked to confirm)", rc == 0 and "paired" in out, out)
            rc, out = vm.run(as_user("cidint-bluetooth devices"))
            step("the device is listed as paired", re.search(rf"{peer_addr} (paired|connected)", out) is not None, out)
            rc, out = vm.run(as_user(f"cidint-bluetooth forget {peer_addr}"))
            rc2, out2 = vm.run(as_user("cidint-bluetooth devices"))
            step("forgetting removes the pairing", rc == 0 and not re.search(rf"{peer_addr} paired", out2),
                 out + "\n" + out2)
            rc, out = vm.run("su -s /bin/sh nobody -c 'cidint-bluetooth adapters' 2>&1; echo rc=$?")
            step("a user outside the group bluetooth cannot use BlueZ", "rc=0" not in out or "/org/bluez" not in out,
                 out)
            vm.run("pkill -f peer.py; pkill btvirt")

            # Driver helper: report, and firmware added on the data volume.
            rc, out = vm.run("cidint-drivers list")
            step("cidint-drivers lists devices and drivers (exit 0)", rc == 0 and out != "", out)
            rc, out = vm.run("mkdir -p /tmp/fw/test && echo test > /tmp/fw/test/cidint-test.bin && "
                             "cidint-drivers firmware add /tmp/fw && cat /sys/module/firmware_class/parameters/path "
                             "&& cidint-drivers firmware list", timeout=120)
            step("firmware from a folder is added to /data/firmware and the kernel looks there",
                 rc == 0 and "/data/firmware" in out and "test/cidint-test.bin" in out, out)
            rc, out = vm.run("cidint-drivers firmware remove && test ! -e /data/firmware && echo gone")
            step("added firmware can be removed again", out.endswith("gone"), out)
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
    (args.workdir / "bluetooth-report.json").write_text(json.dumps(
        {"image": str(args.image), "kvm": os.access("/dev/kvm", os.W_OK), "passed": passed,
         "results": results, "error": error}, indent=2))
    if not passed:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
        clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\r", "", raw)
        print("--- serial console, last 40 lines ---")
        for ln in [x for x in clean.splitlines() if x.strip()][-40:]:
            print(ln[:300])
    print(f"\n{sum(r['passed'] for r in results)}/{len(results)} checks passed")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
