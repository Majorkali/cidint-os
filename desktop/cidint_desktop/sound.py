"""Sound through the PipeWire sound server (controlled with WirePlumber's
wpctl and PipeWire's pw-dump). Each desktop session runs its own PipeWire;
programs reach it through ALSA's "pipewire" device (~/.asoundrc of the
session user), so every program that plays sound appears as a stream with
its own volume. Bluetooth headphones and speakers appear as outputs once
connected. Without a running sound server, available() is False and the
callers fall back to plain ALSA."""
import json
import re
import subprocess

DEFAULT_SINK = "@DEFAULT_AUDIO_SINK@"


class SoundError(Exception):
    pass


def _run(*args, timeout=15) -> str:
    try:
        r = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SoundError(f"{args[0]}: {e}") from e
    if r.returncode != 0:
        raise SoundError((r.stderr or r.stdout).strip() or f"{args[0]} failed")
    return r.stdout


def available() -> bool:
    try:
        _run("wpctl", "status", timeout=5)
        return True
    except SoundError:
        return False


def _nodes() -> list:
    try:
        data = json.loads(_run("pw-dump", timeout=10) or "[]")
    except ValueError as e:
        raise SoundError("pw-dump gave no valid JSON") from e
    return [o for o in data if o.get("type") == "PipeWire:Interface:Node"]


def default_sink_name() -> str:
    try:
        out = _run("wpctl", "inspect", DEFAULT_SINK)
    except SoundError:
        return ""
    m = re.search(r'node\.name = "([^"]+)"', out)
    return m.group(1) if m else ""


def get_volume(target) -> tuple:
    """(volume 0.0-1.5, muted) of a node id or @DEFAULT_AUDIO_SINK@."""
    out = _run("wpctl", "get-volume", str(target))
    m = re.search(r"Volume:\s*([0-9.]+)", out)
    return (float(m.group(1)) if m else 0.0, "[MUTED]" in out)


def set_volume(target, volume: float):
    _run("wpctl", "set-volume", str(target), f"{max(0.0, min(1.0, volume)):.2f}")


def set_mute(target, muted: bool):
    _run("wpctl", "set-mute", str(target), "1" if muted else "0")


def set_default(node_id: int):
    _run("wpctl", "set-default", str(node_id))


def outputs() -> list:
    """[{id, name, description, default, bluetooth}] for each output device."""
    default = default_sink_name()
    out = []
    for n in _nodes():
        p = n.get("info", {}).get("props", {})
        if p.get("media.class") != "Audio/Sink":
            continue
        out.append({"id": n["id"], "name": p.get("node.name", ""),
                    "description": p.get("node.description") or p.get("node.nick") or p.get("node.name", ""),
                    "default": p.get("node.name") == default,
                    "bluetooth": str(p.get("device.api", "")).startswith("bluez")
                    or str(p.get("node.name", "")).startswith("bluez")})
    return out


def streams() -> list:
    """[{id, app, title}] for each program currently playing sound."""
    out = []
    for n in _nodes():
        p = n.get("info", {}).get("props", {})
        if p.get("media.class") != "Stream/Output/Audio":
            continue
        out.append({"id": n["id"],
                    "app": p.get("application.name") or p.get("application.process.binary") or "program",
                    "title": p.get("media.name", "")})
    return out
