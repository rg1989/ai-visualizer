"""20 clips of 'show dan' must come out 16 kHz mono int16. Fails fast if the
generator stack is broken, before hours are spent."""
import glob, os, sys, time, wave
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "piper-sample-generator"))
from generate_samples import generate_samples

out = "smoke_out"
t0 = time.time()
generate_samples(["show dan"], out, max_samples=20, batch_size=10)
files = glob.glob(f"{out}/*.wav")
assert len(files) == 20, files
for f in files:
    with wave.open(f) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2), f
dt = time.time() - t0
print(f"ok: 20 clips in {dt:.1f}s ({20 / dt:.1f} clips/s)")
