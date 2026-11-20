import { vi } from "vitest";

/* A canvas that records what the drawing code does to it and checks every call, so a NaN that reaches a path, a colour
   built from undefined, a label that reads "NaN" or a save() without its restore() fails a test instead of drawing
   nothing (or the wrong thing) in the browser. Node has no canvas, and the draw modules only need a 2D context. */

export interface Recording {
  /** Every method call, in order. */
  calls: { name: string; args: unknown[] }[];
  /** Everything that broke a rule, as readable lines. */
  problems: string[];
  /** Text drawn with fillText or strokeText. */
  text: string[];
  /** save() depth now; 0 when the drawing code is balanced. */
  depth: number;
}

const BAD_TEXT = /NaN|undefined|null|Infinity/;
const COLOUR_PROPS = new Set(["fillStyle", "strokeStyle", "shadowColor"]);

function checkColour(rec: Recording, where: string, v: unknown) {
  if (typeof v === "string" && (v === "" || BAD_TEXT.test(v))) rec.problems.push(`${where} = ${JSON.stringify(v)}`);
}

function checkNumbers(rec: Recording, where: string, args: unknown[]) {
  args.forEach((a, i) => {
    if (typeof a === "number" && !Number.isFinite(a)) rec.problems.push(`${where} argument ${i} is ${a}`);
    if (Array.isArray(a)) checkNumbers(rec, `${where}[${i}]`, a);
  });
}

function gradient(rec: Recording, kind: string) {
  return {
    addColorStop(offset: number, colour: string) {
      if (!Number.isFinite(offset) || offset < 0 || offset > 1) rec.problems.push(`${kind} stop offset ${offset}`);
      checkColour(rec, `${kind} stop`, colour);
    },
  };
}

/** A 2D context and the canvas element that owns it, w × h CSS pixels. */
export function recordingCanvas(w: number, h: number) {
  const rec: Recording = { calls: [], problems: [], text: [], depth: 0 };
  const state: Record<string, unknown> = { font: "10px sans-serif", globalAlpha: 1, lineWidth: 1 };
  let dash: number[] = [];
  const canvas = {
    width: 0,
    height: 0,
    getBoundingClientRect: () => ({ x: 0, y: 0, left: 0, top: 0, right: w, bottom: h, width: w, height: h }),
    getContext: () => ctx,
  } as unknown as HTMLCanvasElement;
  const methods: Record<string, (...a: never[]) => unknown> = {
    save: () => { rec.depth++; },
    restore: () => {
      if (--rec.depth < 0) { rec.problems.push("restore() without save()"); rec.depth = 0; }
    },
    measureText: (s: string) => ({ width: String(s).length * 6, actualBoundingBoxAscent: 7, actualBoundingBoxDescent: 2 }),
    createLinearGradient: () => gradient(rec, "linear gradient"),
    createRadialGradient: () => gradient(rec, "radial gradient"),
    createConicGradient: () => gradient(rec, "conic gradient"),
    setLineDash: (d: number[]) => { dash = [...d]; },
    getLineDash: () => [...dash],
    fillText: (s: string) => { rec.text.push(String(s)); if (BAD_TEXT.test(String(s))) rec.problems.push(`fillText(${JSON.stringify(s)})`); },
    strokeText: (s: string) => { rec.text.push(String(s)); if (BAD_TEXT.test(String(s))) rec.problems.push(`strokeText(${JSON.stringify(s)})`); },
  };
  const ctx = new Proxy(state, {
    get(target, key: string) {
      if (key === "canvas") return canvas;
      if (key in methods) return (...args: never[]) => { rec.calls.push({ name: key, args }); checkNumbers(rec, key, args); return methods[key](...args); };
      if (key in target) return target[key];
      return (...args: unknown[]) => { rec.calls.push({ name: key, args }); checkNumbers(rec, key, args); };
    },
    set(target, key: string, v: unknown) {
      if (typeof v === "number" && !Number.isFinite(v)) rec.problems.push(`${key} = ${v}`);
      if (key === "globalAlpha" && typeof v === "number" && (v < 0 || v > 1)) rec.problems.push(`globalAlpha = ${v}`);
      if (key === "lineWidth" && typeof v === "number" && v < 0) rec.problems.push(`lineWidth = ${v}`);
      if (COLOUR_PROPS.has(key)) checkColour(rec, key, v);
      if (key === "font" && (typeof v !== "string" || BAD_TEXT.test(v))) rec.problems.push(`font = ${JSON.stringify(v)}`);
      target[key] = v;
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
  return { canvas, ctx, rec };
}

/** The theme the page sets on .ddv, so palette() reads real-looking values in node. */
export const THEME: Record<string, string> = {
  "--ink": "#e8e6e0", "--mute": "#8a877f", "--track": "#3a3833", "--base": "#55524b", "--line": "#2a2925",
  "--panel": "#1b1a17", "--bg": "#121110", "--sel": "#f0c040", "--s1": "#e05a4f", "--s2": "#f0a03c", "--s3": "#4fa3e0",
  "--purple": "#b07ae0", "--green": "#5cc28a", "--yellow": "#e8d25a", "--red": "#e0564f", "--blue": "#4f8fe0",
  "--rival": "#d0d0d0", "--f-disp": "Inter", "--f-body": "Inter", "--f-mono": "JetBrains Mono",
};

/** The browser globals palette() and drawMap() read: the computed theme and the device pixel ratio. */
export function stubBrowser(dpr = 2) {
  vi.stubGlobal("window", { devicePixelRatio: dpr });
  vi.stubGlobal("document", { querySelector: () => ({}), documentElement: {} });
  vi.stubGlobal("getComputedStyle", () => ({ getPropertyValue: (k: string) => THEME[k] ?? "" }));
}
