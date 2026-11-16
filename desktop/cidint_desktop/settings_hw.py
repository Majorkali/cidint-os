"""Settings pages for Wi-Fi, sound and display (imported by
cidint-settings)."""
import os
import re
import subprocess
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from cidint_desktop import theme  # noqa: E402

RENDERER = os.path.expanduser("~/.config/cidint/renderer")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _cmd(*args) -> str:
    try:
        r = subprocess.run(list(args), capture_output=True, text=True, timeout=20)
        return (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"({args[0]} not available: {e})"


def wifi_page(page, grid):
    from cidint_desktop import wifi
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    status = theme.label("", "", wrap=True)
    listbox = Gtk.ListBox()
    state = {"dev": None}

    def set_status(text, css=""):
        ctx = status.get_style_context()
        for c in ("cidint-ok", "cidint-error"):
            ctx.remove_class(c)
        if css:
            ctx.add_class(css)
        status.set_text(text)

    def fill(nets):
        for c in listbox.get_children():
            listbox.remove(c)
        for n in nets:
            row = Gtk.Box(spacing=12, margin=6)
            bars = "▂▄▆█"[: max(1, min(4, int((n["signal"] + 90) / 12)))]
            row.pack_start(theme.label(f"{bars:4}  {n['name']}", "cidint-ok" if n["connected"] else ""),
                           True, True, 0)
            row.pack_start(theme.label(("connected · " if n["connected"] else "")
                                       + ("saved · " if n["known"] else "") + (n["security"] or "open"),
                                       "cidint-muted"), False, False, 0)
            btn = Gtk.Button(label="Disconnect" if n["connected"] else "Connect")
            btn.connect("clicked", on_connect, n)
            row.pack_start(btn, False, False, 0)
            if n["known"]:
                f = Gtk.Button(label="Forget")
                f.connect("clicked", on_forget, n)
                row.pack_start(f, False, False, 0)
            listbox.add(row)
        listbox.show_all()

    def refresh(*_):
        def work():
            devs = [d for d in wifi.devices() if d["station"]]
            if not devs:
                return None, []
            wifi.scan(devs[0]["path"])
            time.sleep(4)
            return devs[0], wifi.networks(devs[0]["path"])

        def done(res, err):
            if err:
                set_status(f"Wi-Fi is not available: {err}", "cidint-error")
                return
            dev, nets = res
            state["dev"] = dev
            if not dev:
                set_status("No Wi-Fi device found. The computer may have none, or no driver for it.",
                           "cidint-error")
                return
            set_status(f"Device {dev['name']}: " + (f"connected to {dev['connected']}" if dev["connected"]
                                                     else "not connected"),
                       "cidint-ok" if dev["connected"] else "")
            fill(nets)

        set_status("Searching for networks…")
        theme.run_async(work, done)

    def on_connect(_b, n):
        if n["connected"]:
            theme.run_async(lambda: wifi.disconnect(state["dev"]["path"]), lambda r, e: refresh())
            return
        pw = ""
        if n["security"] in ("psk", "8021x") and not n["known"]:
            d = Gtk.Dialog(title=f"Password for {n['name']}", transient_for=box.get_toplevel(), modal=True)
            d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Connect", Gtk.ResponseType.OK)
            e = Gtk.Entry(visibility=False, activates_default=True)
            d.set_default_response(Gtk.ResponseType.OK)
            d.get_content_area().add(e)
            d.show_all()
            ok = d.run() == Gtk.ResponseType.OK
            pw = e.get_text()
            d.destroy()
            if not ok:
                return
        set_status(f"Connecting to {n['name']}…")
        theme.run_async(lambda: wifi.connect(n["path"], pw),
                        lambda r, e: set_status(f"Could not connect: {e}", "cidint-error") if e else refresh())

    def on_forget(_b, n):
        theme.run_async(lambda: wifi.forget(n["path"]), lambda r, e: refresh())

    again = Gtk.Button(label="Search again")
    again.connect("clicked", refresh)
    box.pack_start(status, False, False, 0)
    box.pack_start(listbox, False, False, 0)
    box.pack_start(again, False, False, 0)
    refresh()
    return page("Wi-Fi", box, theme.label("Saved networks are kept on the data volume and joined "
                                          "automatically. Wired networks connect without setup.",
                                          "cidint-muted", wrap=True))


def mixer_control() -> str:
    names = re.findall(r"'([^']+)'", _cmd("amixer", "scontrols"))
    for pref in ("Master", "PCM", "Speaker", "Headphone"):
        if pref in names:
            return pref
    return names[0] if names else ""


def _volume_row(title, target, sound):
    """A slider and a mute box for one output or program (PipeWire)."""
    row = Gtk.Box(spacing=10)
    try:
        vol, muted = sound.get_volume(target)
    except sound.SoundError:
        vol, muted = 0.0, False
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
    scale.set_value(round(min(vol, 1.0) * 100))
    scale.set_hexpand(True)
    mute = Gtk.CheckButton(label="Mute")
    mute.set_active(muted)

    def apply(*_):
        try:
            sound.set_volume(target, scale.get_value() / 100)
            sound.set_mute(target, mute.get_active())
        except sound.SoundError:
            pass

    scale.connect("value-changed", apply)
    mute.connect("toggled", apply)
    lab = theme.label(title, "cidint-key")
    lab.set_size_request(180, -1)
    row.pack_start(lab, False, False, 0)
    row.pack_start(scale, True, True, 0)
    row.pack_start(mute, False, False, 0)
    return row


def sound_page(page, grid):
    from cidint_desktop import sound
    if not sound.available():
        return alsa_sound_page(page, grid)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

    def fill(*_):
        for c in box.get_children():
            box.remove(c)
        try:
            outs, progs = sound.outputs(), sound.streams()
        except sound.SoundError as e:
            box.pack_start(theme.label(f"The sound server does not answer: {e}", "cidint-error", wrap=True),
                           False, False, 0)
            box.show_all()
            return
        box.pack_start(theme.label("Output", "cidint-key"), False, False, 0)
        if not outs:
            box.pack_start(theme.label("No output device. The computer may have no sound hardware, or no "
                                       "driver for it (see cidint-soundcheck).", "cidint-error", wrap=True),
                           False, False, 0)
        first = None
        for o in outs:
            rb = Gtk.RadioButton.new_with_label_from_widget(
                first, o["description"] + ("  (Bluetooth)" if o["bluetooth"] else ""))
            first = first or rb
            rb.set_active(o["default"])
            rb.connect("toggled", lambda b, i=o["id"]: b.get_active() and _safe(sound.set_default, i))
            box.pack_start(rb, False, False, 0)
        if outs:
            box.pack_start(_volume_row("Volume", sound.DEFAULT_SINK, sound), False, False, 0)
        box.pack_start(theme.label("Programs playing sound", "cidint-key"), False, False, 6)
        if not progs:
            box.pack_start(theme.label("None at the moment.", "cidint-muted"), False, False, 0)
        for p in progs:
            box.pack_start(_volume_row(p["app"], p["id"], sound), False, False, 0)
        test = Gtk.Button(label="Play test sound")
        test.connect("clicked", lambda *_: subprocess.Popen(
            ["pw-play", "/usr/share/cidint/sounds/test-chime.wav"]))
        again = Gtk.Button(label="Refresh")
        again.connect("clicked", fill)
        row = Gtk.Box(spacing=8)
        row.pack_start(test, False, False, 0)
        row.pack_start(again, False, False, 0)
        box.pack_start(row, False, False, 6)
        box.show_all()

    fill()
    return page("Sound", box, theme.label("Each program has its own volume while it plays. Bluetooth "
                                          "headphones appear as an output once connected (Bluetooth page).",
                                          "cidint-muted", wrap=True))


def _safe(func, *args):
    try:
        func(*args)
    except Exception:  # shown again on the next refresh
        pass
    return False


def bluetooth_page(page, grid):
    from gi.repository import GLib

    from cidint_desktop import bluetooth as bt
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    status = theme.label("", "", wrap=True)
    listbox = Gtk.ListBox()
    power = Gtk.CheckButton(label="Bluetooth on")
    state = {"adapter": None, "busy": False}

    def ask_gtk(text):
        """Called on the pairing thread: ask in the GTK thread and wait."""
        import threading
        ev, ans = threading.Event(), {}

        def show():
            d = Gtk.MessageDialog(transient_for=box.get_toplevel(), modal=True,
                                  message_type=Gtk.MessageType.QUESTION,
                                  buttons=Gtk.ButtonsType.YES_NO, text=text)
            ans["ok"] = d.run() == Gtk.ResponseType.YES
            d.destroy()
            ev.set()
            return False

        GLib.idle_add(show)
        ev.wait(120)
        return ans.get("ok", False)

    def ask_pin():
        import threading
        ev, ans = threading.Event(), {}

        def show():
            d = Gtk.Dialog(title="PIN for the device", transient_for=box.get_toplevel(), modal=True)
            d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "OK", Gtk.ResponseType.OK)
            e = Gtk.Entry(activates_default=True)
            d.set_default_response(Gtk.ResponseType.OK)
            d.get_content_area().add(e)
            d.show_all()
            ans["pin"] = e.get_text() if d.run() == Gtk.ResponseType.OK else ""
            d.destroy()
            ev.set()
            return False

        GLib.idle_add(show)
        ev.wait(120)
        return ans.get("pin", "")

    def act(func, *args, then="Done."):
        status.set_text("Working…")
        theme.run_async(lambda: func(*args),
                        lambda r, e: (status.set_text(f"Failed: {e}") if e else status.set_text(then), refresh()))

    def fill(devs):
        for c in listbox.get_children():
            listbox.remove(c)
        for d in devs:
            row = Gtk.Box(spacing=12, margin=6)
            st = "connected" if d["connected"] else ("paired" if d["paired"] else "found")
            row.pack_start(theme.label(d["name"], "cidint-ok" if d["connected"] else ""), True, True, 0)
            row.pack_start(theme.label(f"{st} · {d['kind'] or 'device'}", "cidint-muted"), False, False, 0)
            if not d["paired"]:
                b = Gtk.Button(label="Pair")
                b.connect("clicked", lambda _b, p=d["path"]: act(bt.pair, p, ask_gtk, ask_pin, then="Paired."))
                row.pack_start(b, False, False, 0)
            elif d["connected"]:
                b = Gtk.Button(label="Disconnect")
                b.connect("clicked", lambda _b, p=d["path"]: act(bt.disconnect, p))
                row.pack_start(b, False, False, 0)
            else:
                b = Gtk.Button(label="Connect")
                b.connect("clicked", lambda _b, p=d["path"]: act(bt.connect, p, then="Connected."))
                row.pack_start(b, False, False, 0)
            if d["paired"]:
                f = Gtk.Button(label="Forget")
                f.connect("clicked", lambda _b, p=d["path"]: act(bt.remove, p))
                row.pack_start(f, False, False, 0)
            listbox.add(row)
        listbox.show_all()

    def refresh(*_):
        def work():
            a = bt.adapters()
            return (a[0], bt.devices(a[0]["path"])) if a else (None, [])

        def done(res, err):
            if err:
                status.set_text(f"Bluetooth is not available: {err}")
                return
            a, devs = res
            state["adapter"] = a
            state["busy"] = True
            power.set_active(bool(a and a["powered"]))
            state["busy"] = False
            power.set_sensitive(bool(a))
            if not a:
                status.set_text("No Bluetooth controller found. The computer may have none, no driver for it, "
                                "or a switch turned it off.")
            fill(devs)

        theme.run_async(work, done)

    def on_power(b):
        if not state["busy"] and state["adapter"]:
            act(bt.set_powered, state["adapter"]["path"], b.get_active())

    def on_scan(*_):
        a = state["adapter"]
        if not a:
            return
        status.set_text("Looking for devices for 12 seconds…")

        def work():
            if not a["powered"]:
                bt.set_powered(a["path"], True)
            bt.start_discovery(a["path"])
            import time
            time.sleep(12)
            bt.stop_discovery(a["path"])

        theme.run_async(work, lambda r, e: (status.set_text(f"Search failed: {e}" if e else ""), refresh()))

    power.connect("toggled", on_power)
    scan = Gtk.Button(label="Search for devices")
    scan.connect("clicked", on_scan)
    box.pack_start(power, False, False, 0)
    box.pack_start(status, False, False, 0)
    box.pack_start(listbox, False, False, 0)
    box.pack_start(scan, False, False, 0)
    refresh()
    return page("Bluetooth", box, theme.label("Put the device in pairing mode, search, then Pair. Compare the "
                                              "code it shows before you confirm.", "cidint-muted", wrap=True))


def alsa_sound_page(page, grid):
    cards = [line.strip() for line in _read("/proc/asound/cards").splitlines() if re.match(r"\s*\d+ \[", line)]
    if not cards:
        return page("Sound", theme.label("No sound device found. The computer may have none, or no driver "
                                         "for it.", "cidint-error", wrap=True))
    ctl = mixer_control()
    info = _cmd("amixer", "sget", ctl) if ctl else ""
    m = re.search(r"\[(\d+)%\]", info)
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
    scale.set_value(int(m.group(1)) if m else 70)
    scale.set_hexpand(True)
    mute = Gtk.CheckButton(label="Muted")
    mute.set_active("[off]" in info)
    note = theme.label("", "cidint-muted", wrap=True)

    def apply(*_):
        if ctl:
            note.set_text(_cmd("amixer", "-q", "sset", ctl, f"{int(scale.get_value())}%",
                               "mute" if mute.get_active() else "unmute"))

    scale.connect("value-changed", apply)
    mute.connect("toggled", apply)
    test = Gtk.Button(label="Play test sound")
    test.connect("clicked", lambda *_: subprocess.Popen(["aplay", "-q", "/usr/share/cidint/sounds/test-chime.wav"]))
    row = Gtk.Box(spacing=10)
    row.pack_start(theme.label("Volume", "cidint-key"), False, False, 0)
    row.pack_start(scale, True, True, 0)
    return page("Sound", grid([("Devices", "\n".join(cards)), ("Volume control", ctl or "none")]),
                row, mute, test, note)


def display_page(page, grid):
    drm = "/sys/class/drm"
    names = sorted(os.listdir(drm)) if os.path.isdir(drm) else []
    drivers = [f"{c}: {os.path.basename(os.path.realpath(f'{drm}/{c}/device/driver'))}"
               for c in names if c.startswith("card") and "-" not in c]
    outputs = [f"{c.split('-', 1)[1]}: {_read(f'{drm}/{c}/status').strip()}" for c in names if "-" in c]
    in_use = _read(os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run"), "cidint-renderer")).strip() or "unknown"
    choice = _read(RENDERER).strip() or "auto"
    radios, first = {}, None
    for key, text in (("auto", "Automatic: hardware acceleration when a supported graphics driver is loaded"),
                      ("gl", "Always OpenGL (hardware, or software OpenGL without a driver)"),
                      ("pixman", "Always software rendering (most compatible)")):
        rb = Gtk.RadioButton.new_with_label_from_widget(first, text)
        first = first or rb
        radios[key] = rb
    radios.get(choice, radios["auto"]).set_active(True)
    note = theme.label("", "cidint-ok")

    def save(rb, key):
        if rb.get_active():
            os.makedirs(os.path.dirname(RENDERER), exist_ok=True)
            with open(RENDERER, "w", encoding="utf-8") as f:
                f.write(key + "\n")
            note.set_text("Saved. It applies at your next login.")

    for key, rb in radios.items():
        rb.connect("toggled", save, key)
    return page("Display", grid([("Graphics", "\n".join(drivers) or "none"),
                                 ("Outputs", "\n".join(outputs) or "none"),
                                 ("Rendering now", in_use)]),
                theme.label("Rendering", "cidint-key"), *radios.values(), note)
