#!/usr/bin/env node
/* Turn a session export of one slow lap driven down the middle of the track into a stored base layout.

     node scripts/build-layout.mjs "<path>/Layout_session.json" src/lib/layouts/pickle-lot.json "Pickle Lot"

   It keeps the GPS line only (latitude, longitude), lightly smoothed and spaced about 1 m apart, plus which way round it was
   driven. The app works out the real centre line and the track edges from it when it loads (geometry.ts, trackEdges), so the
   width and the GPS offset can be changed there without running this again. */
import { readFileSync, writeFileSync, mkdirSync } from "fs";
import { dirname } from "path";

const [src, out, name = "Track layout"] = process.argv.slice(2);
if (!src || !out) {
  console.error('usage: node scripts/build-layout.mjs <session.json> <out.json> ["Name"]');
  process.exit(1);
}
const J = JSON.parse(readFileSync(src, "utf8"));
const ok = (s) => s && Number.isFinite(s.lat) && Number.isFinite(s.lon) && (s.lat !== 0 || s.lon !== 0);
const byT = new Map();
for (const s of [...(J.laps || []).flatMap((l) => l.samples || []), ...((J.currentLap || {}).lapSamples || []), ...(J.sampleTail || [])]) {
  if (s && Number.isFinite(s.t)) byT.set(s.t, { ...(byT.get(s.t) || {}), ...s });
}
const st = [...byT.values()].sort((a, b) => a.t - b.t).filter(ok);
/* the longest timed lap is the layout drive */
const laps = (J.laps || []).filter((l) => l.endMs > l.startMs).sort((a, b) => b.endMs - b.startMs - (a.endMs - a.startMs));
if (!laps.length) throw new Error("no timed lap in " + src);
const L = laps[0];
const fx = st.filter((s) => s.t >= L.startMs && s.t <= L.endMs);
if (fx.length < 30) throw new Error("only " + fx.length + " GPS fixes in the layout lap");

const lat0 = fx.reduce((a, s) => a + s.lat, 0) / fx.length;
const lon0 = fx.reduce((a, s) => a + s.lon, 0) / fx.length;
const KY = (6378137 * Math.PI) / 180;
const KX = KY * Math.cos((lat0 * Math.PI) / 180);
let P = fx.map((s) => [(s.lon - lon0) * KX, (s.lat - lat0) * KY]);
/* drop repeated fixes (car stopped) */
P = P.filter((p, i) => i === 0 || Math.hypot(p[0] - P[i - 1][0], p[1] - P[i - 1][1]) > 0.05);
/* close the loop cleanly: the drive rarely ends exactly where it began. Cut the tail at the fix nearest the start (so it does
   not run past it), then spread the leftover gap over the last ~15 m so the join has no step or zig-zag. */
{
  const from = Math.floor(P.length * 0.85);
  let cut = P.length - 1;
  let bd = Infinity;
  for (let i = from; i < P.length; i++) {
    const d = Math.hypot(P[i][0] - P[0][0], P[i][1] - P[0][1]);
    if (d < bd) {
      bd = d;
      cut = i;
    }
  }
  P = P.slice(0, cut + 1);
  if (Math.hypot(P[cut][0] - P[0][0], P[cut][1] - P[0][1]) < 0.5) P.pop();
  const last = P.length - 1;
  const gx = P[0][0] - P[last][0];
  const gy = P[0][1] - P[last][1];
  let run = 0;
  let k = last;
  while (k > 0 && run < 15) {
    run += Math.hypot(P[k][0] - P[k - 1][0], P[k][1] - P[k - 1][1]);
    k--;
  }
  /* weights rise to just under 1 at the last fix, which then sits one spacing short of the start */
  for (let i = k + 1; i <= last; i++) {
    const w = (i - k) / (last - k + 1);
    P[i] = [P[i][0] + gx * w, P[i][1] + gy * w];
  }
}
const n = P.length;
/* light closed-loop smoothing: two passes of a 1-2-1 filter */
for (let pass = 0; pass < 2; pass++) {
  P = P.map((p, i) => {
    const a = P[(i - 1 + n) % n];
    const b = P[(i + 1) % n];
    return [0.25 * a[0] + 0.5 * p[0] + 0.25 * b[0], 0.25 * a[1] + 0.5 * p[1] + 0.25 * b[1]];
  });
}
/* resample the closed loop about every 1 m, starting where the drive started */
const loop = P.concat([P[0]]);
const cum = [0];
for (let i = 1; i < loop.length; i++) cum.push(cum[i - 1] + Math.hypot(loop[i][0] - loop[i - 1][0], loop[i][1] - loop[i - 1][1]));
const RL = cum[cum.length - 1];
const N = Math.round(RL);
const R = [];
let j = 0;
for (let k = 0; k < N; k++) {
  const s = (RL * k) / N;
  while (j < cum.length - 2 && cum[j + 1] < s) j++;
  const f = (s - cum[j]) / (cum[j + 1] - cum[j] || 1);
  R.push([loop[j][0] + f * (loop[j + 1][0] - loop[j][0]), loop[j][1] + f * (loop[j + 1][1] - loop[j][1])]);
}
let area = 0;
for (let i = 0; i < R.length; i++) {
  const a = R[i];
  const b = R[(i + 1) % R.length];
  area += a[0] * b[1] - b[0] * a[1];
}
const r7 = (v) => Math.round(v * 1e7) / 1e7;
const info = J.sessionInfo || {};
const layout = {
  name,
  venue: info.venue || "",
  from: src.split("/").pop(),
  recorded: new Date(L.startMs).toISOString(),
  driver: info.driver || "",
  /** which way round the layout drive went; the app makes the other direction as its own layout */
  direction: area >= 0 ? "ccw" : "cw",
  lengthM: Math.round(RL * 10) / 10,
  /** the GPS line as driven, [lat, lon], a closed loop (the first point is not repeated at the end) */
  line: R.map((p) => [r7(lat0 + p[1] / KY), r7(lon0 + p[0] / KX)]),
};
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, JSON.stringify(layout, null, 0) + "\n");
console.log(`${out}: ${layout.line.length} points, ${layout.lengthM} m, driven ${layout.direction}, from ${fx.length} fixes`);
