import type {
  BaseChannels,
  ChKey,
  LiveLapExport,
  LiveSample,
  Pt,
  RawLap,
  RunData,
  SessionExport,
  Track,
  XY,
} from "./types";

/* Raw session export  ->  run data the viewer draws.
   Runs entirely in the browser: the export can be 15 MB, and nothing needs to leave the machine. */

export interface ConvertResult {
  D: RunData;
  track: Track;
  isNew: boolean;
  renamed: boolean;
  /** Which course of the session this is, when the session has more than one. */
  label: string;
}

const R2 = (v: number, d: number) => {
  const m = Math.pow(10, d);
  return Math.round(v * m) / m;
};
const num = (v: unknown): number => (typeof v === "number" && isFinite(v) ? v : 0);

const medianOf = (a: number[]) => {
  const q = [...a].sort((x, y) => x - y);
  const n = q.length;
  return n ? (n % 2 ? q[(n - 1) / 2] : (q[n / 2 - 1] + q[n / 2]) / 2) : 0;
};

const cumOf = (poly: XY[]) => {
  const c = [0];
  for (let i = 1; i < poly.length; i++) {
    c.push(c[i - 1] + Math.hypot(poly[i][0] - poly[i - 1][0], poly[i][1] - poly[i - 1][1]));
  }
  return c;
};

/** The part of an open path between two arc lengths, with exact end points. */
const subPath = (ref: XY[], cum: number[], a: number, b: number): { ref: XY[]; cum: number[] } => {
  const at = (s: number): XY => {
    let i = 0;
    while (i < cum.length - 2 && cum[i + 1] < s) i++;
    const f = Math.max(0, Math.min(1, (s - cum[i]) / (cum[i + 1] - cum[i] || 1)));
    return [ref[i][0] + f * (ref[i + 1][0] - ref[i][0]), ref[i][1] + f * (ref[i + 1][1] - ref[i][1])];
  };
  const out: XY[] = [at(a)];
  for (let i = 0; i < ref.length; i++) if (cum[i] > a + 1e-6 && cum[i] < b - 1e-6) out.push(ref[i]);
  out.push(at(b));
  return { ref: out, cum: cumOf(out) };
};

/** Nearest point on a polyline: [arc length, distance off the line]. */
const nearOn = (poly: XY[], cum: number[], p: XY): [number, number] => {
  let best = 1e9;
  let bs = 0;
  for (let i = 0; i < poly.length - 1; i++) {
    const a = poly[i];
    const b = poly[i + 1];
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const l2 = dx * dx + dy * dy;
    let t = l2 ? ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2 : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const d = Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
    if (d < best) {
      best = d;
      bs = cum[i] + t * Math.sqrt(l2);
    }
  }
  return [bs, best];
};

const segDist = (a: XY, b: XY, p: XY) => {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const l2 = dx * dx + dy * dy;
  let t = l2 ? ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2 : 0;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
};

interface Frame {
  lat0: number;
  lon0: number;
  KY: number;
  KX: number;
}
const frame = (lat0: number, lon0: number): Frame => {
  const KY = (6378137 * Math.PI) / 180;
  return { lat0, lon0, KY, KX: KY * Math.cos((lat0 * Math.PI) / 180) };
};
const projWith = (f: Frame) => (s: LiveSample): XY => [(s.lon - f.lon0) * f.KX, (s.lat - f.lat0) * f.KY];

/* ---------- which laps are runs, and which course each belongs to ---------- */

interface Prepared {
  o: { l: LiveLapExport; i: number };
  sm: LiveSample[];
}

interface Leg extends Prepared {
  P: XY[];
  len: number;
  start: XY;
  end: XY;
  speed: number; // average, m/s
  loop: boolean; // it ends close to where it began
}

interface Course {
  legs: Leg[];
  open: boolean;
  label: string;
}

const dist2d = (a: XY, b: XY) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const dirOf = (a: XY, b: XY): XY => {
  const d = dist2d(a, b) || 1;
  return [(b[0] - a[0]) / d, (b[1] - a[1]) / d];
};
/** +1 counter-clockwise, -1 clockwise. */
const turnSign = (P: XY[]) => {
  let a = 0;
  for (let k = 0; k < P.length - 1; k++) a += P[k][0] * P[k + 1][1] - P[k + 1][0] * P[k][1];
  return a >= 0 ? 1 : -1;
};

/**
 * A logger lap is not always a lap. On an autocross day the marks bracket a run from the start line to a finish line somewhere else,
 * and the drive back is marked too. So: short or slow legs are left out, a session whose runs end where they began is a loop,
 * and otherwise runs are grouped by where they start and finish and which way they go. A different start line, a different
 * finish line, or the opposite direction is a different course.
 */
function planCourses(prep: Prepared[], pr: (s: LiveSample) => XY): { courses: Course[]; left: Leg[] } {
  const legs: Leg[] = prep.map((x) => {
    const P = x.sm.map(pr);
    let len = 0;
    for (let k = 1; k < P.length; k++) len += dist2d(P[k], P[k - 1]);
    const gap = dist2d(P[0], P[P.length - 1]);
    const sec = Math.max(1, x.o.l.durationMs / 1000);
    return { ...x, P, len, start: P[0], end: P[P.length - 1], speed: len / sec, loop: gap < Math.max(15, 0.08 * len) };
  });
  /* what a run looks like: the upper quartile, so the short slow drives back do not drag it down */
  const upper = (v: number[]) => [...v].sort((x, y) => x - y)[Math.floor(0.75 * (v.length - 1))];
  const Lref = upper(legs.map((l) => l.len));
  const Vref = upper(legs.map((l) => l.speed));
  let runs = legs.filter((l) => l.len >= 0.6 * Lref && l.speed >= 0.4 * Vref);
  if (!runs.length) runs = legs;
  const left = legs.filter((l) => !runs.includes(l));
  const open = runs.filter((l) => l.loop).length < runs.length / 2;

  const same = (a: Leg, b: Leg) => {
    if (!open) return turnSign(a.P) === turnSign(b.P);
    const R = Math.max(25, 0.1 * Math.max(a.len, b.len));
    const da = dirOf(a.start, a.end);
    const db = dirOf(b.start, b.end);
    return dist2d(a.start, b.start) < R && dist2d(a.end, b.end) < R && da[0] * db[0] + da[1] * db[1] > 0.5;
  };
  const groups: Leg[][] = [];
  runs.forEach((l) => {
    const g = groups.find((x) => same(x[0], l));
    if (g) g.push(l);
    else groups.push([l]);
  });
  /* a course driven the other way round an earlier one says so */
  const reverseOf = (g: Leg[], k: number): number => {
    for (let j = 0; j < k; j++) {
      const a = groups[j][0];
      const b = g[0];
      if (open) {
        const R = Math.max(25, 0.1 * Math.max(a.len, b.len));
        if (dist2d(a.start, b.end) < R && dist2d(a.end, b.start) < R) return j;
      } else if (turnSign(a.P) !== turnSign(b.P)) return j;
    }
    return -1;
  };
  const courses = groups.map((legs, k) => {
    const rv = reverseOf(legs, k);
    return { legs, open, label: groups.length === 1 ? "" : "Course " + (k + 1) + (rv >= 0 ? " (reverse of " + (rv + 1) + ")" : "") };
  });
  return { courses, left };
}

/* ---------- the export, one run per course ---------- */

export function convertSession(J: SessionExport, fileName: string, tracks: Track[] = []): ConvertResult[] {
  const warn: string[] = [];
  if (!J || !Array.isArray(J.sampleTail) || !Array.isArray(J.laps)) {
    throw new Error("This does not look like a session export (no sampleTail or laps).");
  }
  const hasGps = (s: LiveSample) => !!s && Number.isFinite(s.lat) && Number.isFinite(s.lon) &&
    Math.abs(s.lat) <= 90 && Math.abs(s.lon) <= 180 && (s.lat !== 0 || s.lon !== 0);
  // The session tail is capped upstream; complete lap exports carry the older samples.
  const byTime = new Map<number, LiveSample>();
  for (const s of [...J.laps.flatMap((l) => Array.isArray(l.samples) ? l.samples : []),
    ...(Array.isArray(J.currentLap?.lapSamples) ? J.currentLap.lapSamples : []), ...J.sampleTail]) {
    if (!s || !Number.isFinite(s.t)) continue;
    const old = byTime.get(s.t);
    byTime.set(s.t, old ? {
      ...old, ...s, values: { ...old.values, ...s.values },
      lat: hasGps(s) ? s.lat : old.lat, lon: hasGps(s) ? s.lon : old.lon,
      cellTemps: s.cellTemps?.length ? s.cellTemps : old.cellTemps,
    } : s);
  }
  const st = [...byTime.values()].sort((a, b) => a.t - b.t);
  const nGps = st.reduce((n, s) => n + (hasGps(s) ? 1 : 0), 0);
  if (nGps < 30) {
    throw new Error("This session has no usable GPS fixes (" + nGps + " of " + st.length + " samples), so there is no track to draw.");
  }
  // Keep intervening telemetry at its original timestamps, with a GPS position
  // interpolated only between valid fixes. Never invent positions outside that range.
  const fixes = st.filter(hasGps);
  let fixIndex = 0;
  for (let i = 0; i < st.length; i++) {
    const s = st[i];
    if (hasGps(s) || s.t < fixes[0].t || s.t > fixes[fixes.length - 1].t) continue;
    while (fixIndex < fixes.length - 2 && fixes[fixIndex + 1].t < s.t) fixIndex++;
    const a = fixes[fixIndex], b = fixes[fixIndex + 1];
    const f = (s.t - a.t) / (b.t - a.t);
    st[i] = { ...s, lat: a.lat + f * (b.lat - a.lat), lon: a.lon + f * (b.lon - a.lon) };
  }
  const info = J.sessionInfo || {};
  const md = J.metadata || {};
  const timed = J.laps
    .map((l, i) => ({ l, i }))
    .filter((o) => typeof o.l.startMs === "number" && typeof o.l.endMs === "number" && o.l.endMs > o.l.startMs)
    .sort((a, b) => a.l.startMs - b.l.startMs);
  if (!timed.length) throw new Error("This session has no timed laps.");
  const slice = (t0: number, t1: number) => st.filter((s) => s.t >= t0 && s.t <= t1 && hasGps(s));

  const lapSamples = timed.map((o) => ({ o, sm: slice(o.l.startMs, o.l.endMs) }));
  const usable = lapSamples.filter((x) => x.sm.length >= 12);
  if (!usable.length) throw new Error("No timed lap has enough GPS fixes to build a track.");

  const allFixes = ([] as LiveSample[]).concat(...usable.map((x) => x.sm));
  const lat0All = allFixes.reduce((a, s) => a + s.lat, 0) / allFixes.length;
  const lon0All = allFixes.reduce((a, s) => a + s.lon, 0) / allFixes.length;
  const { courses, left } = planCourses(usable, projWith(frame(lat0All, lon0All)));
  if (courses.length > 1) {
    warn.push(
      courses.length + " courses in this session: " + courses.map((c) => (c.label || "Course") + " (" + c.legs.map((l) => "Lap " + (l.o.i + 1)).join(", ") + ")").join("; ") +
        ". Each is opened as its own run, because laps on different start and finish lines are not comparable.",
    );
  }
  if (left.length) {
    warn.push(left.map((l) => "Lap " + (l.o.i + 1)).join(", ") + (left.length === 1 ? " is" : " are") + " too short or slow to be a timed run (a drive back or a reposition), so " + (left.length === 1 ? "it was" : "they were") + " left out.");
  }

  const dd = new Date(timed[0].l.startMs);
  const pad = (n: number) => String(n).padStart(2, "0");
  const dateStr = dd.getFullYear() + "-" + pad(dd.getMonth() + 1) + "-" + pad(dd.getDate());
  const eventStr = (info.eventType || md.event || "Session").trim() || "Session";
  const known = tracks.slice();
  const layoutName = () => {
    let n = 1;
    known.forEach((t) => {
      if (t.date === dateStr && t.event === eventStr && t.num >= n) n = t.num + 1;
    });
    return { name: dateStr + " · " + eventStr + " · " + n, date: dateStr, event: eventStr, num: n };
  };
  const nm = info.name || J.name || (fileName || "Session").replace(/\.json$/i, "");
  const firstT = timed[0].l.startMs;
  const lastT = timed[timed.length - 1].l.endMs;

  const cellN = Math.max(0, ...st.map((s) => (Array.isArray(s.cellTemps) ? s.cellTemps.length : 0)));
  const cellIdx: number[] = [];
  if (cellN) {
    const cnt = new Array(cellN).fill(0);
    let tot = 0;
    allFixes.forEach((s) => {
      if (Array.isArray(s.cellTemps)) {
        tot++;
        for (let i = 0; i < cellN; i++) if ((s.cellTemps[i] ?? 0) > 0) cnt[i]++;
      }
    });
    for (let i = 0; i < cellN; i++) if (tot && cnt[i] >= tot * 0.5) cellIdx.push(i);
  }

  return courses.map((course, ci) => {
    const group = course.legs.map((l) => ({ o: l.o, sm: l.sm }));
    const multi = courses.length > 1;
    const cw: string[] = ci === 0 ? warn.slice() : [];
    const all = ([] as LiveSample[]).concat(...group.map((x) => x.sm));
    const probe = all.filter((_, i) => i % Math.max(1, Math.floor(all.length / 300)) === 0);
    const TOL = 6;

    /* 1) is this a track we already know? Same layout means the same line, driven the same way round, at the same size.
          - at least 90% of this run's fixes lie within 6 m of the saved line
          - at least 90% of the saved line lies within 6 m of one of this run's laps (a smaller course in the same lot does not match)
          - a typical lap is within 12% of the saved lap length, and progress runs the same direction
          Open courses are never matched: their start and finish lines are part of what they are. */
    let track: Track | null = null;
    let isNew = false;
    let renamed = false;

    const sameTrack = (t: Track): number => {
      if (t.open) return 0;
      const pr = projWith(frame(t.lat0, t.lon0));
      const near = probe.filter((s) => nearOn(t.ref, t.cum, pr(s))[1] <= TOL).length / probe.length;
      if (near < 0.9) return 0;
      const laps = group.map((x) => x.sm.map(pr));
      const stepN = Math.max(2, Math.round(t.RL / 3));
      let cov = 0;
      for (let i = 0; i < stepN; i++) {
        const sPos = (t.RL * i) / stepN;
        let seg = 0;
        while (seg < t.ref.length - 2 && t.cum[seg + 1] < sPos) seg++;
        const f = (sPos - t.cum[seg]) / (t.cum[seg + 1] - t.cum[seg] || 1);
        const a = t.ref[seg];
        const b = t.ref[seg + 1];
        const p: XY = [a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])];
        if (laps.some((P) => {
          for (let k = 1; k < P.length; k++) if (segDist(P[k - 1], P[k], p) <= TOL) return true;
          return false;
        })) cov++;
      }
      if (cov / stepN < 0.9) return 0;
      const lens = laps
        .map((P) => {
          let l = 0;
          for (let k = 1; k < P.length; k++) l += Math.hypot(P[k][0] - P[k - 1][0], P[k][1] - P[k - 1][1]);
          return l;
        })
        .filter((l) => l > t.RL * 0.5 && l < t.RL * 1.5);
      if (lens.length && Math.abs(medianOf(lens) / t.RL - 1) > 0.12) return 0;
      /* direction: median forward progress along the saved line must be positive */
      const fw = laps
        .map((P) => {
          let d = 0;
          for (let k = 1; k < P.length; k++) {
            let ds = nearOn(t.ref, t.cum, P[k])[0] - nearOn(t.ref, t.cum, P[k - 1])[0];
            ds -= t.RL * Math.round(ds / t.RL);
            d += ds;
          }
          return d;
        })
        .filter((d) => Math.abs(d) > t.RL * 0.4);
      if (fw.length && medianOf(fw) < 0) return 0;
      return near * (cov / stepN);
    };

    if (!course.open) {
      let bestScore = 0;
      for (const t of tracks) {
        const sc = sameTrack(t);
        if (sc > bestScore) {
          bestScore = sc;
          track = t;
        }
      }
      if (track && !track.num) {
        track = Object.assign({}, track, layoutName());
        renamed = true;
      }
    }

    /* 2) otherwise define it from this run */
    if (!track) {
      isNew = true;
      const lat0 = all.reduce((a, s) => a + s.lat, 0) / all.length;
      const lon0 = all.reduce((a, s) => a + s.lon, 0) / all.length;
      const f = frame(lat0, lon0);
      const pr = projWith(f);
      const cand = group.map((x) => {
        const P = x.sm.map(pr);
        let len = 0;
        for (let k = 1; k < P.length; k++) len += Math.hypot(P[k][0] - P[k - 1][0], P[k][1] - P[k - 1][1]);
        return { x, P, len, gap: Math.hypot(P[0][0] - P[P.length - 1][0], P[0][1] - P[P.length - 1][1]) };
      });
      let ref: XY[];
      let cum: number[];
      if (course.open) {
        /* a path from the start line to the finish line, taken from the run of median length */
        const lm = medianOf(cand.map((c) => c.len));
        const BP = cand.reduce((a, b) => (Math.abs(b.len - lm) < Math.abs(a.len - lm) ? b : a)).P;
        const n = BP.length;
        ref = BP.map((p, k) =>
          k === 0 || k === n - 1 ? p : ([0.25 * BP[k - 1][0] + 0.5 * p[0] + 0.25 * BP[k + 1][0], 0.25 * BP[k - 1][1] + 0.5 * p[1] + 0.25 * BP[k + 1][1]] as XY),
        );
        cum = cumOf(ref);
        /* the start line and the finish line are where the logger timed the runs: the middle of where they began and where they ended */
        const a0 = medianOf(cand.map((c) => nearOn(ref, cum, c.P[0])[0]));
        const b0 = medianOf(cand.map((c) => nearOn(ref, cum, c.P[c.P.length - 1])[0]));
        if (b0 - a0 > 30) {
          const sub = subPath(ref, cum, a0, b0);
          ref = sub.ref;
          cum = sub.cum;
        }
      } else {
        /* one clean circuit, start/finish where the laps are actually cut */
        let pool = cand.filter((c) => c.gap < 15);
        if (!pool.length) pool = cand;
        const lm = medianOf(pool.map((c) => c.len));
        const pick = pool.reduce((a, b) => (Math.abs(b.len - lm) < Math.abs(a.len - lm) ? b : a));
        const BP = pick.P;
        const n = BP.length;
        ref = BP.map((p, k) => {
          const a = BP[(k + n - 1) % n];
          const b = BP[(k + 1) % n];
          return [0.25 * a[0] + 0.5 * p[0] + 0.25 * b[0], 0.25 * a[1] + 0.5 * p[1] + 0.25 * b[1]];
        });
        ref.push(ref[0]);
        cum = cumOf(ref);
        const RL0 = cum[cum.length - 1];
        /* start/finish line: where the lap cuts land on the loop (circular median over laps of about one circuit) */
        const ss = cand.filter((c) => c.len > RL0 * 0.75 && c.len < RL0 * 1.25).map((c) => nearOn(ref, cum, c.P[0])[0]);
        if (ss.length) {
          const r0 = ss[0];
          const rel = ss.map((v) => {
            let d = v - r0;
            d -= RL0 * Math.round(d / RL0);
            return d;
          });
          const sf = (((r0 + medianOf(rel)) % RL0) + RL0) % RL0;
          let seg = 0;
          while (seg < ref.length - 2 && cum[seg + 1] < sf) seg++;
          const t = (sf - cum[seg]) / (cum[seg + 1] - cum[seg] || 1);
          const a = ref[seg];
          const b = ref[seg + 1];
          const P0: XY = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
          const body = ref.slice(0, -1);
          const rot: XY[] = [P0].concat(body.slice(seg + 1), body.slice(0, seg + 1));
          rot.push(P0);
          ref = rot;
          cum = cumOf(ref);
        }
      }
      const L0 = layoutName();
      track = {
        id: "trk-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
        name: L0.name + (course.open && course.label ? " · " + course.label.replace(/ \(.*/, "") : ""),
        date: L0.date,
        event: L0.event,
        num: L0.num,
        venue: (info.venue || md.venue || "").trim(),
        lat0,
        lon0,
        ref,
        cum,
        RL: cum[cum.length - 1],
        gates: [1 / 3, 2 / 3],
        open: course.open || undefined,
        created: Date.now(),
        from: info.name || fileName || "",
      };
      known.push(track);
    }

    const tk: Track = track;
    const F = frame(tk.lat0, tk.lon0);
    const proj = projWith(F);
    const ref = tk.ref;
    const cum = tk.cum;
    const RL = tk.RL;
    const open = !!course.open;
    const onRef = (p: XY) => nearOn(ref, cum, p);
    const gates = (tk.gates || [1 / 3, 2 / 3]).map((g) => g * RL);
    const oddStart: string[] = [];

    function build(
      samples: LiveSample[],
      name: string,
      kind: "lap" | "out" | "partial",
      t0: number,
      t1: number,
      energy: { energyWh: number; energyOutWh?: number; energyInWh?: number } | null,
      complete: boolean,
    ): RawLap & { span: number } {
      const m = samples.length;
      const P = samples.map(proj);
      const spd = P.map((p, k) => {
        const a = Math.max(0, k - 1);
        const b = Math.min(m - 1, k + 1);
        const dt = (samples[b].t - samples[a].t) / 1000;
        return dt > 0 ? Math.hypot(P[b][0] - P[a][0], P[b][1] - P[a][1]) / dt : 0;
      });
      const sd = P.map(onRef);
      const u: number[] = [];
      sd.forEach((q, k) => {
        if (open) u.push(k === 0 ? q[0] : Math.max(u[k - 1], q[0]));
        else u.push(k === 0 ? (q[0] > RL / 2 ? q[0] - RL : q[0]) : Math.max(u[k - 1], q[0] + RL * Math.round((u[k - 1] - q[0]) / RL)));
      });
      const tt = samples.map((s) => s.t - t0);
      const dur = t1 - t0;
      const V = (s: LiveSample, key: string) => num((s.values || {})[key]);
      const col = (f: (s: LiveSample) => number, d: number) => samples.map((s) => R2(f(s), d));
      const ch: Partial<BaseChannels> = { t: tt, u: u.map((v) => R2(v, 1)) };
      const set = (k: ChKey, f: (s: LiveSample) => number, d: number) => {
        ch[k] = col(f, d);
      };
      set("thr", (s) => V(s, "accel_pedal_travel") * 100, 1);
      set("brk", (s) => V(s, "brake_pressure_f"), 1);
      set("brkr", (s) => V(s, "brake_pressure_rbll"), 1);
      set("steer", (s) => V(s, "steer_col_angle"), 1);
      set("rpm", (s) => V(s, "motor_speed"), 0);
      set("trq", (s) => V(s, "torque_feedback"), 1);
      set("dcv", (s) => V(s, "dc_bus_v"), 1);
      set("dca", (s) => V(s, "dc_bus_current"), 1);
      set("kw", (s) => (V(s, "dc_bus_v") * V(s, "dc_bus_current")) / 1000, 1);
      set("packv", (s) => num(s.hv_pack_v), 2);
      set("soc", (s) => V(s, "soc_estimate"), 1);
      set("motT", (s) => V(s, "motor_temp"), 1);
      set("invT", (s) => V(s, "inverter_hotspot_temp"), 1);
      set("gateT", (s) => V(s, "gate_driver_temp"), 1);
      set("coolT", (s) => V(s, "coolant_temp"), 1);
      set("cellT", (s) => V(s, "max_cell_temp"), 1);
      set("cellV", (s) => V(s, "min_cell_voltage"), 3);
      set("modA", (s) => V(s, "module_a_temp"), 1);
      set("modB", (s) => V(s, "module_b_temp"), 1);
      set("modC", (s) => V(s, "module_c_temp"), 1);
      set("vmaxC", (s) => V(s, "max_cell_voltage"), 3);
      set("vavgC", (s) => V(s, "avg_cell_v_stat"), 3);
      ch.cells = samples.map((s) => cellIdx.map((i) => R2(num(s.cellTemps && s.cellTemps[i]), 1)));
      const pts: Pt[] = samples.map((_, k) => [
        P[k][0],
        P[k][1],
        R2(spd[k], 2),
        open ? R2(Math.min(RL - 0.05, sd[k][0]), 1) : R2(((sd[k][0] % RL) + RL) % RL, 1),
        R2(sd[k][1], 2),
      ]);
      /* sector splits: the standard gates sit at fixed distances past the start line, so every lap and every driver is cut at the same places */
      const cross = (th: number): number | null => {
        for (let k = 1; k < m; k++) {
          if (u[k - 1] < th && u[k] >= th) return tt[k - 1] + ((tt[k] - tt[k - 1]) * (th - u[k - 1])) / (u[k] - u[k - 1]);
        }
        return null;
      };
      const c1 = cross(gates[0]);
      const c2 = cross(gates[1]);
      let sec: number[] = [];
      if (c1 != null) sec.push(c1);
      if (c1 != null && c2 != null) {
        sec.push(c2 - c1);
        if (complete) sec.push(dur - c2);
      } else if (complete) {
        sec = [dur / 3, dur / 3, dur / 3];
        oddStart.push(name);
      }
      let dist = 0;
      for (let k = 1; k < m; k++) dist += Math.hypot(P[k][0] - P[k - 1][0], P[k][1] - P[k - 1][1]);
      const vmax = Math.max(...spd);
      return {
        ch: ch as BaseChannels,
        name,
        kind,
        t0,
        t1,
        dur,
        sec,
        pts,
        dist,
        vmax,
        vavg: dur > 0 ? dist / (dur / 1000) : 0,
        dev: sd.reduce((a, q) => a + q[1], 0) / m,
        span: u[m - 1] - u[0],
        wh: energy ? num(energy.energyWh) : null,
        whOut: energy ? num(energy.energyOutWh) : null,
        whIn: energy ? num(energy.energyInWh) : null,
      };
    }

    const laps: RawLap[] = [];
    const strip = ({ span, ...rest }: RawLap & { span: number }): RawLap => {
      void span;
      return rest;
    };
    const noun = open ? "course" : "circuit";
    group.forEach((o, gi) => {
      const sm = slice(o.o.l.startMs, o.o.l.endMs);
      const no = multi ? gi + 1 : o.o.i + 1;
      if (sm.length < 4) {
        cw.push("Lap " + no + " skipped: only " + sm.length + " GPS fixes");
        return;
      }
      const L = build(sm, "Lap " + no, "lap", o.o.l.startMs, o.o.l.endMs, o.o.l, true);
      if (L.span > 1.5 * RL) {
        cw.push("Lap " + no + " skipped: it covers " + (L.span / RL).toFixed(1) + " " + noun + "s of the track (" + L.dur / 1000 + " s), probably a missed lap press");
        return;
      }
      if (L.span < 0.6 * RL) cw.push("Lap " + no + " only covers " + Math.round((L.span / RL) * 100) + "% of the " + noun + ", check its GPS");
      laps.push(strip(L));
    });
    if (!laps.length) throw new Error("No timed lap was a clean single " + noun + " of the track.");
    if (oddStart.length) cw.push(oddStart.join(", ") + ": did not pass both standard sector gates, sectors split evenly by time");

    const spdAt = (arr: LiveSample[], k: number) => {
      const a = arr[Math.max(0, k - 1)];
      const b = arr[Math.min(arr.length - 1, k + 1)];
      const pa = proj(a);
      const pb = proj(b);
      const dt = (b.t - a.t) / 1000;
      return dt > 0 ? Math.hypot(pb[0] - pa[0], pb[1] - pa[1]) / dt : 0;
    };
    /* On a loop: the out lap is the last stretch of driving before the first timed lap, and the tail is whatever was driven after the last
       lap press. An open course has neither: what comes before and after a run is a drive to the line and a drive back. */
    if (!open) {
      const first = laps[0];
      if (group[0].o.l.startMs === firstT) {
        const ps = st.filter((s) => s.t <= first.t0 && hasGps(s));
        let k = ps.length - 1;
        while (k > 0 && spdAt(ps, k - 1) >= 3 && ps[k].t - ps[k - 1].t < 8000 && first.t0 - ps[k - 1].t < 180000) k--;
        const out = ps.slice(k);
        if (out.length >= 8) {
          const L = build(out, "Out lap", "out", out[0].t, first.t0, null, true);
          if (L.span <= 1.5 * RL) laps.push(strip(L));
        }
      }
      const last = group[group.length - 1].o;
      if (last.l.endMs === lastT) {
        const tail = st.filter((s) => s.t >= last.l.endMs && hasGps(s));
        while (tail.length && spdAt(tail, tail.length - 1) < 1) tail.pop();
        if (tail.length >= 8) {
          const L = build(tail, "Lap " + (multi ? group.length + 1 : last.i + 2) + " (in progress)", "partial", tail[0].t, tail[tail.length - 1].t, null, false);
          laps.push(strip(L));
        }
      }
    }
    const base = info.id || "file-" + nm + "-" + (J.savedAt || "");
    const D: RunData = {
      ref: ref.map((p) => [R2(p[0], 2), R2(p[1], 2)] as XY),
      cum: cum.map((v) => R2(v, 2)),
      RL,
      open: open || undefined,
      laps,
      meta: {
        cellIdx,
        sessId: multi ? base + "~" + (ci + 1) : base,
        cutoff: num(J.soeCutoffCellV) || 2.8,
        planLaps: num(J.targetLaps) || laps.filter((l) => l.kind === "lap").length,
        planKwh: num(J.targetEnergyKwh) || 0,
        name: multi && course.label ? nm + " · " + course.label : nm,
        driver: info.driver || md.driver || "",
        venue: info.venue || md.venue || "",
        event: info.eventType || md.event || "",
        trackId: tk.id,
        trackName: tk.name,
      },
      warnings: cw,
    };
    return { D, track: tk, isNew, renamed, label: course.label };
  });
}
