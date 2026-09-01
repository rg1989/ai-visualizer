/* shodan (System Shock) -- cold pure green on near-black, bone-ivory
 * ink like her face, sickly yellow warns, cable-purple dims. Keeps the
 * default VT323 terminal font: Citadel Station ran on it. shodan.jpg is
 * her classic in-game portrait; the board face screens it into the
 * circuitry (faceImg below).
 *
 * A theme folder: see themes/README.md for the contract. Copy this
 * folder, rename it, and it is a new theme -- nothing else to edit.
 */
(() => {
"use strict";
const HERE = new URL(".", document.currentScript.src).href;

/* pixel art on a 12-grid -- her wide glowing eyes and the cable-hair
   fanning out, all crispEdges like the game sprites. */
const AGENT =
  '<svg viewBox="-1 -1 14 14" shape-rendering="crispEdges" ' +
  'fill="currentColor">' +
  '<g opacity=".42"><rect x="1" y="0" width="1" height="2"/>' +
  '<rect x="5" y="0" width="2" height="1"/>' +
  '<rect x="10" y="0" width="1" height="2"/>' +
  '<rect x="3" y="1" width="1" height="1"/>' +
  '<rect x="8" y="1" width="1" height="1"/>' +
  '<rect x="0" y="4" width="2" height="1"/>' +
  '<rect x="10" y="4" width="2" height="1"/>' +
  '<rect x="0" y="9" width="2" height="1"/>' +
  '<rect x="10" y="9" width="2" height="1"/></g>' +
  '<g opacity=".55"><rect x="4" y="2" width="4" height="1"/>' +
  '<rect x="3" y="3" width="6" height="5"/>' +
  '<rect x="4" y="8" width="4" height="1"/>' +
  '<rect x="5" y="9" width="2" height="1"/></g>' +
  '<rect x="3" y="4" width="2" height="1"/>' +
  '<rect x="7" y="4" width="2" height="1"/>' +
  '<rect x="5" y="7" width="2" height="1" opacity=".85"/></svg>';
const PERSON =
  '<svg viewBox="-1 -1 14 14" shape-rendering="crispEdges" ' +
  'fill="currentColor"><rect x="4" y="1" width="4" height="4"/>' +
  '<g opacity=".55"><rect x="3" y="6" width="6" height="1"/>' +
  '<rect x="2" y="7" width="8" height="4"/></g></svg>';

AVTheme.add("shodan", {
  label: "SHODAN",
  face: "board",
  css: {
    "--av-bg2": "#07070d",
    "--av-ink": "#dcdcc8",
    "--av-dim": "#6b6a85",
    "--av-dim-bright": "#8886a8",
    "--av-accent": "#21e846",
    "--av-accent-hot": "#baffc8",
    "--av-warn": "#d9cf6e",
    "--av-agent": "#d9cf6e",
    "--av-glow": "rgba(33,232,70,.45)",
    "--av-card": "#101018",
    "--av-card-line": "#26263a",
    "--av-bubble": "rgba(10,10,18,.62)",
    "--av-orb0": "#eaffe8",
    "--av-orb1": "#21e846",
    "--av-orb2": "#141024",
    /* Citadel ran on CRT terminals: corners go sharp, avatars become
       pixel cells — same design language as the VT323 type. */
    "--av-radius": "2px",
    "--av-avatar-radius": "4px",
    /* The folder hub draws no resting ring here. Her portrait is the
       centre of this face and a hoop across it reads as damage; on the
       circular boards the same ring merges with the chip art. It still
       lights up under a hand. */
    "--av-hub-rest": "0",
    "--glass-bg": "rgba(7,7,13,.85)",
    "--glass-line": "#21e846",
    "--glass-line-dim": "rgba(33,232,70,.28)",
    "--glass-text": "#dcdcc8",
    "--glass-text-dim": "#6b6a85",
    "--glass-accent": "#21e846",
    "--glass-radius": "3px",
    "--glass-radius-sm": "2px",
  },
  canvas: {
    name: "SHODAN",   // the glass introduces itself as her
    bg2: "#07070d",
    /* board: default green 146° → SHODAN's colder 132°; ambers
       pre-rotated so they land on her sickly yellow. */
    boardFilter: "hue-rotate(-14deg) saturate(1.1)",
    amber: "#dbe768", amberHot: "#f6ffc4",
    /* SHODAN sees in green: the multi-color voice flashes (cyan,
       purple, pink...) become a family of greens. Only the flashes —
       her portrait keeps its own colors. */
    speakCols: ["#2ee87a", "#8dffb0", "#1fae4e",
                "#c9ffd9", "#35d68a", "#79e85a"],
    faceImg: HERE + "shodan.jpg",
    /* sampled from the lightning leaving her eyes, so the board's
       breakout traces read as continuations of it */
    faceTrace: "#3af068", faceTraceHot: "#cfffdd",
    /* rain */
    rain: [33, 232, 70], rainHead: [222, 255, 226],
    warnRgb: [217, 207, 110],
    /* radial */
    bars: [[14, 50, 26], [18, 90, 40], [24, 140, 55],
           [33, 232, 70], [130, 255, 150], [214, 255, 222]],
    constel: [[186, 255, 200], [240, 255, 240],
              [33, 232, 70], [136, 134, 184]],
    ringA: "#1fae44", ringB: "#146c2e", ringC: "#35c95e",
    nebula: [[8, 40, 18], [10, 24, 40], [6, 30, 14],
             [14, 52, 24], [16, 12, 36], [8, 36, 20]],
    accentHot: "#baffc8", accentSoft: "#58e878", dimSoft: "#6b6a85",
    orbHue: .36, orbHueSpread: .06,
    greenRgb: [33, 232, 70], cyanRgb: [130, 255, 150],
    /* chat crawl */
    agent: "#d9cf6e", unknown: "#8a89a0",
    people: ["#58e878", "#baffc8", "#d9cf6e", "#dcdcc8", "#8886b8"],
    chatAgent: AGENT, chatPerson: PERSON,
  },
});
})();
