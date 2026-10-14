// Parse a lap time typed by a person. Accepts "M:SS.ss" / "MM:SS.ss" or plain
// seconds ("83.45"). Returns milliseconds, or null if it can't be parsed.
export function parseLapTimeInput(text: string): number | null {
  const s = text.trim();
  if (!s) return null;
  const colon = s.match(/^(\d+):(\d{1,2}(?:\.\d+)?)$/);
  if (colon) {
    const min = Number(colon[1]);
    const sec = Number(colon[2]);
    if (!Number.isFinite(min) || !Number.isFinite(sec) || sec >= 60) return null;
    return Math.round((min * 60 + sec) * 1000);
  }
  const n = Number(s);
  return Number.isFinite(n) && n >= 0 ? Math.round(n * 1000) : null;
}
