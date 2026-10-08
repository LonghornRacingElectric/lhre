/** Lap-style time: 19.622, or 1:02.345 once past a minute. Input is milliseconds. */
export function fmt(ms: number | null | undefined, p = 3): string {
  if (ms == null) return "—";
  const s = ms / 1000;
  if (s >= 60) {
    const m = Math.floor(s / 60);
    return m + ":" + (s - m * 60).toFixed(p).padStart(p + 3, "0");
  }
  return s.toFixed(p);
}

/** Clock time with one decimal: 2:05.3. */
export function clock(ms: number): string {
  const s = Math.max(0, ms) / 1000;
  const m = Math.floor(s / 60);
  return m + ":" + (s - m * 60).toFixed(1).padStart(4, "0");
}

/** Signed seconds: +0.123 or −0.123. */
export const sgn = (ms: number) => (ms >= 0 ? "+" : "−") + (Math.abs(ms) / 1000).toFixed(3);

export const MPH = 2.23694;
export const KMH_PER_MPH = 1.609344;
