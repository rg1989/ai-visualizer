"""Merge the matrix gen shards (shard_*/out/shodan/<split>/*.wav) into one
out/shodan tree, prefixing file names so shards never collide."""
import glob, os, shutil, sys

root, dst = sys.argv[1], sys.argv[2]
SPLITS = ("positive_train", "positive_test", "negative_train", "negative_test")
counts = {}
for shard in sorted(glob.glob(os.path.join(root, "shard_*"))):
    tag = os.path.basename(shard)
    for split in SPLITS:
        os.makedirs(os.path.join(dst, split), exist_ok=True)
        for f in glob.glob(os.path.join(shard, "out", "shodan", split, "*.wav")):
            shutil.move(f, os.path.join(dst, split, f"{tag}_{os.path.basename(f)}"))
            counts[split] = counts.get(split, 0) + 1
print(counts)
assert all(counts.get(s, 0) > 0 for s in SPLITS), counts
