/* Theme colours as canvas can use them. Read once per frame instead of per shape: getComputedStyle is slow. */

export interface Palette {
  ink: string; mute: string; track: string; base: string; line: string; panel: string; bg: string; sel: string;
  s1: string; s2: string; s3: string;
  purple: string; green: string; yellow: string; red: string; blue: string; rival: string;
  fDisp: string; fBody: string; fMono: string;
  sector: [string, string, string];
}

const NAMES: Record<string, string> = {
  ink: "--ink", mute: "--mute", track: "--track", base: "--base", line: "--line", panel: "--panel", bg: "--bg", sel: "--sel",
  s1: "--s1", s2: "--s2", s3: "--s3", purple: "--purple", green: "--green", yellow: "--yellow", red: "--red", blue: "--blue", rival: "--rival",
  fDisp: "--f-disp", fBody: "--f-body", fMono: "--f-mono",
};

let cache: Palette | null = null;

/** Forget the cached colours (the theme changed). */
export function invalidatePalette() {
  cache = null;
}

export function palette(): Palette {
  if (cache) return cache;
  const cs = getComputedStyle(document.documentElement);
  const o = {} as Record<string, string>;
  for (const k of Object.keys(NAMES)) o[k] = cs.getPropertyValue(NAMES[k]).trim();
  const p = o as unknown as Palette;
  p.sector = [p.s1, p.s2, p.s3];
  cache = p;
  return p;
}

/** Size a canvas to its CSS box at the device pixel ratio and clear it. */
export function setupCanvas(c: HTMLCanvasElement): { g: CanvasRenderingContext2D; w: number; h: number } | null {
  const r = c.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) return null;
  const dpr = window.devicePixelRatio || 1;
  const W = Math.round(r.width * dpr);
  const H = Math.round(r.height * dpr);
  if (c.width !== W || c.height !== H) {
    c.width = W;
    c.height = H;
  }
  const g = c.getContext("2d");
  if (!g) return null;
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, r.width, r.height);
  return { g, w: r.width, h: r.height };
}

export const MONO = (p: Palette) => "10px " + p.fMono;

/** Viridis-style ramp for 0..1. */
export function ramp(t: number): string {
  t = Math.max(0, Math.min(1, t));
  const st = [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]];
  const f = t * 4;
  const i = Math.min(3, Math.floor(f));
  const u = f - i;
  return "rgb(" + st[i].map((c, k) => Math.round(c + (st[i + 1][k] - c) * u)).join(",") + ")";
}

/** Blue to red speed ramp. */
export function rampSpd(t: number): string {
  t = Math.max(0, Math.min(1, t));
  const st = [[72, 104, 214], [44, 168, 206], [98, 196, 118], [236, 204, 74], [232, 122, 62]];
  const f = t * 4;
  const i = Math.min(3, Math.floor(f));
  const u = f - i;
  return "rgb(" + st[i].map((c, k) => Math.round(c + (st[i + 1][k] - c) * u)).join(",") + ")";
}

export const gradientCss = (f: (t: number) => string, stops = [0, 0.25, 0.5, 0.75, 1]) => "linear-gradient(90deg," + stops.map(f).join(",") + ")";

/** Temperature heat colour: cool blue to hot red. */
export function heat(v: number, lo: number, hi: number): string {
  const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
  return "hsl(" + Math.round(205 - t * 200) + " 78% 46%)";
}
