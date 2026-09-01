/* theme.js — the theme engine.
 *
 * Themes are FOLDERS, the same drop-in rule the faces already use: put a
 * themes/<id>/theme.js in place that calls AVTheme.add(<id>, {...}) and it
 * is in the pickers on the next reload. Nothing in this file lists them —
 * the server does (GET /themes.js, one line per folder it finds), so a
 * theme you never told anyone about still appears, and a theme you delete
 * simply stops existing. themes/README.md is the contract for authors.
 *
 * One storage key ("av_theme"), one attribute (data-theme on <html>).
 * Loaded synchronously right after core.js on every page, so face scripts
 * can read AVTheme.overrides at parse time — no async CSS race. That is
 * also why the loader at the bottom uses document.write: parser-inserted
 * scripts block the parser, so every theme is registered before the next
 * <script> in the page runs. theme.js must stay a plain <script src> —
 * no defer, no async, or the write lands after the document closed.
 *
 * Three halves per theme (see themes/README.md):
 *   css    — custom properties injected as `:root[data-theme=X]{...}`.
 *            Pages consume them as var(--av-*, <original value>), and
 *            the glass tokens (--glass-*) are overridden here too:
 *            :root[data-theme] outranks glass-theme.css's bare :root
 *            whatever the load order.
 *   canvas — JS values for the faces' canvas art, exposed as
 *            AVTheme.overrides ({} on a theme that sets none).
 *   raw    — optional plain CSS (an @font-face for a theme-local
 *            typeface, say) that cannot live inside a :root block.
 *
 * A theme change elsewhere reloads this page (storage event): faces
 * bake colors into consts and sprite atlases at load, so a reload is
 * the one honest way to reskin them.
 *
 * Without the server (file://) /themes.js does not answer and no theme
 * registers: the pages fall back to their baked-in colors, exactly as
 * they already do when theme.js is absent.
 */
"use strict";
const AVTheme = (() => {
  const KEY = "av_theme";
  /* The shipped look. Only a starting point: if this folder is gone,
     the first theme the server hands us holds the screen instead. */
  const DEFAULT = "jarvis";

  const THEMES = {};
  const root = document.documentElement;
  const style = document.createElement("style");
  (document.head || root).appendChild(style);

  let want = DEFAULT;               // what the user picked, last time
  try { want = localStorage.getItem(KEY) || DEFAULT; } catch (_) {}
  let cur = null;                   // what is actually on screen

  function apply(id) {
    cur = id;
    root.setAttribute("data-theme", id);
  }

  function add(id, def) {
    if (!def || THEMES[id]) return;
    THEMES[id] = def;
    if (def.raw) style.textContent += def.raw + "\n";
    const props = Object.entries(def.css || {});
    if (props.length)
      style.textContent += `:root[data-theme="${id}"]{` +
        props.map(([k, v]) => `${k}:${v}`).join(";") + "}\n";
    // Themes arrive one script at a time, so the screen settles in
    // order of preference as they land: the saved choice the moment it
    // registers, the shipped default while that is still missing, and
    // failing both the first theme through — a face is never left
    // unthemed because someone deleted the folder it remembered.
    if (id === want || cur === null || (id === DEFAULT && cur !== want))
      apply(id);
  }

  addEventListener("storage", e => {
    if (e.key === KEY && e.newValue && e.newValue !== cur) location.reload();
  });

  let localAt = 0; // when the USER last picked here (vs. server convergence)

  // The one server write: the server remembers the theme glass-wide, so
  // other displays and browsers — and the voice character backtalk
  // dresses to match — converge on it (fails silently on file://).
  function doCommit() {
    localAt = Date.now();
    try {
      fetch(new URL("/theme", location.origin).href, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme: cur }),
      }).catch(() => {});
    } catch (_) {}
  }

  return {
    add,
    themes: THEMES,
    get current() { return cur || want; },
    get overrides() { return (cur && THEMES[cur].canvas) || {}; },
    get localAt() { return localAt; },
    // push=false previews locally (and is how server convergence applies
    // without POSTing back — a stale /state would otherwise overwrite the
    // user's fresh choice). commit() is the preview's SAVE.
    set(id, push = true) {
      if (!THEMES[id] || id === cur) return;
      apply(id);
      try { localStorage.setItem(KEY, id); } catch (_) {}
      if (push) doCommit();
    },
    commit: doCommit,
  };
})();

/* Pull in the theme folders. Parser-blocking on purpose — see the head
   of this file. Anything that made this script run late (defer/async, an
   injected tag) closes the document first, and document.write would wipe
   the page, so say so instead of doing it. */
if (document.readyState === "loading")
  document.write('<script src="' +
    new URL("themes.js", document.currentScript.src).href + '"><\/script>');
else
  console.error("theme.js: ran too late to load themes/ — it has to be a " +
                "plain <script src>, no defer and no async.");
