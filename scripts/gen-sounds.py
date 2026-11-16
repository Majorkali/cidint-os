#!/usr/bin/env python3
"""Synthesize the CIDINT test sound (original, generated here): a short
three-note chime, 48 kHz 16-bit stereo WAV.

    python3 scripts/gen-sounds.py meta-cidint/recipes-cidint/cidint-desktop/files/sounds
"""
import math
import struct
import sys
import wave
from pathlib import Path

RATE = 48000


def chime() -> bytes:
    notes = [(659.25, 0.0), (783.99, 0.18), (987.77, 0.36)]  # E5, G5, B5
    length = int(RATE * 1.4)
    out = bytearray()
    for i in range(length):
        t = i / RATE
        v = 0.0
        for f, start in notes:
            if t >= start:
                dt = t - start
                env = math.exp(-dt * 3.2) * min(1.0, dt * 200)
                v += env * (math.sin(2 * math.pi * f * dt) + 0.3 * math.sin(4 * math.pi * f * dt))
        s = int(max(-1.0, min(1.0, v * 0.28)) * 32767)
        out += struct.pack("<hh", s, s)
    return bytes(out)


def main() -> int:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out / "test-chime.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(chime())
    print(out / "test-chime.wav")
    return 0


if __name__ == "__main__":
    sys.exit(main())
