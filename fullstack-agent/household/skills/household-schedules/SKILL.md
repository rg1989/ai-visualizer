---
name: household-schedules
description: Anything time-based — alarms, reminders, recurring checks, "wake me at", "every Friday", "in an hour", "remind us". Use to create, list, or remove the household's launchd schedules, or when a scheduled job misfires.
---

# Household schedules (launchd)

You have no scheduler of your own — the Mac does, and you have hands.
Every time-based request becomes a LaunchAgent.

## The pattern

- One plist per schedule:
  `~/Library/LaunchAgents/local.jarvis.<slug>.plist`, label
  `local.jarvis.<slug>`, `StartCalendarInterval` for timing. Keep the
  plist thin; the logic lives in `~/my-agent/schedules/<slug>.sh`.
- **Alarms/reminders**: the script calls
  `~/my-agent/schedules/alarm.sh "the spoken message"`
  — three chimes then the message, works even when the voice line is
  down.
- **Jobs that need YOU** (check something, summarize, update the
  vault): the script exports
  `PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"` and
  `CLAUDE_CONFIG_DIR="$HOME/jarvis-config"`, then
  `cd "$HOME/my-agent" && claude -p "<the task>"`. Write the task so
  the headless run ends by updating the vault (and calling alarm.sh
  with a one-line summary when worth saying aloud) — nobody watches
  its terminal.
- **Arm / disarm / list**:
  `launchctl bootstrap gui/$(id -u) <plist>` ·
  `launchctl bootout gui/$(id -u)/<label>` ·
  `launchctl list | grep local.jarvis`

## The rules

- Every schedule gets a row in the vault note `Schedules.md` — slug,
  what, when, plist path — created and removed in the same checkpoint
  as the schedule itself.
- Creating or removing a schedule is a real change to a running
  system: state it plainly and confirm before writing it.
- Honesty about hardware: launchd fires late, never retroactively — a
  sleeping Mac rings when it wakes. Say this once for any
  wake-up-critical alarm, and check the volume isn't muted. The
  household runs KeepingYouAwake to hold the machine open.
