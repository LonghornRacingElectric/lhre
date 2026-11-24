/* Layout model for the Trackside Live workspace (desktop only).
   Panels live in columns ("sections"). Each column splits its height between its panels by weight. */

export type Density = "comfortable" | "compact" | "large";
export const DENSITIES: Density[] = ["compact", "comfortable", "large"];

export interface PanelDef {
  id: string;
  title: string;
  /** Hidden in the default layout unless a preset shows it. */
  defaultVisible?: boolean;
  /** Column (0-based) in the default layout. */
  defaultCol?: number;
  /** Share of the column height, relative to its neighbours. */
  defaultWeight?: number;
}

export interface PanelState {
  visible: boolean;
  floating: boolean;
  col: number;
  weight: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface WorkspaceLayout {
  cols: number;
  density: Density;
  /** Size the columns to the window so the page never scrolls; panels shrink and scroll inside themselves. */
  fit: boolean;
  /** Relative width of each column (1 = equal). */
  colWeights: number[];
  /** Once a column's panels have been dragged to size, heights follow weights exactly instead of content. */
  colManual: boolean[];
  order: string[];
  panels: Record<string, PanelState>;
}

export interface Preset {
  /** Panel ids to show, in order. Everything else is hidden. */
  show: string[];
  cols: number;
  /** Column per id; ids without one are spread evenly. */
  place?: Record<string, number>;
  weights?: Record<string, number>;
}

export const MIN_COLS = 1;
export const MAX_COLS = 4;
export const MIN_WEIGHT = 0.4;
export const MAX_WEIGHT = 60;
export const STORAGE_LAYOUT = "tlw:layout:v6";
export const STORAGE_PRESETS = "tlw:presets:v6";

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));

export function defaultPanelState(def: PanelDef, index: number, cols = 3): PanelState {
  return {
    visible: def.defaultVisible !== false,
    floating: false,
    col: clamp(def.defaultCol ?? index % cols, 0, cols - 1),
    weight: def.defaultWeight ?? 1,
    x: 40 + index * 28,
    y: 120 + index * 28,
    width: 440,
    height: 420,
  };
}

export function defaultLayout(defs: PanelDef[]): WorkspaceLayout {
  return {
    cols: 3,
    density: "compact",
    fit: true,
    colWeights: [1, 1, 1],
    colManual: [false, false, false],
    order: defs.map((d) => d.id),
    panels: Object.fromEntries(defs.map((d, i) => [d.id, defaultPanelState(d, i)])),
  };
}

/** Merge a saved layout with the panels that exist now: unknown ids are dropped, new ids are appended. */
export function restoreLayout(raw: Partial<WorkspaceLayout> | null | undefined, defs: PanelDef[]): WorkspaceLayout {
  const base = defaultLayout(defs);
  if (!raw || typeof raw !== "object") return base;
  const ids = new Set(defs.map((d) => d.id));
  const cols = Number.isFinite(raw.cols) ? clamp(Math.round(raw.cols as number), MIN_COLS, MAX_COLS) : base.cols;
  const density = DENSITIES.includes(raw.density as Density) ? (raw.density as Density) : base.density;
  const fit = typeof raw.fit === "boolean" ? raw.fit : base.fit;
  const colWeights = Array.from({ length: cols }, (_, i) => {
    const w = Array.isArray(raw.colWeights) ? raw.colWeights[i] : 1;
    return Number.isFinite(w) ? clamp(w as number, 0.2, MAX_WEIGHT) : 1;
  });
  const colManual = Array.from({ length: cols }, (_, i) => (Array.isArray(raw.colManual) ? raw.colManual[i] === true : false));
  const savedOrder = Array.isArray(raw.order) ? raw.order.filter((id): id is string => typeof id === "string" && ids.has(id)) : [];
  const order = [...new Set([...savedOrder, ...defs.map((d) => d.id)])];
  const panels = { ...base.panels };
  for (const def of defs) {
    const saved = raw.panels?.[def.id];
    const next = { ...panels[def.id] };
    if (saved) {
      if (typeof saved.visible === "boolean") next.visible = saved.visible;
      if (typeof saved.floating === "boolean") next.floating = saved.floating;
      for (const key of ["col", "weight", "x", "y", "width", "height"] as const) if (Number.isFinite(saved[key])) next[key] = saved[key];
    }
    next.col = clamp(Math.round(next.col), 0, cols - 1);
    next.weight = clamp(next.weight, MIN_WEIGHT, MAX_WEIGHT);
    next.width = Math.max(260, next.width);
    next.height = Math.max(160, next.height);
    panels[def.id] = next;
  }
  return { cols, density, fit, colWeights, colManual, order, panels };
}

export function applyPreset(layout: WorkspaceLayout, preset: Preset, defs: PanelDef[]): WorkspaceLayout {
  const known = new Set(defs.map((d) => d.id));
  const show = preset.show.filter((id) => known.has(id));
  const rest = layout.order.filter((id) => !show.includes(id));
  const panels = { ...layout.panels };
  let spread = 0;
  for (const def of defs) {
    const cur = panels[def.id];
    const on = show.includes(def.id);
    let col = cur.col;
    if (on) col = preset.place?.[def.id] ?? spread++ % preset.cols;
    panels[def.id] = {
      ...cur,
      visible: on,
      floating: false,
      col: clamp(col, 0, preset.cols - 1),
      weight: clamp(preset.weights?.[def.id] ?? 1, MIN_WEIGHT, MAX_WEIGHT),
    };
  }
  return {
    ...layout,
    cols: preset.cols,
    colWeights: Array.from({ length: preset.cols }, () => 1),
    colManual: Array.from({ length: preset.cols }, () => false),
    order: [...show, ...rest],
    panels,
  };
}

/** Put `id` just before `target`, in target's column. */
export function movePanel(layout: WorkspaceLayout, id: string, target: string): WorkspaceLayout {
  if (id === target || !layout.panels[target]) return layout;
  const order = layout.order.filter((x) => x !== id);
  const at = order.indexOf(target);
  if (at < 0) return layout;
  order.splice(at, 0, id);
  return { ...layout, order, panels: { ...layout.panels, [id]: { ...layout.panels[id], col: layout.panels[target].col, floating: false } } };
}

/** Send `id` to the end of a column. */
export function moveToCol(layout: WorkspaceLayout, id: string, col: number): WorkspaceLayout {
  const target = clamp(col, 0, layout.cols - 1);
  const order = [...layout.order.filter((x) => x !== id), id];
  return { ...layout, order, panels: { ...layout.panels, [id]: { ...layout.panels[id], col: target, floating: false } } };
}

export function setCols(layout: WorkspaceLayout, cols: number): WorkspaceLayout {
  const next = clamp(cols, MIN_COLS, MAX_COLS);
  const panels = Object.fromEntries(Object.entries(layout.panels).map(([id, p]) => [id, { ...p, col: Math.min(p.col, next - 1) }]));
  return {
    ...layout,
    cols: next,
    colWeights: Array.from({ length: next }, (_, i) => layout.colWeights[i] ?? 1),
    colManual: Array.from({ length: next }, (_, i) => layout.colManual[i] ?? false),
    panels,
  };
}

/** Set several panel weights at once (used when a divider is dragged); marks the column as manually sized. */
export function setColumnHeights(layout: WorkspaceLayout, col: number, weights: Record<string, number>): WorkspaceLayout {
  const panels = { ...layout.panels };
  for (const [id, w] of Object.entries(weights)) if (panels[id]) panels[id] = { ...panels[id], weight: clamp(w, MIN_WEIGHT, MAX_WEIGHT) };
  const colManual = [...layout.colManual];
  colManual[col] = true;
  return { ...layout, panels, colManual };
}

export function setColumnWidths(layout: WorkspaceLayout, weights: number[]): WorkspaceLayout {
  return { ...layout, colWeights: weights.map((w) => clamp(w, 0.2, MAX_WEIGHT)) };
}

export function setWeight(layout: WorkspaceLayout, id: string, weight: number): WorkspaceLayout {
  return { ...layout, panels: { ...layout.panels, [id]: { ...layout.panels[id], weight: clamp(weight, MIN_WEIGHT, MAX_WEIGHT) } } };
}

/** Presets by role. Ids that the page does not provide are ignored, so these are safe to extend. */
export const ROLE_PRESETS: Record<string, Preset> = {
  "Pit wall": {
    cols: 3,
    show: ["laps", "plan", "thermal", "vitals", "cells", "pack", "map"],
    place: { laps: 0, plan: 0, thermal: 1, vitals: 1, cells: 1, pack: 2, map: 2 },
    weights: { laps: 1.6, vitals: 1.3, map: 1.3 },
  },
  Strategy: {
    cols: 3,
    show: ["laps", "plan", "strategy", "energy", "pack", "thermal", "map"],
    place: { laps: 0, plan: 0, strategy: 1, energy: 1, pack: 2, thermal: 2, map: 2 },
    weights: { laps: 1.4 },
  },
  Electronics: {
    cols: 3,
    show: ["pack", "cells", "thermal", "temps", "vitals", "livedata", "gforce"],
    place: { pack: 0, cells: 0, thermal: 1, temps: 1, vitals: 2, livedata: 2, gforce: 2 },
  },
  "Driver coach": {
    cols: 3,
    show: ["laps", "status", "driver", "traction", "vitals", "gforce", "map", "plan"],
    place: { laps: 0, status: 0, driver: 1, traction: 1, vitals: 1, gforce: 2, map: 2, plan: 2 },
    weights: { laps: 1.4 },
  },
  Setup: {
    cols: 3,
    show: ["setup", "metadata", "status", "plan"],
    place: { setup: 0, metadata: 1, status: 2, plan: 2 },
    weights: { setup: 1.4 },
  },
  Focus: {
    cols: 2,
    show: ["laps", "thermal"],
    place: { laps: 0, thermal: 1 },
  },
};
