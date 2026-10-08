"use client";

import { useRef } from "react";
import { lsDel, lsSet } from "@/lib/storage";

export const DEFAULT_DOCK = 520;
const MIN_DOCK = 300;

/** The draggable divider between the map and the panels. Reports the panel width in pixels. */
export function Split({ main, dock, onDock }: { main: React.RefObject<HTMLElement | null>; dock: number; onDock: (px: number) => void }) {
  const dragging = useRef(false);
  const apply = (px: number) => {
    const w = main.current?.getBoundingClientRect().width ?? 1200;
    const next = Math.max(MIN_DOCK, Math.min(Math.max(MIN_DOCK + 100, w * 0.75), px));
    lsSet("dockpx", Math.round(next));
    onDock(next);
  };
  const end = () => {
    dragging.current = false;
    document.body.classList.remove("resizing");
  };
  return (
    <div
      className="split"
      role="separator"
      aria-orientation="vertical"
      aria-label="Drag to resize the map and the panels. Arrow keys also work."
      aria-valuenow={Math.round(dock)}
      tabIndex={0}
      title="Drag to resize. Double-click to reset."
      onPointerDown={(e) => {
        dragging.current = true;
        e.currentTarget.setPointerCapture(e.pointerId);
        document.body.classList.add("resizing");
        e.preventDefault();
      }}
      onPointerMove={(e) => {
        if (dragging.current && main.current) apply(main.current.getBoundingClientRect().right - e.clientX - 6);
      }}
      onPointerUp={end}
      onPointerCancel={end}
      onDoubleClick={() => {
        lsDel("dockpx");
        onDock(DEFAULT_DOCK);
      }}
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft") {
          e.preventDefault();
          apply(dock + 24);
        } else if (e.key === "ArrowRight") {
          e.preventDefault();
          apply(dock - 24);
        }
      }}
    />
  );
}
