"""Generate the wake-mode chimes (stdlib only; run once, outputs are
committed). wake.wav rises (I'm listening), done.wav falls (got it).
Tweak the note pairs below and re-run to restyle them."""

import math
import struct
import wave

RATE = 24000


def tone(freq, ms, vol=0.4):
    n = int(RATE * ms / 1000)
    out = []
    for i in range(n):
        # 8ms fade in/out so the edges never click
        env = min(1.0, i / (RATE * 0.008), (n - i) / (RATE * 0.008))
        out.append(vol * env * math.sin(2 * math.pi * freq * i / RATE))
    return out


def write(path, notes):
    samples = []
    for freq, ms in notes:
        samples += tone(freq, ms)
    samples += [0.0] * int(RATE * 0.02)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(
            struct.pack("<h", int(max(-1, min(1, s)) * 32767))
            for s in samples))
    print(f"wrote {path}")


if __name__ == "__main__":
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    # Short on purpose: the mic window arms right behind it, and every
    # extra millisecond of chime is a millisecond the person's answer
    # can start into a deaf mic.
    write(os.path.join(here, "wake.wav"), [(659, 70), (880, 90)])     # E5 -> A5
    write(os.path.join(here, "done.wav"), [(880, 90), (659, 130)])    # A5 -> E5
    write(os.path.join(here, "close.wav"), [(392, 140)])              # G4: window closed, nothing heard
