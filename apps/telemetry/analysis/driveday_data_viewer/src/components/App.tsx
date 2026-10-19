"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { invalidatePalette } from "@/lib/draw/palette";
import { convertDriverSession, detachLayout, importLayoutRecording, isLayoutRecording } from "@/lib/layout-import";
import { builtinTracks, layoutId } from "@/lib/layouts";
import { Player, type Prefs } from "@/lib/player";
import { rebuildRun } from "@/lib/rebuild";
import { Session } from "@/lib/session";
import { loadUserState, lsGet, lsSet, metaOf, runStore, storageProblem } from "@/lib/storage";
import type { RunData, RunListEntry, SessionExport, Track } from "@/lib/types";
import { defaultUnits } from "@/lib/units";
import { Header, type Theme } from "./Header";
import { Workspace } from "./Workspace";
import { PlayerCtx, ToastProvider, useToast } from "./context";

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
  const [layouts, setLayouts] = useState<Track[]>([]);
  const [layout, setLayout] = useState<Track | null>(null);
  const [telemetry, setTelemetry] = useState<RunData | null>(null);
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
      setLayout(null);
      setTelemetry(null);
      setCurrent(id);
      if (!id) return;
      try {
        const D0 = await runStore.get(id);
        if (!D0) throw new Error("That run is no longer stored in this browser.");
        if (D0.telemetryOnly) {
          setTelemetry(D0);
          lsSet("run", id); lsSet("view", { kind: "run", id });
          return;
        }
        if (!(D0.RL >= 30)) throw new Error("This saved run has an invalid GPS reference. Reimport the original JSON to recover telemetry and check GPS quality.");
        // Old bundled matches are not user-approved layout imports.
        const bundled = (await runStore.tracks()).find(t => t.id === D0.meta.trackId)?.builtin || builtinTracks().some(t => t.id === D0.meta.trackId);
        const clean = bundled && D0.edges ? detachLayout(D0) : D0;
        if (clean !== D0) {
          const saved = (await runStore.list()).find(r => r.id === id);
          await runStore.put({ ...metaOf(clean), added: saved?.added ?? Date.now() }, clean);
          setRuns(await runStore.list());
        }
        const D = rebuildRun(clean);
        const session = new Session(D);
        const prefs = lsGet<Partial<Prefs>>("prefs", {});
        const p = new Player(session, loadUserState(D.meta.sessId, D), prefs.units ?? defaultUnits(), prefs);
        p.toast = toast;
        void p.findRivals();
        live.current = p;
        lsSet("run", id);
        lsSet("view", { kind: "run", id });
        setPlayer(p);
      } catch (e) {
        toast("Could not open this run: " + (e as Error).message, "err");
      }
    },
    [toast],
  );

  const openLayout = useCallback((track: Track) => {
    live.current?.savePos();
    live.current?.dispose();
    live.current = null;
    setPlayer(null);
    setCurrent(null);
    setTelemetry(null);
    setLayout(track);
    lsSet("view", { kind: "layout", id: layoutId(track) });
  }, []);

  /* first load: restore runs, theme, and the last run opened */
  useEffect(() => {
    const t = lsGet<Theme>("theme", "auto");
    setTheme(t);
    applyTheme(t);
    void (async () => {
      const list = (await runStore.list()).sort((a, b) => b.added - a.added);
      setRuns(list);
      const savedLayouts = (await runStore.tracks()).filter(t => t.edges && !t.builtin);
      setLayouts(savedLayouts);
      if (storageProblem) toast(storageProblem, "err");
      const view = lsGet<{ kind: "run" | "layout"; id: string } | null>("view", null);
      const savedLayout = view?.kind === "layout" ? savedLayouts.find(t => t.id === view.id || layoutId(t) === view.id) : undefined;
      const last = view?.kind === "run" ? view.id : lsGet<string | null>("run", null);
      const pick = list.find(r => r.id === last)?.id ?? list[0]?.id ?? null;
      if (savedLayout) openLayout(savedLayout);
      else if (pick) await open(pick);
      else if (savedLayouts[0]) openLayout(savedLayouts[0]);
      setReady(true);
    })();
    return () => {
      live.current?.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const importFiles = useCallback(
    async (files: FileList | File[], asLayout = false) => {
      let last: string | null = null;
      // Register layouts before drivers even if the file chooser returns a different order.
      const ordered = Array.from(files).sort((a, b) => Number(asLayout || isLayoutRecording(b.name)) - Number(asLayout || isLayoutRecording(a.name)));
      let importedLayout: Track | null = null;
      for (const f of ordered) {
        try {
          toast("Reading " + f.name + "…", "busy");
          const J = JSON.parse(await f.text()) as SessionExport;
          if (asLayout || isLayoutRecording(f.name)) {
            const result = await importLayoutRecording(J, f.name);
            importedLayout = result.tracks[0];
            toast(f.name + ": track edges created for both directions; " + result.updated.length + " saved runs updated.");
            continue;
          }
          const courses = convertDriverSession(J, f.name, await runStore.tracks());
          const now = Date.now();
          for (const [ci, { D: converted, track, isNew, renamed }] of courses.entries()) {
            const existing = await runStore.get(converted.meta.sessId);
            const D = existing?.layoutDetached ? detachLayout(converted) : converted;
            if (isNew || renamed) await runStore.putTrack(track);
            /* newest first in the list, so the courses of one file stay in order */
            await runStore.put({ ...metaOf(D), added: now - ci }, D);
            last ??= D.meta.sessId;
          }
          const n = courses.reduce((a, c) => a + c.D.laps.filter((l) => l.kind === "lap").length, 0);
          const note = courses[0].D.warnings.join(" ");
          const retained = courses.reduce((n, c) => n + (c.D.telemetryOnly?.intervals.length ?? 0), 0);
          toast(f.name + ": " + n + " GPS-supported laps" + (retained ? "; " + retained + " logged intervals retained as telemetry only" : "") + "." + (note ? " " + note : ""));
        } catch (e) {
          toast(f.name + ": " + (e as Error).message, "err");
        }
      }
      setRuns((await runStore.list()).sort((a, b) => b.added - a.added));
      setLayouts((await runStore.tracks()).filter(t => t.edges && !t.builtin));
      if (last) await open(last);
      else if (importedLayout) openLayout(importedLayout);
    },
    [open, openLayout, toast],
  );

  const remove = useCallback(async (id: string) => {
    const run = runs.find(r => r.id === id);
    if (!run) return;
    if (!window.confirm('Delete "' + (run.name || "Untitled run") + '" from saved runs? You can reimport the original JSON.')) return;
    try {
      await runStore.del(id);
      const list = (await runStore.list()).sort((a, b) => b.added - a.added);
      setRuns(list);
      if (id === current) {
        if (list[0]) await open(list[0].id);
        else if (layouts[0]) openLayout(layouts[0]);
        else { lsSet("view", null); await open(null); }
      }
      toast("Run removed.");
    } catch (e) { toast("Could not delete this run: " + (e as Error).message, "err"); }
  }, [current, runs, layouts, open, openLayout, toast]);

  const removeLayout = useCallback(async (id: string) => {
    const track = layouts.find(t => layoutId(t) === id);
    if (!track || !window.confirm("Delete this saved layout? Existing driver runs and their road geometry will be kept. You can reimport the layout JSON.")) return;
    try {
      await runStore.delLayout(id);
      const remaining = (await runStore.tracks()).filter(t => t.edges && !t.builtin);
      setLayouts(remaining);
      if (layout && layoutId(layout) === id) {
        if (remaining[0]) openLayout(remaining[0]);
        else if (runs[0]) await open(runs[0].id);
        else { lsSet("view", null); await open(null); }
      }
      toast("Layout removed.");
    } catch (e) { toast("Could not delete layout: " + (e as Error).message, "err"); }
  }, [layouts, layout, runs, openLayout, open, toast]);

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
      <PlayerCtx.Provider value={player}>
        <div className="app">
          <Header
            runs={runs}
            current={current}
            layouts={layouts}
            layout={layout}
            telemetry={telemetry}
            onSelectLayout={id => { const selected = layouts.find(t => layoutId(t) === id); if (selected) openLayout(selected); }}
            onRemoveLayout={id => void removeLayout(id)}
            theme={theme}
            onTheme={cycleTheme}
            onSelect={(id) => void open(id)}
            onImport={(f) => void importFiles(f)}
            onImportLayout={(f) => void importFiles(f, true)}
            onRemove={(id) => void remove(id)}
          />
          {telemetry && <Workspace key={telemetry.meta.sessId} telemetry={telemetry} />}
          {player?.session.D.gpsIssues?.length ? <details className="gps-warning"><summary>{player.session.D.gpsIssues.length} logged intervals had unusable GPS and were kept separately under “Telemetry only”.</summary><ul>{player.session.D.gpsIssues.map((issue, i) => <li key={i}>{issue}</li>)}</ul></details> : null}
          {layout && <Workspace key={layoutId(layout)} track={layout} />}
          {player && <Workspace key={player.session.D.meta.sessId} />}
        </div>
        {ready && !player && !current && !layout && !telemetry && (
          <div className="welcome">
            <div>
              <h2>No runs or layouts loaded</h2>
              <p>Import a driver session or use Import layout for a centreline recording. Both are saved in this browser and organized by date and location in the picker. You can also drop files anywhere on this page.</p>
              <button className="btn primary" type="button" onClick={() => file.current?.click()}>Import JSON</button>
              <input ref={file} type="file" accept=".json,application/json" multiple hidden onChange={(e) => { if (e.target.files?.length) void importFiles(e.target.files); e.target.value = ""; }} />
            </div>
          </div>
        )}
        {dragOver && <div className="dropzone">Drop session JSON to import</div>}
      </PlayerCtx.Provider>
  );
}

export function App() {
  return (
    <ToastProvider>
      <Shell />
    </ToastProvider>
  );
}
