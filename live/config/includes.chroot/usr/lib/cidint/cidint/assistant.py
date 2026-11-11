"""Offline assistant: natural-language system control.

Runs entirely on this machine with no model download and no network: a
sentence is normalised, matched against known intents by keyword overlap, and
mapped to one concrete command, which is always shown before it runs.

It is an intent engine, not a large language model. A local LLM can be added
later (llama.cpp + an open model) and plugged in behind `interpret`.
"""
from __future__ import annotations

import re

from . import CLI

SYNONYMS = {
    "anonymity": "anonymous", "anon": "anonymous", "incognito": "anonymous", "tor": "anonymous",
    "hide": "anonymous", "enable": "on", "start": "on", "activate": "on", "turn": "",
    "disable": "off", "stop": "off", "deactivate": "off", "internet": "network", "net": "network",
    "wifi": "network", "connection": "network", "block": "lockdown", "killswitch": "lockdown",
    "panic": "emergency", "threats": "threat", "malware": "threat", "virus": "threat",
    "attacks": "threat", "alerts": "threat", "temp": "temperature", "hot": "temperature",
    "ram": "memory", "slow": "performance", "lag": "performance", "laggy": "performance",
    "speed": "performance", "apps": "app", "applications": "app", "programs": "app",
    "program": "app", "application": "app", "throwaway": "disposable", "temporary": "disposable",
    "errors": "log", "logs": "log", "problems": "log", "wrong": "log", "update": "upgrade",
    "updates": "upgrade", "browser": "browse", "browsing": "browse", "compartments": "domain",
    "domains": "domain", "the": "", "my": "", "me": "", "please": "", "a": "", "is": "", "to": "",
    "i": "", "want": "", "can": "", "you": "", "show": "", "what": "", "whats": "", "of": "",
}

# (keywords, explanation, command, changes_system)
INTENTS = [
    ("anonymous on", "Route all traffic through Tor (Anonymous Mode)", [CLI, "anon", "on"], True),
    ("anonymous off", "Leave Anonymous Mode", [CLI, "anon", "off"], True),
    ("anonymous status", "Show the network privacy mode", [CLI, "anon", "status"], False),
    ("network status", "Show the network privacy mode", [CLI, "anon", "status"], False),
    ("lockdown network", "Engage the network kill switch", [CLI, "lockdown", "on"], True),
    ("network off", "Engage the network kill switch", [CLI, "lockdown", "on"], True),
    ("lockdown off", "Release the network kill switch", [CLI, "lockdown", "off"], True),
    ("network on", "Release the network kill switch", [CLI, "lockdown", "off"], True),
    ("emergency", "Emergency lockdown: block network and lock the screen", [CLI, "panic"], True),
    ("threat", "Show recent security events", [CLI, "sentinel", "events"], False),
    ("security status", "Show the overall security posture", [CLI, "status"], False),
    ("status", "Show the overall system status", [CLI, "status"], False),
    ("hardware", "Show detected hardware and tuning", [CLI, "hw", "status"], False),
    ("performance", "Show load, pressure and the active power profile", [CLI, "hw", "status"], False),
    ("temperature", "Show temperature and power state", [CLI, "hw", "status"], False),
    ("memory", "Show memory usage and pressure", [CLI, "hw", "status"], False),
    ("battery", "Show battery and power state", [CLI, "hw", "status"], False),
    ("app list", "List applications installed through UAEE", [CLI, "app", "list"], False),
    ("installed app", "List applications installed through UAEE", [CLI, "app", "list"], False),
    ("domain", "List security domains", [CLI, "domain", "list"], False),
    ("disposable browse", "Open a browser in a disposable Tor compartment",
     [CLI, "domain", "disposable", "--", "firefox-esr", "--no-remote"], False),
    ("disposable terminal", "Open a terminal in a disposable compartment",
     [CLI, "domain", "disposable", "--", "konsole"], False),
    ("log", "Show errors logged since boot",
     ["journalctl", "-b", "-p", "err", "--no-pager", "-n", "40"], False),
    ("upgrade", "Install pending security updates",
     ["sudo", "sh", "-c", "apt-get update && apt-get -y upgrade"], True),
    ("scan threat", "Scan your home directory for malware",
     ["clamscan", "-r", "-i", "--no-summary", "."], False),
]


def tokens(text: str) -> set:
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        w = SYNONYMS.get(w, w)
        if w:
            out.add(w)
    return out


def interpret(text: str):
    """Return (explanation, argv, changes_system) or None when nothing matches."""
    have = tokens(text)
    if not have:
        return None
    best, best_score = None, 0.0
    for keywords, explanation, argv, mutating in INTENTS:
        want = set(keywords.split())
        hit = len(want & have)
        if hit < len(want):
            continue                      # every keyword of an intent must be present
        s = hit + hit / (len(have) + 1)   # prefer the most specific intent
        if s > best_score:
            best, best_score = (explanation, argv, mutating), s
    return best
