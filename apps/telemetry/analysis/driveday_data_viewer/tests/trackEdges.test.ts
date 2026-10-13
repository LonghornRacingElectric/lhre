import { describe, expect, it } from "vitest";
import { trackEdges, TRACK_WIDTH_M } from "../src/lib/trackEdges";
import { convertSession } from "../src/lib/convert";
import { synthCourses } from "./fixtures/courses";
import { synthSession } from "./fixtures/synth";

describe("estimated track edges", () => {
  it("shifts eastbound GPS south by one foot and spans 11.5 ft", () => {
    const e = trackEdges([[0, 0], [10, 0], [10, 0], [20, 0]], true);
    expect(e.center[1]).toEqual([10, -0.3048]);
    expect(e.left[1][1]).toBeCloseTo(-0.3048 + TRACK_WIDTH_M / 2);
    expect(e.right[1][1]).toBeCloseTo(-0.3048 - TRACK_WIDTH_M / 2);
    expect(e.center.length).toBe(3);
  });
  it("reverses the rightward correction with travel direction", () => {
    expect(trackEdges([[20, 0], [10, 0], [0, 0]], true).center[1]).toEqual([10, 0.3048]);
  });
  it("closes loop edges without a seam", () => {
    const e = trackEdges([[0,0], [10,0], [10,10], [0,10], [0,0]]);
    for (const line of [e.center,e.left,e.right]) expect(line[0]).toEqual(line.at(-1));
  });
});

it("reuses each open course only in its own direction across imports", () => {
  const j = synthCourses();
  const first = convertSession(j, "first.json", []);
  const next = convertSession({...j, sessionInfo: {...j.sessionInfo, id: "second"}}, "second.json", first.map(r=>r.track));
  expect(next.map(r=>r.track.id)).toEqual(first.map(r=>r.track.id));
  expect(next[0].track.id).not.toBe(next[1].track.id);
  expect(next.every(r=>!r.isNew)).toBe(true);
});

it("does not reuse a closed circuit for reverse travel", () => {
  const j = synthSession();
  const first = convertSession(j, "forward.json", []);
  const reversed = {...j, sampleTail: j.sampleTail.map((s,i,a)=>({...a[a.length-1-i],t:s.t}))};
  const next = convertSession(reversed, "reverse.json", first.map(r=>r.track));
  expect(next[0].track.id).not.toBe(first[0].track.id);
});
