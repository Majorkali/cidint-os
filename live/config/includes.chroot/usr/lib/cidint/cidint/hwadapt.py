"""Hardware adaptation engine.

Inventories the machine at start, then every few seconds samples load, memory
pressure, temperature and power state, forecasts near-future CPU demand with
Holt double-exponential smoothing, and switches the power profile, I/O
schedulers and VM tunables to match. Decisions use hysteresis so the system
does not flap between profiles.

State is published to /run/cidint/hw.json for the desktop and the CLI.
"""
from __future__ import annotations

import glob
import os
import time
from pathlib import Path

from . import RUN_DIR, have, read, sh, write_json_atomic

STATE = RUN_DIR / "hw.json"
INTERVAL = 5
SOFTWARE_GPU = {"", "simpledrm", "bochs-drm", "vboxvideo", "cirrus", "vkms"}


class Holt:
    """Double exponential smoothing: tracks level and trend, forecasts ahead."""

    def __init__(self, alpha: float = 0.35, beta: float = 0.15):
        self.alpha, self.beta = alpha, beta
        self.level = None
        self.trend = 0.0

    def update(self, x: float) -> None:
        if self.level is None:
            self.level = x
            return
        prev = self.level
        self.level = self.alpha * x + (1 - self.alpha) * (prev + self.trend)
        self.trend = self.beta * (self.level - prev) + (1 - self.beta) * self.trend

    def forecast(self, steps: int = 3) -> float:
        return 0.0 if self.level is None else self.level + steps * self.trend


class Hysteresis:
    """Only accept a new value after it has been proposed `hold` times in a row."""

    def __init__(self, initial: str, hold: int = 3):
        self.current, self.hold = initial, hold
        self._candidate, self._count = initial, 0

    def propose(self, value: str) -> str:
        if value == self.current:
            self._count = 0
        elif value == self._candidate:
            self._count += 1
            if self._count >= self.hold:
                self.current, self._count = value, 0
        else:
            self._candidate, self._count = value, 1
            if self.hold <= 1:
                self.current, self._count = value, 0
        return self.current


def decide(*, load_forecast: float, cpu_pressure: float, temp_c: float,
           on_ac: bool, battery_pct: float | None) -> tuple[str, str]:
    """Pure policy function -> (profile, reason)."""
    if temp_c >= 88:
        return "power-saver", f"thermal protection ({temp_c:.0f} C)"
    if not on_ac:
        if battery_pct is not None and battery_pct < 25:
            return "power-saver", f"battery low ({battery_pct:.0f}%)"
        return "balanced", "on battery"
    if temp_c >= 80:
        return "balanced", f"running hot ({temp_c:.0f} C)"
    if load_forecast >= 0.60 or cpu_pressure >= 20:
        return "performance", f"high demand forecast ({load_forecast:.0%})"
    return "balanced", "light load"


# ------------------------------------------------------------------ probes ---

def cpu_times() -> tuple[int, int]:
    parts = read("/proc/stat").split("\n")[0].split()[1:]
    vals = [int(v) for v in parts] or [0] * 5
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return sum(vals), idle


def meminfo() -> dict:
    out = {}
    for line in read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        out[k] = int(v.split()[0]) if v.split() else 0
    return out


def pressure(kind: str) -> float:
    for line in read(f"/proc/pressure/{kind}").splitlines():
        if line.startswith("some"):
            for tok in line.split():
                if tok.startswith("avg10="):
                    return float(tok[6:])
    return 0.0


def temperature() -> float:
    best = 0.0
    for f in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            best = max(best, int(read(f, "0")) / 1000)
        except ValueError:
            pass
    return best


def power() -> tuple[bool, float | None]:
    on_ac, battery, has_battery = False, None, False
    for d in glob.glob("/sys/class/power_supply/*"):
        kind = read(d + "/type")
        if kind == "Mains" and read(d + "/online") == "1":
            on_ac = True
        elif kind == "Battery" and read(d + "/scope") != "Device":
            has_battery = True
            try:
                battery = float(read(d + "/capacity", "0"))
            except ValueError:
                pass
    return (on_ac or not has_battery), battery


def inventory() -> dict:
    cpuinfo = read("/proc/cpuinfo")
    model, flags = "", set()
    for line in cpuinfo.splitlines():
        k, _, v = line.partition(":")
        k = k.strip()
        if k in ("model name", "Model", "cpu model") and not model:
            model = v.strip()
        elif k in ("flags", "Features") and not flags:
            flags = set(v.split())
    gpus = []
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]")):
        drv = os.path.basename(os.path.realpath(card + "/device/driver")) \
            if os.path.exists(card + "/device/driver") else ""
        gpus.append({"driver": drv, "vendor": read(card + "/device/vendor")})
    disks = []
    for blk in sorted(glob.glob("/sys/block/*")):
        name = os.path.basename(blk)
        if name.startswith(("loop", "ram", "zram", "dm-", "sr")):
            continue
        disks.append({"name": name, "rotational": read(blk + "/queue/rotational") == "1",
                      "nvme": name.startswith("nvme")})
    mem_kb = meminfo().get("MemTotal", 0)
    inv = {"cpu": model or os.uname().machine, "cores": os.cpu_count() or 1,
           "arch": os.uname().machine, "ram_mb": mem_kb // 1024,
           "aes": bool(flags & {"aes"}), "avx2": "avx2" in flags,
           "virtualization": bool(flags & {"vmx", "svm"}) or os.path.exists("/dev/kvm"),
           "tpm": os.path.exists("/sys/class/tpm/tpm0"),
           "secure_boot": _secure_boot(),
           "hypervisor": sh(["systemd-detect-virt"]).stdout.strip() or "none",
           "gpus": gpus, "disks": disks}
    drivers = {g["driver"] for g in gpus} or {""}
    inv["effects"] = "reduced" if (inv["ram_mb"] < 3500 or drivers <= SOFTWARE_GPU) else "full"
    return inv


def _secure_boot() -> bool:
    for f in glob.glob("/sys/firmware/efi/efivars/SecureBoot-*"):
        try:
            return Path(f).read_bytes()[-1] == 1
        except OSError:
            pass
    return False


# ----------------------------------------------------------------- actions ---

def apply_profile(profile: str) -> bool:
    if have("powerprofilesctl"):
        if sh(["powerprofilesctl", "set", profile]).returncode == 0:
            return True
        if profile == "performance":  # not offered on every platform
            return sh(["powerprofilesctl", "set", "balanced"]).returncode == 0
    governor = {"performance": "performance", "power-saver": "powersave"}.get(profile, "schedutil")
    ok = False
    for g in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor"):
        avail = read(os.path.dirname(g) + "/scaling_available_governors").split()
        choice = governor if governor in avail else ("powersave" if "powersave" in avail else "")
        if choice:
            try:
                Path(g).write_text(choice)
                ok = True
            except OSError:
                pass
    return ok


def tune_io(disks: list) -> dict:
    chosen = {}
    for d in disks:
        path = f"/sys/block/{d['name']}/queue/scheduler"
        avail = read(path).replace("[", "").replace("]", "").split()
        want = "none" if d["nvme"] else ("bfq" if d["rotational"] else "mq-deadline")
        if want in avail:
            try:
                Path(path).write_text(want)
                chosen[d["name"]] = want
            except OSError:
                pass
    return chosen


def tune_vm(ram_mb: int) -> None:
    zram = bool(glob.glob("/sys/block/zram*"))
    settings = {"vm/swappiness": "100" if zram else "20",
                "vm/page-cluster": "0" if zram else "3",
                "vm/vfs_cache_pressure": "50" if ram_mb >= 8000 else "100",
                "vm/dirty_background_ratio": "5", "vm/dirty_ratio": "15"}
    for key, val in settings.items():
        try:
            Path("/proc/sys/" + key).write_text(val)
        except OSError:
            pass


def daemon() -> None:
    inv = inventory()
    io = tune_io(inv["disks"])
    tune_vm(inv["ram_mb"])
    forecast = Holt()
    switch = Hysteresis("balanced")
    applied = None
    last_total, last_idle = cpu_times()
    known_disks = {d["name"] for d in inv["disks"]}
    while True:
        time.sleep(INTERVAL)
        total, idle = cpu_times()
        dt = max(1, total - last_total)
        load = max(0.0, min(1.0, 1 - (idle - last_idle) / dt))
        last_total, last_idle = total, idle
        forecast.update(load)
        temp = temperature()
        on_ac, batt = power()
        mem = meminfo()
        wanted, reason = decide(load_forecast=forecast.forecast(), cpu_pressure=pressure("cpu"),
                                temp_c=temp, on_ac=on_ac, battery_pct=batt)
        profile = switch.propose(wanted)
        if profile != applied and apply_profile(profile):
            applied = profile
        now_disks = {os.path.basename(b) for b in glob.glob("/sys/block/*")
                     if not os.path.basename(b).startswith(("loop", "ram", "zram", "dm-", "sr"))}
        if now_disks != known_disks:  # hot-plugged storage
            inv = inventory()
            io = tune_io(inv["disks"])
            known_disks = now_disks
        write_json_atomic(STATE, {
            "time": int(time.time()), "inventory": inv, "io_schedulers": io,
            "cpu_load": round(load, 3), "cpu_forecast": round(max(0.0, forecast.forecast()), 3),
            "temp_c": round(temp, 1), "on_ac": on_ac, "battery_pct": batt,
            "mem_total_mb": mem.get("MemTotal", 0) // 1024,
            "mem_available_mb": mem.get("MemAvailable", 0) // 1024,
            "pressure": {k: pressure(k) for k in ("cpu", "memory", "io")},
            "profile": applied, "profile_reason": reason})
