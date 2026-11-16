#!/usr/bin/env python3
"""Boot the CIDINT disk image in QEMU with a network card and check the
default-deny firewall with real traffic.

    python3 tests/qemu/firewall_test.py IMAGE.wic --ovmf-code F --ovmf-vars F

The image is copied first; the build output is never modified. Exit code
0 only if every check passed.

Checks:
  - the firewall unit is active and the loaded rule set drops by default
  - outbound: the guest opens a TCP connection to a listener on the test
    host and receives the reply (outbound and reply traffic allowed)
  - inbound: the test host opens a TCP connection to a guest port through
    a QEMU port forward; the packet must be counted by the final drop rule

Only QEMU's user-mode network is used: the guest can reach the test host,
nothing else is exposed.
"""
import argparse
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from persistence_boot_test import Vm, sparse_copy, system_state  # noqa: E402

GUEST_PORT = 5555


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def drop_count(vm) -> int | None:
    rc, out = vm.run("nft list chain inet cidint_filter input | grep cidint-default-deny")
    m = re.search(r"counter packets (\d+)", out)
    return int(m.group(1)) if rc == 0 and m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--ovmf-code", type=Path, required=True)
    ap.add_argument("--ovmf-vars", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test/firewall"))
    args = ap.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    disk = args.workdir / "disk.wic"
    nvram = args.workdir / "OVMF_VARS.fd"
    sparse_copy(args.image, disk)
    shutil.copyfile(args.ovmf_vars, nvram)

    results = []

    def step(name: str, ok: bool, detail: str = ""):
        results.append({"test": name, "passed": bool(ok), "detail": str(detail)[-2000:]})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""),
              flush=True)

    # Listener on the test host for the outbound check.
    received = {}
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(120)
    host_port = listener.getsockname()[1]

    def serve():
        try:
            conn, _ = listener.accept()
            conn.settimeout(10)
            received["data"] = conn.recv(100)
            conn.sendall(b"pong-from-host\n")
            conn.close()
        except OSError as e:
            received["error"] = str(e)

    fwd_port = free_port()
    nic = ["-nic", f"user,model=e1000,hostfwd=tcp:127.0.0.1:{fwd_port}-:{GUEST_PORT}"]

    error = None
    log_path = args.workdir / "firewall-serial.log"
    with open(log_path, "w", encoding="utf-8") as log:
        vm = None
        try:
            vm = Vm(disk, args.ovmf_code, nvram, log, nic=nic)
            vm.login()
            step("boot: image with a network card reaches a login prompt", True)

            rc, out = vm.run("systemctl is-active cidint-firewall.service")
            step("firewall unit is active", out == "active", out)
            rc, out = vm.run("nft list ruleset")
            step("loaded rule set: input and forward chains drop by default",
                 rc == 0 and out.count("policy drop") == 2 and "cidint-default-deny" in out, out)

            rc, ifname = vm.run("ls /sys/class/net | grep -v '^lo$' | head -n 1")
            step("the guest has a network interface", rc == 0 and ifname != "", ifname)
            vm.run(f"ip link set {ifname} up; ip addr add 10.0.2.15/24 dev {ifname} 2>/dev/null; sleep 3; true")
            rc, out = vm.run(f"ip -4 addr show dev {ifname}")
            step("the interface has the address 10.0.2.15", "10.0.2.15" in out, out)

            threading.Thread(target=serve, daemon=True).start()
            rc, out = vm.run(f"echo hello-from-guest | nc 10.0.2.2 {host_port}", timeout=60)
            step("outbound: guest connects to the test host and gets the reply",
                 "pong-from-host" in out and received.get("data", b"").startswith(b"hello-from-guest"),
                 f"guest saw {out!r}; host saw {received!r}")

            before = drop_count(vm)
            step("the default-deny rule has a packet counter", before is not None, str(before))
            for _ in range(3):
                try:
                    with socket.create_connection(("127.0.0.1", fwd_port), timeout=5) as c:
                        c.settimeout(3)
                        c.sendall(b"inbound-probe\n")
                        try:
                            c.recv(10)
                        except OSError:
                            pass
                except OSError:
                    pass
                time.sleep(1)
            after = drop_count(vm)
            step("inbound: a connection to a guest port is dropped by the default-deny rule",
                 before is not None and after is not None and after > before, f"counter {before} -> {after}")

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
            listener.close()

    passed = all(r["passed"] for r in results)
    summary = {"image": str(args.image), "kvm": os.access("/dev/kvm", os.W_OK), "passed": passed,
               "results": results, "error": error}
    (args.workdir / "firewall-report.json").write_text(json.dumps(summary, indent=2))
    if not passed:
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
