"""The one check that fails if two threads may race PortAudio on one mic.

Field-caught 2026-09-02 on macOS 26 (CoreAudio 5): in wake-word mode the
talk key aborts the wake capture (Pa_StopStream on thread A) while the
recorder opens its own stream (Pa_OpenStream on thread B). CoreAudio's IO
thread holds the HAL device mutex while it waits for the AudioUnit mutex
the opener holds: three threads, one cycle, the mic gone for the life of
the process and the face parked on "listening" -- the 60 s hold ceiling
lives inside the recorder, which never got a stream. Replayed cold the
same collision throws paInternalError (-9986), or an AUHAL -50 and a
segfault, depending on the phase. ears._open_mic now serialises every
open/start/stop/close through ears.PA_LOCK.

This replays the press through the real listen_once and record_held,
with the wake capture already running 0.5-2.5 s and the speaker stream
open like the mouth's. --raw swaps PA_LOCK for a no-op and shows the
field failure; a watchdog turns a hang into exit 2. Needs a microphone.
Run: .venv/bin/python test_mic_lock.py [--raw]
"""
import contextlib
import os
import sys
import threading
import time

import numpy as np
import sounddevice as sd

from backtalk import ears

ROUNDS = 12


def main():
    try:
        sd.query_devices(kind="input")
    except Exception:
        print("skipped: no microphone")
        return
    raw = "--raw" in sys.argv
    if raw:
        ears.PA_LOCK = contextlib.nullcontext()
    out = sd.OutputStream(samplerate=24000, channels=1, dtype="int16")
    out.start()
    zeros = np.zeros(2400, dtype=np.int16)

    done = threading.Event()

    def feeder():                       # idle kokoro output keeps its IO proc busy
        while not done.is_set():
            out.write(zeros)
    fd = threading.Thread(target=feeder, daemon=True)
    fd.start()
    E = ears.Ears()
    last = [time.time()]

    def watchdog():
        while True:
            time.sleep(5)
            if time.time() - last[0] > 60:
                print(f"DEADLOCK: no progress for 60s "
                      f"({'raw' if raw else 'guarded'} path)", flush=True)
                os._exit(2)
    threading.Thread(target=watchdog, daemon=True).start()
    t0 = time.time()
    for i in range(ROUNDS):
        claimed = threading.Event()
        held = threading.Event()
        held.set()
        t = threading.Thread(target=lambda: E.listen_once(
            abort=claimed.is_set, timeout_s=10), daemon=True)
        t.start()
        time.sleep(0.5 + (i % 5) * 0.5)      # the room, then the key
        claimed.set()                         # abort + open at once
        r = threading.Thread(target=lambda: ears.record_held(
            held.is_set, max_s=0.3, min_s=10), daemon=True)   # a tap: no whisper
        r.start()
        time.sleep(0.1)
        held.clear()
        r.join()
        t.join()
        last[0] = time.time()
        print(f"round {i + 1}/{ROUNDS} ok", flush=True)
    done.set()                            # never exit with a write in flight
    fd.join()
    with ears.PA_LOCK:
        out.stop()
        out.close()
    print(f"ok: {ROUNDS} collisions survived in {time.time() - t0:.1f}s "
          f"({'raw' if raw else 'guarded'} path)")


if __name__ == "__main__":
    main()
