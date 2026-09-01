"""The listening indicator must fire while you are still talking.

The wake gate runs on whisper's TRANSCRIPT, so every signal downstream
of it is a second late -- and silent entirely when the gate then drops
the line. This check pins the one signal that isn't: on_speech(True) at
the frame the VAD opens, on_speech(False) when that open turns out to
have been a noise blip.

Run it: .venv/bin/python test_mic_indicator.py
"""
import numpy as np

from backtalk import ears


class _Stream:
    def __init__(self, die_after=None):
        self.die_after = die_after
        self.n = 0

    def read(self, n):
        self.n += 1
        if self.die_after and self.n > self.die_after:
            raise RuntimeError("the mic went away")
        return np.zeros((n, 1), dtype=np.int16), None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _ears(script, count=None):
    """An Ears wired to a silent mic and a scripted VAD (one bool per
    frame), so the timing under test is the only thing that varies."""
    ears._open_mic = lambda: _Stream()
    ears.transcribe = lambda pcm: "hello there"
    e = ears.Ears(silence_ms=120)          # 4 frames of trailing quiet

    class VAD:
        def is_speech(self, _buf, _rate):
            if count is not None:
                count.append(None)
            return script.pop(0) if script else False

    e.vad = VAD()
    return e


def main():
    # --- fires DURING the utterance, not after it ------------------------
    fired, frames = [], []
    e = _ears([True] * 24 + [False] * 8, count=frames)
    at = []

    def spy(on):
        fired.append(on)
        at.append(len(frames))

    assert e.listen_once(on_speech=spy, timeout_s=10.0) == "hello there"
    # ...and goes back off when the utterance ends. THIS is the one that
    # stuck the face on LISTENING for minutes: a real sentence that the
    # wake gate then dropped ("not for me") left the light lit forever,
    # because only the noise-blip path ever signalled False.
    assert fired == [True, False], fired
    # frame 4 = OPEN_FRAMES, with ~28 more frames still to come, and the
    # close lands before transcribe() rather than after it
    assert at[0] == ears.OPEN_FRAMES, at
    assert at[1] > 20, at
    assert len(frames) > 20, len(frames)

    # --- a blip takes the indicator back ---------------------------------
    # opens, then quits with <8 frames of speech: a cough, not a
    # sentence. The mic stays open, so the light must go back off.
    seen = []
    e = _ears([True] * 5 + [False] * 6)
    assert e.listen_once(on_speech=seen.append, timeout_s=10.0) is None
    assert seen == [True, False], seen

    # --- every other exit path closes it too -----------------------------
    seen = []
    e = _ears([False] * 400)               # nobody ever speaks: timeout
    assert e.listen_once(on_speech=seen.append, timeout_s=1.0) is None
    assert seen == [], seen                # never opened, never closed

    seen = []
    e = _ears([True] * 400)                # aborted mid-sentence
    n = [0]

    def abort():
        n[0] += 1
        return n[0] > 10

    assert e.listen_once(on_speech=seen.append, abort=abort) is None
    assert seen == [True, False], seen

    seen = []
    e = _ears([True] * 400)
    ears._open_mic = lambda: _Stream(die_after=8)   # unplugged mid-word
    try:
        e.listen_once(on_speech=seen.append, timeout_s=10.0)
        raise AssertionError("the device error should propagate")
    except RuntimeError:
        pass
    assert seen == [True, False], seen

    # --- the callback is optional, and cannot kill the mic ---------------
    e = _ears([True] * 24 + [False] * 8)
    assert e.listen_once(timeout_s=10.0) == "hello there"

    def boom(_on):
        raise RuntimeError("the glass went away mid-sentence")

    e = _ears([True] * 24 + [False] * 8)
    assert e.listen_once(on_speech=boom, timeout_s=10.0) == "hello there"

    print("test_mic_indicator: all assertions passed")


if __name__ == "__main__":
    main()
