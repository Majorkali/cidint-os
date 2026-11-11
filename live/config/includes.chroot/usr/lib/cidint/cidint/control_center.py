"""CIDINT Control Center: security dashboard, live monitoring, domains, apps, assistant."""
from __future__ import annotations

import sys
from collections import deque

from PyQt6.QtCore import QProcess, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QMainWindow, QPlainTextEdit, QPushButton,
                             QStackedWidget, QVBoxLayout, QWidget)

from . import CLI, VERSION, assistant, domains, hwadapt, read_json, sentinel, uaee
from .cli import posture

ACCENT, OK, BAD, WARN = "#35e0c8", "#3ddc97", "#ff5c7a", "#ffc857"
STYLE = f"""
* {{ font-family: 'Inter', 'Noto Sans', sans-serif; font-size: 10.5pt; color: #dfe9f5; }}
QMainWindow, QWidget#root {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
    stop:0 #070b14, stop:0.55 #0b1322, stop:1 #101a30); }}
QFrame#card {{ background: rgba(255,255,255,0.055); border: 1px solid rgba(255,255,255,0.10);
    border-radius: 14px; }}
QFrame#side {{ background: rgba(0,0,0,0.28); border-right: 1px solid rgba(255,255,255,0.07); }}
QLabel#h1 {{ font-size: 19pt; font-weight: 600; }}
QLabel#h2 {{ font-size: 11pt; font-weight: 600; color: #8fa3bd; }}
QLabel#big {{ font-size: 26pt; font-weight: 700; }}
QLabel#dim {{ color: #8fa3bd; }}
QPushButton {{ background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.12);
    border-radius: 9px; padding: 8px 14px; }}
QPushButton:hover {{ background: rgba(53,224,200,0.16); border-color: {ACCENT}; }}
QPushButton#nav {{ text-align: left; border: none; background: transparent; padding: 11px 16px;
    border-radius: 9px; }}
QPushButton#nav:checked {{ background: rgba(53,224,200,0.14); color: {ACCENT}; font-weight: 600; }}
QPushButton#primary {{ background: {ACCENT}; color: #06201c; font-weight: 600; border: none; }}
QPushButton#danger {{ background: rgba(255,92,122,0.18); border-color: {BAD}; color: #ffd5dc; }}
QLineEdit, QPlainTextEdit, QListWidget {{ background: rgba(0,0,0,0.30);
    border: 1px solid rgba(255,255,255,0.10); border-radius: 9px; padding: 7px; }}
QPlainTextEdit {{ font-family: 'JetBrains Mono', 'DejaVu Sans Mono', monospace; font-size: 9.5pt; }}
"""


def card(*widgets, title: str = "") -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 14, 18, 16)
    if title:
        t = QLabel(title.upper())
        t.setObjectName("h2")
        lay.addWidget(t)
    for w in widgets:
        lay.addWidget(w)
    return f


def label(text: str = "", name: str = "") -> QLabel:
    w = QLabel(text)
    w.setWordWrap(True)
    if name:
        w.setObjectName(name)
    return w


class Graph(QWidget):
    """Scrolling area chart for a 0..1 series."""

    def __init__(self, color: str):
        super().__init__()
        self.color = QColor(color)
        self.data = deque([0.0] * 60, maxlen=60)
        self.setMinimumHeight(90)

    def push(self, v: float) -> None:
        self.data.append(max(0.0, min(1.0, v)))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        step = w / (len(self.data) - 1)
        line = QPainterPath()
        for i, v in enumerate(self.data):
            x, y = i * step, h - 3 - v * (h - 8)
            line.moveTo(x, y) if i == 0 else line.lineTo(x, y)
        area = QPainterPath(line)
        area.lineTo(w, h)
        area.lineTo(0, h)
        area.closeSubpath()
        grad = QLinearGradient(0, 0, 0, h)
        top = QColor(self.color)
        top.setAlpha(110)
        bottom = QColor(self.color)
        bottom.setAlpha(0)
        grad.setColorAt(0, top)
        grad.setColorAt(1, bottom)
        p.fillPath(area, grad)
        p.setPen(QPen(self.color, 2))
        p.drawPath(line)


class Center(QMainWindow):
    PAGES = ["Overview", "Network", "Domains", "Applications", "Hardware", "Threats", "Assistant"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CIDINT Control Center")
        self.resize(1080, 700)
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(210)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 20, 12, 16)
        brand = label("CIDINT OS", "h1")
        brand.setStyleSheet(f"color:{ACCENT}; letter-spacing: 2px;")
        sl.addWidget(brand)
        sl.addWidget(label(f"Control Center {VERSION}", "dim"))
        sl.addSpacing(18)
        self.stack = QStackedWidget()
        self.nav = []
        for i, name in enumerate(self.PAGES):
            b = QPushButton(name)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.clicked.connect(lambda _c, i=i: self.go(i))
            sl.addWidget(b)
            self.nav.append(b)
        sl.addStretch()
        panic = QPushButton("Emergency lockdown")
        panic.setObjectName("danger")
        panic.clicked.connect(lambda: self.run_cli(["panic"]))
        sl.addWidget(panic)
        outer.addWidget(side)

        right = QVBoxLayout()
        right.setContentsMargins(24, 22, 24, 18)
        right.addWidget(self.stack, 1)
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(110)
        self.console.setPlaceholderText("Command output appears here")
        right.addWidget(self.console)
        outer.addLayout(right, 1)

        for build in (self.page_overview, self.page_network, self.page_domains, self.page_apps,
                      self.page_hardware, self.page_threats, self.page_assistant):
            self.stack.addWidget(build())
        self.proc = None
        self.go(0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    # -- plumbing -----------------------------------------------------------
    def go(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        for j, b in enumerate(self.nav):
            b.setChecked(i == j)

    def run_cli(self, args: list, detached: bool = False) -> None:
        if detached:
            QProcess.startDetached(CLI, args)
            return
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.console.appendPlainText("(busy: wait for the current command to finish)")
            return
        self.console.appendPlainText("$ cidint " + " ".join(args))
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(lambda: self.console.appendPlainText(
            bytes(self.proc.readAllStandardOutput()).decode(errors="replace").rstrip()))
        self.proc.finished.connect(lambda *_: self.refresh())
        self.proc.start(CLI, args)

    def page(self, title: str, subtitle: str):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 8)
        lay.setSpacing(14)
        lay.addWidget(label(title, "h1"))
        lay.addWidget(label(subtitle, "dim"))
        return w, lay

    # -- pages --------------------------------------------------------------
    def page_overview(self):
        w, lay = self.page("Overview", "Security posture of this session at a glance.")
        grid = QGridLayout()
        grid.setSpacing(14)
        self.ov_score, self.ov_net = label("--", "big"), label("--", "big")
        self.ov_risk, self.ov_profile = label("--", "big"), label("--", "big")
        for i, (t, wdg) in enumerate([("Security posture", self.ov_score), ("Network mode", self.ov_net),
                                      ("Threat risk", self.ov_risk), ("Power profile", self.ov_profile)]):
            grid.addWidget(card(wdg, title=t), 0, i)
        lay.addLayout(grid)
        self.ov_checks = label()
        self.ov_checks.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(card(self.ov_checks, title="Protection checks"))
        lay.addStretch()
        return w

    def page_network(self):
        w, lay = self.page("Network", "Choose how this machine talks to the outside world.")
        self.net_state = label("--", "big")
        lay.addWidget(card(self.net_state, label(
            "Anonymous Mode forces every connection through Tor and blocks anything that cannot use it. "
            "Tor conceals your address from sites and your destinations from your network; it cannot "
            "protect you from every adversary or from identifying yourself.", "dim"), title="Current mode"))
        row = QHBoxLayout()
        for text, args, name in (("Enable Anonymous Mode", ["anon", "on"], "primary"),
                                 ("Disable Anonymous Mode", ["anon", "off"], ""),
                                 ("Kill switch: block all", ["lockdown", "on"], "danger"),
                                 ("Release kill switch", ["lockdown", "off"], "")):
            b = QPushButton(text)
            b.setObjectName(name)
            b.clicked.connect(lambda _c, a=args: self.run_cli(a))
            row.addWidget(b)
        lay.addLayout(row)
        lay.addStretch()
        return w

    def page_domains(self):
        w, lay = self.page("Domains", "Isolated compartments. Each has its own files and network policy.")
        self.dom_list = QListWidget()
        for name, d in domains.load().items():
            self.dom_list.addItem(f"{name}    network: {d.get('network', 'none')}    {d.get('label', '')}")
        self.dom_list.setCurrentRow(0)
        lay.addWidget(card(self.dom_list, title="Persistent domains"))
        row = QHBoxLayout()
        for text, prog in (("Open terminal in domain", "konsole"), ("Open browser in domain", "firefox-esr"),
                           ("Open files in domain", "dolphin")):
            b = QPushButton(text)
            b.clicked.connect(lambda _c, p=prog: self.launch_in_domain(p))
            row.addWidget(b)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        for text, args in (("Disposable browser (Tor)", ["domain", "disposable", "--", "firefox-esr", "--no-remote"]),
                           ("Disposable terminal (Tor)", ["domain", "disposable", "--", "konsole"])):
            b = QPushButton(text)
            b.setObjectName("primary")
            b.clicked.connect(lambda _c, a=args: self.run_cli(a, detached=True))
            row2.addWidget(b)
        lay.addLayout(row2)
        lay.addWidget(label("Disposable compartments live in memory and are destroyed when closed. Compartments "
                            "use kernel namespaces; for a hardware-virtualised boundary boot an image with "
                            "'cidint domain vm'.", "dim"))
        lay.addStretch()
        return w

    def launch_in_domain(self, prog: str) -> None:
        item = self.dom_list.currentItem()
        if item:
            extra = ["--no-remote"] if prog == "firefox-esr" else []
            self.run_cli(["domain", "run", item.text().split()[0], "--", prog, *extra], detached=True)

    def page_apps(self):
        w, lay = self.page("Applications", "Install or run software from any supported ecosystem, isolated.")
        pick = QPushButton("Open a package or program...")
        pick.setObjectName("primary")
        pick.clicked.connect(self.pick_app)
        lay.addWidget(card(pick, label(
            "Supported: DEB, RPM, AppImage, Flatpak, Snap, source archives, shell scripts, Windows EXE / MSI / "
            "BAT (via Wine), Java JAR, Android APK (via Waydroid). macOS and iOS software is identified and "
            "refused: it cannot run outside Apple platforms.", "dim"), title="Universal application engine"))
        self.app_list = QListWidget()
        lay.addWidget(card(self.app_list, title="Installed through UAEE"), 1)
        rm = QPushButton("Remove selected")
        rm.clicked.connect(lambda: self.app_list.currentItem() and self.run_cli(
            ["app", "remove", self.app_list.currentItem().text().split()[0]]))
        lay.addWidget(rm, 0, Qt.AlignmentFlag.AlignLeft)
        return w

    def pick_app(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a file to install or run")
        if path:
            self.run_cli(["app", "open", path], detached=True)

    def page_hardware(self):
        w, lay = self.page("Hardware", "Detected hardware and the adaptive tuning applied to it.")
        row = QHBoxLayout()
        self.g_cpu, self.g_mem = Graph(ACCENT), Graph("#b48cff")
        self.l_cpu, self.l_mem = label("CPU"), label("Memory")
        row.addWidget(card(self.l_cpu, self.g_cpu, title="Processor"))
        row.addWidget(card(self.l_mem, self.g_mem, title="Memory"))
        lay.addLayout(row)
        self.hw_info = label()
        self.hw_info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(card(self.hw_info, title="System"))
        lay.addStretch()
        return w

    def page_threats(self):
        w, lay = self.page("Threats", "Behavioural detections from the Sentinel in the last hour.")
        self.th_list = QListWidget()
        lay.addWidget(card(self.th_list, title="Events"), 1)
        row = QHBoxLayout()
        for text, args in (("Enable containment (freeze critical processes)", ["sentinel", "contain", "on"]),
                           ("Disable containment", ["sentinel", "contain", "off"])):
            b = QPushButton(text)
            b.clicked.connect(lambda _c, a=args: self.run_cli(a))
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        return w

    def page_assistant(self):
        w, lay = self.page("Assistant", "Tell the system what you want in plain language. Runs fully offline.")
        self.ask = QLineEdit()
        self.ask.setPlaceholderText("e.g. turn on anonymous mode  /  why is my system slow  /  show threats")
        self.ask.returnPressed.connect(self.ask_submit)
        self.ask_out = label("", "dim")
        lay.addWidget(card(self.ask, self.ask_out, title="Ask CIDINT"))
        lay.addStretch()
        return w

    def ask_submit(self) -> None:
        hit = assistant.interpret(self.ask.text())
        if not hit:
            self.ask_out.setText("I did not understand that request.")
            return
        explanation, argv, _mutating = hit
        self.ask_out.setText(f"{explanation}\n$ {' '.join(argv)}")
        if argv[0] == CLI:
            self.run_cli(argv[1:])
        else:
            QProcess.startDetached("konsole", ["--hold", "-e", *argv])
        self.ask.clear()

    # -- live data ----------------------------------------------------------
    def refresh(self) -> None:
        p = posture()
        score = p["score"]
        colour = OK if score >= 75 else WARN if score >= 50 else BAD
        self.ov_score.setText(f"<span style='color:{colour}'>{score}</span>/100")
        mode = p["net"]["mode"]
        mode_colour = {"anonymous": ACCENT, "lockdown": BAD}.get(mode, "#dfe9f5")
        self.ov_net.setText(f"<span style='color:{mode_colour}'>{mode.capitalize()}</span>")
        self.net_state.setText(self.ov_net.text())
        risk = p["sentinel"].get("risk", 0)
        self.ov_risk.setText(f"<span style='color:{BAD if risk >= 80 else WARN if risk >= 50 else OK}'>{risk}</span>")
        hw = p["hw"]
        self.ov_profile.setText(str(hw.get("profile") or "--").capitalize())
        self.ov_checks.setText("<br>".join(
            f"<span style='color:{OK if v else BAD}'>{'&#10003;' if v else '&#10007;'}</span>&nbsp;&nbsp;{k}"
            for k, v in p["checks"].items()))
        if hw:
            inv = hw.get("inventory", {})
            total = max(1, hw.get("mem_total_mb", 1))
            used = total - hw.get("mem_available_mb", 0)
            self.g_cpu.push(hw.get("cpu_load", 0))
            self.g_mem.push(used / total)
            self.l_cpu.setText(f"{hw.get('cpu_load', 0):.0%} now, {hw.get('cpu_forecast', 0):.0%} forecast, "
                               f"{hw.get('temp_c', 0)} C")
            self.l_mem.setText(f"{used} of {total} MB in use")
            self.hw_info.setText(
                f"<b>{inv.get('cpu', '')}</b> &middot; {inv.get('cores', '')} threads &middot; {inv.get('arch', '')}"
                f"<br>GPU driver: {', '.join(g['driver'] or 'none' for g in inv.get('gpus', [])) or 'none'}"
                f" &middot; desktop effects: {inv.get('effects', '')}"
                f"<br>Power profile: {hw.get('profile')} ({hw.get('profile_reason', '')})"
                f"<br>I/O schedulers: {', '.join(f'{k}={v}' for k, v in hw.get('io_schedulers', {}).items()) or 'default'}"
                f"<br>Hypervisor: {inv.get('hypervisor', 'none')} &middot; TPM: {'yes' if inv.get('tpm') else 'no'}"
                f" &middot; Secure Boot: {'yes' if inv.get('secure_boot') else 'no'}")
        self.th_list.clear()
        for e in reversed(p["sentinel"].get("events", [])):
            self.th_list.addItem(f"[{e['level'].upper()}] risk {e['risk']}  {e['program']}[{e['pid']}]  "
                                 + "; ".join(e["reasons"]) + ("  (frozen)" if e.get("contained") else ""))
        if not self.th_list.count():
            self.th_list.addItem("No security events in the last hour.")
        current = self.app_list.currentRow()
        self.app_list.clear()
        for app_id, r in uaee.registry().items():
            self.app_list.addItem(f"{app_id}    {r['kind']}    {r['name']}")
        self.app_list.setCurrentRow(current)


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setApplicationName("CIDINT Control Center")
    app.setStyleSheet(STYLE)
    app.setFont(QFont("Noto Sans", 10))
    win = Center()
    win.show()
    return app.exec()
