"use client";

import { useEffect, useRef, type ReactNode } from "react";

/** A button that opens a small popover. Closes on an outside click or Escape. */
export function Menu({ label, children, wide = false, chip = false, onToggle }: { label: ReactNode; children: ReactNode; wide?: boolean; chip?: boolean; onToggle?: (open: boolean) => void }) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const close = (e: Event) => {
      const d = ref.current;
      if (d && d.open && !d.contains(e.target as Node)) d.open = false;
    };
    const esc = (e: KeyboardEvent) => {
      const d = ref.current;
      if (e.key === "Escape" && d && d.open) d.open = false;
    };
    document.addEventListener("click", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", esc);
    };
  }, []);
  return (
    <details className="menu" ref={ref} onToggle={(e) => onToggle?.((e.target as HTMLDetailsElement).open)}>
      <summary className={chip ? "chip" : "btn"}>{label}</summary>
      <div className={"menupop" + (wide ? " wide" : "")} onClick={(e) => {
        /* picking a chip inside closes the menu */
        if (wide && (e.target as HTMLElement).closest("button") && ref.current) ref.current.open = false;
      }}>
        {children}
      </div>
    </details>
  );
}
