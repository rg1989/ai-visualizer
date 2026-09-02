"""Fetch a spread-out slice of the 17 GB ACAV100M negative-feature file with HTTP
byte ranges and write it as a valid .npy (same dtype/shape layout), so a runner
with 14 GB of disk can train.  Usage: slice_acav.py OUT.npy N_CHUNKS PER_CHUNK
ponytail: contiguous chunks spread evenly across the file; the full 2,000 h needs 17 GB."""
import ast, sys, urllib.request
import numpy as np

URL = ("https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/"
       "openwakeword_features_ACAV100M_2000_hrs_16bit.npy")


def fetch(lo, hi):
    req = urllib.request.Request(URL, headers={"Range": f"bytes={lo}-{hi}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        assert r.status == 206, r.status
        return r.read()


def main(out, n_chunks, per_chunk):
    head = fetch(0, 127)
    assert head[:6] == b"\x93NUMPY", head[:6]
    hlen = 10 + int.from_bytes(head[8:10], "little")
    meta = ast.literal_eval(head[10:hlen].decode("latin1").strip())
    n_total, *rest = meta["shape"]
    dtype = np.dtype(meta["descr"])
    ex = dtype.itemsize * int(np.prod(rest))          # bytes per example (3072)
    starts = np.linspace(0, n_total - per_chunk, n_chunks).astype(int)
    with open(out, "wb") as f:
        np.lib.format.write_array_header_1_0(
            f, {"descr": meta["descr"], "fortran_order": False,
                "shape": (n_chunks * per_chunk, *rest)})
        for i, s in enumerate(starts):
            lo = hlen + int(s) * ex
            blob = fetch(lo, lo + per_chunk * ex - 1)
            assert len(blob) == per_chunk * ex, (len(blob), per_chunk * ex)
            f.write(blob)
            print(f"chunk {i + 1}/{n_chunks} from example {s}", flush=True)
    a = np.load(out, mmap_mode="r")
    print("wrote", out, a.shape, a.dtype, "finite:", bool(np.isfinite(a[:1000].astype(np.float32)).all()))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
