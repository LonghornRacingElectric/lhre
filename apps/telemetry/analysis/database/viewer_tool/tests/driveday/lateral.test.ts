import { describe, expect, it } from "vitest";
import { lateralGs } from "../../src/lib/driveday/session";
import type { Pt } from "../../src/lib/driveday/types";

const circle = (direction: number): Pt[] => [-0.2, 0, 0.2].map((a) =>
  [20 * Math.cos(a * direction), 20 * Math.sin(a * direction), 10, 0, 0]);

describe("GPS lateral g", () => {
  it("matches v²/r on a constant-speed circle in either direction", () => {
    const expected = 100 / 20 / 9.80665;
    expect(lateralGs(circle(1), [0, 500, 1000])[1]).toBeCloseTo(expected, 8);
    expect(lateralGs(circle(-1), [0, 500, 1000])[1]).toBeCloseTo(-expected, 8);
  });

  it("reports zero on a straight even when speed changes", () => {
    const pts: Pt[] = [[0, 0, 5, 0, 0], [5, 0, 10, 0, 0], [10, 0, 15, 0, 0]];
    expect(lateralGs(pts, [0, 500, 1000])[1]).toBe(0);
  });

  it("estimates a 20 Hz circle even when adjacent fixes are under a metre apart", () => {
    const pts: Pt[] = Array.from({ length: 21 }, (_, i) => {
      const angle = i * 0.025;
      return [20 * Math.cos(angle), 20 * Math.sin(angle), 10, 0, 0];
    });
    expect(lateralGs(pts, pts.map((_, i) => i * 50))[10]).toBeCloseTo(100 / 20 / 9.80665, 8);
  });

  it("leaves endpoints and insufficient movement unavailable", () => {
    const pts = circle(1);
    const gs = lateralGs(pts, [0, 500, 1000]);
    expect(gs[0]).toBeNaN();
    expect(gs[2]).toBeNaN();
    expect(lateralGs([pts[0], pts[0], pts[2]], [0, 500, 1000])[1]).toBeNaN();
    expect(lateralGs(pts.map((p) => [p[0], p[1], 0.5, 0, 0]), [0, 500, 1000])[1]).toBeNaN();
    expect(lateralGs(pts, [0, 0, 1000])[1]).toBeNaN();
  });
});
