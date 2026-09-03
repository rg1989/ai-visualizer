#!/bin/bash
# watch.sh start "what to watch for" [--source screen] [--for MINUTES] | stop | status
# "watch over the stove", "tell me if anyone comes in", "tell me when the
# build finishes" (--source screen). Runs watch.py in the background: a
# cheap change detector runs locally on the camera (LED lit) or the screen,
# and only a real change wakes the assistant with a few frames and the thing
# it was asked to watch for. Nothing is recorded. It switches ITSELF off
# after 30 minutes or 40 events (--for N minutes for longer), tells the
# voice line so, and a timer card on the glass counts it down. The assistant
# should SAY what is watching and when it stops by itself, and say when it
# stops; it must never restart or extend a watch unasked.
HERE="$(cd "$(dirname "$0")" && pwd)"
case "$1" in
  start) shift; nohup python3 "$HERE/watch.py" start "$@" > "${TMPDIR:-/tmp}/watch.log" 2>&1 & sleep 1; python3 "$HERE/watch.py" status; echo "log: ${TMPDIR:-/tmp}/watch.log" ;;
  stop|status) python3 "$HERE/watch.py" "$1" ;;
  *) echo "usage: watch.sh start \"what to watch for\" [--source camera|screen] [--for MINUTES] [--max-events N] [--threshold N] [--cooldown S] [--dry-run] | stop | status"; exit 2 ;;
esac
