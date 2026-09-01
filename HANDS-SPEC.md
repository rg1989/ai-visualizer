# HANDS-SPEC — hand control for the AI Visualizer

Status: draft, branch `feat/hands-on-glass`.
Companion to [GLASS-SPEC.md](GLASS-SPEC.md). Prior art: `barehands/stage.html`.

---

## 1. The goal

Drive the running app — the face page, the glass overlay, the settings
screen — with a hand in the air. No mouse, no keyboard.

## 2. The one design decision

**Hands are a pointer, not a feature list.**

The obvious build is a gesture-per-feature table: a gesture to open
settings, a gesture to switch theme, a gesture to stop the agent. That
table is wrong. Every one of those controls is already a DOM element that
already listens for `click`. A hand that emits real pointer events gets all
of them for free and stays correct when a control is added, moved, or
removed.

So: **one cursor, one pinch, synthesized `pointerdown`/`pointerup`/`click`.**

| App surface | How it works | Code needed |
|---|---|---|
| Settings screen (Esc) buttons, tabs, SAVE | click | none |
| Theme buttons on the gallery | click | none |
| Face cards on the gallery | click (`<a href>`) | none |
| SND / speaker toggle | click | none |
| Maps pan, iframes scroll inside a glass card | drag | none |
| **Glass cards: move / resize** | **not DOM state — lives on the server** | **the only special case** |

One exception is the whole feature. The rest is a cursor.

## 3. The glass exception

Glass cards are rendered from the `/state` payload and mutated only through
`POST /cmd`. Dragging the DOM node would be a lie the next poll erases. So
the drag is **optimistic locally, authoritative on release**:

```
pinch on .glass-card  →  ghost transform follows the cursor
release               →  POST /cmd {"a":"move","id":…,"cell":…}
                      →  ghost cleared; next /state poll re-places it for real
```

The server already refuses collisions and out-of-bounds moves, and already
FLIPs the card between grid slots. A refused move simply snaps back — the
card never moved in the payload, so nothing has to be undone.

| The gesture | Verb |
|---|---|
| Grab anywhere on the card, drag | `move` with the snapped `cell` |
| Grab the card's bottom-right corner | `move` with a new `span` |
| Grab it, then **pinch with the other hand and pull** | `move` with `cell` **and** `span` |

The two-hand stretch sets the footprint to the bounding box of the two
cursors, snapped to cells. One card, one grab: a second pinch while a hand
is already holding something is the stretch corner, never a second drag and
never a click. The size sticks after the second hand lets go, so you can
size it and then slide it.

**The snap ROUNDS, it does not floor.** A grab begins with the card's corner
sitting exactly on a cell boundary, which is precisely where `floor()`
flips — a hand holding still is not still, and every card stepped a whole
cell up-left on as little as two pixels of shake. Rounding starts the drag
in the middle of its own basin, so it takes half a cell of real movement to
change anything. `cellAt` still floors: asking which cell contains a point
is a different question from asking where a card should land.

**The mouse drags the same cards, through the same code.** `beginDrag` /
`onDragMove` / `finishDrag` are shared: a pinch drives them via
`onPinch`/`onRelease`, a mouse drives them straight from its own events.
There is no second implementation, so the grid math, the ghost and the POST
cannot drift apart between the two devices — and a card behaves identically
under a hand and under a cursor. The mouse half runs whether or not the
camera is on, which is why the ghost layer is built at load rather than at
`start()`.

**A press is a CLICK until it proves otherwise.** The card is its own drag
handle, so grabbing on the press made everything inside a card unreachable:
the release was always a drag, and the click branch under it was dead code.
A pinch is now only *armed* — nothing lifts, ghosts or moves — and becomes a
drag when the hand travels past `tapPx` (26) or outlasts `tapMs` (300).
Release before either and it is a click. `tapCheck()` is the regression.

**The cursor lets go of the fingertips as they close.** This is the one that
made delicate work impossible, and it is geometry, not noise: the cursor is
the point *between* index tip and thumb tip, so closing them translates it by
up to half the gap — tens of pixels of real motion that no filter can touch,
arriving during the very gesture meant to select something.

So the moment the gap starts closing — `gapNear` (0.60), well above the grab
bars, and `closeDrop` (0.05) under its own running average to prove it is
closing rather than resting — the cursor stops listening to the fingertips.
It snaps back to where the hand was aiming and from then on rides the **palm**
(the wrist-to-middle-knuckle centre), which a pinch cannot move at all. The
hand still carries it, so a held pinch still drags; the fingers no longer can,
so a click stays put.

**It is a transient, not a state — and that distinction shipped wrong once.**
A release hysteresis was tried here ("stay frozen until the fingers open back
past `gapNear`"), by analogy with the pinch gate. It is wrong: a pinch *is* a
state, this is not. A relaxed hand often rests at a gap ratio well under
`gapNear`, so the latch caught on the first small finger movement and never
released — everything after that steered by palm, at palm gain, with no
fingertip articulation. Ordinary movement went slow and clunky with nothing on
screen to say why. `closing` is self-limiting instead: the gap average catches
up within a few frames of the fingers stopping, so a hand that stops closing
gets the steering straight back, and a pinch that completes is held by `want`.

Measured on the fixture, which opens and closes by moving the thumb alone:
**the fingertips travel 179px and the cursor moves 0**; a hand parked
half-closed gets the steering back in 6 frames and stays on the fingertips
through a sweep. `holdCheck()` asserts both scenarios on separate cursors —
the parked one must never pinch, or it would pass for the wrong reason.

`gapNear: 0` switches the pre-emptive freeze off entirely and holds only once a
pinch registers. It is live-tunable: `AVHands.T.gapNear = 0` under
`?handsdebug=1`, which also prints `HELD` in the HUD whenever the freeze is
engaged — the readout that would have caught the latch immediately.

`aim` (0.5) is the blend between index tip and thumb tip. Lower it toward 0 to
aim with the index tip alone — a point the thumb cannot move — if *aiming*
still wanders. Clicking is the freeze's job.

**A tap lands where you AIMED, not where the pinch did.** Closing the fingers
pulls the thumb/index midpoint, and that midpoint *is* the cursor — so by the
time a pinch is detected the cursor has already slid off the target. When the freeze
above has already caught it, the frozen point *is* the aim point. `anchorMs`
(120) is the fallback for a pinch snapped shut too fast for the freeze to see
coming: the tap resolves against the position that long before detection, read
back out of the history the cursor already keeps.

Inside a card the same law runs again at the click site (`onTap` in glass.js),
because the mouse has the identical ambiguity: a press that travelled more
than 6px between `mousedown` and `mouseup` was a drag of the card, not a
click on what it started on. Both input paths are measured on the down/up
pair, the one pair both of them emit at real coordinates.

**Dismiss is not a gesture.** Every card wears an X in its own header, so it
closes with a mouse or with a pinch — the hand cursor synthesizes a real
click, so the button needs no gesture of its own. That is strictly better
than the corner-fling it replaced: a corner you can hit by grabbing slightly
off-centre is worse than a button you have to aim at. The X posts the same
`dismiss` the agent would, so the payload stays the source of truth and a
refusal simply leaves the card standing.

`pin` gets no gesture either. It has no shape in the air, and it is one
sentence to the agent. Skipped on purpose.

## 4. Grid math

`#glass-layer` is `position:fixed; inset:0` with `repeat(12,1fr)` /
`repeat(8,1fr)`, `gap: --glass-gutter`, `padding: --glass-margin`. Cell size
is derived from the layer's own rect and computed style, so a theme that
changes the gutter needs no work here:

```
cw = (rect.width  - 2*pad - 11*gap) / 12
ch = (rect.height - 2*pad -  7*gap) / 8
col = clamp(floor((x - rect.left - pad) / (cw + gap)), 0, 11)
cell = "ABCDEFGHIJKL"[col] + (row + 1)
```

## 5. Tracking

Lifted from `barehands/stage.html`, unchanged where it earns it:

- MediaPipe `HandLandmarker`, tasks-vision 0.10.14 from jsDelivr,
  `numHands: 2`, `runningMode: "VIDEO"`, GPU delegate.
- `minHandDetectionConfidence: 0.7` (the busy-background ghost-hand wall),
  `minHandPresenceConfidence: 0.5` (rides through motion blur).
- Cursor = midpoint of index tip (8) and thumb tip (4), **x mirrored** so
  the hand and the cursor move the same way.
- **The cursor is one-euro filtered, not EMA'd.** A fixed alpha has to be one
  compromise for two jobs it cannot both do: low enough to kill the tremor of
  an arm held out in the air, high enough not to lag a hand crossing the
  screen. 0.45 was the middle, and the middle is why anything small was
  unhittable. A one euro filter (Casiez, Roussel & Vogel, CHI 2012) picks the
  alpha per frame from the hand's own speed: `minCut` (0.7 Hz) is what a
  motionless hand gets, `beta` (0.015 Hz per px/s) is how fast it opens up.
  Measured against the constant it replaced: **±1.0px of wander at rest
  instead of ±2.7px, and 8px of lag at 900px/s instead of 37px** — steadier
  AND quicker, which is the only reason it earns its twenty lines. It is
  dt-aware, so a dropped camera frame no longer silently changes the
  smoothing. No dead zone: a dead zone buys stillness with a stick-then-jump
  that is worse than the jitter. `filterCheck()` asserts all three claims.
- **Frame -> screen is not 1:1.** MediaPipe only reports landmarks near 0 or
  1 when the hand is at the very edge of frame, where it stops being
  detected — so mapping the raw range to the screen makes the corners cost a
  hand you cannot afford to move that far. Two knobs fix it:
  - `reach` (0.15) trims that fraction off each frame edge and maps the rest
    to the full screen. Past it, the cursor pins to the screen edge.
  - `expo` (0.70) bends the curve: **slope exactly 1.0 at the centre**, where
    you aim, rising to **2.1x at the edges**, where you just need to arrive.
    The curve is odd about the centre, so mirroring x stays a plain
    `1 - axis(x)`.
- Pinch gate, ported whole. It is **five** rules, not one, and every one of
  them earns its place:
  1. **Shape** — the v3.8.x contrast law: index curls in to the thumb while
     middle/ring/pinky arch out, `back - f8 > 0.18` with a `1.30` back-arch
     floor; or the profile regime, `aspect < 2.0 && tRel > 0.95`.
  2. **Touch** — an entry gap ceiling, `ratio < 0.32` frontal / `0.38`
     rotated. The shape rule proves the POSE; this proves the fingers are
     actually together. **Omitting it is what made the cursor oscillate:** a
     lawful OK-sign with the fingers apart entered a pinch, tripped the
     release bar at `ratio >= 0.55` on the very next frame, and re-entered —
     pinch/release/pinch, three frames a cycle, forever.
  3. **Sustain** — two consecutive clean frames, or a charged EMA above
     `0.55` for a hand whose approach was messy. One lucky frame buys
     nothing.
  4. **Sanity** — `aspect > 6` means the knuckle row has collapsed, which no
     real hand does. The tracker is guessing, and a guess can neither start
     nor keep a grab.
  5. **Probation** — closing a hand into a curl passes THROUGH a lawful
     OK-sign for a frame or two, and hysteresis would then hold that bogus
     grab. A fresh pinch must keep its signature for 400 ms or it is dropped
     silently: no click, no move posted. Skipped at speed (blur would
     false-trip it) and for a hand already carrying a card (that grip is
     allowed to look like a fist).

  Release is speed-aware (`0.70` fast / `0.55` slow) and only needs two
  sustained frames at speed — a slow hand lets go the moment it opens, or
  releasing feels sticky.

**These numbers were fitted to another person's hand.** They live as named
constants at the top of the file with a `?handsdebug=1` HUD that prints the
live values, because a threshold you cannot see is a threshold you cannot
tune. This is the calibration knob, not a magic number.

Two hands are tracked. The second one is a second cursor and nothing more —
no two-hand gestures in v1.

## 6. Boot and consent

- Off by default. The switch lives in the settings screen (Esc) on the
  **LOOK & LISTEN** tab, next to the theme and the mic mode; **H** and
  `?hands=1` still work. The choice is remembered in `localStorage.av_hands`.
- It applies on click, not on SAVE: a camera prompt has to belong to the
  click that asked for it, and one fired later from SAVE reads as the page
  asking unprompted.
- While on, the preview is a **glass card** — the same classes glass.js
  builds, so it wears the theme's hairline, blur and radius for free.
  Default K7, two by two, and it behaves like every other component: move
  it, corner-resize it, two-hand stretch it, close it with its X (which
  turns hands off, since a camera running behind no preview is what nobody
  wants). It lands **locally** rather than through `/cmd` — it is not a
  server item — and the box is remembered in `localStorage.av_hands_cell`
  as `[col, row, w, h]`; the older two-element cell-only form still loads.
  It is an overlay the server does not know about, so it can sit over a
  real card; the fix, if that ever matters, is a reserved cell in server.py.
- The cursor is `--glass-line`, the same colour as the hairline of the cards
  it is reaching for, so it follows the theme.
- The camera prompt fires on first enable only. No camera is opened by
  simply loading a face.
- Camera frames never leave the page: MediaPipe runs in WASM in the tab,
  exactly as it does in barehands.

## 7. Failure rule

Same as the glass (§8 there): any exception tears down the hands layer with
one console line, releases the camera, and leaves the face and the glass
running. **The face never pays for a hands bug.** No hand in frame for 2 s
hides the cursor; the app is a normal mouse app again with nothing to
dismiss.

## 8. Non-goals

Voice-plus-gesture fusion. Handwriting. A gesture to summon the settings
screen (H toggles the hands; Esc is one key). Anything that needs a second
model.

## 9. Files

| File | Change |
|---|---|
| `ai-visualizer/hands.js` | new — the whole feature |
| `ai-visualizer/core.js` | +1 line: load it under `?hands=1` |
| `ai-visualizer/glass.js` | `card.dataset.id` so the drag can name the card, and the X button every card wears |
| `ai-visualizer/glass-theme.css` | `.glass-x`, sized for a hand cursor's jitter rather than a mouse's precision |
| `ai-visualizer/core.js` | the HANDS row on the settings screen's LOOK & LISTEN tab |

No server change. `/cmd` already takes `move` and `dismiss`, already
enforces the grid, and is same-origin from the face page — which is exactly
why this lives here and not in the barehands board on `:8794`.

## 10. The check

Two things here can be silently wrong, and neither needs a camera to test.
The **grid math** (a cell off by one just looks like a normal move) and the
**pinch gate** (which shipped broken once — see §5 rule 2). Both are covered:

```
open  /faces/board/index.html?glassdemo=1&handsdebug=1
run   AVHands.check()
```

Every one of the 96 cell centres must round-trip to its own coordinates, and
every card the browser has actually laid out must sit within 1px of where
`cellRect` says it does. The gate half feeds five synthetic 21-landmark
hands through `readHand` and asserts:

| Hand | Must |
|---|---|
| lawful contrast, fingers **apart** | never grab (the oscillation regression) |
| lawful contrast, fingers **together** | grab and hold |
| pinched, then opening | release |
| collapsed knuckle row (`aspect > 6`) | never grab |
| grabbed, then signature dies | drop inside probation, post nothing |

It also asserts the frame->screen curve (centre stays 1:1, the margin
actually reaches the screen edge, still odd about the centre, monotonic) and
the **jitter regression**: every laid-out card, grabbed and shaken by a third
of a cell in each direction, must snap back to the cell it already occupies.

A card mid-FLIP is skipped: `glass.js` animates a slot change with a
temporary transform, so during those 300ms its rect legitimately disagrees
with its grid slot.

Verified 2026-09-01: all five gate cases, 96/96 cells, 8/8 fixture cards
(max drift 0.01px), live `move` / `move`-span through `/cmd`, the portrait
dragged K7 -> C2 and remembered, the X dismissing a **pinned** card, a real
mouse drag moving `A1 -> G5` with the camera off, and a two-hand stretch
taking `B2 2x2` to `B2 7x5`. The portrait resizes the same way: corner-drag
`9x6 -> 3x3 -> 8x6`, two-hand stretch to `C2 9x6`, restored at `G5 4x3`
across a reload, and the old two-element saved form migrates. The jitter fix was reproduced first: with
`floor`, all 8 cards stepped a cell on -2px of shake; with `round`, none
moved across +/-31px.
