"""The one check that fails if the STT scratch cache goes unbounded again.

MLX caches every Metal buffer it allocates and never shrinks. Measured on
an M-series Mac: 749 MB after one 3s clip, 1222 MB once utterance lengths
varied, 2475 MB in a session that had been up a day -- the largest single
block of memory the voice line held. ears.warm caps it; this proves the
cap survives audio of many LENGTHS, which is what defeated the default.

Run it directly: .venv/bin/python test_stt_cache.py
No-ops off Apple Silicon, where faster-whisper runs and there is no cache.
"""
import numpy as np

from backtalk import ears
from backtalk.config import CFG

CAP_MB = 256


def test_cache_stays_capped():
    if not ears._apple_gpu_available():
        print("skip: MLX/Metal path is Apple Silicon only")
        return
    import mlx.core as mx
    import mlx_whisper

    CFG["stt_cache_mb"] = CAP_MB
    ears.warm()
    repo = ears._mlx_repo(CFG["stt_model"])

    # Varied LENGTHS are the trigger: each new shape allocates its own
    # scratch buffers, and uncapped every one of them is kept forever.
    for secs in (3, 11, 27, 6):
        mlx_whisper.transcribe(np.zeros(16000 * secs, dtype=np.float32),
                               path_or_hf_repo=repo, language="en",
                               verbose=None)

    cache_mb = mx.get_cache_memory() / 1048576
    # A little slack: the limit bounds what is KEPT, not the peak in flight.
    assert cache_mb <= CAP_MB * 1.1, (
        f"cache grew to {cache_mb:.0f} MB against a {CAP_MB} MB cap -- "
        "the set_cache_limit call in ears.warm is gone or too late")
    print(f"ok: cache held at {cache_mb:.0f} MB under a {CAP_MB} MB cap")


def test_quant_falls_back():
    """A quantized conversion that is not on the Hub must not be fatal.

    stt_quant names a HuggingFace repo that may simply not exist for an
    unusual stt_model, and a machine can be offline with only the full
    weights cached. Either way the voice line has to come up.
    """
    if not ears._apple_gpu_available():
        print("skip: MLX/Metal path is Apple Silicon only")
        return
    ears._model = ears._backend = None          # force a fresh load
    CFG["stt_quant"] = "no-such-quantization"
    ears.warm()
    assert ears._backend == "mlx", "the fallback did not produce a usable model"
    assert ears._model == ears._mlx_repo(CFG["stt_model"]), (
        f"fell back to {ears._model!r}, expected the full-precision repo")
    print(f"ok: bogus quant fell back to {ears._model}")


def test_repo_names_and_bridge():
    """No GPU needed: the Hub names, and the weights-file bridge that lets
    mlx_whisper 0.4.3 open the Hub's newer conversions (model.safetensors)."""
    import os
    import tempfile
    assert ears._mlx_repo("small.en", "8bit") == \
        "mlx-community/whisper-small.en-mlx-8bit"
    assert ears._mlx_repo("large-v3-turbo") == \
        "mlx-community/whisper-large-v3-turbo"
    assert ears._mlx_repo("large-v3-turbo", "8bit") == \
        "mlx-community/whisper-large-v3-turbo-8bit"
    new = tempfile.mkdtemp(prefix="backtalk-test-")
    open(os.path.join(new, "model.safetensors"), "w").close()
    assert ears._bridge_weights(new), "no link for the new layout"
    assert os.readlink(os.path.join(new, "weights.safetensors")) == \
        "model.safetensors"
    assert not ears._bridge_weights(new), "bridged twice"
    old = tempfile.mkdtemp(prefix="backtalk-test-")
    open(os.path.join(old, "weights.npz"), "w").close()
    assert not ears._bridge_weights(old), "touched the old layout"
    assert not os.path.lexists(os.path.join(old, "weights.safetensors"))
    print("ok: Hub repo names, and the weights bridge links only the new layout")


def test_turbo_8bit_loads():
    """Opt in (BACKTALK_TEST_TURBO=1: an 824 MB download the first time):
    large-v3-turbo in 8-bit comes up through warm() on the Apple GPU --
    the one conversion that NEEDS the bridge -- and transcribes."""
    import os
    if os.environ.get("BACKTALK_TEST_TURBO") != "1":
        print("skip: set BACKTALK_TEST_TURBO=1 to load large-v3-turbo 8-bit")
        return
    if not ears._apple_gpu_available():
        print("skip: MLX/Metal path is Apple Silicon only")
        return
    ears._model = ears._backend = None
    CFG["stt_model"], CFG["stt_quant"] = "large-v3-turbo", "8bit"
    ears.warm()
    assert (ears._backend, ears._model) == \
        ("mlx", "mlx-community/whisper-large-v3-turbo-8bit"), (
        ears._backend, ears._model)
    import mlx_whisper
    r = mlx_whisper.transcribe(np.zeros(16000, dtype=np.float32),
                               path_or_hf_repo=ears._model, language="en",
                               verbose=None)
    assert "text" in r
    print(f"ok: {ears._model} loads through the bridge and transcribes")


if __name__ == "__main__":
    test_repo_names_and_bridge()
    test_cache_stays_capped()
    test_quant_falls_back()
    test_turbo_8bit_loads()
