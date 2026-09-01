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
   glass.js — the agent-controlled overlay on top of the face.

   core.js injects this script on face pages when the server enables
   the glass (or unconditionally under ?glassdemo=1). It draws a
   12×8 CSS grid layer above the face canvas and renders the items
   the server reports in the /state payload's "glass" object, which
   core.js exposes as AV.glass.

   The payload is the source of truth; everything here diffs it by id:
     - AV.glass.rev unchanged  -> the DOM is not touched at all
     - new id                  -> entrance (except the first paint
                                  after page load, which renders
                                  settled — no entrance wall on refresh)
     - id gone from payload    -> exit animation
     - item.rev moved          -> body swap + hairline flash (a replace
                                  is never a full exit+enter)
     - cell/span moved         -> FLIP transform between grid slots

   Liveness is inferred from object identity: core.js reassigns its
   whole poll result on every SUCCESSFUL fetch, so a fresh AV.glass
   reference means the server answered — no second poll loop, and no
   extra core.js surface. While polls fail the board holds what is
   shown, keeps counting down each item's last-known expires_in
   locally (ephemerals still exit on schedule during an outage), and
   dims the hairlines after ~5 s as the stale cue.

   ?glassdemo=1 stages one local fixture per registered type and never
   polls — serverless eyeball-QA for chrome, motion and every renderer,
   usable from file://.

   Failure rule (§8): any exception in here disables the overlay with
   one console line and leaves the face animating. The face never pays
   for a glass bug.
   ============================================================ */
"use strict";

(() => {
  const ROOT = new URL(".", document.currentScript.src);
  const GLASSDEMO =
    new URLSearchParams(location.search).get("glassdemo") === "1";
  const RM = matchMedia("(prefers-reduced-motion: reduce)");

  /* ------------------------- the §8 failure wrapper ------------------------ */
  let dead = false, layer = null, ticker = 0;
  function die(err) {
    if (dead) return;
    dead = true;
    clearInterval(ticker);
    try { if (layer) layer.remove(); } catch (e) { /* already gone */ }
    console.warn("glass: overlay disabled after an error — the face keeps " +
                 "running.", err);
  }
  // Every entry point (boot, ticker, staged fixtures, media handlers) runs
  // through this, so one throw anywhere tears the layer down instead of
  // leaving a half-rendered board or breaking the page's other timers.
  function guard(fn) {
    return function (...args) {
      if (dead) return;
      try { return fn.apply(this, args); } catch (e) { die(e); }
    };
  }

  /* ------------------------------ grid math ------------------------------- */
  const COLS = "ABCDEFGHIJKL";
  function placeEl(el, cell, span) {
    const c = COLS.indexOf(String(cell)[0]) + 1;
    const r = parseInt(String(cell).slice(1), 10);
    el.style.gridColumn = c + " / span " + span[0];
    el.style.gridRow = r + " / span " + span[1];
  }

  /* ------------------------------- helpers -------------------------------- */
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = String(text);
    return n;
  }
  /* A card IS its own drag handle, so a press inside one is ambiguous:
     grabbing a calendar by a day cell must move the card, not open the day.
     Every interactive thing inside a card therefore has to say which it was,
     and the cheapest true answer is how far the press travelled — measured
     between mousedown and mouseUP, which is the one pair BOTH input paths
     emit at real coordinates (a click's own coordinates are 0,0 when it
     comes from .click()). hands.js runs the same law in the air, in tapPx
     and tapMs, and lands its pair on the aim point. */
  const TAP_PX = 6;
  function onTap(node, fn) {
    let sx = 0, sy = 0, still = false;
    node.addEventListener("mousedown", e => {
      sx = e.clientX; sy = e.clientY; still = false;
    });
    node.addEventListener("mouseup", e => {
      still = Math.hypot(e.clientX - sx, e.clientY - sy) <= TAP_PX;
    });
    node.addEventListener("click", e => {
      // detail 0 is a keyboard activation or a programmatic .click() — the
      // hand's tap, which already proved itself against tapPx/tapMs. Only a
      // real pointer has to show it held still.
      if (e.detail !== 0 && !still) return;
      e.stopPropagation();
      fn(e);
    });
    node.classList.add("glass-tap");
  }

  function fallbackNote(text) {
    // the single degraded mode: a LABELED placeholder, never a naked blank
    return el("div", "glass-fallback", text);
  }
  function mapQuery(item) {
    return item.q != null ? String(item.q) : item.lat + "," + item.lon;
  }
  function titleFor(item) {
    // §4: the map card's title bar always shows the query text — offline or
    // refused, the person still knows what the blank was supposed to be
    if (item.type === "map") return mapQuery(item);
    return item.title != null && String(item.title) !== ""
      ? String(item.title) : item.type;
  }

  /* -------------------------------- motion -------------------------------- */
  function flash(elm) {
    // restart the beat even if one is mid-flight (rapid replaces)
    elm.classList.remove("glass-flash");
    void elm.offsetWidth;
    elm.classList.add("glass-flash");
  }
  // FLIP: measure, re-place, measure, play the inverse transform to zero.
  // The only consumers are `move` and replace-with-new-footprint.
  function flip(elm, replace) {
    if (RM.matches) { replace(); return; }
    const a = elm.getBoundingClientRect();
    replace();
    const b = elm.getBoundingClientRect();
    const dx = a.left - b.left, dy = a.top - b.top;
    const sx = b.width ? a.width / b.width : 1;
    const sy = b.height ? a.height / b.height : 1;
    if (!dx && !dy && sx === 1 && sy === 1) return;
    elm.style.transition = "none";
    elm.style.transformOrigin = "top left";
    elm.style.transform =
      `translate(${dx}px,${dy}px) scale(${sx},${sy})`;
    void elm.offsetWidth;               // commit the inverted frame first
    elm.style.transition = "transform 300ms ease";
    elm.style.transform = "";
    elm.addEventListener("transitionend",
      () => { elm.style.transition = ""; }, { once: true });
  }

  /* --------------------------- component registry -------------------------- */
  // Plain object of render(body, item, rec) functions — adding a component
  // is adding one function. Chrome is owned by the card; these fill the body.

  // note: the markdown subset — bold, lists (-, *, "1."), inline and fenced
  // code. Escape first, then add the allowed shapes back, so item text can
  // never smuggle markup into the page.
  function mdSubset(src) {
    const esc = s => String(s).replace(/&/g, "&amp;")
      .replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const inline = s => esc(s)
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
    let out = "", listTag = null, pre = null;
    const closeList = () => { if (listTag) { out += "</" + listTag + ">"; listTag = null; } };
    for (const line of String(src).split("\n")) {
      if (/^```/.test(line.trim())) {
        if (pre !== null) { out += "<pre>" + esc(pre.join("\n")) + "</pre>"; pre = null; }
        else { closeList(); pre = []; }
        continue;
      }
      if (pre !== null) { pre.push(line); continue; }
      const li = line.match(/^\s*(?:[-*]|(\d+)\.)\s+(.*)$/);
      if (li) {
        const want = li[1] ? "ol" : "ul";
        if (listTag !== want) { closeList(); out += "<" + want + ">"; listTag = want; }
        out += "<li>" + inline(li[2]) + "</li>";
        continue;
      }
      closeList();
      if (line.trim() === "") continue;
      out += "<p>" + inline(line) + "</p>";
    }
    closeList();
    if (pre !== null) out += "<pre>" + esc(pre.join("\n")) + "</pre>";
    return out;
  }
  function renderNote(body, item) {
    body.innerHTML = mdSubset(item.body || "");
  }

  function renderImage(body, item) {
    const img = document.createElement("img");
    // repo-relative srcs resolve against where glass.js lives, the same
    // rule core.js uses for assets/ — absolute URLs pass through untouched
    img.src = new URL(String(item.src || ""), ROOT).href;
    img.alt = item.caption || item.title || item.id;
    img.addEventListener("error", guard(() => {
      body.textContent = "";
      body.appendChild(fallbackNote(
        (item.caption || item.src) + " — image failed to load"));
    }));
    body.appendChild(img);
    if (item.caption) body.appendChild(el("div", "glass-caption", item.caption));
  }

  // map: an iframe preset, not a map engine — the keyless long-standing
  // Google embed form resolves the agent's q itself, so there is no
  // geocoder anywhere in this stack. Same sandbox rules as `iframe`.
  function renderMap(body, item, rec) {
    const q = mapQuery(item);
    if (navigator.onLine === false) {
      rec.offline = true;             // tick() re-renders this on reconnect
      body.appendChild(fallbackNote(q + " — no map offline"));
      return;
    }
    const f = document.createElement("iframe");
    f.setAttribute("sandbox", "allow-scripts allow-same-origin");
    f.title = q;
    f.addEventListener("error", guard(() => {
      rec.offline = true;             // failed mid-outage: retry too
      body.textContent = "";
      body.appendChild(fallbackNote(q + " — no map offline"));
    }));
    f.src = "https://maps.google.com/maps?q=" + encodeURIComponent(q) +
      (item.zoom != null ? "&z=" + encodeURIComponent(item.zoom) : "") +
      "&output=embed";
    body.appendChild(f);
  }

  /* calendar: a hand-rolled STATIC month/week grid rendering exactly the
     passed events — day cells, event labels, today highlighted, and one
     drill-down: a day cell opens its own hours. Still not a scheduling
     suite — nothing here edits — which is why it needs no library and
     inherits the theme tokens natively. Weeks start
     Monday. It anchors on today; when no event falls in today's month (or
     week, for the week view) it anchors on the earliest event instead, so
     "show my trip next month" never renders an empty grid. */
  const MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"];
  const MON3 = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
  const DOW = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];
  function parseDay(s) {
    const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})/);
    return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
  }
  function dayKey(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") +
      "-" + String(d.getDate()).padStart(2, "0");
  }
  function weekStart(d) {
    const x = new Date(d);
    x.setHours(0, 0, 0, 0);
    x.setDate(x.getDate() - ((x.getDay() + 6) % 7));   // back to Monday
    return x;
  }
  function evLabel(ev) {
    return (ev.time ? ev.time + " " : "") + (ev.label == null ? "" : ev.label);
  }
  // An hour is the one thing a month grid structurally cannot show: it can
  // say something happens on the 14th, never when. So a day cell opens one,
  // rendered from the SAME events array — nothing is fetched, and the hours
  // cannot drift out of step with the grid they came from. The open day
  // lives on `rec`, not in the DOM, so a poll re-render does not close it.
  function renderCalendar(body, item, rec) {
    if (rec && rec.calDay) return renderCalDay(body, item, rec);
    const view = item.view === "week" ? "week" : "month";
    const byDay = new Map();
    for (const ev of Array.isArray(item.events) ? item.events : []) {
      const d = ev && parseDay(ev.date);
      if (!d) continue;
      const k = dayKey(d);
      if (!byDay.has(k)) byDay.set(k, []);
      byDay.get(k).push(ev);
    }
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    let anchor = today;
    const keys = [...byDay.keys()].sort();
    if (keys.length) {
      const inView = view === "month"
        ? k => { const d = parseDay(k);
                 return d.getFullYear() === today.getFullYear() &&
                        d.getMonth() === today.getMonth(); }
        : k => weekStart(parseDay(k)).getTime() ===
               weekStart(today).getTime();
      if (!keys.some(inView)) anchor = parseDay(keys[0]);
    }
    const wrap = el("div", "glass-cal");
    const grid = el("div", "cal-grid" + (view === "week" ? " cal-week" : ""));
    // Counted against the day cells actually rendered (the padded month
    // window, or the 7-day week) — not the anchor month, since padded-week
    // adjacent-month events do render.
    let placed = 0;
    if (view === "month") {
      const y = anchor.getFullYear(), m = anchor.getMonth();
      wrap.appendChild(el("div", "cal-head", MONTHS[m] + " " + y));
      DOW.forEach(d => grid.appendChild(el("div", "cal-dow", d)));
      const stop = new Date(y, m + 1, 0);            // last day of the month
      const d = weekStart(new Date(y, m, 1));
      for (let i = 0; (d <= stop || d.getDay() !== 1) && i < 42;
           d.setDate(d.getDate() + 1), i++) {
        const cell = el("div", "cal-day" +
          (d.getMonth() !== m ? " cal-out" : "") +
          (d.getTime() === today.getTime() ? " cal-today" : ""));
        cell.appendChild(el("div", "cal-num", d.getDate()));
        for (const ev of byDay.get(dayKey(d)) || []) {
          cell.appendChild(el("div", "cal-ev", evLabel(ev)));
          placed++;
        }
        // `d` is one Date walked forward by the loop — capture the key, or
        // every cell would open whatever day the loop happened to end on
        openDay(cell, dayKey(d), rec);
        grid.appendChild(cell);
      }
    } else {
      const ws = weekStart(anchor);
      const we = new Date(ws);
      we.setDate(we.getDate() + 6);
      wrap.appendChild(el("div", "cal-head",
        MON3[ws.getMonth()] + " " + ws.getDate() + " – " +
        MON3[we.getMonth()] + " " + we.getDate()));
      for (let i = 0; i < 7; i++) {
        const d = new Date(ws);
        d.setDate(ws.getDate() + i);
        const col = el("div", "cal-day" +
          (d.getTime() === today.getTime() ? " cal-today" : ""));
        col.appendChild(el("div", "cal-num", DOW[i] + " " + d.getDate()));
        const evs = (byDay.get(dayKey(d)) || []).slice()
          .sort((a, b) => String(a.time || "").localeCompare(String(b.time || "")));
        for (const ev of evs) col.appendChild(el("div", "cal-ev", evLabel(ev)));
        placed += evs.length;
        openDay(col, dayKey(d), rec);
        grid.appendChild(col);
      }
    }
    wrap.appendChild(grid);
    // One period renders (a rail card cannot hold a second month), so
    // events outside the rendered window get the §8 labeled-degradation
    // treatment: a counted footer, never a silent drop.
    let total = 0;
    for (const evs of byDay.values()) total += evs.length;
    if (total > placed)
      wrap.appendChild(el("div", "cal-more", "+" + (total - placed) +
        " not shown"));
    body.appendChild(wrap);
  }

  function openDay(cell, key, rec) {
    if (!rec) return;                       // ?glassdemo=1 renders recless
    cell.title = "open " + key;
    onTap(cell, () => { rec.calDay = key; renderInto(rec, rec.item); });
  }

  // "14:30", "2:30pm", "9am", "7" — the label is written for a person, so
  // read it the way one is written. Anything without a readable hour is not
  // a parse failure, it is an all-day event, and gets its own row.
  function hourOf(ev) {
    const m = String(ev && ev.time != null ? ev.time : "")
      .match(/(\d{1,2})(?::(\d{2}))?\s*([ap])/i) ||
      String(ev && ev.time != null ? ev.time : "").match(/^\s*(\d{1,2})(?::(\d{2}))?/);
    if (!m) return null;
    let h = +m[1];
    const ap = m[3] && m[3].toLowerCase();
    if (ap === "p" && h < 12) h += 12;
    if (ap === "a" && h === 12) h = 0;
    return h >= 0 && h <= 23 ? h : null;
  }

  function renderCalDay(body, item, rec) {
    const day = parseDay(rec.calDay);
    if (!day) { rec.calDay = null; return renderCalendar(body, item, rec); }
    const evs = (Array.isArray(item.events) ? item.events : []).filter(ev => {
      const d = ev && parseDay(ev.date);
      return d && dayKey(d) === rec.calDay;
    });
    const wrap = el("div", "glass-cal");
    const head = el("div", "cal-head");
    const back = el("button", "cal-back", "\u2039");
    back.type = "button";
    back.title = "back to the calendar";
    onTap(back, () => { rec.calDay = null; renderInto(rec, rec.item); });
    head.appendChild(back);
    head.appendChild(el("span", null, DOW[(day.getDay() + 6) % 7] + " " +
      MON3[day.getMonth()] + " " + day.getDate()));
    wrap.appendChild(head);

    const hours = el("div", "cal-hours");
    const untimed = evs.filter(ev => hourOf(ev) == null);
    if (untimed.length) {
      const row = el("div", "cal-hour cal-allday");
      row.appendChild(el("div", "cal-hr", "\u2014"));
      const slot = el("div", "cal-hslot");
      for (const ev of untimed)
        slot.appendChild(el("div", "cal-ev", ev.label == null ? "" : ev.label));
      row.appendChild(slot);
      hours.appendChild(row);
    }
    const now = new Date();
    const today = dayKey(now) === rec.calDay;
    let first = null;
    for (let h = 0; h < 24; h++) {
      const row = el("div", "cal-hour" +
        (today && now.getHours() === h ? " cal-now" : ""));
      row.appendChild(el("div", "cal-hr", String(h).padStart(2, "0")));
      const slot = el("div", "cal-hslot");
      for (const ev of evs) if (hourOf(ev) === h) {
        slot.appendChild(el("div", "cal-ev", evLabel(ev)));
        if (!first) first = row;
      }
      row.appendChild(slot);
      hours.appendChild(row);
    }
    wrap.appendChild(hours);
    body.appendChild(wrap);
    // Land on the first thing that happens, else on now, else on the
    // morning — the top of an empty night is the least useful thing a day
    // can open to. Reading offsetTop lays the rows out, so this needs no
    // rAF: an hour view only ever renders from a tap on a card already on
    // screen, and a rAF would not run at all in a hidden tab.
    const to = first || (today ? hours.querySelector(".cal-now") : null) ||
      hours.children[hours.children.length - 24 + 8];
    if (to) hours.scrollTop = to.offsetTop - 4;
  }

  // timer: the served item carries ends_in (server-computed at
  // serialization, like expires_in) — the countdown renders from ends_in at
  // receipt plus local elapsed time. No ISO parsing here, no clock skew;
  // the base resyncs on every fresh poll.
  function renderTimer(body, item, rec) {
    const big = el("div", "glass-timer-big");
    body.appendChild(big);
    if (item.label) body.appendChild(el("div", "glass-timer-label", item.label));
    rec.timerEl = big;
    const ei = item.ends_in != null ? item.ends_in : item.seconds;
    if (ei != null && rec.endsAt == null)
      rec.endsAt = performance.now() + ei * 1000;
    paintTimer(rec, performance.now());
  }
  function paintTimer(rec, now) {
    if (!rec.timerEl) return;
    const left = rec.endsAt == null ? 0
      : Math.max(0, Math.round((rec.endsAt - now) / 1000));
    const h = Math.floor(left / 3600), m = Math.floor(left / 60) % 60,
      s = left % 60;
    const txt = h > 0
      ? h + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0")
      : m + ":" + String(s).padStart(2, "0");
    if (rec.timerEl.textContent !== txt) rec.timerEl.textContent = txt;
    // 0:00 holds in the accent until the server prunes the card — the
    // spec's grace window means an elapsed timer lingers ~30s on purpose
    rec.timerEl.classList.toggle("glass-timer-done", left <= 0);
  }

  function renderList(body, item) {
    for (const it of Array.isArray(item.items) ? item.items : []) {
      const row = el("div", "glass-li" + (it && it.done ? " done" : ""));
      row.appendChild(el("span", "glass-check"));
      row.appendChild(el("span", "glass-li-text", it && it.text));
      body.appendChild(row);
    }
  }

  // iframe: external embeds only. allow-scripts + allow-same-origin is
  // acceptable HERE because the content is cross-origin (the server refuses
  // loopback/non-http srcs at /cmd time, so the same-origin escape cannot
  // arise). The src host always rides the chrome — a framing-refused embed
  // is a blank this page cannot detect, so it must be a LABELED blank.
  function renderIframe(body, item, rec) {
    let host = "";
    try { host = new URL(String(item.src)).host; } catch (e) { /* labeled below */ }
    rec.hostEl.textContent = host || String(item.src || "");
    const label = (item.title || host || "embed");
    if (navigator.onLine === false) {
      rec.offline = true;             // tick() re-renders this on reconnect
      body.appendChild(fallbackNote(label + " — no embed offline"));
      return;
    }
    const f = document.createElement("iframe");
    f.setAttribute("sandbox", "allow-scripts allow-same-origin");
    f.title = String(item.title || host);
    f.addEventListener("error", guard(() => {
      rec.offline = true;             // failed mid-outage: retry too
      body.textContent = "";
      body.appendChild(fallbackNote(label + " — embed failed to load"));
    }));
    f.src = String(item.src);
    body.appendChild(f);
  }

  // html (tier 2): the fragment arrives in the item JSON and renders via
  // the srcdoc PROPERTY of an iframe sandboxed allow-scripts ONLY — no
  // allow-same-origin, ever, and the sandbox attribute is set before the
  // frame enters the DOM. srcdoc without allow-same-origin gets an opaque
  // origin: scripts run, but the fragment cannot reach window.parent,
  // cannot shed its own sandbox, and cannot read any same-origin response.
  function renderHtml(body, item) {
    const f = document.createElement("iframe");
    f.setAttribute("sandbox", "allow-scripts");
    f.title = String(item.title || item.id);
    f.srcdoc = String(item.html || "");
    body.appendChild(f);
  }

  /* ------------------------------- the player ----------------------------- */
  // A Winamp-shaped skin around a normal cross-origin YouTube embed. There is
  // no YouTube API script and no key: the embed's own postMessage protocol
  // (the "widget" channel) is the whole control surface, so the face page
  // still loads nothing from the network but the embed itself.
  //
  // Cross-origin also means the audio is UNREACHABLE to us -- no Web Audio,
  // no analyser node, ever. So the spectrum is an honest fake: CSS keyframes
  // that run while the player reports playing and freeze when it doesn't.
  // ponytail: real bars would need the audio proxied through this server,
  // which is a different (and much larger) program.
  const YT = "https://www.youtube.com";

  function ytSrc(item) {
    const p = new URLSearchParams({
      enablejsapi: "1", autoplay: "1", playsinline: "1", controls: "0",
      rel: "0", modestbranding: "1", iv_load_policy: "3",
    });
    // file:// has origin "null", which YouTube refuses; only send a real one.
    if (/^https?:/.test(location.origin)) p.set("origin", location.origin);
    if (item.playlist) {
      p.set("list", String(item.playlist));
      p.set("listType", "playlist");
      return YT + "/embed/videoseries?" + p;
    }
    const ids = (item.tracks || []).map(t => t.id);
    // /embed/<first>?playlist=<rest> is what makes next/prev walk a queue.
    if (ids.length > 1) p.set("playlist", ids.slice(1).join(","));
    return YT + "/embed/" + encodeURIComponent(ids[0] || "") + "?" + p;
  }

  function mmss(sec) {
    if (!(sec >= 0)) return "--:--";
    const s = Math.floor(sec);
    return (s / 60 | 0) + ":" + String(s % 60).padStart(2, "0");
  }

  /* ---- the media gate ---------------------------------------------------
     The voice line must not answer the music, and it CANNOT filter the
     music out: the mic is sounddevice on the host, the music is inside a
     cross-origin iframe, and nothing joins the two -- no reference signal,
     so no echo cancellation is even possible here. What the agent gets
     instead is the bare FACT that sound is playing; backtalk requires its
     wake word for as long as it is set (signals.media_playing).
     ponytail: one POST on the transition plus a keepalive. No socket, no
     protocol. The keepalive is the part that matters -- the file expires
     30s after the last post, so a closed tab or a crashed renderer cannot
     leave her demanding her name in a silent room. */
  const MEDIA_KEEPALIVE_MS = 10000;
  // She talks OVER the music, not under it. This is the ONLY thing ducking
  // buys here: it does not help her hear YOU. Whisper captures a whole
  // utterance at once, so by the time a wake word has been recognised the
  // command it introduced is already over -- ducking on wake would arrive
  // after the audio it was meant to rescue.
  const DUCK_PCT = 0.25;
  const PLAYERS = new Set();     // live player cards, pruned by isConnected
  let mediaOn = false, mediaAt = 0;

  function mediaTick() {
    // Self-cleaning registry: a card that exited took its frame out of the
    // DOM, which is a truth no lifecycle hook can drift away from.
    for (const p of PLAYERS) if (!p.frame.isConnected) PLAYERS.delete(p);
    const on = [...PLAYERS].some(p => p.playing);
    const now = Date.now();
    if (on !== mediaOn || (on && now - mediaAt > MEDIA_KEEPALIVE_MS)) {
      mediaOn = on;
      mediaAt = now;
      fetch(new URL("media", ROOT).href, {
        method: "POST",
        // the same JSON-only cross-site write defense as every POST here
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ playing: on }),
      }).catch(() => {});
    }
    const speak = (typeof AV !== "undefined") && AV.state === "speaking";
    for (const p of PLAYERS) {
      if (!p.playing || speak === p.ducked) continue;
      // Capture on the way DOWN only: the embed echoes the ducked volume
      // back at us, so reading the slider on the way up would restore to
      // whatever we just set.
      if (speak) p.vol = Number(p.volEl.value) || 100;
      p.ducked = speak;
      p.send("setVolume", [Math.round(speak ? p.vol * DUCK_PCT : p.vol)]);
    }
  }

  function renderPlayer(body, item, rec) {
    const label = item.title || "player";
    if (navigator.onLine === false) {
      rec.offline = true;               // tick() re-renders this on reconnect
      body.appendChild(fallbackNote(label + " — no player offline"));
      return;
    }
    rec.hostEl.textContent = "youtube.com";

    const video = item.mode === "video";
    const wa = el("div", "wa" + (video ? " wa-vid" : ""));
    const tracks = Array.isArray(item.tracks) ? item.tracks : [];

    // -- the embed. Same sandbox premise as the iframe card: the content is
    // cross-origin, so it can never reach this page's origin.
    const stage = el("div", "wa-stage");
    const frame = document.createElement("iframe");
    frame.setAttribute("sandbox",
      "allow-scripts allow-same-origin allow-presentation");
    frame.setAttribute("allow", "autoplay; encrypted-media; picture-in-picture");
    frame.title = label;
    frame.src = ytSrc(item);
    stage.appendChild(frame);
    wa.appendChild(stage);

    // -- the LCD
    const screen = el("div", "wa-screen");
    const timeEl = el("div", "wa-time", "--:--");
    const meta = el("div", "wa-meta");
    const marq = el("div", "wa-marquee");
    const marqTxt = el("span", null, tracks.length
      ? (tracks[0].title || tracks[0].id) : "loading…");
    marq.appendChild(marqTxt);
    const badges = el("div", "wa-badges", "128 kbps  ·  44 kHz  ·  stereo");
    meta.appendChild(marq); meta.appendChild(badges);
    const viz = el("div", "wa-viz");
    for (let i = 0; i < 19; i++) {
      const b = el("i");
      // Per-bar period and phase: one keyframe, 19 different dances.
      b.style.animationDuration = (0.42 + (i % 5) * 0.13).toFixed(2) + "s";
      b.style.animationDelay = "-" + (i * 0.11).toFixed(2) + "s";
      viz.appendChild(b);
    }
    screen.appendChild(timeEl); screen.appendChild(meta); screen.appendChild(viz);
    wa.appendChild(screen);

    // -- seek + transport. Native range inputs: keyboard, touch and drag
    // already work on them, and a hand-rolled slider would only lose that.
    const seek = document.createElement("input");
    seek.type = "range"; seek.className = "wa-seek";
    seek.min = 0; seek.max = 1000; seek.value = 0;
    seek.setAttribute("aria-label", "seek");
    wa.appendChild(seek);

    const deck = el("div", "wa-deck");
    const btns = el("div", "wa-btns");
    const BTN = [["prev", "⏮", "previous"], ["play", "▶", "play"],
                 ["pause", "⏸", "pause"], ["stop", "⏹", "stop"],
                 ["next", "⏭", "next"]];
    const playBtn = {};
    for (const [k, glyph, name] of BTN) {
      const b = el("button", "wa-b", glyph);
      b.type = "button"; b.dataset.c = k; b.title = name;
      b.setAttribute("aria-label", name);
      btns.appendChild(b);
      playBtn[k] = b;
    }
    const tog = el("div", "wa-tog");
    const shuf = el("button", "wa-t", "SHUF");
    const rep = el("button", "wa-t", "REP");
    shuf.type = rep.type = "button";
    tog.appendChild(shuf); tog.appendChild(rep);
    const vol = document.createElement("input");
    vol.type = "range"; vol.className = "wa-vol";
    vol.min = 0; vol.max = 100; vol.value = 100;
    vol.setAttribute("aria-label", "volume");
    deck.appendChild(btns); deck.appendChild(tog); deck.appendChild(vol);
    wa.appendChild(deck);

    // -- the playlist editor
    const pl = el("ol", "wa-pl");
    wa.appendChild(pl);
    function row(i, t) {
      const li = el("li", "wa-row");
      li.appendChild(el("b", null, String(i + 1) + "."));
      li.appendChild(el("span", "wa-rt",
        (t.artist ? t.artist + " — " : "") + (t.title || t.id)));
      li.appendChild(el("em", null, t.dur || ""));
      li.dataset.i = i;
      return li;
    }
    tracks.forEach((t, i) => pl.appendChild(row(i, t)));
    onTap(pl, e => {
      const li = e.target.closest(".wa-row");
      if (li) send("playVideoAt", [Number(li.dataset.i)]);
    });

    body.appendChild(wa);

    /* ---- wiring ---- */
    function send(func, args) {
      try {
        frame.contentWindow.postMessage(JSON.stringify(
          { event: "command", func: func, args: args || [] }), YT);
      } catch (e) { /* frame not ready yet; the handshake retries */ }
    }
    const state = { ready: false, dragging: false, dur: 0, idx: -1,
                    shuffle: false, repeat: false };
    const media = { frame: frame, send: send, volEl: vol,
                    playing: false, vol: 100, ducked: false };
    PLAYERS.add(media);

    onTap(btns, e => {
      const b = e.target.closest("button");
      if (!b) return;
      send({ prev: "previousVideo", play: "playVideo", pause: "pauseVideo",
             stop: "stopVideo", next: "nextVideo" }[b.dataset.c]);
    });
    onTap(shuf, () => {
      state.shuffle = !state.shuffle;
      shuf.classList.toggle("on", state.shuffle);
      send("setShuffle", [state.shuffle]);
    });
    onTap(rep, () => {
      state.repeat = !state.repeat;
      rep.classList.toggle("on", state.repeat);
      send("setLoop", [state.repeat]);
    });
    vol.addEventListener("input", guard(() =>
      send("setVolume", [Number(vol.value)])));
    seek.addEventListener("pointerdown", guard(() => { state.dragging = true; }));
    seek.addEventListener("change", guard(() => {
      state.dragging = false;
      if (state.dur) send("seekTo", [state.dur * seek.value / 1000, true]);
    }));

    // The embed only starts reporting after it is asked to. It is not ready
    // the instant the frame loads, so ask on load and keep asking until the
    // first reply lands (or the card is gone).
    let tries = 0;
    const hello = () => {
      if (state.ready || !frame.isConnected || tries++ > 20) return;
      send("listening");      // "listening" is the subscribe, not a command
      try {
        frame.contentWindow.postMessage(JSON.stringify(
          { event: "listening", id: 1, channel: "widget" }), YT);
      } catch (e) { /* retried below */ }
      setTimeout(guard(hello), 400);
    };
    // Only after load: before it the frame is still about:blank, which
    // inherits THIS origin, and posting to the youtube.com target origin
    // there is a guaranteed console warning for a message nobody wanted.
    frame.addEventListener("load", guard(hello));

    // Dispatched to by the one page-level message listener (see boot()).
    rec.yt = {
      frame: frame,
      onMsg: guard(raw => {
        let m;
        try { m = JSON.parse(raw); } catch (e) { return; }
        if (!m || typeof m !== "object") return;
        state.ready = true;
        const info = m.info;
        if (m.event === "onStateChange") return paint(m.info);
        if (!info || typeof info !== "object") return;
        if (typeof info.duration === "number") state.dur = info.duration;
        if (typeof info.currentTime === "number") {
          timeEl.textContent = mmss(info.currentTime);
          if (!state.dragging && state.dur)
            seek.value = Math.min(1000, info.currentTime / state.dur * 1000);
        }
        if (typeof info.volume === "number" && document.activeElement !== vol
            && !media.ducked)
          vol.value = info.volume;
        if (Array.isArray(info.playlist) && !tracks.length && !pl.children.length)
          info.playlist.forEach((id, i) =>
            pl.appendChild(row(i, { id: id, title: "Track " + (i + 1) })));
        const vd = info.videoData;
        if (vd && vd.video_id) {
          const known = tracks.findIndex(t => t.id === vd.video_id);
          const i = known >= 0 ? known
                   : (typeof info.playlistIndex === "number"
                      ? info.playlistIndex : -1);
          if (i !== state.idx) {
            state.idx = i;
            [...pl.children].forEach((li, j) =>
              li.classList.toggle("on", j === i));
          }
          const t = tracks[i];
          const who = (t && t.artist) || vd.author || "";
          const what = (t && t.title) || vd.title || "";
          if (what) {
            marqTxt.textContent =
              (i >= 0 ? (i + 1) + ". " : "") + what + (who ? " — " + who : "");
            // Winamp only scrolls what does not fit; so does this.
            // The exact overshoot, measured: a percentage keyframe would
            // over- or under-run every title of a different length.
            marq.style.setProperty("--wa-run",
              (marq.clientWidth - marqTxt.scrollWidth - 6) + "px");
            marq.classList.toggle("run",
              marqTxt.scrollWidth > marq.clientWidth + 2);
            const cur = pl.children[i];
            if (cur) {
              const rt = cur.querySelector(".wa-rt");
              if (rt && !tracks[i]) rt.textContent = (who ? who + " — " : "") + what;
            }
          }
        }
        if (typeof info.playerState === "number") paint(info.playerState);
      }),
    };

    function paint(st) {
      // -1 unstarted, 0 ended, 1 playing, 2 paused, 3 buffering, 5 cued
      const playing = st === 1;
      media.playing = playing;    // read by mediaTick, published to the bus
      wa.classList.toggle("wa-on", playing);
      // Autoplay is blocked until the page has been interacted with, and a
      // silent dead player is the worst possible failure here -- so the play
      // button advertises itself until something is actually rolling.
      playBtn.play.classList.toggle("wa-need", st === -1 || st === 5 || st === 2);
      if (st === 0) timeEl.textContent = mmss(state.dur);
    }
    paint(-1);
  }

  function renderUnknown(body, item) {
    // the server validates types at /cmd, so this is future-proofing:
    // nothing ever renders half-broken or unlabeled
    body.appendChild(fallbackNote('no renderer for type "' + item.type + '"'));
  }

  const REGISTRY = {
    note: renderNote,
    image: renderImage,
    map: renderMap,
    calendar: renderCalendar,
    timer: renderTimer,
    list: renderList,
    iframe: renderIframe,
    html: renderHtml,
    player: renderPlayer,
  };

  /* ------------------------------ card lifecycle --------------------------- */
  // id -> {el, body, titleEl, hostEl, timerEl, rev, cell, span, deadline,
  //        endsAt, item, offline}. deadline/endsAt are performance.now()-
  // based absolutes derived from the payload's relative seconds at receipt.
  // item/offline exist for the reconnect re-render: bodies only rebuild on
  // a rev change, so a map/iframe drawn as an offline blank would otherwise
  // stay one forever.
  const cards = new Map();

  function renderInto(rec, item) {
    rec.item = item;         // retained: nothing else keeps it post-render
    rec.offline = false;     // re-earned by the renderer on every render
    rec.titleEl.textContent = titleFor(item);
    rec.hostEl.textContent = "";
    rec.timerEl = null;
    rec.body.textContent = "";
    (REGISTRY[item.type] || renderUnknown)(rec.body, item, rec);
  }

  // The close button every card wears. It posts the same `dismiss` the
  // agent would — the payload stays the source of truth, so the card leaves
  // on the next poll rather than being yanked from under the diff, and a
  // server that refuses simply leaves it standing. Under ?glassdemo=1 there
  // is no server, so the fixture retires locally.
  function closeBtn(item) {
    const x = el("button", "glass-x", "\u00d7");
    x.type = "button";
    x.title = "close";
    x.setAttribute("aria-label", "close " + titleFor(item));
    x.addEventListener("click", guard(e => {
      e.preventDefault();
      e.stopPropagation();
      if (GLASSDEMO) {
        const rec = cards.get(item.id);
        if (rec) removeCard(item.id, rec);
        return;
      }
      fetch(new URL("cmd", ROOT).href, {
        method: "POST",
        // same-origin JSON: the server's cross-site write defense
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ a: "dismiss", id: item.id }),
      }).catch(() => { /* offline: the card stays, which is the truth */ });
    }));
    return x;
  }

  function addCard(item, animate) {
    const card = el("section", "glass-card glass-" + item.type);
    const head = el("header", "glass-head");
    const rec = {
      el: card,
      body: el("div", "glass-body"),
      titleEl: el("span", "glass-title"),
      hostEl: el("span", "glass-host"),
      timerEl: null,
      rev: item.rev,
      cell: item.cell,
      span: item.span.slice(),
      deadline: item.expires_in == null ? null
        : performance.now() + item.expires_in * 1000,
      endsAt: null,
      item: null,          // both set by renderInto below
      offline: false,
    };
    head.append(rec.titleEl, rec.hostEl, el("span", "glass-id", item.id),
                closeBtn(item));
    card.append(head, rec.body);
    card.dataset.id = item.id;      // hands.js reads this to post /cmd
    placeEl(card, item.cell, item.span);
    renderInto(rec, item);
    cards.set(item.id, rec);
    layer.appendChild(card);
    if (animate && !RM.matches) {
      card.classList.add("glass-enter");
      card.addEventListener("animationend",
        () => card.classList.remove("glass-enter"), { once: true });
    }
  }

  function removeCard(id, rec) {
    cards.delete(id);
    const card = rec.el;
    if (RM.matches) { card.remove(); return; }
    card.classList.remove("glass-enter", "glass-expiring");
    card.classList.add("glass-exit");
    let gone = false;
    const fin = () => { if (!gone) { gone = true; card.remove(); } };
    card.addEventListener("animationend", fin, { once: true });
    setTimeout(fin, 400);      // hidden tabs can swallow animationend
  }

  /* ------------------------------ the diff ------------------------------- */
  let lastRev = null;        // last glass.rev actually rendered
  let lastPayload = null;    // last payload OBJECT (identity = liveness)
  let seenFirst = false;     // first paint after load renders settled
  let lastFreshT = 0;
  const locallyExpired = new Set();   // ids we retired during an outage

  function applyBoard(g) {
    const animate = seenFirst;      // no entrance wall on page refresh
    const seen = new Set();
    for (const item of g.items || []) {
      seen.add(item.id);
      const rec = cards.get(item.id);
      if (!rec) { addCard(item, animate); continue; }
      if (item.rev !== rec.rev) {   // content replace: body swap + flash,
        rec.rev = item.rev;         // never a full exit+enter
        renderInto(rec, item);
        flash(rec.el);
      }
      if (item.cell !== rec.cell || item.span[0] !== rec.span[0] ||
          item.span[1] !== rec.span[1]) {
        rec.cell = item.cell;
        rec.span = item.span.slice();
        flip(rec.el, () => placeEl(rec.el, item.cell, item.span));
      }
    }
    for (const [id, rec] of [...cards])
      if (!seen.has(id)) removeCard(id, rec);
  }

  // Fresh payloads also carry freshly-computed relative clocks; resync the
  // local absolutes every poll so an outage counts down from the latest
  // server truth, not from wherever the page happened to load.
  function resyncClocks(g, now) {
    for (const item of g.items || []) {
      const rec = cards.get(item.id);
      if (!rec) continue;
      rec.deadline = item.expires_in == null ? null
        : now + item.expires_in * 1000;
      if (item.ends_in != null) rec.endsAt = now + item.ends_in * 1000;
    }
  }

  // Local countdowns: the expiry pre-fade pulse in the last 10 s, the exit
  // when a deadline lapses (this is what keeps ephemerals honest during a
  // poll outage), and the ~1 Hz timer digits.
  function sweep(now) {
    for (const [id, rec] of [...cards]) {
      if (rec.timerEl) paintTimer(rec, now);
      if (rec.deadline == null) {
        // pinned mid-pulse: the deadline vanished (pin bumps glass.rev but
        // not item.rev, so no re-render), and nothing else ever toggles the
        // class off — clear it here or the card pulses forever
        rec.el.classList.remove("glass-expiring");
        continue;
      }
      const left = rec.deadline - now;
      rec.el.classList.toggle("glass-expiring", left > 0 && left <= 10000);
      if (left <= 0) { locallyExpired.add(id); removeCard(id, rec); }
    }
  }

  let wasOffline = navigator.onLine === false;

  function tick() {
    const now = performance.now();
    // Reconnect recovery: a card first drawn during a network drop is a
    // labeled blank, and bodies only rebuild on a rev change — so on the
    // offline→online edge, re-render just the flagged cards. flash() makes
    // the upgrade read as the standard replace beat. Runs for glassdemo
    // fixtures too, and inside guard() so §8 teardown is untouched.
    mediaTick();
    const offline = navigator.onLine === false;
    if (wasOffline && !offline) {
      for (const rec of cards.values())
        if (rec.offline && rec.item) { renderInto(rec, rec.item); flash(rec.el); }
    }
    wasOffline = offline;
    if (!GLASSDEMO) {
      const g = (typeof AV !== "undefined") ? AV.glass : null;
      if (g && g !== lastPayload) {           // a poll landed
        lastPayload = g;
        lastFreshT = now;
        layer.classList.remove("glass-stale");
        resyncClocks(g, now);
        // If we retired anything locally during an outage, the server may
        // still be listing it (its rev needn't have moved) — force one
        // diff so the board reconverges on the server's truth.
        const force = locallyExpired.size > 0;
        locallyExpired.clear();
        if (g.rev !== lastRev || force) { applyBoard(g); lastRev = g.rev; }
        seenFirst = true;
      } else if (seenFirst && now - lastFreshT > 5000) {
        layer.classList.add("glass-stale");   // cleared on the next good poll
      }
    }
    sweep(now);
  }

  /* --------------------------- ?glassdemo fixtures ------------------------- */
  // One local item per registered type, staged with a small stagger so the
  // materialize beat is actually visible (the settled-first-paint rule
  // governs live polling, not this QA mode). The timer fixture runs out
  // inside two minutes so the pre-fade pulse and the exit are eyeballable
  // too. Cells hug the rails; the center stays mostly clear so this
  // composes with core's ?demo=1 loop.
  function stageFixtures() {
    const today = new Date();
    const plus = days => {
      const d = new Date(today);
      d.setDate(d.getDate() + days);
      return dayKey(d);
    };
    const FIXTURES = [
      { id: "note-1", type: "note", title: "Groceries", cell: "A1",
        span: [3, 2], pin: true, rev: 1, expires_in: null, flags: [],
        body: "**market run**, then `glass.sh`:\n- eggs\n- basil\n" +
          "```\n{\"a\":\"show\",\"type\":\"note\"}\n```" },
      { id: "list-1", type: "list", title: "Preflight", cell: "A3",
        span: [3, 3], pin: false, rev: 1, expires_in: 600, flags: [],
        items: [{ text: "power", done: true }, { text: "mic level", done: true },
          { text: "glass overlay", done: false }] },
      { id: "player-1", type: "player", title: "Sultans of Swing",
        cell: "G1", span: [4, 4], pin: false, rev: 1, expires_in: 600,
        flags: [], mode: "audio", tracks: [
          { id: "h0ffIJ7ZO4U", title: "Sultans Of Swing",
            artist: "Dire Straits", dur: "4:27" },
          { id: "8Tzs8LiHf8Y", title: "Money For Nothing",
            artist: "Dire Straits", dur: "4:07" }] },
      { id: "timer-1", type: "timer", title: "Timer", cell: "A6",
        span: [2, 2], pin: false, rev: 1, expires_in: 120, flags: [],
        label: "tea", seconds: 90, ends_in: 90 },
      { id: "calendar-1", type: "calendar", title: "Week ahead", cell: "D1",
        span: [3, 4], pin: false, rev: 1, expires_in: 600, flags: [],
        view: "month", events: [
          { date: plus(0), time: "09:00", label: "standup" },
          { date: plus(2), label: "dentist" },
          { date: plus(5), time: "19:30", label: "flight BCN" }] },
      { id: "html-1", type: "html", title: "Fragment", cell: "G1",
        span: [3, 3], pin: false, rev: 1, expires_in: 600, flags: [],
        html: "<body style='margin:0;font-family:monospace;color:#3ddc84;" +
          "padding:12px'><p>agent-authored fragment — opaque origin.</p>" +
          "<p id=t></p><script>document.getElementById('t').textContent=" +
          "'scripts run: '+new Date().toLocaleTimeString()<\/script>" },
      { id: "image-1", type: "image", title: "Face plate", cell: "J1",
        span: [3, 3], pin: false, rev: 1, expires_in: 600, flags: [],
        src: "assets/face.png", caption: "assets/face.png" },
      { id: "map-1", type: "map", title: "Reykjavík", cell: "J4",
        span: [3, 3], pin: false, rev: 1, expires_in: 600, flags: [],
        q: "Reykjavík", zoom: 6 },
      { id: "iframe-1", type: "iframe", title: "Example", cell: "J7",
        span: [3, 2], pin: false, rev: 1, expires_in: 600, flags: [],
        src: "https://example.com/" },
    ];
    FIXTURES.forEach((item, i) =>
      setTimeout(guard(() => addCard(item, true)), 250 + i * 140));
  }

  /* --------------------------------- boot ---------------------------------- */
  function boot() {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = new URL("glass-theme.css", ROOT).href;
    document.head.appendChild(link);
    layer = el("div");
    layer.id = "glass-layer";
    document.body.appendChild(layer);
    // One listener for every player card. It matches the sender against the
    // live cards' own frames, so a dead card unregisters itself just by
    // leaving the map -- and a message from anywhere but the embed's own
    // origin is dropped before it is even parsed.
    addEventListener("message", guard(e => {
      if (e.origin !== YT) return;
      for (const rec of cards.values())
        if (rec.yt && rec.yt.frame.contentWindow === e.source)
          return rec.yt.onMsg(e.data);
    }));
    if (GLASSDEMO) stageFixtures();
    // ~8 Hz, matching the poll cadence — the diff only touches the DOM
    // when glass.rev moves, so an idle tick is a couple of comparisons
    ticker = setInterval(guard(tick), 120);
  }

  if (document.body) guard(boot)();
  else addEventListener("DOMContentLoaded", guard(boot));
})();
