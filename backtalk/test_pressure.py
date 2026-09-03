"""The one check that fails if pressure eviction breaks a model or a reload.

Whisper is the model under test: the kokoro and speaker-model branches are
the same three lines each and are not warmed here (1.3 GB, slow). Run it
when nothing else is loading models: .venv/bin/python test_pressure.py
No-ops off Apple Silicon.
"""
import sys
import numpy as np

from backtalk import ears, pressure


def test_warn_keeps_models_critical_drops_then_reloads():
    if not ears._apple_gpu_available():
        print("skip: MLX path is Apple Silicon only")
        return
    ears.warm()
    assert ears._model is not None
    nb = sys.modules.get("numba")
    assert nb is not None and not hasattr(nb, "__file__"), "numba stub not in place"

    assert pressure.evict(2, idle=True).startswith("warn"), "level 2 must only clear caches"
    assert ears._model is not None, "warn dropped a model"
    assert pressure.evict(4, idle=False).startswith("warn"), "critical mid-turn must not drop models"
    assert ears._model is not None, "dropped a model mid-turn"

    msg = pressure.evict(4, idle=True)
    assert "whisper" in msg, msg
    assert ears._model is None
    from mlx_whisper.transcribe import ModelHolder
    assert ModelHolder.model is None, "mlx_whisper still holds the model"

    ears.warm()
    assert ears._backend == "mlx" and ears._model is not None, "reload failed"
    out = ears.transcribe(np.zeros(16000, dtype=np.int16))   # a second of silence
    assert isinstance(out, str), "reloaded model does not transcribe"
    print(f"ok: warn kept models; critical dropped whisper ({msg}); warm() reloaded it")


if __name__ == "__main__":
    test_warn_keeps_models_critical_drops_then_reloads()
