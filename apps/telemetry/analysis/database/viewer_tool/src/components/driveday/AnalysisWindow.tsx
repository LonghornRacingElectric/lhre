"use client";

import { useEffect, useRef, useState, type ReactNode, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { lsGet, lsSet } from "@/lib/driveday/storage";

let nextWindowZ = 900;
interface Position { floating: boolean; x: number; y: number; width: number; height: number }

/** Each analysis view can leave the dock without losing its playback subscription. */
export function AnalysisWindow({ id, title, children, onClose, onMove, layoutKey }: {
  id: string; title: string; layoutKey: string; children: ReactNode; onClose: () => void; onMove: (source: string, side?: "left" | "right") => void;
}) {
  const [position, setPosition] = useState<Position>(() => lsGet("analysis-window-" + id, { floating: false, x: 80, y: 80, width: 560, height: 440 }));
  const [dockSize, setDockSize] = useState<{ height?: number; span: number }>({ span: 24 });
  useEffect(() => { setDockSize({ span: 24 }); }, [layoutKey, position.floating]);
  const dockRoot = useRef<HTMLElement>(null);
  const dockDrag = useRef<{ x: number; y: number; width: number; height: number; available: number } | null>(null);
  const resizeDock = (width: number, height: number, available: number) => {
    const next = { height: Math.max(id === "driver" ? 320 : 180, Math.round(height)), span: Math.min(24, Math.max(Math.ceil(220 / available * 24), Math.round(width / available * 24))) };
    setDockSize(next); lsSet("analysis-dock-size-" + id, next);
  };
  const [z, setZ] = useState(900);
  const root = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const save = (next: Position) => { setPosition(next); lsSet("analysis-window-" + id, next); };
  useEffect(() => {
    const fit = () => setPosition(current => {
      const next = { ...current, width: Math.min(current.width, window.innerWidth - 16), height: Math.min(current.height, window.innerHeight - 16),
        x: Math.max(8, Math.min(current.x, window.innerWidth - Math.min(current.width, window.innerWidth - 16) - 8)),
        y: Math.max(8, Math.min(current.y, window.innerHeight - Math.min(current.height, window.innerHeight - 16) - 8)) };
      lsSet("analysis-window-" + id, next); return next;
    });
    fit(); window.addEventListener("resize", fit); return () => window.removeEventListener("resize", fit);
  }, [id]);
  useEffect(() => {
    const element = root.current;
    if (!position.floating || !element) return;
    const observer = new ResizeObserver(() => {
      const bounds = element.getBoundingClientRect();
      setPosition(current => {
        if (Math.abs(bounds.width - current.width) < 2 && Math.abs(bounds.height - current.height) < 2) return current;
        const next = { ...current, width: bounds.width, height: bounds.height }; lsSet("analysis-window-" + id, next); return next;
      });
    });
    observer.observe(element); return () => observer.disconnect();
  }, [id, position.floating]);
  const body = <section ref={dockRoot} style={!position.floating ? { height: dockSize.height, flex: dockSize.height ? "none" : undefined, gridColumn: "span " + dockSize.span, "--analysis-width": dockSize.height ? (dockSize.span / 24 * 100) + "%" : undefined } as CSSProperties : undefined} className={"pn panel analysis-window" + (!position.floating ? " is-docked" : "")} id={"pn-" + id} aria-label={title + " panel"}>
    <header className="pnh" title="Drag onto the left or right edge of another header to dock side by side" draggable={!position.floating}
      onDragStart={e => e.dataTransfer.setData("application/lapdog-analysis", id)}
      onDragOver={e => { if (e.dataTransfer.types.includes("application/lapdog-analysis")) e.preventDefault(); }}
      onDrop={e => { e.preventDefault(); const source = e.dataTransfer.getData("application/lapdog-analysis"); if (source && source !== id) { const bounds = e.currentTarget.getBoundingClientRect(); const fraction = (e.clientX - bounds.left) / bounds.width; onMove(source, fraction < 0.3 ? "left" : fraction > 0.7 ? "right" : undefined); } }}
      onPointerDown={e => {
        if (!position.floating || (e.target as HTMLElement).closest("button")) return;
        drag.current = { x: e.clientX, y: e.clientY, left: position.x, top: position.y }; e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={e => { if (drag.current) save({ ...position,
        x: Math.max(8, Math.min(window.innerWidth - position.width - 8, drag.current.left + e.clientX - drag.current.x)),
        y: Math.max(8, Math.min(window.innerHeight - position.height - 8, drag.current.top + e.clientY - drag.current.y)) }); }}
      onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
      <h3>{title}</h3><div className="analysis-window-actions">
        <button className="btn" type="button" aria-label={(position.floating ? "Dock " : "Float ") + title} onClick={() => save({ ...position, floating: !position.floating })}>{position.floating ? "Dock" : "Float"}</button>
        <button className="x" type="button" aria-label={"Close " + title} onClick={onClose}>×</button>
      </div>
    </header><div className="pnb" role="region" aria-label={title + " content"} tabIndex={0}>{children}</div>
    {!position.floating && <div className="analysis-dock-resize" role="separator" tabIndex={0} aria-label={"Resize " + title + " panel"}
      title="Drag to resize panel; arrow keys adjust size"
      onPointerDown={e => {
        const bounds = dockRoot.current!.getBoundingClientRect();
        dockDrag.current = { x: e.clientX, y: e.clientY, width: bounds.width, height: bounds.height, available: dockRoot.current!.parentElement!.clientWidth };
        e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId); e.preventDefault();
      }}
      onPointerMove={e => { const start = dockDrag.current; if (start) resizeDock(start.width + e.clientX - start.x, start.height + e.clientY - start.y, start.available); }}
      onPointerUp={() => { dockDrag.current = null; }} onPointerCancel={() => { dockDrag.current = null; }}
      onKeyDown={e => {
        if (!e.key.startsWith("Arrow")) return;
        const element = dockRoot.current!, bounds = element.getBoundingClientRect(), available = element.parentElement!.clientWidth;
        resizeDock(bounds.width + (e.key === "ArrowRight" ? available / 24 : e.key === "ArrowLeft" ? -available / 24 : 0), bounds.height + (e.key === "ArrowDown" ? 24 : e.key === "ArrowUp" ? -24 : 0), available);
        e.preventDefault(); e.stopPropagation();
      }} />}

  </section>;
  if (!position.floating) return body;
  return createPortal(<div ref={root} className="ddv analysis-floating" data-theme={document.querySelector(".ddv")?.getAttribute("data-theme") ?? undefined}
    style={{ left: position.x, top: position.y, width: position.width, height: position.height, zIndex: z }}
    onPointerDown={() => setZ(++nextWindowZ)}
    onPointerUp={() => { const bounds = root.current?.getBoundingClientRect(); if (bounds) save({ ...position, width: bounds.width, height: bounds.height }); }}>
    {body}
  </div>, document.body);
}
