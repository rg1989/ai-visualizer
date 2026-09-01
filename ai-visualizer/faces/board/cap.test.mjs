/* node faces/board/cap.test.mjs — checks the ?cap= frame gate in index.html.
   A vsync-gated cap can only land on 60/n, so cap=24 legitimately yields 20;
   the contract is "never above the cap, and as close under it as vsync allows". */
function run(CAP, hz = 60, secs = 4) {
  const minDt = CAP > 0 ? 1000 / CAP - 2 : 0;   // must match index.html
  let last = 0, drawn = 0;
  for (let i = 1; i <= hz * secs; i++) {
    const ts = i * (1000 / hz);
    const dt = Math.min(50, ts - last);
    if (dt < minDt) continue;
    last = ts; drawn++;
  }
  return drawn / secs;
}
const best = (cap, hz) => {            // largest hz/n that fits under the cap
  for (let n = 1; n <= hz; n++) if (hz / n <= cap) return hz / n;
  return 0;
};
for (const cap of [0, 20, 24, 30, 45, 60]) {
  const got = run(cap), want = cap ? best(cap, 60) : 60;
  console.assert(got === want, `cap=${cap}: got ${got}, want ${want}`);
  console.assert(!cap || got <= cap, `cap=${cap}: ${got} exceeds the cap`);
  console.log(`cap=${cap || "off"} -> ${got} fps`);
}
console.assert(run(60, 60) === 60, "an uncapped-equivalent cap must not halve a 60Hz screen");
console.assert(run(30, 120) === 30, "cap must hold on a 120Hz screen too");

/* how index.html resolves the cap: ?cap= wins, then localStorage av_fps,
   then 30. "0" is a real value meaning uncapped, so ?? not || . */
const pick = (q, ls) => +(q ?? ls ?? 30);
console.assert(pick(null, null) === 30, "default must be 30");
console.assert(pick("60", null) === 60, "?cap= must win");
console.assert(pick(null, "24") === 24, "av_fps must apply when no ?cap=");
console.assert(pick("60", "24") === 60, "?cap= must beat av_fps");
console.assert(pick("0", null) === 0, "?cap=0 must mean uncapped, not default");
console.assert(pick(null, "0") === 0, "av_fps=0 must mean uncapped");
console.assert(run(pick("0", null)) === 60, "uncapped must actually run free");
console.log("ok");
