"""MIT room impulse responses (270 wavs) and ~1.5 h of AudioSet background audio,
both as 16 kHz mono int16 wavs, read straight from Hugging Face parquet with
pyarrow + soundfile (the `datasets` pin in the notebook is what rots).
Usage: fetch_data.py [--rir-only]"""
import io, os, sys, urllib.request
import numpy as np, pyarrow.parquet as pq, soundfile as sf
from scipy.signal import resample_poly

here = os.path.dirname(os.path.abspath(__file__))
RIR = ("https://huggingface.co/api/datasets/davidscripka/MIT_environmental_impulse_responses/"
       "parquet/default/train/0.parquet")
BG = "https://huggingface.co/datasets/agkphysics/AudioSet/resolve/main/data/bal_train/09.parquet"


def decode(cell):
    x, sr = sf.read(io.BytesIO(cell["bytes"]), dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != 16000:
        x = resample_poly(x, 16000, sr)
    return x


def pull(url, out_dir, prefix, max_secs, normalize):
    os.makedirs(out_dir, exist_ok=True)
    tmp = os.path.join(here, "data", os.path.basename(url))
    if not os.path.exists(tmp):
        urllib.request.urlretrieve(url, tmp)
    pf = pq.ParquetFile(tmp)
    n = secs = 0
    for batch in pf.iter_batches(batch_size=64, columns=["audio"]):
        for cell in batch.column("audio").to_pylist():
            x = decode(cell)
            if normalize:
                x = x / max(1e-6, np.abs(x).max())
            sf.write(os.path.join(out_dir, f"{prefix}_{n:04d}.wav"),
                     (np.clip(x, -1, 1) * 32767).astype(np.int16), 16000, subtype="PCM_16")
            n += 1; secs += len(x) / 16000
            if secs > max_secs:
                break
        if secs > max_secs:
            break
    os.remove(tmp)
    print(f"{prefix}: {n} files, {secs / 3600:.2f} h -> {out_dir}", flush=True)
    return n


os.makedirs(os.path.join(here, "data"), exist_ok=True)
n = pull(RIR, os.path.join(here, "data", "mit_rirs"), "rir", 1e9, normalize=True)
assert n >= 200, n
if "--rir-only" not in sys.argv:
    n = pull(BG, os.path.join(here, "data", "background"), "bg", 3600 * 1.5, normalize=False)
    assert n >= 100, n
