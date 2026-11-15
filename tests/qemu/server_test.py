#!/usr/bin/env python3
"""Boot the CIDINT Server Edition image in QEMU and test SSH administration
over a real (user-mode) network connection.

    python3 tests/qemu/server_test.py IMAGE.wic --ovmf-code F --ovmf-vars F

The image is copied first; the build output is never modified. Exit code
0 only if every check passed. Needs the OpenSSH client tools on the host.

Checks:
  - the network comes up by itself (DHCP) and the SSH port is listening
  - a key-based login over the network works and reaches this very guest
  - a password login is refused, also for root with its empty
    development password
  - the firewall opens SSH only: another port is still dropped
  - the host key is on the data volume and is the same after a restart
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from persistence_boot_test import Vm, system_state  # noqa: E402
from firewall_test import drop_count, free_port  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--ovmf-code", type=Path, required=True)
    ap.add_argument("--ovmf-vars", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test/server"))
    args = ap.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    disk = args.workdir / "disk.wic"
    nvram = args.workdir / "OVMF_VARS.fd"
    shutil.copyfile(args.image, disk)
    shutil.copyfile(args.ovmf_vars, nvram)

    # Throwaway client key for this run.
    key = args.workdir / "client_key"
    for f in (key, Path(str(key) + ".pub")):
        f.unlink(missing_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "cidint-test", "-f", str(key)],
                   check=True)
    pubkey = Path(str(key) + ".pub").read_text().strip()

    ssh_port, other_port = free_port(), free_port()
    nic = ["-nic", f"user,model=e1000,hostfwd=tcp:127.0.0.1:{ssh_port}-:22,"
                   f"hostfwd=tcp:127.0.0.1:{other_port}-:5555"]
    base = ["-p", str(ssh_port), "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=20", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR"]

    def ssh(extra: list[str], command: str) -> subprocess.CompletedProcess:
        return subprocess.run(["ssh", *base, *extra, "root@127.0.0.1", command],
                              capture_output=True, text=True, timeout=90)

    def host_key() -> str:
        p = subprocess.run(["ssh-keyscan", "-T", "20", "-t", "ed25519", "-p", str(ssh_port), "127.0.0.1"],
                           capture_output=True, text=True, timeout=60)
        m = re.search(r"ssh-ed25519 (\S+)", p.stdout)
        return m.group(1) if m else ""

    results = []

    def step(name: str, ok: bool, detail: str = ""):
        results.append({"test": name, "passed": bool(ok), "detail": str(detail)[-2000:]})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""),
              flush=True)

    def wait_network(vm) -> str:
        out = ""
        for _ in range(20):
            rc, out = vm.run("ip -4 -o addr show scope global")
            if "10.0.2.15" in out:
                break
            time.sleep(3)
        return out

    error = None
    log_path = args.workdir / "server-serial.log"
    with open(log_path, "w", encoding="utf-8") as log:
        vm = None
        try:
            vm = Vm(disk, args.ovmf_code, nvram, log, nic=nic)
            vm.login()
            step("boot: server image reaches a login prompt", True)

            out = wait_network(vm)
            step("network: address obtained by DHCP without manual setup", "10.0.2.15" in out, out)
            rc, out = vm.run("systemctl is-active sshd.socket")
            step("SSH socket is active", out == "active", out)
            rc, out = vm.run("nft list ruleset")
            step("firewall: SSH rule loaded, default still drop",
                 "cidint-server-ssh" in out and out.count("policy drop") == 2, out)

            vm.run("mkdir -p /home/root/.ssh && chmod 700 /home/root/.ssh && "
                   f"echo '{pubkey}' > /home/root/.ssh/authorized_keys && chmod 600 /home/root/.ssh/authorized_keys")
            rc, boot_id = vm.run("cat /proc/sys/kernel/random/boot_id")

            p = ssh(["-i", str(key), "-o", "IdentitiesOnly=yes"], "cat /proc/sys/kernel/random/boot_id")
            ok = p.returncode == 0 and p.stdout.strip() == boot_id and boot_id != ""
            detail = f"rc={p.returncode} out={p.stdout!r} err={p.stderr!r}"
            if not ok:
                # Say why the daemon refused, as seen from inside the guest.
                rc, why = vm.run("journalctl -b --no-pager -o cat | grep -i sshd | tail -n 8; "
                                 "ls -ldn / /data /data/home /home /home/root /home/root/.ssh "
                                 "/home/root/.ssh/authorized_keys; wc -c /home/root/.ssh/authorized_keys; "
                                 "grep '^root:' /etc/passwd; cut -d: -f1,2 /etc/shadow | head -n 1 | cut -c1-12")
                detail += " | guest: " + why
            step("SSH: key-based login over the network reaches this guest", ok, detail)

            p = ssh(["-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password,keyboard-interactive"],
                    "true")
            step("SSH: login without the key is refused; passwords are not offered",
                 p.returncode != 0 and "Permission denied" in p.stderr and "password" not in p.stderr,
                 f"rc={p.returncode} err={p.stderr!r}")

            key1 = host_key()
            rc, out = vm.run("ls /data/server/ssh/ssh_host_ed25519_key")
            step("host key exists on the data volume", rc == 0 and key1 != "", f"{out} / scanned {key1[:20]}")

            before = drop_count(vm)
            for _ in range(3):
                try:
                    with socket.create_connection(("127.0.0.1", other_port), timeout=5) as c:
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
            step("firewall: a port other than SSH is still dropped",
                 before is not None and after is not None and after > before, f"counter {before} -> {after}")

            vm.send("systemctl reboot")
            vm.login()
            rc, boot_id2 = vm.run("cat /proc/sys/kernel/random/boot_id")
            wait_network(vm)
            key2 = host_key()
            step("restart: the SSH host key is unchanged", boot_id2 != boot_id and key1 != "" and key1 == key2,
                 f"{key1[:24]} vs {key2[:24]}")
            p = ssh(["-i", str(key), "-o", "IdentitiesOnly=yes"], "cidint persistence status >/dev/null && echo ok")
            step("restart: the authorised key still works (kept on /data)",
                 p.returncode == 0 and p.stdout.strip() == "ok", f"rc={p.returncode} err={p.stderr!r}")

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

    passed = all(r["passed"] for r in results)
    summary = {"image": str(args.image), "kvm": os.access("/dev/kvm", os.W_OK), "passed": passed,
               "results": results, "error": error}
    (args.workdir / "server-report.json").write_text(json.dumps(summary, indent=2))
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
