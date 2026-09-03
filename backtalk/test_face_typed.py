"""The face's prompt box (P) -> a first-class turn.

The browser POSTs to the visualizer's /say, the server drops the line
beside the bus as .voice_typed, and _face_typed_reader lifts it into
the same queue the keyboard feeds. This pins the reader end: what
lands on disk comes out of the queue as one clean message, and the
file is consumed so it can never fire twice.
"""
import json
import os
import queue
import tempfile
import threading
import time

from backtalk import main, signals


def _reader_on_a_temp_bus():
    d = tempfile.mkdtemp()
    signals._DIR = d
    q: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=main._face_typed_reader, args=(q,),
                     daemon=True).start()
    return d, q, os.path.join(d, ".voice_typed")


def test_a_line_becomes_a_turn_and_the_file_is_eaten():
    _d, q, path = _reader_on_a_temp_bus()
    with open(path, "w") as f:                  # gutter glyph + a paste
        f.write(json.dumps("  > what is the weather\nin Ramat Gan  ") + "\n")   # one message: JSON keeps the newline inside
    assert q.get(timeout=3) == "what is the weather in Ramat Gan"
    assert not os.path.exists(path)


def test_whitespace_is_not_a_turn():
    _d, q, path = _reader_on_a_temp_bus()
    with open(path, "w") as f:
        f.write(json.dumps("   \n  ") + "\n")
    time.sleep(0.6)
    assert q.empty()
    assert not os.path.exists(path)
