#!/bin/bash
# glass-look.sh — a picture of the glass, for an agent that cannot see its
# own screen. Renders the configured face page off-screen (facewin, WebKit,
# no browser, no screen-recording permission, no focus steal) and prints
# the PNG path. Then READ that file: the Read tool hands a PNG to a vision
# model as an image. Use it when a visual result matters -- after a layout
# or style change you were not sure about, when the person says something
# looks off -- not after every routine card: glass.sh's own `rendered:`
# line already says whether a card came out legible.
#
#   glass-look.sh              -> /tmp/.../glass-look.png
#   glass-look.sh out.png      -> that path
#   GLASS_PORT=8799 glass-look.sh   (tests)
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
CFG="$HERE/../ai-visualizer.json"
PORT="${GLASS_PORT:-$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1])).get("port", 8790)))' "$CFG" 2>/dev/null || echo 8790)}"
FACE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("face") or "board")' "$CFG" 2>/dev/null || echo board)"
OUT="${1:-${TMPDIR:-/tmp}/glass-look.png}"
if [ ! -x "$HERE/facewin" ]; then
  command -v swiftc >/dev/null || { echo "glass-look.sh: needs the Xcode Command Line Tools (swiftc) to build facewin once" >&2; exit 2; }
  swiftc -O "$HERE/facewin.swift" -o "$HERE/facewin" >&2 || exit 2
fi
curl -s --max-time 2 -o /dev/null "http://127.0.0.1:$PORT/state" || { echo "glass-look.sh: the face isn't running on :$PORT" >&2; exit 1; }
"$HERE/facewin" "http://127.0.0.1:$PORT/faces/$FACE/" --snapshot "$OUT" --after "${GLASS_LOOK_AFTER:-3}" || { echo "glass-look.sh: snapshot failed" >&2; exit 3; }
# Retina renders 2560x1600 at ~5 MB; a vision model needs a third of that.
sips -Z 1400 "$OUT" >/dev/null 2>&1 || true
echo "$OUT"
echo "glass-look.sh: the glass as a viewer sees it right now. Read this file to look at it." >&2
