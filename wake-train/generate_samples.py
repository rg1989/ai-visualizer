"""openWakeWord's train.py does `from generate_samples import generate_samples`
and calls it WITHOUT model=. rhasspy v3.2.0 moved the function into a package,
requires model=, and writes 22,050 Hz clips, which the trainer rejects.
This shim bridges all three. It is copied INTO the piper-sample-generator clone."""
import glob, os
from pathlib import Path
import numpy as np, soundfile as sf
from scipy.signal import resample_poly
from piper_sample_generator.__main__ import generate_samples as _gen

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODEL = os.path.join(_HERE, "models", "en_US-libritts_r-medium.pt")
_BREW = Path("/opt/homebrew/share/espeak-ng-data")
if _BREW.exists():  # the Mac case only; Linux uses the wheel's bundled data
    import piper.phonemize_espeak as pe
    pe.EspeakPhonemizer.__init__.__defaults__ = (_BREW,)


def generate_samples(text, output_dir, max_samples, batch_size=10, **kw):
    kw.pop("auto_reduce_batch_size", None)
    kw.setdefault("max_speakers", 600)
    _gen(text=text, output_dir=output_dir, model=_MODEL,
         max_samples=max_samples, batch_size=batch_size, **kw)
    for f in glob.glob(os.path.join(output_dir, "*.wav")):
        x, sr = sf.read(f, dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        if sr != 16000:
            x = resample_poly(x, 16000, sr)
            sf.write(f, (np.clip(x, -1, 1) * 32767).astype(np.int16), 16000, subtype="PCM_16")
