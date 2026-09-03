#!/bin/bash
# overlay.sh — the face and the glass over everything on screen: a
# borderless, transparent, click-through window on every Space, above
# full-screen apps, no browser. The board wallpaper stays home; the face
# and the cards come along. The browser view keeps working beside it.
#
#   bin/overlay.sh            needs the server up (server.py, or
#                             server.py --no-open when the tab is not wanted)
#
# One chord runs it, LEFT Option + LEFT Command (the right pair is taken):
# hold it and the overlay takes the mouse and the keys (Opt+Cmd+Enter
# talks, Opt+Cmd+1 picks a mic mode);
# tap it and the overlay shows. The voice shows it too. OVERLAY_IDLE
# seconds (default 10) with none of that and it fades out; "stop
# listening" fades it at once and keeps it down until she is addressed
# again. The kill line this prints closes it. Builds facewin once, like
# glass-look.sh. Check: bash bin/overlay-hush.test.sh
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
CFG="$HERE/../ai-visualizer.json"
PORT="${GLASS_PORT:-$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1])).get("port", 8790)))' "$CFG" 2>/dev/null || echo 8790)}"
FACE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("face") or "board")' "$CFG" 2>/dev/null || echo board)"
# anchored to the binary: a shell whose script merely mentions the words must not count
UP=$(pgrep -f "^[^ ]*/facewin .*--overlay" | head -1 || true)
if [ -n "$UP" ]; then echo "overlay.sh: already up (pid $UP)" >&2; exit 0; fi
if [ ! -x "$HERE/facewin" ]; then
  command -v swiftc >/dev/null || { echo "overlay.sh: needs the Xcode Command Line Tools (swiftc) to build facewin once" >&2; exit 2; }
  swiftc -O "$HERE/facewin.swift" -o "$HERE/facewin" >&2 || exit 2
fi
# the server may still be coming up (start.sh launches it a moment earlier)
for _ in $(seq 40); do curl -s --max-time 1 -o /dev/null "http://127.0.0.1:$PORT/state" && break; sleep .25; done
curl -s --max-time 2 -o /dev/null "http://127.0.0.1:$PORT/state" || { echo "overlay.sh: the face isn't running on :$PORT" >&2; exit 1; }
"$HERE/facewin" "http://127.0.0.1:$PORT/faces/$FACE/" --overlay --idle "${OVERLAY_IDLE:-10}" >/dev/null 2>&1 &
disown
echo "overlay up (pid $!)  hold LEFT Opt+Cmd to use it, tap to show it; close: kill $!"
# The machine at a glimpse sits top-left unless OVERLAY_SYSMON=0: pinned,
# so it survives restarts; "dismiss the system monitor" takes it down.
if [ "${OVERLAY_SYSMON:-1}" != "0" ] && ! curl -s --max-time 2 "http://127.0.0.1:$PORT/state" | grep -q '"type": *"sysmon"'; then
  "$HERE/glass.sh" '{"a":"show","type":"sysmon","cell":"A1","span":[2,2],"pin":true}' >/dev/null || true
fi
