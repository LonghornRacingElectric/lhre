import { describe, expect, it } from "vitest";
import { ghostCar, roadLine, defaultLine } from "../../src/lib/driveday/video-overlay";
import type { Lap } from "../../src/lib/driveday/types";

const straight = (side = 0): Lap => {
  const t = Array.from({ length: 21 }, (_, i) => i * 1000);
  const u = t.map(v => v / 100);
  const pts = u.map(s => [s, side, 10, s, 0]);
  return { kind: "lap", dur: 20000, pts, ch: { t, u, x: u, y: u.map(() => side), spd: u.map(() => 22), brkp: u.map(() => 20), thr: u.map(() => 0) } } as Lap;
};

describe("recorded racing-line video projection", () => {
  it("projects a straight ahead of the car toward the horizon", () => {
    const lap = straight();
    const points = roadLine(lap, lap, 5000, defaultLine);
    expect(points.length).toBeGreaterThan(10);
    expect(points.every(p => Math.abs(p.x - 0.5) < 0.001)).toBe(true);
    expect(points[0].y).toBeGreaterThan(points.at(-1)!.y);
    expect(points.every(p => p.y > defaultLine.horizon && p.y < 1)).toBe(true);
    expect(points[0].brake).toBe(20);
  });
  it("places a ghost to the correct side and applies calibration", () => {
    const points = roadLine(straight(), straight(2), 5000, defaultLine);
    expect(points[0].x).toBeLessThan(0.5);
    const shifted = roadLine(straight(), straight(2), 5000, { ...defaultLine, center: 0.6 });
    expect(shifted[0].x - points[0].x).toBeCloseTo(0.1);
  });
  it("does not fabricate a line beyond the finish or while stationary", () => {
    const lap = straight();
    expect(roadLine(lap, lap, lap.dur, defaultLine)).toEqual([]);
    expect(roadLine({ ...lap, ch: { ...lap.ch, spd: lap.ch.spd.map(() => 0) } }, lap, 5000, defaultLine)).toEqual([]);
  });
});

describe("equal-time 3D ghost projection", () => {
  const faster = () => { const lap=straight();return {...lap,ch:{...lap.ch,x:lap.ch.x.map(x=>x*2)}}; };
  it("uses equal elapsed time rather than equal distance", () => {
    const projected=ghostCar(straight(),faster(),5000,defaultLine);
    expect(projected.forward).toBeCloseTo(50);
    expect(projected.faces.length).toBeGreaterThan(20);
    expect(projected.faces.flatMap(f=>f.points.split(/[ ,]/).map(Number)).every(Number.isFinite)).toBe(true);
    expect(projected.faces.every((f,i,all)=>i===0||all[i-1].depth>=f.depth)).toBe(true);
  });
  it("changes perspective with distance and camera calibration", () => {
    const current=straight(),target=faster();
    const width=(faces:ReturnType<typeof ghostCar>["faces"])=>{const xs=faces.flatMap(f=>f.points.split(" ").map(pair=>+pair.split(",")[0]));return Math.max(...xs)-Math.min(...xs);};
    const near=ghostCar(current,target,3000,defaultLine),far=ghostCar(current,target,8000,defaultLine);
    expect(width(near.faces)).toBeGreaterThan(width(far.faces));
    const shifted=ghostCar(current,target,3000,{...defaultLine,center:0.6});
    expect(+shifted.faces[0].points.split(",")[0]-(+near.faces[0].points.split(",")[0])).toBeCloseTo(100);
  });
  it("suppresses behind, overlapping, finished and synthetic ghosts", () => {
    const current=straight(),target=faster();
    expect(ghostCar(target,current,5000,defaultLine).status).toBe("Ghost behind");
    expect(ghostCar(current,straight(),5000,defaultLine).faces).toEqual([]);
    expect(ghostCar(current,target,21000,defaultLine).status).toBe("Ghost finished");
    expect(ghostCar(current,{...target,kind:"theo"},5000,defaultLine).faces).toEqual([]);
    expect(ghostCar(current,current,5000,defaultLine).faces).toEqual([]);
  });
  it("does not project through GPS gaps or stationary headings", () => {
    const current=straight(),target=faster();
    const gap={...target,ch:{...target.ch,t:target.ch.t.map((t,i)=>i>=5?t+3000:t)}};
    expect(ghostCar(current,gap,5500,defaultLine).faces).toEqual([]);
    const stationary={...current,ch:{...current.ch,x:current.ch.x.map(()=>0)}};
    expect(ghostCar(stationary,target,5000,defaultLine).faces).toEqual([]);
  });
});
