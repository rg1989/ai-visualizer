/* jarvis -- the shipped look: the legacy green (146 deg) slid to sky
 * blue (~197 deg); neutrals cool from green-gray to blue-gray, warns
 * stay amber.
 *
 * A theme folder: see themes/README.md for the contract. Copy this
 * folder, rename it, and it is a new theme -- nothing else to edit.
 */
(() => {
"use strict";
const HERE = new URL(".", document.currentScript.src).href;

/* the arc-reactor core -- segmented coil ring, hairline inner ring,
   lit hub with three spokes. Strokes/fills currentColor, so chat.js's
   per-speaker colors tint it for free. */
const AGENT =
  '<svg viewBox="0 0 30 30" fill="none" stroke="currentColor">' +
  '<circle cx="15" cy="15" r="11.5" stroke-width="1.3" ' +
  'stroke-dasharray="4.2 2.1"/>' +
  '<circle cx="15" cy="15" r="7.2" stroke-width="1" opacity=".8"/>' +
  '<circle cx="15" cy="15" r="2.6" fill="currentColor" stroke="none"/>' +
  '<g stroke-width="1.1"><line x1="15" y1="7.8" x2="15" y2="11"/>' +
  '<line x1="8.8" y1="18.6" x2="11.5" y2="17"/>' +
  '<line x1="21.2" y1="18.6" x2="18.5" y2="17"/></g></svg>';
const PERSON =
  '<svg viewBox="0 0 30 30" fill="none" stroke="currentColor" ' +
  'stroke-width="1.3"><circle cx="15" cy="10.5" r="4.2"/>' +
  '<path d="M6.5 24.5a8.5 7.5 0 0 1 17 0"/></svg>';

AVTheme.add("jarvis", {
  label: "JARVIS",
  face: "board",
  /* fontshare.com Satoshi (variable, self-hosted), theme-local so
     copying this folder copies the typeface with it. */
  raw: `@font-face{font-family:"Satoshi";src:url("${HERE}Satoshi.ttf") format("truetype");font-weight:300 900;font-display:swap}`,
  css: {
    "--av-bg2": "#020508",
    "--av-ink": "#e6eef5",
    "--av-dim": "#5a6b7a",
    "--av-dim-bright": "#9db3c6",
    "--av-accent": "#3daedc",
    "--av-accent-hot": "#a8d4f0",
    "--av-glow": "rgba(61,174,220,.4)",
    "--av-card": "#040a10",
    "--av-card-line": "#14242f",
    "--av-bubble": "rgba(4,10,16,.62)",
    "--av-orb0": "#d6efff",
    "--av-orb1": "#3daedc",
    "--av-orb2": "#0d2b3d",
    "--av-display": '"Satoshi","Segoe UI",sans-serif',
    "--glass-bg": "rgba(4,9,14,.85)",
    "--glass-accent": "#3daedc",
    "--glass-text": "#dceaf2",
    "--glass-text-dim": "#7a8b9c",
    "--glass-title-font": '"Satoshi","SF Mono",Menlo,monospace',
  },
  canvas: {
    /* board: greens live in ~100 canvas literals — one hue rotation
       (green 146° → sky 197°); ambers pre-rotated to stay gold. */
    boardFilter: "hue-rotate(50deg)",
    amber: "#e76877", amberHot: "#ffaeb6",
    chatAgent: AGENT, chatPerson: PERSON,
  },
});
})();
