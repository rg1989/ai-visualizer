#!/bin/bash
# How the AGENT changes the way it listens.
#
# The spoken console already catches the plain phrasings ("open mic",
# "switch to wake word") without a model round trip. This is for
# everything else — "stop listening until I get back", "you can just
# listen while I cook" — where the brain understood the ask and needs a
# way to act on it.
#
# It writes the SAME file the face's picker writes, so the switch takes
# the same path: the poller in main.py consumes it, aborts the capture in
# flight, republishes the badge and says the confirm out loud. One lane,
# one behaviour, whether the choice came from a tap or a sentence.
#
# ponytail: the bus is this repo's root because that is what config.py
# defaults signals_dir to. A custom signals_dir needs BACKTALK_BUS set.
#
#   mic.sh open   always listening
#   mic.sh wake   say the name first
#   mic.sh ptt    mic closed until the talk key
set -eu
MODE="${1:-}"
case "$MODE" in
  open|wake|ptt) ;;
  *) echo "usage: mic.sh open|wake|ptt" >&2; exit 2 ;;
esac
BUS="${BACKTALK_BUS:-$(cd "$(dirname "$0")/.." && pwd)}"
[ -d "$BUS" ] || { echo "no bus dir: $BUS" >&2; exit 1; }
# atomic, like every other writer on this bus: a half-written token must
# never be read as a mode
printf '%s' "$MODE" > "$BUS/.voice_mode_pick.tmp"
mv "$BUS/.voice_mode_pick.tmp" "$BUS/.voice_mode_pick"
echo "listening mode -> $MODE (takes effect within a second, spoken)"
