import { trackEdges } from "./geometry";
import pickleLot from "./layouts/pickle-lot.json";
import type { RoadEdges, Track, XY } from "./types";

/* Base layouts shipped with the app. Each comes from one slow lap driven down the middle of the track
   (scripts/build-layout.mjs turns that session export into the JSON here). From the GPS line we work out the real centre
   line and the track edges (geometry.ts, trackEdges), and make one layout for each direction: laps driven the other way
   round are not comparable, so each direction is its own track and only matches runs driven that way. */

interface LayoutFile {
  name: string;
  venue: string;
  from: string;
  recorded: string;
  driver: string;
  direction: "ccw" | "cw";
  lengthM: number;
  line: [number, number][];
}

const FILES: LayoutFile[] = [pickleLot as LayoutFile];

const cumOf = (p: XY[]) => {
  const c = [0];
  for (let i = 1; i < p.length; i++) c.push(c[i - 1] + Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]));
  return c;
};
const DIR = { ccw: "counter-clockwise", cw: "clockwise" } as const;
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

/** Both directions of one layout file, in a local metre frame centred on the layout. */
export function tracksFromLayout(f: LayoutFile): Track[] {
  const lat0 = f.line.reduce((a, p) => a + p[0], 0) / f.line.length;
  const lon0 = f.line.reduce((a, p) => a + p[1], 0) / f.line.length;
  const KY = (6378137 * Math.PI) / 180;
  const KX = KY * Math.cos((lat0 * Math.PI) / 180);
  const gps: XY[] = f.line.map(([la, lo]) => [(lo - lon0) * KX, (la - lat0) * KY]);
  gps.push(gps[0]);
  const E = trackEdges(gps);
  const made = Date.parse(f.recorded) || 0;
  const one = (dir: "ccw" | "cw", ref: XY[], edges: RoadEdges): Track => {
    const cum = cumOf(ref);
    return {
      id: "layout-" + slug(f.name) + "-" + dir,
      name: f.name + " · " + DIR[dir],
      date: f.recorded.slice(0, 10),
      event: "Base layout",
      num: 1,
      venue: f.venue,
      lat0,
      lon0,
      ref,
      cum,
      RL: cum[cum.length - 1],
      gates: [1 / 3, 2 / 3],
      created: made,
      from: f.from,
      builtin: true,
      edges,
    };
  };
  const rev = <T,>(a: T[]) => a.slice().reverse();
  const asDriven = one(f.direction, E.centre, { centre: E.centre, left: E.left, right: E.right, width: E.width });
  /* the other way round: the same road, so the same edges, with left and right swapped */
  const other = f.direction === "ccw" ? "cw" : "ccw";
  const reversed = one(other, rev(E.centre), { centre: rev(E.centre), left: rev(E.right), right: rev(E.left), width: E.width });
  return [asDriven, reversed];
}

let cache: Track[] | null = null;
/** Every built-in layout, both directions. */
export function builtinTracks(): Track[] {
  return (cache ??= FILES.flatMap(tracksFromLayout));
}
