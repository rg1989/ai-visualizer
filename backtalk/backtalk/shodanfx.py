# backtalk: talk to your Claude Code agent out loud.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""SHODAN voice effect — System Shock's rogue AI, as a DSP pass.

The canon recipe (Eric Brosius processing Terri Brosius' takes, per the
TTLG forum archaeology and the System Shock wiki): pitch the voice DOWN
a few semitones, add a mild flange so it reads subtly robotic, layer
copies of the voice that alternately lead ahead of and lag behind the
original, let the pitch wander, and stutter-retrigger syllables like a
sound card losing its mind. This module does all five, in numpy, on a
whole sentence at a time.

Deterministic per sentence text: the same line glitches the same way
every time it is spoken, and the self-check below can assert on it.

Enable with "voice_fx": "shodan" in backtalk.json. Pair with an American
female Kokoro voice (Terri Brosius is American) — af_bella works well —
and a touch of "speed" (1.1) so the tape-slowdown lands back near normal
pace with the pitch still dropped.

Try it aloud:  python -m backtalk.shodanfx "Look at you, hacker."
Self-check:    python -m backtalk.shodanfx
"""
import sys
import zlib

import numpy as np

# The calibration knobs — ears differ, speakers differ, tune freely.
PITCH = 0.92            # tape-style resample: <1 = deeper and slower
WOW_DEPTH = 0.012       # slow pitch wander, +/-1.2%
WOW_HZ = 0.7
FLUTTER_DEPTH = 0.004   # fast shimmer on top of the wander
FLUTTER_HZ = 3.1
FLANGE = (0.008, 0.005, 0.35, 0.50)     # base s, sweep s, LFO Hz, gain
CHORUS = ((0.025, 0.012, 0.55, 0.45),   # the voices that lead and lag
          (0.038, 0.015, 0.85, 0.40))
STUTTER_CHANCE = 0.85   # per sentence
DRIVE = 1.25            # tanh saturation into the final limiter


def _mod_delay(x, rate, base_s, sweep_s, hz, gain, phase):
    """One tap whose delay swings on an LFO. Short sweep = flange,
    longer = a doubled voice drifting ahead and behind the dry one."""
    n = np.arange(x.size, dtype=np.float64)
    d = (base_s + sweep_s * np.sin(2 * np.pi * hz * n / rate + phase)) * rate
    idx = np.clip(n - d, 0, x.size - 1)
    return (gain * np.interp(idx, n, x)).astype(np.float32)


def _warp(x, rate, rng):
    """Tape pitch-down plus wandering wow/flutter, as one time-varying
    resample. Output is longer than input — that slowdown is part of
    the menace."""
    n_out = int(x.size / PITCH)
    t = np.arange(n_out, dtype=np.float64) / rate
    p1, p2 = rng.uniform(0, 2 * np.pi, 2)
    ratio = PITCH * (1 + WOW_DEPTH * np.sin(2 * np.pi * WOW_HZ * t + p1)
                     + FLUTTER_DEPTH * np.sin(2 * np.pi * FLUTTER_HZ * t + p2))
    pos = np.clip(np.cumsum(ratio), 0, x.size - 1)
    return np.interp(pos, np.arange(x.size), x).astype(np.float32)


def _stutter(x, rate, rng):
    """Retrigger one or two syllables, machine-gun style. The slice is
    aimed at a local energy peak so the glitch lands on a voiced sound
    instead of a silence."""
    if x.size < int(0.8 * rate) or rng.random() > STUTTER_CHANCE:
        return x
    env = np.convolve(np.abs(x), np.ones(1024, np.float32), "same")
    fade = np.linspace(1, 0, max(1, int(0.006 * rate)), dtype=np.float32)
    out, cursor = [], 0
    for _ in range(int(rng.integers(1, 3))):
        lo = max(cursor, int(0.1 * x.size))
        hi = int(0.85 * x.size)
        if hi - lo < rate // 4:
            break
        w0 = int(rng.integers(lo, hi - rate // 5))
        start = w0 + int(np.argmax(env[w0:w0 + rate // 5]))
        length = int(rng.uniform(0.07, 0.13) * rate)
        start = max(cursor, min(start, x.size - length - 1))
        piece = x[start:start + length].copy()
        piece[-fade.size:] *= fade          # kill the click at the cut
        out.append(x[cursor:start])
        for _ in range(int(rng.integers(2, 4))):
            out.append(piece)
        cursor = start                      # then the real syllable plays on
    out.append(x[cursor:])
    return np.concatenate(out)


def process(pcm: np.ndarray, rate: int, seed: int) -> np.ndarray:
    """int16 mono in -> int16 mono out, SHODAN'd."""
    if pcm.size < int(0.05 * rate):
        return pcm              # a click or a breath — nothing to treat
    x = pcm.astype(np.float32) / 32768.0
    rng = np.random.default_rng(seed)
    x = _warp(x, rate, rng)
    y = x + _mod_delay(x, rate, *FLANGE, rng.uniform(0, 2 * np.pi))
    for base_s, sweep_s, hz, gain in CHORUS:
        y = y + _mod_delay(x, rate, base_s, sweep_s, hz, gain,
                           rng.uniform(0, 2 * np.pi))
    y = _stutter(y, rate, rng)
    y = np.tanh(DRIVE * y) * 0.9
    return (y * 32767.0).astype(np.int16)


def fx_stream(gen, fn):
    """Buffer a synth (rate, pcm) stream per contiguous sample rate —
    a sentence, in practice — run fn(pcm, rate) over the whole line,
    yield it back in 0.25 s chunks. Shared by every character fx:
    glitches and room tails want the full sentence in hand. Voice-mode
    sentences are short, so the cost is a beat of latency per line.
    Flushes early if the rate changes mid-line (engine fallback)."""
    def flush(buf, rate):
        y = fn(np.concatenate(buf), rate)
        step = max(1, int(rate * 0.25))
        for i in range(0, y.size, step):
            yield rate, y[i:i + step]

    rate, buf = None, []
    for r, pcm in gen:
        if rate is not None and r != rate:
            yield from flush(buf, rate)
            buf = []
        rate = r
        buf.append(np.asarray(pcm))
    if buf:
        yield from flush(buf, rate)


def shodanize_stream(gen, text: str):
    """Wrap a synth (rate, pcm) stream in the effect, seeded by the
    sentence text so the same line glitches the same way every time."""
    seed = zlib.adler32(text.encode("utf-8", "ignore")) or 1
    return fx_stream(gen, lambda pcm, rate: process(pcm, rate, seed))


if __name__ == "__main__":
    if len(sys.argv) > 1:       # speak a line through the effect, aloud
        from backtalk.config import CFG
        CFG["voice_fx"] = "shodan"
        from backtalk.mouth import Mouth
        m = Mouth()
        m.say(" ".join(sys.argv[1:]))
        m.wait_done(timeout=60)
    else:                       # silent self-check, no audio device needed
        rate = 24000
        t = np.arange(int(rate * 1.5)) / rate
        x = (0.4 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)
        a = process(x, rate, seed=7)
        b = process(x, rate, seed=7)
        assert a.dtype == np.int16
        assert a.size > x.size                      # tape-down lengthens
        assert np.abs(a.astype(np.int32)).max() <= 30000   # limiter holds
        assert np.array_equal(a, b)                 # deterministic per seed
        short = x[:rate // 40]
        assert np.array_equal(process(short, rate, 7), short)  # passthrough
        chunks = list(shodanize_stream(
            iter([(rate, x[:x.size // 2]), (rate, x[x.size // 2:])]), "hi"))
        whole = process(x, rate, zlib.adler32(b"hi"))
        assert sum(c.size for _, c in chunks) == whole.size  # one buffered pass
        assert all(r == rate for r, _ in chunks)
        print("shodanfx ok:", x.size, "->", a.size, "samples,",
              len(chunks), "stream chunks")
