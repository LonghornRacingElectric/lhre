export function timelineWindow(duration: number, start = 0, end = duration) {
  const span = Math.max(Math.min(2000, duration), Math.min(duration, end - start));
  const a = Math.max(0, Math.min(duration - span, start));
  return { start: a, end: a + span, span };
}

export function zoomWindow(duration: number, start: number, end: number, factor: number, anchor: number) {
  const current = timelineWindow(duration, start, end);
  const fraction = Math.max(0, Math.min(1, (anchor - current.start) / current.span));
  const span = Math.max(Math.min(2000, duration), Math.min(duration, current.span / factor));
  return timelineWindow(duration, anchor - span * fraction, anchor + span * (1 - fraction));
}

/** Render a pixel envelope; retain peaks and troughs without changing stored samples. */
export function speedEnvelope(times: number[], speeds: number[], start: number, end: number, pixels: number) {
  const lower = (value: number) => { let a = 0, b = times.length; while (a < b) { const m = (a + b) >>> 1; if (times[m] < value) a = m + 1; else b = m; } return a; };
  const first = Math.max(0, lower(start) - 1), last = Math.min(times.length - 1, lower(end));
  const out: number[] = [];
  let bucket = -Infinity, begin = first, min = first, max = first, finish = first;
  const flush = () => { if (bucket !== -Infinity) out.push(...[...new Set([begin, min, max, finish])].sort((a, b) => a - b)); };
  for (let i = first; i <= last; i++) {
    const next = Math.floor((times[i] - start) / Math.max(1, end - start) * Math.max(1, pixels));
    if (next !== bucket) { flush(); bucket = next; begin = min = max = i; }
    finish = i;
    if (speeds[i] < speeds[min]) min = i;
    if (speeds[i] > speeds[max]) max = i;
  }
  flush();
  return out;
}
