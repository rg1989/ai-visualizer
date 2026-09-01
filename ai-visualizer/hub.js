/*
ai-visualizer: give your AI agent a face.
Copyright (C) 2026 Jared Rhodenizer

SPDX-License-Identifier: AGPL-3.0-or-later
*/

/* THE HUB — barehands' ORBITAL BLOOM, brought to the faces.
   A ring hovers on the face's own center. Tap it and the folders in
   ai-visualizer.json's "orbs" bloom around it; tap an orb for its file
   explorer (a list, or big icons); tap a note for the reader.
   A pinch is a click (hands.js calls el.click() on a tap), so every
   control here is an ordinary clickable element and nothing else. */
"use strict";
(() => {
  const ROOT = new URL(".", document.currentScript.src);
  let ORBS = [];            // [{title, kind}] from /config
  const TREES = [];         // lazily fetched /tree?orb=i, by index
  let path = [];            // explorer breadcrumb: [orbIndex, dirName, ...]

  /* Everything sits UNDER z-index 70, which is the hands cursor layer.
     A native <dialog> would have handed us esc-to-close for free, but
     showModal() puts it in the top layer — above the cursor — so a hand
     would vanish the moment it opened its own panel. Plain divs; esc and
     click-outside are four lines below. */
  const CSS = `
  #av-hub, #av-orbs, #av-panel, #av-reader {
    --a: var(--av-accent, #3ddc84); --ink: var(--av-ink, #e8f0f2);
    --dim: var(--av-dim, #5a6a72); --line: var(--av-card-line, #1c2f26);
    --r: var(--av-radius, 6px);
    font-family: var(--av-display, "SF Mono", Menlo, Consolas, monospace);
  }
  #av-hub { position: fixed; left: 50%; top: 50%; z-index: 60;
    width: clamp(120px, 22vmin, 210px); aspect-ratio: 1; padding: 0;
    transform: translate(-50%, -50%); border-radius: 50%; cursor: pointer;
    background: transparent;
    /* screen, so the ring ADDS light instead of painting over the face.
       SHODAN's bright portrait swamps it — the hub reads as sitting
       BEHIND her head — while the dark board around it still carries the
       ring, and the hover glow burns through her face rather than
       covering it. Every theme here is dark, which is what screen wants. */
    mix-blend-mode: screen;
    border: 0; }
  /* The resting ring is a THEME CALL, not a global one. The face is a
     single opaque canvas, so a DOM ring can never sit between a portrait
     and the board behind it — it is drawn over the art or not at all. On
     JARVIS that is free, the ring lands inside the board's own circular
     chip and merges with it. On SHODAN there is no ring to merge with and
     it just draws a hoop across her face, so that theme sets
     --av-hub-rest: 0 and the hub stays a bare hit target until hovered.
     Hover ALWAYS shows, on every theme. */
  #av-hub::before, #av-hub::after { content: ""; position: absolute;
    border-radius: 50%; transition: opacity .25s, border-color .25s, box-shadow .25s; }
  #av-hub::before { inset: 0; opacity: var(--av-hub-rest, 1);
    border: 1.5px solid color-mix(in srgb, var(--a) 48%, transparent);
    box-shadow: 0 0 14px color-mix(in srgb, var(--a) 14%, transparent); }
  #av-hub::after { inset: 9%; opacity: calc(var(--av-hub-rest, 1) * .8);
    border: 1px dashed color-mix(in srgb, var(--a) 34%, transparent);
    animation: av-spin 26s linear infinite; }
  #av-hub:hover::before, #av-hub.on::before { opacity: 1;
    border-color: color-mix(in srgb, var(--a) 70%, transparent);
    box-shadow: 0 0 26px var(--av-glow, rgba(61,220,132,.4)); }
  #av-hub:hover::after, #av-hub.on::after { opacity: 1; }
  @keyframes av-spin { to { transform: rotate(360deg); } }

  /* The board face already flags its flythrough on the body (its own .hud
     hides the same way), so the hub, the orbs and any open panel clear the
     shot for free — and come back when the camera lands. */
  body.cine #av-hub, body.cine #av-orbs,
  body.cine #av-panel, body.cine #av-reader {
    opacity: 0; pointer-events: none; transition: opacity .5s; }

  #av-orbs { position: fixed; inset: 0; z-index: 60; pointer-events: none; }
  .av-orb { position: absolute; width: 136px; height: 136px; border-radius: 50%;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 5px; text-align: center; cursor: pointer; pointer-events: auto;
    background: var(--av-bubble, rgba(4,16,10,.62)); backdrop-filter: blur(10px);
    border: 1px solid color-mix(in srgb, var(--a) 45%, transparent);
    box-shadow: 0 0 30px color-mix(in srgb, var(--a) 18%, transparent);
    opacity: 0; transform: translate(-50%, -50%) scale(.4);
    transition: transform .38s cubic-bezier(.2,.9,.3,1.3), opacity .3s,
                border-color .2s, box-shadow .2s; }
  .av-orb.up { opacity: 1; transform: translate(-50%, -50%) scale(1); }
  .av-orb:hover { border-color: var(--a);
    box-shadow: 0 0 40px color-mix(in srgb, var(--a) 40%, transparent); }
  .av-orb b { font-size: 14px; letter-spacing: .18em; font-weight: normal; color: var(--a); }
  .av-orb span { font-size: 10.5px; letter-spacing: .05em; color: var(--dim); }

  #av-panel, #av-reader { position: fixed; left: 50%; top: 50%; z-index: 61;
    transform: translate(-50%, -50%); display: none; flex-direction: column;
    border-radius: var(--r); overflow: hidden;
    background: var(--av-bubble, rgba(4,16,10,.72)); backdrop-filter: blur(18px);
    border: 1px solid color-mix(in srgb, var(--a) 35%, transparent);
    box-shadow: 0 0 60px rgba(0,0,0,.6), 0 0 40px color-mix(in srgb, var(--a) 14%, transparent); }
  #av-panel.up, #av-reader.up { display: flex; }
  #av-panel { width: min(880px, 88vw); height: min(620px, 78vh); }
  #av-reader { width: min(760px, 88vw); max-height: 84vh; z-index: 62; }

  .av-bar { display: flex; align-items: center; gap: 10px; flex: none;
    padding: 13px 18px; border-bottom: 1px solid var(--line);
    font-size: 11px; letter-spacing: .18em; }
  .av-bar .sp { flex: 1; }
  .av-bar button { font: inherit; letter-spacing: inherit; cursor: pointer;
    padding: 4px 10px; border-radius: var(--r); color: var(--dim);
    background: transparent; border: 1px solid var(--line);
    transition: color .2s, border-color .2s; }
  .av-bar button:hover { color: var(--ink); }
  .av-bar button.on { color: var(--a); border-color: var(--a); }
  .av-crumb { color: var(--a); cursor: pointer; }
  .av-crumb:last-child { color: var(--ink); cursor: default; }
  .av-crumb:not(:last-child)::after { content: " / "; color: var(--dim); }
  .av-name { color: var(--ink); }

  #av-items { flex: 1; overflow: auto; padding: 14px; }
  #av-items.list { display: flex; flex-direction: column; gap: 2px; }
  #av-items.icons { display: grid; align-content: start; gap: 10px;
    grid-template-columns: repeat(auto-fill, minmax(124px, 1fr)); }
  .av-item { display: flex; align-items: center; gap: 12px; padding: 9px 12px;
    cursor: pointer; border: 1px solid transparent; border-radius: var(--r); }
  .av-item:hover { border-color: color-mix(in srgb, var(--a) 35%, transparent);
    background: color-mix(in srgb, var(--a) 9%, transparent); }
  #av-items.icons .av-item { flex-direction: column; gap: 11px; padding: 18px 8px;
    text-align: center; }
  .av-item svg { width: 19px; height: 19px; flex: none; color: var(--a); }
  #av-items.icons .av-item svg { width: 46px; height: 46px; }
  .av-item .t { font-size: 12px; line-height: 1.35; color: var(--ink); word-break: break-word; }
  .av-item .m { margin-left: auto; font-size: 10px; color: var(--dim); }
  #av-items.icons .av-item .m { display: none; }
  #av-items .empty { padding: 20px 12px; font-size: 11px; letter-spacing: .18em;
    color: var(--dim); }

  /* The reader. Accent carries the STRUCTURE — headings, rules, markers,
     code, links. Prose stays ink; theming every glyph makes it unreadable. */
  #av-md { padding: 28px 38px 38px; overflow: auto; color: var(--ink);
    font-family: var(--av-body, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif);
    font-size: 14.5px; line-height: 1.72; }
  #av-md h1, #av-md h2, #av-md h3 { font-family: var(--av-display, "SF Mono", Menlo, monospace);
    font-weight: normal; letter-spacing: .16em; }
  #av-md h1 { font-size: 19px; color: var(--a); margin: 0 0 18px; }
  #av-md h2 { font-size: 14px; color: var(--ink); margin: 30px 0 12px; padding-bottom: 7px;
    border-bottom: 1px solid color-mix(in srgb, var(--a) 28%, transparent); }
  #av-md h3 { font-size: 12px; color: var(--av-accent-hot, #8fc4a8); margin: 22px 0 8px; }
  #av-md p { margin: 0 0 13px; }
  #av-md ul, #av-md ol { margin: 0 0 14px; padding-left: 22px; }
  #av-md li { margin-bottom: 5px; }
  #av-md li::marker { color: var(--a); }
  #av-md strong { color: #fff; font-weight: 600; }
  #av-md a { color: var(--a); text-decoration: none;
    border-bottom: 1px solid color-mix(in srgb, var(--a) 45%, transparent); }
  #av-md code { font-family: var(--av-display, "SF Mono", Menlo, monospace);
    font-size: 12.5px; color: var(--a); background: rgba(255,255,255,.06);
    padding: 2px 6px; border-radius: 3px; }
  #av-md pre { margin: 0 0 16px; padding: 14px 16px; overflow-x: auto; border-radius: var(--r);
    background: rgba(255,255,255,.045);
    border-left: 2px solid color-mix(in srgb, var(--a) 55%, transparent); }
  #av-md pre code { padding: 0; background: none; color: var(--ink); line-height: 1.6; }
  #av-md blockquote { margin: 0 0 14px; padding: 2px 0 2px 16px; color: var(--dim);
    border-left: 2px solid color-mix(in srgb, var(--a) 45%, transparent); }
  #av-md hr { margin: 26px 0; border: 0;
    border-top: 1px solid color-mix(in srgb, var(--a) 22%, transparent); }
  `;

  const ICON = {
    dir: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4l2 2.5h7A1.5 1.5 0 0 1 19 9v8.5A1.5 1.5 0 0 1 17.5 19h-13A1.5 1.5 0 0 1 3 17.5Z"/></svg>',
    md: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M6 3h8l4 4v14H6Z"/><path d="M14 3v4h4"/><path d="M9.5 12h5M9.5 15h5M9.5 18h3.5"/></svg>',
  };

  /* A fuller markdown pass than glass.js's mdSubset: that one feeds tiny
     cards (bold, code, lists) and this one is a document reader, so it
     needs headings, links, quotes and rules. Swap in a real parser if you
     ever need tables or footnotes. */
  function md(src) {
    const fences = [];
    let s = String(src).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/```\w*\n([\s\S]*?)```/g, (m, c) =>
        "\x01" + (fences.push("<pre><code>" + c.replace(/\n$/, "") +
                              "</code></pre>") - 1) + "\x01")
      .replace(/^### (.*)$/gm, "<h3>$1</h3>")
      .replace(/^## (.*)$/gm, "<h2>$1</h2>")
      .replace(/^# (.*)$/gm, "<h1>$1</h1>")
      .replace(/^&gt;\s?(.*)$/gm, "<blockquote>$1</blockquote>")
      .replace(/^(?:---|\*\*\*)\s*$/gm, "<hr>")
      .replace(/^\s*(?:[-*]|\d+\.)\s+(.*)$/gm, "<li>$1</li>")
      .replace(/(?:^<li>.*<\/li>\n?)+/gm, m => "<ul>" + m.trim() + "</ul>\n")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*\n]+)\*/g, "<em>$1</em>")
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2">$1</a>');
    s = s.split(/\n{2,}/).filter(b => b.trim())
      .map(b => /^[<\x01]/.test(b.trim()) ? b
                : "<p>" + b.trim().replace(/\n/g, "<br>") + "</p>")
      .join("\n");
    return s.replace(/\x01(\d+)\x01/g, (m, i) => fences[i]);
  }

  function counts(t) {
    let dirs = 0, notes = 0;
    (function walk(d) {
      dirs += d.dirs.length; notes += d.notes.length; d.dirs.forEach(walk);
    })(t);
    return { dirs, notes };
  }

  /* ---------------------------------------------------------------- DOM */
  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);

  const hub = document.createElement("button");
  hub.id = "av-hub";
  hub.setAttribute("aria-label", "Folders");
  const orbLayer = document.createElement("div");
  orbLayer.id = "av-orbs";

  const panel = document.createElement("div");
  panel.id = "av-panel";
  panel.innerHTML =
    '<div class="av-bar"><span id="av-crumbs"></span><span class="sp"></span>' +
    '<button id="av-list" class="on">LIST</button>' +
    '<button id="av-icons">ICONS</button>' +
    '<button id="av-close">CLOSE</button></div>' +
    '<div id="av-items" class="list"></div>';

  const reader = document.createElement("div");
  reader.id = "av-reader";
  reader.innerHTML =
    '<div class="av-bar"><span class="av-name" id="av-rname"></span>' +
    '<span class="sp"></span><button id="av-rclose">CLOSE</button></div>' +
    '<div id="av-md"></div>';

  document.body.append(hub, orbLayer, panel, reader);
  const $ = id => document.getElementById(id);

  /* --------------------------------------------------------------- bloom */
  function bloom() {
    const up = orbLayer.childElementCount > 0;
    orbLayer.innerHTML = "";
    hub.classList.toggle("on", !up);
    if (up) return;
    const n = ORBS.length;
    const R = Math.min(window.innerWidth, window.innerHeight) * 0.31;
    ORBS.forEach((o, i) => {
      // a fan centred on straight-up, 90° apart. Deliberately NOT an even
      // ring: the chat crawl lives at bottom centre, and an even ring parks
      // an orb on top of it the moment there are two folders.
      const ang = (-90 + (i - (n - 1) / 2) * 90) * Math.PI / 180;
      const el = document.createElement("div");
      el.className = "av-orb";
      el.style.left = `calc(50% + ${(Math.cos(ang) * R).toFixed(1)}px)`;
      el.style.top = `calc(50% + ${(Math.sin(ang) * R).toFixed(1)}px)`;
      const c = TREES[i] ? counts(TREES[i]) : null;
      el.innerHTML = `<b>${esc(o.title)}</b><span>${
        c ? `${c.dirs} folders · ${c.notes} notes` : "…"}</span>`;
      el.onclick = () => openOrb(i);
      orbLayer.appendChild(el);
      requestAnimationFrame(() => setTimeout(() => el.classList.add("up"), i * 70));
    });
  }

  function esc(t) {
    return String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  async function tree(i) {
    if (TREES[i]) return TREES[i];
    try {
      const r = await fetch(`/tree?orb=${i}`, { cache: "no-store" });
      if (r.ok) TREES[i] = await r.json();
    } catch (e) { /* offline: the orb just stays empty */ }
    return TREES[i];
  }

  /* ------------------------------------------------------------ explorer */
  async function openOrb(i) {
    await tree(i);
    path = [i];
    draw();
    panel.classList.add("up");
  }

  // walk the breadcrumb down the tree: path[0] is the orb, the rest are
  // folder names as they were clicked
  function at() {
    let d = TREES[path[0]];
    for (const name of path.slice(1)) {
      d = d && d.dirs.find(x => x.name === name);
    }
    return d;
  }

  function draw() {
    const d = at();
    if (!d) return;
    $("av-crumbs").innerHTML = [ORBS[path[0]].title].concat(path.slice(1))
      .map((p, i) => `<span class="av-crumb" data-i="${i}">${esc(p)}</span>`).join("");
    const rows = d.dirs.map(sub => {
      const c = counts(sub);
      return `<div class="av-item" data-dir="${esc(sub.name)}">${ICON.dir}` +
             `<span class="t">${esc(sub.name)}</span>` +
             `<span class="m">${c.notes} notes</span></div>`;
    }).concat(d.notes.map(n =>
      `<div class="av-item" data-file="${esc(n.file)}">${ICON.md}` +
      `<span class="t">${esc(n.title)}</span>` +
      `<span class="m">${n.size ? (n.size / 1024).toFixed(1) + " KB" : ""}</span></div>`));
    $("av-items").innerHTML = rows.join("") || '<div class="empty">EMPTY</div>';
  }

  // one click (= one pinch) opens: a folder walks in, a note reads
  $("av-items").onclick = async e => {
    const el = e.target.closest(".av-item");
    if (!el) return;
    if (el.dataset.dir) { path.push(el.dataset.dir); draw(); return; }
    const f = el.dataset.file;
    try {
      const r = await fetch(`/note?f=${encodeURIComponent(f)}`, { cache: "no-store" });
      if (!r.ok) return;
      $("av-rname").textContent = f.split("/").pop();
      $("av-md").innerHTML = md(await r.text());
      $("av-md").scrollTop = 0;
      reader.classList.add("up");
    } catch (err) { /* offline */ }
  };
  $("av-crumbs").onclick = e => {
    const c = e.target.closest(".av-crumb");
    if (c) { path = path.slice(0, +c.dataset.i + 1); draw(); }
  };
  const view = icons => {
    $("av-items").classList.toggle("icons", icons);
    $("av-items").classList.toggle("list", !icons);
    $("av-icons").classList.toggle("on", icons);
    $("av-list").classList.toggle("on", !icons);
  };
  $("av-list").onclick = () => view(false);
  $("av-icons").onclick = () => view(true);
  $("av-close").onclick = () => panel.classList.remove("up");
  $("av-rclose").onclick = () => reader.classList.remove("up");
  hub.onclick = bloom;

  // esc and click-outside, innermost first (what <dialog> would have given us)
  addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    if (reader.classList.contains("up")) reader.classList.remove("up");
    else if (panel.classList.contains("up")) panel.classList.remove("up");
    else if (orbLayer.childElementCount) bloom();
  });
  addEventListener("click", e => {
    if (reader.classList.contains("up") && !reader.contains(e.target)) {
      reader.classList.remove("up"); return;
    }
    if (panel.classList.contains("up") && !panel.contains(e.target) &&
        !orbLayer.contains(e.target)) panel.classList.remove("up");
  }, true);

  /* ------------------------------------------------------------- config */
  fetch("/config", { cache: "no-store" }).then(r => r.json()).then(c => {
    ORBS = (c.orbs || []).filter(o => o.kind !== "media");
    // no folders configured, no hub: nothing to bloom, so nothing to show
    if (!ORBS.length) { hub.remove(); orbLayer.remove(); return; }
    hub.style.display = "block";
    // counts come from the trees, so warm them once — cheap, and the orbs
    // read true the first time they bloom
    ORBS.forEach((o, i) => tree(i));
  }).catch(() => { hub.remove(); orbLayer.remove(); });
})();
