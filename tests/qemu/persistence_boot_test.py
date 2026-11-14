#!/usr/bin/env python3
"""Boot the CIDINT image in QEMU and run the persistence acceptance tests
that this release can exercise (docs/TESTING.md, tests P1, P4, P12-lite
and the power-loss check).

    python3 tests/qemu/persistence_boot_test.py IMAGE.wic --ovmf-code F --ovmf-vars F

The image is copied first; the build output is never modified.
Exit code 0 only if every step passed. A JSON report is written next
to the log with the real results.

Sequence:
  boot 1  check mode + volumes, arm the reboot marker, create a file in
          /home, set a config key, clean reboot
  boot 2  marker survived a real reboot, file and config still there;
          set another key, then the VM is killed without shutdown
  boot 3  the key committed before the power cut is present, the state
          store and audit chain verify
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

LOGIN_TIMEOUT = 900  # generous: hosted runners may lack KVM
CMD_TIMEOUT = 120


class Vm:
    def __init__(self, image: Path, code: Path, nvram: Path, log):
        accel = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.W_OK) else ["-cpu", "max"]
        cmd = [
            "qemu-system-x86_64", "-machine", "q35", "-m", "2048", "-smp", "2", *accel,
            "-drive", f"if=pflash,format=raw,readonly=on,file={code}",
            "-drive", f"if=pflash,format=raw,file={nvram}",
            "-drive", f"file={image},format=raw,if=none,id=disk0",
            "-device", "ide-hd,drive=disk0,bus=ide.0",
            "-display", "none", "-serial", "stdio", "-monitor", "none",
            "-nic", "none",
        ]
        self.log = log
        self.buf = ""
        self.lock = threading.Lock()
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while True:
            chunk = self.proc.stdout.read1(4096)
            if not chunk:
                return
            text = chunk.decode("utf-8", "replace")
            self.log.write(text)
            self.log.flush()
            with self.lock:
                self.buf += text

    def expect(self, pattern: str, timeout: float) -> re.Match:
        deadline = time.time() + timeout
        rx = re.compile(pattern)
        while time.time() < deadline:
            with self.lock:
                m = rx.search(self.buf)
                if m:
                    self.buf = self.buf[m.end():]
                    return m
            if self.proc.poll() is not None:
                raise RuntimeError(f"QEMU exited while waiting for {pattern!r}")
            time.sleep(0.2)
        raise TimeoutError(f"timed out after {timeout}s waiting for {pattern!r}")

    def send(self, line: str):
        self.proc.stdin.write((line + "\n").encode())
        self.proc.stdin.flush()

    def login(self):
        self.expect(r"login: ", LOGIN_TIMEOUT)
        self.send("root")
        time.sleep(2)
        self.send("stty -echo; export PS1=''")
        self.run("true")

    def run(self, command: str) -> tuple[int, str]:
        """Run a shell command in the guest; return (exit status, output)."""
        with self.lock:
            self.buf = ""
        self.send(f"echo __BEGIN__; {command}; echo __RC=$?__")
        m = self.expect(r"__BEGIN__\r?\n(.*?)__RC=(\d+)__", CMD_TIMEOUT)
        return int(m.group(2)), re.sub(r"\r", "", m.group(1)).strip()

    def kill(self):
        self.proc.kill()
        self.proc.wait()

    def wait_exit(self, timeout: float):
        self.proc.wait(timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--ovmf-code", type=Path, required=True)
    ap.add_argument("--ovmf-vars", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test"))
    args = ap.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    disk = args.workdir / "disk.wic"
    nvram = args.workdir / "OVMF_VARS.fd"
    shutil.copyfile(args.image, disk)
    shutil.copyfile(args.ovmf_vars, nvram)

    results = []

    def step(name: str, ok: bool, detail: str = ""):
        results.append({"test": name, "passed": bool(ok), "detail": detail[-2000:]})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""),
              flush=True)

    error = None
    with open(args.workdir / "serial.log", "w", encoding="utf-8") as log:
        vm = None
        try:
            # ---- boot 1 -------------------------------------------------
            vm = Vm(disk, args.ovmf_code, nvram, log)
            vm.login()
            step("boot: image reaches a login prompt under UEFI", True)

            rc, out = vm.run("cidint persistence status --json")
            report = json.loads(out)["data"] if out.startswith("{") else {}
            step("persistence status: mode 'installed', all volumes on a separate durable filesystem",
                 rc == 0 and report.get("mode") == "installed" and report.get("consistent") is True, out)

            rc, out = vm.run("touch /usr/cidint-write-probe")
            step("system image is read-only", rc != 0, out)

            rc, out = vm.run("cidint persistence arm")
            step("arm reboot marker", rc == 0, out)
            rc, out = vm.run("echo 'cidint acceptance file' > /home/root/kept.txt && sync")
            step("create a user file under /home", rc == 0, out)
            rc, out = vm.run("cidint config set acceptance/setting 42")
            step("change a configuration value", rc == 0, out)
            rc1, id1 = vm.run("cat /proc/sys/kernel/random/boot_id")

            vm.send("systemctl reboot")

            # ---- boot 2 (same QEMU process, guest rebooted) -------------
            vm.login()
            rc2, id2 = vm.run("cat /proc/sys/kernel/random/boot_id")
            step("a real reboot happened (kernel boot id changed)", rc1 == 0 and rc2 == 0 and id1 != id2,
                 f"{id1} -> {id2}")
            rc, out = vm.run("cidint persistence verify")
            step("P1: reboot marker survived restart", rc == 0 and "PASS" in out, out)
            rc, out = vm.run("cat /home/root/kept.txt")
            step("P1: user file survived restart", rc == 0 and out == "cidint acceptance file", out)
            rc, out = vm.run("cidint config get acceptance/setting --json")
            step("P4: configuration survived restart",
                 rc == 0 and json.loads(out)["data"]["value"] == 42, out)

            rc, out = vm.run("cidint config set acceptance/before-power-cut committed")
            step("commit a value, then cut power without shutdown", rc == 0, out)
            vm.kill()

            # ---- boot 3 (after simulated power loss) --------------------
            vm = Vm(disk, args.ovmf_code, nvram, log)
            vm.login()
            rc, out = vm.run("cidint config get acceptance/before-power-cut --json")
            step("power loss: value committed before the cut is present",
                 rc == 0 and json.loads(out)["data"]["value"] == "committed", out)
            rc, out = vm.run("cidint audit verify")
            step("power loss: audit chain verifies", rc == 0, out)
            rc, out = vm.run("cat /home/root/kept.txt")
            step("power loss: user file intact", rc == 0 and out == "cidint acceptance file", out)
            rc, out = vm.run("systemctl is-system-running")
            step("no failed units after recovery boot", out in ("running", "starting"), out)

            vm.send("systemctl poweroff")
            vm.wait_exit(180)
        except Exception as e:  # report, do not hide
            error = f"{type(e).__name__}: {e}"
            step("test run completed", False, error)
        finally:
            if vm and vm.proc.poll() is None:
                vm.kill()

    passed = all(r["passed"] for r in results)
    summary = {
        "image": str(args.image),
        "kvm": os.access("/dev/kvm", os.W_OK),
        "passed": passed,
        "results": results,
        "error": error,
    }
    (args.workdir / "persistence-report.json").write_text(json.dumps(summary, indent=2))
    print(f"\n{sum(r['passed'] for r in results)}/{len(results)} checks passed")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
