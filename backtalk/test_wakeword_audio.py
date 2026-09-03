"""The one check that fails if the audio wake gate stops gating.

Plumbing, always: fed 30 ms mic frames the way listen_once does, the
detector must never fire on silence or on white noise, and must handle
frames that do not divide its 80 ms hop. Accuracy, when clips exist: set
BACKTALK_WAKE_CLIPS to a dir of pos_*.wav / neg_*.wav (any rate) and every
positive must fire and no negative may. Run: .venv/bin/python test_wakeword_audio.py
"""
import glob
import os

import numpy as np

from backtalk import wakeword_audio
from backtalk.config import CFG


def frames_of(pcm, n=480):                 # 30 ms frames, like the mic
    for i in range(0, len(pcm) - n + 1, n):
        yield pcm[i:i + n]


def fired_on(det, pcm):
    det.m.reset(); det.buf = det.buf[:0]
    return any(det.feed(f) for f in frames_of(pcm))


def test_gate():
    det = wakeword_audio.Detector(CFG["wake_model_file"],
                                  CFG["wake_model_threshold"], CFG["wake_model_vad"])
    rng = np.random.default_rng(0)
    assert not fired_on(det, np.zeros(16000 * 5, dtype=np.int16)), "fired on silence"
    assert not fired_on(det, (rng.standard_normal(16000 * 10) * 0.05 * 32767).astype(np.int16)), "fired on white noise"
    det.buf = det.buf[:0]
    assert det.feed(np.zeros(7, dtype=np.int16)) is False and len(det.buf) == 7, "odd frame sizes must buffer"
    print("ok: silent and noisy rooms never fire; odd frames buffer")
    d = os.environ.get("BACKTALK_WAKE_CLIPS")
    if not d:
        return
    import soundfile as sf
    def load(p):
        a, sr = sf.read(p, dtype="float32")
        if sr != 16000:
            a = np.interp(np.arange(0, len(a), sr / 16000), np.arange(len(a)), a)
        return (np.clip(a, -1, 1) * 32767).astype(np.int16)
    pos = sorted(glob.glob(f"{d}/pos_*.wav")); neg = sorted(glob.glob(f"{d}/neg_*.wav"))
    missed = [os.path.basename(p) for p in pos if not fired_on(det, load(p))]
    false = [os.path.basename(p) for p in neg if fired_on(det, load(p))]
    assert not missed, f"positives missed: {missed}"
    assert not false, f"negatives fired: {false}"
    print(f"ok: {len(pos)} positives fired, {len(neg)} negatives silent")


def test_gate_only_for_its_name():
    """The stock model hears "hey jarvis" and nothing else. Any other persona
    name must get NO audio gate and fall back to transcript matching, as
    before -- with the gate engaged, a name the model does not know is never
    heard at all. Found the hard way: the SHODAN theme went deaf."""
    assert wakeword_audio.enabled("Jarvis") is True
    assert wakeword_audio.enabled("jarvis") is True
    assert wakeword_audio.enabled("SHODAN") is False
    assert wakeword_audio.enabled("") is False
    print("ok: gate engages for Jarvis only; other names use transcripts")


if __name__ == "__main__":
    test_gate()
    test_gate_only_for_its_name()
