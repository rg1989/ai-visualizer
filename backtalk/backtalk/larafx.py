# backtalk: talk to your Claude Code agent out loud.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lara Croft voice — the booth-professional Brit, with tomb-raider poise.

Not an AI this time, so there is no robot chain to borrow: Lara is
human, and what reads as "Croft" across every actress who has played
her — Shelley Blond's original, Jonell Elliott, Keeley Hawes'
aristocratic RP, Camilla Luddington's younger reboot — is register and
composure. A low-set, warm, self-possessed British RP female voice;
serious, unhurried, faintly amused. Built for a convention booth:
pleasant to walk past, impressive to stop for, never a gimmick.

So the base voice does the character (bf_emma — Kokoro's most mature
British female; bf_isabella is the softer alternative) and this pass
only sets register and polish: a modest static pitch-down into the
low warm zone, a consonant lift so RP stays crisp over booth speakers
in a noisy hall, a tiny clean room, and gentle evenness so her level
never jumps. No wow, no doubling, no glitches — she must sound like
a person, or the booth reads as a toy.

Deterministic: the same line renders identically every time.

Enable with "voice_fx": "lara" in backtalk.json, "voice": "bf_emma",
"speed": 1.05 (nets out the pitch-down's slowdown — booth crisp).

Try it aloud:  python -m backtalk.larafx "Do come closer."
Self-check:    python -m backtalk.larafx
"""
import sys

import numpy as np

from backtalk.shodanfx import fx_stream

# The calibration knobs — subtle by design, tune freely.
PITCH = 0.96            # ~0.7 semitone down: warm and low, not mannish
HIGHPASS_HZ = 90        # rumble only — keep the chest, she is human
PRESENCE_HZ = 4500.0    # RP consonant crispness for a noisy hall
PRESENCE_GAIN = 0.26    # ~ +2 dB at the peak
REVERB_S = 0.16         # smaller and cleaner than even the JARVIS room
REVERB_WET = 0.04
DRIVE = 1.1             # evenness: booth speakers want a steady level

# ponytail: _eq/_reverb mirror jarvisfx with different knobs — a fourth
# character fx is the moment to pull a voicefx commons module.


def _deepen(x):
    """Static tape-style resample, no wander: humans hold their pitch."""
    pos = np.arange(int(x.size / PITCH)) * PITCH
    return np.interp(pos, np.arange(x.size), x).astype(np.float32)


def _eq(x, rate):
    """One FFT pass: soft high-pass plus a gaussian presence lift."""
    nf = 1 << (x.size + 1).bit_length()
    X = np.fft.rfft(x, nf)
    f = np.fft.rfftfreq(nf, 1.0 / rate)
    g = f / (f + HIGHPASS_HZ)
    g *= 1 + PRESENCE_GAIN * np.exp(
        -0.5 * (np.log2((f + 1.0) / PRESENCE_HZ) / 0.7) ** 2)
    return np.fft.irfft(X * g, nf)[:x.size].astype(np.float32)


def _reverb(x, rate):
    """A fixed seeded exp-decay noise burst, top damped — the same
    small clean room every line, barely there behind the dry voice."""
    n_ir = int(REVERB_S * rate)
    ir = np.random.default_rng(23).standard_normal(n_ir).astype(np.float32)
    ir *= np.exp(np.linspace(0.0, -6.9, n_ir, dtype=np.float32))
    nf = 1 << (x.size + n_ir).bit_length()
    f = np.fft.rfftfreq(nf, 1.0 / rate)
    irf = np.fft.rfft(ir, nf) / (1 + f / 2500.0)          # soft walls
    ir = np.fft.irfft(irf, nf)[:n_ir]
    ir *= REVERB_WET / np.sqrt(np.sum(ir * ir))
    wet = np.fft.irfft(np.fft.rfft(x, nf) * np.fft.rfft(ir, nf), nf)
    y = wet[:x.size + n_ir].astype(np.float32)            # keep the tail
    y[:x.size] += x
    return y


def process(pcm: np.ndarray, rate: int) -> np.ndarray:
    """int16 mono in -> int16 mono out, Croft'd."""
    if pcm.size < int(0.05 * rate):
        return pcm              # a click or a breath — nothing to treat
    x = pcm.astype(np.float32) / 32768.0
    x = _deepen(x)
    x = _eq(x, rate)
    y = _reverb(x, rate)
    y = np.tanh(DRIVE * y) * 0.9
    return (y * 32767.0).astype(np.int16)


def laraize_stream(gen, text: str = ""):
    """Wrap a synth (rate, pcm) stream in the effect; text is unused
    (nothing here is randomized per line)."""
    return fx_stream(gen, process)


if __name__ == "__main__":
    if len(sys.argv) > 1:       # speak a line through the effect, aloud
        from backtalk.config import CFG
        CFG["voice_fx"] = "lara"
        if not str(CFG.get("voice", "")).startswith("bf_"):
            CFG["voice"] = "bf_emma"            # audition the real thing
            CFG["speed"] = 1.05
        from backtalk.mouth import Mouth
        m = Mouth()
        m.say(" ".join(sys.argv[1:]))
        m.wait_done(timeout=60)
    else:                       # silent self-check, no audio device needed
        rate = 24000
        t = np.arange(int(rate * 1.5)) / rate
        x = (0.4 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)
        a = process(x, rate)
        b = process(x, rate)
        assert a.dtype == np.int16
        assert a.size == int(x.size / PITCH) + int(REVERB_S * rate)
        assert np.abs(a.astype(np.int32)).max() <= 30000   # limiter holds
        assert np.array_equal(a, b)                 # deterministic
        short = x[:rate // 40]
        assert np.array_equal(process(short, rate), short)  # passthrough
        chunks = list(laraize_stream(
            iter([(rate, x[:x.size // 2]), (rate, x[x.size // 2:])]), "hi"))
        assert sum(c.size for _, c in chunks) == a.size  # one buffered pass
        assert all(r == rate for r, _ in chunks)
        print("larafx ok:", x.size, "->", a.size, "samples,",
              len(chunks), "stream chunks")
