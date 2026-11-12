"""Platform-independent tests for CIDINT OS core logic.

    python -m unittest discover -s tests -v
"""
import importlib.util
import io
import struct
import sys
import tarfile
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHROOT = ROOT / "live" / "config" / "includes.chroot"
sys.path.insert(0, str(CHROOT / "usr" / "lib" / "cidint"))

from cidint import assistant, hwadapt, sentinel, session, uaee  # noqa: E402

spec = importlib.util.spec_from_file_location("patch_bootloaders", ROOT / "tools" / "patch-bootloaders.py")
patch_bootloaders = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch_bootloaders)


def elf(machine: int, appimage: bool = False) -> bytes:
    ident = b"\x7fELF\x02\x01\x01\x00" + (b"AI\x02" if appimage else b"\0\0\0") + b"\0" * 5
    return ident + struct.pack("<HH", 2, machine) + b"\0" * 64


def pe(machine: int) -> bytes:
    head = bytearray(b"MZ" + b"\0" * 62)
    head[0x3C:0x40] = struct.pack("<I", 0x80)
    return bytes(head) + b"\0" * 0x40 + b"PE\0\0" + struct.pack("<H", machine) + b"\0" * 64


def zipped(names) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in names:
            zf.writestr(n, "x")
    return buf.getvalue()


class Detect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make(self, name: str, data: bytes) -> str:
        p = Path(self.tmp.name) / name
        p.write_bytes(data)
        return str(p)

    def kind(self, name, data):
        return uaee.detect(self.make(name, data))

    def test_formats(self):
        cases = [
            ("a.bin", elf(62), "elf", "x86_64"),
            ("a.arm", elf(183), "elf", "aarch64"),
            ("a.rv", elf(243), "elf", "riscv64"),
            ("App.AppImage", elf(62, appimage=True), "appimage", "x86_64"),
            ("setup.exe", pe(0x8664), "pe", "x86_64"),
            ("old.exe", pe(0x14C), "pe", "x86"),
            ("arm.exe", pe(0xAA64), "pe", "aarch64"),
            ("x.msi", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600, "msi", ""),
            ("x.deb", b"!<arch>\ndebian-binary   " + b"\0" * 100, "deb", ""),
            ("x.rpm", b"\xed\xab\xee\xdb" + b"\0" * 100, "rpm", ""),
            ("x.apk", zipped(["AndroidManifest.xml", "classes.dex"]), "apk", ""),
            ("x.aab", zipped(["BundleConfig.pb"]), "aab", ""),
            ("x.jar", zipped(["META-INF/MANIFEST.MF"]), "jar", ""),
            ("x.ipa", zipped(["Payload/Foo.app/Foo"]), "ios", ""),
            ("x.zip", zipped(["readme.txt"]), "archive", ""),
            ("x.tar.gz", b"\x1f\x8b\x08" + b"\0" * 50, "archive", ""),
            ("x.tar.xz", b"\xfd7zXZ\0" + b"\0" * 50, "archive", ""),
            ("x.dmg", b"\0" * 1024 + b"koly" + b"\0" * 508, "macos", ""),
            ("macho", b"\xcf\xfa\xed\xfe" + b"\0" * 100, "macos", ""),
            ("x.pkg", b"xar!" + b"\0" * 100, "macos", ""),
            ("run.bat", b"@echo off\r\n", "winbatch", ""),
            ("run.ps1", b"Write-Host hi\n", "powershell", ""),
            ("x.sh", b"#!/bin/sh\necho hi\n", "script", ""),
            ("noext", b"#!/usr/bin/env python3\nprint(1)\n", "script", ""),
            ("plain.sh", b"echo hi\n", "script", ""),
            ("mystery.dat", b"\x01\x02\x03\x04" * 20, "unknown", ""),
        ]
        for name, data, kind, arch in cases:
            with self.subTest(name=name):
                info = self.kind(name, data)
                self.assertEqual(info.kind, kind)
                if arch:
                    self.assertEqual(info.arch, arch)

    def test_extension_is_not_trusted(self):
        # a Windows executable renamed to .sh is still identified as a Windows executable
        self.assertEqual(self.kind("invoice.sh", pe(0x8664)).kind, "pe")
        self.assertEqual(self.kind("photo.jpg", elf(62)).kind, "elf")

    def test_shebang_interpreter(self):
        self.assertEqual(self.kind("s", b"#!/usr/bin/env python3\n").detail, "/usr/bin/env python3")

    def test_sha256(self):
        p = self.make("h", b"abc")
        self.assertEqual(uaee.sha256(p), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_build_hint(self):
        src = Path(self.tmp.name) / "src" / "proj-1.0"
        src.mkdir(parents=True)
        (src / "CMakeLists.txt").write_text("")
        self.assertIn("cmake", uaee.build_hint(src.parent))

    def test_tar_extraction_filter_blocks_traversal(self):
        evil = Path(self.tmp.name) / "evil.tar"
        with tarfile.open(evil, "w") as tf:
            ti = tarfile.TarInfo("../escape.txt")
            ti.size = 1
            tf.addfile(ti, io.BytesIO(b"x"))
        with tarfile.open(evil) as tf, self.assertRaises(tarfile.FilterError):
            tf.extractall(Path(self.tmp.name) / "out", filter="data")


class Planning(unittest.TestCase):
    def plan(self, kind, arch="", host="x86_64", installed=()):
        return uaee.plan(uaee.FileInfo("f", kind, arch), host=host, which=lambda b: b in installed)

    def test_native(self):
        p = self.plan("elf", "x86_64")
        self.assertTrue(p.supported)
        self.assertEqual(p.missing, [])

    def test_foreign_arch_uses_qemu(self):
        p = self.plan("elf", "aarch64", installed=("qemu-aarch64",))
        self.assertIn("QEMU", p.runtime)
        self.assertEqual(p.missing, [])
        self.assertEqual(self.plan("elf", "riscv64").missing, ["qemu-riscv64"])

    def test_windows_needs_wine(self):
        self.assertEqual(self.plan("pe", "x86_64").missing, ["wine"])
        self.assertTrue(self.plan("msi", installed=("wine",)).supported)

    def test_windows_on_arm(self):
        self.assertFalse(self.plan("pe", "aarch64", host="x86_64", installed=("wine",)).supported)
        self.assertIn("box64", self.plan("pe", "x86_64", host="aarch64", installed=("wine",)).missing)

    def test_apple_software_refused_with_reason(self):
        for kind in ("macos", "ios"):
            p = self.plan(kind)
            self.assertFalse(p.supported)
            self.assertTrue(p.notes)

    def test_unknown_refused(self):
        self.assertFalse(self.plan("unknown").supported)

    def test_sandbox_command(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "tool.exe"
            exe.write_bytes(pe(0x8664))
            argv = uaee.sandbox_argv(uaee.detect(exe), None, net=False)
        self.assertEqual(argv[0], "bwrap")
        self.assertIn("--unshare-net", argv)
        self.assertIn("--unshare-pid", argv)
        self.assertIn("--clearenv", argv)
        self.assertEqual(argv[argv.index("--") + 1:], ["wine", "/run/app/tool.exe"])
        self.assertEqual(argv[argv.index("--tmpfs", argv.index("/var")) + 1], "/home/user")


class Hardware(unittest.TestCase):
    def test_holt_tracks_trend(self):
        h = hwadapt.Holt()
        for i in range(40):
            h.update(0.02 * i)
        self.assertGreater(h.forecast(3), 0.02 * 39)      # rising series: forecast is ahead
        flat = hwadapt.Holt()
        for _ in range(40):
            flat.update(0.5)
        self.assertAlmostEqual(flat.forecast(3), 0.5, places=3)

    def test_policy(self):
        d = lambda **k: hwadapt.decide(**{"load_forecast": 0.1, "cpu_pressure": 0, "temp_c": 50,
                                          "on_ac": True, "battery_pct": None, **k})[0]
        self.assertEqual(d(), "balanced")
        self.assertEqual(d(load_forecast=0.9), "performance")
        self.assertEqual(d(cpu_pressure=40), "performance")
        self.assertEqual(d(load_forecast=0.9, temp_c=82), "balanced")
        self.assertEqual(d(load_forecast=0.9, temp_c=95), "power-saver")
        self.assertEqual(d(load_forecast=0.9, on_ac=False, battery_pct=80), "balanced")
        self.assertEqual(d(on_ac=False, battery_pct=10), "power-saver")

    def test_hysteresis_prevents_flapping(self):
        h = hwadapt.Hysteresis("balanced", hold=3)
        self.assertEqual(h.propose("performance"), "balanced")
        self.assertEqual(h.propose("balanced"), "balanced")
        self.assertEqual(h.propose("performance"), "balanced")
        self.assertEqual(h.propose("performance"), "balanced")
        self.assertEqual(h.propose("performance"), "performance")


class Sentinel(unittest.TestCase):
    BENIGN = dict(exe="/usr/bin/firefox-esr", comm="firefox-esr", uid=1000, parent_uid=1000,
                  preload=False, write_rate=0.0, mass_write_ticks=0, anomalies=[])

    def score(self, **k):
        return sentinel.score(**{**self.BENIGN, **k})[0]

    def test_benign(self):
        self.assertEqual(self.score(), 0)
        self.assertLess(self.score(anomalies=["cpu", "memory", "threads", "open files"]), sentinel.WARN)

    def test_dropper(self):
        self.assertGreaterEqual(self.score(exe="/tmp/.x/payload (deleted)"), sentinel.WARN)

    def test_privilege_escalation(self):
        self.assertGreaterEqual(self.score(uid=0, parent_uid=1000, comm="payload", exe="/tmp/payload"),
                                sentinel.WARN)
        self.assertEqual(self.score(uid=0, parent_uid=1000, comm="sudo", exe="/usr/bin/sudo"), 0)

    def test_ransomware_pattern(self):
        s = self.score(exe="/dev/shm/enc", comm="enc", write_rate=2e8, mass_write_ticks=4,
                       anomalies=["write rate", "cpu"])
        self.assertGreaterEqual(s, sentinel.WARN)
        self.assertEqual(self.score(comm="rsync", exe="/usr/bin/rsync", write_rate=2e8, mass_write_ticks=9), 0)

    def test_critical_and_capped(self):
        s = self.score(exe="/tmp/x (deleted)", uid=0, parent_uid=1000, comm="x", preload=True)
        self.assertGreaterEqual(s, sentinel.CRITICAL)
        self.assertLessEqual(s, 100)

    def test_ewma_flags_outlier_only_after_warmup(self):
        m = sentinel.Ewma()
        self.assertEqual(m.z(1e9), 0.0)
        for i in range(60):
            m.update(100 + (i % 5))
        self.assertLess(abs(m.z(103)), sentinel.Z_LIMIT)
        self.assertGreater(m.z(1000), sentinel.Z_LIMIT)


class Assistant(unittest.TestCase):
    def cmd(self, text):
        hit = assistant.interpret(text)
        return hit[1][1:] if hit else None

    def test_intents(self):
        self.assertEqual(self.cmd("please turn on anonymous mode"), ["anon", "on"])
        self.assertEqual(self.cmd("start tor"), ["anon", "on"])
        self.assertEqual(self.cmd("disable anonymity"), ["anon", "off"])
        self.assertEqual(self.cmd("why is my system slow"), ["hw", "status"])
        self.assertEqual(self.cmd("show me threats"), ["sentinel", "events"])
        self.assertEqual(self.cmd("PANIC!"), ["panic"])
        self.assertEqual(self.cmd("list installed apps"), ["app", "list"])
        self.assertEqual(self.cmd("open a throwaway browser")[:2], ["domain", "disposable"])
        self.assertIsNone(self.cmd("what is the meaning of life"))
        self.assertIsNone(self.cmd(""))

    def test_destructive_intents_are_marked(self):
        self.assertTrue(assistant.interpret("turn on anonymous mode")[2])
        self.assertFalse(assistant.interpret("show threats")[2])


class BootMenus(unittest.TestCase):
    GRUB = "\n".join(['set theme=x', 'menuentry "Live system (amd64)" --hotkey=l {',
                      '    linux /live/vmlinuz-6.12 boot=live components quiet',
                      '    initrd /live/initrd.img-6.12', '}', 'menuentry "Other" {', '    chainloader x', '}', ''])
    SYSLINUX = "\n".join(['label live-amd64', '    menu label ^Live system (amd64)', '    menu default',
                          '    linux /live/vmlinuz', '    initrd /live/initrd.img',
                          '    append boot=live components quiet', '', 'label other', '    kernel x', ''])

    def test_grub(self):
        out = patch_bootloaders.patch_grub(self.GRUB)
        self.assertEqual(out.count("menuentry"), 3)
        self.assertEqual(out.count("persistence-encryption=luks"), 1)
        self.assertIn("boot=live components quiet persistence persistence-encryption=luks", out)
        self.assertEqual(out.count("--hotkey=l"), 1)
        self.assertLess(out.index("set timeout=5"), out.index("menuentry"))
        self.assertEqual(patch_bootloaders.patch_grub(out), out)      # idempotent

    def test_syslinux(self):
        out = patch_bootloaders.patch_syslinux(self.SYSLINUX)
        self.assertIn("label live-amd64-persist", out)
        self.assertEqual(out.count("menu default"), 1)
        self.assertEqual(out.count("persistence-encryption=luks"), 1)
        self.assertEqual(patch_bootloaders.patch_syslinux(out), out)

    def test_rebrand(self):
        out = patch_bootloaders.rebrand('text = "Debian GNU/Linux 13 (trixie) amd64"' + chr(10) + 'Live system (amd64)')
        self.assertEqual(out, 'text = "CIDINT OS 0.1"' + chr(10) + 'CIDINT OS (amnesic) (amd64)')

    def test_splash_is_valid_png_of_requested_size(self):
        import struct
        import zlib
        png = patch_bootloaders.make_splash(80, 60)
        self.assertEqual(png[:8], bytes([137, 80, 78, 71, 13, 10, 26, 10]))
        self.assertEqual(struct.unpack(">II", png[16:24]), (80, 60))
        length = struct.unpack(">I", png[33:37])[0]
        self.assertEqual(png[37:41], b"IDAT")
        self.assertEqual(len(zlib.decompress(png[41:41 + length])), 60 * (1 + 80 * 3))

    def test_splash_replacement_keeps_dimensions(self):
        import struct
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "boot" / "grub"
            target.mkdir(parents=True)
            (target / "splash.png").write_bytes(patch_bootloaders.make_splash(96, 64))
            (target / "notes.png").write_bytes(b"not a png")
            self.assertEqual(patch_bootloaders.replace_splashes(Path(d)), 1)
            self.assertEqual(struct.unpack(">II", (target / "splash.png").read_bytes()[16:24]), (96, 64))

    def test_unknown_template_untouched(self):
        self.assertEqual(patch_bootloaders.patch_grub("set x=5"), "set x=5")


class Session(unittest.TestCase):
    def test_exec_binary(self):
        self.assertEqual(session.exec_binary("/usr/bin/firefox-esr %u"), "firefox-esr")
        self.assertEqual(session.exec_binary("env GDK_BACKEND=x11 /opt/app/run --flag"), "run")
        self.assertEqual(session.exec_binary('"/usr/bin/kate" -b %U'), "kate")
        self.assertEqual(session.exec_binary(""), "")

    def test_parse_desktop(self):
        ok = "[Desktop Entry]" + chr(10) + "Type=Application" + chr(10) + "Name=Kate" + chr(10) + "Exec=kate -b %U"
        self.assertEqual(session.parse_desktop(ok), {"name": "Kate", "binary": "kate"})
        self.assertIsNone(session.parse_desktop(ok + chr(10) + "NoDisplay=true"))
        self.assertIsNone(session.parse_desktop(ok.replace("Exec=kate", "Exec=python3 /x.py")))
        self.assertIsNone(session.parse_desktop(ok.replace("Application", "Link")))
        # keys in other sections (desktop actions) must not override the main entry
        extra = ok + chr(10) + "[Desktop Action new]" + chr(10) + "Exec=other" + chr(10) + "Name=Other"
        self.assertEqual(session.parse_desktop(extra)["binary"], "kate")

    def test_save_restore_roundtrip(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = d
            try:
                self.assertEqual(session.describe(), "No saved session.")
                self.assertEqual(session.resume_if_pending(launch=None) if False else [], [])
                apps = [{"name": "Kate", "argv": ["/usr/bin/kate", "notes.txt"], "cwd": ""},
                        {"name": "Firefox", "argv": ["/usr/bin/firefox-esr"], "cwd": "/nonexistent"}]
                session.save(apps)
                self.assertTrue(session.load()["resume"])
                self.assertIn("will resume", session.describe())
                launched = []
                started = session.restore(launch=lambda argv, cwd: launched.append((argv, cwd)),
                                          running={("/usr/bin/firefox-esr",)})
                self.assertEqual(started, ["Kate"])                 # Firefox already open: not duplicated
                self.assertEqual(launched, [(["/usr/bin/kate", "notes.txt"], None)])
                self.assertFalse(session.load()["resume"])          # one-shot: no relaunch loop
                session.clear()
                self.assertEqual(session.load(), {})
            finally:
                if old is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old

    def test_assistant_knows_session(self):
        self.assertEqual(assistant.interpret("continue my tasks after reboot")[1][1:], ["session", "save"])
        self.assertEqual(assistant.interpret("resume my work")[1][1:], ["session", "restore"])


class ShippedFiles(unittest.TestCase):
    def test_domains_file_parses(self):
        with open(CHROOT / "etc" / "cidint" / "domains.toml", "rb") as fh:
            doms = tomllib.load(fh)["domain"]
        self.assertIn("vault", doms)
        for d in doms.values():
            self.assertIn(d["network"], ("none", "direct", "tor"))

    def test_package_lists_have_no_duplicates(self):
        core = [l.strip() for l in (ROOT / "live/config/package-lists/cidint-core.list.chroot").read_text().splitlines()
                if l.strip() and not l.startswith("#")]
        opt = [l.strip() for l in (CHROOT / "usr/share/cidint/optional-packages.list").read_text().splitlines()
               if l.strip() and not l.startswith("#")]
        self.assertEqual(len(core), len(set(core)))
        self.assertEqual(len(opt), len(set(opt)))
        self.assertFalse(set(core) & set(opt))

    def test_all_python_compiles(self):
        files = list((CHROOT / "usr/lib/cidint/cidint").glob("*.py")) + [CHROOT / "usr/bin/cidint"]
        self.assertGreater(len(files), 8)
        for f in files:
            compile(f.read_text(encoding="utf-8"), str(f), "exec")


if __name__ == "__main__":
    unittest.main()
