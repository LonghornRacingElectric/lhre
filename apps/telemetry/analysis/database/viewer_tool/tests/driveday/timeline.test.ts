import { describe, expect, it, vi } from "vitest";
import { convertSession } from "../../src/lib/driveday/convert";
import { Player } from "../../src/lib/driveday/player";
import { rebuildRun } from "../../src/lib/driveday/rebuild";
import { Session } from "../../src/lib/driveday/session";
import { emptyUserState } from "../../src/lib/driveday/types";
import { defaultUnits } from "../../src/lib/driveday/units";
import { synthSession } from "./fixtures/synth";

const drawing = vi.hoisted(() => ({ height: 108, width: 800, fonts: [] as string[], calls: [] as unknown[][], rectangles: [] as string[], strokes: [] as string[], bubbles: 0, fills: [] as string[], scale: vi.fn() }));
vi.mock("../../src/lib/driveday/draw/palette", () => {
  const properties: Record<string, unknown> = {};
  const context = new Proxy(properties, {
    get: (object, key: string) => {
      if (key === "scale") return drawing.scale;
      if (key === "measureText") return (text: string) => ({ width: text.length * 6 });
      if (key === "fillText") return (...args: unknown[]) => { drawing.fonts.push(String(properties.font)); drawing.calls.push(args); };
      if (key === "fillRect") return () => drawing.rectangles.push(String(properties.fillStyle));
      if (key === "roundRect") return () => { drawing.bubbles++; };
      if (key === "fill") return () => drawing.fills.push(String(properties.fillStyle));
      if (key === "stroke") return () => drawing.strokes.push(String(properties.strokeStyle));
      return key in object ? object[key] : () => {};
    },
  });
  return { MONO: () => "10px monospace", palette: () => ({ fMono: "monospace", blue: "blue", purple: "purple", green: "green", yellow: "yellow", track: "track", s2: "orange", panel: "panel", ink: "ink" }), setupCanvas: () => ({ g: context, w: drawing.width, h: drawing.height }) };
});

it("retains the speed trace and identical chapter colours at compact heights", () => {
  const p = new Player(new Session(rebuildRun(convertSession(synthSession({ laps: 3 }), "colours.json")[0].D)), emptyUserState(), defaultUnits());
  const colours = [108, 48].map(height => {
    drawing.height = height; drawing.rectangles = []; drawing.strokes = [];
    drawTimeline(p, {} as HTMLCanvasElement);
    expect(drawing.strokes).toContain("blue");
    return [...drawing.rectangles];
  });
  expect(colours[0]).toEqual(colours[1]);
  p.dispose();
});
import { drawTimeline } from "../../src/lib/driveday/draw/timeline";

describe("timeline text while resizing", () => {
  it("keeps fonts and glyph proportions fixed while graph height changes", () => {
    const session = new Session(rebuildRun(convertSession(synthSession({ laps: 3 }), "font-test.json")[0].D));
    const p = new Player(session, emptyUserState(), defaultUnits());
    for (const height of [80, 108, 220, 48]) {
      drawing.height = height; drawing.fonts = []; drawing.calls = []; drawing.scale.mockClear();
      drawTimeline(p, {} as HTMLCanvasElement);
      expect(drawing.scale).not.toHaveBeenCalled();
      expect(drawing.fonts.every(font => /(?:10|11)px/.test(font))).toBe(true);
      expect(drawing.calls.every(call => call.length === 3)).toBe(true);
    }
    p.dispose();
  });
});

it("draws note bubbles and cone dots at compact and expanded heights", () => {
  const p = new Player(new Session(rebuildRun(convertSession(synthSession({ laps: 3 }), "markers.json")[0].D)), emptyUserState(), defaultUnits());
  const t = p.session.laps[0].t0 - p.session.T0 + 1000;
  p.user.marks.push({ id: "note", t, text: "Brake earlier" });
  p.user.ce.push({ id: "cone", t: t + 1000 });
  for (const height of [48, 108]) {
    drawing.height = height; drawing.bubbles = 0; drawing.fills = [];
    drawTimeline(p, {} as HTMLCanvasElement);
    expect(drawing.bubbles).toBe(1);
    expect(drawing.fills).toContain("purple");
    expect(drawing.fills).toContain("orange");
    expect(drawing.rectangles).toContain("yellow");
    expect(drawing.rectangles).toContain("purple");
  }
  p.dispose();
});

it("skips out laps when stepping forward and backward without reindexing laps", () => {
  const p = new Player(new Session(rebuildRun(convertSession(synthSession({ laps: 3 }), "navigation.json")[0].D)), emptyUserState(), defaultUnits());
  const original = [...p.session.laps];
  p.session.laps[1].kind = "out";
  p.sel = 0; p.seekLap(0); p.stepLap(1);
  expect(p.T).toBe(p.session.laps[2].t0 - p.T0);
  p.sel = 2; p.stepLap(-1);
  expect(p.T).toBe(p.session.laps[0].t0 - p.T0);
  expect(p.session.laps).toEqual(original);
  p.dispose();
});
