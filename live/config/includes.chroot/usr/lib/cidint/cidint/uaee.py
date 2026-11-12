"""Universal Application Execution Engine (UAEE).

Pipeline for any file handed to CIDINT OS:
    identify (magic bytes, not the extension) -> hash -> malware scan ->
    architecture check -> choose runtime -> isolate -> install/run -> register

Compatibility is delegated to the real runtimes (apt, alien, Wine, Flatpak,
QEMU user-mode, Waydroid). Formats that cannot legally or technically run on
Linux (macOS and iOS software) are identified and refused with an explanation
instead of failing obscurely.
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import struct
import subprocess
import tarfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import CLI, have, read_json, sh, user_data, write_json_atomic
from .sandbox import SANDBOX_HOME, bwrap_cmd

ELF_MACHINES = {3: "x86", 62: "x86_64", 40: "arm", 183: "aarch64", 243: "riscv64",
                20: "ppc", 21: "ppc64", 22: "s390x", 8: "mips", 258: "loongarch64"}
PE_MACHINES = {0x14C: "x86", 0x8664: "x86_64", 0xAA64: "aarch64", 0x1C0: "arm", 0x1C4: "arm"}
QEMU_USER = {"x86": "qemu-i386", "x86_64": "qemu-x86_64", "arm": "qemu-arm",
             "aarch64": "qemu-aarch64", "riscv64": "qemu-riscv64", "ppc64": "qemu-ppc64le",
             "s390x": "qemu-s390x", "mips": "qemu-mips", "loongarch64": "qemu-loongarch64"}
MACHO_MAGICS = {b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"}


@dataclass
class FileInfo:
    path: str
    kind: str            # deb rpm appimage elf pe msi winbatch powershell script apk aab
    #                      flatpak snap jar archive macos ios unknown
    arch: str = ""
    detail: str = ""


@dataclass
class Plan:
    info: FileInfo
    supported: bool
    runtime: str
    isolation: str
    missing: list = field(default_factory=list)   # binaries that must be installed first
    notes: list = field(default_factory=list)


def host_arch() -> str:
    m = platform.machine().lower()
    return {"amd64": "x86_64", "arm64": "aarch64", "i386": "x86", "i686": "x86",
            "armv7l": "arm", "ppc64le": "ppc64"}.get(m, m)


def detect(path) -> FileInfo:
    path = str(path)
    ext = os.path.splitext(path.rstrip("/\\"))[1].lower()
    if os.path.isdir(path):
        if ext == ".app":
            return FileInfo(path, "macos", detail="macOS application bundle")
        return FileInfo(path, "unknown", detail="directory")
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(4096)
        tail = b""
        if size >= 512:
            fh.seek(size - 512)
            tail = fh.read(4)
    info = FileInfo(path, "unknown")

    if head[:4] == b"\x7fELF" and len(head) >= 20:
        endian = "<" if head[5] == 1 else ">"
        machine = struct.unpack(endian + "H", head[18:20])[0]
        info.arch = ELF_MACHINES.get(machine, f"elf-machine-{machine}")
        if head[8:10] == b"AI" and head[10] in (1, 2):
            info.kind, info.detail = "appimage", f"AppImage type {head[10]}"
        else:
            info.kind = "elf"
            info.detail = "64-bit" if head[4] == 2 else "32-bit"
    elif head[:2] == b"MZ" and len(head) >= 0x40:
        info.kind, info.detail = "pe", "Windows executable"
        off = struct.unpack("<I", head[0x3C:0x40])[0]
        if off + 6 <= len(head) and head[off:off + 4] == b"PE\0\0":
            machine = struct.unpack("<H", head[off + 4:off + 6])[0]
            info.arch = PE_MACHINES.get(machine, f"pe-machine-{machine:#x}")
        else:
            info.arch, info.detail = "x86", "DOS / 16-bit executable"
    elif head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        info.kind = "msi" if ext in (".msi", ".msp") else "unknown"
        info.detail = "Windows Installer database" if info.kind == "msi" else "OLE compound document"
    elif head[:21] == b"!<arch>\ndebian-binary":
        info.kind, info.detail = "deb", "Debian package"
    elif head[:4] == b"\xed\xab\xee\xdb":
        info.kind, info.detail = "rpm", "RPM package"
    elif head[:4] in MACHO_MAGICS or tail == b"koly" or head[:4] == b"xar!" \
            or (head[:4] == b"\xca\xfe\xba\xbe" and ext not in (".class",)) \
            or ext in (".dmg", ".app"):
        info.kind, info.detail = "macos", "macOS software"
    elif head[:4] == b"PK\x03\x04":
        info.kind, info.detail = _classify_zip(path, ext)
    elif head[:4] == b"hsqs" and ext == ".snap":
        info.kind, info.detail = "snap", "Snap package"
    elif ext in (".flatpak", ".flatpakref"):
        info.kind, info.detail = "flatpak", "Flatpak bundle/reference"
    elif head[:2] == b"\x1f\x8b" or head[:6] == b"\xfd7zXZ\0" or head[:3] == b"BZh" \
            or head[:4] == b"\x28\xb5\x2f\xfd" or head[257:262] == b"ustar":
        info.kind, info.detail = "archive", "compressed archive / source tarball"
    elif ext in (".bat", ".cmd"):
        info.kind, info.detail = "winbatch", "Windows batch script"
    elif ext == ".ps1":
        info.kind, info.detail = "powershell", "PowerShell script"
    elif head[:2] == b"#!":
        line = head[2:200].split(b"\n")[0].decode(errors="replace").strip()
        info.kind, info.detail = "script", line
    elif ext in (".sh", ".bash", ".run"):
        info.kind, info.detail = "script", "/bin/bash"
    elif ext == ".py":
        info.kind, info.detail = "script", "/usr/bin/python3"
    return info


def _classify_zip(path: str, ext: str) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist()[:5000])
    except (zipfile.BadZipFile, OSError):
        names = set()
    if ext == ".ipa" or any(n.startswith("Payload/") and ".app/" in n for n in names):
        return "ios", "iOS application"
    if "AndroidManifest.xml" in names:
        return "apk", "Android package"
    if "BundleConfig.pb" in names:
        return "aab", "Android App Bundle"
    if "META-INF/MANIFEST.MF" in names or ext == ".jar":
        return "jar", "Java archive"
    return "archive", "zip archive"


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(path) -> tuple[str, str]:
    """Return (verdict, detail); verdict is clean | infected | unscanned."""
    if not have("clamscan"):
        return "unscanned", "ClamAV is not installed"
    r = sh(["clamscan", "--no-summary", str(path)])
    if r.returncode == 0:
        return "clean", "no known malware signature matched"
    if r.returncode == 1:
        return "infected", r.stdout.strip().split(": ", 1)[-1]
    return "unscanned", "ClamAV has no signature database (run: sudo freshclam)"


def plan(info: FileInfo, host: str | None = None, which=have) -> Plan:
    """Decide how a file would be executed. Pure function: touches nothing."""
    host = host or host_arch()
    k = info.kind
    p = Plan(info, True, "", "sandbox (private home, no network unless granted)")

    def need(*bins):
        p.missing += [b for b in bins if not which(b)]

    if k == "deb":
        p.runtime, p.isolation = "apt (native package manager)", "system-wide install, rollback point recorded"
        p.notes.append("Third-party .deb files run their install scripts as root. Install only what you trust.")
        need("apt-get", "dpkg-deb")
    elif k == "rpm":
        p.runtime, p.isolation = "alien (RPM converted to DEB, then apt)", "system-wide install, rollback point recorded"
        p.notes.append("Converted packages may have unmet dependencies; a Fedora container (distrobox) is the fallback.")
        need("alien", "fakeroot", "apt-get")
    elif k in ("appimage", "elf"):
        p.runtime = "native Linux"
        if info.arch != host:
            if info.arch == "x86" and host == "x86_64":
                p.notes.append("32-bit binary: needs i386 libraries (multiarch).")
            elif info.arch in QEMU_USER:
                p.runtime = f"QEMU user-mode emulation ({info.arch} on {host})"
                need(QEMU_USER[info.arch])
                p.notes.append("Emulated: expect a large slowdown; foreign-arch libraries are also required.")
            else:
                p.supported = False
                p.notes.append(f"No emulator available for architecture {info.arch}.")
    elif k in ("pe", "msi", "winbatch"):
        p.runtime = {"pe": "Wine", "msi": "Wine (msiexec)", "winbatch": "Wine (cmd.exe)"}[k]
        p.isolation = "sandbox with a dedicated Wine prefix per application"
        need("wine")
        if k == "pe" and info.arch == "aarch64" and host != "aarch64":
            p.supported = False
            p.notes.append("Windows-on-ARM binaries need an ARM64 host.")
        elif k == "pe" and info.arch in ("x86", "x86_64") and host == "aarch64":
            need("box64")
            p.notes.append("x86 Windows software on ARM needs box64/FEX in addition to Wine.")
        p.notes.append("Wine runs most but not all Windows software; drivers and kernel anti-cheat never work. "
                       "Use a Windows VM (cidint domain vm) for those.")
    elif k == "powershell":
        p.runtime = "PowerShell (pwsh)"
        need("pwsh")
    elif k == "script":
        interp = (info.detail.split() or ["/bin/bash"])[0]
        p.runtime = f"native interpreter ({interp})"
    elif k == "jar":
        p.runtime = "Java runtime"
        need("java")
    elif k == "flatpak":
        p.runtime, p.isolation = "Flatpak", "Flatpak sandbox"
        need("flatpak")
    elif k == "snap":
        p.runtime, p.isolation = "snapd", "snap confinement"
        need("snap")
    elif k in ("apk", "aab"):
        p.runtime, p.isolation = "Waydroid (Android container)", "Android container"
        need("waydroid")
        if k == "aab":
            p.supported = False
            p.notes.append("An .aab is a publishing format, not installable; build an .apk with bundletool first.")
        p.notes.append("Waydroid needs a Wayland session and a one-time 'waydroid init' (downloads an Android image).")
    elif k == "archive":
        p.runtime = "extract, detect build system, build in sandbox"
    elif k == "macos":
        p.supported = False
        p.runtime = "none"
        p.notes.append("macOS software cannot run here: there is no usable Mach-O/Cocoa compatibility layer, "
                       "and Apple's licence only permits macOS on Apple hardware.")
    elif k == "ios":
        p.supported = False
        p.runtime = "none"
        p.notes.append("iOS apps are encrypted to Apple devices and cannot run on any other operating system.")
    else:
        p.supported = False
        p.runtime = "none"
        p.notes.append("Unrecognised file format.")
    if p.missing:
        p.notes.append("Missing runtime: " + ", ".join(p.missing))
    return p


# ---------------------------------------------------------------- registry ---

def _registry_path() -> Path:
    return user_data() / "apps.json"


def registry() -> dict:
    return read_json(_registry_path(), {})


def _save(reg: dict) -> None:
    write_json_atomic(_registry_path(), reg, 0o600)


def _app_dir(app_id: str) -> Path:
    d = user_data() / "apps" / app_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _new_id(path: str, digest: str) -> str:
    stem = "".join(c if c.isalnum() else "-" for c in Path(path).stem.lower()).strip("-")[:24]
    return f"{stem or 'app'}-{digest[:8]}"


def _launcher(app_id: str, name: str, extra: str = "") -> None:
    d = Path.home() / ".local" / "share" / "applications"
    d.mkdir(parents=True, exist_ok=True)
    suffix = ("-" + hashlib.sha1(extra.encode()).hexdigest()[:6]) if extra else ""
    exe_arg = f' --exe "{extra}"' if extra else ""
    (d / f"cidint-app-{app_id}{suffix}.desktop").write_text(
        "[Desktop Entry]\nType=Application\n"
        f"Name={name}\nComment=Installed by CIDINT UAEE (sandboxed)\n"
        f"Exec={CLI} app run {app_id}{exe_arg}\nIcon=application-x-executable\n"
        "Categories=CIDINT;Utility;\nTerminal=false\n")


def _dpkg_set() -> set:
    return set(sh(["dpkg-query", "-W", "-f", "${Package}\n"]).stdout.split())


def _root(cmd: list[str]) -> int:
    if os.geteuid() == 0:
        return subprocess.call(cmd)
    if sh(["sudo", "-n", "true"]).returncode == 0:
        return subprocess.call(["sudo", *cmd])
    return subprocess.call(["pkexec", *cmd])


# ----------------------------------------------------------------- actions ---

def sandbox_argv(info: FileInfo, home: Path | None, net: bool, exe: str = "") -> list[str]:
    """Build the sandboxed command line for a runnable file."""
    src = os.path.abspath(info.path) if info.path else ""
    inside = "/run/app/" + os.path.basename(src)
    binds = () if exe else ((src, inside),)   # an installed program needs no installer file
    k, env = info.kind, {}
    if k in ("pe", "msi", "winbatch"):
        env = {"WINEPREFIX": SANDBOX_HOME + "/.wine", "WINEDEBUG": "-all"}
        if exe:
            cmd = ["wine", SANDBOX_HOME + "/.wine/drive_c/" + exe]
        elif k == "msi":
            cmd = ["wine", "msiexec", "/i", inside]
        elif k == "winbatch":
            cmd = ["wine", "cmd", "/c", inside]
        else:
            cmd = ["wine", inside]
    elif k == "appimage":
        env = {"APPIMAGE_EXTRACT_AND_RUN": "1"}
        cmd = [inside]
    elif k == "elf":
        cmd = [inside]
        if info.arch != host_arch() and info.arch in QEMU_USER and not (
                info.arch == "x86" and host_arch() == "x86_64"):
            cmd = [QEMU_USER[info.arch], inside]
    elif k == "script":
        interp = info.detail.split() or ["/bin/bash"]
        cmd = [*interp, inside] if os.path.isabs(interp[0]) else ["/bin/bash", inside]
    elif k == "powershell":
        cmd = ["pwsh", "-NoLogo", "-File", inside]
    elif k == "jar":
        cmd = ["java", "-jar", inside]
    else:
        raise ValueError(f"'{k}' files are installed, not run directly")
    return bwrap_cmd(cmd, home=home, net=net, name="app", binds_ro=binds, env=env)


def run_once(path, net: bool = False) -> int:
    """Run a file in a disposable sandbox; nothing it writes survives."""
    info = detect(path)
    os.chmod(path, os.stat(path).st_mode | 0o100) if info.kind in ("elf", "appimage") else None
    return subprocess.call(sandbox_argv(info, None, net))


def install(path, net: bool = False, force: bool = False, log=print) -> str | None:
    info = detect(path)
    pl = plan(info)
    log(f"[1/6] identified: {info.kind} {info.arch} ({info.detail})")
    digest = sha256(path)
    log(f"[2/6] sha256: {digest}")
    verdict, why = scan(path)
    log(f"[3/6] malware scan: {verdict} ({why})")
    if verdict == "infected" and not force:
        log("      BLOCKED. Use --force only if you are certain this is a false positive.")
        return None
    log(f"[4/6] runtime: {pl.runtime}; isolation: {pl.isolation}")
    for n in pl.notes:
        log("      note: " + n)
    if not pl.supported or pl.missing:
        log("      cannot install this file on this system.")
        return None

    need, free = space_needed_mb(info.kind, os.path.getsize(path)), free_mb(user_data())
    if free < need:
        log(f"      not enough free space: this needs about {need} MB, only {free} MB is available.")
        for line in space_advice():
            log("      " + line)
        return None

    app_id = _new_id(path, digest)
    d = _app_dir(app_id)
    try:
        return _install_steps(path, info, digest, app_id, d, net, log)
    except OSError as exc:
        shutil.rmtree(d, ignore_errors=True)        # give the space back
        if exc.errno == 28:
            log("      ran out of space during installation; the partial install was removed.")
            for line in space_advice():
                log("      " + line)
        else:
            log(f"      installation failed: {exc}")
        return None


def free_mb(where) -> int:
    return shutil.disk_usage(where).free // (1024 * 1024)


def space_needed_mb(kind: str, size_bytes: int) -> int:
    """Rough space an install consumes, so we can refuse cleanly instead of failing midway."""
    size = size_bytes // (1024 * 1024) + 1
    if kind in ("pe", "msi"):
        return 1400 + 3 * size      # fresh Wine prefix (64 + 32 bit) plus the unpacked program
    if kind in ("deb", "rpm", "archive", "flatpak", "snap"):
        return 100 + 4 * size
    return 50 + 2 * size


def space_advice() -> list:
    cmdline = Path("/proc/cmdline").read_text() if os.path.exists("/proc/cmdline") else ""
    if "boot=live" in cmdline and " persistence" not in " " + cmdline:
        return ["This is an amnesic session: every file is kept in memory, so free space is limited by RAM.",
                "Give the machine more RAM (8 GB or more is comfortable for Windows software),",
                "or boot the 'encrypted persistence' entry so installs go to disk.",
                "Deleting large downloads you no longer need also frees space immediately."]
    return ["Free some disk space and try again."]


def _install_steps(path, info, digest, app_id, d, net, log) -> str | None:
    rec = {"id": app_id, "name": Path(path).stem, "kind": info.kind, "arch": info.arch,
           "sha256": digest, "source": os.path.abspath(path), "installed": int(time.time()),
           "net": net, "packages": []}
    k = info.kind
    log("[5/6] installing")
    if k in ("deb", "rpm"):
        before = _dpkg_set()
        (d / "pre-install.pkgs").write_text("\n".join(sorted(before)))
        deb = os.path.abspath(path)
        if k == "rpm":
            r = sh(["fakeroot", "alien", "--to-deb", os.path.abspath(path)], cwd=d)
            debs = sorted(d.glob("*.deb"))
            if r.returncode != 0 or not debs:
                log("      conversion failed: " + r.stderr.strip()[-300:])
                return None
            deb = str(debs[0])
        if _root(["apt-get", "install", "-y", deb]) != 0:
            log("      apt failed; nothing was registered.")
            return None
        rec["packages"] = sorted(_dpkg_set() - before)
    elif k == "flatpak":
        if subprocess.call(["flatpak", "install", "--user", "-y", os.path.abspath(path)]) != 0:
            return None
    elif k == "snap":
        if _root(["snap", "install", "--dangerous", os.path.abspath(path)]) != 0:
            return None
    elif k == "apk":
        if subprocess.call(["waydroid", "app", "install", os.path.abspath(path)]) != 0:
            return None
    elif k == "archive":
        src = d / "src"
        src.mkdir(exist_ok=True)
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                zf.extractall(src)
        else:
            with tarfile.open(path) as tf:
                tf.extractall(src, filter="data")
        rec["build_hint"] = build_hint(src)
        log(f"      extracted to {src}")
        log(f"      suggested build: {rec['build_hint'] or 'no known build system found'}")
    else:
        home = d / "home"
        if k == "msi":
            # An MSI is only an installer: run it from where it is, never keep a second copy.
            prefix_c = home / ".wine" / "drive_c"
            rc = subprocess.call(sandbox_argv(info, home, net))
            new = [e for e in _program_exes(prefix_c) if "unins" not in e.lower()][:5]
            if not new:
                shutil.rmtree(d, ignore_errors=True)
                log(f"      the installer exited with code {rc} and installed no program; nothing was kept.")
                return None
            rec["exes"] = new
            for e in new:
                _launcher(app_id, Path(e).stem, e)
        else:
            payload = d / os.path.basename(path)
            shutil.copy2(path, payload)
            os.chmod(payload, 0o755)
            rec["payload"] = str(payload)
            if k == "pe":
                prefix_c = home / ".wine" / "drive_c"
                before_exes = set(_program_exes(prefix_c))
                subprocess.call(sandbox_argv(detect(payload), home, net))
                new = [e for e in _program_exes(prefix_c) if e not in before_exes
                       and "unins" not in e.lower()][:5]
                rec["exes"] = new
                for e in new:
                    _launcher(app_id, Path(e).stem, e)
                if not new:
                    _launcher(app_id, rec["name"])   # portable program: launch the file itself
            else:
                _launcher(app_id, rec["name"])
    reg = registry()
    reg[app_id] = rec
    _save(reg)
    log(f"[6/6] registered as '{app_id}'")
    return app_id


def _program_exes(drive_c: Path) -> list[str]:
    found = []
    for top in ("Program Files", "Program Files (x86)"):
        base = drive_c / top
        if base.is_dir():
            for p in base.rglob("*.exe"):
                rel = p.relative_to(drive_c).as_posix()
                if not rel.startswith(("Program Files/Common Files", "Program Files/Internet Explorer",
                                       "Program Files/Windows", "Program Files (x86)/Common Files",
                                       "Program Files (x86)/Internet Explorer", "Program Files (x86)/Windows")):
                    found.append(rel)
    return sorted(found)


def build_hint(src: Path) -> str:
    roots = [src] + [p for p in src.iterdir() if p.is_dir()][:1]
    for root in roots:
        for marker, cmd in (("meson.build", "meson setup build && ninja -C build"),
                            ("CMakeLists.txt", "cmake -B build && cmake --build build -j"),
                            ("configure", "./configure --prefix=$HOME/.local && make -j && make install"),
                            ("Cargo.toml", "cargo build --release"),
                            ("go.mod", "go build ./..."),
                            ("pyproject.toml", "python3 -m venv .venv && .venv/bin/pip install ."),
                            ("setup.py", "python3 -m venv .venv && .venv/bin/pip install ."),
                            ("package.json", "npm install"),
                            ("Makefile", "make -j")):
            if (root / marker).exists():
                return f"cd '{root}' && {cmd}"
    return ""


def run_installed(app_id: str, exe: str = "") -> int:
    rec = registry().get(app_id)
    if not rec:
        raise KeyError(f"no such app '{app_id}'")
    if not exe and "payload" not in rec:
        exe = (rec.get("exes") or [""])[0]           # installed from an MSI: start its first program
        if not exe:
            raise KeyError(f"'{app_id}' has nothing runnable")
    info = FileInfo(rec.get("payload", ""), rec["kind"], rec.get("arch", ""))
    if info.kind == "script":
        info = detect(rec["payload"])                 # scripts need their interpreter line
    home = _app_dir(app_id) / "home"
    return subprocess.call(sandbox_argv(info, home, rec.get("net", False), exe))


def remove(app_id: str, log=print) -> bool:
    reg = registry()
    rec = reg.get(app_id)
    if not rec:
        log(f"no such app '{app_id}'")
        return False
    if rec.get("packages"):
        _root(["apt-get", "remove", "--autoremove", "-y", *rec["packages"]])
    for f in (Path.home() / ".local/share/applications").glob(f"cidint-app-{app_id}*.desktop"):
        f.unlink()
    shutil.rmtree(user_data() / "apps" / app_id, ignore_errors=True)
    del reg[app_id]
    _save(reg)
    log(f"removed '{app_id}' and its data")
    return True
