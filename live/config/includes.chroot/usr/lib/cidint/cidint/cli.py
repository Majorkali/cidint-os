"""`cidint` command-line interface: one entry point for every subsystem."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from . import (ETC_DIR, RUN_DIR, SHARE_DIR, VERSION, anon, assistant, domains, elevate, have,
               hwadapt, read, read_json, sentinel, session, sh, uaee)


def _kv(title: str, rows: list) -> None:
    print(title)
    for k, v in rows:
        print(f"  {k:<22} {v}")


# ------------------------------------------------------------------ status ---

def posture() -> dict:
    hw = read_json(hwadapt.STATE, {})
    inv = hw.get("inventory", {})
    net = anon.status()
    sen = read_json(sentinel.SUMMARY, {})
    cmdline = read("/proc/cmdline")
    live = "boot=live" in cmdline
    persistent = " persistence" in cmdline
    checks = {
        "Firewall (inbound blocked)": sh(["systemctl", "is-active", "nftables"]).stdout.strip() == "active",
        "AppArmor enforcing": read("/sys/module/apparmor/parameters/enabled") == "Y",
        "Kernel pointers hidden": read("/proc/sys/kernel/kptr_restrict") == "2",
        "Memory wiped on free": "init_on_free=1" in cmdline,
        "Threat sentinel running": sh(["systemctl", "is-active", "cidint-sentinel"]).stdout.strip() == "active",
        "UEFI Secure Boot": bool(inv.get("secure_boot")),
        "TPM 2.0 present": bool(inv.get("tpm")),
        "Storage amnesic or encrypted": (live and not persistent) or "persistence-encryption=luks" in cmdline
                                        or bool(sh(["sh", "-c", "lsblk -no TYPE | grep -q crypt"]).returncode == 0),
    }
    return {"net": net, "hw": hw, "sentinel": sen, "checks": checks, "live": live,
            "persistent": persistent,
            "score": round(100 * sum(checks.values()) / len(checks))}


def cmd_status(_a) -> int:
    p = posture()
    print(f"CIDINT OS {VERSION}  |  security posture {p['score']}/100")
    _kv("Session", [("mode", ("live, amnesic" if not p["persistent"] else "live, encrypted persistence")
                     if p["live"] else "installed"),
                    ("network", p["net"]["mode"] + (" (Tor running)" if p["net"]["tor_running"] else "")),
                    ("threat risk", f"{p['sentinel'].get('risk', 0)}/100"),
                    ("power profile", p["hw"].get("profile", "unknown"))])
    _kv("Checks", [(k, "yes" if v else "NO") for k, v in p["checks"].items()])
    return 0


# ----------------------------------------------------------------- network ---

def cmd_anon(a) -> int:
    if a.action == "status":
        s = anon.status()
        print(f"mode: {s['mode']}   tor: {'running' if s['tor_running'] else 'stopped'}")
        return 0
    elevate()
    if a.action == "on":
        return 0 if anon.anon_on() else 1
    anon.anon_off()
    return 0


def cmd_lockdown(a) -> int:
    elevate()
    anon.lockdown(a.action == "on")
    return 0


def cmd_panic(a) -> int:
    elevate()
    anon.lockdown(True)
    sh(["loginctl", "lock-sessions"])
    Path("/proc/sys/vm/drop_caches").write_text("3")
    print("Emergency lockdown: network blocked, sessions locked, caches dropped.")
    if a.poweroff:
        print("Powering off now. In amnesic live mode nothing from this session is kept.")
        subprocess.call(["systemctl", "poweroff", "--force", "--force"])
    return 0


# ----------------------------------------------------------------- domains ---

def cmd_domain(a) -> int:
    if a.action == "list":
        for name, d in domains.load().items():
            print(f"  {name:<12} network={d.get('network', 'none'):<7} {d.get('label', '')}")
        print("  disposable   network=tor     destroyed when it exits")
        return 0
    if a.action == "vm":
        if not a.args:
            sys.exit("usage: cidint domain vm IMAGE [--net]")
        return domains.vm(a.args[0], network=a.net)
    cmd = [x for x in a.args if x != "--"]
    try:
        if a.action == "disposable":
            return domains.run("disposable", cmd or ["konsole"], disposable=True,
                               network="direct" if a.net else None)
        if not cmd:
            sys.exit("usage: cidint domain run NAME -- COMMAND...")
        return domains.run(cmd[0], cmd[1:] or ["konsole"])
    except (KeyError, ValueError, RuntimeError) as exc:
        sys.exit(f"cidint: {exc}")


# -------------------------------------------------------------------- apps ---

def _describe(path: str) -> tuple:
    info = uaee.detect(path)
    return info, uaee.plan(info)


def cmd_app(a) -> int:
    act = a.action
    if act == "list":
        reg = uaee.registry()
        if not reg:
            print("no applications installed through UAEE")
        for app_id, r in reg.items():
            print(f"  {app_id:<34} {r['kind']:<9} {r['name']}")
        return 0
    if not a.target:
        sys.exit(f"usage: cidint app {act} FILE_OR_ID")
    if act == "remove":
        return 0 if uaee.remove(a.target) else 1
    if act == "run" and a.target in uaee.registry():
        return uaee.run_installed(a.target, a.exe or "")
    if not os.path.exists(a.target):
        sys.exit(f"cidint: no such file or app: {a.target}")
    info, pl = _describe(a.target)
    if act == "inspect":
        _kv(os.path.basename(a.target), [
            ("type", f"{info.kind} ({info.detail})"), ("architecture", info.arch or "n/a"),
            ("host", uaee.host_arch()), ("supported", "yes" if pl.supported and not pl.missing else "no"),
            ("runtime", pl.runtime), ("isolation", pl.isolation),
            ("sha256", uaee.sha256(a.target)), ("malware scan", " - ".join(uaee.scan(a.target)))])
        for n in pl.notes:
            print("  note: " + n)
        return 0
    if act == "run":
        if not pl.supported or pl.missing:
            sys.exit("cidint: cannot run this file here: " + "; ".join(pl.notes))
        verdict, why = uaee.scan(a.target)
        if verdict == "infected" and not a.force:
            sys.exit(f"cidint: blocked, malware detected ({why})")
        return uaee.run_once(a.target, net=a.net)
    if act == "install":
        return 0 if uaee.install(a.target, net=a.net, force=a.force) else 1
    if act == "open":
        return _open_dialog(a.target, info, pl)
    return 2


def _open_dialog(path: str, info, pl) -> int:
    """Double-click handler: explain what the file is and let the user choose."""
    summary = (f"{os.path.basename(path)}\n\nType: {info.kind} {info.arch} ({info.detail})\n"
               f"Runtime: {pl.runtime}\nIsolation: {pl.isolation}\n\n" + "\n".join(pl.notes))
    if not have("kdialog") or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        print(summary)
        return 0
    if not pl.supported or pl.missing:
        subprocess.call(["kdialog", "--title", "CIDINT Installer", "--sorry", summary])
        return 1
    runnable = info.kind in ("pe", "msi", "winbatch", "appimage", "elf", "script", "powershell", "jar")
    menu = ["install", "Install (scan, isolate, add to launcher)"]
    if runnable:
        menu += ["once", "Run once in a disposable sandbox, no network",
                 "oncenet", "Run once in a disposable sandbox, with network"]
    r = sh(["kdialog", "--title", "CIDINT Installer", "--menu", summary, *menu])
    choice = r.stdout.strip()
    if r.returncode != 0 or not choice:
        return 0
    args = {"install": ["app", "install"], "once": ["app", "run"], "oncenet": ["app", "run", "--net"]}[choice]
    return subprocess.call(["konsole", "--hold", "-e", "/usr/bin/cidint", *args, path])


# ---------------------------------------------------------- hw / sentinel ---

def cmd_hw(a) -> int:
    if a.action == "daemon":
        hwadapt.daemon()
    s = read_json(hwadapt.STATE, None)
    if s is None:
        s = {"inventory": hwadapt.inventory()}
    inv = s["inventory"]
    _kv("Hardware", [("cpu", f"{inv['cpu']} ({inv['cores']} threads, {inv['arch']})"),
                     ("memory", f"{inv['ram_mb']} MB"),
                     ("gpu", ", ".join(g["driver"] or "none" for g in inv["gpus"]) or "none"),
                     ("storage", ", ".join(d["name"] for d in inv["disks"])),
                     ("hypervisor", inv["hypervisor"]),
                     ("AES acceleration", "yes" if inv["aes"] else "no"),
                     ("virtualization", "yes" if inv["virtualization"] else "no"),
                     ("TPM / Secure Boot", f"{'yes' if inv['tpm'] else 'no'} / {'yes' if inv['secure_boot'] else 'no'}"),
                     ("desktop effects", inv["effects"])])
    if "profile" in s:
        _kv("Adaptive tuning", [
            ("power profile", f"{s['profile']} ({s['profile_reason']})"),
            ("cpu load / forecast", f"{s['cpu_load']:.0%} / {s['cpu_forecast']:.0%}"),
            ("memory available", f"{s['mem_available_mb']} of {s['mem_total_mb']} MB"),
            ("temperature", f"{s['temp_c']} C"),
            ("power", "AC" if s["on_ac"] else f"battery {s['battery_pct']}%"),
            ("pressure cpu/mem/io", " / ".join(str(s["pressure"][k]) for k in ("cpu", "memory", "io"))),
            ("I/O schedulers", ", ".join(f"{k}={v}" for k, v in s["io_schedulers"].items()) or "default")])
    return 0


def cmd_sentinel(a) -> int:
    if a.action == "daemon":
        sentinel.daemon()
    if a.action == "contain":
        elevate()
        flag = ETC_DIR / "sentinel-contain"
        if a.target == "off":
            flag.unlink(missing_ok=True)
        else:
            flag.touch()
        print("containment " + ("disabled" if a.target == "off" else "enabled: critical processes will be frozen"))
        return 0
    s = read_json(sentinel.SUMMARY, {})
    if a.action == "status":
        print(f"risk {s.get('risk', 0)}/100, watching {s.get('processes', 0)} processes, "
              f"containment {'on' if s.get('containment') else 'off'}")
        return 0
    events = s.get("events", [])
    if not events:
        print("no security events in the last hour")
    for e in events:
        stamp = time.strftime("%H:%M:%S", time.localtime(e["time"]))
        print(f"{stamp} {e['level'].upper():<8} risk={e['risk']:<3} {e['program']}[{e['pid']}] "
              + "; ".join(e["reasons"]) + (" [FROZEN]" if e["contained"] else ""))
    return 0


# ------------------------------------------------------------- persistence ---

def cmd_persist(a) -> int:
    elevate()
    dev = a.device
    if not Path(dev).is_block_device():
        sys.exit(f"cidint: {dev} is not a block device")
    if dev in read("/proc/mounts"):
        sys.exit(f"cidint: {dev} is mounted; refusing to touch it")
    if not a.yes_destroy_data:
        sys.exit(f"cidint: this DESTROYS everything on {dev}. Re-run with --yes-destroy-data to confirm.")
    if a.action == "destroy":
        r = subprocess.call(["cryptsetup", "erase", "-q", dev])
        print("All LUKS key slots erased; the data is unrecoverable without a header backup.\n"
              "Note: flash storage may retain old header copies in spare blocks.")
        return r
    steps = [
        ["cryptsetup", "luksFormat", "--type", "luks2", "--cipher", "aes-xts-plain64",
         "--key-size", "512", "--hash", "sha512", "--pbkdf", "argon2id", "--iter-time", "4000", dev],
        ["cryptsetup", "open", dev, "cidint-persist"],
        ["mkfs.ext4", "-q", "-L", "persistence", "/dev/mapper/cidint-persist"],
    ]
    for step in steps:
        if subprocess.call(step) != 0:
            sys.exit("cidint: failed at: " + " ".join(step))
    mnt = RUN_DIR / "persist-mnt"
    mnt.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.check_call(["mount", "/dev/mapper/cidint-persist", str(mnt)])
        (mnt / "persistence.conf").write_text("/home union\n/etc/NetworkManager/system-connections union\n")
    finally:
        subprocess.call(["umount", str(mnt)])
        subprocess.call(["cryptsetup", "close", "cidint-persist"])
    print("Encrypted persistence created. Boot the 'encrypted persistence' menu entry to use it.")
    return 0


# ------------------------------------------------------------ misc / entry ---

def cmd_ask(a) -> int:
    text = " ".join(a.text)
    hit = assistant.interpret(text)
    if not hit:
        print("I did not understand that. Try: 'turn on anonymous mode', 'show threats', "
              "'why is my system slow', 'open a disposable browser', 'show errors'.")
        return 1
    explanation, argv, mutating = hit
    print(f"{explanation}\n  $ {' '.join(argv)}")
    if mutating and not a.yes:
        if input("Run it? [y/N] ").strip().lower() not in ("y", "yes"):
            return 0
    return subprocess.call(argv)


def cmd_session_init(_a) -> int:
    """Per-user first-login setup: apply the CIDINT look, adapt effects to the hardware."""
    marker = Path.home() / ".config" / "cidint-session-initialized"
    if marker.exists():
        started = session.resume_if_pending()      # continue the last tasks after a reboot
        if started and have("notify-send"):
            sh(["notify-send", "-a", "CIDINT OS", "Session resumed", ", ".join(started)])
        return 0
    if have("plasma-apply-colorscheme"):
        sh(["plasma-apply-colorscheme", "CidintDark"])
    if have("plasma-apply-wallpaperimage"):
        sh(["plasma-apply-wallpaperimage", str(SHARE_DIR / "wallpaper.svg")])
    effects = read_json(hwadapt.STATE, {}).get("inventory", {}).get("effects") \
        or hwadapt.inventory()["effects"]
    if have("kwriteconfig6"):
        on = "true" if effects == "full" else "false"
        for plugin in ("blurEnabled", "contrastEnabled", "wobblywindowsEnabled"):
            sh(["kwriteconfig6", "--file", "kwinrc", "--group", "Plugins", "--key", plugin,
                on if plugin != "wobblywindowsEnabled" else "false"])
        if effects != "full":
            sh(["kwriteconfig6", "--file", "kdeglobals", "--group", "KDE",
                "--key", "AnimationDurationFactor", "0"])
        for bus in ("qdbus6", "qdbus"):
            if have(bus):
                sh([bus, "org.kde.KWin", "/KWin", "reconfigure"])
                break
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    return 0


def cmd_session(a) -> int:
    if a.action == "save":
        data = session.save()
        print(f"Saved {len(data['apps'])} open application(s):")
        for app in data["apps"]:
            print("  " + app["name"])
        if session.is_persistent():
            print("They will reopen automatically the next time you log in, including after a reboot.")
        else:
            print("NOTE: this is an amnesic session, so nothing survives a reboot by design.")
            print("The session will resume after logging out and in, but not after power-off.")
            print("Boot the 'encrypted persistence' entry to resume across reboots.")
        return 0
    if a.action == "restore":
        started = session.restore()
        print("Reopened: " + ", ".join(started) if started else "Nothing to reopen.")
        return 0
    if a.action == "clear":
        session.clear()
        print("Saved session removed.")
        return 0
    print(session.describe())
    return 0


def cmd_center(_a) -> int:
    from . import control_center
    return control_center.main()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cidint", description=f"CIDINT OS {VERSION} control tool")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="security posture and system summary").set_defaults(fn=cmd_status)

    s = sub.add_parser("anon", help="Anonymous Mode: force all traffic through Tor")
    s.add_argument("action", choices=["on", "off", "status"])
    s.set_defaults(fn=cmd_anon)

    s = sub.add_parser("lockdown", help="network kill switch")
    s.add_argument("action", choices=["on", "off"])
    s.set_defaults(fn=cmd_lockdown)

    s = sub.add_parser("panic", help="emergency lockdown")
    s.add_argument("--poweroff", action="store_true")
    s.set_defaults(fn=cmd_panic)

    s = sub.add_parser("domain", help="isolated security domains")
    s.add_argument("action", choices=["list", "run", "disposable", "vm"])
    s.add_argument("--net", action="store_true", help="grant direct network access")
    s.add_argument("args", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_domain)

    s = sub.add_parser("app", help="universal application engine")
    s.add_argument("action", choices=["inspect", "open", "install", "run", "list", "remove"])
    s.add_argument("target", nargs="?")
    s.add_argument("--net", action="store_true", help="allow network inside the sandbox")
    s.add_argument("--force", action="store_true", help="proceed despite a malware detection")
    s.add_argument("--exe", help="program inside a Wine prefix, relative to drive_c")
    s.set_defaults(fn=cmd_app)

    s = sub.add_parser("hw", help="hardware adaptation engine")
    s.add_argument("action", choices=["status", "daemon"])
    s.set_defaults(fn=cmd_hw)

    s = sub.add_parser("sentinel", help="behavioural threat detection")
    s.add_argument("action", choices=["status", "events", "daemon", "contain"])
    s.add_argument("target", nargs="?", choices=["on", "off"])
    s.set_defaults(fn=cmd_sentinel)

    s = sub.add_parser("persist", help="encrypted persistent storage (LUKS2)")
    s.add_argument("action", choices=["create", "destroy"])
    s.add_argument("device")
    s.add_argument("--yes-destroy-data", action="store_true")
    s.set_defaults(fn=cmd_persist)

    s = sub.add_parser("ask", help="natural-language control")
    s.add_argument("text", nargs="+")
    s.add_argument("-y", "--yes", action="store_true")
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("session", help="save open applications and continue them after a reboot")
    s.add_argument("action", choices=["save", "restore", "status", "clear"])
    s.set_defaults(fn=cmd_session)

    sub.add_parser("center", help="open the Control Center").set_defaults(fn=cmd_center)
    sub.add_parser("session-init", help=argparse.SUPPRESS).set_defaults(fn=cmd_session_init)

    a = p.parse_args(argv)
    try:
        return a.fn(a) or 0
    except KeyboardInterrupt:
        return 130
