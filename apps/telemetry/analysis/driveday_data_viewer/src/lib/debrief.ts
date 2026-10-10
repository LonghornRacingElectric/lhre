import { Analysis, gpsStats } from "./analysis";
import { fmt, MPH } from "./format";
import { med, sd } from "./math";
import { segStats } from "./segments";
import { cvS, cvT, sU, tU, type Units } from "./units";

export interface DebriefItem {
  k: "good" | "warn" | "info";
  t: string;
  seg?: number;
  zone?: number;
  /** Lap numbers the item offers to skip. */
  skip?: number[];
}

/** What stands out in the run, from the laps that have not been skipped. */
export function buildDebrief(a: Analysis, units: Units): DebriefItem[] {
  const { s } = a;
  const { runs, segs, zones, RL } = s;
  const out: DebriefItem[] = [];
  const vr = runs.filter((l) => a.valid(l));
  if (vr.length < 3) return [{ k: "info", t: "Not enough clean laps yet for a debrief. Skipped laps do not count." }];
  const durs = vr.map((l) => l.dur);
  const mdn = med(durs);
  const b1 = Math.min(...durs);
  out.push({ k: "info", t: `Pace: best ${fmt(b1)}, median ${fmt(mdn)} over ${vr.length} clean laps, spread ${((Math.max(...durs) - b1) / 1000).toFixed(2)} s.` });
  if (vr.length >= 8) {
    const x = med(vr.slice(0, 4).map((l) => l.dur));
    const y = med(vr.slice(-4).map((l) => l.dur));
    const d = (y - x) / 1000;
    out.push({ k: d < 0 ? "good" : "warn", t: `Trend: the last 4 clean laps are ${Math.abs(d).toFixed(2)} s ${d < 0 ? "faster" : "slower"} than the first 4.` });
  }
  const off = runs.filter((l) => a.valid(l) && l.dur > mdn * 1.1);
  if (off.length) {
    out.push({ k: "warn", t: `${off.map((l) => l.name).join(", ")} ${off.length > 1 ? "are" : "is"} more than 10% slower than the median and pull the averages around.`, skip: off.map((l) => runs.indexOf(l) + 1) });
  }
  const sg = segs
    .map((g, i) => {
      const ts = vr.map((l) => segStats(l, g, RL).t);
      return { i, g, med: med(ts), min: Math.min(...ts), sd: sd(ts) };
    })
    .filter((x) => x.med > 0);
  [...sg]
    .sort((p, q) => q.med - q.min - (p.med - p.min))
    .slice(0, 2)
    .forEach((x) => out.push({ k: "warn", t: `${x.g.name} costs the most: a typical lap is ${((x.med - x.min) / 1000).toFixed(2)} s slower through it than your best, and it varies by about ${(x.sd / 1000).toFixed(2)} s.`, seg: x.i }));
  const steady = [...sg].sort((p, q) => p.sd - q.sd)[0];
  if (steady) out.push({ k: "good", t: `${steady.g.name} is your most repeatable segment, within ${(steady.sd / 1000).toFixed(2)} s lap to lap.`, seg: steady.i });
  const ss = [0, 1, 2].map((k) => sd(vr.map((l) => (l.sec[k] as number) / 1000)));
  const wk = ss.indexOf(Math.max(...ss));
  out.push({ k: "info", t: `S${wk + 1} varies the most between laps (spread ${ss[wk].toFixed(2)} s), S${ss.indexOf(Math.min(...ss)) + 1} the least (${Math.min(...ss).toFixed(2)} s).` });
  const bz = a.cons.bstd.map((v, i) => ({ v, i })).filter((x) => a.cons.brake[x.i].length >= 3);
  if (bz.length) {
    const w = [...bz].sort((p, q) => q.v - p.v)[0];
    const s2 = [...bz].sort((p, q) => p.v - q.v)[0];
    out.push({ k: w.v > 4 ? "warn" : "info", t: `Brake point: ${zones[w.i].name} moves the most, about ${w.v.toFixed(1)} m between laps.${w.v > 4 ? " Pick a marker for it." : ""}`, zone: w.i });
    if (s2.i !== w.i) out.push({ k: "good", t: `Brake point: ${zones[s2.i].name} is the most repeatable, within ${s2.v.toFixed(1)} m.`, zone: s2.i });
  }
  const az = a.cons.astd
    .map((v, i) => ({ v, i }))
    .filter((x) => segs[x.i].type === "c" && a.cons.apex[x.i].length >= 3)
    .sort((p, q) => q.v - p.v)[0];
  if (az) out.push({ k: "info", t: `Minimum speed in ${segs[az.i].name} varies by about ${cvS(units, az.v).toFixed(1)} ${sU(units)} between laps, the most of any corner.`, seg: az.i });
  const bl = vr.reduce((p, l) => (l.dur < p.dur ? l : p));
  const gains = [0, 1, 2].map((k) => (bl.sec[k] as number) - a.best[k]);
  const gk = gains.indexOf(Math.max(...gains));
  out.push({ k: "info", t: `Theoretical best is ${((bl.dur - a.theo) / 1000).toFixed(3)} s under your best lap. The biggest piece is S${gk + 1} (${(gains[gk] / 1000).toFixed(3)} s), set on Lap ${a.bestSec[gk].lap}.` });
  const wh = vr.map((l) => l.wh).filter((v): v is number => v != null);
  if (wh.length >= 5) out.push({ k: "info", t: `Energy: median ${med(wh).toFixed(0)} Wh a lap, last 5 clean laps ${(wh.slice(-5).reduce((x, y) => x + y, 0) / 5).toFixed(0)} Wh.` });
  const hot = Math.max(...runs.map((l) => Math.max(...l.ch.cellT)));
  const hi = Math.max(...runs.map((l) => Math.max(...l.ch.invT)));
  out.push({ k: "info", t: `Temperatures: hottest cell reached ${cvT(units, hot).toFixed(0)}${tU(units)}, inverter hotspot ${cvT(units, hi).toFixed(0)}${tU(units)}.` });
  const tc = runs.reduce((x, l) => x + a.conesOf(l), 0);
  if (tc) {
    const wc = runs.filter((l) => a.conesOf(l)).sort((p, q) => a.conesOf(q) - a.conesOf(p)).slice(0, 3).map((l) => `${l.name} (${a.conesOf(l)})`).join(", ");
    out.push({ k: "warn", t: `${tc} cone${tc === 1 ? "" : "s"} hit, costing ${(tc * a.pen()).toFixed(1)} s in total. Most on ${wc}.` });
  }
  const gp = gpsStats(s);
  out.push({ k: gp.gaps ? "warn" : "info", t: `GPS: about ${gp.hz.toFixed(1)} Hz${gp.gaps ? `, with ${gp.gaps} gap${gp.gaps === 1 ? "" : "s"} longer than 1.5 s. Sector times near a gap are less certain.` : ", no dropouts."}` });
  return out;
}

/** The lap table as CSV. */
export function lapsCsv(a: Analysis, units: Units): string {
  const unit = units.spd === "kph" ? "kmh" : "mph";
  const rows: (string | number)[][] = [["lap", "real_s", "s1_s", "s2_s", "s3_s", "cones", "adjusted_s", "flag", "skipped", "energy_Wh", `avg_speed_${unit}`, `max_speed_${unit}`]];
  a.s.runs.forEach((l, i) => {
    const n = i + 1;
    const f = a.u.flags[n] || {};
    rows.push([
      n, (l.dur / 1000).toFixed(3), ...[0, 1, 2].map((k) => ((l.sec[k] as number) / 1000).toFixed(3)),
      a.conesOf(l), (a.adj(l) / 1000).toFixed(3), f.f || "", f.x ? 1 : 0,
      l.wh != null ? l.wh.toFixed(1) : "", cvS(units, l.vavg * MPH).toFixed(1), cvS(units, l.vmax * MPH).toFixed(1),
    ]);
  });
  return rows.map((r) => r.join(",")).join("\n");
}

/** A few lines to paste into a chat or an email. */
export function summaryText(a: Analysis, units: Units): string {
  const vr = a.s.runs.filter((l) => a.valid(l));
  const mdn = med(vr.map((l) => l.dur));
  const cones = a.s.runs.reduce((x, l) => x + a.conesOf(l), 0);
  const adjBest = Math.min(...a.s.runs.map((l) => a.adj(l)));
  return (
    `${a.s.D.meta.name}${a.s.D.meta.driver ? " · " + a.s.D.meta.driver : ""}\n` +
    `${a.s.runs.length} timed laps, best ${fmt(a.bestLap)}, median ${fmt(mdn)}, theoretical best ${fmt(a.theo)}.\n` +
    `Cones: ${cones} (${(cones * a.pen()).toFixed(1)} s). Best adjusted ${fmt(adjBest)}.\n` +
    buildDebrief(a, units).map((x) => "- " + x.t).join("\n")
  );
}
