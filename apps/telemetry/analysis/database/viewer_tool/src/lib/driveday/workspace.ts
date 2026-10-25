export const PANE_IDS = ["video", "track", "analysis"] as const;
export type PaneId = typeof PANE_IDS[number];
export interface PaneState { visible: boolean; floating: boolean; weight: number; x: number; y: number; width: number; height: number }
export interface WorkspaceLayout { order: PaneId[]; panes: Record<PaneId, PaneState> }
export interface SavedWorkspace { layout: WorkspaceLayout; timelineHeight: number; timelineMode: "full" | "track"; panel: string | null }
export function workspacePreset(name: "Driving review" | "Battery analysis" | "Video comparison"): SavedWorkspace {
  const layout = defaultWorkspace();
  if (name === "Battery analysis") { layout.panes.video.visible = false; layout.panes.analysis.weight = 2; layout.panes.track.weight = 1; }
  if (name === "Video comparison") { layout.panes.video.weight = 2; layout.panes.track.weight = 1.2; }
  return { layout, timelineHeight: 48, timelineMode: "full", panel: name === "Driving review" ? "driving" : name === "Battery analysis" ? "cells" : "compare" };
}
export function defaultWorkspace(): WorkspaceLayout {
  const panes = Object.fromEntries(PANE_IDS.map((id, i) => [id, { visible: true, floating: false, weight: id === "track" ? 1.5 : 1,
    x: 32 + i * 48, y: 90 + i * 32, width: id === "track" ? 640 : 420, height: 560 }])) as Record<PaneId, PaneState>;
  return { order: [...PANE_IDS], panes };
}
export function restoreWorkspace(raw: Partial<WorkspaceLayout> | null): WorkspaceLayout {
  const layout = defaultWorkspace();
  if (!raw) return layout;
  if (Array.isArray(raw.order) && raw.order.length === 3 && new Set(raw.order).size === 3 && raw.order.every(id => PANE_IDS.includes(id))) layout.order = raw.order;
  for (const id of PANE_IDS) {
    const saved = raw.panes?.[id];
    if (!saved) continue;
    for (const key of ["visible", "floating"] as const) if (typeof saved[key] === "boolean") layout.panes[id][key] = saved[key];
    for (const key of ["weight", "x", "y", "width", "height"] as const) if (Number.isFinite(saved[key])) layout.panes[id][key] = saved[key];
    layout.panes[id].weight = Math.max(0.25, Math.min(5, layout.panes[id].weight));
    layout.panes[id].width = Math.max(260, layout.panes[id].width);
    layout.panes[id].height = Math.max(280, layout.panes[id].height);
  }
  return layout;
}
export function movePane(layout: WorkspaceLayout, id: PaneId, target: PaneId): WorkspaceLayout {
  const order = [...layout.order];
  const from = order.indexOf(id), to = order.indexOf(target);
  order.splice(from, 1); order.splice(to, 0, id);
  return { ...layout, order };
}
export function resizePair(layout: WorkspaceLayout, left: PaneId, right: PaneId, fraction: number): WorkspaceLayout {
  const total = layout.panes[left].weight + layout.panes[right].weight;
  const ratio = Math.max(0.2, Math.min(0.8, fraction));
  return { ...layout, panes: { ...layout.panes, [left]: { ...layout.panes[left], weight: total * ratio }, [right]: { ...layout.panes[right], weight: total * (1 - ratio) } } };
}
