/* ============================================================
   hands.js — drive the app with a hand in the air.

   See HANDS-SPEC.md. The one idea: a hand is a POINTER, not a
   gesture-per-feature table. Everything in this app that reacts to a
   click — the settings screen, the theme buttons, the face cards, the
   SND toggle — reacts to this cursor with no code here, and keeps
   reacting when someone adds a button.

   The single exception is the glass. Glass cards are rendered from the
   /state payload and mutated only through POST /cmd, so dragging the DOM
   node would be a lie the next poll erases. Instead a snapped GHOST
   follows the hand and the release posts the verb; the server places the
   card for real and glass.js FLIPs it there. A refused move (collision,
   out of bounds) simply never happens — nothing to undo.

   A press is a CLICK until it proves otherwise. Grabbing on the press made
   every control inside a card unreachable by hand, so the card is armed and
   only becomes a drag once the hand travels past tapPx or outlasts tapMs.
   The cursor behind that is one-euro filtered, not EMA'd: heavy smoothing
   when you are holding still on a target, light when you are crossing the
   screen. And it LETS GO of the fingertips as they close, riding the palm
   instead — because the cursor is the point between two fingertips, so
   closing them moves it by half the gap no matter how well it is filtered.
   See filterCheck(), holdCheck() and tapCheck().

   Off by default. ?hands=1 or the H key; the choice is remembered. No
   camera opens, and nothing is fetched from a CDN, until it is on.

   Failure rule, same as the glass: any throw tears this layer down with
   one console line and releases the camera. The face never pays for a
   hands bug.
   ============================================================ */
"use strict";

(() => {
  const ROOT = new URL(".", document.currentScript.src);
  const Q = new URLSearchParams(location.search);
  const DEBUG = Q.get("handsdebug") === "1";

  const CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";
  const MODEL = "https://storage.googleapis.com/mediapipe-models/" +
    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";

  /* --------------------------- the tuning knobs --------------------------- *
     Lifted from barehands v3.8.x/v3.9.x, where they were FITTED TO ANOTHER
     PERSON'S HAND. They are named and gathered here, and ?handsdebug=1
     prints the live values, because a threshold you cannot see is a
     threshold you cannot tune. Raise `contrast` if impostor poses grab;
     lower it if a real pinch is ignored.                                    */
  const T = {
    contrast:  0.18,   // (middle/ring/pinky arch) − (index arch) to admit
    backFloor: 1.30,   // back-arch floor: the fist wall
    profAspect: 2.0,   // palm aspect under which the profile rule applies
    profThumb: 0.95,   // thumb off the knuckle row, profile pinch
    relSlow:   0.55,   // gap/span release bar, slow hand
    relFast:   0.70,   // ...and fast (blur under-reads an opening hand)
    relSpeed:  800,    // px/s above which the fast bar applies
    // ENTRY gap ceiling. Without this a hand whose back fingers arch
    // correctly but whose thumb and index are APART enters a pinch, trips
    // the release bar on the very next frame, and re-enters — the
    // oscillation. The contrast law proves the SHAPE; this proves the touch.
    gapFront:  0.32,   // frontal regime
    gapProf:   0.38,   // rotated palm: the gap number inflates, so allow more
    emaBar:    0.55,   // the sustained-signature path into a pinch
    maxAspect: 6,      // above this the knuckle row has collapsed: the
                       // tracker is guessing, and a guess cannot pinch
    probMs:    400,    // a fresh pinch must keep its signature this long
    probSpeed: 600,    // ...unless the hand is moving faster than this
    probFrames: 4,     // consecutive signature-dead frames that drop it
    // CURSOR FILTER — a one euro filter, not a constant. minCut is the
    // cutoff a motionless hand gets, in Hz: lower is steadier, and steady is
    // the only thing that makes a small target hittable. beta is how fast
    // the filter opens up as the hand moves, in Hz per px/s: raise it if a
    // sweep feels dragged, lower it if a slow aim feels twitchy.
    minCut:    0.7,
    beta:      0.015,
    dCut:      1.0,    // cutoff on the speed estimate that drives the above
    // REACH. MediaPipe only reports landmarks near 0 or 1 when the hand is
    // at the very edge of frame, where it stops being detected — so mapping
    // the raw 0..1 to the screen makes the corners cost a hand you cannot
    // afford to move that far. This fraction of each frame edge maps
    // OFF-screen instead, and the cursor pins to the edge once you pass it.
    reach:     0.15,
    // EXPO. Uniform gain buys the corners at the cost of a twitchy centre.
    // This keeps the middle of the frame at roughly 1:1 (where you aim) and
    // accelerates toward the edges (where you just need to arrive). 1 is a
    // straight line; lower is more curve.
    expo:      0.70,
    tapMs:     300,    // a pinch shorter than this, with little travel...
    tapPx:     26,     // ...is a click, not a drag — and never lifts a card
    anchorMs:  120,    // the fallback aim lookback, for a pinch so fast the
                       // closing detector below never saw it coming
    // PINCH DRIFT. The cursor is the point BETWEEN two fingertips, so
    // closing them translates it — by up to half the gap, which is tens of
    // pixels. That is real motion, not noise, and no amount of smoothing
    // touches it. gapNear is the gap ratio at which fingers count as
    // closing (well above the grab bars, so it fires EARLY); closeDrop is
    // how far under its own recent average the gap must fall to prove it is
    // closing rather than resting. Cross both and the cursor stops
    // listening to the fingertips entirely.
    // Set gapNear to 0 to switch the pre-emptive freeze off entirely and
    // hold only once a pinch actually registers — the conservative fallback
    // if the freeze ever misreads your hand. Live-tunable under
    // ?handsdebug=1: AVHands.T.gapNear = 0
    gapNear:   0.60,
    closeDrop: 0.05,
    // AIM. 0.5 is the midpoint of index tip and thumb tip, which is what
    // this has always steered by. Lower it toward 0 to aim with the index
    // tip alone — a point the thumb cannot move at all. Try that first if
    // AIMING still wanders; clicking is covered by the freeze above.
    aim:       0.5,
    lostMs:    2000,   // no hand this long -> hide the cursor
    cornerPx:  56,     // corner zone size, capped against small cards
    cornerFr:  0.28,   // ...or this fraction of the card, whichever is less
  };

  const COLS = "ABCDEFGHIJKL";
  const clamp = (v, a, b) => v < a ? a : v > b ? b : v;

  /* --------------------------- the cursor filter ---------------------------
     A ONE EURO FILTER (Casiez, Roussel & Vogel, CHI 2012) where a fixed-alpha
     EMA used to be. One constant cannot do the filter's two jobs: low enough
     to kill the tremor of an arm held out in the air, high enough not to lag
     a hand crossing the screen. 0.45 was the compromise, and the compromise
     is why anything small was unhittable — a hand "holding still" still
     wandered several pixels, so the click landed on the neighbour.

     This picks the alpha every frame from how fast the hand is actually
     moving: nearly motionless collapses to a minCut Hz low-pass, far heavier
     than 0.45, so holding still LOOKS still; a real sweep opens the cutoff
     wide and ends up more responsive than the constant ever was. Continuous
     movement is tracked, tremor is not, and there is no dead zone to explain
     the stick-then-jump that a dead zone would cause.

     It is also dt-aware, so a dropped camera frame no longer silently
     changes the smoothing. Both facts are asserted in filterCheck().        */
  function lowpass(s, v, a) {
    s.y = s.has ? s.y + a * (v - s.y) : v;
    s.has = true;
    return s.y;
  }
  // Teleport, for when the cursor is deliberately PLACED rather than
  // tracked (the pinch freeze): it lands instantly instead of gliding, and
  // reports no speed for having done so.
  function euroSet(s, v) { s.x.y = v; s.x.has = true; s.d.y = 0; s.d.has = true; }
  function euro(s, v, dt) {
    const alpha = fc => 1 / (1 + 1 / (2 * Math.PI * fc * dt));
    const prev = s.x.has ? s.x.y : v;
    const spd = lowpass(s.d, (v - prev) / dt, alpha(T.dCut));
    return lowpass(s.x, v, alpha(T.minCut + T.beta * Math.abs(spd)));
  }

  /* ------------------------------ lifecycle ------------------------------- */
  let on = false, dead = false;
  let video = null, lm = null, stream = null, raf = 0, lastTs = -1;
  let layer = null, hud = null, ghost = null, toastEl = null;
  const curs = new Map();

  function guard(fn) {
    return function (...a) {
      if (dead) return;
      try { return fn.apply(this, a); } catch (e) { die(e); }
    };
  }
  function die(err) {
    if (dead) return;
    dead = true;
    console.warn("hands: disabled after an error — the app keeps running.",
                 err);
    try { stop(); } catch (e) { /* already torn down */ }
    // a throw kills the mouse drag too: the shared layer goes with it
    try { if (layer) { layer.remove(); layer = null; ghost = null; } }
    catch (e) { /* already gone */ }
  }

  function store(v) {
    try {
      if (v == null) localStorage.removeItem("av_hands");
      else localStorage.setItem("av_hands", v);
    } catch (e) { /* private mode: the toggle just won't persist */ }
  }
  function stored() {
    try { return localStorage.getItem("av_hands"); } catch (e) { return null; }
  }

  /* -------------------------------- chrome -------------------------------- */
  function toast(msg, ms) {
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.style.cssText =
        "position:fixed;left:50%;bottom:6%;transform:translateX(-50%);" +
        "z-index:80;padding:7px 16px;border-radius:4px;pointer-events:none;" +
        "background:rgba(0,0,0,.72);border:1px solid rgba(255,255,255,.22);" +
        "color:#e8f0f2;font:13px/1.4 var(--av-display,ui-monospace,monospace);" +
        "letter-spacing:.18em;transition:opacity .3s;text-transform:uppercase";
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.style.opacity = "1";
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { if (toastEl) toastEl.style.opacity = "0"; },
                          ms || 1600);
  }

  function buildLayer() {
    if (layer) return;
    layer = document.createElement("div");
    layer.id = "av-hands";
    // pointer-events:none everywhere in here — elementFromPoint has to see
    // THROUGH the cursor to the control underneath, or a hand can only ever
    // click itself.
    layer.style.cssText =
      "position:fixed;inset:0;z-index:70;pointer-events:none";

    ghost = document.createElement("div");
    ghost.style.cssText =
      "position:fixed;display:none;border:1px solid var(--av-accent,#3ddc84);" +
      "background:rgba(61,220,132,.10);border-radius:6px;" +
      "transition:left .07s linear,top .07s linear,width .07s,height .07s;" +
      "font:12px/1 var(--av-display,ui-monospace,monospace);" +
      "letter-spacing:.2em;color:var(--av-accent,#3ddc84);" +
      "align-items:flex-end;justify-content:center;padding-bottom:6px";
    layer.appendChild(ghost);


    if (DEBUG) {
      hud = document.createElement("div");
      hud.style.cssText =
        "position:fixed;left:14px;bottom:14px;z-index:71;padding:8px 10px;" +
        "background:rgba(0,0,0,.75);color:#9fb4ac;border-radius:4px;" +
        "font:11px/1.6 ui-monospace,monospace;white-space:pre;" +
        "pointer-events:none";
      layer.appendChild(hud);
    }
    document.body.appendChild(layer);
  }

  /* ---------------------------- the portrait ------------------------------
     A viewfinder you cannot see is a camera you cannot aim, so the preview
     is a glass CARD — same classes glass.js builds, so it inherits the
     theme's hairline, blur and radius with no styling of its own. It rides
     the grid when there is one and the corner when there is not.

     ponytail: it is an overlay the SERVER does not know about, so it can
     sit over a real card. That is the ceiling; the fix, if it ever matters,
     is a reserved cell in server.py rather than more code here. It is
     pointer-events:none so a hand can still click straight through it. */
  const PORT_BOX = [10, 6, 2, 2];                    // K7, two by two
  let port = null;
  // Where the person last put it and how big they made it. It is a local
  // overlay, not a server item, so the box lives here rather than behind
  // /cmd. A two-element value is the older cell-only form; the video is
  // object-fit:cover, so any footprint is a valid one.
  function portBox() {
    let v = null;
    try { v = JSON.parse(localStorage.getItem("av_hands_cell")); }
    catch (e) { /* nothing saved, or private mode */ }
    if (!Array.isArray(v) || (v.length !== 2 && v.length !== 4) ||
        !v.every(n => Number.isInteger(n))) return PORT_BOX.slice();
    const w = clamp(v.length === 4 ? v[2] : PORT_BOX[2], 1, 12);
    const h = clamp(v.length === 4 ? v[3] : PORT_BOX[3], 1, 8);
    return [clamp(v[0], 0, 12 - w), clamp(v[1], 0, 8 - h), w, h];
  }
  function placePortrait(c, r, w, h, save) {
    if (!port) return;
    port.style.gridColumn = (c + 1) + " / span " + w;
    port.style.gridRow = (r + 1) + " / span " + h;
    if (save) {
      try {
        localStorage.setItem("av_hands_cell", JSON.stringify([c, r, w, h]));
      } catch (e) { /* the change still stands for this session */ }
    }
  }
  function buildPortrait() {
    port = document.createElement("section");
    port.className = "glass-card";
    const head = document.createElement("header");
    head.className = "glass-head";
    const t = document.createElement("span");
    t.className = "glass-title"; t.textContent = "Hands";
    const id = document.createElement("span");
    id.className = "glass-id"; id.textContent = "camera";
    const x = document.createElement("button");
    x.type = "button"; x.className = "glass-x"; x.textContent = "\u00d7";
    x.title = "turn hands off";
    x.setAttribute("aria-label", "turn hands off");
    // closing the viewfinder IS turning hands off — a camera with no
    // preview running in the background is exactly what nobody wants
    x.addEventListener("click", guard(e => {
      e.preventDefault(); e.stopPropagation();
      stop(); toast("hands off");
    }));
    head.append(t, id, x);
    const body = document.createElement("div");
    body.className = "glass-body";
    body.style.cssText = "padding:0;overflow:hidden";
    body.appendChild(video);
    port.append(head, body);
    const gl = document.getElementById("glass-layer");
    if (gl) {
      gl.appendChild(port);
      placePortrait(...portBox(), false);
    } else {
      // no glass on this page (the gallery): same chrome, parked in the corner
      port.style.cssText += ";position:fixed;right:24px;bottom:24px;" +
        "width:190px;height:150px;z-index:69;display:flex;" +
        "flex-direction:column";
      document.body.appendChild(port);
    }
  }

  function cursorEl() {
    const d = document.createElement("div");
    d.style.cssText =
      "position:fixed;width:22px;height:22px;margin:-11px 0 0 -11px;" +
      // themes override --glass-line, so the cursor is the same colour as
      // the hairline of the cards it is reaching for
      "border:2px solid var(--glass-line,var(--av-accent,#3ddc84));" +
      "border-radius:50%;pointer-events:none;" +
      "transition:background .08s,transform .08s;" +
      "box-shadow:0 0 12px var(--av-glow,rgba(61,220,132,.5))";
    layer.appendChild(d);
    return d;
  }
  function getCur(i) {
    let c = curs.get(i);
    if (!c) {
      c = { x: innerWidth / 2, y: innerHeight / 2, el: cursorEl(),
            pinched: false, okPrev: false, openPrev: false, seen: 0,
            hist: [], down: null, drag: null, dbg: "", t: null,
            // the pinch freeze: null while aiming, {x,y,px,py} while the
            // fingers are closed or closing (see readHand)
            hold: null, gapEma: null, palm: null,
            // one euro state per axis: the value lowpass and the speed one
            fx: { x: {}, d: {} }, fy: { x: {}, d: {} } };
      curs.set(i, c);
    }
    return c;
  }

  /* ------------------------------- grid math ------------------------------ */
  // Derived from the layer's own rect and computed style, so a theme that
  // changes --glass-gutter or --glass-margin needs no work here.
  function grid() {
    const g = document.getElementById("glass-layer");
    if (!g) return null;
    const r = g.getBoundingClientRect();
    const cs = getComputedStyle(g);
    const px = parseFloat(cs.paddingLeft) || 0;
    const py = parseFloat(cs.paddingTop) || 0;
    const gx = parseFloat(cs.columnGap) || 0;
    const gy = parseFloat(cs.rowGap) || 0;
    const cw = (r.width - 2 * px - 11 * gx) / 12;
    const ch = (r.height - 2 * py - 7 * gy) / 8;
    if (!(cw > 0 && ch > 0)) return null;
    return { r, px, py, gx, gy, cw, ch };
  }
  function cellAt(g, x, y) {
    return { c: Math.floor((x - g.r.left - g.px) / (g.cw + g.gx)),
             r: Math.floor((y - g.r.top - g.py) / (g.ch + g.gy)) };
  }
  // Which cell a dragged card's TOP-LEFT should snap to. ROUNDING, not
  // flooring: a grab begins with that corner sitting exactly on a cell
  // boundary, which is precisely where floor() flips — so a hand holding
  // still would step the card a whole cell on nothing but tracking jitter.
  // Rounding starts the drag in the middle of its own basin, so it takes
  // half a cell of real movement to change anything.
  function cellNear(g, x, y) {
    return { c: Math.round((x - g.r.left - g.px) / (g.cw + g.gx)),
             r: Math.round((y - g.r.top - g.py) / (g.ch + g.gy)) };
  }
  function cellRect(g, c, r, w, h) {
    return { left: g.r.left + g.px + c * (g.cw + g.gx),
             top: g.r.top + g.py + r * (g.ch + g.gy),
             width: w * g.cw + (w - 1) * g.gx,
             height: h * g.ch + (h - 1) * g.gy };
  }
  // glass.js places cards with gridColumn "3 / span 2" — read it back rather
  // than duplicating its bookkeeping.
  function cardPos(card) {
    const m = /^(\d+)\s*\/\s*span\s*(\d+)/;
    const a = m.exec(card.style.gridColumn), b = m.exec(card.style.gridRow);
    return (a && b) ? { c: +a[1] - 1, r: +b[1] - 1, w: +a[2], h: +b[2] } : null;
  }

  function cmd(body) {
    return fetch(new URL("cmd", ROOT).href, {
      method: "POST",
      // the server's cross-site write defense: same-origin JSON only
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(r => r.json());
  }

  /* --------------------------- synthesized input -------------------------- */
  function fire(el, type, x, y, buttons) {
    el.dispatchEvent(new MouseEvent(type, {
      bubbles: true, cancelable: true, view: window,
      clientX: x, clientY: y, button: 0, buttons: buttons,
    }));
  }
  function under(x, y) {
    if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return null;
    return document.elementFromPoint(x, y);
  }

  /* ------------------------------ the gestures ---------------------------- */
  // Bottom-right resizes; everything else moves. Dismiss is the X in the
  // card's own header — a real button, clickable by mouse or by pinch, and
  // impossible to trigger by grabbing slightly off-centre.
  function corner(card, x, y) {
    const b = card.getBoundingClientRect();
    const w = Math.min(T.cornerPx, b.width * T.cornerFr);
    const h = Math.min(T.cornerPx, b.height * T.cornerFr);
    return (x > b.right - w && y > b.bottom - h) ? "resize" : "move";
  }

  // Begin/finish are shared by BOTH input devices: a pinch drives them
  // through onPinch/onRelease, a mouse drives them straight from its own
  // events. One drag implementation, so the grid math, the ghost and the
  // POST cannot drift apart between the hand and the cursor.
  // (gx, gy) is where the press LANDED, which is not where the cursor is by
  // the time a drag threshold has been crossed. Grabbing from the landing
  // point keeps the card under the hand instead of jumping by the threshold
  // the moment it engages. The mouse omits it and grabs at the pointer.
  function beginDrag(c, el, gx, gy) {
    const card = el && el.closest && el.closest(".glass-card");
    // The viewfinder is a glass component too, so it moves, corner-resizes
    // and two-hand stretches exactly like the rest — it just lands locally
    // instead of through /cmd. Closing it is the X, which turns hands off.
    const local = !!card && card === port;
    if (!card || !(local || card.dataset.id) || !grid()) return false;
    const pos = cardPos(card);
    if (!pos) return false;
    const b = card.getBoundingClientRect();
    const ax = gx == null ? c.x : gx, ay = gy == null ? c.y : gy;
    c.drag = { card: card, id: card.dataset.id, pos: pos, local: local,
               mode: corner(card, ax, ay),
               ox: ax - b.left, oy: ay - b.top, target: null };
    card.style.opacity = ".45";
    return true;
  }
  function finishDrag(c) {
    const d = c.drag;
    c.drag = null;
    if (ghost) ghost.style.display = "none";
    if (!d) return false;
    d.card.style.opacity = "";
    const t = d.target;
    if (d.local) {
      if (t && (t.c !== d.pos.c || t.r !== d.pos.r ||
                t.w !== d.pos.w || t.h !== d.pos.h))
        placePortrait(t.c, t.r, t.w, t.h, true);
      return true;
    }
    if (t && (t.c !== d.pos.c || t.r !== d.pos.r ||
              t.w !== d.pos.w || t.h !== d.pos.h)) {
      const body = d.mode === "resize"
        ? { a: "move", id: d.id, span: [t.w, t.h] }
        : d.span
          ? { a: "move", id: d.id, cell: COLS[t.c] + (t.r + 1),
              span: [t.w, t.h] }
          : { a: "move", id: d.id, cell: COLS[t.c] + (t.r + 1) };
      cmd(body).then(r => {
        // A refusal needs no undo: the card never moved in the payload, so
        // the next poll simply re-places it where it already was.
        if (!r || !r.ok) toast((r && r.error) || "refused");
      }).catch(() => toast("no server"));
    }
    return true;
  }

  // Where the hand was anchorMs ago. hist already holds the last ~10
  // filtered positions with timestamps, so this costs nothing to keep: the
  // newest entry old enough wins, and a history too short gives its oldest.
  function anchor(c, now) {
    for (let i = c.hist.length - 1; i >= 0; i--)
      if (now - c.hist[i].t >= T.anchorMs) return c.hist[i];
    return c.hist[0] || { x: c.x, y: c.y };
  }

  function onPinch(c, now) {
    // The AIM point, not the pinch point. Closing the fingers pulls the
    // thumb/index midpoint — which is literally the cursor — off whatever
    // you were pointing at, so by the time a pinch is detected the cursor
    // has already left the target. On a calendar hour that drift is the
    // difference between two cells, which is the whole complaint.
    // If the cursor is already frozen — the fingers began closing a few
    // frames ago and it stopped following them — that frozen point IS the
    // aim point, and no lookback is needed. The lookback below only covers
    // a pinch snapped shut too fast for the freeze to see coming.
    const a = c.hold ? { x: c.x, y: c.y } : anchor(c, now);
    const el = under(a.x, a.y);
    // The close button is a button, not a drag handle — the same exclusion
    // the mouse has always had, so holding a pinch on the X dismisses the
    // card instead of quietly picking it up.
    const in_ = (sel) => (el && el.closest && el.closest(sel)) || null;
    c.down = { t: now, x: a.x, y: a.y, el: el,
               card: in_(".glass-x") ? null : in_(".glass-card") };
    // One card, one grab: while another hand is holding something, this one
    // is the stretch corner, not a second drag and not a click.
    for (const o of curs.values())
      if (o !== c && o.drag) { c.stretch = true; return; }
    // NOTHING is grabbed yet, not even a card. A press is a click until it
    // proves otherwise (tryDrag), because the card is its own drag handle —
    // grabbing on the press made every control inside a card unreachable:
    // the release was always a drag, so the click branch never ran.
    c.el.style.background = "var(--av-accent,#3ddc84)";
    if (el) fire(el, "mousedown", a.x, a.y, 1);
  }

  // The drag threshold every desktop toolkit has and this did not: travel
  // past tapPx, or outlast tapMs, and the press becomes a grab. Called once
  // per frame per pinched hand. Only ever grabs the card the press landed
  // on, so a hand that wanders over another card mid-pinch cannot steal it.
  function tryDrag(c, now) {
    const dn = c.down;
    if (!dn || c.drag || c.stretch || !dn.card) return;
    if (Math.hypot(c.x - dn.x, c.y - dn.y) < T.tapPx && now - dn.t < T.tapMs)
      return;
    beginDrag(c, dn.el, dn.x, dn.y);
  }

  function paintGhost(g, t) {
    if (!ghost) return;
    const rc = cellRect(g, t.c, t.r, t.w, t.h);
    ghost.style.display = "flex";
    ghost.style.left = rc.left + "px";
    ghost.style.top = rc.top + "px";
    ghost.style.width = rc.width + "px";
    ghost.style.height = rc.height + "px";
    ghost.textContent = t.mode === "move" ? COLS[t.c] + (t.r + 1)
                                          : t.w + "\u00d7" + t.h;
  }

  function onDragMove(c) {
    const g = grid();
    if (!g) return;
    const d = c.drag;
    // a footprint set by a two-hand stretch survives the partner leaving:
    // you sized it, so sliding it afterwards keeps that size
    const pw = d.span ? d.span[0] : d.pos.w;
    const ph = d.span ? d.span[1] : d.pos.h;
    let t;
    if (d.mode === "resize") {
      const e = cellAt(g, c.x, c.y);
      t = { c: d.pos.c, r: d.pos.r,
            w: clamp(e.c - d.pos.c + 1, 1, 12 - d.pos.c),
            h: clamp(e.r - d.pos.r + 1, 1, 8 - d.pos.r), mode: "resize" };
    } else {
      const a = cellNear(g, c.x - d.ox, c.y - d.oy);
      t = { c: clamp(a.c, 0, 12 - pw), r: clamp(a.r, 0, 8 - ph),
            w: pw, h: ph, mode: d.span ? "stretch" : "move" };
    }
    d.target = t;
    paintGhost(g, t);
  }

  /* ------------------------------ two hands -------------------------------
     Pinch with both and pull them apart: the card's footprint becomes the
     bounding box of the two cursors, snapped to cells. The hand that
     grabbed the card is the holder; the second hand is the far corner and
     never starts a grab of its own (see onPinch). Releasing either hand
     commits, and the size sticks if the holder keeps dragging. */
  function stretchDrag(a, b) {
    const g = grid();
    if (!g) return;
    const d = a.drag;
    const lo = cellAt(g, Math.min(a.x, b.x), Math.min(a.y, b.y));
    const hi = cellAt(g, Math.max(a.x, b.x), Math.max(a.y, b.y));
    const c0 = clamp(lo.c, 0, 11), r0 = clamp(lo.r, 0, 7);
    const w = clamp(hi.c - c0 + 1, 1, 12 - c0);
    const h = clamp(hi.r - r0 + 1, 1, 8 - r0);
    d.span = [w, h];                       // remembered past the release
    d.target = { c: c0, r: r0, w: w, h: h, mode: "stretch" };
    paintGhost(g, d.target);
  }

  function onRelease(c, now) {
    c.el.style.background = "";
    if (ghost) ghost.style.display = "none";
    const dn = c.down;
    c.down = null;
    // A grab the probation window killed was never a real pinch: put the
    // card back and post nothing, click nothing.
    if (c.probKill) {
      c.probKill = false;
      if (c.drag) { c.drag.card.style.opacity = ""; c.drag = null; }
      return;
    }
    const tap = dn && (now - dn.t) < T.tapMs &&
      Math.hypot(c.x - dn.x, c.y - dn.y) < T.tapPx;

    if (finishDrag(c)) return;
    if (c.stretch) { c.stretch = false; return; }   // the far corner, not a tap

    // A tap resolves against the element it PRESSED, at the point it aimed
    // at — never the release point, which the opening hand has already
    // moved off. Anything longer or further is a gesture that missed, and
    // clicking wherever it happened to end is worse than clicking nothing.
    const el = tap ? dn.el : under(c.x, c.y);
    if (!el) return;
    fire(el, "mouseup", tap ? dn.x : c.x, tap ? dn.y : c.y, 0);
    // .click() rather than a synthetic click event: it bubbles AND runs the
    // default activation, so an <a href> on the gallery actually navigates.
    if (tap && typeof el.click === "function") el.click();
  }

  /* --------------------------- frame -> screen ---------------------------- *
     One axis of the camera frame, mapped to one axis of the screen: trim the
     unreachable margin, then bend the middle flat and the ends steep. The
     curve is odd about the centre, so mirroring x is still 1 - axis(x).     */
  function axis(v) {
    const t = clamp((v - T.reach) / (1 - 2 * T.reach), 0, 1);
    const u = t * 2 - 1;                                  // centre-relative
    const e = T.expo * u + (1 - T.expo) * u * u * u;      // slope k at the
    return (e + 1) / 2;                                   // centre, 3-2k at
  }                                                       // the edges

  /* The two points on a hand this thing can steer by, both mapped through
     the same frame->screen curve.

     `t` is where you AIM: index tip blended toward thumb tip by T.aim. It is
     precise, and it is the one a pinch ruins — closing the fingers moves it.

     `p` is the PALM, the wrist-to-middle-knuckle centre. It is coarser, and
     a pinch does not move it at all. That is the entire reason it exists
     here: once the fingers start closing, the cursor rides this instead. */
  function points(lms) {
    const bx = lms[8].x + (lms[4].x - lms[8].x) * T.aim;
    const by = lms[8].y + (lms[4].y - lms[8].y) * T.aim;
    const qx = (lms[0].x + lms[9].x) / 2, qy = (lms[0].y + lms[9].y) / 2;
    return { tx: (1 - axis(bx)) * innerWidth, ty: axis(by) * innerHeight,
             px: (1 - axis(qx)) * innerWidth, py: axis(qy) * innerHeight };
  }

  /* ------------------------------ the read -------------------------------- */
  function readHand(lms, c, now) {
    const d = (a, b) => Math.hypot(lms[a].x - lms[b].x, lms[a].y - lms[b].y);
    const span = d(0, 9);
    // finger arch: tip distance from the wrist over knuckle distance from
    // the wrist — scale- and distance-free
    const W = lms[0];
    const fR = (t, m) => {
      const md = Math.hypot(lms[m].x - W.x, lms[m].y - W.y);
      return md > 0 ? Math.hypot(lms[t].x - W.x, lms[t].y - W.y) / md : 9;
    };
    const f8 = fR(8, 5);
    const back = (fR(12, 9) + fR(16, 13) + fR(20, 17)) / 3;
    const palmW = d(5, 17);
    const aspect = palmW > 0 ? span / palmW : 9;
    const tRel = span > 0 ? d(4, 13) / span : 0;
    // THE CONTRAST LAW (frontal) OR the far-thumb read (profile). A rotated
    // palm compresses the arch ratios, so the two regimes each get a rule
    // and either one admits.
    const ok = (back - f8 > T.contrast && back > T.backFloor) ||
               (aspect < T.profAspect && tRel > T.profThumb);
    const ratio = span > 0 ? d(4, 8) / span : 1;

    let spd = 0;
    if (c.hist.length >= 2) {
      const p0 = c.hist[0], p1 = c.hist[c.hist.length - 1];
      const dt = (p1.t - p0.t) / 1000;
      if (dt > 0) spd = Math.hypot(p1.x - p0.x, p1.y - p0.y) / dt;
    }
    // Two ways in: two consecutive clean frames (a snap pinch), or a
    // charged EMA (a hand whose approach was messy). One lucky frame from a
    // pose passing through buys neither.
    c.okEma = 0.70 * (c.okEma || 0) + 0.30 * (ok ? 1 : 0);
    const okNow = ok && c.okPrev;
    c.okPrev = ok;

    // Release is two-frame-sustained only at speed; a slow hand releases the
    // moment it opens, or letting go feels sticky.
    const openRead = ratio >= (spd > T.relSpeed ? T.relFast : T.relSlow);
    const relOk = spd > T.relSpeed ? (openRead && c.openPrev) : openRead;
    c.openPrev = openRead;

    // The sanity bound: no real hand collapses its knuckle row this far, so
    // the tracker is hallucinating. A guess can neither start nor keep a grab.
    const garbage = aspect > T.maxAspect;
    if (garbage) c.probKill = true;

    const was = c.pinched;
    // a hand already dragging is exempt from the shape test — the grip that
    // carries a card is allowed to look like a fist
    const holding = !!c.drag;
    const gapBar = aspect < T.profAspect ? T.gapProf : T.gapFront;
    let want = garbage ? false
             : was ? !relOk
                   : ratio < gapBar &&
                     (okNow || c.okEma > T.emaBar || holding);

    // Probation: closing a hand into a curl passes THROUGH a lawful OK-sign
    // for a frame or two, and hysteresis would then hold that bogus grab.
    // A fresh pinch has to KEEP its signature for probMs or it is dropped
    // silently — no click, no move posted.
    if (want && !was) { c.badRun = 0; c.probKill = false; c.pinchT = now; }
    else if (want && was && now - (c.pinchT || 0) < T.probMs &&
             spd < T.probSpeed && !holding) {
      c.badRun = ok ? 0 : (c.badRun || 0) + 1;
      if (c.badRun >= T.probFrames) { want = false; c.probKill = true; }
    } else c.badRun = 0;

    /* THE PINCH DRIFT, which smoothing cannot reach. The cursor is a point
       BETWEEN two fingertips; closing them translates that point by up to
       half the gap. So the moment the fingers start CLOSING — not when the
       pinch is finally read, which is already too late — the cursor stops
       listening to them: it snaps back to where the hand was aiming and
       from then on rides the palm, which a pinch cannot move. Opening the
       hand hands it back. holdCheck() is the regression. */
    const ref = c.gapEma;
    c.gapEma = ref == null ? ratio : ref + 0.25 * (ratio - ref);
    const closing = ref != null && ratio < T.gapNear && ratio < ref - T.closeDrop;
    // NO hysteresis here, deliberately — it was tried and it was wrong. The
    // pinch gate holds a state because a pinch IS a state; this is a
    // transient. A relaxed hand often rests at a gap ratio well under
    // gapNear, so "hold until the fingers open past gapNear" latched on the
    // first small movement and never let go: the cursor then steered by palm
    // for good, at palm gain, with no fingertip articulation. Ordinary
    // movement went slow and clunky and nothing said why.
    //
    // `closing` is self-limiting instead: gapEma catches up within a few
    // frames of the fingers stopping, so a hand that stops closing gets the
    // steering straight back. A pinch that completes is held by `want`.
    const wantHold = want || closing;
    if (wantHold && !c.hold && c.palm) {
      const a = anchor(c, now);
      c.hold = { x: a.x, y: a.y, px: c.palm.px, py: c.palm.py };
      euroSet(c.fx, a.x); euroSet(c.fy, a.y);
      c.x = a.x; c.y = a.y;
    } else if (!wantHold && c.hold) c.hold = null;

    if (DEBUG) c.dbg =
      "contrast " + (back - f8).toFixed(2) + "  (>" + T.contrast + ")\n" +
      "back     " + back.toFixed(2) + "  (>" + T.backFloor + ")\n" +
      "aspect   " + aspect.toFixed(2) + "   tRel  " + tRel.toFixed(2) + "\n" +
      "gap      " + ratio.toFixed(2) + "  (<" + gapBar + " to grab)\n" +
      "at       " + Math.round(c.x) + "," + Math.round(c.y) + "  of " +
      innerWidth + "x" + innerHeight + "\n" +
      "ema      " + (c.okEma || 0).toFixed(2) + "   spd  " + Math.round(spd) +
      "\n" + "ok " + (ok ? "YES" : "no ") + "  pinched " +
      (want ? "YES" : "no ") + (garbage ? "  GARBAGE" : "") +
      (c.hold ? "  HELD" : "") +
      (c.drag ? "  DRAG" : c.down ? "  armed" : "");

    if (want && !was) { c.pinched = true; onPinch(c, now); }
    else if (!want && was) { c.pinched = false; onRelease(c, now); }
  }

  /* ------------------------------ the loop -------------------------------- */
  function frame(now) {
    if (!on || dead) return;
    raf = requestAnimationFrame(guard(frame));
    if (!video.videoWidth || video.currentTime === lastTs) return;
    lastTs = video.currentTime;

    const res = lm.detectForVideo(video, now);
    const seen = new Set();
    const live = [];
    (res.landmarks || []).forEach((lms, i) => {
      seen.add(i);
      const c = getCur(i);
      live.push(c);
      c.seen = now;
      // mirrored inside points(): the camera sees you facing it, so
      // hand-right is screen-right
      const pt = points(lms);
      c.palm = pt;
      // Held, the cursor is its frozen aim point plus however far the PALM
      // has carried since — so a pinch cannot move it, but the hand still
      // can. That is what keeps a drag working while a click stays put.
      const gx = c.hold ? c.hold.x + (pt.px - c.hold.px) : pt.tx;
      const gy = c.hold ? c.hold.y + (pt.py - c.hold.py) : pt.ty;
      // real elapsed time, clamped: a stalled tab or a dropped frame must
      // not hand the filter a dt that rewrites its whole response
      const dt = clamp((now - (c.t == null ? now - 33 : c.t)) / 1000,
                       1 / 240, 0.25);
      c.t = now;
      const nx = euro(c.fx, gx, dt), ny = euro(c.fy, gy, dt);
      const moved = Math.hypot(nx - c.x, ny - c.y);
      c.x = nx; c.y = ny;
      c.hist.push({ x: c.x, y: c.y, t: now });
      if (c.hist.length > 10) c.hist.shift();
      c.el.style.display = "";
      c.el.style.left = c.x + "px";
      c.el.style.top = c.y + "px";
      c.el.style.transform = c.pinched ? "scale(.62)" : "";

      readHand(lms, c, now);
      // an armed press becomes a drag only once the hand really travels
      if (c.pinched) tryDrag(c, now);
      c.moved = moved;
    });

    // Drag movement is resolved only once every hand has been read, because
    // a two-hand stretch needs both cursors' positions from the SAME frame.
    const held = live.filter(c => c.pinched && c.drag);
    const partner = held.length === 1
      ? live.find(o => o !== held[0] && o.pinched && o.stretch)
      : null;
    if (held.length === 1 && partner) stretchDrag(held[0], partner);
    else for (const c of held) onDragMove(c);
    for (const c of live) {
      if (c.drag) continue;
      if (c.moved > 2) {
        // let the page know the pointer lives — hover states, core.js's own
        // idle-cursor logic, anything watching mousemove
        const el = under(c.x, c.y);
        if (el) fire(el, "mousemove", c.x, c.y, c.pinched ? 1 : 0);
      }
    }

    for (const [i, c] of curs) {
      if (seen.has(i)) continue;
      if (c.pinched) { c.pinched = false; onRelease(c, now); }
      if (now - c.seen > T.lostMs) c.el.style.display = "none";
    }
    if (hud) {
      const c = curs.get(0);
      hud.textContent = (c && c.dbg) || "no hand in frame";
    }
  }

  /* ------------------------------ start / stop ---------------------------- */
  async function start() {
    if (on || dead) return;
    on = true;
    buildLayer();
    toast("hands: starting camera…", 4000);
    try {
      // The preview earns its pixels: without it there is no way to know
      // your hand simply left the frame. Built here and PLACED once the
      // stream is live, so a refused permission never flashes an empty card.
      video = document.createElement("video");
      video.autoplay = true; video.playsInline = true; video.muted = true;
      video.style.cssText =
        "width:100%;height:100%;object-fit:cover;transform:scaleX(-1);" +
        "display:block";
      stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 640, height: 480 }, audio: false });
      video.srcObject = stream;
      await video.play();
      buildPortrait();
      const { HandLandmarker, FilesetResolver } =
        await import(CDN + "/vision_bundle.mjs");
      const vision = await FilesetResolver.forVisionTasks(CDN + "/wasm");
      lm = await HandLandmarker.createFromOptions(vision, {
        baseOptions: { modelAssetPath: MODEL, delegate: "GPU" },
        numHands: 2, runningMode: "VIDEO",
        // 0.7 is the busy-background ghost-hand wall; 0.5 presence lets a
        // latched hand ride through motion blur instead of being dropped
        minHandDetectionConfidence: 0.7,
        minHandPresenceConfidence: 0.5 });
    } catch (e) {
      on = false;
      if (port) { port.remove(); port = null; }
      // release the camera too: a failed start used to leave the stream
      // running with no preview, so the light stayed on and every retry
      // opened another one
      if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
      video = null;
      console.warn("hands: could not start.", e);
      toast("hands: no camera — " + (e && e.name || "failed"), 3500);
      return;
    }
    store("1");
    toast("hands on — pinch to click, H to stop");
    raf = requestAnimationFrame(guard(frame));
  }

  // Turning hands off releases the camera and the cursors — but NOT the
  // layer: the ghost belongs to the drag, and the mouse still drags.
  function stop() {
    on = false;
    cancelAnimationFrame(raf);
    for (const c of curs.values()) c.el.remove();
    curs.clear();
    if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
    if (lm) { try { lm.close(); } catch (e) { /* older builds */ } lm = null; }
    if (port) { port.remove(); port = null; }
    if (hud) hud.textContent = "hands off";
    video = null;
    lastTs = -1;
    store(null);
  }

  /* ------------------------------- the mouse ------------------------------
     The same drag, driven by a real pointer, running whether or not the
     camera is on. A card behaves identically under a hand and under a mouse
     because it is literally the same code path. */
  let mouse = null;
  addEventListener("mousedown", guard(e => {
    if (!e.isTrusted) return;    // our own synthesized input, not a mouse
    if (e.button !== 0 || mouse) return;
    const t = e.target;
    // the close button is a button, not a drag handle
    if (t && t.closest && t.closest(".glass-x")) return;
    const c = { x: e.clientX, y: e.clientY, el: { style: {} }, drag: null };
    if (!beginDrag(c, t)) return;
    e.preventDefault();          // no text selection while dragging a card
    mouse = c;
  }));
  addEventListener("mousemove", guard(e => {
    if (!e.isTrusted) return;    // our own synthesized input, not a mouse
    if (!mouse) return;
    mouse.x = e.clientX; mouse.y = e.clientY;
    onDragMove(mouse);
  }));
  addEventListener("mouseup", guard(e => {
    if (!e.isTrusted) return;    // our own synthesized input, not a mouse
    if (!mouse) return;
    // take the release point from the mouseup itself: a fast drag can land
    // without a single mousemove in between, and that must still count
    mouse.x = e.clientX; mouse.y = e.clientY;
    onDragMove(mouse);
    finishDrag(mouse);
    mouse = null;
  }));

  // returns the start() promise so a caller can repaint once the camera
  // permission has actually resolved, rather than guessing with a timer
  function toggle() {
    if (!on) return start();
    stop(); toast("hands off");
    return Promise.resolve();
  }

  // The public switch. Always exposed (not just under ?handsdebug=1) —
  // core.js's settings screen drives hands through exactly this.
  window.AVHands = { toggle, start, stop, on: () => on };

  addEventListener("keydown", guard(e => {
    if (e.key !== "h" && e.key !== "H") return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" ||
              t.isContentEditable)) return;
    toggle();
  }));

  // ?handsdebug=1 also exposes the grid math. It is the one part here that
  // can be silently wrong — a cell off by one looks like a normal move — and
  // unlike the pinch gate it is testable with no camera. See the round-trip
  // check in HANDS-SPEC.md §4.
  // The check: every cell centre must round-trip to its own coordinates, and
  // every card the browser has actually laid out must sit where cellRect
  // says it does. Run it in the console on ?handsdebug=1 — ideally with
  // ?glassdemo=1 too, which stages one card per type with no server.
  // A synthetic 21-landmark hand, built from the four numbers the gate
  // actually reads. Enough to prove the gate, and it needs no camera.
  function fakeHand(f8, back, gap, palmW) {
    const L = Array.from({ length: 21 }, () => ({ x: 0, y: 0, z: 0 }));
    const K = { 0: [0, 0], 5: [-palmW / 2, -0.95], 9: [-0.05, -1],
                13: [0.08, -0.97], 17: [palmW / 2, -0.93] };
    for (const k in K) { L[k].x = K[k][0]; L[k].y = K[k][1]; }
    const R = i => Math.hypot(L[i].x, L[i].y);
    // a tip sits along its own knuckle's direction, `mult` times as far out
    // from the wrist — which is exactly the arch ratio the gate measures
    const tip = (t, m, mult) => { L[t].x = L[m].x * mult; L[t].y = L[m].y * mult; };
    tip(8, 5, f8); tip(12, 9, back); tip(16, 13, back); tip(20, 17, back);
    L[4].x = L[8].x + gap * R(9); L[4].y = L[8].y;
    return L;
  }
  function gateCheck(bad) {
    // somewhere with no card under it: a grab exempts probation on purpose
    let px = 8, py = 8;
    for (const q of [[8, 8], [innerWidth - 8, 8], [8, innerHeight - 8]]) {
      const e = document.elementFromPoint(q[0], q[1]);
      if (!(e && e.closest && e.closest(".glass-card"))) { px = q[0]; py = q[1]; break; }
    }
    const cur = () => ({ x: px, y: py, el: { style: {} }, hist: [],
                         down: null, drag: null, pinched: false, seen: 0 });
    const run = (c, L, n, t0) => {
      for (let i = 0; i < n; i++) readHand(L, c, t0 + i * 33);
      return c.pinched;
    };
    // the oscillation regression: a lawful OK-sign with the fingers APART
    // must NEVER grab. Without the entry gap ceiling this pinches, releases
    // and re-pinches every three frames.
    if (run(cur(), fakeHand(1.35, 1.9, 0.70, 0.40), 12, 1e3))
      bad.push("gate: wide gap grabbed");
    if (!run(cur(), fakeHand(1.35, 1.9, 0.20, 0.40), 8, 2e3))
      bad.push("gate: closed gap did not grab");
    const c3 = cur();
    run(c3, fakeHand(1.35, 1.9, 0.20, 0.40), 8, 3e3);
    if (!c3.pinched || run(c3, fakeHand(1.7, 1.9, 0.80, 0.40), 4, 3.3e3))
      bad.push("gate: opening did not release");
    if (run(cur(), fakeHand(1.35, 1.9, 0.20, 0.12), 10, 4e3))
      bad.push("gate: collapsed knuckle row grabbed");
    const c5 = cur();
    run(c5, fakeHand(1.35, 1.9, 0.20, 0.40), 3, 5e3);
    if (!c5.pinched || run(c5, fakeHand(1.85, 1.9, 0.20, 0.40), 6, 5.1e3))
      bad.push("gate: dead signature survived probation");
  }

  // The frame->screen map. Cheap to assert and easy to break by touching a
  // knob: the centre must stay 1:1 (or aiming changes), the margin must
  // actually reach the screen edge (or the corners stay unreachable), and
  // the curve must stay odd about the centre (or mirroring x is wrong).
  function axisCheck(bad) {
    if (Math.abs(axis(0.5) - 0.5) > 1e-9) bad.push("axis: centre moved");
    if (axis(T.reach) > 1e-9 || axis(1 - T.reach) < 1 - 1e-9)
      bad.push("axis: margin does not reach the screen edge");
    if (axis(T.reach / 2) !== 0 || axis(1 - T.reach / 2) !== 1)
      bad.push("axis: past the margin does not pin to the edge");
    for (const v of [0.2, 0.35, 0.66, 0.8])
      if (Math.abs(axis(v) + axis(1 - v) - 1) > 1e-9)
        bad.push("axis: not odd about the centre — mirroring x breaks");
    const slope = v => (axis(v + 1e-4) - axis(v - 1e-4)) / 2e-4;
    if (Math.abs(slope(0.5) - 1) > 0.02)
      bad.push("axis: centre is no longer 1:1 (" + slope(0.5).toFixed(2) + ")");
    if (slope(T.reach + 0.02) < 1.5)
      bad.push("axis: edges are not faster than the centre");
    let prev = -1;
    for (let i = 0; i <= 200; i++) {
      const a = axis(i / 200);
      if (a < prev - 1e-12) { bad.push("axis: not monotonic"); break; }
      prev = a;
    }
  }


  /* The filter earns its complexity only by being BOTH steadier at rest and
     quicker in motion than the constant it replaced, so both are asserted
     against that constant. A knob turned too far fails here rather than in
     the air, where "the cursor feels wrong" is all the report you get.     */
  function filterCheck(bad) {
    const mk = () => ({ x: {}, d: {} });
    const dt = 1 / 30;
    // 1. a hand held still, carrying a few px of tracking noise
    const noise = i => 500 + 4 * Math.sin(i * 2.4) + 3 * Math.sin(i * 5.1);
    let st = mk(), ema = 500, jf = 0, je = 0;
    for (let i = 0; i < 200; i++) {
      const v = noise(i);
      const f = euro(st, v, dt);
      ema += (v - ema) * 0.45;                        // what this used to do
      if (i > 60) {
        jf = Math.max(jf, Math.abs(f - 500));
        je = Math.max(je, Math.abs(ema - 500));
      }
    }
    if (!(jf < je / 2))
      bad.push("filter: not steadier at rest than the old EMA (" +
               jf.toFixed(2) + "px vs " + je.toFixed(2) + "px)");
    // half the tap radius is the bar: wander wider than that and a press
    // cannot be told from a drag no matter what the thresholds say
    if (!(jf < T.tapPx / 2))
      bad.push("filter: rest wander " + jf.toFixed(2) + "px is over tapPx/2");
    // 2. a hand crossing the screen: the lag must beat the old EMA's
    st = mk(); ema = 0;
    let lf = 0, le = 0;
    for (let i = 1; i <= 60; i++) {
      const v = i * 900 * dt;
      lf = v - euro(st, v, dt);
      ema += (v - ema) * 0.45;
      le = v - ema;
    }
    if (!(lf < le))
      bad.push("filter: laggier in motion than the EMA it replaced (" +
               lf.toFixed(1) + "px vs " + le.toFixed(1) + "px)");
    // 3. dt-aware: the same physical sweep must land in the same place
    //    whether the camera gave 30 frames or 60, or the smoothing quietly
    //    changes with the frame rate and no knob explains why
    const sweep = hz => {
      const z = mk();
      let out = 0;
      for (let i = 1; i <= hz; i++) out = euro(z, i * (600 / hz), 1 / hz);
      return out;
    };
    if (Math.abs(sweep(30) - sweep(60)) > 12)
      bad.push("filter: frame rate changes the result (" +
               sweep(30).toFixed(1) + " vs " + sweep(60).toFixed(1) + ")");
  }

  /* THE TAP REGRESSION. The press used to grab the card outright, so the
     release was always a drag and the click branch below it was dead code —
     which is why nothing inside a card, a calendar day included, could ever
     be clicked by hand. Driven with a fake cursor over a real card. */
  function tapCheck(bad) {
    const card = document.querySelector(".glass-card[data-id]");
    if (!card || !grid()) return;
    const b = card.getBoundingClientRect();
    const x = b.left + b.width * 0.4, y = b.top + b.height * 0.4;  // off the
    const mk = () => ({ x: x, y: y, el: { style: {} },              // corner
                        hist: [{ x: x, y: y, t: 0 }],
                        down: null, drag: null, pinched: false, seen: 0 });
    const clear = c => {
      if (c.drag) c.drag.card.style.opacity = "";
      c.drag = null; c.down = null;
      if (ghost) ghost.style.display = "none";
    };
    let clicks = 0;
    // capture on the card, so the probe click is counted and then stopped
    // before it reaches anything that would act on it
    const spy = e => { clicks++; e.stopPropagation(); e.preventDefault(); };
    card.addEventListener("click", spy, true);
    try {
      const a = mk();                                   // press, hold still,
      onPinch(a, 1000);                                 // release inside the
      tryDrag(a, 1033);                                 // window
      if (a.drag) bad.push("tap: a still press grabbed the card");
      if (card.style.opacity) bad.push("tap: a still press lifted the card");
      onRelease(a, 1100);
      if (clicks !== 1)
        bad.push("tap: press-and-release did not click (" + clicks + ")");
      clear(a);

      const d = mk();                                   // press, then travel
      onPinch(d, 2000);
      d.x = x + T.tapPx + 4;
      tryDrag(d, 2033);
      if (!d.drag) bad.push("drag: travelling past tapPx did not grab");
      clear(d);

      const h = mk();                                   // press, then outlast
      onPinch(h, 3000);
      tryDrag(h, 3000 + T.tapMs + 20);
      if (!h.drag) bad.push("drag: holding past tapMs did not grab");
      clear(h);

      if (clicks !== 1) bad.push("drag: a drag also clicked");
    } finally {
      card.removeEventListener("click", spy, true);
      card.style.opacity = "";
      if (ghost) ghost.style.display = "none";
    }
  }


  /* THE PINCH-DRIFT REGRESSION, and the reason the freeze exists at all.
     fakeHand opens and closes its gap by moving the THUMB, leaving the index
     tip and the whole palm exactly where they are — the drift in its purest
     form: the midpoint between the tips slides the full half-gap while the
     hand itself has not moved a millimetre.

     Two scenarios, each on its own cursor, because the second one must never
     pinch and a pinched hand would answer for the wrong reason. */
  function holdRig() {
    const c = { x: 0, y: 0, el: { style: {} }, hist: [], down: null, drag: null,
                pinched: false, seen: 0, hold: null, gapEma: null, palm: null,
                fx: { x: {}, d: {} }, fy: { x: {}, d: {} } };
    let now = 1000;
    // fakeHand is built for RATIOS — the gate only reads distances over
    // distances, so it puts the wrist at the origin and lets y run negative.
    // Nothing here can use those coordinates raw: axis() would clamp the whole
    // hand to one frame edge and every delta would come out zero. Scale it
    // down and set it in the frame; uniform scaling leaves the ratios alone.
    const hand = (g, dx) => fakeHand(1.35, 1.9, g, 0.40)
      .map(q => ({ x: 0.5 + (dx || 0) + q.x * 0.35, y: 0.55 + q.y * 0.35, z: 0 }));
    const step = g_or_L => {                         // one frame(), minus the DOM
      const L = Array.isArray(g_or_L) ? g_or_L : hand(g_or_L);
      const pt = points(L);
      c.palm = pt;
      c.x = euro(c.fx, c.hold ? c.hold.x + (pt.px - c.hold.px) : pt.tx, 1 / 30);
      c.y = euro(c.fy, c.hold ? c.hold.y + (pt.py - c.hold.py) : pt.ty, 1 / 30);
      c.hist.push({ x: c.x, y: c.y, t: now });
      if (c.hist.length > 10) c.hist.shift();
      readHand(L, c, now);
      now += 33;
    };
    return { c: c, hand: hand, step: step };
  }

  function holdCheck(bad) {
    // 1. a pinch: the cursor must let go of the fingertips as they close,
    //    still follow the hand while held, and be handed back on opening
    const r = holdRig();
    for (let i = 0; i < 12; i++) r.step(0.90);
    if (r.c.hold) bad.push("hold: an open hand held still froze the cursor");
    const aim = { x: r.c.x, y: r.c.y };

    for (const g of [0.75, 0.60, 0.45, 0.30, 0.20]) r.step(g);
    if (!r.c.hold) bad.push("hold: closing fingers never froze the cursor");
    if (!r.c.pinched) bad.push("hold: the closing sequence never pinched");
    const drift = Math.hypot(points(r.hand(0.20)).tx - aim.x,
                             points(r.hand(0.20)).ty - aim.y);
    const slip = Math.hypot(r.c.x - aim.x, r.c.y - aim.y);
    if (!(drift > 40))
      bad.push("hold: the fixture no longer reproduces the drift (" +
               drift.toFixed(0) + "px) — the assertion below proves nothing");
    else if (!(slip < drift / 8))
      bad.push("hold: cursor slid " + slip.toFixed(0) + "px of the " +
               drift.toFixed(0) + "px the fingertips moved");

    // Carry the pinch across the frame. The fingers do not move at all here,
    // only the hand — the motion a drag is made of, and the one the freeze
    // must NOT swallow.
    const held = r.c.x;
    for (let i = 1; i <= 8; i++) r.step(r.hand(0.20, i * 0.015));
    if (!(Math.abs(r.c.x - held) > 20))
      bad.push("hold: a held pinch stopped following the hand (" +
               (r.c.x - held).toFixed(0) + "px) — nothing could be dragged");

    for (let i = 1; i <= 10; i++) r.step(r.hand(0.90, 0.12 + i * 0.015));
    if (r.c.hold) bad.push("hold: opening the hand did not give the cursor back");

    /* 2. THE ONE THAT SHIPPED BROKEN. A hand that closes halfway and PARKS
       there — an ordinary resting posture, well under gapNear, and never a
       pinch — must get the steering back. A release hysteresis here latched
       it on the first small movement and never let go: everything after that
       steered by palm, at palm gain, with no fingertip articulation, and
       ordinary movement went slow and clunky with nothing to say why. The
       freeze is a transient, not a state. 0.50 stays clear of the 0.32 grab
       bar, so nothing here is a pinch. */
    const q = holdRig();
    for (let i = 0; i < 12; i++) q.step(0.90);
    for (const g of [0.75, 0.62, 0.50]) q.step(g);
    if (!q.c.hold) bad.push("hold: closing halfway did not freeze at all");
    if (q.c.pinched) bad.push("hold: 0.50 pinched — the parked case proves nothing");
    let freed = -1;
    for (let i = 0; i < 20 && freed < 0; i++) {
      q.step(0.50);
      if (!q.c.hold) freed = i;
    }
    if (freed < 0)
      bad.push("hold: a hand parked half-closed never got the cursor back — " +
               "everything it does now steers by palm");
    else if (freed > 12)
      bad.push("hold: took " + freed + " frames to give the cursor back");
    // and once back, it is following the fingertips again, not the palm
    let sx = 0;
    for (let i = 0; i < 12; i++) { sx += 0.02; q.step(q.hand(0.50, sx)); }
    if (q.c.hold) bad.push("hold: a parked hand re-froze while merely moving");
    // Let it settle before comparing. Mid-sweep the cursor legitimately
    // trails the fingertips by the filter's lag, and measuring that here
    // would be asserting the filter's tuning, not what is being steered by.
    for (let i = 0; i < 12; i++) q.step(q.hand(0.50, sx));
    const back = Math.abs(q.c.x - points(q.hand(0.50, sx)).tx);
    if (back > 2)
      bad.push("hold: released, but the cursor sits " + back.toFixed(0) +
               "px off the fingertips — it is still steering by palm");
  }

  function check() {
    const bad = [];
    axisCheck(bad);
    gateCheck(bad);
    filterCheck(bad);
    holdCheck(bad);
    tapCheck(bad);
    const g = grid();
    if (!g) {
      console[bad.length ? "error" : "log"]("hands check:",
        bad.length ? bad
          : "axis + gate + filter + hold ok (no #glass-layer here, grid and tap untested)");
      return bad.length ? bad : "ok";
    }
    for (let c = 0; c < 12; c++) for (let r = 0; r < 8; r++) {
      const b = cellRect(g, c, r, 1, 1);
      const got = cellAt(g, b.left + b.width / 2, b.top + b.height / 2);
      if (got.c !== c || got.r !== r) bad.push("centre " + c + "," + r);
    }
    for (const el of document.querySelectorAll(".glass-card")) {
      const p = cardPos(el);
      if (!p) { bad.push("no cardPos: " + el.dataset.id); continue; }
      // glass.js FLIPs a card between slots with a temporary transform, so
      // mid-animation its rect legitimately disagrees with its grid slot
      if (getComputedStyle(el).transform !== "none") continue;
      const w = el.getBoundingClientRect(), o = cellRect(g, p.c, p.r, p.w, p.h);
      const d = Math.max(Math.abs(w.left - o.left), Math.abs(w.top - o.top),
                         Math.abs(w.width - o.width),
                         Math.abs(w.height - o.height));
      if (d > 1) bad.push(el.dataset.id + " off " + d.toFixed(2) + "px");
      // THE JITTER REGRESSION. A hand holding still is not still, and with
      // floor() a grabbed card's corner starts exactly ON a cell boundary —
      // so every card stepped a cell up-left on as little as two pixels of
      // shake. The snap has to survive a third of a cell in every direction.
      const jx = (g.cw + g.gx) / 3, jy = (g.ch + g.gy) / 3;
      for (const j of [-1, -0.5, -0.02, 0, 0.02, 0.5, 1]) {
        const n = cellNear(g, w.left + j * jx, w.top + j * jy);
        if (n.c !== p.c || n.r !== p.r) {
          bad.push(el.dataset.id + " jitters " + p.c + "," + p.r + " -> " +
                   n.c + "," + n.r);
          break;
        }
      }
    }
    console[bad.length ? "error" : "log"]("hands check:",
      bad.length ? bad : "axis + gate + filter + hold + tap + 96 cells + " +
        document.querySelectorAll(".glass-card").length + " cards ok");
    return bad.length ? bad : "ok";
  }

  if (DEBUG)
    Object.assign(window.AVHands,
                  { grid, cellAt, cellRect, cardPos, corner, T, check,
                    // the gesture path, drivable with a fake cursor so the
                    // drag can be tested without a hand in frame
                    onPinch, onDragMove, onRelease, readHand, axis, cellNear,
                    beginDrag, stretchDrag, finishDrag, portBox, tryDrag,
                    euro, anchor, points, fakeHand,
                    portrait: () => port });

  function boot() {
    buildLayer();      // the ghost: the MOUSE drag needs it with hands off
    // A remembered toggle still needs a user gesture in some browsers before
    // getUserMedia will prompt; if it is refused, the settings screen and
    // the H key are both right there.
    if (Q.get("hands") === "1" || stored() === "1") start();
  }
  if (document.body) guard(boot)();
  else addEventListener("DOMContentLoaded", guard(boot));
})();
