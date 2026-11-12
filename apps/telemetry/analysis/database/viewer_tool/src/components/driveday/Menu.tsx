"use client";

import { useEffect, useRef, type ReactNode } from "react";

/** A button that opens a small popover. Closes on an outside click or Escape. */
export function Menu({ label, children, wide = false, right = false, chip = false, onToggle, ariaLabel }: { label: ReactNode; children: ReactNode; wide?: boolean; right?: boolean; chip?: boolean; ariaLabel?: string; onToggle?: (open: boolean) => void }) {
  const ref = useRef<HTMLDetailsElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const place = () => {
    const d = ref.current, pop = popup.current;
    if (!d?.open || !pop) return;
    const anchor = d.querySelector("summary")!.getBoundingClientRect();
    const bounds = pop.getBoundingClientRect();
    const left = Math.max(8, Math.min(window.innerWidth - bounds.width - 8, right ? anchor.right - bounds.width : anchor.left));
    const below = anchor.bottom + 6;
    const top = below + bounds.height <= window.innerHeight - 8 ? below : Math.max(8, anchor.top - bounds.height - 6);
    Object.assign(pop.style, { position: "fixed", left: left + "px", right: "auto", top: top + "px", margin: "0", maxHeight: (window.innerHeight - top - 8) + "px", overflowY: "auto", zIndex: "1000" });
  };
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
    window.addEventListener("resize", place);
    document.addEventListener("scroll", place, true);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", esc);
      window.removeEventListener("resize", place);
      document.removeEventListener("scroll", place, true);
    };
  // Placement reads current DOM geometry; changing a label does not change the listeners.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <details className="menu" ref={ref} onToggle={(e) => {
      const open = (e.target as HTMLDetailsElement).open;
      const pop = popup.current;
      if (pop?.showPopover) { if (open && !pop.matches(":popover-open")) pop.showPopover(); else if (!open && pop.matches(":popover-open")) pop.hidePopover(); }
      if (open) place();
      onToggle?.(open);
    }}>
      <summary className={chip ? "chip" : "btn"} aria-label={ariaLabel}>{label}</summary>
      <div ref={popup} popover="manual" className={"menupop" + (wide ? " wide" : "") + (right ? " right" : "")} onClick={(e) => {
        /* picking a chip inside closes the menu */
        if (wide && (e.target as HTMLElement).closest("button") && ref.current) ref.current.open = false;
      }}>
        {children}
      </div>
    </details>
  );
}
