"""Sentinel: behavioural threat detection.

Every few seconds each process is sampled from /proc. Two signals are combined
into a 0-100 risk score:

  * statistical anomaly: per-program online models (exponentially weighted
    mean and variance) of CPU, memory, write rate, open files and threads; a
    reading several standard deviations above what that program normally does
    is anomalous.
  * heuristics for known-bad behaviour: executing a deleted binary, running
    from a world-writable directory, unexplained root children of unprivileged
    parents, library injection, sustained mass writes (ransomware-like).

Events go to /var/log/cidint/sentinel.jsonl. With containment enabled
(/etc/cidint/sentinel-contain) a critical process is frozen with SIGSTOP for
you to inspect; it is never killed automatically.

This is a detector, not a guarantee: it raises the cost of an attack and will
miss a careful adversary, like every behavioural monitor.
"""
from __future__ import annotations

import json
import math
import os
import signal
import time
from pathlib import Path

from . import ETC_DIR, LOG_DIR, RUN_DIR, read, write_json_atomic

INTERVAL = 5
WARMUP = 12
Z_LIMIT = 4.0
WARN, CRITICAL = 50, 80
MASS_WRITE = 50 * 1024 * 1024          # bytes/second
TEMP_DIRS = ("/tmp/", "/dev/shm/", "/var/tmp/", "/run/user/")
PRIV_OK = {"sudo", "su", "pkexec", "polkit-agent-he", "login", "sshd", "cron", "systemd",
           "sddm-helper", "unix_chkpwd", "fusermount3", "newuidmap", "newgidmap",
           "dbus-daemon-lau", "mount", "umount", "passwd", "ping", "bwrap", "cidint"}
HEAVY_WRITERS = {"dd", "rsync", "cp", "tar", "apt-get", "dpkg", "mksquashfs", "qemu-system-x86",
                 "clamscan", "freshclam", "flatpak", "podman", "baloo_file", "btrfs", "git"}
EVENTS = LOG_DIR / "sentinel.jsonl"
SUMMARY = RUN_DIR / "sentinel.json"


class Ewma:
    """Online mean/variance with exponential forgetting."""
    __slots__ = ("n", "mean", "var")

    def __init__(self):
        self.n, self.mean, self.var = 0, 0.0, 0.0

    def z(self, x: float) -> float:
        if self.n < WARMUP:
            return 0.0
        floor = max(abs(self.mean) * 0.10, 1e-6)   # ignore tiny wobbles around a flat baseline
        return (x - self.mean) / max(math.sqrt(self.var), floor)

    def update(self, x: float, alpha: float = 0.08) -> None:
        if self.n == 0:
            self.mean = x
        else:
            d = x - self.mean
            self.mean += alpha * d
            self.var = (1 - alpha) * (self.var + alpha * d * d)
        self.n += 1


def score(*, exe: str, comm: str, uid: int, parent_uid: int, preload: bool,
          write_rate: float, mass_write_ticks: int, anomalies: list) -> tuple[int, list]:
    """Pure scoring function -> (risk 0-100, reasons)."""
    pts, why = 0, []
    if exe.endswith(" (deleted)"):
        pts += 35
        why.append("running a binary that was deleted from disk")
    if exe.startswith(TEMP_DIRS):
        pts += 30
        why.append(f"executing from a temporary directory ({exe})")
    if uid == 0 and parent_uid not in (0, -1) and comm not in PRIV_OK:
        pts += 40
        why.append("root process spawned by an unprivileged parent")
    if preload:
        pts += 15
        why.append("LD_PRELOAD library injection")
    if mass_write_ticks >= 3 and comm not in HEAVY_WRITERS:
        pts += 25
        why.append(f"sustained mass writes ({write_rate / 1048576:.0f} MB/s), ransomware-like")
    if anomalies:
        pts += min(30, 10 * len(anomalies))
        why.append("unusual for this program: " + ", ".join(anomalies))
    return min(100, pts), why


def _stat(pid: str):
    raw = read(f"/proc/{pid}/stat")
    if not raw or ")" not in raw:
        return None
    comm = raw[raw.index("(") + 1:raw.rindex(")")]
    f = raw[raw.rindex(")") + 2:].split()
    try:
        return {"comm": comm, "ppid": int(f[1]), "cpu": int(f[11]) + int(f[12]),
                "threads": int(f[17]), "rss": int(f[21])}
    except (IndexError, ValueError):
        return None


def _uid(pid) -> int:
    try:
        return os.stat(f"/proc/{pid}").st_uid
    except OSError:
        return -1


def _write_bytes(pid: str) -> int:
    for line in read(f"/proc/{pid}/io").splitlines():
        if line.startswith("write_bytes:"):
            return int(line.split()[1])
    return 0


def daemon() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    models: dict = {}          # (comm, feature) -> Ewma
    prev: dict = {}            # pid -> (cpu_ticks, write_bytes)
    mass: dict = {}            # pid -> consecutive mass-write ticks
    reported: dict = {}        # (pid, reasons) -> timestamp
    recent: list = []
    modules = set(l.split()[0] for l in read("/proc/modules").splitlines() if l)
    hz = os.sysconf("SC_CLK_TCK")
    while True:
        time.sleep(INTERVAL)
        now = time.time()
        contain = (ETC_DIR / "sentinel-contain").exists()
        seen = set()
        for pid in filter(str.isdigit, os.listdir("/proc")):
            st = _stat(pid)
            if st is None:
                continue
            try:
                exe = os.readlink(f"/proc/{pid}/exe")
            except OSError:
                continue  # kernel thread or gone
            seen.add(pid)
            wb = _write_bytes(pid)
            last_cpu, last_wb = prev.get(pid, (st["cpu"], wb))
            prev[pid] = (st["cpu"], wb)
            cpu_rate = (st["cpu"] - last_cpu) / hz / INTERVAL
            write_rate = max(0, wb - last_wb) / INTERVAL
            mass[pid] = mass.get(pid, 0) + 1 if write_rate > MASS_WRITE else 0
            try:
                fds = len(os.listdir(f"/proc/{pid}/fd"))
            except OSError:
                fds = 0
            feats = {"cpu": cpu_rate, "memory": float(st["rss"]), "write rate": write_rate,
                     "open files": float(fds), "threads": float(st["threads"])}
            anomalies = []
            for name, val in feats.items():
                m = models.setdefault((st["comm"], name), Ewma())
                if m.z(val) > Z_LIMIT:
                    anomalies.append(name)
                m.update(val)
            env = read(f"/proc/{pid}/environ")
            risk, why = score(exe=exe, comm=st["comm"], uid=_uid(pid), parent_uid=_uid(st["ppid"]),
                              preload="LD_PRELOAD=" in env, write_rate=write_rate,
                              mass_write_ticks=mass[pid], anomalies=anomalies)
            if risk < WARN:
                continue
            key = (pid, tuple(why))
            if now - reported.get(key, 0) < 600:
                continue
            reported[key] = now
            ev = {"time": int(now), "level": "critical" if risk >= CRITICAL else "warning",
                  "risk": risk, "pid": int(pid), "program": st["comm"], "exe": exe,
                  "reasons": why, "contained": False}
            if contain and risk >= CRITICAL and int(pid) != os.getpid():
                try:
                    os.kill(int(pid), signal.SIGSTOP)
                    ev["contained"] = True
                except OSError:
                    pass
            recent.append(ev)
            with open(EVENTS, "a") as fh:
                fh.write(json.dumps(ev) + "\n")
        for table in (prev, mass):
            for pid in set(table) - seen:
                del table[pid]
        current = set(l.split()[0] for l in read("/proc/modules").splitlines() if l)
        for mod in sorted(current - modules):
            ev = {"time": int(now), "level": "info", "risk": 10, "pid": 0, "program": "kernel",
                  "exe": "", "reasons": [f"kernel module loaded: {mod}"], "contained": False}
            recent.append(ev)
            with open(EVENTS, "a") as fh:
                fh.write(json.dumps(ev) + "\n")
        modules = current
        recent[:] = [e for e in recent if now - e["time"] < 3600][-50:]
        write_json_atomic(SUMMARY, {
            "time": int(now), "processes": len(seen), "containment": contain,
            "risk": max((e["risk"] for e in recent), default=0), "events": recent[-20:]})
