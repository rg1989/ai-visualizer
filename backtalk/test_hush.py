"""The one check for "stop listening" reaching the face.

"stop listening" publishes hush on the mic line (.voice_mic "wake hush"):
the overlay window fades on it at once and stays down; the next publish
without it (being addressed: talk key, wake word, typing) lets it wake.
Field-caught before this: she said "Stopped." and lingered on screen for
twenty seconds. Bus files are redirected; a live face is never touched.
Run: .venv/bin/python test_hush.py
"""
import os
import tempfile

from backtalk import signals

_tmp = tempfile.mkdtemp(prefix="backtalk-test-")
signals._MIC_FILE = os.path.join(_tmp, ".voice_mic")


def _line():
    with open(signals._MIC_FILE) as f:
        return f.read()


def test_hush_rides_the_mic_line():
    signals.set_mic("wake", hush=True)
    assert _line() == "wake hush", _line()
    # A hushed wake mode is still cold wake mode for the ears.
    assert signals.unsummoned()
    signals.set_mic("ptt", hush=True)
    assert _line() == "ptt hush", _line()
    # The next publish without it is the all-clear; hot and hush are
    # separate tokens and never collide.
    signals.set_mic("wake", hot=True)
    assert _line() == "wake hot", _line()
    signals.set_mic("wake")
    assert _line() == "wake", _line()
    print("ok: hush rides the mic line and the next publish clears it")


if __name__ == "__main__":
    test_hush_rides_the_mic_line()
