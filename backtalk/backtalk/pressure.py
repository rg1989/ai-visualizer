"""Evict what can be re-made when the Mac itself says it is short of memory.

The models and caches here are the biggest thing on a 16 GB machine, and
most of the day nobody is speaking. stt_cache_mb bounds the scratch cache;
this bounds everything else, but only when it matters. macOS publishes
kern.memorystatus_vm_pressure_level: 1 normal, 2 warn, 4 critical. At 1
nothing is touched and every turn stays warm. At 2 the scratch caches go
(MLX Metal, torch MPS): a few hundred MB, ~15 ms on the next utterance.
At 4, if no turn is live, the models themselves are dropped -- whisper,
kokoro, the speaker model -- and each module's warm() brings its own
back on the next turn, ~3 s once, instead of the whole machine swapping.
Off with "evict_on_pressure": false.

ponytail: a 5 s sysctl poll, not a libdispatch memory-pressure source.
Wire the dispatch source up if 5 s ever proves too slow to matter.
"""
import gc
import subprocess
import threading
import time

from backtalk.config import CFG
from backtalk.vlog import log


def level() -> int:
    try:
        out = subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                             capture_output=True, text=True, timeout=2).stdout
        return int(out.strip() or 1)
    except Exception:
        return 1


def drop_caches() -> list[str]:
    freed = []
    try:
        import mlx.core as mx
        mb = mx.get_cache_memory() // 1048576
        mx.clear_cache()
        freed.append(f"mlx {mb} MB")
    except Exception:
        pass
    try:
        import torch
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
            freed.append("mps")
    except Exception:
        pass
    return freed


def drop_models() -> list[str]:
    from backtalk import ears, mouth, voiceid
    dropped = []
    with ears._model_lock:
        if ears._model is not None:
            ears._model = None
            try:                      # mlx_whisper keeps its own handle too
                from mlx_whisper.transcribe import ModelHolder
                ModelHolder.model = ModelHolder.model_path = None
            except Exception:
                pass
            dropped.append("whisper")
    with mouth._pipe_lock:
        if mouth._pipe is not None:   # _pipe_lang stays: warm() must not re-sweep espeak
            mouth._pipe = None
            dropped.append("kokoro")
    with voiceid._model_lock:
        if voiceid._model is not None:
            voiceid._model = None
            dropped.append("voiceid")
    gc.collect()
    drop_caches()
    return dropped


def evict(lvl: int, idle: bool) -> str:
    """What one pressure reading does. Returns the log line, '' if nothing."""
    if lvl >= 4 and idle:
        d = drop_models()
        return f"critical: dropped {', '.join(d) or 'nothing (already cold)'}"
    if lvl >= 2:
        f = drop_caches()
        return f"warn: cleared {', '.join(f) or 'nothing'}"
    return ""


def watch(is_idle, period_s: float = 5.0):
    last = 1
    while True:
        time.sleep(period_s)
        lvl = level()
        if lvl >= 2 and (lvl != last or lvl >= 4):
            try:
                idle = bool(is_idle())
            except Exception:
                idle = False          # unsure means a turn may be live: caches only
            msg = evict(lvl, idle)
            if msg:
                log(f"[pressure] level {lvl} -> {msg}")
        last = lvl


def start(is_idle):
    if not CFG.get("evict_on_pressure", True):
        return
    threading.Thread(target=watch, args=(is_idle,), daemon=True,
                     name="pressure").start()
