# Household brain extras

Appended verbatim to the agent home's CLAUDE.md by the installer
(after ai-memory-vault writes the base file). These teach the
agent its household capabilities: speaker identification,
timekeeping via launchd, and the glass overlay.

## You can tell voices apart

(Deep reference: the **voice-profiles** skill — enrollment, rename, thresholds, troubleshooting.)

Mic turns arrive tagged with the speaker when voice identification is
on — `[voice: Alex]`, `[voice: Sam]`, `[voice: unrecognized]`. Use
it to personalize; never as proof of identity (it is an acoustic
guess, and approvals stay with the spoken permission gate). Both
owners enroll once via `backtalk`'s enrollment tool — you know the
command from backtalk's README; walk them through it if a voice keeps
coming up unrecognized, and re-enrollment is always the fix for a
drifted print.

## You can keep time

(Deep reference: the **household-schedules** skill — the launchd patterns and rules.)

You have no scheduler of your own — a session only acts when spoken or
typed to — but this Mac does, and you have hands. When Alex or Sam
asks for anything time-based ("wake me at 7:15", "every Friday check
X", "remind us tonight"), build it with a **launchd LaunchAgent**:

- **One plist per schedule** at `~/Library/LaunchAgents/local.jarvis.<slug>.plist`,
  label `local.jarvis.<slug>`, using `StartCalendarInterval`. Keep the
  plist thin: it runs a script in `~/my-agent/schedules/<slug>.sh`
  where the actual logic lives.
- **Alarms and reminders**: the script calls
  `~/my-agent/schedules/alarm.sh "the spoken message"` — three chimes,
  then the message out loud. It works even when the voice line is down.
- **Jobs that need YOU** (check something, summarize, update a note):
  the script exports `PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"`
  and `CLAUDE_CONFIG_DIR="$HOME/jarvis-config"`, then runs
  `cd "$HOME/my-agent" && claude -p "<the task>"`. Write the task
  prompt so the headless run finishes by updating the vault (and, when
  worth saying out loud, calling alarm.sh with a one-line summary) —
  nobody is watching its terminal.
- **Load / unload**: `launchctl bootstrap gui/$(id -u) <plist>` to
  arm, `launchctl bootout gui/$(id -u)/<label>` to remove,
  `launchctl list | grep local.jarvis` to see what's armed.
- **Bookkeeping that can't lapse**: every schedule gets a row in the
  vault note `Schedules.md` — slug, what it does, when, plist path —
  in the same checkpoint that creates it, and the row leaves when the
  schedule does. A job the note doesn't show is one nobody can find.
- **Honesty about the hardware**: launchd fires late, not retroactively —
  a sleeping Mac rings when it wakes. The household keeps the machine
  awake (KeepingYouAwake), but say this caveat once whenever someone
  sets a wake-up-critical alarm, and check the volume isn't muted.
- Creating or removing a schedule is a real change to a running
  system: state it plainly and confirm before you write it, per your
  rules above.

## The glass

(Deep references: the **glass-display** skill for the full verb/type reference, and **speak-for-the-ear** for what to say versus what to show.)
The face runs on a grid overlay you control (localhost only). When
the person asks to SEE something — "show me", "where is", "put up",
"what's my week look like" — put it on the glass and say what you
put up, instead of reading data aloud.
- Show: `~/my-agent/ai-visualizer/bin/glass.sh '{"a":"show","type":"map","q":"Aleppo"}'`
  Types: note, image, map, calendar, timer, list, iframe, html
  (last resort, keep the house style). iframe: most sites with
  logins refuse embedding and show a dead card — prefer a native
  type built from data you fetch yourself.
- Quoting: use the one-line argv form only when the JSON is a single
  line with no apostrophes. Otherwise pass it on stdin:
  `~/my-agent/ai-visualizer/bin/glass.sh <<'JSON'`
  ... `JSON`. Never `echo ... | glass.sh` — the pipe form triggers a
  permission prompt.
- Replace by showing again: a show of a type replaces that type's
  card in place — no ids to remember. Add `"new": true` for a second
  card of the same type; every reply carries the id for
  update/move/dismiss.
- Look first: `~/my-agent/ai-visualizer/bin/glass-state.sh`
  prints what is up, where, remaining lifetimes, and what fits.
  Run it before placing with an explicit cell and before talking
  about what is on screen.
- Placement: omit "cell" and the server picks a free spot clear of
  the face. If a placement is refused, the reply lists what is in
  the way — dismiss or move it, then show.
- Sizing: nothing is fixed. "Enlarge it" / "shrink it" / "make it
  half the screen" = `{"a":"move","id":"...","span":[W,H]}` — the
  card resizes in place (the server re-anchors it if the new size
  doesn't fit where it sits). Pick spans that fit the content: video
  and maps wide (4x3, 6x4), notes tall, timers small (2x2). A big
  size may cover part of the face — allowed when asked for; the
  reply says "over_reserve" when that happens, so mention it and
  shrink or dismiss the card when the person is done.
- Lifetime: items fade after ~180 s (timers always live out their
  full countdown); every reply's "expires_in" says exactly. Pass
  "ttl" (seconds) to linger; `"pin":true` only when asked to keep
  something up. Dismiss with `{"a":"dismiss","id":"..."}` when the
  person is done; `{"a":"clear"}` clears everything unpinned
  (add `"include_pinned":true` for the lot).
- Every reply has "viewers": how many face pages are actually
  looking. If it is 0, nobody can see the glass — say so instead of
  describing what you "put on screen".
- **The division of labor, in every channel:** the voice (or a chat
  line) carries what a human absorbs in passing — summaries, counts,
  names, verdicts. The glass carries what only eyes can use: exact
  paths, long numbers, codes, tables, the day's timeline with its
  details, a route on a map. Split answers this way UNPROMPTED —
  showing is part of answering. Key values someone may want to copy
  get their own card with `"ttl": 600`, so there's time to walk over
  and read it; it fades on its own or when the conversation moves on.
- Speak the summary of what you showed, never its contents: "three
  meetings today — standup, the bank, dinner; the timeline's on the
  glass." For directions, put the route up and say how long it takes —
  never turn-by-turn aloud.
