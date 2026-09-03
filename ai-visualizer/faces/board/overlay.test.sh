#!/bin/bash
# bash faces/board/overlay.test.sh — the one check for ?overlay=1: the
# overlay snapshot is see-through at the corners and off to the side, and
# painted in the middle. Fails when the board wallpaper leaks back in,
# the back glow floods the desktop again, or the face stops drawing.
# Uses ?shot= (the deterministic still): an off-screen window never gets
# animation frames, so the live loop paints nothing in a snapshot.
# Needs swiftc (builds facewin once) and Pillow for the pixel read.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"; AV="$HERE/../.."
python3 -c "import PIL" 2>/dev/null || { echo "overlay.test.sh: needs Pillow (pip install pillow)"; exit 2; }
[ -x "$AV/bin/facewin" ] || swiftc -O "$AV/bin/facewin.swift" -o "$AV/bin/facewin"
PORT=8797
python3 "$AV/server.py" --no-open --mock speaking --port $PORT >/dev/null 2>&1 &
SRV=$!; trap 'kill $SRV 2>/dev/null' EXIT
for _ in $(seq 40); do curl -s --max-time 1 -o /dev/null "http://127.0.0.1:$PORT/state" && break; sleep .25; done
OUT="${TMPDIR:-/tmp}/overlay-test.png"
"$AV/bin/facewin" "http://127.0.0.1:$PORT/faces/board/?shot=speaking&t=4000" --overlay --snapshot "$OUT" --after 3
python3 - "$OUT" <<'PY'
import sys; from PIL import Image
im=Image.open(sys.argv[1]).convert("RGBA"); w,h=im.size
corners=[im.getpixel(p)[3] for p in [(2,2),(w-3,2),(2,h-3),(w-3,h-3)]]
side=im.getpixel((w//5,h//2))[3]
mid=im.getpixel((w//2,h//2))[3]
assert max(corners)==0, f"wallpaper leaked: corner alpha {corners}"
assert side==0, f"the face floods the screen: alpha {side} a fifth of the way in"
assert mid>0, "nothing drawn at the centre: where is the face?"
print(f"overlay ok: corners {corners}, side {side}, centre {mid}  ({w}x{h}) {sys.argv[1]}")
PY
