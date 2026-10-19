import { roadWidth } from "./geometry";
import { layoutId } from "./layouts";
import { withRunDirection } from "./direction";
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
  list: async (): Promise<RunListEntry[]> => {
    const list = await guard<RunListEntry[]>(() => run(["meta"], "readonly", ([m]) => m.getAll()), () => [...memory.meta.values()]);
    const upgraded: RunListEntry[] = [];
    // Read each old run once, then cache only its small display metadata. Saved telemetry stays untouched.
    for (const meta of list) {
      if (meta.direction != null) { upgraded.push(meta); continue; }
      const data = await runStore.get(meta.id);
      if (!data) { upgraded.push(meta); continue; }
      const next = { ...meta, direction: data.meta.direction, name: data.meta.name, track: data.meta.trackName };
      await guard<void>(() => run(["meta"], "readwrite", ([m]) => { m.put(next); }), () => { memory.meta.set(meta.id, next); });
      upgraded.push(next);
    }
    return upgraded;
  },
  get: async (id: string): Promise<RunData | undefined> => {
    const data = await guard<RunData | undefined>(() => run(["data"], "readonly", ([d]) => d.get(id)), () => memory.data.get(id));
    return data ? withRunDirection({ ...data, edges: data.edges ? { ...data.edges, width: roadWidth(data.edges) } : undefined }) : undefined;
  },
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
  tracks: async () => (await guard<Track[]>(() => run(["tracks"], "readonly", ([t]) => t.getAll()), () => [...memory.tracks.values()])).map(t => ({ ...t, edges: t.edges ? { ...t.edges, width: roadWidth(t.edges) } : undefined })),
  delLayout: async (id: string) => {
    const ids = (await runStore.tracks()).filter(t => t.edges && layoutId(t) === id).map(t => t.id);
    await guard<void>(() => run(["tracks"], "readwrite", ([s]) => { ids.forEach(key => s.delete(key)); }),
      () => { ids.forEach(key => memory.tracks.delete(key)); });
  },
  putTrack: (t: Track) => guard<void>(() => run(["tracks"], "readwrite", ([s]) => s.put(t)), () => { memory.tracks.set(t.id, t); }),
};

export function metaOf(D: RunData): RunListEntry {
  D = withRunDirection(D);
  const runs = D.laps.filter((l) => l.kind === "lap");
  const best = runs.length ? Math.min(...runs.map((l) => l.dur)) : null;
  return {
    id: D.meta.sessId,
    direction: D.meta.direction,
    name: D.meta.name,
    driver: D.meta.driver,
    venue: D.meta.venue,
    event: D.meta.event,
    track: D.meta.trackName || "",
    trackId: D.meta.trackId || "",
    ts: D.laps.length ? Math.min(...D.laps.map((l) => l.t0)) : D.telemetryOnly?.intervals[0]?.start ?? 0,
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

/** Merge exported hits by stable identity; manual hits and explicit removals survive reimport. */
export function seedCones(D: RunData, user: UserState): UserState {
  const origin = Math.min(...D.laps.map((lap) => lap.t0));
  const dismissed = new Set(user.dismissedCones ?? []);
  const events = new Map(user.ce.map((event) => [event.id, { ...event }]));
  (D.coneEvents ?? []).filter((event) => !dismissed.has(event.id)).forEach((event) => {
    events.set(event.id, { ...event, t: event.t - origin });
  });
  return { ...user, ce: [...events.values()].sort((a, b) => a.t - b.t) };
}

export const loadUserState = (sessId: string, D?: RunData): UserState => {
  const user = { ...emptyUserState(), ...lsGet<Partial<UserState>>("state:" + sessId, {}) };
  return D ? seedCones(D, user) : user;
};
export const saveUserState = (sessId: string, u: UserState) => lsSet("state:" + sessId, u);
