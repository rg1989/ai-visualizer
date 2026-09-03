"""The one check for the typed-turn queue: two reports written a hair
apart both reach the brain, in order, and a pasted multi-line prompt is
still one message."""
import json
import os
import queue
import tempfile
import threading
import time

from backtalk import signals
from backtalk.main import _face_typed_reader


def test_typed_queue():
    d = tempfile.mkdtemp(prefix="typed-")
    signals._DIR = d
    q: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=_face_typed_reader, args=(q,), daemon=True).start()
    path = os.path.join(d, ".voice_typed")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps("[task finished: one] done") + "\n")
        f.write(json.dumps("[task finished: two] done") + "\n")
        f.write(json.dumps("first line\nsecond line") + "\n")
        f.write("a bare line from an older server\n")
    got = [q.get(timeout=3) for _ in range(4)]
    assert got[0] == "[task finished: one] done" and got[1] == "[task finished: two] done", got
    assert got[2] == "first line second line", got   # one message, joined
    assert got[3] == "a bare line from an older server", got
    time.sleep(0.5)
    assert not os.path.exists(path) and not os.path.exists(path + ".busy"), os.listdir(d)
    print("ok: four lines, four turns, in order; paste joined; file drained")


if __name__ == "__main__":
    test_typed_queue()
