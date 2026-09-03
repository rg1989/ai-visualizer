"""The face page's talk key (ptt_scope "face") -> a press the voice line
believes, a release it drops, and a dead page's hold it ignores.

core.js POSTs {held, n} to the visualizer's /ptt, the server drops
.voice_ptt beside the bus, and ptt.FacePTT reads it with the same
wait_press()/is_held() PTTListener has. This pins the reader end.

Run: .venv/bin/python test_face_ptt.py
"""
import json
import os
import tempfile
import threading
import time

from backtalk import ptt, signals

signals._DIR = tempfile.mkdtemp()
PATH = os.path.join(signals._DIR, ".voice_ptt")


def post(held, n, age=0.0):
    """What server.py writes for one POST /ptt, `age` seconds ago."""
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"held": held, "n": n, "t": time.time() - age}, f)
    os.replace(tmp, PATH)


def waiter(p):
    t = threading.Thread(target=p.wait_press, daemon=True)
    t.start()
    return t


post(True, 7, age=30)                     # a hold a dead page left behind
p = ptt.FacePTT()
assert not p.is_held(), "a stale hold counted as held"
t = waiter(p)
t.join(0.3)
assert t.is_alive(), "a stale hold woke wait_press"

post(True, 8)                             # a real press
t.join(0.5)
assert not t.is_alive(), "a fresh press did not wake wait_press"
assert p.is_held()
post(True, 8, age=signals.PTT_STALE_S + 1)   # heartbeat stopped: tab died
assert not p.is_held(), "a page that stopped re-posting still held the key"
post(True, 8)                             # heartbeat back
assert p.is_held()
post(False, 8)                            # released
assert not p.is_held()

t = waiter(p)
t.join(0.3)
assert t.is_alive(), "a release (same counter) woke wait_press"
post(True, 9)                             # the next press
t.join(0.5)
assert not t.is_alive(), "the next press did not wake wait_press"
assert not signals.face_ptt()[0] or p.is_held()
print("ok: face talk key -- press wakes, release drops, stale holds ignored")
