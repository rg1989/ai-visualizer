"""The one check that fails if the face says "speaking" before any sound.

The mouth used to raise "speaking" the moment a sentence was dequeued and
then synthesize it -- at startup that was a 3 s kokoro load plus ~0.7 s of
synthesis with the face already animating a voice that was not there.
The state must flip at AUDIO START, with "generating speech" as the
stage while the wait is real. Synthesis and the output device are stubbed;
every bus file is redirected so a live face is never touched.
Run: .venv/bin/python test_mouth_state.py
"""
import os
import tempfile
import threading
import time

import numpy as np

from backtalk import signals

# Redirect every bus file BEFORE anything writes: the voice line may be live.
_tmp = tempfile.mkdtemp(prefix="backtalk-test-")
for _n in dir(signals):
    if _n.endswith("_FILE") and isinstance(getattr(signals, _n), str):
        setattr(signals, _n, os.path.join(_tmp, _n))
signals._BH_STATE = None
for _f in ("feed_waveform", "static_stop", "static_start", "reply_done",
           "chat_add", "direction", "play_cue"):
    if hasattr(signals, _f):
        setattr(signals, _f, lambda *a, **k: None)
_stage = {"v": ""}
_orig_set_stage = signals.set_stage
signals.set_stage = lambda name="": (_stage.__setitem__("v", name), _orig_set_stage(name))[1]

import backtalk.ducking as ducking


class _NoDuck:
    def speech_start(self): pass
    def speech_end(self, *a): pass


ducking.Ducker = _NoDuck
import backtalk.mouth as mouth

SYNTH_S = 0.6


def _slow_synth(text):
    time.sleep(SYNTH_S)                       # the model "rendering"
    yield 24000, np.zeros(24000, dtype=np.int16)   # 1 s of silence


mouth.synth_stream = _slow_synth


class _NoOut:
    active = True
    def write(self, pcm): time.sleep(len(pcm) / 24000 / 4)   # play "fast"
    def start(self): pass
    def close(self): pass


mouth.Mouth._get_out = lambda self, rate: _NoOut()


def test_speaking_waits_for_audio():
    signals.set_state("idle")
    m = mouth.Mouth()
    t0 = time.monotonic()
    m.say("Hello there.")
    seen = []
    while time.monotonic() - t0 < SYNTH_S * 3:
        seen.append((round(time.monotonic() - t0, 2), signals.state(), _stage["v"]))
        time.sleep(0.02)
    mid = [s for t, s, _ in seen if 0.15 <= t <= SYNTH_S * 0.7]
    mid_stage = {st for t, _, st in seen if 0.15 <= t <= SYNTH_S * 0.7}
    late = [s for t, s, _ in seen if t >= SYNTH_S + 0.25]
    assert mid and "speaking" not in mid, f"face said 'speaking' during synthesis: {mid[:5]}"
    assert "generating speech" in mid_stage, f"no 'generating speech' stage during the wait: {mid_stage}"
    assert "speaking" in late, f"never reached 'speaking' after audio started: {late[:5]}"
    print(f"ok: mid-synthesis state {set(mid)} / stage {mid_stage}; speaking after audio")


if __name__ == "__main__":
    test_speaking_waits_for_audio()
