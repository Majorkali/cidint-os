"""CIDINT OS core library: shared helpers used by every subsystem."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

VERSION = "0.1.0"
RUN_DIR = Path("/run/cidint")
ETC_DIR = Path("/etc/cidint")
LOG_DIR = Path("/var/log/cidint")
SHARE_DIR = Path("/usr/share/cidint")
CLI = "/usr/bin/cidint"


def user_data() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    path = base / "cidint"
    path.mkdir(parents=True, exist_ok=True)
    return path


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Run a command, capturing text output. Never raises on non-zero exit."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kw)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def read(path, default: str = "") -> str:
    try:
        return Path(path).read_text(errors="replace").strip()
    except OSError:
        return default


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_json_atomic(path, data, mode: int = 0o644) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def elevate() -> None:
    """Re-execute the current cidint command as root (sudo if passwordless, else polkit)."""
    if is_root():
        return
    argv = [CLI, *sys.argv[1:]]
    if have("sudo") and sh(["sudo", "-n", "true"]).returncode == 0:
        os.execvp("sudo", ["sudo", *argv])
    if have("pkexec"):
        os.execvp("pkexec", ["pkexec", *argv])
    sys.exit("cidint: this command needs root privileges")
