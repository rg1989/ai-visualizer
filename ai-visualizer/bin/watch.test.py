#!/usr/bin/env python3
"""The one check for watch.py: a change wakes the voice line with frames, the
event cap and the time limit switch the watch off and say so, and a picture
that stops coming is reported too. Fake frames, a scratch bus, no glass:
touches no camera, no screen, no model.   python3 watch.test.py"""
import json
import os, sys, tempfile, pathlib, types
tmp = tempfile.mkdtemp()
os.environ["WATCH_BUS"] = tmp; os.environ["TMPDIR"] = tmp
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import watch
watch.BURST_GAP = 0
watch.glass = lambda body: None


def args(**k):
    d = dict(what="the stove", source="screen", minutes=60, max_events=5, interval=0, threshold=12, cooldown=0, dry_run=False)
    d.update(k); return types.SimpleNamespace(**d)


def frames(seq):
    for v in seq:
        yield np.full(watch.W * watch.H, v, dtype=np.int16)


def grab(p): pathlib.Path(p).write_bytes(b"jpg")


def typed():
    p = pathlib.Path(tmp) / ".voice_typed"; t = p.read_text() if p.exists() else ""; p.unlink(missing_ok=True)
    return [json.loads(l) for l in t.splitlines() if l]


# a quiet picture wakes nobody; one change wakes once, with frames; the stream ending is reported
lines = (watch.run(args(), frames([10, 10, 10, 200, 200]), grab), typed())[1]
assert len(lines) == 2, lines
assert "the stove" in lines[0] and "event-0.jpg" in lines[0] and "[silent]" in lines[0] and "own face" in lines[0], lines[0]
assert "stopped coming" in lines[1] and "only if they ask" in lines[1], lines[1]
assert not watch.PIDF.exists() and not watch.INFOF.exists()

# the event cap: the last event's line carries the OFF notice, nothing follows it
lines = (watch.run(args(max_events=2), frames([0, 100, 0, 100, 0, 100, 0]), grab), typed())[1]
assert len(lines) == 2 and "OFF" not in lines[0] and "event 2 of 2" in lines[1] and "OFF" in lines[1], lines

# the time limit: a spent budget stops on the first frame, before any event, and says so
lines = (watch.run(args(minutes=0), frames([0, 100, 0]), grab), typed())[1]
assert len(lines) == 1 and "switched itself off" in lines[0] and "0-minute limit" in lines[0], lines

# camera lines say camera and carry no screen-only caveat
lines = (watch.run(args(source="camera"), frames([0, 100]), grab), typed())[1]
assert "The camera saw" in lines[0] and "own face" not in lines[0], lines[0]
print("watch ok: event line, stream end, event cap, time limit, camera wording")
