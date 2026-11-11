"""Security domains: named compartments for different activities.

Domains are declared in /etc/cidint/domains.toml. Each has its own private
home directory and a network policy:
    none    no network namespace access at all
    direct  normal networking
    tor     traffic is forced through Tor with torsocks (and by the firewall
            as well when Anonymous Mode is on)
"""
from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

from . import ETC_DIR, have, user_data
from .sandbox import bwrap_cmd

DEFAULTS = {
    "personal":  {"label": "Personal", "color": "#3ddc97", "network": "direct"},
    "work":      {"label": "Work", "color": "#4aa8ff", "network": "direct"},
    "untrusted": {"label": "Untrusted", "color": "#ff5c7a", "network": "tor"},
    "vault":     {"label": "Vault (offline)", "color": "#b48cff", "network": "none"},
}


def load() -> dict:
    try:
        with open(ETC_DIR / "domains.toml", "rb") as fh:
            data = tomllib.load(fh).get("domain", {})
    except (OSError, tomllib.TOMLDecodeError):
        data = {}
    return data or DEFAULTS


def home_of(name: str) -> Path:
    return user_data() / "domains" / name / "home"


def command(name: str, cmd: list[str], *, disposable: bool = False,
            network: str | None = None) -> list[str]:
    if disposable:
        policy = network or "tor"
        home = None
    else:
        domains = load()
        if name not in domains:
            raise KeyError(f"unknown domain '{name}' (known: {', '.join(domains)})")
        policy = network or domains[name].get("network", "none")
        home = home_of(name)
    if policy not in ("none", "direct", "tor"):
        raise ValueError(f"bad network policy '{policy}'")
    if policy == "tor":
        if not have("torsocks"):
            raise RuntimeError("torsocks is not installed")
        cmd = ["torsocks", *cmd]
    return bwrap_cmd(cmd, home=home, net=policy != "none", name=name)


def run(name: str, cmd: list[str], **kw) -> int:
    argv = command(name, cmd, **kw)
    return subprocess.call(argv)


def vm(image: str, memory_mb: int = 2048, network: bool = False) -> int:
    """Boot a disk image or ISO in a throwaway VM. All disk writes are discarded."""
    qemu = "qemu-system-x86_64" if have("qemu-system-x86_64") else "qemu-system-aarch64"
    if not have(qemu):
        sys.exit("cidint: QEMU is not installed")
    a = [qemu, "-m", str(memory_mb), "-smp", "2", "-snapshot",
         "-display", "gtk", "-device", "virtio-rng-pci"]
    if os.access("/dev/kvm", os.R_OK | os.W_OK):
        a += ["-enable-kvm", "-cpu", "host"]
    if image.lower().endswith(".iso"):
        a += ["-cdrom", image, "-boot", "d"]
    else:
        a += ["-drive", f"file={image},if=virtio"]
    a += ["-nic", "user,model=virtio-net-pci" if network else "none"]
    return subprocess.call(a)
