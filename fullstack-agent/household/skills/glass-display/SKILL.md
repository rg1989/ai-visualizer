---
name: glass-display
description: Put things on the glass — the grid overlay on the face, equally called the dashboard, the display, the screen, the board. Use whenever showing beats saying — "show me", "put it on the dashboard", "where is", "put it up", a timeline, a map, a route, a key value someone will read or copy, enlarging/shrinking/moving/dismissing a card, or checking what is currently on screen.
---

# The glass

A 12×8 grid overlay on the face (columns A–L, rows 1–8). You control it
with two scripts; everything renders in the house style automatically.

**One surface, many names.** The glass, the dashboard, the display, the
screen, the board, the overlay — all the same grid. There is no other
dashboard in this house, so "show me X", "put X on the dashboard" and
"throw X up on the screen" are one instruction: a `glass.sh` show.
Never go hunting for another surface, and never answer a "show me" in
prose alone.

## The two commands

Show (one JSON verb per call — heredoc form when the JSON has
apostrophes or newlines; never `echo | pipe`):

    ~/my-agent/ai-visualizer/bin/glass.sh '{"a":"show","type":"map","q":"Aleppo"}'

Look (run BEFORE explicit placement and before describing the screen):

    ~/my-agent/ai-visualizer/bin/glass-state.sh

## After every `html` show, look again

An html card measures itself once it is on screen and glass-state.sh prints
a `rendered:` line under it: the text it actually shows, its largest font
size, `EMPTY` when nothing visible came out, `DARK TEXT` when the colour is
unreadable on the glass. Run glass-state.sh a second after the show and read
that line BEFORE telling the person it is up. "It should be visible now" is
not a report; `rendered: "23:14:05"  largest text 64px` is. A native type
(clock, timer, note, the components) needs no such check — prefer them.

## Looking at it

`glass-look.sh` (next to glass.sh) renders the glass off-screen to a PNG and
prints the path; Read that file and you see exactly what a viewer sees --
theme, sizes, overlap, the lot. Reach for it when the picture matters: a
layout or style change you were not sure of, "that looks wrong", or before
saying a visual change did what the person meant. Not after routine cards.
`cam-look.sh` grabs one webcam frame the same way: use it whenever seeing
helps (take a picture, identify or read something physical, check the room)
and announce it as you do. `watch.sh start "what to watch for"` keeps the
camera on and wakes you with frames only when the picture changes; add
`--source screen` to watch the Mac's screen the same way. A watch switches
itself off after 30 minutes or 40 events (`--for MINUTES` for longer) and says
so in a `[watch]` line; `watch.sh stop` ends it early, `watch.sh status` says
whether one runs. Say when a watch starts, when it will stop by itself, and
when it stops. Never restart or extend one unless the person asks.

## Types and their fields

| type | fields | notes |
|---|---|---|
| note | `title`, `body` (bold **x**, lists, `code`) | general card; sizes to its text (one short line = a big hero line in a 3x1 card) |
| image | `src`, `caption?` | http(s) or repo-relative |
| map | `q` (place) or `lat`+`lon`, `zoom?` | "where is X" |
| calendar | `events` `[{date,time?,label}]`, `view?` | day plans; a day opens its own hours, so always send `time` |
| timer | `until` (ISO) or `seconds`, `label?` | never dies early |
| list | `items` `[{text,done?}]`, `title` | checklists |
| image, live webcam | `src`: `cam.mjpg` | the glass server streams the camera itself while the card is up; dismiss it (or let it expire) and the camera light goes off |
| iframe | `src`, `title` | YouTube: `youtube.com/embed/<id>`; many login-walled sites refuse framing — prefer a native type built from fetched data |
| clock | `label?`, `format?` (`hms` default, `hm`) | the time of day, digits sized to the card — never an html card |
| sysmon | `label?` | the machine at a glimpse: CPU and RAM bars, thermal, disk, load, battery, uptime, live. The overlay pins one at A1 by default; "hide the system monitor" = dismiss it — never an html card |
| tasks | `label?` | your background work, live (bin/task.py shows it and it dismisses itself) — never show it by hand, never an html card |
| html | `html`, `title` | last resort — see **glass-design**. The card is a sandboxed iframe that inherits NOTHING: set `color` yourself (default is black on a dark card); give it `html,body{height:100%;margin:0}` or `height:100%` boxes collapse to nothing; size type with `container-type:size` + `cqh`, never `vw`/`vmin` and never by measuring boxes in JS (a box measured before it has a height returns ~0 and the font goes to 0). Then LOOK — see below |

## The rules that make it feel right

- **Placement**: omit `cell` — the server finds a spot clear of the
  face and the conversation pane. Pass `cell`+`span` only when the
  person asked for a position.
- **Sizing is free**: "enlarge it" = `{"a":"move","id":"map-1","span":[6,4]}`
  — resizes in place, re-anchors only if it must. Big sizes may cover
  part of the face; the reply then carries `over_reserve: true` —
  that's allowed when asked for, mention it, and shrink or dismiss
  when the person is done.
- **Replace, don't stack**: a `show` of a type replaces that type's
  card. `"new": true` makes a second one. Reuse a reply's `id` for
  update/move/dismiss.
- **Lifetime**: cards fade after ~180s. Key values someone may copy
  get `"ttl": 600`. `"pin": true` only when asked to keep something.
  `{"a":"dismiss","id":"..."}` when done; `{"a":"clear"}` clears the
  unpinned lot.
- **Update in place** for changing content (`{"a":"update","id":...,
  fields}`) — content only, no re-animation.
- **Honesty**: every reply has `viewers`, a number for you, never
  for the person. Zero means no face is open — say the detail is ready
  on the glass rather than describing pixels nobody sees. One or more:
  never mention viewers or anyone watching; the person is looking. A refusal tells you exactly what's
  in the way (`map`, `free`, `dismiss_candidates`) — act on it.

## Prebuilt cards

Rows and columns, big numbers, a service board, labelled values to copy —
don't hand-write an `html` card for those. The **glass-components** skill
has them ready-made: data in, themed card out. For a shape none of them
has, **glass-design** builds one from scratch in the same house style.

## When to reach for the glass unprompted

Timelines, routes/maps, exact values (codes, big numbers, paths),
tables, anything the person will re-read or copy. Speak the summary,
show the substance — see the speak-for-the-ear skill for the split.
