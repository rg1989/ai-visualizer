#!/bin/bash
# alarm.sh "message to speak" — the standard alarm bell for scheduled jobs.
# Three rising chimes, then the message out loud via the system voice.
# ponytail: system `say`, not the Kokoro pipeline — an alarm must fire even
# when the voice line is down; swap in a Kokoro one-liner if the household
# ever wants the same voice for alarms.
for _ in 1 2 3; do afplay "$HOME/my-agent/backtalk/assets/wake.wav"; sleep 0.3; done
[ -n "$1" ] && say "$1"
exit 0
