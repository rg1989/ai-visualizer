/* prompt.js — the typed turn.
 *
 * P opens a box, you type, Enter sends. The line goes to POST /say,
 * the server drops it beside the bus as .voice_typed, and the voice
 * line reads it into the SAME queue its keyboard feeds — so a message
 * sent from the browser is a first-class turn, answered out loud like
 * anything you said out of your mouth.
 *
 * Why it exists: the face is the whole UI on a wall display, and some
 * things you do not want to say out loud (a paste, a path, a name the
 * transcriber keeps mangling). The mouth stays optional, not required.
 *
 * Keys: P opens, Esc closes, Enter sends, a click on the backdrop
 * closes. While it is open it OWNS the keyboard (capture + stop), so
 * F does not go fullscreen and Esc does not fire core.js's panic stop
 * behind it. It never opens over the settings screen: that screen is
 * modal, blocks the boot, and has its own keys.
 *
 * Chrome is tokens only (--av-*, --glass-*), so it reskins with the
 * theme like every other pane.
 */
"use strict";
(() => {
  // AV is a top-level const in core.js: a global lexical binding, NOT
  // a window property — typeof is the only safe existence test.
  if (typeof AV === "undefined") return;
  const ROOT = new URL(".", document.currentScript.src);

  let pane = null, input = null, note = null, sending = false;

  const FONT = "var(--av-display,'VT323'),monospace";
  const LINE = "color-mix(in srgb, var(--av-accent,#3ddc84) 38%, transparent)";

  function close() {
    if (pane) pane.remove();
    pane = input = note = null;
    sending = false;
  }

  function say(text, color) {
    if (note) {
      note.textContent = text;
      note.style.color = color || "var(--av-dim,#5a6a72)";
    }
  }

  function send() {
    const text = input.value.trim();
    if (!text || sending) return;
    sending = true;
    say("SENDING...", "var(--av-accent,#3ddc84)");
    fetch(new URL("say", ROOT).href, {
      method: "POST",
      // the server's whole cross-site write defense — a form or text
      // POST is refused, so keep this header on the call
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    })
      .then(r => r.json())
      .then(j => {
        if (j && j.ok) { close(); return; }
        // the words stay in the box: a failed send must never eat them
        sending = false;
        say("NOT SENT — IS THE VOICE LINE UP?", "var(--av-warn,#e7c368)");
      })
      .catch(() => {
        sending = false;
        say("NOT SENT — NO ANSWER FROM THE SERVER", "var(--av-warn,#e7c368)");
      });
  }

  function open() {
    pane = document.createElement("div");
    pane.id = "av-prompt";
    pane.style.cssText =
      "position:fixed;inset:0;z-index:55;display:flex;align-items:center;" +
      "justify-content:center;background:rgba(0,0,0,.72);cursor:default;" +
      "font-family:" + FONT + ";";
    // a press on the backdrop is a dismissal; a press inside is not.
    // mousedown, not click: a selection dragged out of the field and
    // released on the backdrop would otherwise close the box on you.
    pane.addEventListener("mousedown", e => { if (e.target === pane) close(); });

    const card = document.createElement("div");
    card.style.cssText =
      "width:min(560px,88vw);padding:20px 22px 16px;display:flex;" +
      "flex-direction:column;gap:12px;" +
      "background:var(--glass-bg,rgba(4,10,16,.92));" +
      "border:1px solid " + LINE + ";" +
      "border-radius:var(--av-radius,var(--glass-radius,6px));" +
      "box-shadow:0 0 40px var(--av-glow,rgba(61,220,132,.15));";

    const title = document.createElement("div");
    title.textContent = "SAY TO " + String(AV.name || "").toUpperCase();
    title.style.cssText =
      "font-size:13px;letter-spacing:.28em;color:var(--av-accent,#3ddc84);";

    const row = document.createElement("div");
    row.style.cssText = "display:flex;gap:10px;align-items:stretch;";

    input = document.createElement("input");
    input.type = "text";
    input.maxLength = 2000;          // the server's cap, said up front
    input.placeholder = "type it";
    input.style.cssText =
      "flex:1;min-width:0;font:16px/1.4 " + FONT + ";padding:10px 12px;" +
      "color:var(--av-ink,#e8f0f2);background:var(--av-card,rgba(0,0,0,.35));" +
      "border:1px solid " + LINE + ";outline:none;" +
      "border-radius:var(--av-radius,var(--glass-radius-sm,3px));";
    input.addEventListener("focus", () => {
      input.style.borderColor = "var(--av-accent,#3ddc84)";
    });

    const go = document.createElement("button");
    go.textContent = "SEND";
    go.style.cssText =
      "font:13px/1 " + FONT + ";letter-spacing:.22em;padding:0 20px;" +
      "cursor:pointer;color:var(--av-accent,#3ddc84);background:transparent;" +
      "border:1px solid " + LINE + ";" +
      "border-radius:var(--av-radius,var(--glass-radius-sm,3px));";
    go.addEventListener("click", send);

    note = document.createElement("div");
    note.style.cssText =
      "font-size:11px;letter-spacing:.2em;color:var(--av-dim,#5a6a72);";
    note.textContent = "ENTER SENDS - ESC CLOSES";

    row.appendChild(input); row.appendChild(go);
    card.appendChild(title); card.appendChild(row); card.appendChild(note);
    pane.appendChild(card);
    document.body.appendChild(pane);
    input.focus();
  }

  // One capturing listener owns both halves: capture runs before every
  // bubble-phase handler on the page, which is the only way an open box
  // can keep F, H, SPACE and Esc from firing the face behind it.
  window.addEventListener("keydown", e => {
    if (pane) {
      if (e.key === "Escape") close();
      else if (e.key === "Enter") { e.preventDefault(); send(); }
      e.stopPropagation();          // typing still reaches the field
      return;
    }
    if (e.key !== "p" && e.key !== "P") return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    // The settings screen is modal, blocks the boot, and binds its own
    // keys — P belongs to it while it is up.
    if (document.getElementById("av-mode-picker")) return;
    const t = e.target;
    if (t && (t.isContentEditable ||
              /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName || ""))) return;
    e.preventDefault();
    try { open(); } catch (err) { console.warn("[prompt] disabled:", err); close(); }
  }, true);
})();
