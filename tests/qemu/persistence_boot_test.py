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
# Throwaway root password for disposable test machines: given at the
# first-start prompt of disk images and to the installer.
TEST_ROOT_PASSWORD = "cidint-test-root-9"
SHELL_PROMPT = r"root@[\w.-]+:[^\r\n]*# "


def sparse_copy(src: Path, dst: Path, block: int = 1 << 20):
    """Copy a disk image without writing its empty (all-zero) blocks, so a
    mostly empty multi-GB image costs little space on the test machine."""
    zero = bytes(block)
    size = os.path.getsize(src)
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            chunk = fi.read(block)
            if not chunk:
                break
            if chunk == zero[:len(chunk)]:
                fo.seek(len(chunk), os.SEEK_CUR)
            else:
                fo.write(chunk)
        fo.truncate(size)


class Vm:
    def __init__(self, image: Path, code: Path | None, nvram: Path | None, log, cdrom: bool = False,
                 extra_disk: Path | None = None, passphrase: str | None = None,
                 root_password: str = TEST_ROOT_PASSWORD, nic: list[str] | None = None,
                 usb: bool = False, secure_boot: bool = False, extra: list[str] | None = None):
        """code/nvram None: legacy BIOS (SeaBIOS) instead of UEFI.
        usb: attach the image as a read-only USB stick instead.
        secure_boot: firmware built with Secure Boot (needs SMM).
        extra: further QEMU arguments (devices, audio, graphics)."""
        self.passphrase = passphrase
        self.root_password = root_password
        self.unlock_prompts = 0
        self.first_start_prompts = 0
        self.autologin = False
        self.password_asked = False
        accel = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.W_OK) else ["-cpu", "max"]
        firmware = [] if code is None else [
            "-drive", f"if=pflash,format=raw,readonly=on,file={code}",
            "-drive", f"if=pflash,format=raw,file={nvram}"]
        if usb:
            media = ["-device", "qemu-xhci,id=xhci",
                     "-drive", f"file={image},format=raw,if=none,id=stick,readonly=on",
                     "-device", "usb-storage,bus=xhci.0,drive=stick,removable=on"]
        elif cdrom:
            media = ["-cdrom", str(image)]
            if extra_disk is not None:
                media += ["-drive", f"file={extra_disk},format=raw,if=none,id=disk0",
                          "-device", "ide-hd,drive=disk0,bus=ide.0"]
        else:
            media = ["-drive", f"file={image},format=raw,if=none,id=disk0",
                     "-device", "ide-hd,drive=disk0,bus=ide.0"]
        cmd = [
            "qemu-system-x86_64", "-machine", "q35,smm=on" if secure_boot else "q35",
            "-m", "2048", "-smp", "2", *accel,
            *(["-global", "driver=cfi.pflash01,property=secure,value=on"] if secure_boot else []),
            *firmware,
            *media,
            *(extra or ["-display", "none"]), "-serial", "stdio", "-monitor", "none",
            *(nic or ["-nic", "none"]),
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
        """Get a root shell on the serial console: answer the disk
        passphrase and first-start password prompts, log in with the root
        password, or accept the automatic login of a live medium."""
        self.autologin = False
        self.password_asked = False
        while True:
            m = self.expect(r"(login: |Please enter passphrase for disk|Choose a password for [\w-]+[^:\r\n]*: "
                            r"|Repeat the password: |" + SHELL_PROMPT + ")", LOGIN_TIMEOUT)
            g = m.group(1)
            if g.startswith("root@"):
                # A shell without a login prompt: a live medium's automatic
                # login. Only acceptable there.
                self.autologin = True
                break
            if g.startswith("Choose") or g.startswith("Repeat"):
                if g.startswith("Choose"):
                    self.first_start_prompts += 1
                    if self.first_start_prompts > 4:
                        raise RuntimeError("the first-start password prompt repeated more than 4 times")
                time.sleep(1)
                self.send(self.root_password)
                continue
            if g.startswith("login"):
                time.sleep(1.5)
                with self.lock:
                    auto = "automatic login" in self.buf[:80]
                if auto:
                    self.expect(SHELL_PROMPT, 120)
                    self.autologin = True
                    break
                self._password_login()
                break
            # Encrypted data volume: systemd asks on the console before the
            # system can finish starting.
            if self.passphrase is None:
                raise RuntimeError("the system asked for a disk passphrase; none was expected")
            self.unlock_prompts += 1
            if self.unlock_prompts > 6:
                raise RuntimeError("the disk passphrase was asked for more than 6 times")
            time.sleep(2)
            self.send(self.passphrase)
        self.send("stty -echo; export PS1=''")
        last = None
        for _ in range(4):
            try:
                self.run("true", timeout=20)
                return
            except TimeoutError as e:
                last = e
        raise last

    def _password_login(self):
        self.send("root")
        m = self.expect(r"(Password: |" + SHELL_PROMPT + ")", 120)
        if not m.group(1).startswith("Password"):
            raise RuntimeError("root was logged in without a password on an installed system")
        self.password_asked = True
        self.send(self.root_password)
        m = self.expect(r"(" + SHELL_PROMPT + "|Login incorrect)", 120)
        if m.group(1).startswith("Login"):
            raise RuntimeError("the root password was not accepted")

    def run(self, command: str, timeout: float = CMD_TIMEOUT) -> tuple[int, str]:
        """Run a shell command in the guest; return (exit status, output)."""
        with self.lock:
            self.buf = ""
        self.send(f"echo __BEGIN__; {command}; echo __RC=$?__")
        m = self.expect(r"(?s)__BEGIN__\r?\n(.*?)__RC=(\d+)__", timeout)
        return int(m.group(2)), re.sub(r"\r", "", m.group(1)).strip()

    def kill(self):
        self.proc.kill()
        self.proc.wait()

    def wait_exit(self, timeout: float):
        self.proc.wait(timeout=timeout)


def system_state(vm) -> tuple[bool, str]:
    """True if no unit failed; otherwise name the failed units and show
    the end of their logs, so a CI failure explains itself."""
    rc, out = vm.run("systemctl is-system-running")
    if out in ("running", "starting"):
        return True, out
    rc, failed = vm.run("systemctl --failed --no-legend --plain | cut -d' ' -f1")
    detail = f"{out}; failed units: {failed}"
    for unit in failed.split()[:3]:
        rc, journal = vm.run(f"journalctl -b -u {unit} --no-pager -o cat | tail -n 12")
        detail += f"\n[{unit}]\n{journal}"
    return False, detail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--ovmf-code", type=Path, required=True)
    ap.add_argument("--ovmf-vars", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test"))
    ap.add_argument("--passphrase", help="data volume passphrase; also turns on the encryption checks")
    ap.add_argument("--root-password", default=TEST_ROOT_PASSWORD,
                    help="root password set at installation (or to give at the first-start prompt)")
    ap.add_argument("--expect-first-start", action="store_true",
                    help="a fresh disk image: the first start must ask for a root password")
    ap.add_argument("--legacy-bios", action="store_true", help="start with SeaBIOS instead of UEFI")
    args = ap.parse_args()
    vmopts = {"passphrase": args.passphrase, "root_password": args.root_password}

    args.workdir.mkdir(parents=True, exist_ok=True)
    disk = args.workdir / "disk.wic"
    nvram = args.workdir / "OVMF_VARS.fd"
    sparse_copy(args.image, disk)
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
            fw = (None, None) if args.legacy_bios else (args.ovmf_code, nvram)
            vm = Vm(disk, *fw, log, **vmopts)
            vm.login()
            step(f"boot: image reaches a login prompt under {'legacy BIOS' if args.legacy_bios else 'UEFI'}", True)
            rc, out = vm.run("test -d /sys/firmware/efi && echo uefi || echo bios")
            step("the system really started with the firmware under test",
                 out == ("bios" if args.legacy_bios else "uefi"), out)
            if args.passphrase is not None:
                step("encryption: the passphrase was asked for at boot", vm.unlock_prompts >= 1)
                rc, out = vm.run("cryptsetup status cidint-data")
                step("encryption: /dev/mapper/cidint-data is an active LUKS2 volume",
                     rc == 0 and "LUKS2" in out and "is active" in out, out)
                rc, out = vm.run("grep ' /data ' /proc/mounts")
                step("encryption: /data is mounted from the unlocked volume",
                     rc == 0 and out.startswith("/dev/mapper/cidint-data "), out)
            if args.expect_first_start:
                step("first start: root has no default password; the console asked for one",
                     vm.first_start_prompts == 1, f"prompts={vm.first_start_prompts}")
            rc, out = vm.run("awk -F: '$1 == \"root\" { print substr($2, 1, 3) }' /etc/shadow")
            step("root password: stored as a SHA-512 hash, and login required it",
                 rc == 0 and out == "$6$" and vm.password_asked and not vm.autologin, out)

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
            step("the root password survived the restart (no second first-start prompt)",
                 vm.password_asked and vm.first_start_prompts <= 1, f"prompts={vm.first_start_prompts}")
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
            vm = Vm(disk, *fw, log, **vmopts)
            vm.login()
            rc, out = vm.run("cidint config get acceptance/before-power-cut --json")
            step("power loss: value committed before the cut is present",
                 rc == 0 and json.loads(out)["data"]["value"] == "committed", out)
            rc, out = vm.run("cidint audit verify")
            step("power loss: audit chain verifies", rc == 0, out)
            rc, out = vm.run("cat /home/root/kept.txt")
            step("power loss: user file intact", rc == 0 and out == "cidint acceptance file", out)
            ok, out = system_state(vm)
            step("no failed units after recovery boot", ok, out)
            rc, out = vm.run("cidint-hwcheck >/dev/null; f=$(ls -t /data/hwcheck/*.txt | head -n 1); "
                             "grep -c '^== ' $f; grep '^boot mode' $f; grep '^PCI devices without' $f", timeout=120)
            step("hardware report: written to /data/hwcheck with all sections", rc == 0 and out.startswith("9"), out)

            vm.send("systemctl poweroff")
            vm.wait_exit(180)
        except Exception as e:  # report, do not hide
            error = f"{type(e).__name__}: {e}"
            step("test run completed", False, error)
        finally:
            if vm and vm.proc.poll() is None:
                vm.kill()

    # Look at the raw disk from outside the guest: the text written to
    # /home (on the data volume) is readable on an unencrypted disk and
    # must not be on an encrypted one.
    if error is None:
        needle = b"cidint acceptance file"
        found, tail = False, b""
        with open(disk, "rb") as f:
            while True:
                block = f.read(8 * 1024 * 1024)
                if not block:
                    break
                if needle in tail + block:
                    found = True
                    break
                tail = block[-len(needle):]
        if args.passphrase is not None:
            step("encryption: the user file's text is not readable on the raw disk", not found)
        else:
            step("control: without encryption the user file's text is readable on the raw disk", found)

    passed = all(r["passed"] for r in results)
    summary = {
        "image": str(args.image),
        "kvm": os.access("/dev/kvm", os.W_OK),
        "passed": passed,
        "results": results,
        "error": error,
    }
    (args.workdir / "persistence-report.json").write_text(json.dumps(summary, indent=2))
    if not passed:
        # Show the end of the serial console so the cause is visible in CI.
        raw = (args.workdir / "serial.log").read_text(encoding="utf-8", errors="replace")
        clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\r", "", raw)
        lines = [ln for ln in clean.splitlines() if ln.strip()]
        print("--- serial console, last 34 lines ---")
        for ln in lines[-34:]:
            print(ln[:300])
    print(f"\n{sum(r['passed'] for r in results)}/{len(results)} checks passed")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
