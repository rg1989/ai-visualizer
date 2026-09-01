# backtalk: talk to your Claude Code agent out loud.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""JARVIS voice effect — Iron Man's butler AI, as a light DSP polish.

The opposite philosophy to shodanfx: SHODAN is a broken machine wearing
a voice, JARVIS is a valet who happens to be software. Paul Bettany's
takes reached the films nearly untouched (two hours in a booth with
Favreau, done) — what sells "AI in the ceiling" is placement, and fan
recreations of the sound agree on the numbers: a small clean room
reverb at a few percent wet, a 1-2 dB consonant lift up around 6 kHz
so the RP accent reads crisp, and gentle slow compression. This module
does exactly that, plus one whisper-quiet detuned double for electronic
smoothness, and nothing else. If you can hear the effect working, it is
set too loud. The base voice carries the character: a refined British
male — bm_george by default.

Everything is deterministic (fixed room, fixed LFO phases): the same
line renders identically every time.

Enable with "voice_fx": "jarvis" in backtalk.json, "voice": "bm_george"
(or bm_fable for a lighter read), "speed": 1.0.

Try it aloud:  python -m backtalk.jarvisfx "Good evening, sir."
Self-check:    python -m backtalk.jarvisfx
"""
import sys

import numpy as np

from backtalk.shodanfx import _mod_delay, fx_stream

# The calibration knobs — subtle by design, tune freely.
HIGHPASS_HZ = 130       # soft low cut: a voice from the walls has no chest
PRESENCE_HZ = 6000.0    # consonant crispness, per the recreation lore
PRESENCE_GAIN = 0.26    # ~ +2 dB at the peak
DOUBLE = (0.014, 0.0018, 0.4, 0.16)   # base s, sweep s, LFO Hz, gain
REVERB_S = 0.22         # small clean room
REVERB_WET = 0.06       # "3-5% wet" — Kokoro is bone-dry, sit at the top
DRIVE = 1.06            # barely-there tanh evenness, the lazy 2:1


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
    """Convolve with a fixed seeded exp-decay noise burst, top damped —
    the same small room every line, mixed low behind the dry voice."""
    n_ir = int(REVERB_S * rate)
    ir = np.random.default_rng(11).standard_normal(n_ir).astype(np.float32)
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
    """int16 mono in -> int16 mono out, JARVIS'd."""
    if pcm.size < int(0.05 * rate):
        return pcm              # a click or a breath — nothing to treat
    x = pcm.astype(np.float32) / 32768.0
    x = _eq(x, rate)
    x = x + _mod_delay(x, rate, *DOUBLE, 0.0)
    y = _reverb(x, rate)
    y = np.tanh(DRIVE * y) * 0.9
    return (y * 32767.0).astype(np.int16)


def jarvisize_stream(gen, text: str = ""):
    """Wrap a synth (rate, pcm) stream in the effect; text is unused
    (nothing here is randomized per line)."""
    return fx_stream(gen, process)


if __name__ == "__main__":
    if len(sys.argv) > 1:       # speak a line through the effect, aloud
        from backtalk.config import CFG
        CFG["voice_fx"] = "jarvis"
        if not str(CFG.get("voice", "")).startswith("bm_"):
            CFG["voice"] = "bm_george"          # audition the real thing
            CFG["speed"] = 1.0
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
        assert a.size == x.size + int(REVERB_S * rate)  # tail kept
        assert np.abs(a.astype(np.int32)).max() <= 30000   # limiter holds
        assert np.array_equal(a, b)                 # deterministic
        # subtle by design: the dry body must still dominate the output
        assert np.corrcoef(a[:x.size].astype(np.float32),
                           x.astype(np.float32))[0, 1] > 0.7
        short = x[:rate // 40]
        assert np.array_equal(process(short, rate), short)  # passthrough
        chunks = list(jarvisize_stream(
            iter([(rate, x[:x.size // 2]), (rate, x[x.size // 2:])]), "hi"))
        assert sum(c.size for _, c in chunks) == a.size  # one buffered pass
        assert all(r == rate for r, _ in chunks)
        print("jarvisfx ok:", x.size, "->", a.size, "samples,",
              len(chunks), "stream chunks")
