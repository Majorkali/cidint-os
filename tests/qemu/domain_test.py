#!/usr/bin/env python3
"""Boot the CIDINT image in QEMU and test security domains: start two
isolated micro-VMs inside it, check what they can see, stop them.

    python3 tests/qemu/domain_test.py IMAGE.wic --ovmf-code F --ovmf-vars F

The image is copied first; the build output is never modified. Exit code
0 only if every check passed.

Whether the domains ran with hardware virtualisation (KVM inside this
test VM, i.e. nested) or in software emulation is recorded in the report;
both are accepted, because nested virtualisation is not available on
every test host.
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

READY_TIMEOUT = 900  # software emulation inside a VM is slow


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--ovmf-code", type=Path, required=True)
    ap.add_argument("--ovmf-vars", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test/domains"))
    args = ap.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    disk = args.workdir / "disk.wic"
    nvram = args.workdir / "OVMF_VARS.fd"
    shutil.copyfile(args.image, disk)
    shutil.copyfile(args.ovmf_vars, nvram)

    results = []

    def step(name: str, ok: bool, detail: str = ""):
        results.append({"test": name, "passed": bool(ok), "detail": str(detail)[-2500:]})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""),
              flush=True)

    def wait_ready(vm, name: str) -> str:
        deadline = time.time() + READY_TIMEOUT
        log = ""
        while time.time() < deadline:
            rc, log = vm.run(f"cidint-domain log {name} 2>&1 | tail -n 40")
            if f"CIDINT domain '{name}' ready" in log:
                break
            rc, active = vm.run(f"systemctl is-active cidint-domain-{name}.service")
            if active not in ("active", "activating"):
                rc, j = vm.run(f"journalctl -b -u cidint-domain-{name}.service --no-pager -o cat | tail -n 12")
                return log + f"\n[unit {active}]\n" + j
            time.sleep(10)
        return log

    def field(log: str, key: str) -> str:
        m = re.search(rf"^{key}: ?(.*)$", log, re.M)
        return m.group(1).strip() if m else "<missing>"

    error = None
    accel = "unknown"
    log_path = args.workdir / "domains-serial.log"
    with open(log_path, "w", encoding="utf-8") as log:
        vm = None
        try:
            vm = Vm(disk, args.ovmf_code, nvram, log)
            vm.login()
            step("boot: image reaches a login prompt", True)

            rc, out = vm.run("cidint-domain info")
            accel = "kvm" if "available (KVM)" in out else "software emulation"
            step("domain tooling is installed", rc == 0 and "(installed)" in out, out)
            print(f"       acceleration inside the test VM: {accel}", flush=True)

            rc, out = vm.run("cidint-domain start 'Bad Name'")
            step("an invalid domain name is rejected (exit 2)", rc == 2, f"rc={rc} {out}")

            rc, out = vm.run("cidint-domain start work", timeout=120)
            step("domain 'work' starts", rc == 0, out)
            rc, out = vm.run("cidint-domain start personal -m 128", timeout=120)
            step("a second domain 'personal' starts alongside it", rc == 0, out)
            rc, out = vm.run("cidint-domain start work")
            step("starting a running domain again is refused (exit 7)", rc == 7, f"rc={rc} {out}")

            work = wait_ready(vm, "work")
            step("domain 'work' boots its own kernel and reports ready",
                 "CIDINT domain 'work' ready" in work, work)
            personal = wait_ready(vm, "personal")
            step("domain 'personal' reports ready", "CIDINT domain 'personal' ready" in personal, personal)

            step("isolation: a domain has no network interface except loopback",
                 field(work, "domain-interfaces").split() == ["lo"]
                 and field(personal, "domain-interfaces").split() == ["lo"],
                 field(work, "domain-interfaces") + " / " + field(personal, "domain-interfaces"))
            step("isolation: a domain has no disk", field(work, "domain-disks") == "" and
                 field(personal, "domain-disks") == "", field(work, "domain-disks"))
            mem = field(personal, "domain-memory-kb")
            step("the memory limit given at start applies (128 MiB)", mem.isdigit() and 60000 < int(mem) < 131072, mem)

            rc, out = vm.run("for p in $(pidof qemu-system-x86_64); do stat -c %U /proc/$p; done | sort -u")
            step("the emulator runs as the unprivileged user 'cidint-domain', not root", out.strip() == "cidint-domain", out)
            rc, out = vm.run("p=$(pidof qemu-system-x86_64 | cut -d' ' -f1); "
                             "sed '1,2d' /proc/$p/net/dev | cut -d: -f1 | tr -d ' ' | tr '\\n' ' '")
            step("the emulator's own sandbox has no network interface except loopback", out.split() == ["lo"], out)

            rc, out = vm.run("cidint-domain list | sort | tr '\\n' ' '")
            step("both domains are listed", out.split() == ["personal", "work"], out)
            rc, out = vm.run("cidint-domain stop work && cidint-domain list | tr '\\n' ' '", timeout=120)
            step("stopping one domain leaves the other running", rc == 0 and out.split()[-1:] == ["personal"], out)
            rc, out = vm.run("cidint-domain stop personal && cidint-domain list | wc -l", timeout=120)
            step("all domains stopped", rc == 0 and out.strip().endswith("0"), out)

            ok, out = system_state(vm)
            step("no failed units on the host system", ok, out)

            vm.send("systemctl poweroff")
            vm.wait_exit(180)
        except Exception as e:  # report, do not hide
            error = f"{type(e).__name__}: {e}"
            step("test run completed", False, error)
        finally:
            if vm and vm.proc.poll() is None:
                vm.kill()

    passed = all(r["passed"] for r in results)
    summary = {"image": str(args.image), "kvm": os.access("/dev/kvm", os.W_OK), "domain_acceleration": accel,
               "passed": passed, "results": results, "error": error}
    (args.workdir / "domains-report.json").write_text(json.dumps(summary, indent=2))
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
