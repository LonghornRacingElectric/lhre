import { describe, expect, it } from "vitest";
import { defaultWorkspace, movePane, resizePair, restoreWorkspace, workspacePreset, workspacePanels } from "../../src/lib/driveday/workspace";
import { lineChevron } from "../../src/lib/driveday/video-overlay";

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

describe("team layouts", () => {
  it("gives electronics the full width and four simultaneous panels", () => {
    const preset = workspacePreset("Electronics");
    expect(preset.showBetweenLaps).toBe(true);
    expect(preset.layout.panes.video.visible).toBe(false);
    expect(preset.layout.panes.track.visible).toBe(false);
    expect(preset.layout.panes.analysis.visible).toBe(true);
    expect(workspacePanels(preset)).toEqual(["car", "cells", "kw", "temp"]);
  });
  it("prioritizes driver video and trackside timing without sharing mutable state", () => {
    const driver = workspacePreset("Driver"), trackside = workspacePreset("Trackside");
    expect(driver.layout.panes.video.weight).toBeGreaterThan(driver.layout.panes.track.weight);
    expect(workspacePanels(trackside)).toEqual(["laps"]);
    expect(driver.showBetweenLaps).toBe(false); expect(trackside.showBetweenLaps).toBe(false);
    expect(trackside.layout.panes.video.visible).toBe(true);
    expect(trackside.layout.panes.analysis.visible).toBe(true);
    trackside.layout.panes.track.visible = false;
    expect(driver.layout.panes.track.visible).toBe(true);
    expect(workspacePreset("Trackside").layout.panes.track.visible).toBe(true);
  });
  it("migrates saved single-panel layouts and retains empty or multiple selections", () => {
    expect(workspacePanels({panel:"cells"})).toEqual(["cells"]);
    expect(workspacePanels({panel:"cells", panels:[]})).toEqual([]);
    expect(workspacePanels({panel:null, panels:["car","kw","car"]})).toEqual(["car","kw"]);
  });
});
