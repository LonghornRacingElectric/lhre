/** Linear interpolation of ys at x, over ascending xs. Clamps outside the range. */
export function interp(xs: number[], ys: number[], x: number): number {
  const n = xs.length;
  if (x <= xs[0]) return ys[0];
  if (x >= xs[n - 1]) return ys[n - 1];
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const m = (lo + hi) >> 1;
    if (xs[m] <= x) lo = m;
    else hi = m;
  }
  const d = xs[hi] - xs[lo];
  return d > 0 ? ys[lo] + ((ys[hi] - ys[lo]) * (x - xs[lo])) / d : ys[lo];
}

/** Centred time average of a piecewise-linear signal; keeps every original timestamp. */
export function timeAverage(times: number[], values: number[], halfWindowMs = 250): number[] {
  if (times.length < 2) return [...values];
  const area = [0];
  for (let i = 1; i < times.length; i++) {
    area.push(area[i - 1] + (times[i] - times[i - 1]) * (values[i - 1] + values[i]) / 2);
  }
  const integral = (t: number) => {
    let lo = 0, hi = times.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (times[mid] <= t) lo = mid; else hi = mid;
    }
    const dt = times[hi] - times[lo];
    const elapsed = t - times[lo];
    return area[lo] + values[lo] * elapsed + (dt > 0 ? (values[hi] - values[lo]) * elapsed ** 2 / (2 * dt) : 0);
  };
  return times.map((t, i) => {
    const a = Math.max(times[0], t - halfWindowMs);
    const b = Math.min(times[times.length - 1], t + halfWindowMs);
    return b > a ? (integral(b) - integral(a)) / (b - a) : values[i];
  });
}

export function med(a: number[]): number {
  const s = [...a].sort((x, y) => x - y);
  const n = s.length;
  if (!n) return 0;
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
}

export function pctl(a: number[], p: number): number {
  const s = [...a].sort((x, y) => x - y);
  return s[Math.floor((s.length - 1) * p)];
}

/** Sample standard deviation (0 for fewer than two values). */
export function sd(a: number[]): number {
  if (a.length < 2) return 0;
  const m = a.reduce((x, y) => x + y, 0) / a.length;
  return Math.sqrt(a.reduce((x, y) => x + (y - m) * (y - m), 0) / (a.length - 1));
}

/** Theil–Sen slope: the median of all pairwise slopes, so single bad readings do not move it. */
export function theil(xs: number[], ys: number[]): number {
  const m: number[] = [];
  for (let i = 0; i < xs.length; i++) {
    for (let j = i + 1; j < xs.length; j++) {
      if (xs[j] !== xs[i]) m.push((ys[j] - ys[i]) / (xs[j] - xs[i]));
    }
  }
  return m.length ? med(m) : 0;
}

export const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** Round bounds out to "nice" axis ticks. */
export function nice(lo: number, hi: number, n: number): { lo: number; hi: number; t: number[] } {
  const span = hi - lo || 1;
  const raw = span / n;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / mag;
  const st = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag;
  const a = Math.floor(lo / st + 1e-9) * st;
  const b = Math.ceil(hi / st - 1e-9) * st;
  const t: number[] = [];
  for (let v = a; v <= b + st / 2; v += st) t.push(+v.toFixed(6));
  return { lo: a, hi: b, t };
}

/** Wrap s into [0, len). */
export const wrap = (s: number, len: number) => ((s % len) + len) % len;

/** Shortest circular distance between two arc lengths on a loop of length len. */
export const circDist = (a: number, b: number, len: number) =>
  Math.abs((((a - b + len / 2) % len) + len) % len - len / 2);
