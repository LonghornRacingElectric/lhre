import { describe, expect, it } from "vitest";
import { defaultWorkspace, movePane, resizePair, restoreWorkspace } from "../src/lib/workspace";
import { lineChevron } from "../src/lib/video-overlay";

describe("workspace layout", () => {
  it("moves windows without losing hidden, floating or geometry state", () => {
    const layout = defaultWorkspace();
    layout.panes.video.floating = true; layout.panes.analysis.visible = false;
    const moved = movePane(layout, "video", "analysis");
    expect(moved.order).toEqual(["track", "analysis", "video"]);
    expect(moved.panes.video.floating).toBe(true);
    expect(moved.panes.analysis.visible).toBe(false);
    expect(layout.order).toEqual(["video", "track", "analysis"]);
  });
  it("resizes adjacent windows while preserving total width and the third pane", () => {
    const layout = defaultWorkspace();
    const resized = resizePair(layout, "video", "track", 0.6);
    expect(resized.panes.video.weight + resized.panes.track.weight).toBe(2.5);
    expect(resized.panes.analysis).toEqual(layout.panes.analysis);
    expect(resizePair(layout, "video", "track", -1).panes.video.weight).toBe(0.5);
  });
  it("recovers malformed saved order and validates dimensions", () => {
    const layout = defaultWorkspace();
    layout.order = ["video", "video", "track"];
    layout.panes.video.weight = NaN; layout.panes.video.width = -10;
    const restored = restoreWorkspace(layout);
    expect(restored.order).toEqual(["video", "track", "analysis"]);
    expect(restored.panes.video.weight).toBe(1);
    expect(restored.panes.video.width).toBe(260);
  });
});

describe("racing line arrows", () => {
  it("points chevrons toward the next projected point and rejects repeated points", () => {
    const a = { x: 0.5, y: 0.7, brake: 0, throttle: 70 }, b = { ...a, y: 0.5 };
    const path = lineChevron(a, b)!;
    const numbers = path.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
    expect(numbers[2]).toBe(500);
    expect(numbers[3]).toBe(281.25);
    expect(numbers[1]).toBeGreaterThan(numbers[3]);
    expect(numbers[5]).toBeGreaterThan(numbers[3]);
    expect(lineChevron(a, a)).toBeNull();
  });
});
