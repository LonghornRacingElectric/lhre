import { describe, expect, it, vi } from "vitest";
import { convertSession } from "../../src/lib/driveday/convert";
import { Player } from "../../src/lib/driveday/player";
import { rebuildRun } from "../../src/lib/driveday/rebuild";
import { Session } from "../../src/lib/driveday/session";
import { emptyUserState } from "../../src/lib/driveday/types";
import { defaultUnits } from "../../src/lib/driveday/units";
import { synthSession } from "./fixtures/synth";

const drawing = vi.hoisted(() => ({ height: 108, width: 800, fonts: [] as string[], calls: [] as unknown[][], scale: vi.fn() }));
vi.mock("../../src/lib/driveday/draw/palette", () => {
  const properties: Record<string, unknown> = {};
  const context = new Proxy(properties, {
    get: (object, key: string) => {
      if (key === "scale") return drawing.scale;
      if (key === "measureText") return (text: string) => ({ width: text.length * 6 });
      if (key === "fillText") return (...args: unknown[]) => { drawing.fonts.push(String(properties.font)); drawing.calls.push(args); };
      return key in object ? object[key] : () => {};
    },
  });
  return { MONO: () => "10px monospace", palette: () => ({ fMono: "monospace" }), setupCanvas: () => ({ g: context, w: drawing.width, h: drawing.height }) };
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
