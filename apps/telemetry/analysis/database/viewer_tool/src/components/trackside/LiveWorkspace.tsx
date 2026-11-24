"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import {
  DENSITIES,
  MAX_COLS,
  MIN_COLS,
  ROLE_PRESETS,
  STORAGE_LAYOUT,
  STORAGE_PRESETS,
  applyPreset,
  defaultLayout,
  moveToCol,
  movePanel,
  restoreLayout,
  setCols,
  setColumnHeights,
  setColumnWidths,
  setWeight,
  type Density,
  type PanelDef,
  type PanelState,
  type Preset,
  type WorkspaceLayout,
} from "@/lib/trackside/liveWorkspace";

export interface LivePanel extends PanelDef {
  /** Rendered content. Null means "not available right now" and the panel is skipped. */
  node: ReactNode | null;
}

/** The workspace is desktop-only. Below this width the page keeps its original stacked layout. */
const DESKTOP_QUERY = "(min-width: 1025px)";
/** Content of a panel only shrinks (never grows) to fit its box; below the minimum it scrolls instead. */
const MIN_SCALE = 0.8;
const MAX_SCALE = 1;
/** Smallest height of the whole workspace; below this the page scrolls rather than crush panels. */
const MIN_FIT_HEIGHT = 240;

const COLUMN_NAMES: Record<number, string[]> = {
  1: ["Panels"],
  2: ["Left", "Right"],
  3: ["Left", "Middle", "Right"],
  4: ["Left", "Mid-left", "Mid-right", "Right"],
};
const columnName = (cols: number, c: number) => (COLUMN_NAMES[cols] ?? [])[c] ?? `Column ${c + 1}`;

function read<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}
function write(key: string, value: unknown) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage can be blocked; the layout just won't persist */
  }
}

function useDesktop(): boolean | null {
  const [match, setMatch] = useState<boolean | null>(null);
  useEffect(() => {
    const mq = window.matchMedia(DESKTOP_QUERY);
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return match;
}

/** Movable, resizable, hideable panels for the live page. Phones and tablets get `fallback` untouched. */
export function LiveWorkspace({ panels, fallback }: { panels: LivePanel[]; fallback: ReactNode }) {
  const desktop = useDesktop();
  // Before the first media-query read we render the fallback so server and client markup match.
  if (!desktop) return <>{fallback}</>;
  return <DesktopWorkspace panels={panels} />;
}

function DesktopWorkspace({ panels }: { panels: LivePanel[] }) {
  const defs: PanelDef[] = useMemo(
    () => panels.map(({ id, title, defaultVisible, defaultCol, defaultWeight }) => ({ id, title, defaultVisible, defaultCol, defaultWeight })),
    // Defs only change if the set of ids changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [panels.map((p) => p.id).join("|")],
  );
  const [layout, setLayout] = useState<WorkspaceLayout>(() => restoreLayout(read<Partial<WorkspaceLayout> | null>(STORAGE_LAYOUT, null), defs));
  const [saved, setSaved] = useState<Record<string, Preset>>(() => read<Record<string, Preset>>(STORAGE_PRESETS, {}));
  const [presetName, setPresetName] = useState("");
  const [z, setZ] = useState<Record<string, number>>({});
  const nextZ = useRef(100);
  const dragId = useRef<string | null>(null);
  // Briefly outlines a panel that was just opened or moved, so you can see which section it landed in.
  const [flash, setFlash] = useState<string | null>(null);
  const flashTimer = useRef<number | undefined>(undefined);
  const flashPanel = (id: string) => {
    window.clearTimeout(flashTimer.current);
    setFlash(id);
    flashTimer.current = window.setTimeout(() => setFlash(null), 1600);
  };
  useEffect(() => () => window.clearTimeout(flashTimer.current), []);

  // Re-merge if the available panel set changes (a new panel ships, for example).
  useEffect(() => {
    setLayout((cur) => restoreLayout(cur, defs));
  }, [defs]);

  const update = useCallback((fn: (cur: WorkspaceLayout) => WorkspaceLayout) => {
    setLayout((cur) => {
      const next = fn(cur);
      write(STORAGE_LAYOUT, next);
      return next;
    });
  }, []);
  const patch = (id: string, changes: Partial<PanelState>) =>
    update((cur) => ({ ...cur, panels: { ...cur.panels, [id]: { ...cur.panels[id], ...changes } } }));

  // Keep floating panels reachable after the window shrinks.
  useEffect(() => {
    const fit = () =>
      setLayout((cur: WorkspaceLayout) => {
        let changed = false;
        const next: Record<string, PanelState> = { ...cur.panels };
        for (const [id, p] of Object.entries(cur.panels) as [string, PanelState][]) {
          if (!p.floating) continue;
          const width = Math.min(p.width, window.innerWidth - 16);
          const x = Math.max(8, Math.min(p.x, window.innerWidth - width - 8));
          const y = Math.max(8, Math.min(p.y, window.innerHeight - 80));
          if (width !== p.width || x !== p.x || y !== p.y) {
            next[id] = { ...p, width, x, y };
            changed = true;
          }
        }
        return changed ? { ...cur, panels: next } : cur;
      });
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);

  const byId = useMemo(() => new Map(panels.map((p) => [p.id, p])), [panels]);
  const ordered = layout.order.map((id) => byId.get(id)).filter((p): p is LivePanel => !!p);
  const available = ordered.filter((p) => p.node != null);
  const visible = available.filter((p) => layout.panels[p.id].visible);
  const floating = visible.filter((p) => layout.panels[p.id].floating);
  const columns = Array.from({ length: layout.cols }, (_, c) => visible.filter((p) => !layout.panels[p.id].floating && layout.panels[p.id].col === c));
  const hiddenCount = available.length - visible.length;

  // Fit the columns to what is left of the window under the page header.
  const colsRef = useRef<HTMLDivElement>(null);
  const [fitHeight, setFitHeight] = useState(0);
  useEffect(() => {
    if (!layout.fit) return;
    const measure = () => {
      const el = colsRef.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top + window.scrollY;
      setFitHeight(Math.max(MIN_FIT_HEIGHT, Math.round(window.innerHeight - top - 10)));
    };
    measure();
    window.addEventListener("resize", measure);
    // The page header above can change height (wrapping banners, status rows).
    const ro = new ResizeObserver(measure);
    const shell = colsRef.current?.closest(".liveShell");
    if (shell) for (const child of Array.from(shell.children)) if (!child.contains(colsRef.current)) ro.observe(child);
    return () => {
      window.removeEventListener("resize", measure);
      ro.disconnect();
    };
  }, [layout.fit]);

  const raise = (id: string) => setZ((cur) => ({ ...cur, [id]: ++nextZ.current }));
  const roles = Object.keys(ROLE_PRESETS);
  const savePreset = () => {
    const name = presetName.trim();
    if (!name) return;
    const show = layout.order.filter((id) => layout.panels[id]?.visible);
    const preset: Preset = {
      cols: layout.cols,
      show,
      place: Object.fromEntries(show.map((id) => [id, layout.panels[id].col])),
      weights: Object.fromEntries(show.map((id) => [id, layout.panels[id].weight])),
    };
    const next = { ...saved, [name]: preset };
    setSaved(next);
    write(STORAGE_PRESETS, next);
    setPresetName("");
  };
  const setAllVisible = (on: boolean) =>
    update((c) => ({ ...c, panels: Object.fromEntries((Object.entries(c.panels) as [string, PanelState][]).map(([id, st]) => [id, { ...st, visible: on }])) }));

  const fitStyle: CSSProperties | undefined = layout.fit && fitHeight ? { height: fitHeight } : undefined;

  return (
    <div className="tlw" data-density={layout.density} data-fit={layout.fit ? "1" : "0"}>
      <div className="tlw-bar" role="toolbar" aria-label="Workspace layout">
        <Dropdown label={`Panels ${visible.length}/${available.length}`}>
          <div className="tlw-groups">
            {Array.from({ length: layout.cols }, (_, c) => {
              const inCol = available.filter((p) => layout.panels[p.id].col === c);
              return (
                <div className="tlw-group" key={c}>
                  <div className="tlw-group-head">
                    <b>{columnName(layout.cols, c)}</b>
                    <span>{inCol.filter((p) => layout.panels[p.id].visible).length} open</span>
                  </div>
                  {inCol.length ? null : <div className="tlw-group-empty">No panels here</div>}
                  {inCol.map((p) => {
                    const st = layout.panels[p.id];
                    return (
                      <div className={"tlw-item" + (st.visible ? " on" : "")} key={p.id}>
                        <label>
                          <input
                            type="checkbox"
                            checked={st.visible}
                            onChange={() => {
                              patch(p.id, { visible: !st.visible });
                              if (!st.visible) flashPanel(p.id);
                            }}
                          />
                          {p.title}
                          {st.visible && st.floating ? <em>floating</em> : null}
                        </label>
                        <span className="tlw-item-move">
                          <button type="button" aria-label={`Move ${p.title} to the left section`} title="Open in the section to the left" disabled={st.col <= 0} onClick={() => { update((cur) => moveToCol(cur, p.id, st.col - 1)); if (st.visible) flashPanel(p.id); }}>‹</button>
                          <button type="button" aria-label={`Move ${p.title} to the right section`} title="Open in the section to the right" disabled={st.col >= layout.cols - 1} onClick={() => { update((cur) => moveToCol(cur, p.id, st.col + 1)); if (st.visible) flashPanel(p.id); }}>›</button>
                        </span>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
          <div className="tlw-row">
            <button type="button" onClick={() => setAllVisible(true)}>Show all</button>
            <button type="button" onClick={() => setAllVisible(false)}>Hide all</button>
          </div>
        </Dropdown>
        <Dropdown label="Layouts">
          <small>Roles</small>
          {roles.map((name) => (
            <button key={name} type="button" onClick={() => update((c) => applyPreset(c, ROLE_PRESETS[name], defs))}>{name}</button>
          ))}
          {Object.keys(saved).length ? <small>Saved</small> : null}
          {Object.entries(saved).map(([name, preset]) => (
            <div className="tlw-row" key={name}>
              <button type="button" onClick={() => update((c) => applyPreset(c, preset, defs))}>{name}</button>
              <button
                type="button"
                className="tlw-x"
                aria-label={`Delete layout ${name}`}
                onClick={() => {
                  const next = { ...saved };
                  delete next[name];
                  setSaved(next);
                  write(STORAGE_PRESETS, next);
                }}
              >
                ×
              </button>
            </div>
          ))}
          <small>Save current</small>
          <div className="tlw-row">
            <input aria-label="Layout name" placeholder="Name" value={presetName} onChange={(e) => setPresetName(e.target.value)} />
            <button type="button" disabled={!presetName.trim()} onClick={savePreset}>Save</button>
          </div>
        </Dropdown>
        <Dropdown label="More">
          <small>Sections</small>
          <span className="tlw-step">
            <button type="button" aria-label="Fewer sections" disabled={layout.cols <= MIN_COLS} onClick={() => update((c) => setCols(c, c.cols - 1))}>−</button>
            <b>{layout.cols}</b>
            <button type="button" aria-label="More sections" disabled={layout.cols >= MAX_COLS} onClick={() => update((c) => setCols(c, c.cols + 1))}>+</button>
          </span>
          <small>Text size</small>
          <span className="tlw-seg" role="group" aria-label="Text size">
            {DENSITIES.map((d: Density) => (
              <button key={d} type="button" aria-pressed={layout.density === d} onClick={() => update((c) => ({ ...c, density: d }))}>
                {d === "compact" ? "S" : d === "comfortable" ? "M" : "L"}
              </button>
            ))}
          </span>
          <label className="tlw-check">
            <input type="checkbox" checked={layout.fit} onChange={() => update((c) => ({ ...c, fit: !c.fit }))} />
            Fit to screen (no page scroll)
          </label>
          <button type="button" onClick={() => update(() => defaultLayout(defs))}>Reset layout</button>
        </Dropdown>
        {hiddenCount ? <span className="tlw-note">{hiddenCount} hidden</span> : null}
      </div>

      <div ref={colsRef} className="tlw-cols" style={fitStyle}>
        {columns.map((list, c) => (
          <Fragment key={c}>
            {c > 0 ? (
              <Splitter
                axis="x"
                label={`Resize ${columnName(layout.cols, c - 1)} and ${columnName(layout.cols, c)} sections`}
                onStart={() => {
                  const els = Array.from(colsRef.current?.querySelectorAll<HTMLElement>(":scope > .tlw-col") ?? []);
                  return els.map((el) => el.getBoundingClientRect().width);
                }}
                onMove={(start, d) => {
                  const a = c - 1;
                  const total = start[a] + start[c];
                  const left = Math.max(120, Math.min(total - 120, start[a] + d));
                  const next = start.map((w) => w / 100);
                  next[a] = left / 100;
                  next[c] = (total - left) / 100;
                  update((cur) => setColumnWidths(cur, next));
                }}
              />
            ) : null}
            <div
              className="tlw-col"
              data-col={c}
              style={{ flex: `${layout.colWeights[c] ?? 1} 1 0` }}
              onDragOver={(e) => {
                if (e.dataTransfer.types.includes("application/trackside-panel")) e.preventDefault();
              }}
              onDrop={(e) => {
                const source = e.dataTransfer.getData("application/trackside-panel");
                if (!source) return;
                e.preventDefault();
                update((cur) => moveToCol(cur, source, c));
              }}
            >
              {list.map((p, i) => (
                <Fragment key={p.id}>
                  {layout.fit && i > 0 ? (
                    <Splitter
                      axis="y"
                      label={`Resize ${list[i - 1].title} and ${p.title}`}
                      onStart={() => list.map((q) => document.querySelector<HTMLElement>(`.tlw-pane[data-panel="${q.id}"]`)?.getBoundingClientRect().height ?? 100)}
                      onMove={(start, d) => {
                        const a = i - 1;
                        const total = start[a] + start[i];
                        const top = Math.max(70, Math.min(total - 70, start[a] + d));
                        const heights = [...start];
                        heights[a] = top;
                        heights[i] = total - top;
                        update((cur) => setColumnHeights(cur, c, Object.fromEntries(list.map((q, k) => [q.id, heights[k] / 100]))));
                      }}
                    />
                  ) : null}
                  <Pane
                    def={p}
                    state={layout.panels[p.id]}
                    cols={layout.cols}
                    fit={layout.fit}
                    manual={!!layout.colManual[c]}
                    z={z[p.id] ?? 1}
                    dragId={dragId}
                    flash={flash === p.id}
                    onRaise={() => raise(p.id)}
                    onPatch={(ch) => patch(p.id, ch)}
                    onWeight={(d) => update((cur) => setWeight(cur, p.id, cur.panels[p.id].weight + d))}
                    onCol={(d) => update((cur) => moveToCol(cur, p.id, cur.panels[p.id].col + d))}
                    onDrop={(source) => update((cur) => movePanel(cur, source, p.id))}
                  />
                </Fragment>
              ))}
              {!list.length ? <div className="tlw-empty">Drop a panel here</div> : null}
            </div>
          </Fragment>
        ))}
      </div>
      {!visible.length ? <div className="tlw-empty">Every panel is hidden. Open some from the Panels menu.</div> : null}
      {floating.map((p) => (
        <Pane
          key={p.id}
          def={p}
          state={layout.panels[p.id]}
          cols={layout.cols}
          fit={layout.fit}
          z={z[p.id] ?? 1}
          dragId={dragId}
          flash={flash === p.id}
          onRaise={() => raise(p.id)}
          onPatch={(ch) => patch(p.id, ch)}
          onWeight={() => undefined}
          onCol={() => undefined}
          onDrop={() => undefined}
        />
      ))}
    </div>
  );
}

function Pane({
  def,
  state,
  cols,
  fit,
  manual = false,
  z,
  dragId,
  flash,
  onPatch,
  onWeight,
  onCol,
  onDrop,
  onRaise,
}: {
  def: LivePanel;
  state: PanelState;
  cols: number;
  fit: boolean;
  manual?: boolean;
  z: number;
  dragId: { current: string | null };
  flash: boolean;
  onPatch: (c: Partial<PanelState>) => void;
  onWeight: (delta: number) => void;
  onCol: (delta: number) => void;
  onDrop: (source: string) => void;
  onRaise: () => void;
}) {
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const size = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const resizeTo = (w: number, h: number) =>
    onPatch({
      width: Math.max(260, Math.min(window.innerWidth - state.x - 8, w)),
      height: Math.max(160, Math.min(window.innerHeight - state.y - 8, h)),
    });

  // Shrink the content to fit its box (down to MIN_SCALE) instead of scrolling, when fitting to the screen.
  useEffect(() => {
    const body = bodyRef.current;
    if (!body) return;
    const inner = body.firstElementChild as HTMLElement | null;
    if (!inner || !fit || state.floating) {
      if (inner) {
        inner.style.zoom = "";
        delete inner.dataset.scale;
      }
      return;
    }
    let frame = 0;
    // Height the content wants at a given zoom, measured with the stretch rules switched off.
    const measure = (zoom: number) => {
      inner.style.zoom = String(zoom);
      body.dataset.measure = "1";
      const h = inner.getBoundingClientRect().height;
      delete body.dataset.measure;
      return h;
    };
    const apply = () => {
      frame = 0;
      const room = body.getBoundingClientRect().height;
      if (room <= 0) return;
      let z = 1;
      let h = measure(z);
      // Zoom reflows the content (narrower layout width), so refine a few times.
      for (let i = 0; i < 4 && h > 0; i++) {
        const next = Math.max(MIN_SCALE, Math.min(MAX_SCALE, z * (room / h) * 0.985));
        if (Math.abs(next - z) < 0.01) break;
        z = next;
        h = measure(z);
      }
      if (h > room) z = Math.max(MIN_SCALE, z * (room / h) * 0.985);
      const prev = Number(inner.dataset.scale || 1);
      // Keep the last value when the change is tiny, so live data does not make panels jitter.
      const final = Math.abs(z - prev) < 0.03 ? prev : z;
      inner.dataset.scale = String(final);
      inner.style.zoom = Math.abs(final - 1) < 0.02 ? "" : final.toFixed(3);
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(apply);
    };
    schedule();
    const ro = new ResizeObserver(schedule);
    ro.observe(body);
    // Live data changes content height without resizing the box; recheck about once a second.
    const timer = window.setInterval(schedule, 1000);
    return () => {
      ro.disconnect();
      window.clearInterval(timer);
      if (frame) cancelAnimationFrame(frame);
      inner.style.zoom = "";
      delete inner.dataset.scale;
    };
  }, [fit, state.floating, state.weight, state.col, cols]);

  const style: CSSProperties = state.floating
    ? { left: state.x, top: state.y, width: state.width, height: state.height, zIndex: 200 + z }
    : fit
      ? { flex: manual ? `${state.weight} 1 0` : `${state.weight} 1 auto` }
      : { flex: `${state.weight} 0 auto` };

  return (
    <section
      className={"tlw-pane" + (state.floating ? " is-floating" : "") + (flash ? " is-flash" : "")}
      data-panel={def.id}
      style={style}
      aria-label={def.title + " panel"}
      onPointerDown={() => state.floating && onRaise()}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("application/trackside-panel")) e.preventDefault();
      }}
      onDrop={(e) => {
        const source = e.dataTransfer.getData("application/trackside-panel");
        if (!source) return;
        e.preventDefault();
        e.stopPropagation();
        onDrop(source);
      }}
    >
      <header
        className="tlw-head"
        draggable={!state.floating}
        title={state.floating ? "Drag to move" : "Drag onto another panel or section to move"}
        onDragStart={(e) => {
          dragId.current = def.id;
          e.dataTransfer.setData("application/trackside-panel", def.id);
          e.dataTransfer.effectAllowed = "move";
        }}
        onPointerDown={(e) => {
          if (!state.floating || (e.target as HTMLElement).closest("button")) return;
          drag.current = { x: e.clientX, y: e.clientY, ox: state.x, oy: state.y };
          e.currentTarget.setPointerCapture(e.pointerId);
          e.preventDefault();
        }}
        onPointerMove={(e) => {
          if (!drag.current) return;
          onPatch({
            x: Math.max(8, Math.min(window.innerWidth - Math.min(state.width, window.innerWidth - 16) - 8, drag.current.ox + e.clientX - drag.current.x)),
            y: Math.max(8, Math.min(window.innerHeight - 60, drag.current.oy + e.clientY - drag.current.y)),
          });
        }}
        onPointerUp={() => (drag.current = null)}
        onPointerCancel={() => (drag.current = null)}
      >
        <span className="tlw-grip" aria-hidden="true">⠿</span>
        <span className="tlw-title">{def.title}</span>
        <span className="tlw-head-actions">
          {!state.floating ? (
            <>
              <button type="button" aria-label={`Move ${def.title} left`} title="Section left" disabled={state.col <= 0} onClick={() => onCol(-1)}>‹</button>
              <button type="button" aria-label={`Move ${def.title} right`} title="Section right" disabled={state.col >= cols - 1} onClick={() => onCol(1)}>›</button>
              <button type="button" aria-label={`Shorter ${def.title}`} title="Shorter" onClick={() => onWeight(-0.3)}>▴</button>
              <button type="button" aria-label={`Taller ${def.title}`} title="Taller" onClick={() => onWeight(0.3)}>▾</button>
            </>
          ) : null}
          <button
            type="button"
            aria-label={(state.floating ? "Dock " : "Float ") + def.title}
            title={state.floating ? "Return to its section" : "Float this panel"}
            onClick={() =>
              onPatch({
                floating: !state.floating,
                x: Math.max(8, Math.min(state.x, window.innerWidth - 300)),
                y: Math.max(8, Math.min(state.y, window.innerHeight - 120)),
              })
            }
          >
            {state.floating ? "Dock" : "Float"}
          </button>
          <button type="button" aria-label={`Hide ${def.title}`} title="Hide" onClick={() => onPatch({ visible: false })}>×</button>
        </span>
      </header>
      <div className="tlw-body" ref={bodyRef}>
        <div className="tlw-inner">{def.node}</div>
      </div>
      {state.floating ? (
        <div
          className="tlw-resize"
          role="separator"
          aria-label={`Resize ${def.title}`}
          tabIndex={0}
          onPointerDown={(e) => {
            size.current = { x: e.clientX, y: e.clientY, w: state.width, h: state.height };
            e.currentTarget.setPointerCapture(e.pointerId);
            e.preventDefault();
          }}
          onPointerMove={(e) => size.current && resizeTo(size.current.w + e.clientX - size.current.x, size.current.h + e.clientY - size.current.y)}
          onPointerUp={() => (size.current = null)}
          onPointerCancel={() => (size.current = null)}
          onKeyDown={(e) => {
            if (!e.key.startsWith("Arrow")) return;
            resizeTo(state.width + (e.key === "ArrowRight" ? 24 : e.key === "ArrowLeft" ? -24 : 0), state.height + (e.key === "ArrowDown" ? 24 : e.key === "ArrowUp" ? -24 : 0));
            e.preventDefault();
          }}
        />
      ) : null}
    </section>
  );
}

/** A button that opens a small popover. Closes on an outside click or Escape. */
function Dropdown({ label, children }: { label: ReactNode; children: ReactNode }) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const close = (e: Event) => {
      const d = ref.current;
      if (d?.open && !d.contains(e.target as Node)) d.open = false;
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape" && ref.current?.open) ref.current.open = false;
    };
    document.addEventListener("click", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", esc);
    };
  }, []);
  return (
    <details className="tlw-menu" ref={ref}>
      <summary>{label} ▾</summary>
      <div className="tlw-pop">{children}</div>
    </details>
  );
}

/** A draggable divider. `onStart` measures sizes at drag start; `onMove` gets those sizes and the pixel offset. */
function Splitter({
  axis,
  label,
  onStart,
  onMove,
}: {
  axis: "x" | "y";
  label: string;
  onStart: () => number[];
  onMove: (start: number[], delta: number) => void;
}) {
  const drag = useRef<{ pos: number; start: number[] } | null>(null);
  const begin = (pos: number) => {
    drag.current = { pos, start: onStart() };
  };
  return (
    <div
      className={"tlw-split tlw-split-" + axis}
      role="separator"
      aria-orientation={axis === "x" ? "vertical" : "horizontal"}
      aria-label={label}
      title="Drag to resize"
      tabIndex={0}
      onPointerDown={(e) => {
        begin(axis === "x" ? e.clientX : e.clientY);
        e.currentTarget.setPointerCapture(e.pointerId);
        e.preventDefault();
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (d) onMove(d.start, (axis === "x" ? e.clientX : e.clientY) - d.pos);
      }}
      onPointerUp={() => (drag.current = null)}
      onPointerCancel={() => (drag.current = null)}
      onKeyDown={(e) => {
        const keys = axis === "x" ? ["ArrowLeft", "ArrowRight"] : ["ArrowUp", "ArrowDown"];
        if (!keys.includes(e.key)) return;
        const start = onStart();
        onMove(start, e.key === keys[0] ? -24 : 24);
        e.preventDefault();
      }}
    />
  );
}
