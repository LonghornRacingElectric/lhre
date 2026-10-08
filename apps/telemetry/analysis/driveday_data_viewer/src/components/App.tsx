"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { convertSession } from "@/lib/convert";
import { invalidatePalette } from "@/lib/draw/palette";
import { Player, type Prefs } from "@/lib/player";
import { rebuildRun } from "@/lib/rebuild";
import { Session } from "@/lib/session";
import { getSF, loadUserState, lsGet, lsSet, metaOf, runStore, storageProblem } from "@/lib/storage";
import type { RunListEntry, SessionExport } from "@/lib/types";
import { defaultUnits } from "@/lib/units";
import { Header, type Theme } from "./Header";
import { Workspace } from "./Workspace";
import { PlayerCtx, ReloadCtx, ToastProvider, useToast } from "./context";

const NEXT_THEME: Record<Theme, Theme> = { auto: "light", light: "dark", dark: "auto" };

function applyTheme(t: Theme) {
  const r = document.documentElement;
  if (t === "auto") r.removeAttribute("data-theme");
  else r.setAttribute("data-theme", t);
  invalidatePalette();
}

function Shell() {
  const toast = useToast();
  const [runs, setRuns] = useState<RunListEntry[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [player, setPlayer] = useState<Player | null>(null);
  const [ready, setReady] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [theme, setTheme] = useState<Theme>("auto");
  const file = useRef<HTMLInputElement>(null);
  const live = useRef<Player | null>(null);

  const open = useCallback(
    async (id: string | null) => {
      live.current?.savePos();
      live.current?.dispose();
      live.current = null;
      setPlayer(null);
      setCurrent(id);
      if (!id) return;
      try {
        const D0 = await runStore.get(id);
        if (!D0) throw new Error("That run is no longer stored in this browser.");
        const sf = getSF(D0.meta);
        const D = rebuildRun(D0, sf);
        const session = new Session(D);
        const prefs = lsGet<Partial<Prefs>>("prefs", {});
        const p = new Player(session, loadUserState(D.meta.sessId), prefs.units ?? defaultUnits(), sf, prefs);
        p.toast = toast;
        live.current = p;
        lsSet("run", id);
        setPlayer(p);
      } catch (e) {
        toast("Could not open this run: " + (e as Error).message, "err");
      }
    },
    [toast],
  );

  const reload = useCallback(() => {
    if (current) void open(current);
  }, [current, open]);

  /* first load: restore runs, theme, and the last run opened */
  useEffect(() => {
    const t = lsGet<Theme>("theme", "auto");
    setTheme(t);
    applyTheme(t);
    void (async () => {
      const list = (await runStore.list()).sort((a, b) => b.added - a.added);
      setRuns(list);
      if (storageProblem) toast(storageProblem, "err");
      const last = lsGet<string | null>("run", null);
      const pick = list.find((r) => r.id === last)?.id ?? list[0]?.id ?? null;
      await open(pick);
      setReady(true);
    })();
    return () => {
      live.current?.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const importFiles = useCallback(
    async (files: FileList | File[]) => {
      let last: string | null = null;
      for (const f of Array.from(files)) {
        try {
          toast("Reading " + f.name + "…", "busy");
          const J = JSON.parse(await f.text()) as SessionExport;
          const { D, track, isNew, renamed } = convertSession(J, f.name, await runStore.tracks());
          if (isNew || renamed) await runStore.putTrack(track);
          await runStore.put(metaOf(D), D);
          last = D.meta.sessId;
          toast(f.name + ": " + D.laps.filter((l) => l.kind === "lap").length + " laps." + (D.warnings.length ? " " + D.warnings.join(" ") : ""));
        } catch (e) {
          toast(f.name + ": " + (e as Error).message, "err");
        }
      }
      setRuns((await runStore.list()).sort((a, b) => b.added - a.added));
      if (last) await open(last);
    },
    [open, toast],
  );

  const remove = useCallback(async () => {
    if (!current) return;
    if (!window.confirm("Remove this run from the browser? Your notes for it go too.")) return;
    await runStore.del(current);
    const list = (await runStore.list()).sort((a, b) => b.added - a.added);
    setRuns(list);
    await open(list[0]?.id ?? null);
    toast("Run removed.");
  }, [current, open, toast]);

  /* drag and drop anywhere */
  useEffect(() => {
    let depth = 0;
    const has = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const enter = (e: DragEvent) => { if (has(e)) { depth++; setDragOver(true); } };
    const leave = (e: DragEvent) => { if (has(e) && --depth <= 0) { depth = 0; setDragOver(false); } };
    const over = (e: DragEvent) => { if (has(e)) e.preventDefault(); };
    const drop = (e: DragEvent) => {
      if (!has(e)) return;
      e.preventDefault();
      depth = 0;
      setDragOver(false);
      if (e.dataTransfer?.files.length) void importFiles(e.dataTransfer.files);
    };
    addEventListener("dragenter", enter);
    addEventListener("dragleave", leave);
    addEventListener("dragover", over);
    addEventListener("drop", drop);
    return () => {
      removeEventListener("dragenter", enter);
      removeEventListener("dragleave", leave);
      removeEventListener("dragover", over);
      removeEventListener("drop", drop);
    };
  }, [importFiles]);

  /* repaint canvases when the theme or the OS colour scheme changes */
  useEffect(() => {
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const f = () => {
      invalidatePalette();
      player?.schedule(true);
    };
    mq.addEventListener("change", f);
    return () => mq.removeEventListener("change", f);
  }, [player]);

  useEffect(() => {
    if (document.fonts?.ready) void document.fonts.ready.then(() => player?.schedule(true));
  }, [player]);

  const cycleTheme = () => {
    const t = NEXT_THEME[theme];
    setTheme(t);
    lsSet("theme", t);
    applyTheme(t);
    player?.schedule(true);
  };

  return (
    <ReloadCtx.Provider value={reload}>
      <PlayerCtx.Provider value={player}>
        <div className="app">
          <Header
            runs={runs}
            current={current}
            theme={theme}
            onTheme={cycleTheme}
            onSelect={(id) => void open(id)}
            onImport={(f) => void importFiles(f)}
            onRemove={() => void remove()}
          />
          {player && <Workspace key={player.session.D.meta.sessId + ":" + player.sf} />}
        </div>
        {ready && !player && !current && (
          <div className="welcome">
            <div>
              <h2>No runs loaded</h2>
              <p>Import a session JSON exported from the logger. Each run is kept in this browser, and the dropdown at the top switches between them. You can also drop files anywhere on this page.</p>
              <button className="btn primary" type="button" onClick={() => file.current?.click()}>Import JSON</button>
              <input ref={file} type="file" accept=".json,application/json" multiple hidden onChange={(e) => { if (e.target.files?.length) void importFiles(e.target.files); e.target.value = ""; }} />
            </div>
          </div>
        )}
        {dragOver && <div className="dropzone">Drop session JSON to import</div>}
      </PlayerCtx.Provider>
    </ReloadCtx.Provider>
  );
}

export function App() {
  return (
    <ToastProvider>
      <Shell />
    </ToastProvider>
  );
}
