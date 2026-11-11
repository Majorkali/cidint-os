"""Namespace sandbox builder (bubblewrap).

Every compartment gets its own user, PID, IPC, UTS and (unless networking is
granted) network namespace, a read-only view of the system, a scrubbed
environment and either a private persistent home or a tmpfs home that is
destroyed when the last process exits.

This is kernel-namespace isolation, the same primitive Flatpak uses. It is
weaker than the hardware-virtualised VMs of Qubes OS: a kernel exploit crosses
it. Use `cidint domain vm` when you need a VM boundary.
"""
from __future__ import annotations

import os
from pathlib import Path

SANDBOX_HOME = "/home/user"
PASS_ENV = ("LANG", "LC_ALL", "TERM", "COLORTERM", "DISPLAY", "WAYLAND_DISPLAY",
            "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "QT_QPA_PLATFORM")


def bwrap_cmd(cmd: list[str], *, home: Path | None = None, net: bool = False,
              gui: bool = True, name: str = "sandbox",
              binds_ro: tuple = (), binds_rw: tuple = (),
              env: dict | None = None) -> list[str]:
    """Return the argv that runs `cmd` inside a fresh compartment.

    binds_ro / binds_rw are (host_path, sandbox_path) pairs.
    home=None gives a disposable tmpfs home.
    """
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    xdg = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    a = ["bwrap",
         "--unshare-user", "--unshare-ipc", "--unshare-pid", "--unshare-uts",
         "--unshare-cgroup-try", "--hostname", f"cidint-{name}",
         "--die-with-parent", "--new-session",
         "--ro-bind", "/usr", "/usr",
         "--ro-bind", "/etc", "/etc",
         "--ro-bind-try", "/opt", "/opt",
         "--ro-bind", "/sys", "/sys",
         "--proc", "/proc", "--dev", "/dev",
         "--tmpfs", "/tmp", "--tmpfs", "/run", "--tmpfs", "/var",
         "--ro-bind-try", "/var/cache/fontconfig", "/var/cache/fontconfig",
         "--dir", xdg]
    for link in ("bin", "sbin", "lib", "lib32", "lib64", "libx32"):
        p = Path("/" + link)
        if p.is_symlink():
            a += ["--symlink", os.readlink(p), str(p)]
        elif p.is_dir():
            a += ["--ro-bind", str(p), str(p)]

    if home is None:
        a += ["--tmpfs", SANDBOX_HOME]
    else:
        Path(home).mkdir(parents=True, exist_ok=True)
        a += ["--bind", str(home), SANDBOX_HOME]

    a += ["--clearenv",
          "--setenv", "HOME", SANDBOX_HOME,
          "--setenv", "USER", "user",
          "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin:/usr/games",
          "--setenv", "XDG_RUNTIME_DIR", xdg,
          "--setenv", "CIDINT_DOMAIN", name]
    for key in PASS_ENV:
        if os.environ.get(key):
            a += ["--setenv", key, os.environ[key]]

    if gui:
        wd = os.environ.get("WAYLAND_DISPLAY")
        if wd:
            sock = wd if os.path.isabs(wd) else os.path.join(xdg, wd)
            a += ["--ro-bind-try", sock, sock]
        a += ["--ro-bind-try", "/tmp/.X11-unix", "/tmp/.X11-unix"]
        xauth = os.environ.get("XAUTHORITY")
        if xauth:
            a += ["--ro-bind-try", xauth, "/run/xauth", "--setenv", "XAUTHORITY", "/run/xauth"]
        for s in ("pipewire-0", "pulse"):
            a += ["--ro-bind-try", os.path.join(xdg, s), os.path.join(xdg, s)]
        a += ["--dev-bind-try", "/dev/dri", "/dev/dri"]

    if net:
        a += ["--ro-bind-try", "/run/systemd/resolve", "/run/systemd/resolve"]
    else:
        a += ["--unshare-net"]

    for src, dst in binds_ro:
        a += ["--ro-bind", str(src), str(dst)]
    for src, dst in binds_rw:
        a += ["--bind", str(src), str(dst)]
    for key, val in (env or {}).items():
        a += ["--setenv", key, str(val)]

    a += ["--chdir", SANDBOX_HOME, "--", *cmd]
    return a
