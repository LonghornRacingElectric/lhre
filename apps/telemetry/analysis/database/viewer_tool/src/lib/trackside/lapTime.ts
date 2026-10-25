// Parse a lap time typed by a person. Accepts "M:SS.ss" / "MM:SS.ss" or plain
// seconds ("83.45"). Returns milliseconds, or null if it can't be parsed.
// Anything over a day is a typo, and would put the lap's timestamps outside what a Date can hold.
const MAX_LAP_MS = 24 * 3_600_000;
const lapMs = (seconds: number) => {
  const ms = Math.round(seconds * 1000);
  return Number.isFinite(ms) && ms >= 0 && ms <= MAX_LAP_MS ? ms : null;
};

export function parseLapTimeInput(text: string): number | null {
  const s = text.trim();
  if (!s) return null;
  const colon = s.match(/^(\d+):(\d{1,2}(?:\.\d+)?)$/);
  if (colon) {
    const min = Number(colon[1]);
    const sec = Number(colon[2]);
    if (!Number.isFinite(min) || !Number.isFinite(sec) || sec >= 60) return null;
    return lapMs(min * 60 + sec);
  }
  const n = Number(s);
  return Number.isFinite(n) ? lapMs(n) : null;
}
