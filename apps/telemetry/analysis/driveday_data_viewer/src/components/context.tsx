"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, useSyncExternalStore, type ReactNode, type RefObject } from "react";
import type { Player } from "@/lib/player";

/* How React reads the Player. Two feeds: `ui` for anything that is not the clock, `frame` once per painted frame. */

export const PlayerCtx = createContext<Player | null>(null);

export function usePlayer(): Player {
  const p = useContext(PlayerCtx);
  if (!p) throw new Error("usePlayer outside a run");
  return p;
}

/** Re-render when options, selection, notes or settings change. */
export function useUi(p: Player): number {
  return useSyncExternalStore(p.ui.subscribe, p.ui.getVersion, p.ui.getVersion);
}

/** Re-render on every painted frame, for things that follow the playhead. */
export function useFrame(p: Player): number {
  return useSyncExternalStore(p.frame.subscribe, p.frame.getVersion, p.frame.getVersion);
}

/** A canvas the Player paints each frame. `live` repaints every frame, otherwise only when something other than the clock changed. */
export function usePainter(p: Player, id: string, draw: (c: HTMLCanvasElement) => void, live: boolean): RefObject<HTMLCanvasElement | null> {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const drawRef = useRef(draw);
  drawRef.current = draw;
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const off = p.addPainter(id, () => {
      if (el.isConnected) drawRef.current(el);
    }, live);
    const ro = new ResizeObserver(() => p.schedule(true));
    ro.observe(el);
    return () => {
      off();
      ro.disconnect();
    };
  }, [p, id, live]);
  return ref;
}

/* ---------- toasts ---------- */

type ToastFn = (msg: string, kind?: "err" | "busy" | "") => void;
const ToastCtx = createContext<ToastFn>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children, bind }: { children: ReactNode; bind?: (fn: ToastFn) => void }) {
  const [t, setT] = useState<{ msg: string; kind: string } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const toast = useCallback<ToastFn>((msg, kind = "") => {
    setT({ msg, kind });
    clearTimeout(timer.current);
    if (kind !== "busy") timer.current = setTimeout(() => setT(null), kind === "err" ? 14000 : 4500);
  }, []);
  useEffect(() => bind?.(toast), [bind, toast]);
  return (
    <ToastCtx.Provider value={toast}>
      {children}
      <div className={"toast " + (t?.kind ?? "")} id="toast" role="status" hidden={!t}>
        {t?.msg}
      </div>
    </ToastCtx.Provider>
  );
}

/** Re-open the current run from storage, for when something it depends on changed (the start/finish line). */
export const ReloadCtx = createContext<() => void>(() => {});
export const useReload = () => useContext(ReloadCtx);
