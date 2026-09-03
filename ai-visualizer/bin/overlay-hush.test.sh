#!/bin/bash
# bash bin/overlay-hush.test.sh — the one check for "stop listening" in
# overlay mode. The voice line marks the mic line hushed (.voice_mic
# "wake hush"); the overlay must fade to alpha 0 AT ONCE, even while the
# face is "speaking" (her "Stopped."), and wake again on the next
# un-hushed publish. Runs a private copy of the server on :8798 with its
# own bus files, so a live face is never touched. The test overlay IS on
# screen for about nine seconds: a window off every screen never animates.
# Needs swiftc (builds facewin once, plus a ten-line window-alpha reader).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"; AV="$HERE/.."
[ -x "$AV/bin/facewin" ] || swiftc -O "$AV/bin/facewin.swift" -o "$AV/bin/facewin"
T="$(mktemp -d "${TMPDIR:-/tmp}/overlay-hush.XXXXXX")"
trap 'kill $OV $SRV 2>/dev/null; rm -rf "$T"' EXIT
cat > "$T/alpha.swift" <<'SW'
import AppKit
// the window-server alpha of a pid's widest window (the panel, not a menu item)
let pid = Int32(CommandLine.arguments[1])!
let ws = CGWindowListCopyWindowInfo([.optionAll], kCGNullWindowID) as? [[String: Any]] ?? []
var best = (w: -1.0, a: -1.0)
for w in ws where (w[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid {
    let b = w[kCGWindowBounds as String] as? [String: Any] ?? [:]
    let wd = (b["Width"] as? NSNumber)?.doubleValue ?? 0
    if wd > best.w { best = (wd, (w[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? -1) }
}
print(best.a)
SW
swiftc -O "$T/alpha.swift" -o "$T/alpha" 2>/dev/null
cp -R "$AV/." "$T/av"; rm -f "$T/av/.voice_"* "$T/av/ai-visualizer.json"   # defaults: bus = its own dir
PORT=8798
echo speaking > "$T/av/.voice_state"; echo wake > "$T/av/.voice_mic"
python3 "$T/av/server.py" --no-open --port $PORT >/dev/null 2>&1 & SRV=$!; disown
for _ in $(seq 40); do curl -s --max-time 1 -o /dev/null "http://127.0.0.1:$PORT/state" && break; sleep .25; done
"$AV/bin/facewin" "http://127.0.0.1:$PORT/faces/board/" --overlay --idle 60 >/dev/null 2>&1 & OV=$!; disown
sleep 5; a1=$("$T/alpha" $OV)
echo "wake hush" > "$T/av/.voice_mic"; sleep 2.5; a2=$("$T/alpha" $OV)
echo wake > "$T/av/.voice_mic"; sleep 2; a3=$("$T/alpha" $OV)
echo "alpha: speaking $a1 -> hushed $a2 -> addressed again $a3"
python3 - "$a1" "$a2" "$a3" <<'PY'
import sys; a1, a2, a3 = map(float, sys.argv[1:])
assert a1 > 0.99, f"not awake while speaking (alpha {a1})"
assert a2 < 0.01, f"hush did not fade the overlay (alpha {a2})"
assert a3 > 0.99, f"did not wake again once the hush ended (alpha {a3})"
print("overlay hush ok")
PY
