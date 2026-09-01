/*
 * ai-visualizer: give your AI agent a face.
 * Copyright (C) 2026 Jared Rhodenizer
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as published
 * by the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program. If not, see <https://www.gnu.org/licenses/>.
 *
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */
/* ============================================================
   ai-visualizer core — the shared plumbing every face rides on.

   A face is one self-contained page in faces/<name>/index.html.
   It includes this script, calls AV.init(opts), then reads these
   fields every animation frame after calling AV.tick(dtMs):

     AV.state      "idle" | "listening" | "thinking" | "speaking"
     AV.level      0..1 raw voice loudness (speaking only)
     AV.env        0..1 smoothed speech envelope (attack/release eased,
                   adaptively normalized — use this for motion)
     AV.samples    Float32Array(64), 0..1 normalized waveform ring
     AV.alert      bool, optional attention signal
     AV.micLevel   0..1 your microphone (only if init({mic:true}))
     AV.name       display name from config ("JARVIS" by default)
     AV.label      the dotted chip label ("J.A.R.V.I.S.")
     AV.badge      optional handle from config ("" by default)
     AV.glass      the glass overlay board from /state (read by glass.js;
                   null when the server runs no glass)

   Modes:
     live   served by server.py — rides the real signal bus
     demo   ?demo=1, or the page opened as a plain file — a scripted
            voice-turn loop (idle, listening, thinking, speaking) with
            synthesized audio, so every face performs with no voice
            line installed
     shot   ?shot=<state>&t=ms — pins one state and runs the frame
            loop deterministically, then sets document.title to
            "ready" (screenshot/verification harness)

   The thinking sound: assets/thinking.wav plays while the state is
   "thinking", exactly like a voice line would play it. If the bus
   says the voice line is already playing its own (.voice_loading_pid),
   this player stays quiet — you never hear it twice. The speaker
   button (bottom left) toggles it; browsers may require one click on
   the page before audio is allowed.
   ============================================================ */
"use strict";

const AV = (() => {
  const Q = new URLSearchParams(location.search);
  const SHOT = Q.get("shot");
  const SHOT_T = parseInt(Q.get("t") || "4000", 10);
  const DEMO = Q.get("demo") === "1" || location.protocol === "file:" || !!SHOT;

  // where core.js lives -> where assets/ lives (works over http and file://)
  const ROOT = new URL(".", document.currentScript.src);

  // The glass overlay rides face pages only. The pathname match is sound
  // for this stack: server.py discovers every face — custom and promoted
  // ones included — exclusively by scanning faces/, and the gallery at "/"
  // (whose whole purpose is clickable cards the overlay would occlude)
  // never matches. The same id rides every /state poll so the server can
  // keep its effective reserve honest. ?glassdemo=1 forces the overlay
  // with local fixtures in any mode, even file:// — serverless eyeball-QA.
  const FACE = (location.pathname.match(/^\/faces\/([^\/]+)\//) || [])[1] || null;
  const GLASSDEMO = Q.get("glassdemo") === "1";
  // THE TAB ICON. Same icon as the Dock app, from the same one file, so a
  // browser tab, the gallery and the Dock are recognisably one thing --
  // without it every face page gets the browser's blank-page glyph.
  // Injected here rather than pasted into each faces/*/index.html: there
  // are five of those and a sixth is one `cp -r` away. A face that sets
  // its own icon keeps it.
  if (!document.querySelector('link[rel~="icon"]')) {
    const ico = document.createElement("link");
    ico.rel = "icon";
    ico.type = "image/svg+xml";
    ico.href = new URL("icon.svg", ROOT).href;
    document.head.appendChild(ico);
  }
  function loadGlass() {
    const s = document.createElement("script");
    s.src = new URL("glass.js", ROOT).href;
    document.head.appendChild(s);
  }
  if (GLASSDEMO) loadGlass();
  // hands.js — camera pointer (HANDS-SPEC.md). Loaded on every page core.js
  // rides, because it owns its own H toggle; it opens no camera and fetches
  // nothing until it is switched on.
  {
    const s = document.createElement("script");
    s.src = new URL("hands.js", ROOT).href;
    document.head.appendChild(s);
  }
  // prompt.js — P opens a box, Enter sends the line to the voice line as
  // a typed turn. Every page core.js rides except demo and shot modes,
  // which have no server behind them to send to.
  if (!DEMO) {
    const s = document.createElement("script");
    s.src = new URL("prompt.js", ROOT).href;
    document.head.appendChild(s);
  }
  // hub.js — the folder hub on the face (barehands' orbital bloom). Face
  // pages only, and it needs the server behind it for /tree and /note, so
  // it skips demo and shot modes the way glass does.
  if (FACE && !DEMO) {
    const s = document.createElement("script");
    s.src = new URL("hub.js", ROOT).href;
    document.head.appendChild(s);
  }
  function loadChat() {
    const s = document.createElement("script");
    s.src = new URL("chat.js", ROOT).href;
    document.head.appendChild(s);
  }

  const A = {
    state: "idle", level: 0, env: 0, alert: false, micLevel: 0,
    samples: new Float32Array(64),
    name: "JARVIS", label: "J.A.R.V.I.S.", badge: "",
    demo: DEMO, shot: SHOT, faces: [],
    _sndOn: true, _mic: false, _readyCbs: [], _ready: false,
  };

  function dotted(name) {
    const up = String(name).toUpperCase();
    if (/^[A-Z0-9]{2,10}$/.test(up)) return up.split("").join(".") + ".";
    return up;
  }

  /* -------------------------------- config -------------------------------- */
  function applyConfig(cfg) {
    if (cfg.name) { A.name = String(cfg.name); A.label = dotted(A.name); }
    // a portrait theme fronts its own name — SHODAN's board says SHODAN
    const tn = typeof AVTheme !== "undefined" && AVTheme.overrides.name;
    if (tn) { A.name = String(tn); A.label = dotted(tn); }
    A.badge = String(cfg.badge || "");
    // The brain block, when the signals server publishes it here rather
    // than on /state (either lane is fine; the settings picker reads both,
    // and simply says "unknown" when neither carries it).
    if (cfg.brain && typeof cfg.brain === "object") _cfgBrain = cfg.brain;
    if (cfg.thinking_sound === false) A._sndWant = false;
    A.faces = cfg.faces || [];
    // glass only when the server says so, on a face page, outside demo and
    // shot modes (the screenshot harness must stay untouched)
    if (cfg.glass && FACE && !DEMO && !GLASSDEMO) loadGlass();
    // the conversation crawl rides the same face-page gating
    if (cfg.chat && FACE && !DEMO && !GLASSDEMO) {
      A.chatArea = cfg.chat_area || null;
      loadChat();
    }
    A._ready = true;
    A._readyCbs.forEach(cb => cb(A));
    A._readyCbs = [];
  }

  A.ready = cb => { A._ready ? cb(A) : A._readyCbs.push(cb); };

  /* ------------------------------ bus polling ------------------------------ */
  let raw = { state: "idle", level: 0, samples: null, alert: false,
              loading: false };
  if (!DEMO) {
    // harmless when the glass is off — the server just ignores ?face=
    const STATE_URL = FACE ? "/state?face=" + encodeURIComponent(FACE) : "/state";
    /* ponytail: "server gone" used to be a silent catch, so the face kept
       breathing over a dead agent — close the terminal and nothing in the
       browser said so. Two and a half seconds of failed polls and it says
       so, in the theme's own colors; the next good poll clears it, so a
       restart heals itself with no reload. */
    let lastOk = Date.now(), offEl = null, offWas = null;
    /* One curtain, two reasons to draw it: the voice line is gone, or it
       has not warmed up yet. The server is up long before the agent can
       speak, so without the second case the face looks alive for ten or
       twenty seconds of silence. It lifts on the first word, never
       before. */
    function offline(why) {
      if (why === offWas) return;
      offWas = why;
      if (offEl) { offEl.remove(); offEl = null; }
      if (!why) return;
      const lost = why === "lost";   // else "warm:<stage>"
      if (!_statusEl) statusBuild();   // it owns the av-breathe keyframe
      offEl = document.createElement("div");
      offEl.id = "av-offline";
      offEl.style.cssText =
        "position:fixed;inset:0;z-index:60;display:flex;align-items:center;" +
        "justify-content:center;text-align:center;background:rgba(0,0,0,.74);" +
        "font-family:var(--av-display,'SF Mono',Menlo,monospace);cursor:default";
      offEl.innerHTML =
        '<div>' +
        '<div class="k" style="font-size:11px;letter-spacing:.3em;color:var(--av-dim,#5a6a72)"></div>' +
        '<div class="n" style="margin:16px 0;font-size:clamp(20px,2.6vw,36px);letter-spacing:.42em;' +
        'color:var(--av-accent,#3ddc84);text-shadow:0 0 20px var(--av-glow,rgba(61,220,132,.45))"></div>' +
        '<div class="s" style="font-size:11px;letter-spacing:.24em;color:var(--av-dim,#5a6a72);' +
        'animation:av-breathe 2s ease-in-out infinite"></div></div>';
      offEl.querySelector(".k").textContent = lost ? "SIGNAL LOST" : "STANDBY";
      offEl.querySelector(".n").textContent = A.label || dotted(A.name || "agent");
      // read the stage out of `why` (not A.stage): the poll is the fresh
      // one, and A.* only advances when a face is calling AV.tick
      const stage = lost ? "" : why.slice(5);
      offEl.querySelector(".s").textContent = lost
        ? "THE AGENT STOPPED"
        : ((stage || "getting ready").toUpperCase() + " \u00b7 STAND BY");
      document.body.appendChild(offEl);
    }
    setInterval(async () => {
      try {
        const r = await fetch(STATE_URL, { cache: "no-store" });
        raw = await r.json();
        lastOk = Date.now();
      } catch (e) { /* server gone: hold last state, and say so below */ }
      // "lost" outranks "warming": a dead server cannot be warming up.
      // `ready` is absent on an older voice line, so treat a missing
      // field as ready and never curtain a setup that cannot say so.
      const warming = raw && raw.ready === false;
      offline(Date.now() - lastOk > 2500 ? "lost"
              : warming ? "warm:" + (raw.stage || "") : null);
    }, 120);
  }

  /* ------------------------------ demo driver ------------------------------ */
  // A scripted voice turn: the face performs everything with no voice line.
  const SCRIPT = [["idle", 6000], ["listening", 3500], ["thinking", 4200],
                  ["speaking", 8500]];
  let demoT = 0, demoClock = 0;
  const PIN = SHOT || Q.get("state");   // ?state=speaking pins the demo
  function demoUpdate(dt) {
    demoClock += dt;
    let st = PIN || "idle";
    if (!PIN) {
      demoT = (demoT + dt) % SCRIPT.reduce((a, s) => a + s[1], 0);
      let t = demoT;
      for (const [name, len] of SCRIPT) {
        if (t < len) { st = name; break; }
        t -= len;
      }
    }
    const tt = demoClock / 1000;
    const speaking = st === "speaking";
    const cadence = speaking
      ? Math.max(0, Math.sin(tt * 2.1) * 0.6 + Math.sin(tt * 0.9) * 0.5)
      : 0;
    const samples = new Array(64);
    for (let i = 0; i < 64; i++) {
      // drifting per-sample color so the synthetic voice has a moving
      // spectrum, not a steady tone — spectrum-driven faces dance
      const m = 0.3 + 0.7 * Math.abs(Math.sin(i * 0.23 + tt * 1.7))
        * Math.abs(Math.sin(tt * 2.9 + i * 0.05));
      samples[i] = speaking
        ? (Math.sin(i * 0.55 + tt * 9) * 0.6 + Math.sin(i * 1.7 - tt * 13)
           * 0.4) * 9000 * (0.15 + 0.85 * cadence) * m
        : 0;
    }
    raw = { state: st, level: speaking ? Math.min(1, cadence) : 0,
            samples, alert: false, loading: false };
    if (st === "listening")
      A.micLevel = 0.25 + 0.55 * Math.abs(Math.sin(tt * 2.7))
        * Math.abs(Math.sin(tt * 0.61));
  }

  /* ----------------------- envelope + samples easing ----------------------- */
  let peak = 0.05, sPeak = 200;
  // A small always-on corner badge naming the microphone mode, colored
  // by how much the room is being heard: red = open mic (everything),
  // amber = wake word (name first; bright while a follow-up window is
  // live), green = push to talk (mic closed). Faces don't draw this —
  // it must be identical and truthful everywhere, custom faces included.
  let _micEl = null, _micKey = "";
  function updateMicBadge(mic) {
    if (!mic) {
      if (_micEl) { _micEl.remove(); _micEl = null; _micKey = ""; }
      return;
    }
    const key = mic.mode + (mic.hot ? "+hot" : "");
    if (key === _micKey) return;
    _micKey = key;
    if (!_micEl) {
      _micEl = document.createElement("div");
      _micEl.id = "av-mic-badge";
      _micEl.style.cssText =
        "position:fixed;right:14px;bottom:12px;z-index:40;" +
        "font:16px var(--av-display,'VT323'),monospace;letter-spacing:.12em;" +
        "padding:3px 10px;border:1px solid;border-radius:3px;" +
        "background:rgba(0,0,0,.45);pointer-events:none;" +
        "display:flex;align-items:center;gap:8px;";
      const dot = document.createElement("span");
      dot.style.cssText = "width:8px;height:8px;border-radius:50%;" +
        "display:inline-block;";
      _micEl.appendChild(dot);
      _micEl.appendChild(document.createElement("span"));
      document.body.appendChild(_micEl);
    }
    const [dot, label] = _micEl.children;
    const looks = {
      open: ["#ff5f56", "OPEN MIC"],
      wake: [mic.hot ? "#ffd166" : "#b8860b",
             mic.hot ? "WAKE · LIVE" : "WAKE WORD"],
      ptt: ["#27c93f", "PUSH TO TALK"],
    };
    const [color, text] = looks[mic.mode] || ["#888", mic.mode];
    dot.style.background = color;
    dot.style.boxShadow = mic.mode === "ptt" ? "none"
      : "0 0 8px " + color;
    label.textContent = text;
    _micEl.style.color = color;
    _micEl.style.borderColor = color;
  }

  // The board face's cinematic flythrough — its idle screensaver and its
  // Space key. Local and immediate like HANDS below: the face reads this
  // property every frame, SETTINGS flips it and remembers the choice.
  try { window.AV_CINE = localStorage.getItem("av_cine") !== "0"; }
  catch (_) { window.AV_CINE = true; }

  // The launch-time settings picker — one screen, three tabs. The voice
  // line publishes mic mode "select" and BLOCKS until a choice arrives, so
  // at launch this opens on LISTEN: the only tab that unblocks the boot.
  // Its buttons POST to /pick, the server writes the pick file, the voice
  // line boots in that mode, publishes the real mode, and this overlay
  // removes itself on the next poll. Keys: TAB / Shift-TAB walk the tabs,
  // 1/2/3 pick the mic mode from ANY tab (muscle memory outranks the tab
  // you happen to be on), the arrows drive the open tab's own controls,
  // Enter confirms, Esc dismisses a mid-session summon.
  let _pickEl = null, _pickSel = null, _pickGo = null;
  // Only the open tab's body is displayed: three stacked bodies would grow
  // the screen past the viewport, and the picker must never need scrolling
  // to reach SAVE.
  // LOOK and LISTEN are ONE tab: they are the two things changed together
  // (a theme and how she hears you), and splitting them meant two trips for
  // one sitting. BRAIN stays apart — it ends the conversation to apply.
  // THE SETTINGS SCREEN IS A SUMMARY, NOT A FORM. Two tabs holding every
  // control at once meant reading five rows of buttons to learn what a
  // single thing was set to, and the two hint paragraphs that explain
  // HANDS and CINEMATIC sat on the same screen as the choice the boot was
  // blocked on. So: a home page of one line per section — name, what it
  // is set to right now, EDIT — and a page per section behind it, each
  // with BACK at the top. Nothing changes about what the controls do or
  // when they commit; only how much is on screen at once.
  //
  // The third entry is the line under the section name on the home page.
  // It answers "what is this set to", in words, and is repainted on every
  // _pickPaint — a summary that can go stale is just a menu.
  // The fourth field is how the setting is EDITED, and it is chosen by how
  // big the setting is, not for consistency's sake:
  //   "page"   real choices with real consequences — its own screen
  //   "menu"   one of a growing list — a dropdown in the row
  //   "toggle" two states — the buttons ARE the summary; an EDIT that leads
  //            to one pair of buttons is a page nobody needed
  const _SECTIONS = [
    ["theme", "THEME", () => _sumTheme(), "menu"],
    ["listen", "LISTENING", () => _sumListen(), "menu"],
    ["brain", "BRAIN", () => _sumBrain(), "page"],
    ["hands", "HANDS", () => _sumHands(), "toggle"],
    ["cine", "CINEMATIC", () => _sumCine(), "toggle"],
  ];
  let _themeMenu = null, _listenMenu = null, _brainMenu = null, _mdlMenu = null;
  // "home" or a section id; _pickRow is the keyboard highlight on home.
  let _pickPage = "home", _pickRow = 0, _pickBodies = {}, _pickRows = {};
  // BRAIN. _brainNow is the provider the SERVER says is live; _brainSel is
  // what the owner has picked on this screen. SAVE posts only when those
  // two disagree — a redundant post costs a real brain restart, a dropped
  // conversation and a spoken line. The API key never lives here: the UI
  // knows only whether one EXISTS and its last four characters.
  let _brainSel = null, _brainNow = null, _brainSig = "", _brainKey4 = "";
  // Which tier of the chosen brain answers. Same two-value dance as the
  // provider above: _mdlNow is what the server says is live, _mdlSel what
  // this screen is holding.
  let _mdlSel = null, _mdlNow = null, _mdlRow = null;
  // The tiers each brain offers, id -> label, and the one a brain nobody
  // has picked a tier for is already on. MUST match provider.py's
  // PROVIDERS[p].variants / .variant — the visualizer server allowlists
  // these ids too (BRAIN_MODELS), so an id invented here is dropped on
  // the way through rather than honoured.
  const _MDL = {
    claude: { fast: "FASTEST", balanced: "BALANCED", think: "THINKING" },
    zai: { "glm-5.3": "GLM-5.3", "glm-5.3-flash": "5.3 FLASH" },
  };
  const _MDL_DEF = { claude: "balanced", zai: "glm-5.3" };
  const _mdlIds = p => Object.keys(_MDL[p] || {});
  let _brainStatEl = null, _brainKeyRow = null,
      _brainKeyIn = null, _brainKeyGo = null, _brainKeyMsg = null,
      _brainWarn = null, _cfgBrain = null;
  const _BRAIN_LABEL = { claude: "CLAUDE", zai: "Z.AI" };
  // Esc on a face page summons the same select screen mid-session
  // (and dismisses it); the blocking launch-time picker ignores Esc.
  let _pickForce = false, _lastMic = null;
  // A theme step in the picker navigates to the previewed theme's own
  // face (full-fidelity preview: real art, baked colors) — this payload
  // carries the open picker across that navigation. While it is pending,
  // convergence and garden checks hold, or the fresh page would bounce
  // straight back before the picker rebuilds. EVERY choice the screen is
  // holding rides in it (mic mode, theme baseline, open tab, provider) —
  // anything left out is silently lost on each theme step.
  let _pickResume = null;
  try {
    _pickResume = JSON.parse(sessionStorage.getItem("av_pick_resume") || "null");
    if (_pickResume) sessionStorage.removeItem("av_pick_resume");
  } catch (_) { _pickResume = null; }
  if (_pickResume && _pickResume.force) _pickForce = true;
  // Preview transitions dip through black instead of flashing the bare
  // face: the leaving page fades out, and the arriving page (resume
  // pending) starts covered until its picker is rebuilt, so the new
  // face only ever appears behind the picker, fading in.
  let _fadeEl = null;
  function _fadeCover(instant) {
    if (!_fadeEl) {
      _fadeEl = document.createElement("div");
      _fadeEl.id = "av-pick-fade";
      _fadeEl.style.cssText =
        "position:fixed;inset:0;z-index:70;background:#000;" +
        "opacity:0;transition:opacity .4s ease;pointer-events:none;";
      (document.body || document.documentElement).appendChild(_fadeEl);
    }
    if (instant) _fadeEl.style.transition = "none";
    _fadeEl.style.opacity = "1";
  }
  function _fadeAway() {
    if (!_fadeEl) return;
    const el = _fadeEl;
    _fadeEl = null;
    el.style.transition = "opacity .8s ease";
    requestAnimationFrame(() => { el.style.opacity = "0"; });
    setTimeout(() => el.remove(), 1100);
  }
  function _fadeThen(fn) {
    _fadeCover(false);
    setTimeout(fn, 430);
  }
  if (_pickResume) {
    _fadeCover(true);
    setTimeout(_fadeAway, 4000);   // never leave the screen black
  }
  // Walled garden: a theme and its face travel together (theme.face in
  // theme.js). Returns the URL this page should move to for `id`, or
  // null when already paired / not on a face page (gallery etc.).
  let _faceNav = false;
  function _faceHome(id) {
    const t = typeof AVTheme !== "undefined" && AVTheme.themes[id];
    const here = (location.pathname.match(/\/faces\/([^/]+)\//) || [])[1];
    if (!t || !t.face || !here || here === t.face) return null;
    return "/faces/" + t.face + "/index.html";
  }
  window.addEventListener("keydown", e => {
    if (e.key !== "Escape" || !FACE || _pickEl) return;
    // Esc means STOP first and settings second. While she is thinking or
    // talking it is the panic key: drop the turn, go back to resting, keep
    // listening in whatever mic mode is on. Only an idle face has nothing
    // to stop, and there Esc still summons the select screen.
    if (A.state === "thinking" || A.state === "speaking") {
      _postJSON("stop", {}).catch(() => {});
      return;
    }
    _pickForce = true;
    updateModePicker(_lastMic);
  });
  function _postJSON(path, body) {
    return fetch(new URL(path, ROOT).href, {
      method: "POST",
      // the server's whole cross-site write defense — a form or text POST
      // is refused, so keep this header on every one of these calls
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }
  function _postPick(mode) {
    return _postJSON("pick", { mode }).catch(() => {});
  }
  // one voice, one color: read the live accent each paint so a theme flip
  // inside the picker recolors everything it drew
  function _accent() {
    return getComputedStyle(document.documentElement)
      .getPropertyValue("--av-accent").trim() || "#3ddc84";
  }

  /* -------------------------------- tabs -------------------------------- */
  /* --------------------------- summary lines --------------------------- */
  // Each answers one question in the owner's own words: what is this set
  // to? Never "configure…" — a home page that does not say the values is
  // a menu, and the screen it replaced was at least honest about that.
  const _MIC_SHORT = { open: "Open mic", wake: "Wake word",
                       ptt: "Push to talk" };
  const _MIC_WORD = { open: "she hears the whole room",
                      wake: "say the name first",
                      ptt: "mic closed until the key" };
  function _sumTheme() {
    if (typeof AVTheme === "undefined") return "no themes loaded";
    // The dropdown says WHICH theme, so this line is free to say the one
    // thing the dropdown cannot: a previewed theme is real on screen and
    // not written yet, and that gap is what a summary must never hide.
    const staged = _pickEl && AVTheme.current !== _pickEl.dataset.theme0;
    return staged ? "unsaved — the button below commits it" : "";
  }
  function _sumListen() { return _MIC_WORD[_pickSel] || "not chosen"; }
  function _sumBrain() {
    const p = _brainSel || _brainNow;
    if (!p) return "unknown";
    const tier = (_MDL[p] || {})[_mdlSel] || "";
    const st = _brainStatus(p);
    return _BRAIN_LABEL[p] + (tier ? "  ·  " + tier : "")
      + (st ? "  ·  " + st : "");
  }
  // The toggle shows the state, so these say what the thing IS — the two
  // hint paragraphs that used to sit on the launch screen, one line each.
  function _sumHands() { return "pinch to click · drag to move"; }
  function _sumCine() { return "flies itself when idle · space runs it"; }

  // Choosing or stepping a theme: preview it for REAL — land on the paired
  // face with baked colours, the settings screen riding across the
  // navigation. Nothing commits until START / SAVE & CONTINUE.
  function _themePick(id) {
    if (typeof AVTheme === "undefined" || !_pickEl) return;
    AVTheme.set(id, false);
    _pickPaint();
    try {
      // EVERY held choice rides along, or the step quietly eats it
      sessionStorage.setItem("av_pick_resume", JSON.stringify({
        sel: _pickSel, theme0: _pickEl.dataset.theme0, force: _pickForce,
        page: _pickPage, prov: _brainSel, mdl: _mdlSel }));
    } catch (_) {}
    const home = _faceHome(id);
    _fadeThen(() => { if (home) location.href = home; else location.reload(); });
  }
  function _themeIds() {
    if (typeof AVTheme === "undefined") return [];
    return Object.keys(AVTheme.themes).filter(k => !AVTheme.themes[k].wip);
  }
  function _themeStep(d) {
    const ids = _themeIds();
    if (!ids.length) return;
    const i = Math.max(ids.indexOf(AVTheme.current), 0);
    _themePick(ids[(i + d + ids.length) % ids.length]);
  }

  /* ------------------------------ dropdowns ----------------------------- */
  // A menu that follows the theme. NOT <select>: a native popup is OS
  // chrome — it cannot take a font, an accent or a border, and "styled like
  // the theme" is the entire reason this control exists instead of the row
  // of buttons it replaced. opts is [[value, label], ...]; cur() reads the
  // live value; ok(v) hides options that do not apply right now (the model
  // list is every brain's tiers, showing one brain's at a time).
  let _menus = [];
  function _mkMenu(opts, cur, onPick, o) {
    o = o || {};
    const wrap = document.createElement("div");
    wrap.style.cssText = "position:relative;display:flex;";
    const btn = document.createElement("button");
    btn.style.cssText =
      "font:17px/1.2 var(--av-display,'VT323'),monospace;letter-spacing:.14em;" +
      "padding:7px 14px;cursor:pointer;text-align:left;white-space:nowrap;" +
      "min-width:" + (o.width || 200) + "px;background:rgba(0,0,0,.45);" +
      "border:1px solid;border-radius:3px;";
    const list = document.createElement("div");
    list.style.cssText =
      "display:none;position:absolute;top:calc(100% + 4px);left:0;right:0;" +
      "z-index:3;flex-direction:column;background:var(--av-bg2,#020705);" +
      "border:1px solid;border-radius:3px;overflow:hidden;" +
      "box-shadow:0 10px 28px rgba(0,0,0,.75);";
    const items = [];
    for (const [v, label] of opts) {
      const it = document.createElement("button");
      it.textContent = label;
      it.dataset.v = v;
      it.style.cssText =
        "font:16px/1.3 var(--av-display,'VT323'),monospace;letter-spacing:.12em;" +
        "padding:9px 14px;cursor:pointer;text-align:left;white-space:nowrap;" +
        "background:transparent;border:0;" +
        "border-top:1px solid var(--av-card-line,#1c2f26);";
      // stopPropagation: the overlay closes every open menu on any click,
      // which would otherwise eat the option's own click on the way past
      it.onclick = e => { e.stopPropagation(); m.close(); onPick(v); };
      it.onmouseenter = () => { it.style.background = _accent() + "26"; };
      it.onmouseleave = () => { it.style.background = "transparent"; };
      items.push(it);
      list.appendChild(it);
    }
    wrap.appendChild(btn);
    wrap.appendChild(list);
    btn.onclick = e => {
      e.stopPropagation();
      const was = list.style.display === "flex";
      for (const other of _menus) other.close();
      if (!was) list.style.display = "flex";
    };
    const m = {
      el: wrap,
      close: () => { list.style.display = "none"; },
      open: () => list.style.display === "flex",
      paint: () => {
        const ac = _accent();
        const v = cur();
        let first = true;
        for (const it of items) {
          const show = !o.ok || o.ok(it.dataset.v);
          it.style.display = show ? "" : "none";
          if (show) { it.style.borderTopWidth = first ? "0" : "1px"; first = false; }
          it.style.color = it.dataset.v === v ? ac : "var(--av-dim,#5a6a72)";
        }
        const hit = opts.find(x => x[0] === v);
        btn.textContent = (hit ? hit[1] : "—") + "  \u25be";
        btn.style.color = ac;
        btn.style.borderColor = ac;
        list.style.borderColor = ac;
      },
    };
    _menus.push(m);
    return m;
  }
  // label + control, the same line shape as a summary row — so a section
  // page reads as more of the screen it was opened from, not another idiom
  function _detailRow(label) {
    const el = document.createElement("div");
    el.style.cssText =
      "display:flex;align-items:center;gap:18px;width:min(520px,84vw);" +
      "padding:9px 14px;border:1px solid var(--av-card-line,#1c2f26);" +
      "border-radius:4px;background:rgba(0,0,0,.35);";
    const nm = document.createElement("div");
    nm.textContent = label;
    nm.style.cssText =
      "font:19px/1.2 var(--av-display,'VT323'),monospace;letter-spacing:.22em;" +
      "color:var(--av-fg,#cfe);min-width:126px;";
    const slot = document.createElement("div");
    slot.style.cssText =
      "flex:1;display:flex;gap:10px;align-items:center;position:relative;" +
      "justify-content:flex-end;";
    el.appendChild(nm);
    el.appendChild(slot);
    return { el: el, slot: slot };
  }

  /* ------------------------------ the router ---------------------------- */
  function _navPaint() {
    const ac = _accent();
    for (const [id, b] of Object.entries(_pickBodies))
      b.style.display = id === _pickPage ? "flex" : "none";
    _SECTIONS.forEach(([id, , sum], i) => {
      const r = _pickRows[id];
      if (!r) return;
      r.val.textContent = sum();
      const on = _pickPage === "home" && i === _pickRow;
      r.el.style.borderColor = on ? ac : "var(--av-card-line,#1c2f26)";
      r.el.style.background = on ? ac + "1e" : "rgba(0,0,0,.55)";
      // only "page" rows have an EDIT — a menu or a toggle IS the control
      if (r.edit) {
        r.edit.style.color = on ? ac : "var(--av-dim,#5a6a72)";
        r.edit.style.borderColor = on ? ac : "var(--av-card-line,#1c2f26)";
      }
    });
  }
  function _navGo(page) {
    if (page !== "home" && !_pickBodies[page]) return;
    _pickPage = page;
    const i = _SECTIONS.findIndex(sec => sec[0] === page);
    if (i >= 0) _pickRow = i;
    _pickPaint();
    if (_pickEl) _pickEl.scrollTop = 0;
  }
  function _rowStep(d) {
    const n = _SECTIONS.length;
    _pickRow = ((_pickRow + d) % n + n) % n;
    _navPaint();
  }

  /* -------------------------------- brain ------------------------------- */
  // The server's word on the brain: /state carries it, /config may carry it
  // instead — read either, and survive a server that publishes neither (an
  // older signals server simply has no BRAIN news to tell, and this screen
  // must still open).
  function _brainRead() {
    const b = (raw && raw.brain) || _cfgBrain || null;
    return b && typeof b === "object" ? b : null;
  }
  // What to say under each provider. Never the key — only whether one
  // exists, and its last four.
  function _brainStatus(prov) {
    const b = _brainRead();
    const s = b && b[prov];
    if (prov === "zai") {
      const l4 = String((s && (s.last4 || s.key_last4)) || _brainKey4 || "");
      const has = !!l4 || (s && (s.key === true || s.stored === true ||
                                 s.has_key === true));
      if (!s && !_brainKey4) return "";
      if (!has) return "no key yet";
      return l4 ? "key stored ····" + l4.slice(-4)
                : "key stored";
    }
    if (!s) return "";
    const on = s.signed_in === true || s.logged_in === true;
    const off = s.signed_in === false || s.logged_in === false;
    return on ? "signed in"
         : off ? "signed out — run claude login" : "";
  }
  function _brainPaint() {
    if (!_pickEl) return;
    // A selection left over from the OTHER brain is not a tier here, so it
    // falls back to what the server says is live and then to this brain's
    // default — a never-touched menu still means something, and SAVE can
    // never post a tier this brain never heard of.
    const ids = _mdlIds(_brainSel);
    if (ids.length && ids.indexOf(_mdlSel) < 0)
      _mdlSel = ids.indexOf(_mdlNow) >= 0 ? _mdlNow : _MDL_DEF[_brainSel];
    if (_brainMenu) _brainMenu.paint();
    if (_mdlMenu) _mdlMenu.paint();
    if (_mdlRow) _mdlRow.style.display = ids.length ? "flex" : "none";
    if (_brainStatEl) {
      const p = _brainSel || _brainNow;
      const st = p ? _brainStatus(p) : "";
      const live = !!p && p === _brainNow;
      // "status unknown · live" was a sentence arguing with itself: with no
      // detail from the server, being the running brain IS the status
      _brainStatEl.textContent = st ? st + (live ? "  ·  live" : "")
                                    : (live ? "running" : "status unknown");
      _brainStatEl.style.color = live ? "var(--av-accent,#3ddc84)"
                                      : "var(--av-dim,#5a6a72)";
      _brainStatEl.style.opacity = st ? "1" : ".5";
    }
    // the key field belongs to Z.AI only — Claude signs in through the CLI
    if (_brainKeyRow)
      _brainKeyRow.style.display = _brainSel === "zai" ? "flex" : "none";
    if (_brainWarn) {
      // not fine print: he is about to lose the thread he is in
      const moved = (!!_brainSel && !!_brainNow && _brainSel !== _brainNow)
        || (!!_mdlSel && !!_mdlNow && _mdlSel !== _mdlNow
            && ids.indexOf(_mdlSel) >= 0);
      _brainWarn.style.display = moved ? "block" : "none";
    }
    _navPaint();
  }
  // Refreshed while the picker stands, so a key just saved (or a provider
  // switched from another display) shows up with no reload. It moves the
  // BASELINE and the status lines — never the owner's own selection.
  function _brainSync() {
    let sig = "";
    try { sig = JSON.stringify(_brainRead()); } catch (_) { sig = ""; }
    if (sig === _brainSig) return;
    _brainSig = sig;
    const b = _brainRead();
    const p = b && b.provider;
    if (p === "claude" || p === "zai") {
      _brainNow = p;
      if (!_brainSel) _brainSel = p;   // seed once; a click always wins
      const m = b && b.model;
      if (_mdlIds(p).indexOf(m) >= 0) {
        _mdlNow = m;
        if (!_mdlSel) _mdlSel = m;
      }
    }
    _brainPaint();
  }
  // /config is fetched once at boot, so when the key status lives THERE a
  // fresh save would otherwise not show until a reload.
  function _brainRefetch() {
    fetch("/config", { cache: "no-store" })
      .then(r => r.json())
      .then(c => {
        if (c && c.brain) { _cfgBrain = c.brain; _brainSig = ""; _brainSync(); }
      })
      .catch(() => {});
  }
  function _brainSay(msg) {
    if (_brainKeyMsg) _brainKeyMsg.textContent = msg || "";
  }
  // The key crosses ONCE, in a POST body, and is gone from the page the
  // moment the server takes it. It is never logged, never read back, never
  // put in a URL, and never shown — the field is masked and the only echo
  // is the last four the server hands back.
  function _brainSaveKey() {
    if (!_brainKeyIn) return;
    const key = (_brainKeyIn.value || "").trim();
    if (!key) { _brainSay("type the key first"); return; }
    _brainSay("saving…");
    if (_brainKeyGo) _brainKeyGo.disabled = true;
    _postJSON("brainkey", { provider: "zai", key })
      .then(r => r.json().catch(() => ({
        ok: false,
        error: r.status === 404 ? "this server cannot store keys yet"
                                : "http " + r.status })))
      .then(j => {
        if (j && j.ok) {
          _brainKeyIn.value = "";        // never hold it a moment longer
          _brainKeyIn.blur();            // 1/2/3 answer to the picker again
          _brainKey4 = String(j.last4 || j.key_last4 || "").slice(-4);
          _brainSay("key stored in the keychain");
          _brainRefetch();
          _brainPaint();
        } else {
          // keep the typed value: retyping a key over a hiccup is cruel
          _brainSay(String((j && j.error) || "save failed").slice(0, 60));
        }
      })
      .catch(() => _brainSay("no answer from the server"))
      .then(() => { if (_brainKeyGo) _brainKeyGo.disabled = false; });
  }
  function _brainStep(d) {
    const ps = ["claude", "zai"];
    const i = ps.indexOf(_brainSel);
    _brainSel = i < 0 ? ps[0] : ps[(i + d + ps.length) % ps.length];
    _brainPaint();
  }

  function _mdlStep(d) {
    const ms = _mdlIds(_brainSel);
    if (!ms.length) return;
    const i = ms.indexOf(_mdlSel);
    _mdlSel = i < 0 ? ms[0] : ms[(i + d + ms.length) % ms.length];
    _brainPaint();
  }

  function _pickPaint() {
    // one voice, one color: every menu wears the theme accent — repainted
    // each time so a live theme flip inside the screen recolors it
    for (const m of _menus) m.paint();
    if (_pickGo) {
      _pickGo.disabled = !_pickSel;
      _pickGo.style.opacity = _pickSel ? "1" : ".35";
      _pickGo.style.cursor = _pickSel ? "pointer" : "default";
    }
    _navPaint();
    _brainPaint();
  }
  function _pickConfirm() {
    if (!_pickSel || !_pickEl) return;
    // Theme steps only previewed locally (the page already navigated to
    // the paired face with real baked colors) — SAVE commits both
    // choices at once: one /theme write, which is also what re-dresses
    // the voice character, and one /pick.
    if (typeof AVTheme !== "undefined" &&
        AVTheme.current !== _pickEl.dataset.theme0)
      AVTheme.commit();
    const done = () => {
      if (_pickForce) { _pickForce = false; updateModePicker(_lastMic); }
    };
    try { localStorage.setItem("av_mic_mode", _pickSel); } catch (_) {}
    const go = () => _postPick(_pickSel).then(done, done);
    // The brain goes first and ONLY when it actually moved: the switch
    // stops and restarts the CLI, which is what the mic pick then boots
    // into. A no-op post here would restart the brain — and end the
    // conversation — for nothing.
    // A tier change restarts the brain exactly like a provider change
    // does, so it commits through the same gate and the same "did it
    // actually move?" test.
    const mdlMoved = !!_mdlSel && _mdlSel !== _mdlNow
      && _mdlIds(_brainSel).indexOf(_mdlSel) >= 0;
    if (_brainSel && (_brainSel !== _brainNow || mdlMoved)) {
      const want = _brainSel;
      const wantM = _mdlIds(want).indexOf(_mdlSel) >= 0 ? _mdlSel : null;
      _brainNow = want;                  // do not post the same switch twice
      _mdlNow = wantM;
      _postJSON("brain", wantM ? { provider: want, model: wantM }
                               : { provider: want })
        .then(r => r.json().catch(() => ({ ok: r.ok })))
        // a switch that did not take leaves the live brain UNKNOWN, so the
        // next SAVE is free to try again instead of assuming it landed
        .then(j => { if (!j || j.ok === false) _brainNow = _mdlNow = null; },
              () => { _brainNow = _mdlNow = null; })
        .then(go, go);
    } else go();
  }
  function _pickKeys(e) {
    // TAB first and from anywhere (the key field included), so browser
    // focus never wanders out of the screen. It walked the tabs when there
    // were tabs; now it walks the summary, and out of a section it is a
    // second way back — the contract is "TAB always does something here".
    if (e.key === "Tab") {
      e.preventDefault();
      if (_pickPage === "home") _rowStep(e.shiftKey ? -1 : 1);
      else _navGo("home");
      return;
    }
    // Typing in the key field is typing, not shortcuts: an API key is full
    // of 1/2/3, which would otherwise re-pick the mic mode per keystroke.
    if (e.target && e.target.tagName === "INPUT") {
      if (e.key === "Enter") { e.preventDefault(); _brainSaveKey(); }
      else if (e.key === "Escape") { e.preventDefault(); e.target.blur(); }
      return;
    }
    const m = { "1": "open", "2": "wake", "3": "ptt" }[e.key];
    if (m) { _pickSel = m; _pickPaint(); }   // from any tab: muscle memory
    else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      const d = e.key === "ArrowDown" ? 1 : -1;
      e.preventDefault();
      if (_pickPage === "home") { _rowStep(d); return; }
      // Left/Right is the provider, Up/Down its tier
      if (_pickPage === "brain" && _mdlIds(_brainSel).length) _mdlStep(d);
    }
    else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      const d = e.key === "ArrowRight" ? 1 : -1;
      e.preventDefault();
      if (_pickPage === "home") {
        // Left/Right drives whatever control the highlighted row holds —
        // a page opens (Right only), a menu steps, a toggle flips. Left on
        // a page row has nowhere to go: home is the top, and ESC/ENTER are
        // the ways off the screen.
        const [id, , , kind] = _SECTIONS[_pickRow];
        if (kind === "toggle") {
          const t = _pickRows[id];
          const b = t && t.slot.querySelector(
            "button[data-v=\"" + (d > 0 ? "1" : "0") + "\"]");
          if (b) b.click();
          return;
        }
        if (kind === "menu") {
          if (id === "theme") _themeStep(d);
          else {
            const ms = Object.keys(_MIC_SHORT);
            const i = ms.indexOf(_pickSel);
            _pickSel = i < 0 ? ms[0] : ms[(i + d + ms.length) % ms.length];
            _pickPaint();
          }
          return;
        }
        if (d > 0) _navGo(id);
        return;
      }
      // Inside a section the arrows belong to that section's own controls —
      // BACK is ESC, TAB and the button, so Left stays a real control.
      if (_pickPage === "brain") _brainStep(d);
    }
    else if (e.key === "Enter") {
      // stop the browser re-firing whatever button happens to hold focus
      e.preventDefault();
      _pickConfirm();
    }
    else if (e.key === "Escape") {
      // Inside a section ESC is BACK and nothing else: dismissing the whole
      // screen from three levels of muscle memory down is how a staged
      // theme gets thrown away by accident.
      if (_pickPage !== "home") { e.preventDefault(); _navGo("home"); return; }
      if (!_pickForce) return;      // the launch screen blocks the boot
      // dismiss = abandon the preview: back to the committed theme (and
      // its face, when the preview walked away from it)
      _pickForce = false;
      const t0 = _pickEl && _pickEl.dataset.theme0;
      if (typeof AVTheme !== "undefined" && t0 && AVTheme.current !== t0) {
        AVTheme.set(t0, false);
        const home = _faceHome(t0);
        _fadeThen(() => {
          if (home) location.href = home; else location.reload();
        });
        return;
      }
      updateModePicker(_lastMic);
    }
  }
  function updateModePicker(mic) {
    _lastMic = mic;
    const want = !!(mic && mic.mode === "select") || _pickForce;
    if (!want) {
      if (_pickEl) {
        _pickEl.remove(); _pickEl = null; _pickGo = null;
        _pickBodies = {}; _pickRows = {};
        _brainStatEl = null; _menus = [];
        _themeMenu = _listenMenu = _brainMenu = _mdlMenu = null;
        _brainKeyRow = _brainKeyIn = _brainKeyGo = null;
        _brainKeyMsg = _brainWarn = null;
        // a dismissed screen holds no staged provider: the next open
        // re-seeds from the server, so SAVE can never post a choice he
        // abandoned
        _brainSel = null; _brainSig = "";
        _mdlSel = null; _mdlRow = null;
        window.removeEventListener("keydown", _pickKeys);
      }
      return;
    }
    if (_pickEl) { _brainSync(); return; }
    // read (don't consume) the resume payload — the tabs and the provider
    // have to be in place before the bodies are painted
    const rz = _pickResume;
    _pickEl = document.createElement("div");
    _pickEl.id = "av-mode-picker";
    _pickEl.style.cssText =
      "position:fixed;inset:0;z-index:60;display:flex;flex-direction:" +
      "column;align-items:center;justify-content:center;gap:18px;" +
      "background:rgba(0,0,0,.72);font-family:var(--av-display,'VT323'),monospace;" +
      "cursor:default;overflow-y:auto;padding:20px 0;";
    if (typeof AVTheme !== "undefined")
      _pickEl.dataset.theme0 = AVTheme.current;
    const title = document.createElement("div");
    title.textContent = "SETTINGS";
    title.style.cssText =
      "font-size:34px;line-height:1.1;letter-spacing:.2em;color:#cfe;opacity:.9;";
    _pickEl.appendChild(title);

    // The nav hint. ENTER is deliberately still the confirm from every
    // page, exactly as it was under the tabs: the launch screen blocks the
    // boot, and the one key that gets past it must not move house.
    const hint = document.createElement("div");
    hint.textContent = "\u2191\u2193 MOVE  \u00b7  \u2190\u2192 CHANGE  \u00b7  " +
      "ESC BACK  \u00b7  ENTER CONFIRMS";
    hint.style.cssText =
      "font-size:13px;letter-spacing:.24em;color:var(--av-dim,#5a6a72);" +
      "opacity:.6;margin:-10px 0 -2px;";
    _pickEl.appendChild(hint);

    // ------------------------------- tab bodies
    // one wrapper of a steady size, so SAVE & CONTINUE does not jump up
    // and down the screen as the tabs change
    const wrap = document.createElement("div");
    wrap.style.cssText =
      "display:flex;flex-direction:column;align-items:center;" +
      "justify-content:center;min-height:300px;";
    _pickEl.appendChild(wrap);
    const mkBody = (id, label) => {
      const d = document.createElement("div");
      d.style.cssText =
        "display:none;flex-direction:column;align-items:center;gap:14px;";
      if (label) {
        // BACK sits at the top of every section, where the eye already is
        // after clicking an EDIT one line lower.
        const bar = document.createElement("div");
        bar.style.cssText =
          "display:flex;gap:14px;align-items:center;justify-content:center;";
        const back = document.createElement("button");
        back.textContent = "\u2039 BACK";
        back.style.cssText =
          "font:18px/1.2 var(--av-display,'VT323'),monospace;" +
          "letter-spacing:.2em;padding:7px 18px;cursor:pointer;" +
          "background:transparent;color:var(--av-dim,#5a6a72);" +
          "border:1px solid var(--av-card-line,#1c2f26);border-radius:3px;";
        back.onclick = () => _navGo("home");
        const h = document.createElement("div");
        h.textContent = label;
        h.style.cssText = "font-size:22px;letter-spacing:.24em;" +
          "color:var(--av-accent,#3ddc84);";
        bar.appendChild(back); bar.appendChild(h);
        d.appendChild(bar);
      }
      _pickBodies[id] = d;
      wrap.appendChild(d);
      return d;
    };

    // ------------------------------- home: the summary
    const home = mkBody("home");
    home.style.gap = "8px";
    _SECTIONS.forEach(([id, label, , kind], i) => {
      const r = document.createElement("div");
      r.style.cssText =
        "display:flex;align-items:center;gap:18px;width:min(680px,92vw);" +
        "padding:11px 16px;border:1px solid;border-radius:4px;" +
        // .35 let the face read straight through the row; the screen is an
        // overlay, but the words on it still have to be words
        "background:rgba(0,0,0,.55);cursor:pointer;text-align:left;";
      const nm = document.createElement("div");
      nm.textContent = label;
      nm.style.cssText =
        "font:20px/1.2 var(--av-display,'VT323'),monospace;" +
        "letter-spacing:.22em;color:var(--av-fg,#cfe);min-width:148px;";
      const val = document.createElement("div");
      val.style.cssText =
        "flex:1;font-size:17px;letter-spacing:.1em;line-height:1.25;" +
        "color:var(--av-dim,#5a6a72);";
      const slot = document.createElement("div");
      slot.style.cssText =
        "display:flex;gap:10px;align-items:center;position:relative;";
      r.appendChild(nm); r.appendChild(val); r.appendChild(slot);
      let edit = null;
      if (kind === "page") {
        edit = document.createElement("button");
        edit.textContent = "EDIT";
        edit.style.cssText =
          "font:16px/1.2 var(--av-display,'VT323'),monospace;" +
          "letter-spacing:.2em;padding:6px 16px;cursor:pointer;" +
          "background:transparent;border:1px solid;border-radius:3px;";
        slot.appendChild(edit);
        // the whole row is the target, not just the button: a 560px row
        // with a 70px hit area is the old clutter in a different shape
        r.onclick = () => _navGo(id);
        r.style.cursor = "pointer";
      } else {
        // the control IS in the row, so the row is not also a link — a
        // click meant for a dropdown must not double as navigation
        r.style.cursor = "default";
      }
      r.onmouseenter = () => { _pickRow = i; _navPaint(); };
      _pickRows[id] = { el: r, val: val, edit: edit, slot: slot };
      home.appendChild(r);
    });

    // Every menu closes on any click that was not its own (the option and
    // button handlers stop propagation on their way past).
    _pickEl.addEventListener("click", () => {
      for (const m of _menus) m.close();
    });

    // THEME — a dropdown in the summary row. It was a row of buttons that
    // grew with every theme and was the widest thing on the screen.
    // ...and only when there are themes to list. A server with no themes/
    // folder answers /themes.js with a 404, nothing registers, and an empty
    // dropdown reading "—" is a broken control where the old code said so.
    if (typeof AVTheme !== "undefined" && _themeIds().length) {
      _themeMenu = _mkMenu(
        _themeIds().map(id => [id, AVTheme.themes[id].label || id]),
        () => AVTheme.current, _themePick, { width: 210 });
      _pickRows.theme.slot.appendChild(_themeMenu.el);
    } else {
      const none = document.createElement("div");
      none.textContent = "NO THEMES LOADED";
      none.style.cssText =
        "font-size:15px;letter-spacing:.2em;color:var(--av-dim,#5a6a72);";
      _pickRows.theme.slot.appendChild(none);
    }

    // LISTENING — the same. Three cards 420px wide were most of the launch
    // screen, and the boot only ever needed one value out of them.
    _listenMenu = _mkMenu(
      Object.keys(_MIC_WORD).map(m => [m, _MIC_SHORT[m]]),
      () => _pickSel, v => { _pickSel = v; _pickPaint(); }, { width: 210 });
    _pickRows.listen.slot.appendChild(_listenMenu.el);

    // HANDS — camera gesture control (HANDS-SPEC.md). Unlike the theme and
    // the mic mode this applies IMMEDIATELY rather than on SAVE: the camera
    // permission prompt has to belong to the click that asked for it, and a
    // prompt fired later from SAVE reads as the page asking unprompted.
    // hands.js owns the remembering, so there is no state to carry here.
    const hBtns = [];
    const hPaint = () => {
      const live = !!(window.AVHands && window.AVHands.on());
      for (const b of hBtns) {
        const on = (b.dataset.v === "1") === live;
        b.style.color = on ? "var(--av-accent,#3ddc84)"
                           : "var(--av-dim,#5a6a72)";
        b.style.borderColor = on ? "var(--av-accent,#3ddc84)"
                                 : "var(--av-card-line,#1c2f26)";
      }
      _navPaint();   // ...and the line about it back on the summary
    };
    for (const [v, label] of [["0", "OFF"], ["1", "ON"]]) {
      const hb = document.createElement("button");
      hb.textContent = label;
      hb.dataset.v = v;
      hb.style.cssText =
        "font:15px/1.2 var(--av-display,'VT323'),monospace;" +
        "letter-spacing:.2em;padding:6px 16px;cursor:pointer;" +
        "background:transparent;border:1px solid;border-radius:3px;";
      hb.onclick = () => {
        const H = window.AVHands;
        if (!H) return;
        if ((v === "1") !== H.on()) Promise.resolve(H.toggle()).then(hPaint);
        else hPaint();
      };
      hBtns.push(hb);
      _pickRows.hands.slot.appendChild(hb);
    }
    hPaint();

    // CINEMATIC — the circuit board's flythrough. Applies immediately and
    // locally, same as HANDS: window.AV_CINE is what the face reads, the
    // localStorage line is what survives the reload.
    const cBtns = [];
    const cPaint = () => {
      for (const b of cBtns) {
        const on = (b.dataset.v === "1") === (window.AV_CINE !== false);
        b.style.color = on ? "var(--av-accent,#3ddc84)"
                           : "var(--av-dim,#5a6a72)";
        b.style.borderColor = on ? "var(--av-accent,#3ddc84)"
                                 : "var(--av-card-line,#1c2f26)";
      }
      _navPaint();
    };
    for (const [v, label] of [["0", "OFF"], ["1", "ON"]]) {
      const cb = document.createElement("button");
      cb.textContent = label;
      cb.dataset.v = v;
      cb.style.cssText =
        "font:15px/1.2 var(--av-display,'VT323'),monospace;" +
        "letter-spacing:.2em;padding:6px 16px;cursor:pointer;" +
        "background:transparent;border:1px solid;border-radius:3px;";
      cb.onclick = () => {
        window.AV_CINE = v === "1";
        try { localStorage.setItem("av_cine", v); } catch (_) {}
        cPaint();
      };
      cBtns.push(cb);
      _pickRows.cine.slot.appendChild(cb);
    }
    cPaint();

    // BRAIN — one question per line, in the order they depend on each
    // other: which brain, then which of ITS tiers, then whether it can be
    // reached, then the key, then what changing it costs. The old screen
    // put both providers' status on at once and every provider's tiers in
    // one row, so most of what it showed belonged to the brain you had
    // NOT chosen — the section read as noise because most of it was.
    const brain = mkBody("brain", "BRAIN");
    const pvRow = _detailRow("PROVIDER");
    _brainMenu = _mkMenu(
      [["claude", _BRAIN_LABEL.claude], ["zai", _BRAIN_LABEL.zai]],
      () => _brainSel, v => { _brainSel = v; _brainPaint(); },
      { width: 190 });
    pvRow.slot.appendChild(_brainMenu.el);
    brain.appendChild(pvRow.el);

    // Every brain's tiers in one menu, showing one brain's at a time.
    // Claude: FASTEST is Haiku (the wait between your sentence and hers
    // is almost all model), BALANCED is Sonnet with Opus still behind
    // "switch to the deep model", THINKING is Opus on both. Z.AI: 5.3
    // thinks harder; flash is quicker to the first word and far cheaper
    // on the plan's credit multipliers.
    const mdlPairs = [];
    for (const ms of Object.values(_MDL))
      for (const m of Object.keys(ms)) mdlPairs.push([m, ms[m]]);
    const mdRow = _detailRow("MODEL");
    _mdlRow = mdRow.el;
    _mdlMenu = _mkMenu(mdlPairs, () => _mdlSel,
      v => { _mdlSel = v; _brainPaint(); },
      { width: 190, ok: v => _mdlIds(_brainSel).indexOf(v) >= 0 });
    mdRow.slot.appendChild(_mdlMenu.el);
    brain.appendChild(_mdlRow);

    // One status line, for the brain on this screen — straight from the
    // server: whether it is reachable and, for a key, its last four. Never
    // the key. The other provider's status is not this screen's business.
    const stRow = _detailRow("STATUS");
    _brainStatEl = document.createElement("div");
    _brainStatEl.style.cssText =
      "font-size:16px;letter-spacing:.14em;text-align:right;" +
      "color:var(--av-dim,#5a6a72);";
    stRow.slot.appendChild(_brainStatEl);
    brain.appendChild(stRow.el);

    // the key field — masked, Z.AI only, and saved on its own button: a
    // key is not a setting you stage behind SAVE & CONTINUE
    _brainKeyRow = document.createElement("div");
    _brainKeyRow.style.cssText =
      "display:none;flex-direction:column;align-items:center;gap:6px;";
    const kRow = _detailRow("API KEY").el;
    kRow.style.gap = "10px";
    _brainKeyIn = document.createElement("input");
    _brainKeyIn.type = "password";     // it is never on screen, not once
    _brainKeyIn.placeholder = "Z.AI API KEY";
    _brainKeyIn.autocomplete = "off";
    _brainKeyIn.spellcheck = false;
    _brainKeyIn.setAttribute("aria-label", "Z.AI API key");
    _brainKeyIn.style.cssText =
      "font:18px/1.2 var(--av-display,'VT323'),monospace;letter-spacing:.18em;" +
      "padding:9px 12px;width:190px;color:var(--av-accent,#3ddc84);" +
      "background:rgba(0,0,0,.5);border:1px solid var(--av-card-line,#1c2f26);" +
      "border-radius:3px;outline:none;";
    _brainKeyGo = document.createElement("button");
    _brainKeyGo.textContent = "SAVE KEY";
    _brainKeyGo.style.cssText =
      "font:18px/1.2 var(--av-display,'VT323'),monospace;letter-spacing:.2em;" +
      "padding:9px 16px;cursor:pointer;color:var(--av-accent,#3ddc84);" +
      "background:transparent;border:1px solid var(--av-accent,#3ddc84);" +
      "border-radius:3px;";
    _brainKeyGo.onclick = _brainSaveKey;
    kRow.lastChild.appendChild(_brainKeyIn);
    kRow.lastChild.appendChild(_brainKeyGo);
    _brainKeyRow.appendChild(kRow);
    _brainKeyMsg = document.createElement("div");
    _brainKeyMsg.textContent = "";
    _brainKeyMsg.style.cssText =
      "font-size:15px;letter-spacing:.12em;min-height:16px;" +
      "color:var(--av-accent,#3ddc84);opacity:.8;";
    _brainKeyRow.appendChild(_brainKeyMsg);
    const kNote = document.createElement("div");
    kNote.textContent = "STORED IN THE MACOS KEYCHAIN · NEVER SHOWN AGAIN";
    kNote.style.cssText =
      "font-size:13px;letter-spacing:.16em;color:var(--av-dim,#5a6a72);" +
      "opacity:.6;";
    _brainKeyRow.appendChild(kNote);
    brain.appendChild(_brainKeyRow);
    // the line he must read BEFORE he reaches SAVE — it sits directly
    // above the button that does it
    _brainWarn = document.createElement("div");
    _brainWarn.textContent =
      "CHANGING THE BRAIN STARTS A NEW CONVERSATION — THIS THREAD ENDS";
    // Hidden until something actually moved. It used to stand permanently
    // in two shades of grey, which is how a real warning becomes furniture.
    _brainWarn.style.cssText =
      "display:none;font-size:16px;letter-spacing:.14em;text-align:center;" +
      "max-width:520px;line-height:1.3;color:var(--av-accent,#3ddc84);" +
      "border:1px solid var(--av-accent,#3ddc84);border-radius:4px;" +
      "padding:9px 14px;";
    brain.appendChild(_brainWarn);

    // ------------------------------- global save
    _pickGo = document.createElement("button");
    // At LAUNCH the voice line is blocked waiting for a mic mode and this
    // button is the thing that starts the stack, so it says so. A
    // mid-session summon is editing something that already runs.
    _pickGo.textContent = _pickForce ? "SAVE & CONTINUE" : "START";
    _pickGo.style.cssText =
      "font:22px/1.2 var(--av-display,'VT323'),monospace;letter-spacing:.25em;" +
      "margin-top:10px;padding:12px 40px;min-width:420px;text-align:center;" +
      "color:var(--av-bg2,#020705);background:var(--av-accent,#3ddc84);" +
      "border:1px solid var(--av-accent,#3ddc84);border-radius:4px;";
    _pickGo.onclick = _pickConfirm;
    _pickEl.appendChild(_pickGo);
    // summoned mid-session: the current mode starts selected
    if (_pickForce && mic && _MIC_SHORT[mic.mode]) _pickSel = mic.mode;
    // ...and a default is ALWAYS standing, so SAVE is never a dead button
    // and the screen never opens on nothing: the live mode, then the last
    // one saved on this machine, then wake. (At launch mic.mode is the
    // "select" sentinel, which is not a card, so it falls through.)
    if (!_pickSel) {
      let last = null;
      try { last = localStorage.getItem("av_mic_mode"); } catch (_) {}
      _pickSel = _MIC_SHORT[last] ? last : "wake";
    }
    // ALWAYS THE SUMMARY. Launch and a mid-session Esc open the same
    // screen, and it is the one that says what everything is set to —
    // "reopen where he left off" put him back inside a section, which is
    // the one place the summary cannot be read.
    _pickPage = "home";
    _pickRow = 0;
    // a theme-preview navigation carries the picker's state across —
    // the payload's choices override the fresh-page defaults
    if (rz) {
      if (rz.theme0) _pickEl.dataset.theme0 = rz.theme0;
      if (_MIC_SHORT[rz.sel]) _pickSel = rz.sel;
      if (rz.page === "home" || (rz.page && _pickBodies[rz.page])) {
        _pickPage = rz.page;
        const i = _SECTIONS.findIndex(sec => sec[0] === rz.page);
        _pickRow = i >= 0 ? i : _pickRow;
      }
      if (rz.prov === "claude" || rz.prov === "zai") _brainSel = rz.prov;
      if (_mdlIds(_brainSel).indexOf(rz.mdl) >= 0) _mdlSel = rz.mdl;
      _pickResume = null;
      _fadeAway();   // picker is standing again — reveal the new face
    }
    _brainSig = "";
    _brainSync();          // seed the provider and the status lines
    _pickPaint();
    document.body.appendChild(_pickEl);
    window.addEventListener("keydown", _pickKeys);
  }

  /* ------------------------------ status pill ------------------------------ */
  /* ponytail: a face shows MOOD, not progress — transcribing and speech
     synthesis are seconds of real work that looked exactly like a hang.
     One pill at the bottom and one breathing edge, both painted straight
     from the theme's own vars, so a new theme needs no work here. Idle
     shows nothing at all: the point is to mark NOT-idle. */
  const STAGE_WORD = { idle: "", listening: "LISTENING",
                       thinking: "THINKING", speaking: "SPEAKING" };
  let _statusEl = null, _statusTxt = null, _edgeEl = null, _statusWas = null;

  function statusBuild() {
    const css = document.createElement("style");
    css.textContent =
      "@keyframes av-breathe{0%,100%{opacity:.30}50%{opacity:.85}}" +
      "#av-status{position:fixed;left:50%;bottom:8px;transform:translateX(-50%);" +
      "z-index:55;display:none;align-items:center;gap:10px;padding:7px 16px 6px;" +
      "border:1px solid var(--av-accent,#3ddc84);border-radius:999px;" +
      "background:rgba(0,0,0,.62);color:var(--av-accent,#3ddc84);" +
      "font-family:var(--av-display,'SF Mono',Menlo,monospace);font-size:11px;" +
      "letter-spacing:.26em;white-space:nowrap;pointer-events:none;" +
      "max-width:min(88vw,720px);overflow:hidden;text-overflow:ellipsis;" +
      "box-shadow:0 0 22px var(--av-glow,rgba(61,220,132,.35))}" +
      "#av-status .dot{width:6px;height:6px;border-radius:50%;" +
      "background:var(--av-accent,#3ddc84);animation:av-breathe 1.6s ease-in-out infinite}" +
      "#av-edge{position:fixed;inset:0;z-index:54;display:none;pointer-events:none;" +
      "box-shadow:inset 0 0 90px 8px var(--av-glow,rgba(61,220,132,.35));" +
      "animation:av-breathe 2.4s ease-in-out infinite}";
    document.head.appendChild(css);
    _edgeEl = document.createElement("div");
    _edgeEl.id = "av-edge";
    _statusEl = document.createElement("div");
    _statusEl.id = "av-status";
    const dot = document.createElement("span");
    dot.className = "dot";
    _statusTxt = document.createElement("span");
    _statusEl.appendChild(dot);
    _statusEl.appendChild(_statusTxt);
    document.body.appendChild(_edgeEl);
    document.body.appendChild(_statusEl);
  }

  function statusPaint() {
    if (SHOT || !document.body) return;         // never in the screenshot harness
    // the sub-step wins when there is one: "TRANSCRIBING" says more than
    // "LISTENING", and it is the part that actually takes the time
    const word = (A.stage || STAGE_WORD[A.state] || "").toUpperCase();
    if (word === _statusWas) return;            // DOM writes only on change
    _statusWas = word;
    if (!_statusEl) statusBuild();
    _statusTxt.textContent = word;
    _statusEl.style.display = word ? "flex" : "none";
    _edgeEl.style.display = word ? "block" : "none";
  }

  function tick(dt) {
    if (DEMO) demoUpdate(dt);
    A.state = raw.state || "idle";
    A.stage = raw.stage || "";
    A.alert = !!raw.alert;
    statusPaint();
    // Empty unless the voice line was told to publish usage. A face that
    // wants to draw it reads AV.rateLimits; every other face ignores it.
    A.rateLimits = raw.rate_limits || {};
    // Which brain answers, as a line a face can print: "CLAUDE - BALANCED".
    // Either lane carries it (poll or /config), same as the picker reads.
    const _b = raw.brain || _cfgBrain;
    A.brain = _b && _BRAIN_LABEL[_b.provider]
      ? { provider: _BRAIN_LABEL[_b.provider],
          model: (_MDL[_b.provider] || {})[_b.model] || "" }
      : null;
    // Glass-wide theme: the server remembers it; a page that disagrees
    // (theme picked on another display or browser) reskins itself once.
    // Hold off right after a LOCAL pick — the poll racing the POST would
    // otherwise see the stale server value and revert the user's click.
    // Hold while a picker previews too (open, or mid-navigation): the
    // preview is deliberately ahead of the server until SAVE.
    // Only reload when the choice actually stuck in localStorage —
    // otherwise a storage-blocked browser would reload forever.
    if (!_pickEl && !_pickResume &&
        raw.theme && typeof AVTheme !== "undefined" &&
        AVTheme.themes[raw.theme] && AVTheme.current !== raw.theme &&
        Date.now() - AVTheme.localAt > 5000) {
      AVTheme.set(raw.theme, false);
      let saved = null;
      try { saved = localStorage.getItem("av_theme"); } catch (_) {}
      if (saved === raw.theme) {
        const home = _faceHome(raw.theme);
        if (home) location.href = home; else location.reload();
      }
    }
    // Walled garden: no mixing — a face page whose theme belongs to a
    // different face moves to the paired one (picker open or resuming =
    // previewing, hold; SAVE/dismiss paths land here anyway).
    if (!_pickEl && !_pickResume && !_faceNav &&
        typeof AVTheme !== "undefined") {
      const home = _faceHome(AVTheme.current);
      if (home) { _faceNav = true; location.href = home; }
    }
    // The mic badge: one glance answers "is it listening to me?".
    updateMicBadge(raw.mic || null);
    // The launch-time mode picker: while the voice line waits on a
    // choice, three buttons stand in front of everything.
    updateModePicker(raw.mic || null);
    // The conversation crawl's cheap change pointer (chat.js fetches
    // /chat only when this moves).
    A.chatRev = raw.chat_rev || 0;
    // The glass board, when the server runs one. glass.js watches .rev
    // and leaves the DOM alone until it moves.
    A.glass = raw.glass || null;
    A.level = raw.level || 0;

    // adaptive envelope: normalize against a decaying peak, then ease
    // (attack 50ms, release 350ms) — motion code rides AV.env
    const dts = dt / 1000;
    peak = Math.max(A.level, 0.05, peak - 0.5 * peak * dts);
    const target = Math.min(1, A.level / peak);
    const tau = target > A.env ? 50 : 350;
    A.env += (target - A.env) * Math.min(1, dt / tau);

    // waveform ring: rectify, normalize against its own decaying peak,
    // blend toward the newest frame so the ring flows instead of flickers
    const s = raw.samples;
    A.rawSamples = s && s.length ? s : null;   // signed, int16-scale floats
    if (s && s.length) {
      let mx = 0;
      for (let i = 0; i < s.length; i++) mx = Math.max(mx, Math.abs(s[i]));
      sPeak = Math.max(mx, 200, sPeak * 0.98);
      const n = s.length;
      for (let i = 0; i < 64; i++) {
        const v = Math.abs(s[Math.min(n - 1, Math.round(i * (n - 1) / 63))])
          / sPeak;
        A.samples[i] = A.samples[i] * 0.45 + Math.min(1, v) * 0.55;
      }
    } else {
      for (let i = 0; i < 64; i++) A.samples[i] *= Math.max(0, 1 - dts * 6);
    }
    if (A.state !== "speaking" && !DEMO)
      for (let i = 0; i < 64; i++) A.samples[i] *= Math.max(0, 1 - dts * 6);

    if (A._mic && A._micAnalyser) micRead();
    soundUpdate();
  }

  /* --------------------------------- mic ---------------------------------- */
  let micPeak = 0.02;
  function micRead() {
    const an = A._micAnalyser;
    const buf = A._micBuf;
    an.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    const rms = Math.sqrt(sum / buf.length);
    micPeak = Math.max(rms, 0.02, micPeak * 0.999);
    A.micLevel = Math.min(1, rms / micPeak);
  }
  async function micStart() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const ctx = new AudioContext();
      const src = ctx.createMediaStreamSource(stream);
      const an = ctx.createAnalyser();
      an.fftSize = 512;
      src.connect(an);
      A._micAnalyser = an;
      A._micBuf = new Float32Array(an.fftSize);
      const kick = () => ctx.state === "suspended" && ctx.resume();
      addEventListener("click", kick); addEventListener("keydown", kick);
    } catch (e) { /* no mic permission: level stays 0, faces degrade */ }
  }

  /* ----------------------------- thinking sound ---------------------------- */
  let audio = null, sndBtn = null, playing = false;
  A._sndWant = true;
  function soundInit() {
    if (SHOT) return;
    try { A._sndOn = localStorage.getItem("av_sound") !== "0"; }
    catch (e) { A._sndOn = true; }
    audio = new Audio(new URL("assets/thinking.wav", ROOT).href);
    audio.volume = 0.35;
    sndBtn = document.createElement("div");
    // hidden until the mouse moves, so it never collides with a face's
    // chrome and never shows on camera or in an OBS source
    sndBtn.style.cssText =
      "position:fixed;left:64px;bottom:14px;z-index:50;cursor:pointer;" +
      "font:12px 'SF Mono',Menlo,Consolas,monospace;letter-spacing:.2em;" +
      "color:#5a6a72;opacity:0;transition:opacity .4s;user-select:none;" +
      "pointer-events:none";
    sndBtn.title = "thinking sound on/off";
    let hideT = null;
    addEventListener("mousemove", () => {
      sndBtn.style.opacity = ".65";
      sndBtn.style.pointerEvents = "auto";
      clearTimeout(hideT);
      hideT = setTimeout(() => {
        sndBtn.style.opacity = "0";
        sndBtn.style.pointerEvents = "none";
      }, 3000);
    });
    sndBtn.onclick = () => {
      A._sndOn = !A._sndOn;
      try { localStorage.setItem("av_sound", A._sndOn ? "1" : "0"); }
      catch (e) {}
      if (!A._sndOn) stopSound();
      paintBtn();
    };
    paintBtn();
    document.body.appendChild(sndBtn);
  }
  function paintBtn() {
    if (sndBtn) sndBtn.textContent = A._sndOn ? "SND ON" : "SND OFF";
  }
  function stopSound() {
    if (audio && playing) { audio.pause(); audio.currentTime = 0; }
    playing = false;
  }
  function soundUpdate() {
    if (!audio || !A._sndWant) return;
    const want = A._sndOn && A.state === "thinking" && !raw.loading;
    if (want && !playing) {
      playing = true;
      audio.currentTime = 0;
      audio.play().catch(() => { playing = false; });
    } else if (!want && playing) {
      stopSound();
    }
  }

  /* ------------------------------ shot harness ----------------------------- */
  // Runs the face's frame() deterministically (a synchronous burst of t ms).
  // A headless browser resizes the window and finishes loading images AFTER
  // the first burst, so the burst re-runs on resize and on two late timers
  // (the last one flags "ready"), then keeps painting at frame pace so the
  // late capture always sees a fresh composite.
  A.shotRun = (frame) => {
    const burst = () => { for (let t = 0; t < SHOT_T; t += 16.6) frame(16.6); };
    burst();
    addEventListener("resize", burst);
    setTimeout(burst, 450);
    setTimeout(burst, 900);
    setTimeout(() => { burst(); document.title = "ready"; }, 3000);
    // fat 100ms steps: assets that finish loading after the last burst
    // still reach their steady state within a few paints
    const loop = () => { frame(100); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  };

  /* ---------------------------------- init --------------------------------- */
  A.init = (opts = {}) => {
    A._mic = !!opts.mic;
    if (A._mic && !DEMO) micStart();
    if (opts.sound !== false) soundInit(); else A._sndWant = false;
    if (DEMO) {
      applyConfig({ name: Q.get("name") || "JARVIS" });
    } else {
      fetch("/config", { cache: "no-store" })
        .then(r => r.json()).then(applyConfig)
        .catch(() => applyConfig({}));
    }
    return A;
  };

  A.tick = tick;

  /* ----------------------------- render helpers ---------------------------- */
  const U = {};
  U.dim = (c, f) => {
    f = Math.max(0, Math.min(1, f));
    return `rgb(${c[0] * f | 0},${c[1] * f | 0},${c[2] * f | 0})`;
  };
  U.rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

  // How long until a usage window resets, in the shortest honest unit.
  U.relTime = (ep) => {
    const d = ep - Date.now() / 1000;
    if (!(d > 0)) return "";
    if (d < 3600) return Math.round(d / 60) + "m";
    if (d < 86400) return Math.round(d / 3600) + "h";
    return Math.round(d / 86400) + "d";
  };

  // The plan-usage windows, formatted ONCE for every face that draws them.
  // Lives here rather than in each face because four copies of one format
  // drift apart silently, and the first symptom is two faces disagreeing
  // about the same number.
  //
  // Returns [] when the voice line publishes no usage, so a face can call
  // it unconditionally and simply draw nothing when there is nothing to say.
  // A window that is KNOWN but has no percentage yet still returns a row:
  // hiding it entirely was the original bug, and a row that says "no number
  // yet" is information where a missing row is just confusing.
  U.usageRows = () => {
    const rl = A.rateLimits || {};
    const out = [];
    for (const [label, w] of [["5H", rl.five_hour], ["7D", rl.seven_day]]) {
      if (!w) continue;
      const known = w.utilization != null;
      const pct = known ? Math.round(w.utilization * 100) : null;
      const rel = w.resets_at ? U.relTime(w.resets_at) : "";
      out.push({
        label, pct, known,
        hot: known && pct >= 80,
        text: (known ? pct + "%" : "\u2014") + (rel ? "  " + rel : "")
      });
    }
    return out;
  };
  U.mix = (c1, c2, t) => [c1[0] + (c2[0] - c1[0]) * t | 0,
                          c1[1] + (c2[1] - c1[1]) * t | 0,
                          c1[2] + (c2[2] - c1[2]) * t | 0];
  // soft additive glow sprite (canvas), cached by the caller
  U.makeGlow = (rgb, size) => {
    const c = document.createElement("canvas");
    c.width = c.height = size;
    const g = c.getContext("2d");
    const grd = g.createRadialGradient(size / 2, size / 2, 0,
                                       size / 2, size / 2, size / 2);
    grd.addColorStop(0, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},1)`);
    grd.addColorStop(.25, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},.55)`);
    grd.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = grd;
    g.fillRect(0, 0, size, size);
    return c;
  };
  // the one-field bloom rule: draw everything luminous into one field
  // canvas, bloom the WHOLE field (two downscale taps), composite
  // additively — bloom applied per-element reads as pencil lines
  U.bloomBlit = (dst, field, w, h) => {
    if (!field._b4 || field._b4.width !== w >> 2) {
      field._b4 = document.createElement("canvas");
      field._b4.width = Math.max(1, w >> 2);
      field._b4.height = Math.max(1, h >> 2);
      field._b8 = document.createElement("canvas");
      field._b8.width = Math.max(1, w >> 3);
      field._b8.height = Math.max(1, h >> 3);
    }
    const g4 = field._b4.getContext("2d"), g8 = field._b8.getContext("2d");
    g4.clearRect(0, 0, field._b4.width, field._b4.height);
    g4.drawImage(field, 0, 0, field._b4.width, field._b4.height);
    g8.clearRect(0, 0, field._b8.width, field._b8.height);
    g8.drawImage(field, 0, 0, field._b8.width, field._b8.height);
    const prev = dst.globalCompositeOperation;
    dst.globalCompositeOperation = "lighter";
    dst.drawImage(field, 0, 0);
    dst.drawImage(field._b4, 0, 0, w, h);
    dst.drawImage(field._b8, 0, 0, w, h);
    dst.globalCompositeOperation = prev;
  };
  // text that resolves out of glyph noise, left to right
  U.Descrambler = class {
    constructor(text, perChar = 50, hold = null) {
      this.text = text; this.per = perChar; this.hold = hold;
      this.t = 0; this.done = false;
      this.chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#$%&";
    }
    render(dt) {
      this.t += dt;
      const n = this.t / this.per | 0;
      let out = "";
      for (let i = 0; i < this.text.length; i++) {
        const ch = this.text[i];
        out += (i < n || ch === " ") ? ch
          : this.chars[Math.random() * this.chars.length | 0];
      }
      if (this.hold != null && this.t > this.per * this.text.length + this.hold)
        this.done = true;
      return out;
    }
  };
  A.util = U;

  return A;
})();
