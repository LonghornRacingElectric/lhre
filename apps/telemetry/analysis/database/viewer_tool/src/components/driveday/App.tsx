"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { convertSession } from "@/lib/driveday/convert";
import { invalidatePalette } from "@/lib/driveday/draw/palette";
import { Player, type Prefs } from "@/lib/driveday/player";
import { rebuildRun } from "@/lib/driveday/rebuild";
import { Session } from "@/lib/driveday/session";
import { loadUserState, lsDel, lsGet, lsSet, metaOf, runStore, saveUserState, storageProblem } from "@/lib/driveday/storage";
import { isConeLabel, seedFlags, type RunListEntry, type SessionExport } from "@/lib/driveday/types";
import { defaultUnits } from "@/lib/driveday/units";
import { DbMenu, Header, type Theme } from "./Header";
import { Workspace } from "./Workspace";
import { PlayerCtx, ToastProvider, useToast } from "./context";

const NEXT_THEME: Record<Theme, Theme> = { auto: "light", light: "dark", dark: "auto" };

function applyTheme(t: Theme) {
  const r = document.querySelector(".ddv");
  if (!r) return;
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
  const [theme, setTheme] = useState<Theme>("auto");
  const live = useRef<Player | null>(null);
  const openReq = useRef(0);

  const open = useCallback(
    async (id: string | null) => {
      const req = ++openReq.current;
      live.current?.savePos();
      live.current?.dispose();
      live.current = null;
      setPlayer(null);
      setCurrent(id);
      if (!id) return;
      try {
        const D0 = await runStore.get(id);
        if (req !== openReq.current) return;
        if (!D0) throw new Error("That run is no longer stored in this browser.");
        const D = rebuildRun(D0);
        const session = new Session(D);
        const prefs = lsGet<Partial<Prefs>>("prefs", {});
        const p = new Player(session, loadUserState(D.meta.sessId, D), prefs.units ?? defaultUnits(), prefs);
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

  /* first load: restore runs, theme, and the last run opened */
  useEffect(() => {
    const t = lsGet<Theme>("theme", "auto");
    setTheme(t);
    applyTheme(t);
    void (async () => {
      try {
        const list = (await runStore.list()).sort((a, b) => b.added - a.added);
        setRuns(list);
        if (storageProblem) toast(storageProblem, "err");
        const last = lsGet<string | null>("run", null);
        const pick = list.find((r) => r.id === last)?.id ?? list[0]?.id ?? null;
        await open(pick);
      } catch (error) {
        toast("Could not read saved runs: " + (error as Error).message, "err");
      }
      setReady(true);
    })();
    return () => {
      live.current?.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* a drive day straight from the database, built server-side in the trackside-live export format */
  const importDb = useCallback(async (day: number) => {
    toast("Loading day " + day + " from the database…", "busy");
    const r = await fetch("/api/driveday/db?day=" + day).catch(() => null);
    if (!r?.ok) return toast(((await r?.json().catch(() => null))?.error as string) || "Could not reach the database.", "err");
    let last: string | null = null;
    try {
      const J = (await r.json()) as SessionExport;
      const courses = convertSession(J, "orion-day-" + day + ".json", await runStore.tracks());
      const now = Date.now();
      for (const [ci, { D, track, isNew, renamed }] of courses.entries()) {
        if (isNew || renamed) await runStore.putTrack(track);
        /* newest first in the list, so the courses of one day stay in order */
        await runStore.put({ ...metaOf(D), added: now - ci }, D);
        if (J.flags?.length) {
          const S = new Session(rebuildRun(D));
          const flags = J.flags.filter(f => f.t >= S.T0 && f.t <= S.T0 + S.TD &&
            !isConeLabel(f.label));
          saveUserState(D.meta.sessId, seedFlags(loadUserState(D.meta.sessId, D), flags, S.T0));
        }
        last ??= D.meta.sessId;
      }
      const n = courses.reduce((a, c) => a + c.D.laps.filter((l) => l.kind === "lap").length, 0);
      const note = courses[0].D.warnings.join(" ");
      toast("Day " + day + ": " + n + " laps" + (courses.length > 1 ? " in " + courses.length + " courses" : "") + "." + (note ? " " + note : ""));
    } catch (e) {
      toast("Day " + day + ": " + (e as Error).message, "err");
    }
    setRuns((await runStore.list()).sort((a, b) => b.added - a.added));
    if (last) await open(last);
  }, [open, toast]);

  const remove = useCallback(async (id: string) => {
    const run = runs.find(r => r.id === id);
    if (!run) return;
    if (!window.confirm('Delete "' + (run.name || "Untitled run") + '" from saved runs? Its notes are deleted for good; database days can be loaded again, older imported runs cannot.')) return;
    try {
      /* open the next run first, so the old player saves its position before that is cleared */
      if (id === current) await open(runs.find((r) => r.id !== id)?.id ?? null);
      await runStore.del(id);
      for (const key of ["state:", "pos:", "base:"]) lsDel(key + id);
      setRuns((await runStore.list()).sort((a, b) => b.added - a.added));
      toast("Run removed.");
    } catch (e) { toast("Could not delete this run: " + (e as Error).message, "err"); }
  }, [current, runs, open, toast]);

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
            theme={theme}
            onTheme={cycleTheme}
            onSelect={(id) => void open(id)}
            onDb={(day) => void importDb(day)}
            onRemove={(id) => void remove(id)}
          />
          {player && <Workspace key={player.session.D.meta.sessId} />}
        </div>
        {ready && !player && !current && (
          <div className="welcome">
            <div>
              <h2>Give LAPDOG a lap to follow.</h2>
              <p>Choose a drive day from the database. LAPDOG keeps your runs in this browser, ready for the next debrief.</p>
              <DbMenu onDb={(day) => void importDb(day)} />
            </div>
          </div>
        )}
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
