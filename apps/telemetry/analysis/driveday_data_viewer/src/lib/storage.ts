import { emptyUserState, type RunData, type RunListEntry, type Track, type UserState } from "./types";

/* Browser-side storage. Runs and track layouts go in IndexedDB (an export can be 15 MB). Small things the user
   changes (cones, notes, preferences) go in localStorage. Nothing leaves the machine. */

const DB_NAME = "driveday_data_viewer";
const DB_VERSION = 1;

let dbp: Promise<IDBDatabase> | null = null;
let blocked = false;
export let storageMode: "idb" | "memory" = "idb";

const memory = { meta: new Map<string, RunListEntry>(), data: new Map<string, RunData>(), tracks: new Map<string, Track>() };

function openDb(): Promise<IDBDatabase> {
  if (dbp) return dbp;
  dbp = new Promise((res, rej) => {
    if (typeof indexedDB === "undefined") return rej(new Error("IndexedDB is not available"));
    let r: IDBOpenDBRequest;
    try {
      r = indexedDB.open(DB_NAME, DB_VERSION);
    } catch (e) {
      return rej(e);
    }
    r.onupgradeneeded = () => {
      const db = r.result;
      if (!db.objectStoreNames.contains("meta")) db.createObjectStore("meta", { keyPath: "id" });
      if (!db.objectStoreNames.contains("data")) db.createObjectStore("data");
      if (!db.objectStoreNames.contains("tracks")) db.createObjectStore("tracks", { keyPath: "id" });
    };
    r.onblocked = () => {
      blocked = true;
    };
    r.onsuccess = () => {
      const db = r.result;
      db.onversionchange = () => {
        db.close();
        dbp = null;
      };
      res(db);
    };
    r.onerror = () => rej(r.error || new Error("Could not open browser storage"));
  });
  return dbp;
}

async function run<T>(stores: string[], mode: IDBTransactionMode, fn: (s: IDBObjectStore[]) => IDBRequest | void): Promise<T> {
  const db = await openDb();
  return new Promise<T>((res, rej) => {
    const t = db.transaction(stores, mode);
    let req: IDBRequest | void;
    try {
      req = fn(stores.map((n) => t.objectStore(n)));
    } catch (e) {
      rej(e);
      return;
    }
    t.oncomplete = () => res((req ? req.result : undefined) as T);
    t.onerror = t.onabort = () => rej(t.error || new Error("Storage error"));
  });
}

/** Fall back to memory if IndexedDB is unavailable, so the tool still works for this tab. */
async function guard<T>(idb: () => Promise<T>, mem: () => T): Promise<T> {
  if (storageMode === "memory") return mem();
  try {
    return await idb();
  } catch (e) {
    storageMode = "memory";
    storageProblem = blocked
      ? "Another tab with an older copy of this page is holding the run storage. Close it and reload to get your saved runs back. Imports last for this tab until then."
      : String((e as Error)?.message || e);
    return mem();
  }
}
export let storageProblem: string | null = null;

export const runStore = {
  list: () => guard<RunListEntry[]>(() => run(["meta"], "readonly", ([m]) => m.getAll()), () => [...memory.meta.values()]),
  get: (id: string) => guard<RunData | undefined>(() => run(["data"], "readonly", ([d]) => d.get(id)), () => memory.data.get(id)),
  put: (meta: RunListEntry, D: RunData) =>
    guard<void>(
      () => run(["meta", "data"], "readwrite", ([m, d]) => { m.put(meta); return d.put(D, meta.id); }),
      () => { memory.meta.set(meta.id, meta); memory.data.set(meta.id, D); },
    ),
  del: (id: string) =>
    guard<void>(
      () => run(["meta", "data"], "readwrite", ([m, d]) => { m.delete(id); return d.delete(id); }),
      () => { memory.meta.delete(id); memory.data.delete(id); },
    ),
  tracks: () => guard<Track[]>(() => run(["tracks"], "readonly", ([t]) => t.getAll()), () => [...memory.tracks.values()]),
  putTrack: (t: Track) => guard<void>(() => run(["tracks"], "readwrite", ([s]) => s.put(t)), () => { memory.tracks.set(t.id, t); }),
};

export function metaOf(D: RunData): RunListEntry {
  const runs = D.laps.filter((l) => l.kind === "lap");
  const best = runs.length ? Math.min(...runs.map((l) => l.dur)) : null;
  return {
    id: D.meta.sessId,
    name: D.meta.name,
    driver: D.meta.driver,
    venue: D.meta.venue,
    event: D.meta.event,
    track: D.meta.trackName || "",
    trackId: D.meta.trackId || "",
    ts: D.laps.length ? Math.min(...D.laps.map((l) => l.t0)) : 0,
    laps: runs.length,
    best,
    added: Date.now(),
  };
}

export function runLabel(m: RunListEntry): string {
  const d = m.ts ? new Date(m.ts).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "";
  return (m.driver || "No driver") + " · " + (m.name || "Untitled") + (m.event ? " · " + m.event : "") + (d ? " · " + d : "") + " · " + m.laps + " laps";
}

/* ---- small things in localStorage ---- */

const P = "driveday:";

export function lsGet<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(P + key);
    return v == null ? fallback : (JSON.parse(v) as T);
  } catch {
    return fallback;
  }
}
export function lsSet(key: string, value: unknown) {
  try {
    localStorage.setItem(P + key, JSON.stringify(value));
  } catch {
    /* storage blocked or full: the page still works without it */
  }
}
export function lsDel(key: string) {
  try {
    localStorage.removeItem(P + key);
  } catch {
    /* ignore */
  }
}

export const loadUserState = (sessId: string): UserState => ({ ...emptyUserState(), ...lsGet<Partial<UserState>>("state:" + sessId, {}) });
export const saveUserState = (sessId: string, u: UserState) => lsSet("state:" + sessId, u);

/** Start/finish line position (metres along the stored loop), shared by every run on a track layout. */
export const sfKey = (d: { trackId: string; trackName: string }) => "sf:" + (d.trackId || d.trackName || "track");
export const getSF = (meta: { trackId: string; trackName: string }) => lsGet<number>(sfKey(meta), 0);
