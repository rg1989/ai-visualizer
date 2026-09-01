"""Music must not talk to her — and must not deafen her either.

The face's player publishes .voice_media; signals.media_playing() reads
it, and main.py turns that into "the wake word is required again" so a
lyric can never be mistaken for a command. This pins the read end.

The expiry is the half worth a test: the writer is a browser TAB, so a
note that outlives its writer is the failure mode that leaves an always-
listening mic silently demanding a name in an empty room.
"""
import os
import tempfile
import time

from backtalk import signals


def _bus():
    path = os.path.join(tempfile.mkdtemp(), ".voice_media")
    signals._MEDIA_FILE = path
    return path


def test_silence_is_the_default():
    _bus()
    assert signals.media_playing() is False


def test_a_fresh_note_means_music():
    path = _bus()
    with open(path, "w") as f:
        f.write("1")
    assert signals.media_playing() is True


def test_a_stale_note_expires():
    """Tab closed, renderer crashed, server killed — she gets her open
    mic back on her own. This is the whole reason it is read by mtime
    and not by existence."""
    path = _bus()
    with open(path, "w") as f:
        f.write("1")
    old = time.time() - signals.MEDIA_STALE_S - 1
    os.utime(path, (old, old))
    assert signals.media_playing() is False


def test_stopping_clears_it():
    path = _bus()
    with open(path, "w") as f:
        f.write("1")
    os.unlink(path)             # what POST /media {"playing": false} does
    assert signals.media_playing() is False


if __name__ == "__main__":
    test_silence_is_the_default()
    test_a_fresh_note_means_music()
    test_a_stale_note_expires()
    test_stopping_clears_it()
    print("media gate ok")
