---
name: glass-design
description: Design a one-off card for the glass from scratch, in the house style — the tokens, the type scale and the sizing idiom that make a bespoke card look like it shipped. Use when "show me X" lands on a shape no native type and no prebuilt component has: a day timeline, a gauge, a colour scale, a seating chart, a signal ladder. Read this before hand-writing any html card.
---

# Designing a card

Eight native types and five prebuilt components cover almost everything
anyone asks to see. This is for what is left — and for making it look like
it shipped with the others rather than like something you typed.

## First, don't

The catalogues are in **glass-display** (the eight native types) and
**glass-components** (`table`, `keyvalue`, `bars`, `stat`, `status`). Read
the shape you want off one of those tables before you read any further here.
Almost every "show me" is already a row in one of them.

**The test.** Write the card as a `note` first, in your head, in full. Then:

1. Must the type **grow when the card grows**?
2. Must a **colour carry meaning** the word alone cannot?
3. Must **two boxes sit side by side** and reflow with the card?
4. Must **one element own the whole card** and fill it at any span?

No to all four — ship the note. That is not a downgrade: a note is dressed
by the glass's own CSS, so it retunes on a theme change for free and you
never guess a size. Yes to (1) or (2) in a tile or row shape — that is
`stat` or `status`, already built. Yes, and no shipped card has that
shape — design it.

Not a yes: **it needs to scroll** (nothing scrolls) · **it needs a click or
a hover** (`hands.js` hit-tests the top document, so a hand gesture lands on
the iframe element and never enters it) · **it needs an icon** (so does
`status`, which uses `● ▲ ✕ ○`) · **it's a lot of data** (that is a `table`)
· **it needs a heading** (pass `title` — the chrome draws it).

## The call

`custom` runs your markup and your CSS through the same shell as `stat` and
`status`: tokens injected, reset applied, background transparent, status
colours defined.

    ~/my-agent/components/component.sh <<'JSON'
    {"component":"custom","id":"moon","title":"Moon","span":[3,2],"ttl":600,"data":{
      "css":"body{display:grid;grid-auto-rows:1fr}.r{container-type:size;min-width:0;overflow:hidden;display:flex;flex-direction:column;justify-content:center}.v{font-family:var(--glass-title-font);font-size:clamp(14px,42cqh,76px);line-height:1.05;color:var(--glass-accent);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.l{margin-top:.5em;font-size:clamp(8px,13cqh,16px);color:var(--glass-text-dim);text-transform:uppercase;letter-spacing:.06em}",
      "html":"<div class=r><div class=v>{{pct}}</div><div class=l>{{phase}}</div></div>",
      "values":{"pct":"63%","phase":"waxing gibbous"}}}
    JSON

- **`id` is mandatory** and names the *subject* — `moon`, `departures`,
  `today-line`. `custom` refuses to go up unnamed, because a moon card and a
  departures board answering to the same id are one card. Never `<type>-<n>`:
  that steals the shared auto-counter.
- **`span` is yours**, defaulting to `[3,3]` — nothing can guess it from
  arbitrary markup. Width 3, because the faces reserve their middle and a
  4-wide card often has nowhere to land. For N repeated units use the height
  `stat` and `status` use: `min(8, max(2, ceil(N * 2 / 3)))`.
- **Data never goes into `html` literally.** Every value from elsewhere — a
  filename, stdout, an API field, the person's own words — is `{{name}}` in
  the markup with the value in `values`, and arrives escaped by `e()`. An
  unknown `{{name}}` stays visible on the card, which is the bug report.
  Double-quote every attribute: escaping does not cover spaces.
- **Put CSS in `css`, never in a `<style>` inside `html`.** `css` has its
  `</style` sealed for you; a style block inside `html` is the one path the
  shell does not check.
- **Pass `ttl`.** A bespoke card dies at 180 s like any other, and `pin` is
  wrong here — the tokens bake at render time, so a pinned card comes back
  after a restart wearing the old theme.
- `--dry-run` prints the payload. `title`, `cell`, `ttl`, `new` ride at the
  top level, as in **glass-display**. `move` and `dismiss` are `glass.sh`
  verbs, not `component.sh` ones; to change content, re-run this same call
  with the same `id`.
- Omit `theme` and the card wears whatever face is live. Refusals are the
  glass's own (`no_room`, `over_reserve`) — read them and act.

## Tokens — consume, never hardcode

The chrome draws the border, its corner, the background, the title, the id,
the close button and every animation. Your fragment is the body, nothing more.

| token | for |
|---|---|
| `--glass-text` | primary copy |
| `--glass-text-dim` | labels, units, secondary detail |
| `--glass-accent` | the affirmative or highlighted thing — stroke or text, never a field behind text |
| `--glass-line` | the hairline hue |
| `--glass-line-dim` | any divider you draw |
| `--glass-radius-sm` | any inner box — never `--glass-radius`, that is the card's own corner |
| `--glass-title-font` | the one hero number, nothing else |
| `--glass-body-font` | everything else; already on `body` |
| `--ok` `--warn` `--crit` `--off` | the four status colours, fixed on every face |

Those, and nothing else — `--glass-gutter`, `--glass-margin` and
`--glass-blur` are **not** injected, so `padding:var(--glass-gutter)`
computes to no padding at all. The colour tokens move across four palettes,
so a literal `#3ddc84` is right on one and reads as a rendering bug on the
rest. The two font tokens never move. A recessed panel is `rgba(0,0,0,.35)`
with `--glass-radius-sm`. Never paint a background: the card's own shows
through at `.85` alpha and yours would double it.

**The house display faces live in the chrome only.** VT323, Satoshi and
Space Grotesk cannot load inside the frame, so `--glass-title-font` resolves
to its monospace fallback there. No card body renders in them — not yours,
not `stat`'s, not a note's — so this costs you nothing the shipped cards
keep.

## Layout — a 1fr row per unit, a size container on it

    body{display:grid;grid-auto-rows:1fr;gap:.8vmin}
    .r{container-type:size;min-width:0;overflow:hidden}
    .n{font-size:clamp(10px,34cqh,26px)}

Not optional. Each repeated unit is its own size container, the `1fr` track
gives it a real height, and type in `cqh` scales to **the box that unit
actually got** — four rows in a 3×2 card and ten in a 3×8 both fill the
space instead of clipping, with no count baked into the CSS. Put
`container-type:size` on a normal-flow div instead and every `cqh` resolves
against zero, every `clamp()` collapses to its floor, and the card renders
tiny at every size. Enlarging it changes nothing: that is the tell.

Side by side, reflowing:
`grid-template-columns:repeat(auto-fit,minmax(min(100%,110px),1fr))` — the
`min(100%,…)` is load-bearing, a bare `110px` overflows a narrow card. A row
with a glyph, a name and a state word is `grid-template-columns:auto 1fr auto`.
When one element owns the whole card, size it in `cqmin`.

**Type in `cqh`, space in `vmin`, never px for either.** The shell already
pads the body `2.5vmin`; gaps run `.8`–`1.4vmin`; offsets that track the type
run in `em`. These are the shipped values — match the row you are building:

| role | size | rest |
|---|---|---|
| hero number | `clamp(14px,42cqh,76px)` | title font, `line-height:1.05` |
| unit riding a hero number | `clamp(8px,15cqh,20px)` | dim, `margin-left:.35em` |
| badge riding a value | `clamp(7px,11cqh,14px)` | `700`, `.08em`, `margin-left:.45em`, `vertical-align:middle` |
| name, primary line | `clamp(10px,34cqh,26px)` | body font, `line-height:1.15` |
| secondary line under a name | `clamp(8px,24cqh,18px)` | dim, `line-height:1.15` |
| state word in its own column | `clamp(7px,22cqh,16px)` | `700`, `letter-spacing:.08em` |
| state glyph | `clamp(8px,30cqh,22px)` | `line-height:1`, own `auto` column |
| label under a value | `clamp(8px,13cqh,16px)` | dim, UPPER, `.06em`, `margin-top:.5em` |

Uppercase is for labels and state words only, never a value or a name, and
every uppercase run carries letter-spacing. `700` is the only weight in the
house — there is no 500 and no 600.

**Overflow.** Every grid or flex child holding data gets
`min-width:0;overflow:hidden`; every single-line run gets
`white-space:nowrap;overflow:hidden;text-overflow:ellipsis`. Both, every
time. The card cannot scroll and there is no pointer to reveal what was cut,
so an overrun is lost content, not a deferred read. If you cap the rows you
render, count it on the card — a dim `+3 not shown`, as the calendar does.
Never a bare blank.

## Marks — there is no icon library

No icon font, no sprite, no external SVG, and **no emoji** — its colour is
baked, it defeats the tokens and clashes with every palette. Draw it in CSS
if it is a box, circle or rule, sized in `em` so it tracks the type. Use a
glyph only from the set already in play — `● ▲ ✕ ○ ─ █ ░` — never outside
it: the frame has a system stack and nothing to fall back to. Otherwise
hand-author inline SVG, every stroke and fill `currentColor`, so the
parent's token colours it for free:

    <span style="color:var(--glass-accent)"><svg viewBox="0 0 30 30" width="1em"
     height="1em" fill="none" stroke="currentColor" stroke-width="1.2"><circle
     cx="15" cy="15" r="11"/><path d="M15 8v8l5 3" stroke-linecap="round"/></svg></span>

Hairlines stroke at 1–1.4, solid glyphs are `fill="currentColor"`, secondary
structure is opacity (`.42 .55 .6 .8`). Never a hex inside a glyph.

## The frame is an opaque origin

An iframe sandboxed `allow-scripts`, with no `allow-same-origin`. Hence:

- **The frame cannot reach this server at all** — not `assets/`, not
  `glass-theme.css`, not `/state`. The block is Private Network Access, not
  CORS, so adding CORS headers to `server.py` would not change it. Don't try.
- **Zero network references.** No `<img src>`, no `background-image:url()`,
  no CDN, no `fetch`. A public URL *will* load, which is worse — it works
  while you test, and `renderHtml` has no offline branch and no error path to
  catch it later. Inline SVG and `data:` URIs only.
- **Everything bakes at build time.** Nothing can be read back, and
  `localStorage` *throws* — guard it or the script dies mid-card.
- **Never lift the shell's `overflow:hidden`.** You can, at equal
  specificity; you would get a scrollbar nothing on this display can drive.
- **Static.** Its motion is the card's, which the chrome supplies. If you
  must animate, keep it finite, under 250 ms, and inside
  `@media (prefers-reduced-motion:reduce){*{animation:none!important}}` —
  the shell carries none.
- **Colour never travels alone.** A state is colour **and** shape **and**
  word — `OK` `WARN` `CRIT` `OFF`, verbatim. The accent is green on two of
  the four palettes, so green alone says nothing.

## What gets this wrong

| mistake | rule |
|---|---|
| `container-type:size` on a normal-flow div | it must be a child of a `1fr` grid track, or `cqh` is zero |
| fixed px type, hand-picked span | scale in `cqh` so a bad span shrinks type instead of losing rows |
| no `min-width:0` | one long name blows the grid and the right column vanishes |
| a hex where a token belongs | four palettes; three of them will look broken |
| `var(--glass-gutter)`, `--glass-margin`, `--glass-blur` | not injected — the declaration is dropped |
| `background:var(--glass-bg)` | `.85` on `.85` — a darker rectangle inset in the card |
| naming a house font | silent fall to mono; the chrome above it does not match |
| data pasted into `html`, or a value in an unquoted attribute | `{{name}}` + `values`, and double-quote the attribute |
| growing content and card in one call | re-run `component.sh` for content; resize with `glass.sh` `{"a":"move","id":…,"span":[w,h]}` |

## Promoting it

A bespoke card is a draft of a component. Move it into `render.py` once you
have built the same shape three times, once someone else's numbers would
fill its `data` keys untouched, or once the person asks for it back by name.
Then follow **glass-components → Adding one**, and delete the bespoke recipe:
two sources of one card is how drift starts.

## Worked example — a day timeline with a now line

The calendar renders months and weeks. It has no day view and no time axis,
and four dated rows are not a `list` — nothing here is tickable.

    ~/my-agent/components/component.sh <<'JSON'
    {"component":"custom","id":"today-line","title":"Today","span":[3,3],"ttl":600,"data":{
      "css":"body{display:grid;grid-auto-rows:1fr;gap:.8vmin}.r{container-type:size;display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:1.4vmin;min-width:0;overflow:hidden;border-top:1px solid var(--glass-line-dim)}.h{font-size:clamp(8px,24cqh,18px);color:var(--glass-text-dim);line-height:1.15}.e{font-size:clamp(10px,34cqh,26px);line-height:1.15;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.now{border-top-color:var(--glass-accent)}.now .h,.now .e{color:var(--glass-accent)}.w{font-size:clamp(7px,22cqh,16px);font-weight:700;letter-spacing:.08em;color:var(--glass-accent)}",
      "html":"<div class=\"r\"><span class=\"h\">09:00</span><span class=\"e\">{{a}}</span></div><div class=\"r now\"><span class=\"h\">11:20</span><span class=\"e\">{{b}}</span><span class=\"w\">NOW</span></div><div class=\"r\"><span class=\"h\">13:00</span><span class=\"e\">{{c}}</span></div><div class=\"r\"><span class=\"h\">16:30</span><span class=\"e\">{{d}}</span></div>",
      "values":{"a":"standup","b":"Ilya & Sons — kitchen quote","c":"lunch with R","d":"collect the car"}}}
    JSON

Read it back against the rules: four `1fr` rows and a size container on each,
`[3,3]` because `ceil(4 × 2 / 3)` is 3, type in `cqh` at the shipped sizes,
gaps in `vmin`, `min-width:0` and an ellipsis on the line that can run long,
the divider on `--glass-line-dim`, the now state carried by colour *and* the
word, every name a `{{value}}`, no background, no font named, nothing fetched.
