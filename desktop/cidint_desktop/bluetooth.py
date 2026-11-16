"""Bluetooth through BlueZ's D-Bus interface (org.bluez). Used by Settings
and by the cidint-bluetooth command. The caller must be root or in the
group bluetooth (CIDINT's D-Bus policy). Pairing goes through a short-lived
agent that shows the six-digit code (or asks for a PIN) and needs the
user's confirmation; nothing here pairs on its own. Audio devices
(headphones, speakers) appear in Sound once connected: the sound server
(PipeWire with WirePlumber) handles them."""
import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS = "org.bluez"
AGENT_PATH = "/org/cidint/bluetooth/agent"
AGENT_XML = """
<node>
  <interface name="org.bluez.Agent1">
    <method name="Release"/>
    <method name="RequestPinCode">
      <arg type="o" direction="in"/>
      <arg type="s" direction="out"/>
    </method>
    <method name="DisplayPinCode">
      <arg type="o" direction="in"/>
      <arg type="s" direction="in"/>
    </method>
    <method name="RequestPasskey">
      <arg type="o" direction="in"/>
      <arg type="u" direction="out"/>
    </method>
    <method name="DisplayPasskey">
      <arg type="o" direction="in"/>
      <arg type="u" direction="in"/>
      <arg type="q" direction="in"/>
    </method>
    <method name="RequestConfirmation">
      <arg type="o" direction="in"/>
      <arg type="u" direction="in"/>
    </method>
    <method name="RequestAuthorization">
      <arg type="o" direction="in"/>
    </method>
    <method name="AuthorizeService">
      <arg type="o" direction="in"/>
      <arg type="s" direction="in"/>
    </method>
    <method name="Cancel"/>
  </interface>
</node>
"""


class BluetoothError(Exception):
    pass


def _bus():
    return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)


def _call(path, iface, method, args=None, timeout=30000, reply="()"):
    try:
        return _bus().call_sync(BUS, path, iface, method, args, GLib.VariantType(reply) if reply else None,
                                Gio.DBusCallFlags.NONE, timeout, None)
    except GLib.Error as e:
        msg = e.message.split(":", 2)[-1].strip()
        raise BluetoothError(msg or e.message) from e


def _set(path, iface, prop, value: GLib.Variant):
    _call(path, "org.freedesktop.DBus.Properties", "Set", GLib.Variant("(ssv)", (iface, prop, value)))


def objects() -> dict:
    v = _call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", reply="(a{oa{sa{sv}}})")
    return v.unpack()[0]


def adapters() -> list:
    """[{path, name, address, powered, discovering}] for each controller."""
    out = []
    for path, ifaces in objects().items():
        a = ifaces.get("org.bluez.Adapter1")
        if a:
            out.append({"path": path, "name": a.get("Alias") or a.get("Name", ""), "address": a.get("Address", ""),
                        "powered": a.get("Powered", False), "discovering": a.get("Discovering", False)})
    return sorted(out, key=lambda a: a["path"])


def devices(adapter_path: str = "") -> list:
    """[{path, name, address, kind, paired, connected, trusted, rssi}]."""
    out = []
    for path, ifaces in objects().items():
        d = ifaces.get("org.bluez.Device1")
        if not d or (adapter_path and d.get("Adapter") != adapter_path):
            continue
        out.append({"path": path, "name": d.get("Alias") or d.get("Name") or d.get("Address", "?"),
                    "address": d.get("Address", ""), "kind": d.get("Icon", ""),
                    "paired": d.get("Paired", False), "connected": d.get("Connected", False),
                    "trusted": d.get("Trusted", False), "rssi": d.get("RSSI")})
    return sorted(out, key=lambda d: (not d["connected"], not d["paired"], d["name"].lower()))


def set_powered(adapter_path: str, on: bool):
    _set(adapter_path, "org.bluez.Adapter1", "Powered", GLib.Variant("b", on))


def start_discovery(adapter_path: str):
    try:
        _call(adapter_path, "org.bluez.Adapter1", "StartDiscovery")
    except BluetoothError as e:
        if "progress" not in str(e).lower():
            raise


def stop_discovery(adapter_path: str):
    try:
        _call(adapter_path, "org.bluez.Adapter1", "StopDiscovery")
    except BluetoothError:
        pass


def pair(device_path: str, confirm, pin=None, timeout_ms: int = 60000):
    """Pair with a device. confirm(text) -> bool is asked whenever the
    other side shows a code; pin() -> str when a PIN must be typed."""
    bus = _bus()
    node = Gio.DBusNodeInfo.new_for_xml(AGENT_XML)
    ctx = GLib.MainContext.new()
    ctx.push_thread_default()

    def on_call(_conn, _sender, _path, _iface, method, params, invocation):
        reject = ("org.bluez.Error.Rejected", "refused by the user")
        try:
            if method == "RequestConfirmation":
                code = params.unpack()[1]
                if confirm(f"Does the device show the code {code:06d}?"):
                    invocation.return_value(None)
                else:
                    invocation.return_dbus_error(*reject)
            elif method == "RequestAuthorization":
                if confirm("Allow pairing with this device?"):
                    invocation.return_value(None)
                else:
                    invocation.return_dbus_error(*reject)
            elif method == "RequestPinCode":
                p = pin() if pin else ""
                if p:
                    invocation.return_value(GLib.Variant("(s)", (p,)))
                else:
                    invocation.return_dbus_error(*reject)
            elif method == "RequestPasskey":
                p = pin() if pin else ""
                if p.isdigit():
                    invocation.return_value(GLib.Variant("(u)", (int(p),)))
                else:
                    invocation.return_dbus_error(*reject)
            elif method in ("DisplayPinCode", "DisplayPasskey"):
                code = params.unpack()[1]
                confirm(f"Type {code} on the device, then press Enter there.")
                invocation.return_value(None)
            elif method == "AuthorizeService":
                invocation.return_value(None)  # services of a device the user paired
            else:  # Release, Cancel
                invocation.return_value(None)
        except Exception as e:  # never leave BlueZ waiting
            invocation.return_dbus_error("org.bluez.Error.Rejected", str(e))

    reg = bus.register_object(AGENT_PATH, node.interfaces[0], on_call, None, None)
    try:
        _call("/org/bluez", "org.bluez.AgentManager1", "RegisterAgent",
              GLib.Variant("(os)", (AGENT_PATH, "KeyboardDisplay")))
        loop = GLib.MainLoop.new(ctx, False)
        result = {}

        def done(conn, res):
            try:
                conn.call_finish(res)
            except GLib.Error as e:
                result["error"] = e.message.split(":", 2)[-1].strip() or e.message
            loop.quit()

        bus.call(BUS, device_path, "org.bluez.Device1", "Pair", None, None,
                 Gio.DBusCallFlags.NONE, timeout_ms, None, done)
        loop.run()
        if "error" in result and "AlreadyExists" not in result["error"]:
            raise BluetoothError(result["error"])
        # A paired device the user chose may reconnect by itself later.
        _set(device_path, "org.bluez.Device1", "Trusted", GLib.Variant("b", True))
    finally:
        try:
            _call("/org/bluez", "org.bluez.AgentManager1", "UnregisterAgent", GLib.Variant("(o)", (AGENT_PATH,)))
        except BluetoothError:
            pass
        bus.unregister_object(reg)
        ctx.pop_thread_default()


def connect(device_path: str):
    _call(device_path, "org.bluez.Device1", "Connect", timeout=60000)


def disconnect(device_path: str):
    _call(device_path, "org.bluez.Device1", "Disconnect")


def remove(device_path: str):
    """Forget a device (removes its pairing)."""
    adapter = objects().get(device_path, {}).get("org.bluez.Device1", {}).get("Adapter")
    if not adapter:
        raise BluetoothError("unknown device")
    _call(adapter, "org.bluez.Adapter1", "RemoveDevice", GLib.Variant("(o)", (device_path,)))
