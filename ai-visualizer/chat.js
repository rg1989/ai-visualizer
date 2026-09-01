/* chat.js — the conversation crawl.

   The most important pane of the constant UI: what was said and what
   was answered, rolling upward like a title crawl. New lines land at
   the bottom in full focus; each one holds the stage for 30s from
   when it was spoken, then fades to nothing. One glance confirms the
   transcript heard what you actually said; a missed reply can always
   be re-read.

   Data: backtalk appends the dialogue to .voice_chat on the signal
   bus; the server exposes it at /chat and puts a cheap chat_rev in
   /state. core.js mirrors that as AV.chatRev; this file fetches /chat
   only when it moves. A streaming reply grows its own bubble in place.

   Interaction: hovering the pane brings the faded history back — just
   the words, no backdrop — and frees the wheel to scroll through it;
   leaving snaps focus to the newest. Timestamps sit small and dim
   beside every message. A reply still streaming keeps its own clock
   fresh so it never fades mid-sentence.

   Wrapped like glass.js: any exception removes the pane and leaves
   the face untouched. */

(() => {
  "use strict";
  // AV is a top-level const in core.js: a global lexical binding, NOT
  // a window property — typeof is the only safe existence test.
  if (typeof AV === "undefined") return;
  const T = (typeof AVTheme !== "undefined") ? AVTheme.overrides : {};

  const COLS = 12, ROWS = 8, GUTTER = 12, MARGIN = 24;

  function guard(fn) {
    return function (...a) {
      try { return fn.apply(this, a); } catch (e) {
        try {
          console.warn("[chat] disabled:", e);
          const el = document.getElementById("av-chat");
          if (el) el.remove();
          if (_timer) clearInterval(_timer);
        } catch (_) { /* the face plays on */ }
      }
    };
  }

  function cellRect(cell, span) {
    const c = cell.charCodeAt(0) - 65, r = parseInt(cell.slice(1), 10) - 1;
    const [w, h] = span;
    const cw = (innerWidth - 2 * MARGIN - (COLS - 1) * GUTTER) / COLS;
    const ch = (innerHeight - 2 * MARGIN - (ROWS - 1) * GUTTER) / ROWS;
    return {
      left: MARGIN + c * (cw + GUTTER),
      top: MARGIN + r * (ch + GUTTER),
      width: w * cw + (w - 1) * GUTTER,
      height: h * ch + (h - 1) * GUTTER,
    };
  }

  const AREA = AV.chatArea || { cell: "D6", span: [6, 3] };
  const PILL_LANE = 40;   // status pill's reserved strip at the bottom
  let pane = null, feed = null, _timer = null;
  let seenRev = -1, lastCount = 0, lastMidText = "";

  const build = guard(function () {
    pane = document.createElement("div");
    pane.id = "av-chat";
    pane.style.cssText =
      "position:fixed;z-index:20;overflow:hidden;pointer-events:auto;" +
      "display:flex;flex-direction:column;justify-content:flex-end;" +
      "font-family:var(--av-display,'VT323'),monospace;cursor:default;";
    feed = document.createElement("div");
    feed.style.cssText =
      "overflow-y:auto;scrollbar-width:none;display:flex;" +
      "flex-direction:column;gap:10px;padding:8px 6px;";
    pane.appendChild(feed);
    // Hovering resurrects faded lines for scrubbing — no backdrop,
    // only the messages themselves come back.
    pane.addEventListener("mouseenter", () => { hovered = true; ageTick(); });
    pane.addEventListener("mouseleave", () => {
      hovered = false;
      ageTick();
      feed.scrollTop = feed.scrollHeight;
    });
    document.body.appendChild(pane);
    place();
    addEventListener("resize", place);
  });

  const place = guard(function () {
    const r = cellRect(AREA.cell || "A2", AREA.span || [3, 5]);
    pane.style.left = r.left + "px";
    pane.style.top = r.top + "px";
    pane.style.width = r.width + "px";
    // ponytail: the status pill parks at the bottom of the viewport and the
    // chat grid's last row runs straight under it. Give the pill its lane.
    pane.style.height = Math.max(80, r.height - PILL_LANE) + "px";
  });

  function stamp(t) {
    const d = new Date(t * 1000);
    return String(d.getHours()).padStart(2, "0") + ":" +
           String(d.getMinutes()).padStart(2, "0");
  }

  const AGENT = (AV.name || "JARVIS").toUpperCase();
  const AGENT_COLOR = T.agent || "#ffd166";
  const UNKNOWN_COLOR = T.unknown || "#9fb0a8";
  // Each identified human gets a stable color of their own, picked by
  // name hash from a fixed palette — same person, same color, always.
  const HUMAN_PALETTE = T.people || ["#7fd9ff", "#d78cff", "#8cff9e",
                        "#ff9e7f", "#f2e97f"];
  function humanColor(who) {
    if (!who) return UNKNOWN_COLOR;
    let h = 0;
    for (const ch of who) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return HUMAN_PALETTE[h % HUMAN_PALETTE.length];
  }

  function avatar(you, who, color) {
    const a = document.createElement("div");
    a.style.cssText =
      "flex:none;width:30px;height:30px;overflow:hidden;" +
      "border-radius:var(--av-avatar-radius,50%);" +
      "display:flex;align-items:center;justify-content:center;" +
      "font-size:17px;border:1px solid " + color + ";" +
      "background:rgba(0,0,0,.5);color:" + color +
      ";box-shadow:0 0 8px " + color + "44;";
    // The agent and an unknown voice wear the theme's own glyphs
    // (theme.js draws each set in that theme's design idiom, in
    // currentColor so the speaker color tints them); a known human
    // keeps their initial. No theme -> the shipped emoji.
    const glyph = !you ? (T.chatAgent || "🤖")
                : (who ? who[0].toUpperCase() : (T.chatPerson || "👤"));
    if (glyph[0] === "<") {
      a.innerHTML = glyph;   // trusted markup: authored in theme.js
      a.firstChild.style.cssText = "display:block;width:100%;height:100%";
    } else {
      a.textContent = glyph;
    }
    return a;
  }

  function bubble(m) {
    const you = m.role === "you";
    const color = you ? humanColor(m.who) : AGENT_COLOR;
    const row = document.createElement("div");
    row.className = "av-chat-msg";
    row.style.cssText =
      "display:flex;align-items:flex-end;gap:10px;width:100%;" +
      (you ? "flex-direction:row-reverse;" : "") +
      "animation:avChatIn .35s ease-out;transition:opacity 1.2s ease;";
    // Age from when it was spoken, so a page opened later starts dark.
    row._born = m.t ? m.t * 1000 : Date.now();
    row.appendChild(avatar(you, m.who, color));
    const el = document.createElement("div");
    el.style.cssText =
      "max-width:78%;line-height:1.3;letter-spacing:.03em;" +
      "padding:8px 14px;border-radius:var(--av-radius,10px);" +
      (you ? "border-top-right-radius:2px;text-align:right;"
           : "border-top-left-radius:2px;") +
      "background:var(--av-bubble,rgba(4,14,10,.55));border:1px solid " + color + "33;";
    const head = document.createElement("div");
    head.style.cssText =
      "font-size:15px;letter-spacing:.18em;opacity:.6;color:" + color +
      ";display:flex;gap:10px;justify-content:space-between;" +
      (you ? "flex-direction:row-reverse;" : "");
    head.innerHTML =
      "<span></span><span style='opacity:.8'>" + stamp(m.t) + "</span>";
    head.firstChild.textContent =
      you ? ((m.who || "UNKNOWN VOICE").toUpperCase()) : AGENT;
    const body = document.createElement("div");
    body.className = "av-chat-body";
    body.style.cssText =
      "font-size:var(--av-chat-size,22px);white-space:pre-wrap;color:var(--av-ink,#eafaf4);";
    setBody(body, m);
    el.appendChild(head);
    el.appendChild(body);
    row.appendChild(el);
    return row;
  }

  // Speech-paced reveal: the voice line stamps each fresh agent segment
  // with its audio start and duration (m.seg = {off, dur, t}); the text
  // past `off` types out in step with the voice, so nothing is readable
  // before it has been said. Progress is anchored to seg.t (wall clock,
  // same machine), so an already-spoken segment — an old message, a
  // page that joined late — renders complete instantly.
  function setBody(body, m) {
    if (body._tw) { clearInterval(body._tw); body._tw = null; }
    const full = m.text || "", seg = m.seg;
    if (!seg || seg.off == null || !seg.dur || m.role === "you") {
      body.textContent = full;
      return;
    }
    const segText = full.slice(seg.off);
    const cps = segText.length / seg.dur;
    const paint = () => {
      const n = Math.floor((Date.now() / 1000 - seg.t) * cps);
      if (n >= segText.length) {
        if (body._tw) { clearInterval(body._tw); body._tw = null; }
        body.textContent = full;
        return;
      }
      body.textContent = full.slice(0, seg.off) + segText.slice(0, Math.max(0, n));
      feed.scrollTop = feed.scrollHeight;   // tail stays visible as it grows
    };
    paint();
    if (!body._tw && body.textContent !== full)
      body._tw = setInterval(paint, 66);
  }

  function msgKey(m) {
    return (m.mid || "") + "|" + m.t + "|" + m.role;
  }

  const render = guard(function (data) {
    const msgs = data.msgs || [];
    const last = msgs[msgs.length - 1];
    // Grow-in-place ONLY when it is genuinely the same last message
    // with more text: same count AND same identity. Once the store
    // hits its 120 cap the count stops moving while messages still
    // roll — the key check catches that and rebuilds.
    if (msgs.length === lastCount && last && feed.lastElementChild &&
        msgKey(last) === lastMidText) {
      setBody(feed.lastElementChild.querySelector(".av-chat-body"), last);
      feed.lastElementChild._born = Date.now();  // streaming: stay lit
    } else if (msgs.length >= lastCount + 1 || !msgs.length ||
               feed.children.length === 0) {
      // A stream can grow AND a new message land between two polls
      // (an interrupt mid-reply): refresh the current last bubble
      // before appending, or its final sentences are lost for good.
      if (lastCount > 0 && feed.children.length > 0 &&
          msgs[lastCount - 1]) {
        const fin = feed.lastElementChild.querySelector(".av-chat-body");
        if (fin._tw) { clearInterval(fin._tw); fin._tw = null; }
        fin.textContent = msgs[lastCount - 1].text;
      }
      for (let i = lastCount; i < msgs.length; i++) {
        feed.appendChild(bubble(msgs[i]));
      }
      while (feed.children.length > 120) feed.firstChild.remove();
      lastCount = msgs.length;
    } else {
      // Pruned window shifted under us: rebuild whole.
      feed.textContent = "";
      for (const m of msgs) feed.appendChild(bubble(m));
      lastCount = msgs.length;
    }
    if (last) lastMidText = msgKey(last);
    // A new message always snaps focus back to now.
    feed.scrollTop = feed.scrollHeight;
    ageTick();
  });

  const CHAT_URL = new URL("/chat", location.origin).href;

  const poll = guard(async function () {
    const rev = AV.chatRev || 0;
    if (rev === seenRev) return;
    try {
      const r = await fetch(CHAT_URL);
      render(await r.json());
      // Only a delivered render retires the revision — a failed fetch
      // (server blipped) must retry it, not silently skip messages.
      seenRev = rev;
    } catch (e) { /* next poll retries */ }
  });

  // Each line stands for 30s from when it was spoken, then fades to
  // nothing; hovering the pane brings every line back while scrubbing.
  const FADE_AFTER_MS = 30000;
  let hovered = false;
  const ageTick = guard(function () {
    const kids = feed.children, now = Date.now();
    for (let i = 0; i < kids.length; i++) {
      const live = now - (kids[i]._born || 0) < FADE_AFTER_MS;
      kids[i].style.opacity =
        (hovered || live) ? (i === kids.length - 1 ? "1" : ".85") : "0";
    }
  });

  const css = document.createElement("style");
  css.textContent =
    "@keyframes avChatIn{from{opacity:0;transform:translateY(14px)}" +
    "to{opacity:1;transform:none}}" +
    "#av-chat ::-webkit-scrollbar{display:none}" +
    "@media (prefers-reduced-motion: reduce){.av-chat-msg{animation:none !important}}";
  document.head.appendChild(css);

  build();
  _timer = setInterval(() => { poll(); ageTick(); }, 300);
})();
