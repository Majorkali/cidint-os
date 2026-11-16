"""Wi-Fi through iwd's D-Bus interface (net.connman.iwd). Used by
Settings and by the cidint-wifi command. The caller must be root or in
the group netdev (iwd's own D-Bus policy). Passwords are handed to iwd
through a short-lived agent and are not stored by this module; iwd keeps
known networks in /var/lib/iwd (on the data volume in CIDINT OS)."""
import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS = "net.connman.iwd"
AGENT_PATH = "/org/cidint/wifi/agent"
AGENT_XML = """
<node>
  <interface name="net.connman.iwd.Agent">
    <method name="Release"/>
    <method name="RequestPassphrase">
      <arg type="o" direction="in"/>
      <arg type="s" direction="out"/>
    </method>
    <method name="RequestPrivateKeyPassphrase">
      <arg type="o" direction="in"/>
      <arg type="s" direction="out"/>
    </method>
    <method name="RequestUserNameAndPassword">
      <arg type="o" direction="in"/>
      <arg type="s" direction="out"/>
      <arg type="s" direction="out"/>
    </method>
    <method name="RequestUserPassword">
      <arg type="o" direction="in"/>
      <arg type="s" direction="in"/>
      <arg type="s" direction="out"/>
    </method>
    <method name="Cancel">
      <arg type="s" direction="in"/>
    </method>
  </interface>
</node>
"""


class WifiError(Exception):
    pass


def _bus():
    return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)


def _call(path, iface, method, args=None, timeout=30000, reply="()"):
    try:
        return _bus().call_sync(BUS, path, iface, method, args, GLib.VariantType(reply) if reply else None,
                                Gio.DBusCallFlags.NONE, timeout, None)
    except GLib.Error as e:
        msg = e.message.split(":", 2)[-1].strip()
        raise WifiError(msg or e.message) from e


def objects() -> dict:
    v = _call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", reply="(a{oa{sa{sv}}})")
    return v.unpack()[0]


def available() -> bool:
    try:
        objects()
        return True
    except WifiError:
        return False


def devices() -> list:
    """[{path, name, powered, mode, state, connected}] for each Wi-Fi device."""
    objs = objects()
    out = []
    for path, ifaces in objs.items():
        dev = ifaces.get("net.connman.iwd.Device")
        if not dev:
            continue
        st = ifaces.get("net.connman.iwd.Station", {})
        connected = ""
        net = st.get("ConnectedNetwork")
        if net and net in objs:
            connected = objs[net].get("net.connman.iwd.Network", {}).get("Name", "")
        out.append({"path": path, "name": dev.get("Name", ""), "powered": dev.get("Powered", False),
                    "mode": dev.get("Mode", ""), "state": st.get("State", "") if st else "",
                    "connected": connected, "station": bool(st)})
    return sorted(out, key=lambda d: d["name"])


def scan(device_path: str):
    try:
        _call(device_path, "net.connman.iwd.Station", "Scan")
    except WifiError as e:
        if "busy" not in str(e).lower() and "progress" not in str(e).lower():
            raise


def networks(device_path: str) -> list:
    """[{path, name, security, signal (dBm), connected, known}] strongest first."""
    v = _call(device_path, "net.connman.iwd.Station", "GetOrderedNetworks", reply="(a(on))")
    objs = objects()
    out = []
    for path, strength in v.unpack()[0]:
        n = objs.get(path, {}).get("net.connman.iwd.Network", {})
        out.append({"path": path, "name": n.get("Name", "?"), "security": n.get("Type", ""),
                    "signal": strength / 100, "connected": n.get("Connected", False),
                    "known": "KnownNetwork" in n})
    return out


def connect(network_path: str, passphrase: str = "", timeout_ms: int = 60000):
    """Connect; the passphrase is given to iwd only if it asks for it."""
    bus = _bus()
    node = Gio.DBusNodeInfo.new_for_xml(AGENT_XML)
    # A private main context: works from the command line and from a
    # worker thread of a GTK program alike.
    ctx = GLib.MainContext.new()
    ctx.push_thread_default()

    def on_call(_conn, _sender, _path, _iface, method, _params, invocation):
        if method == "RequestPassphrase":
            if passphrase:
                invocation.return_value(GLib.Variant("(s)", (passphrase,)))
            else:
                invocation.return_dbus_error("net.connman.iwd.Agent.Error.Canceled", "no passphrase given")
        elif method in ("Release", "Cancel"):
            invocation.return_value(None)
        else:
            invocation.return_dbus_error("net.connman.iwd.Agent.Error.Canceled", "not supported")

    reg = bus.register_object(AGENT_PATH, node.interfaces[0], on_call, None, None)
    try:
        _call("/net/connman/iwd", "net.connman.iwd.AgentManager", "RegisterAgent",
              GLib.Variant("(o)", (AGENT_PATH,)))
        # The agent answers on this thread's own context while we wait.
        loop = GLib.MainLoop.new(ctx, False)
        result = {}

        def done(conn, res):
            try:
                conn.call_finish(res)
                result["ok"] = True
            except GLib.Error as e:
                result["error"] = e.message.split(":", 2)[-1].strip() or e.message
            loop.quit()

        bus.call(BUS, network_path, "net.connman.iwd.Network", "Connect", None, None,
                 Gio.DBusCallFlags.NONE, timeout_ms, None, done)
        loop.run()
        if "error" in result:
            raise WifiError(result["error"])
    finally:
        try:
            _call("/net/connman/iwd", "net.connman.iwd.AgentManager", "UnregisterAgent",
                  GLib.Variant("(o)", (AGENT_PATH,)))
        except WifiError:
            pass
        bus.unregister_object(reg)
        ctx.pop_thread_default()


def disconnect(device_path: str):
    _call(device_path, "net.connman.iwd.Station", "Disconnect")


def forget(network_path: str):
    objs = objects()
    known = objs.get(network_path, {}).get("net.connman.iwd.Network", {}).get("KnownNetwork")
    if not known:
        raise WifiError("this network is not saved")
    _call(known, "net.connman.iwd.KnownNetwork", "Forget")
