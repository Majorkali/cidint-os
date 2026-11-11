"""Network privacy modes.

  normal     inbound traffic dropped, outbound allowed (default at boot)
  anonymous  every outbound TCP connection and DNS query is transparently
             redirected into Tor; anything that cannot go through Tor (UDP,
             ICMP, IPv6, LAN) is rejected. Rules are loaded BEFORE Tor starts,
             so the mode fails closed: if Tor stops, nothing leaves the machine.
  lockdown   emergency kill switch: only loopback traffic is permitted.

Tor hides your IP address from the sites you visit and hides the sites from
your network. It does not make you anonymous against every adversary: a global
observer, a compromised endpoint or your own behaviour can still identify you.
"""
from __future__ import annotations

import time

from . import ETC_DIR, RUN_DIR, read, sh

ANON_RULES = ETC_DIR / "nft-anon.nft"
STATE = RUN_DIR / "net-mode"
LOCKDOWN_RULES = """
table inet cidint_lockdown {
  chain output { type filter hook output priority -10; policy drop; oifname "lo" accept; }
  chain input  { type filter hook input  priority -10; policy drop; iifname "lo" accept; }
  chain forward { type filter hook forward priority -10; policy drop; }
}
"""


def _table(name: str) -> bool:
    return sh(["nft", "list", "table", "inet", name]).returncode == 0


def _drop_table(name: str) -> None:
    if _table(name):
        sh(["nft", "delete", "table", "inet", name])


def _set_state(mode: str) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(mode + "\n")
    STATE.chmod(0o644)


def bootstrap_percent() -> int:
    out = sh(["journalctl", "-u", "tor@default", "-b", "--no-pager", "-o", "cat"]).stdout
    pct = 0
    for line in out.splitlines():
        if "Bootstrapped " in line:
            try:
                pct = int(line.split("Bootstrapped ")[1].split("%")[0])
            except (IndexError, ValueError):
                pass
    return pct


def anon_on(wait: int = 90, log=print) -> bool:
    r = sh(["nft", "-f", str(ANON_RULES)]) if not _table("cidint_anon") else None
    if r is not None and r.returncode != 0:
        log(f"failed to load Tor firewall rules: {r.stderr.strip()}")
        return False
    _set_state("anonymous")
    log("firewall: all traffic now forced through Tor (fail-closed)")
    sh(["systemctl", "restart", "tor@default"])
    time.sleep(3)  # let the new instance log "Bootstrapped 0%" before we poll
    deadline = time.time() + wait
    pct = 0
    while time.time() < deadline:
        pct = bootstrap_percent()
        if pct >= 100:
            log("Tor circuit established. Anonymous Mode is active.")
            return True
        time.sleep(2)
    log(f"Tor has not finished connecting ({pct}%). Traffic stays blocked until it does. "
        "If your network blocks Tor, configure a bridge in /etc/tor/torrc.")
    return False


def anon_off(log=print) -> None:
    sh(["systemctl", "stop", "tor@default"])
    _drop_table("cidint_anon")
    _set_state("lockdown" if _table("cidint_lockdown") else "normal")
    log("Anonymous Mode off: traffic leaves directly from your real IP address.")


def lockdown(enable: bool, log=print) -> None:
    if enable:
        if not _table("cidint_lockdown"):
            sh(["nft", "-f", "-"], input=LOCKDOWN_RULES)
        _set_state("lockdown")
        log("Kill switch engaged: all network traffic is blocked.")
    else:
        _drop_table("cidint_lockdown")
        _set_state("anonymous" if _table("cidint_anon") else "normal")
        log("Kill switch released.")


def status() -> dict:
    """Readable without root."""
    mode = read(STATE, "normal")
    tor = sh(["systemctl", "is-active", "tor@default"]).stdout.strip() == "active"
    return {"mode": mode, "tor_running": tor}
