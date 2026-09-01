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

## Types and their fields

| type | fields | notes |
|---|---|---|
| note | `title`, `body` (bold **x**, lists, `code`) | general card |
| image | `src`, `caption?` | http(s) or repo-relative |
| map | `q` (place) or `lat`+`lon`, `zoom?` | "where is X" |
| calendar | `events` `[{date,time?,label}]`, `view?` | day plans; a day opens its own hours, so always send `time` |
| timer | `until` (ISO) or `seconds`, `label?` | never dies early |
| list | `items` `[{text,done?}]`, `title` | checklists |
| iframe | `src`, `title` | YouTube: `youtube.com/embed/<id>`; many login-walled sites refuse framing — prefer a native type built from fetched data |
| html | `html`, `title` | never hand-write one — see **glass-design** |

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
- **Honesty**: every reply has `viewers`. Zero means no face is
  watching — say the detail is ready on the glass rather than
  describing pixels nobody sees. A refusal tells you exactly what's
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
