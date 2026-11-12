"""Session resume: continue your last tasks after a reboot.

`save` records the applications you have open (including the files or
arguments they were started with, and which security domain they run in) and
marks the session to be resumed. At the next login `resume_if_pending`
relaunches them.

What survives a reboot depends on how the system was booted:
  * encrypted persistence: the saved session is kept on the LUKS volume and
    is resumed after a reboot.
  * amnesic mode: nothing is written to disk, by design, so a saved session
    only lasts until power-off (it still resumes after logging out and in).
Disposable compartments are never recorded.

Applications are relaunched, not frozen and thawed: programs restore their own
documents and tabs where they support it; unsaved work is not preserved.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from . import CLI, read, read_json, user_data, write_json_atomic

APP_DIRS = ("/usr/share/applications", "/usr/local/share/applications",
            "/var/lib/flatpak/exports/share/applications")
INTERPRETERS = {"python3", "python", "sh", "bash", "env", "perl", "flatpak", "pkexec", "sudo"}
SKIP = {"plasmashell", "kwin_wayland", "kwin_x11", "ksmserver", "kded6", "krunner", "kscreen",
        "plasma-discover", "polkit-kde-authentication-agent-1", "xdg-desktop-portal", "kdialog",
        "ksystemstats", "kaccess", "xwaylandvideobridge", "kwalletd6", "baloo_file", "sddm-greeter"}


def exec_binary(exec_line: str) -> str:
    """Basename of the program a .desktop Exec= line starts ('env A=1 /usr/bin/foo %U' -> 'foo')."""
    for tok in exec_line.replace('"', "").split():
        if "=" in tok and not tok.startswith("/"):
            continue                      # VAR=value prefix
        name = os.path.basename(tok)
        if name == "env":
            continue
        return name
    return ""


def parse_desktop(text: str) -> dict | None:
    """Return {'name', 'binary'} for a launchable application entry, else None."""
    fields, section = {}, ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line
        elif section == "[Desktop Entry]" and "=" in line:
            k, _, v = line.partition("=")
            fields.setdefault(k.strip(), v.strip())
    if fields.get("Type") != "Application" or fields.get("NoDisplay", "").lower() == "true":
        return None
    binary = exec_binary(fields.get("Exec", ""))
    if not binary or binary in INTERPRETERS or binary in SKIP:
        return None
    return {"name": fields.get("Name", binary), "binary": binary}


def desktop_index() -> dict:
    index = {}
    dirs = [*APP_DIRS, str(Path.home() / ".local/share/applications")]
    for d in dirs:
        try:
            files = sorted(Path(d).glob("*.desktop"))
        except OSError:
            continue
        for f in files:
            try:
                entry = parse_desktop(f.read_text(errors="replace"))
            except OSError:
                continue
            if entry:
                index.setdefault(entry["binary"], entry["name"])
    return index


def _cmdline(pid) -> list:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [a.decode(errors="replace") for a in raw.split(b"\0") if a]


def _exe(pid) -> str:
    try:
        return os.path.basename(os.readlink(f"/proc/{pid}/exe")).replace(" (deleted)", "")
    except OSError:
        return ""


def _ppid(pid) -> str:
    raw = read(f"/proc/{pid}/stat")
    try:
        return raw[raw.rindex(")") + 2:].split()[1]
    except (ValueError, IndexError):
        return "0"


def running_apps() -> list:
    """Top-level graphical applications of the current user, as relaunchable commands."""
    index = desktop_index()
    uid, me = os.getuid(), str(os.getpid())
    apps, seen = [], set()
    for pid in sorted(filter(str.isdigit, os.listdir("/proc")), key=int):
        try:
            if os.stat(f"/proc/{pid}").st_uid != uid or pid == me:
                continue
        except OSError:
            continue
        argv = _cmdline(pid)
        if not argv:
            continue
        entry = None
        if CLI in argv[:3]:
            args = argv[argv.index(CLI) + 1:]
            if args[:2] == ["domain", "run"] and len(args) > 2:
                entry = {"name": f"{args[2]} domain: {' '.join(a for a in args[3:] if a != '--')}",
                         "argv": [CLI, *args]}
            elif args[:1] == ["center"]:
                entry = {"name": "CIDINT Control Center", "argv": [CLI, "center"]}
        elif "CIDINT_DOMAIN=" not in read(f"/proc/{pid}/environ"):   # sandboxed apps come back via their domain
            exe = _exe(pid)
            if exe in index and exe not in SKIP and _exe(_ppid(pid)) != exe:
                entry = {"name": index[exe], "argv": argv}
        if entry and tuple(entry["argv"]) not in seen:
            seen.add(tuple(entry["argv"]))
            try:
                entry["cwd"] = os.readlink(f"/proc/{pid}/cwd")
            except OSError:
                entry["cwd"] = ""
            apps.append(entry)
    return apps


def path() -> Path:
    return user_data() / "session.json"


def is_persistent() -> bool:
    """True when the home directory survives a reboot."""
    cmdline = read("/proc/cmdline")
    return "boot=live" not in cmdline or " persistence" in " " + cmdline


def save(apps: list | None = None) -> dict:
    data = {"time": int(time.time()), "resume": True,
            "apps": running_apps() if apps is None else apps}
    write_json_atomic(path(), data, 0o600)
    return data


def load() -> dict:
    return read_json(path(), {})


def clear() -> None:
    path().unlink(missing_ok=True)


def restore(launch=None, running=None) -> list:
    """Relaunch saved applications that are not already open. Returns the names started."""
    data = load()
    if running is None:
        running = {tuple(a["argv"]) for a in running_apps()}
    started = []
    for app in data.get("apps", []):
        argv = app.get("argv") or []
        if not argv or tuple(argv) in running:
            continue
        cwd = app.get("cwd") or None
        if cwd and not os.path.isdir(cwd):
            cwd = None
        try:
            if launch:
                launch(argv, cwd)
            else:
                subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            started.append(app.get("name", argv[0]))
        except OSError:
            continue
    if data:
        data["resume"] = False
        write_json_atomic(path(), data, 0o600)
    return started


def resume_if_pending() -> list:
    return restore() if load().get("resume") else []


def describe() -> str:
    data = load()
    if not data.get("apps"):
        return "No saved session."
    when = time.strftime("%d %b %H:%M", time.localtime(data.get("time", 0)))
    names = ", ".join(a.get("name", "?") for a in data["apps"][:6])
    more = "" if len(data["apps"]) <= 6 else f" and {len(data['apps']) - 6} more"
    state = "will resume at next login" if data.get("resume") else "already resumed"
    return f"Saved {when}: {names}{more} ({state})."
