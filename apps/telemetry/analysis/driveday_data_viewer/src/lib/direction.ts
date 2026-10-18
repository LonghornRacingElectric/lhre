import type { RunData, TrackDirection, XY } from "./types";

export const directionLabel = (direction: TrackDirection): string =>
  direction === "cw" ? "Clockwise" : direction === "ccw" ? "Counter-clockwise" : "Direction unclear";

/** East/north coordinates: positive signed area means counter-clockwise. The closing chord also makes open paths translation invariant. */
export function signedArea(points: XY[]): number {
  if (points.length < 3) return 0;
  const [x, y] = points[0];
  let twiceArea = 0;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i];
    twiceArea += (a[0] - x) * (b[1] - y) - (b[0] - x) * (a[1] - y);
  }
  return twiceArea / 2;
}

/** Overall circulation, independent of venue, course number, start point, or driving speed. */
export function pathDirection(path: XY[]): TrackDirection {
  const points = path.filter(p => Number.isFinite(p[0]) && Number.isFinite(p[1]));
  if (points.length < 3) return "unknown";
  // Equal-distance sampling prevents a slow/noisy corner from dominating the estimate.
  const spaced: XY[] = [points[0]];
  let carry = 0, length = 0;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i];
    const segment = Math.hypot(b[0] - a[0], b[1] - a[1]);
    length += segment;
    for (let along = 2 - carry; along <= segment; along += 2) {
      spaced.push([a[0] + (b[0] - a[0]) * along / segment, a[1] + (b[1] - a[1]) * along / segment]);
    }
    carry = (carry + segment) % 2;
  }
  spaced.push(points[points.length - 1]);
  const smooth: XY[] = spaced.map((p, i) => i === 0 || i === spaced.length - 1 ? p : [
    (spaced[i - 1][0] + 2 * p[0] + spaced[i + 1][0]) / 4,
    (spaced[i - 1][1] + 2 * p[1] + spaced[i + 1][1]) / 4,
  ]);
  const area = signedArea(smooth);
  // Straight paths, nearly cancelling figure-eights and GPS jitter have no reliable overall rotation.
  if (Math.abs(area) <= Math.max(4, length * length * 0.001)) return "unknown";
  return area > 0 ? "ccw" : "cw";
}

export function pathsDirection(paths: XY[][]): TrackDirection {
  const votes = paths.map(pathDirection);
  const cw = votes.filter(d => d === "cw").length, ccw = votes.filter(d => d === "ccw").length;
  return cw > ccw ? "cw" : ccw > cw ? "ccw" : "unknown";
}

/** Replace generated course suffixes only; preserve the original session name and stable run/layout IDs. */
export function directionName(name: string, direction: TrackDirection): string {
  return name.replace(/\s*·\s*Course\s+\d+(?:\s*\(reverse of(?:\s+course)?\s+\d+\))?\s*$/i, " · " + directionLabel(direction));
}

export function withRunDirection(data: RunData): RunData {
  let direction = data.meta.direction ?? pathDirection(data.ref);
  if (direction === "unknown") direction = pathsDirection(data.laps.filter(l => l.kind === "lap").map(l => l.pts.map(p => [p[0], p[1]] as XY)));
  return { ...data, meta: { ...data.meta, direction,
    name: directionName(data.meta.name, direction), trackName: directionName(data.meta.trackName, direction) } };
}
