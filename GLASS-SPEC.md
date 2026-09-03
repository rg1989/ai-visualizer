# The Glass — spec v2

An agent-controlled overlay on top of the face: a transparent grid where
Jarvis manifests visual components on demand — a calendar, a map, a
note, a timer — with smooth animated entrances and exits, a consistent
design language, and full awareness of what is on screen and where its
own face is. "Show me" gets an answer on the glass, not a wall of
spoken text.

Prior art in this stack: barehands' board (verb API via `bin/board.sh`,
state readback via `bin/board-state.sh`, a CLAUDE.md block teaching the
agent when to reach for it). The Glass is that pattern, without hand
tracking, fused onto the face. Same design philosophy as everything
here: stdlib server, files and HTTP as IPC, no build step, everything
inspectable.

v2 incorporates the adversarial review: zero vendored libraries (v1's
four are gone), exact wire contracts for every payload, a security
story that is enforced rather than asserted, and verbs an agent can use
without remembering anything.

## 1. Architecture

```
backtalk (voice)                    ai-visualizer
   |  spoken turn                      server.py  (stdlib, 127.0.0.1:8790)
   v                                     |   adds: POST /cmd (mutations);
Jarvis (Claude session) --------------->|   /state gains a "glass" object
   |   bin/glass.sh '{"a":"show",...}'  |   holds: glass model (in memory,
   |   bin/glass-state.sh               |   one lock; pinned items mirrored
   v                                     |   to glass-state.json)
 CLAUDE.md block                      face page (faces/<id>/ pages only)
 (teaches the verbs)                    core.js polls /state?face=<id>
                                        ~8x/sec -> glass.js re-renders
                                        only when glass.rev changes
```

- **Server**: `server.py` grows `POST /cmd` (mutations). There is **no
  second read endpoint and no second poll loop**: when the glass is
  enabled, the `/state` payload — which every face already polls every
  120 ms — gains one `"glass"` object (§3.5). `core.js` exposes it as
  `AV.glass` exactly the way it already exposes `rate_limits` as
  `AV.rateLimits`; `glass.js` watches `AV.glass.rev` and touches the
  DOM only when it changes. Change latency is one poll (~125 ms).
- **Model**: lives in memory. Every access — `/cmd` mutations, lazy TTL
  pruning, and `/state` serialization — happens under one
  `threading.Lock` (the server is `ThreadingHTTPServer`; without the
  lock, auto-placement races itself and `json.dumps` can see a
  half-applied mutation). Pinned items and the id counter are mirrored
  to `glass-state.json` next to the config, written as
  `glass-state.json.tmp` then `os.replace()`d into place, inside the
  same lock. A restart restores pinned items only; ephemeral items die
  with the server — by definition. A missing or corrupt
  `glass-state.json` restores an empty glass and prints one
  plain-English line, mirroring `load_config()`'s tolerant handling of
  a corrupt `ai-visualizer.json`. `glass-state.json` is added to
  `.gitignore` in the same commit that introduces it.
- **Config**: `DEFAULTS` in server.py gains `"glass": True`; set
  `"glass": false` in `ai-visualizer.json` to turn the layer off. The
  `/config` response gains the key (`"glass": bool(CFG["glass"])`) —
  note this is an edit to the deliberate whitelist dict in the handler,
  not a switch to spreading CFG. With the flag off, every `/cmd` verb
  replies `ok:false` with a plain-English message.
- **Page**: a new `glass.js` beside `core.js`. `core.js` injects
  `<script src=ROOT+"glass.js">` when the `/config` reply has
  `glass: true` AND the page is a face page AND not demo/shot mode —
  OR when the query string has `?glassdemo=1` (any mode, including
  `file://`). The face-page test is `location.pathname` matching
  `^/faces/<id>/`, which is sound for this stack: server.py discovers
  every face — custom and promoted ones included — exclusively by
  scanning `faces/`, and the gallery at `/` is the only other core.js
  consumer, so every face gets the overlay for free and the gallery
  (whose whole purpose is clickable cards the overlay would occlude)
  never does. The same match yields the face id that core.js appends
  to its poll URL: `/state?face=<id>` (harmless when glass is off; the
  server ignores it). `?glassdemo=1` is a serverless QA mode: glass.js
  stages one local fixture item per registered type and never polls —
  usable from `file://`, composable with core's `?demo=1` loop. The
  `?demo=` and `?shot=` params are never overloaded; in plain demo and
  shot modes glass.js is not loaded at all, so the screenshot harness
  is untouched.
- **Overlay**: a CSS grid layer above the face canvas
  (`pointer-events: none` on the layer, `auto` on items). glass.js
  renders from the payload by diffing against what is on screen, by id
  (§3.5).
- **Agent side**: `bin/glass.sh` (POST one JSON verb, from argv or
  stdin) and `bin/glass-state.sh` (human-readable state). Both resolve
  the server as `http://127.0.0.1:$PORT` where PORT comes from a
  `python3 -c` one-liner reading `"$(dirname "$0")/../ai-visualizer.json"`
  (bin/ lives inside ai-visualizer/), falling back to 8790 — the same
  precedence as `load_config()`. `--port` is a debugging override the
  scripts do not see; glass users set the port in the config.

**Origin defense** (this replaces v1's "localhost only, like
everything else", which covered a read-only server and does not cover
mutations):

- `POST /cmd` requires `Content-Type: application/json`, else 403.
  This is the whole cross-site write defense, and it works because of
  how browsers classify requests: a cross-origin POST with a
  `text/plain` or form content type is a "simple request" browsers
  send without asking, so any web page open on the machine could draw
  on the glass; `application/json` is not a simple content type, so
  the browser first sends an OPTIONS preflight — which this server
  never answers (no `do_OPTIONS`, no `Access-Control-Allow-*` headers
  anywhere) — and the POST is never sent. `curl` is unaffected. The
  same rule closes the residual write path from sandboxed `html`
  fragments (§4): an opaque-origin frame's fire-and-forget no-cors
  POST dies on the same preflight.
- Every request (GET and POST) is refused with 403 unless the `Host`
  header is `127.0.0.1:PORT`, `localhost:PORT`, or `[::1]:PORT`. This
  is the DNS-rebinding defense: a rebound page becomes same-origin
  with the server and could otherwise *read* `/state` — the usage
  readout and whatever is on the glass — so the guard covers reads,
  not just `/cmd`.

## 2. The grid

- **12 columns × 8 rows**, addressed like a spreadsheet: columns
  `A`–`L` left→right, rows `1`–`8` top→bottom. A placement is an
  anchor cell + a span: `{"cell":"J4","span":[3,2]}` = right edge,
  mid-height, 3 wide 2 tall.
- Gutters and outer margin are CSS tokens (default 12px gutter, 24px
  margin). The grid adapts to any resolution; cells are fractional
  (`fr`) units.
- **No size is fixed.** Any item may occupy any span the grid can
  hold, at any time: the per-type defaults in §4 are openings, not
  limits, and "enlarge it" / "make it smaller" is one `move` with a
  new span (animated, §6). The grid itself is the only law — every
  item is always cell-aligned, so nothing ever sits at a weird size or
  position.
- **The face reserve**: cells the layout will never assign, configured
  per face in `face.json` as `"reserve"`: one rect or a list of rects,
  each `{"cell":"D2","span":[6,6]}` (a single object is normalized to
  a one-element list). A face with no `reserve` key gets the default
  `[{"cell":"D2","span":[6,6]}]` — the center block. Multiple rects
  exist because real faces have chrome outside the center: the board
  face draws functional blocks in all four corners (status top-right,
  brand top-left, echo/mic bottom-left, hint bottom-right), so its
  reserve is center + corners, e.g. `[D2 6x6, A1 2x1, J1 3x2, A7 3x2,
  J8 3x1]`. Measuring and writing reserve lists for the four shipped
  faces is a scheduled work item (§10 step 4).
- **The effective reserve**: the server cannot know which face a
  browser is showing from config alone (the gallery switches faces
  client-side; two faces can be open in two tabs). So faces report
  themselves: core.js appends `?face=<id>` to its `/state` poll, and
  the server keeps an in-memory `{face_id: last_seen}` map. The
  effective reserve — used by auto-placement and every conflict check
  — is the **union** of the reserves of all faces seen in the last
  3 s. Deterministic with two tabs (stay clear of both), no
  flip-flopping. When no face has polled recently (server just
  started, headless, selfcheck), it falls back to the configured
  default face's reserve — the face a newly opened browser will show —
  never the union of all installed faces. A face switch updates the
  effective reserve within one poll interval. An item that a face
  switch leaves overlapping the new reserve is **not** auto-moved: it
  stays put and is flagged `"over_reserve"` in the payload and in
  glass-state output, so the agent can move or dismiss it.
- **Auto-placement**: `cell` is optional, and agents SHOULD omit it
  unless the person asked for a position — the server is the one
  source of truth for "free". Omitted, the item gets its type's
  default span (§4 table; every default fits the 3-wide side rails
  left by the default reserve) unless `span` is passed, and the
  scanner tries anchor cells in this fixed order: columns `J,K,L`
  top→bottom (right rail), then `A,B,C` top→bottom (left rail), then
  `D`–`I` top→bottom. An anchor is accepted iff anchor+span fits
  inside the 12×8 bounds and overlaps no item and no effective-reserve
  rect; the first accepted anchor wins. The order hugs the rails and
  keeps the center clear even for a face that defines no reserve.
- **Refusals** — never a silent re-place, clamp, or shrink; the agent
  always knows what actually happened:
  - An explicit `cell` overlapping ITEMS is refused with
    `error:"occupied"`, **all** overlapping item ids (a multi-cell
    span can overlap several), the occupancy map, and the free probes
    (§3.5). The RESERVE does not refuse explicit geometry: explicit
    cells/spans only arrive when the person asked (§7), and the grid
    is the only law — the placement succeeds over the face and the
    reply carries `"over_reserve": true` (the item is flagged in the
    payload too). A span-only `move` that fits nowhere clear of the
    reserve likewise goes over it, flagged, before ever refusing
    `no_room`. Auto-placement never touches the reserve on its own.
  - An explicit `cell`+`span` that runs past column `L` or row `8` is
    refused with `error:"out_of_bounds"` and the offending bounds —
    never clamped or wrapped.
  - Auto-place with no fitting free slot is refused with
    `error:"no_room"`, the occupancy map, and the ids of the oldest
    unpinned items as dismissal candidates — the reply tells the agent
    to dismiss something (or pass a smaller span) and retry. There is
    no force-eviction in v1 (§9): the agent resolves conflicts with
    the verbs it already knows.

## 3. Verbs (`POST /cmd`, one JSON object per call)

| verb | fields | effect |
|---|---|---|
| `show` | `type`, type-specific fields, `id?`, `new?`, `cell?`, `span?`, `ttl?`, `pin?`, `title?` | Create or replace an item (targeting rules below). |
| `update` | `id`, content fields | Patch content in place. **Content-only**: may not change `type`, `cell`, `span`, or `pin` — naming those (or unknown fields) is an error and changes nothing. No re-animation; bumps the item's `rev`. |
| `move` | `id`, `cell?`, `span?` (at least one) | Animated re-place and/or **resize**. Omitted field keeps its current value. `span`-only is the "enlarge it / shrink it" verb: the item resizes at its current anchor, and when the grown footprint no longer fits there, the server re-anchors it to the nearest fit via auto-placement instead of refusing — the person asked for a size, not a position. Explicit `cell` keeps the §2 refuse-on-conflict rule. |
| `pin` / `unpin` | `id` | Toggle persistence. Pinning clears `ttl` (`expires_in` becomes null); unpinning restarts the item's effective ttl. |
| `dismiss` | `id` \| `ids` | Animated exit for the named item(s). |
| `clear` | `include_pinned?` | `{"a":"clear"}` dismisses everything unpinned; `{"a":"clear","include_pinned":true}` clears the lot. `include_pinned` without a clear is an error, not a guess. |
| `state` | — | The glass object (§3.5) plus `map`, `free`, `viewers` — what `glass-state.sh` prints. |

**Targeting — singleton per type by default.** The 95% case needs no
ids at all: a `show` of a type **replaces that type's existing item in
place** (same cell, same span, same pin state unless those fields are
passed; ttl restarts). "Actually, show Rome instead" is just another
`show type:map` — no dismiss+show dance, no server-assigned slug to
remember across a topic shift. Precisely:

1. `id` passed → replace that item if it exists, create it with that
   id otherwise.
2. `"new": true` → always create another instance (this is how you get
   two notes up at once).
3. Otherwise: exactly one item of the type on the glass → replace it;
   none → create; **two or more** → refused with `error:"ambiguous"`
   and their ids (pass `id` or `new:true`) — the server never guesses
   which card to destroy.

Ids are server-assigned short slugs (`map-1`, `note-2`); the counter
persists in `glass-state.json` so a restarted server never reissues an
id a page may still be holding. A replace runs the §2 conflict check
with the item's own old footprint treated as free — an in-place
replace never self-collides; a footprint that grew gets the normal
refusal. Visually, a replace swaps the body inside the existing card
with the one-frame hairline flash (§6), plus the FLIP move if
cell/span changed — never a full exit+enter.

**Booleans** (`pin`, `new`, `include_pinned`): JSON booleans; the
strings `"true"`/`"false"` are coerced (the one common LLM emission
slip); any other value gets an error naming the field and the two
accepted spellings.

**Lifecycle.** `ttl` defaults to **180 s** for every type — one
number, no per-type table (v1's note exception was trimmed; pass `ttl`
explicitly when something should linger). An explicit `ttl` always
wins. One invariant on top: *an item whose content encodes its own end
time never expires before that time* — a `timer`'s effective ttl is
`max(ttl, remaining duration + 30 s grace)`, so a 10-minute timer
never dies at 3:00. A timer whose `seconds <= 0` or whose `until` is
in the past is a refused show (the agent almost certainly mis-built
the payload); `until` must be a full ISO 8601 date-time (date-only is
refused), parsed server-side; an offset-less value means the server's
local time, and agents SHOULD include an offset. `pin:true` means
forever (survives restarts). `update` and `move` reset the clock to
the item's own effective ttl — the value last set on it, or the
default; an explicit `ttl` on update sets a new effective ttl.

**Clocks.** The server owns expiry and tracks it with
`time.monotonic()` (immune to wall-clock jumps). Expired items are
pruned lazily — at the top of handling any `/cmd` or `/state` request,
under the model lock; the ~8 Hz poll means pruning lands within
~125 ms of anyone being able to observe it, and no background thread
exists. Every payload and reply reports lifetime as **`expires_in`
(seconds remaining, null when pinned)**, computed at serialization —
no epoch timestamps, no ISO strings, no client clock math, and a
future second-browser glass (§9) stays correct for free. The last
10 s an item gets a subtle fade pulse so disappearance never feels
like a glitch.

**Replies.** Every reply carries `"viewers"`: the number of distinct
face ids that polled `/state?face=` in the last 3 s. 0 means nobody is
looking at a glass right now (§7 tells the agent what to do with
that). One example per verb — these worked examples ARE the schema:

```
show    {"a":"show","type":"map","q":"Aleppo","zoom":8}
     -> {"ok":true,"id":"map-1","replaced":false,"cell":"J3",
         "span":[3,3],"expires_in":180,"viewers":1}
        ("replaced":true when it swapped an existing card)

update  {"a":"update","id":"note-1","body":"- eggs\n- basil"}
     -> {"ok":true,"id":"note-1","rev":4,"expires_in":180,"viewers":1}

move    {"a":"move","id":"map-1","cell":"A2"}
     -> {"ok":true,"id":"map-1","cell":"A2","span":[3,3],
         "expires_in":180,"viewers":1}

pin     {"a":"pin","id":"note-1"}
     -> {"ok":true,"id":"note-1","pin":true,"expires_in":null,
         "viewers":1}

dismiss {"a":"dismiss","id":"map-1"}
     -> {"ok":true,"dismissed":["map-1"],"viewers":1}

clear   {"a":"clear"}
     -> {"ok":true,"cleared":["map-1","timer-2"],
         "kept_pinned":["note-1"],"viewers":1}

state   {"a":"state"}
     -> {"ok":true,"glass":{...§3.5 object...},
         "map":["##.#########", ...8 row strings...],
         "free":{"2x2":"J6","3x2":"J6","3x3":null,"3x4":null},
         "viewers":1}

refusal {"a":"show","type":"note","cell":"J3","body":"hi"}
     -> {"ok":false,"error":"occupied","by":["map-1"],
         "map":[...8 rows...],"free":{...},"viewers":1}
        (out_of_bounds carries the offending bounds instead of "by";
         no_room carries "dismiss_candidates":["map-1","timer-2"])
```

The occupancy `map` is rendered server-side — one implementation of
the grid math, asserted against the item list by `--selfcheck`: 8
strings of 12 chars, `.` free, `#` effective reserve, and a lowercase
letter per item in listing order. `free` reports, for each common
probe span, the anchor the auto-placer would return right now (or
null) — it reuses the placement scanner, so "will my next show fit"
has one answer.

### 3.5 The glass payload (inside `/state`)

When glass is enabled, `/state` (real and `--mock` paths both) gains:

```json
"glass": {
  "rev": 17,
  "reserve": [{"cell":"D2","span":[6,6]}, {"cell":"A1","span":[2,1]},
              {"cell":"J1","span":[3,2]}, {"cell":"A7","span":[3,2]},
              {"cell":"J8","span":[3,1]}],
  "items": [
    {"id":"map-1","type":"map","title":"Aleppo","cell":"J3",
     "span":[3,3],"pin":false,"rev":3,"expires_in":142.5,"flags":[],
     "q":"Aleppo","zoom":8},
    {"id":"note-1","type":"note","title":"Groceries","cell":"A4",
     "span":[3,2],"pin":true,"rev":1,"expires_in":null,
     "flags":["over_reserve"],"body":"- eggs\n- basil"}
  ]
}
```

Items carry their type-specific fields (that is what glass.js renders
from) plus the server-computed ones shown. `reserve` is the effective
reserve (§2). Three rules make the dumb 8 Hz poll cheap and the
motion contract exact:

1. **`glass.rev`** is bumped on *every* model change — mutations via
   `/cmd` and lazy TTL pruning alike. glass.js compares it against
   the last value it rendered and does nothing until it moves; core.js
   contributes exactly one line (`A.glass = raw.glass || null` in
   `tick`, mirroring `rate_limits`).
2. **`item.rev`** is bumped only by *content* mutations — `update`,
   or a `show` that replaced the item. glass.js rebuilds a card body
   only when it changes; a `cell`/`span` change drives the FLIP move
   and `expires_in` drives the fade pulse without touching the body.
   This is what lets non-reinitializable bodies (map, calendar,
   iframe, html) survive polling.
3. **The payload is the source of truth; glass.js diffs it by id**:
   new ids play the entrance, ids missing from the payload play the
   exit, matching ids patch in place. This one contract makes
   restart-reconciliation automatic (a restarted server's model lacks
   ephemerals, so they animate out on the first good poll; pinned
   items match by id and do not re-animate) and page refresh cheap
   (items present in the *first* poll after page load render settled,
   no entrance — only ids appearing in a later diff play the
   materialize beat). On failed polls the glass follows core.js's
   precedent — hold what is shown, never blank the layer — but keeps
   counting down each item's last-known `expires_in` locally, so
   ephemerals still exit on schedule during an outage; after ~5 s of
   consecutive failures the card hairlines dim as a stale cue,
   cleared on the next good poll.

## 4. Component registry (tier 1 — the consistent vocabulary)

Every type renders inside the same **card chrome**: glass panel
(translucent dark), 1px cyan hairline border, title bar in VT323 with
the item id ghosted right, body in the system font. Chrome is owned by
glass.js — components only fill the body. All of it is hand-rolled on
the §5 tokens; there is no component library and no vendored code in
v1 (see §9 for what earns its way back). This is what keeps eight
components looking like one system.

| type | fields | default span | renders |
|---|---|---|---|
| `note` | `title`, `body` (markdown subset: bold, lists, code) | 3×2 | text card | A note without `span` hugs its text: one short plain line opens 3x1 as a hero line, a paragraph 3x2, longer 3x3.
| `image` | `src` (URL or repo-relative), `caption?` | 3×3 | image, letterboxed |
| `map` | `q` (place/address) or `lat`+`lon`, `zoom?` | 3×3 | Google Maps embed (below) |
| `calendar` | `events` `[{date, time?, label}]`, `view?` (month/week) | 3×4 | hand-rolled grid (below) |
| `timer` | `until` (ISO date-time) or `seconds`, `label?` | 2×2 | big countdown, VT323 |
| `list` | `items` `[{text, done?}]`, `title` | 3×3 | checklist card |
| `iframe` | `src`, `title` | 3×3 | sandboxed external embed (below) |
| `html` | `html`, `title` | 3×3 | **tier 2 escape hatch**: agent-authored fragment, origin-isolated (below) |

Every default span fits the 3-wide side rails left by the default
reserve; the calendar grows down the rail, not across. The registry
lives in `glass.js` as a plain object of `render(el, item)` functions
— adding a component is adding one function, which is exactly the kind
of work the agent-mechanic does later.

- **map** is an iframe preset, not a map engine:
  `https://maps.google.com/maps?q=<urlencoded q or "lat,lon">&z=<zoom>&output=embed`
  — the keyless, unofficial-but-long-standing embed form (never the
  Embed API, which wants a key this stack refuses on principle). No
  geocoding anywhere: the agent's `q` goes straight into the URL and
  Google resolves it, which is what kills v1's q→lat/lon gap and the
  Leaflet dependency in one move. The card's title bar always shows
  the query text, and offline (`navigator.onLine === false`, or the
  embed refuses to load) the body swaps to the query text plus
  "no map offline" — a labeled placeholder, the single degraded mode.
  The embed goes through the same iframe sandbox rules as the `iframe`
  type.
- **calendar** is a hand-rolled static month/week CSS grid (~100
  lines) rendering exactly the passed events — day cells, event
  labels, today highlighted. It is explicitly **not** a scheduling
  suite: no dragging, clicking, or editing (all interactivity is §9),
  which is why it needs no library and inherits the theme tokens
  natively.
- **timer**: the served item carries `ends_in` (seconds to the
  timer's end, server-computed at serialization like `expires_in`), so
  glass.js renders the countdown from `ends_in` at receipt plus local
  elapsed time — no ISO parsing in the browser, no clock skew.
- **iframe** (external embeds): `sandbox="allow-scripts
  allow-same-origin"`. That token pair is acceptable *here only*
  because the content is cross-origin — `allow-same-origin` grants the
  embed its own origin, not the glass's, and cross-origin content
  cannot reach the parent page regardless. To keep that premise true,
  the server refuses at `/cmd` time any `src` whose scheme is not
  http/https or whose host is loopback (`127.0.0.1`, `localhost`,
  `[::1]`) — a same-origin embed with those tokens would be a textbook
  sandbox escape into the server's origin. The chrome always shows the
  src host in the title bar: most login-bearing sites refuse framing
  (`X-Frame-Options`/`frame-ancestors`) and a refused cross-origin
  embed is a blank the parent cannot detect — so it renders as a
  *labeled* blank, and §7 teaches the agent to prefer native types.
- **html** (tier 2): the fragment is delivered in the item JSON via
  the `/state` poll and rendered by assigning the `srcdoc` property of
  an iframe with `sandbox="allow-scripts"` **only** — no
  `allow-same-origin`, no served-fragment endpoint, sandbox attribute
  set before the iframe enters the DOM. Why this contains it: srcdoc
  without `allow-same-origin` gets an **opaque origin** — its scripts
  run, but the frame cannot reach `window.parent`, cannot remove its
  own sandbox, and cannot read any same-origin response (every fetch
  from an opaque origin is cross-origin), and its one remaining
  fire-and-forget write path to `/cmd` dies on §1's Content-Type
  preflight rule. One cosmetic consequence: an opaque-origin fragment
  cannot load same-host fonts — fine, because the VT323 card chrome
  lives outside the iframe.

## 5. Design language

- **Tokens, not a kit**: one `glass-theme.css` defines the design
  language — `--glass-bg`, `--glass-line` (cyan), `--glass-text`,
  `--glass-accent`, radii, `--glass-blur`. Card chrome and every
  component consume only tokens. That IS the consistent design
  language; v1's Shoelace "base kit" shipped zero controls v1 could
  use (all interactivity is deferred) and is gone with the rest of the
  vendor list.
- **Type stack**: VT323 for titles/numerals (already shipped in
  `assets/`), system sans for body.
- **Blur is opt-in, off by default**: every shipped face repaints a
  full-viewport canvas every frame, and `backdrop-filter` over a
  moving backdrop makes the compositor re-blur continuously — several
  cards up can visibly drop the face's frame rate on the always-on
  hardware this spec exists for. So `--glass-bg` defaults to a solid
  translucent dark (`rgba(4,12,8,.85)`) with `--glass-blur: 0`;
  opting in is overriding `--glass-blur` (e.g. `8px` — cost scales
  with radius) and lowering the bg alpha. `prefers-reduced-transparency`
  forces the flat look regardless.
- **Nothing vendored, nothing hotlinked**: v1's `vendor/` step is
  deleted entirely — no Shoelace, Leaflet, FullCalendar, or Chart.js
  (§9 records what would earn each back). The only network the glass
  itself touches is the map/iframe embeds, which degrade to labeled
  placeholder cards offline (§8).

## 6. Motion

- **Enter**: scale 0.96→1 + fade, 240 ms, ease-out; a 1-frame cyan
  hairline flash on the border (the "materialize" beat). Items present
  in the first poll after page load render settled — no entrance wall
  on refresh (§3.5).
- **Exit**: fade + scale to 0.98, 180 ms. Expiry adds the last-10 s
  fade pulse before it.
- **Move**: FLIP transform between grid slots, 300 ms — one ~30-line
  helper, the only consumer being `move` and replace-with-new-cell.
- **Replace**: body swap + hairline flash; FLIP if the footprint
  changed; never a full exit+enter.
- All motion honors `prefers-reduced-motion`. No motion library, and
  no View Transitions API: a document transition snapshots the page
  and would freeze the per-frame canvas face for ~300 ms per move —
  CSS transitions + the FLIP helper are one mechanism per effect,
  nothing behind feature checks (§9 parks View Transitions).

## 7. Teaching the agent (the CLAUDE.md block)

Appended to the home CLAUDE.md (mirrors the barehands block). The
wiring step (§10 step 4) writes it with the machine's real absolute
paths — no placeholders, ever: a literal `REPO/bin/...` lands verbatim
in CLAUDE.md, fails, and the agent's improvised variants
(`cd ... && ./bin/glass.sh`, `bash bin/glass.sh`) all fall outside the
permission rule and trigger the spoken prompt the allowlist exists to
prevent.

> ## The glass
> The face runs on a grid overlay you control (localhost only). When
> the person asks to SEE something — "show me", "where is", "put up",
> "what's my week look like" — put it on the glass and say what you
> put up, instead of reading data aloud.
> - Show: `~/my-agent/ai-visualizer/bin/glass.sh '{"a":"show","type":"map","q":"Aleppo"}'`
>   Types: note, image, map, calendar, timer, list, iframe, html
>   (last resort, keep the house style). iframe: most sites with
>   logins refuse embedding and show a dead card — prefer a native
>   type built from data you fetch yourself.
> - Quoting: use the one-line argv form only when the JSON is a single
>   line with no apostrophes. Otherwise pass it on stdin:
>   `~/my-agent/ai-visualizer/bin/glass.sh <<'JSON'`
>   ... `JSON`. Never `echo ... | glass.sh` — the pipe form triggers a
>   permission prompt.
> - Replace by showing again: a show of a type replaces that type's
>   card in place — no ids to remember. Add `"new": true` for a second
>   card of the same type; every reply carries the id for
>   update/move/dismiss.
> - Look first: `~/my-agent/ai-visualizer/bin/glass-state.sh`
>   prints what is up, where, remaining lifetimes, and what fits.
>   Run it before placing with an explicit cell and before talking
>   about what is on screen.
> - Placement: omit "cell" and the server picks a free spot clear of
>   the face. If a placement is refused, the reply lists what is in
>   the way — dismiss or move it, then show.
> - Sizing: nothing is fixed. "Enlarge it" / "shrink it" / "make it
>   half the screen" = `{"a":"move","id":"...","span":[W,H]}` — the
>   card resizes in place (the server re-anchors it if the new size
>   doesn't fit where it sits). Pick spans that fit the content: video
>   and maps wide (4x3, 6x4), notes tall, timers small (2x2). A big
>   size may cover part of the face — allowed when asked for; the
>   reply says "over_reserve" when that happens, so mention it and
>   shrink or dismiss the card when the person is done.
> - Lifetime: items fade after ~180 s (timers always live out their
>   full countdown); every reply's "expires_in" says exactly. Pass
>   "ttl" (seconds) to linger; `"pin":true` only when asked to keep
>   something up. Dismiss with `{"a":"dismiss","id":"..."}` when the
>   person is done; `{"a":"clear"}` clears everything unpinned
>   (add `"include_pinned":true` for the lot).
> - Every reply has "viewers": how many face pages are actually
>   looking. If it is 0, nobody can see the glass — say so instead of
>   describing what you "put on screen".

**Permissions**: the install step creates
`~/my-agent/.claude/settings.json` — genuinely
project-scoped — with two allow rules on absolute paths and argument
wildcards (a rule without `:*` matches only an argument-less call, and
every real glass.sh call carries JSON):
`Bash(~/my-agent/ai-visualizer/bin/glass.sh:*)` and
`Bash(~/my-agent/ai-visualizer/bin/glass-state.sh:*)`.
This file, not `~/jarvis-config/settings.json` (which v1 mislabeled
"project-scoped" — it is the user-level settings for that config dir),
because project settings are read from the cwd: every launcher cds to
`~/my-agent`, so one file covers all of them — including the GLM
launcher, which runs under a different `CLAUDE_CONFIG_DIR`
(`~/jarvis-config-glm`) and would never inherit rules written to
`~/jarvis-config`. The heredoc form is covered by the same rule (the
command text still begins with the script path), which is why the pipe
form is banned rather than allowlisted. The CLAUDE.md block and both
allowlist entries are generated from the same resolved path variable
by the wiring script and must agree byte-for-byte; the step's check is
running the block's own example in a Jarvis session and confirming no
permission prompt appears.

**Why the allowlist is sound — honestly**: the verbs ARE a rendering
channel on an always-on screen. An injected instruction in something
Jarvis reads could put content up, and an `image`/`iframe` src is a
GET with an agent-controlled query string. The allowlist is justified
because the blast radius is display-only (no verb touches files or
executes anything), localhost-only (§1's origin defense), and
human-visible — whatever appears, appears on the glass in front of the
person, which is the opposite of a covert channel — with the sharp
edges filed off where cheap: the server refuses loopback/non-http
iframe sources, and html fragments are origin-isolated (§4). It is
*not* justified by "the verbs can't touch the network" — v1's claim,
which was false. `glass-state.sh` is read-only and unconditionally
safe.

## 8. Failure behavior

- `glass.sh` resolves the port from the config (§1) and splits its
  diagnosis: connection refused → "the face isn't running; start it
  and I'll put this up"; connected but the reply is not glass-shaped
  JSON (no `ok` key) → "something else is on the face's port". A
  malformed JSON body (from either input form) fails locally with a
  one-line error naming the parse position, before anything is posted.
  `glass.sh` exits non-zero on any `ok:false` reply, so the agent's
  shell sees failures.
- Glass disabled in config → every `/cmd` verb replies `ok:false`
  with a plain-English line, so the agent never announces content on a
  layer that cannot exist. `viewers: 0` is the softer sibling: the
  show succeeds (items persist and render when a page connects — a
  hard error would misfire during a mere page reload), and the agent
  is taught to say the glass isn't visible (§7).
- Unknown type, unknown field, `update` touching immutable fields,
  bad boolean spelling, timer already elapsed → structured error
  reply naming the problem and the valid options; nothing renders
  half-broken.
- **The glass never shows an *unlabeled* empty card**: a refused
  cross-origin embed is the one blank the page cannot detect, which is
  exactly why iframe and map cards carry their src host / query text
  in the chrome, and why the map's offline state is the query text +
  "no map offline".
- glass.js failures never take the face down: the overlay is wrapped
  so an exception disables the layer AND its `AV.glass` hook, logs to
  console, and leaves the face animating.
- Poll failure: hold what is shown, keep expiring locally, dim
  hairlines after ~5 s stale (§3.5). Server restart: pinned items
  return from `glass-state.json`, ephemerals animate out on the first
  good poll via the id diff. Missing/corrupt `glass-state.json`:
  empty glass, one log line, the server always comes up.

## 9. Deliberately deferred (v2+)

- Face auto-shrink when the glass is crowded (v1: the reserve is
  fixed; items over a switched-in face's reserve are flagged, not
  moved).
- Multi-display / second-browser glass (any face page riding
  `/state`'s glass object renders the same board; `expires_in` being
  relative already makes remote clocks a non-issue).
- Interactive components (checking off list items feeds events back to
  the agent — needs an events file on the bus).
- A `present` spotlight verb à la barehands (enlarge one item, dim the
  rest).
- Voice-anchored placement ("top left" → cell math in backtalk).
- `chart` type + Chart.js — no user goal asks for it; the `html`
  escape hatch covers ad-hoc charts until a real need appears.
- Force-eviction (`force:true`) — a policy engine for the exception to
  the exception; revisit if transcripts show the dismiss-then-show
  dance actually occurring.
- Shoelace-based components — gated on the first component that needs
  a real interactive control, i.e. the same milestone as
  interactivity.
- View Transitions API for moves (would freeze the canvas face
  per-transition; FLIP covers v1).
- Leaflet renderer for the map card (tile-level control: markers, dark
  tiles). The map field contract is renderer-agnostic, so it is a
  drop-in registry swap — but it must bring its own geocoder for `q`
  (e.g. Nominatim), a network dependency v1 deliberately avoids.
- FullCalendar — earns its place the moment events become
  clickable/draggable.
- Per-type TTL overrides — v1 ships one default plus the timer
  invariant; add a table only if real use shows types that need it.

## 10. Implementation plan

1. **Server** (~300 lines, stdlib only): glass model + one
   `threading.Lock` around all access; `POST /cmd` with every verb,
   targeting rules, validation (types, fields, booleans, iframe src
   guard), auto-placement + the three refusal shapes; the `/state`
   glass object + `?face=` tracking + effective-reserve union; lazy
   monotonic TTL pruning; `viewers` on every reply; atomic
   `glass-state.json` persistence (pinned + id counter, tmp +
   `os.replace`, tolerant load); `"glass": True` in `DEFAULTS` and in
   the `/config` whitelist dict; the Host guard on every request and
   the `Content-Type: application/json` requirement on `/cmd`;
   `glass-state.json` added to `.gitignore` in this same commit.
2. **Page**: `glass.js` (grid layer, `AV.glass` rev watch, diff-by-id
   renderer, hand-rolled card chrome, motion incl. the FLIP helper,
   registry with all eight types incl. the ~100-line calendar and the
   map/iframe/html sandbox rules) + `glass-theme.css`. `core.js`
   edits (~4 lines): `?face=` on the poll URL, `A.glass` in `tick`,
   the injection rule (flag AND face page AND not demo/shot, OR
   `?glassdemo=1`). `?glassdemo=1` fixtures: one fake item per type,
   serverless, works from `file://`.
3. **CLI**: `bin/glass.sh` (argv or stdin, config-derived port, JSON
   content type, non-zero exit on `ok:false`), `bin/glass-state.sh`
   (POSTs `{"a":"state"}`, prints the §3 map, item list with
   remaining lifetimes in human terms, and the free line via a ~15
   line stdlib formatter):

   ```
   glass 12x8   rev 17   viewers 1   faces: board
      A B C D E F G H I J K L
    1 # # . # # # # # # # # #
    2 . . . # # # # # # # # #
    3 . . . # # # # # # a a a
    4 b b b # # # # # # a a a
    5 b b b # # # # # # a a a
    6 . . . # # # # # # . . .
    7 # # # # # # # # # . . .
    8 # # # . . . . . . # # #
    a  map-1   map   "Aleppo"     J3 3x3   expires in ~2m
    b  note-1  note  "Groceries"  A4 3x2   pinned  [over_reserve]
   free: 2x2 at J6, 3x2 at J6, 3x3 no room, 3x4 no room
   ```

4. **Wiring**: measured `reserve` lists in the four shipped faces'
   `face.json` (board: center + four corners; neural: center + top
   strip + right CORTEX column; radial/rain: center only); the
   CLAUDE.md block and the two allow rules in
   `~/my-agent/.claude/settings.json`, both generated
   from one resolved path variable; README section; TROUBLESHOOTING
   entries (server down, port changed in config, stranger on the
   port, glass disabled, viewers 0, blur opt-in stutters the face).
5. **Checks**: `python3 server.py --selfcheck` exercises
   place/conflict/out-of-bounds/no-room/ambiguous-type/ttl/
   timer-outlives-its-countdown/persist/corrupt-state-file, the §1
   origin defenses (wrong Content-Type → 403, bad Host → 403), two
   threaded shows racing auto-placement while a loop hammers `/state`,
   and map-rows-match-items-and-reserve. `?glassdemo=1` is the
   eyeball-QA path for chrome, motion, and every renderer on target
   hardware; the `?shot=` harness is untouched.

Each step lands separately in ai-visualizer (a fork-style local
branch, same as the wake-word work in backtalk); updates from upstream
stay mergeable.
