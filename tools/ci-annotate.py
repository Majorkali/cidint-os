#!/usr/bin/env python3
"""Publish the tail of a build log as GitHub error annotations.

Raw Actions logs need a signed-in session; annotations are readable through
the public API, which makes a failed cloud build diagnosable from anywhere.

usage: ci-annotate.py LOGFILE [PARTS] [LINES_PER_PART]
"""
import re
import sys

path = sys.argv[1]
parts = int(sys.argv[2]) if len(sys.argv) > 2 else 4
per = int(sys.argv[3]) if len(sys.argv) > 3 else 40

try:
    raw = open(path, errors="replace").read()
except OSError as exc:
    print(f"::error title=build output::could not read {path}: {exc}")
    sys.exit(0)

ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
lines = [ansi.sub("", l).replace("\r", "")[:300] for l in raw.splitlines()]
tail = lines[-parts * per:]
chunks = [tail[i:i + per] for i in range(0, len(tail), per)]
for n, chunk in enumerate(chunks, 1):
    msg = "\n".join(chunk).replace("%", "%25").replace("\n", "%0A")
    print(f"::error title=build output part {n} of {len(chunks)}::{msg}")
