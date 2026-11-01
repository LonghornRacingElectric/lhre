"use client";

import { useEffect, useRef, type ReactNode } from "react";
import type { PaneId, PaneState } from "@/lib/driveday/workspace";

export function WorkspacePane({ id, title, state, order, z, children, headerControls, onPatch, onMove, onDrop, onRaise }: {
  id: PaneId; title: string; state: PaneState; order: number; z: number; children: ReactNode; headerControls?: ReactNode;
  onPatch: (patch: Partial<PaneState>) => void; onMove: (direction: number) => void;
  onDrop: (source: PaneId) => void; onRaise: () => void;
}) {
  const element = useRef<HTMLElement>(null);
  const patchRef = useRef(onPatch); patchRef.current = onPatch;
  const stateRef = useRef(state); stateRef.current = state;
  const drag = useRef<{ x: number; y: number; originX: number; originY: number } | null>(null);
  const resize = useRef<{ x: number; y: number; width: number; height: number } | null>(null);
  const resizeTo = (width: number, height: number) => onPatch({ width: Math.max(260, Math.min(window.innerWidth - state.x - 8, width)), height: Math.max(280, Math.min(window.innerHeight - state.y - 8, height)) });
  useEffect(() => {
    const el = element.current;
    if (!el || !state.floating || !state.visible) return;
    const observer = new ResizeObserver(() => {
      const width = Math.round(el.getBoundingClientRect().width), height = Math.round(el.getBoundingClientRect().height);
      const current = stateRef.current;
      if (Math.abs(width - current.width) > 2 || Math.abs(height - current.height) > 2) patchRef.current({ width, height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [state.floating, state.visible]);
  return <section ref={element} className={"workspace-pane pane-" + id + (state.floating ? " is-floating" : "")}
    hidden={!state.visible} aria-label={title + " window"}
    style={state.floating ? { left: state.x, top: state.y, width: state.width, height: state.height, zIndex: z } : { order: order * 2, flex: state.weight + " 1 0" }}
    onPointerDown={() => { if (state.floating) onRaise(); }}>
    <header className="pane-header" draggable={!state.floating}
      title={state.floating ? "Drag this header to move the window; resize from its bottom-right corner" : "Drag onto another window header to reorder"}
      onDragStart={e => { e.dataTransfer.setData("application/lapdog-pane", id); e.dataTransfer.effectAllowed = "move"; }}
      onDragOver={e => { if (e.dataTransfer.types.includes("application/lapdog-pane")) e.preventDefault(); }}
      onDrop={e => { e.preventDefault(); const source = e.dataTransfer.getData("application/lapdog-pane"); if (["video", "track", "analysis"].includes(source)) onDrop(source as PaneId); }}
      onPointerDown={e => {
        if (!state.floating || (e.target as HTMLElement).closest("button, summary, .menu")) return;
        drag.current = { x: e.clientX, y: e.clientY, originX: state.x, originY: state.y };
        e.currentTarget.setPointerCapture(e.pointerId); e.preventDefault();
      }}
      onPointerMove={e => {
        if (!drag.current) return;
        onPatch({ x: Math.max(8, Math.min(window.innerWidth - Math.min(state.width, window.innerWidth - 16) - 8, drag.current.originX + e.clientX - drag.current.x)),
          y: Math.max(8, Math.min(window.innerHeight - 60, drag.current.originY + e.clientY - drag.current.y)) });
      }} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
      <strong aria-label={title + " drag handle"}><span aria-hidden="true">⠿</span></strong>
      {headerControls && <div className="pane-header-controls" onPointerDown={e => e.stopPropagation()} onDragStart={e => { e.preventDefault(); e.stopPropagation(); }}>{headerControls}</div>}
      <div className="pane-actions">
        {!state.floating && <><button aria-label={"Move " + title + " left"} title="Move left" disabled={order === 0} onClick={() => onMove(-1)}>←</button><button aria-label={"Move " + title + " right"} title="Move right" disabled={order === 2} onClick={() => onMove(1)}>→</button></>}
        <button aria-label={(state.floating ? "Dock " : "Float ") + title} title={state.floating ? "Return to docked layout" : "Float this window"} onClick={() => onPatch({ floating: !state.floating, x: Math.max(8, Math.min(state.x, window.innerWidth - 280)), y: Math.max(8, Math.min(state.y, window.innerHeight - 100)), width: Math.min(state.width, window.innerWidth - 16), height: Math.min(state.height, window.innerHeight - 16) })}>{state.floating ? "Dock" : "Float"}</button>
        <button aria-label={"Hide " + title} title="Hide this window" onClick={() => onPatch({ visible: false })}>×</button>
      </div>
    </header>
    <div className="pane-body">{children}</div>
    {state.floating && <div className="pane-resize" role="separator" aria-label={"Resize " + title + " window"} tabIndex={0} title="Drag to resize this floating window"
      onPointerDown={e => { resize.current = { x: e.clientX, y: e.clientY, width: state.width, height: state.height }; e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId); e.preventDefault(); }}
      onPointerMove={e => { if (resize.current) resizeTo(resize.current.width + e.clientX - resize.current.x, resize.current.height + e.clientY - resize.current.y); }}
      onPointerUp={() => { resize.current = null; }} onPointerCancel={() => { resize.current = null; }}
      onKeyDown={e => { if (!e.key.startsWith("Arrow")) return; resizeTo(state.width + (e.key === "ArrowRight" ? 24 : e.key === "ArrowLeft" ? -24 : 0), state.height + (e.key === "ArrowDown" ? 24 : e.key === "ArrowUp" ? -24 : 0)); e.preventDefault(); e.stopPropagation(); }} />}
  </section>;
}

export function PaneDivider({ left, right, order, fraction, onResize }: { left: PaneId; right: PaneId; order: number; fraction: number; onResize: (fraction: number) => void }) {
  const drag = useRef<{ left: number; width: number } | null>(null);
  const measure = (element: HTMLElement) => {
    const parent = element.parentElement;
    const a = parent?.querySelector<HTMLElement>(".pane-" + left)?.getBoundingClientRect();
    const b = parent?.querySelector<HTMLElement>(".pane-" + right)?.getBoundingClientRect();
    return a && b ? { left: a.left, width: a.width + b.width } : null;
  };
  return <div className="pane-divider" style={{ order: order * 2 + 1 }} role="separator" aria-orientation="vertical"
    aria-label={"Resize " + left + " and " + right} aria-valuemin={20} aria-valuemax={80} aria-valuenow={Math.round(fraction * 100)} tabIndex={0}
    onPointerDown={e => { drag.current = measure(e.currentTarget); e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId); e.preventDefault(); }}
    onPointerMove={e => { if (drag.current) onResize((e.clientX - drag.current.left) / drag.current.width); }}
    onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}
    onDoubleClick={() => onResize(0.5)} onKeyDown={e => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const pair = measure(e.currentTarget);
      const width = e.currentTarget.parentElement?.querySelector<HTMLElement>(".pane-" + left)?.clientWidth;
      if (pair && width) onResize((width + (e.key === "ArrowLeft" ? -24 : 24)) / pair.width);
      e.preventDefault(); e.stopPropagation();
    }} />;
}
