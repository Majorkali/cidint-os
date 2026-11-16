"""Shared look of the CIDINT desktop programs (GTK 3 CSS)."""
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

CSS = b"""
* { font-family: "Liberation Sans", sans-serif; }
window, .cidint-bg { background-color: #0b1626; color: #e6eef7; }
.cidint-greeter { background-image: linear-gradient(160deg, #0b1f33 0%, #0a1626 55%, #06303a 100%); }
.cidint-card { background-color: rgba(16, 32, 52, 0.92); border-radius: 14px;
               border: 1px solid rgba(43, 212, 224, 0.35); padding: 28px; }
.cidint-title { font-size: 30px; font-weight: bold; color: #f2f7fc; letter-spacing: 2px; }
.cidint-sub { font-size: 13px; color: #8fb4cc; }
.cidint-clock { font-size: 56px; font-weight: 300; color: #f2f7fc; }
.cidint-date { font-size: 16px; color: #a9c4d8; }
.cidint-error { color: #ff8a80; }
.cidint-ok { color: #7ee2a8; }
.cidint-muted { color: #8fa3b8; }
.cidint-h1 { font-size: 20px; font-weight: bold; color: #f2f7fc; }
.cidint-key { color: #8fb4cc; }
entry { background-color: #0f2238; color: #e6eef7; border: 1px solid #24476a;
        border-radius: 8px; padding: 8px 10px; caret-color: #2bd4e0; }
entry:focus { border-color: #2bd4e0; }
button { background-image: none; background-color: #163150; color: #e6eef7;
         border: 1px solid #24476a; border-radius: 8px; padding: 6px 14px; }
button:hover { background-color: #1d3e63; }
button.suggested-action { background-color: #13a3b0; border-color: #2bd4e0; color: #021018; font-weight: bold; }
button.suggested-action:hover { background-color: #2bd4e0; }
button.destructive-action { background-color: #6b1f2a; border-color: #b33a4b; }
headerbar { background-image: none; background-color: #0e1d31; border-bottom: 1px solid #1e3a5a; color: #e6eef7; }
headerbar .title { color: #e6eef7; font-weight: bold; }
treeview.view { background-color: #0d1b2d; color: #e6eef7; }
treeview.view:selected { background-color: #13a3b0; color: #021018; }
treeview.view header button { background-color: #10243b; color: #a9c4d8; border-radius: 0; }
list, list row { background-color: #0d1b2d; color: #e6eef7; }
list row:selected { background-color: #163a5c; }
stacksidebar list { background-color: #0a1626; }
textview, textview text { background-color: #0d1b2d; color: #e6eef7; }
scrolledwindow { background-color: #0d1b2d; }
"""


def apply():
    settings = Gtk.Settings.get_default()
    if settings is not None:
        settings.set_property("gtk-application-prefer-dark-theme", True)
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    screen = Gdk.Screen.get_default()
    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def label(text: str = "", css: str = "", xalign: float = 0.0, wrap: bool = False, selectable: bool = False):
    lab = Gtk.Label(label=text)
    lab.set_xalign(xalign)
    lab.set_line_wrap(wrap)
    lab.set_selectable(selectable)
    if css:
        for c in css.split():
            lab.get_style_context().add_class(c)
    return lab


def run_async(func, done):
    """Run func() in a thread; call done(result, error) in the GTK thread."""
    import threading

    from gi.repository import GLib

    def work():
        try:
            res, err = func(), None
        except Exception as e:  # shown to the user, not swallowed
            res, err = None, e
        GLib.idle_add(lambda: (done(res, err), False)[1])

    threading.Thread(target=work, daemon=True).start()
