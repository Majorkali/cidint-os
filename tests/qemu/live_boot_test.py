#!/usr/bin/env python3
"""Boot the CIDINT live ISO in QEMU (UEFI, as a CD-ROM) and check the
amnesic contract: acceptance test P15, "amnesic mode does not persist".

    python3 tests/qemu/live_boot_test.py IMAGE.iso --ovmf-code F --ovmf-vars F

The ISO is attached read-only as a CD-ROM; no disk is present in the VM.
Exit code 0 only if every check passed.

Sequence:
  boot 1  mode is live_amnesic and the layout is consistent with it, /data
          is RAM-backed; create a file under /home and a config value;
          clean reboot
  boot 2  the kernel boot id changed; the file and the value are gone
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from persistence_boot_test import Vm, system_state  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--ovmf-code", type=Path, required=True)
    ap.add_argument("--ovmf-vars", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test"))
    args = ap.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    nvram = args.workdir / "OVMF_VARS.live.fd"
    shutil.copyfile(args.ovmf_vars, nvram)

    results = []

    def step(name: str, ok: bool, detail: str = ""):
        results.append({"test": name, "passed": bool(ok), "detail": detail[-2000:]})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""),
              flush=True)

    error = None
    log_path = args.workdir / "live-serial.log"
    with open(log_path, "w", encoding="utf-8") as log:
        vm = None
        try:
            vm = Vm(args.image, args.ovmf_code, nvram, log, cdrom=True)
            vm.login()
            step("boot: live ISO reaches a login prompt under UEFI", True)

            rc, out = vm.run("cat /proc/cmdline")
            step("kernel command line declares cidint.mode=amnesic", "cidint.mode=amnesic" in out, out)

            rc, out = vm.run("cidint persistence status --json")
            report = json.loads(out)["data"] if out.startswith("{") else {}
            step("persistence status: mode 'live_amnesic', layout consistent with it",
                 rc == 0 and report.get("mode") == "live_amnesic" and report.get("consistent") is True, out)

            rc, out = vm.run("grep ' /data ' /proc/mounts")
            step("/data is RAM-backed (tmpfs)", rc == 0 and " tmpfs " in out, out)

            rc, out = vm.run("echo 'live session file' > /home/root/session.txt && cat /home/root/session.txt")
            step("a file can be created during the session", rc == 0 and out == "live session file", out)
            rc, out = vm.run("cidint config set live/setting 7 && cidint config get live/setting --json")
            step("a configuration value can be set during the session", rc == 0, out)
            ok, out = system_state(vm)
            step("no failed units in the live session", ok, out)
            rc1, id1 = vm.run("cat /proc/sys/kernel/random/boot_id")

            vm.send("sync; systemctl reboot")

            vm.login()
            rc2, id2 = vm.run("cat /proc/sys/kernel/random/boot_id")
            step("a real reboot happened (kernel boot id changed)", rc1 == 0 and rc2 == 0 and id1 != id2,
                 f"{id1} -> {id2}")
            rc, out = vm.run("test ! -e /home/root/session.txt")
            step("P15: the session file is gone after restart", rc == 0, out)
            rc, out = vm.run("cidint config get live/setting --json")
            step("P15: the configuration value is gone after restart", rc != 0, out)

            vm.send("systemctl poweroff")
            vm.wait_exit(180)
        except Exception as e:  # report, do not hide
            error = f"{type(e).__name__}: {e}"
            step("test run completed", False, error)
        finally:
            if vm and vm.proc.poll() is None:
                vm.kill()

    passed = all(r["passed"] for r in results)
    summary = {"image": str(args.image), "passed": passed, "results": results, "error": error}
    (args.workdir / "live-report.json").write_text(json.dumps(summary, indent=2))
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
