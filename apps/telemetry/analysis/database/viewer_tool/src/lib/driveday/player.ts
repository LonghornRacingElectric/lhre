import { Analysis } from "./analysis";
import { clock } from "./format";
import { posAt } from "./geometry";
import { interp, wrap } from "./math";
import { timelineWindow, zoomWindow } from "./timeline-window";
import { pieces, segAt, zoneAt } from "./segments";
import { sectorTimes, suggestGates, validGates, type SectorGates } from "./sectors";
import type { Session } from "./session";
import { lsGet, lsSet, saveUserState } from "./storage";
import type { Lap, LapFlag, UserState } from "./types";
import type { Units } from "./units";

/* The one mutable model behind the screen: where the playhead is, which lap is watched, what the camera sees, what the user has marked.
   React reads it through subscriptions; canvases are painted from it once per animation frame. */

export type Mode = "speed" | "pedal" | "acc" | "gain" | "sec";
export type Cmp = "best" | "theo" | "lap" | "external" | "none";
export type EndMode = "go" | "stop" | "loop";
export interface View { cx: number; cy: number; z: number }
export interface Focus { a: number; len: number; type: "c" | "s" | "b"; name: string }
export interface Hit { x: number; y: number; r: number; fn: () => void }
export interface Prefs { mode: Mode; endMode: EndMode; units: Units; rate: number }

export const RATES = [0.25, 0.5, 1, 2, 4];

interface Painter { fn: () => void; live: boolean }

class Emitter {
  version = 0;
  private subs = new Set<() => void>();
  subscribe = (fn: () => void) => {
    this.subs.add(fn);
    return () => {
      this.subs.delete(fn);
    };
  };
  getVersion = () => this.version;
  bump() {
    this.version++;
    this.subs.forEach((f) => f());
  }
}

const ease = (p: number) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);

export class Player {
  readonly ui = new Emitter(); // anything that is not the clock: options, selection, notes
  readonly frame = new Emitter(); // once per painted frame

  analysis: Analysis;
  user: UserState;
  units: Units;
  toast: (msg: string, kind?: "err" | "busy" | "") => void = () => {};

  /* playhead */
  T = 0;
  sel = 0;
  playing = false;
  rate = 1;
  endMode: EndMode = "go";
  loopA: number | null = null;
  loopB: number | null = null;
  /* what the map shows */
  mode: Mode = "speed";
  cmp: Cmp = "best";
  ghostLapI: number;
  ghostAll = false;
  externalGhost: Lap | null = null;
  externalKey = "";
  videoUrl = "";
  videoName = "";
  videoKey = "";
  videoLinked = false;
  videoCodecLabel = "";
  videoCurrentTime = 0;
  reviewMarkers: { point: [number, number]; label: string; color: string }[] = [];
  showSeg = false;
  showCones = true;
  raceMode = false;
  consMode = false;
  layoutOnly = false;
  baseIdx: number;
  sectorMethod: "auto" | "manual" = "auto";
  hoverT: number | null = null;
  timelineStart = 0;
  timelineEnd: number | null = null;
  get timelineRange() { return timelineWindow(this.TD, this.timelineStart, this.timelineEnd ?? this.TD); }
  setTimelineWindow(start: number, end: number) {
    const range = timelineWindow(this.TD, start, end);
    this.timelineStart = range.start; this.timelineEnd = range.end;
    this.hoverT = null; this.ui.bump(); this.schedule(true);
  }
  zoomTimeline(factor: number, anchor = this.T) {
    const r = this.timelineRange;
    const next = zoomWindow(this.TD, r.start, r.end, factor, Math.max(r.start, Math.min(r.end, anchor)));
    this.setTimelineWindow(next.start, next.end);
  }
  /* camera */
  view: View;
  anim: { f: View; t: View; t0: number; dur: number } | null = null;
  follow = false;
  followZ = 3.6;
  focus: Focus | null = null;
  W = 0;
  H = 0;
  sc = 1;
  ox = 0;
  oy = 0;
  scFit = 1;
  cyScreen = 0;
  hits: Hit[] = [];
  /* the note box under the timeline */
  noteOpen = false;
  /* where the car is now */
  curSeg = -1;
  curZone = -1;

  private painters = new Map<string, Painter>();
  private dirty = false;
  private forceStatic = true;
  private lastSel = -1;
  private lastTs = 0;
  private posTimer: ReturnType<typeof setTimeout> | undefined;
  private disposed = false;

  static readonly PADX = 50;
  static readonly PADT = 44;
  static readonly PADB = 64;

  constructor(
    readonly session: Session,
    user: UserState,
    units: Units,
    prefs: Partial<Prefs> = {},
  ) {
    this.user = user;
    this.units = units;
    const savedSectors = lsGet<{ gates: number[]; method: "auto" | "manual" } | null>("sectors:" + session.D.meta.trackId, null);
    if (savedSectors && Array.isArray(savedSectors.gates)) {
      const gates = savedSectors.gates.map((g) => g * session.RL);
      if (validGates(gates, session.RL)) {
        session.setGates(gates);
        this.sectorMethod = savedSectors.method === "manual" ? "manual" : "auto";
      }
    } else this.saveSectors();
    this.analysis = new Analysis(session, user);
    this.mode = prefs.mode ?? "speed";
    this.endMode = prefs.endMode ?? "go";
    this.rate = prefs.rate ?? 1;
    const { bbox, laps, runs } = session;
    this.view = { cx: (bbox.x[0] + bbox.x[1]) / 2, cy: (bbox.y[0] + bbox.y[1]) / 2, z: 1 };
    this.ghostLapI = laps.indexOf(this.analysis.bestRun);
    const b = Math.max(0, laps.indexOf(runs[0]));
    const saved = lsGet<number | null>("base:" + session.D.meta.sessId, null);
    this.baseIdx = saved != null && laps[saved] ? saved : b;
    const pos = lsGet<number | null>("pos:" + session.D.meta.sessId, null);
    this.T = pos != null && pos >= 0 && pos <= session.TD ? pos : session.base.t0 - session.T0;
  }

  private saveSectors() {
    lsSet("sectors:" + this.session.D.meta.trackId, { gates: this.session.gates.map((g) => g / this.RL), method: this.sectorMethod });
  }

  setSectorAtPlayhead(index: 0 | 1): void {
    const { L, rel } = this.curState();
    if (L.kind !== "lap") return this.toast("Choose a completed lap to place a sector boundary.");
    const distance = interp(L.ch.t, L.ch.u, rel);
    const gates: SectorGates = [...this.session.gates];
    gates[index] = distance;
    if (!validGates(gates, this.RL) || Math.min(gates[0], gates[1] - gates[0], this.RL - gates[1]) < 5)
      return this.toast("Keep the boundaries in order, at least 5 m apart and away from start/finish.");
    this.session.setGates(gates);
    this.sectorMethod = "manual";
    this.saveSectors();
    this.analysis.recompute();
    this.changed();
    this.toast("S" + (index + 1) + " ends here. All laps now use these boundaries.");
  }

  autoSectors(): void {
    const lap = this.analysis.bestRun;
    this.session.setGates(suggestGates(lap, this.session.segs, this.RL));
    this.sectorMethod = "auto";
    this.saveSectors();
    this.analysis.recompute();
    this.changed();
  }

  /* ================= small helpers ================= */
  get laps() { return this.session.laps; }
  get runs() { return this.session.runs; }
  get RL() { return this.session.RL; }
  get T0() { return this.session.T0; }
  get TD() { return this.session.TD; }

  replayPosition(lap: Lap, elapsed: number): [number, number] {
    return posAt(lap, Math.max(0, Math.min(lap.dur, elapsed)));
  }

  curState(): { L: Lap; rel: number } {
    const L = this.laps[this.sel];
    return { L, rel: Math.max(0, Math.min(L.dur, this.T + this.T0 - L.t0)) };
  }
  refLap(): Lap | null {
    switch (this.cmp) {
      case "best": return Number.isFinite(this.analysis.bestLap) ? this.analysis.bestRun : null;
      case "theo": return this.analysis.theoLap();
      case "lap": return this.laps[this.ghostLapI] ?? null;
      case "external":
        if (this.externalGhost) this.externalGhost.sec = sectorTimes(this.externalGhost, this.session.gates);
        return this.externalGhost;
      default: return null;
    }
  }
  refKey() {
    return this.cmp + "|" + this.ghostLapI + "|" + this.externalKey;
  }
  setExternalGhost(lap: Lap, key: string) {
    this.externalGhost = lap;
    this.externalKey = key;
    this.cmp = "external";
    this.changed();
  }
  lapLabelAt(tms: number) {
    const l = this.laps[this.session.lapIndexAt(tms)];
    return l.kind === "out" ? "Out lap" : l.kind === "partial" ? "In progress" : l.name;
  }
  /** Position of a cone hit on the track. */
  conePos(e: { t: number }) {
    const li = this.session.lapIndexAt(e.t);
    const l = this.laps[li];
    return { l, li, p: posAt(l, Math.max(0, Math.min(l.dur, e.t + this.T0 - l.t0))) };
  }
  /** Segment name and lap time at a cone hit, for lists and toasts. */
  coneWhere(e: { t: number }) {
    const { l } = this.conePos(e);
    const rel = Math.max(0, e.t + this.T0 - l.t0);
    const u = interp(l.ch.t, l.ch.u, rel);
    const sg = segAt(this.session.segs, this.RL, u, this.session.open);
    return { lap: l, rel, seg: sg >= 0 ? this.session.segs[sg].name : "" };
  }

  /* ================= change notification ================= */
  /** Something other than the clock changed. */
  changed(forceStatic = true) {
    this.ui.bump();
    this.schedule(forceStatic);
  }

  set<K extends "mode" | "cmp" | "ghostLapI" | "ghostAll" | "showSeg" | "showCones" | "raceMode" | "consMode" | "layoutOnly" | "baseIdx" | "endMode" | "rate">(k: K, v: this[K]) {
    (this as unknown as Record<string, unknown>)[k] = v;
    if (k === "baseIdx") lsSet("base:" + this.session.D.meta.sessId, v);
    if (k === "mode" || k === "endMode" || k === "rate") this.savePrefs();
    this.changed();
  }
  setUnits(u: Units) {
    this.units = u;
    this.savePrefs();
    this.changed();
  }
  savePrefs() {
    lsSet("prefs", { mode: this.mode, endMode: this.endMode, units: this.units, rate: this.rate } satisfies Prefs);
  }

  /* ================= frame loop ================= */
  addPainter(id: string, fn: () => void, live: boolean) {
    this.painters.set(id, { fn, live });
    this.schedule(true);
    return () => {
      this.painters.delete(id);
    };
  }
  schedule(force = false) {
    if (force) this.forceStatic = true;
    if (this.dirty || this.disposed || typeof requestAnimationFrame === "undefined") return;
    this.dirty = true;
    requestAnimationFrame(() => this.runFrame());
  }
  /** Paint everything once. Exposed so tests and screenshots can force a frame. */
  runFrame() {
    this.dirty = false;
    if (this.disposed) return;
    const li = this.session.lapIndexAt(this.T);
    if (li !== this.sel || this.lastSel < 0) {
      this.sel = li;
      this.lastSel = li;
      this.forceStatic = true;
      this.ui.bump();
    }
    const more = this.stepCam(performance.now());
    this.updateLoc();
    this.painters.forEach((p) => {
      if (p.live || this.forceStatic) p.fn();
    });
    this.forceStatic = false;
    this.frame.bump();
    if (more) this.schedule();
  }
  dispose() {
    this.disposed = true;
    this.playing = false;
    if (this.videoUrl) URL.revokeObjectURL(this.videoUrl);
  }
  private updateLoc() {
    const { L, rel } = this.curState();
    const s = interp(L.ch.t, L.ch.u, rel);
    this.curSeg = segAt(this.session.segs, this.RL, s, this.session.open);
    this.curZone = zoneAt(this.session.zones, this.RL, s, this.session.open);
  }

  /* ================= playback ================= */
  setT(ms: number) {
    this.T = Math.max(0, Math.min(this.TD, ms));
    this.schedule();
    clearTimeout(this.posTimer);
    this.posTimer = setTimeout(() => this.savePos(), 500);
  }
  savePos() {
    lsSet("pos:" + this.session.D.meta.sessId, Math.round(this.T));
  }
  seekLap(i: number) {
    this.setT(this.laps[i].t0 - this.T0);
  }
  stepLap(d: number) {
    const order = this.session.TLAPS.map((l) => this.laps.indexOf(l));
    const k = order.indexOf(this.sel);
    const rel = this.T + this.T0 - this.laps[this.sel].t0;
    let n = k + d;
    if (d < 0 && rel > 1500) n = k;
    n = Math.max(0, Math.min(order.length - 1, n));
    this.seekLap(order[n]);
  }
  play(on: boolean) {
    this.playing = on;
    if (on) {
      if (this.endMode === "stop" && this.T >= this.laps[this.sel].t1 - this.T0 - 80 && this.T < this.TD - 80) this.T = this.laps[this.sel].t1 - this.T0 + 1;
      if (this.T >= this.TD - 50) this.T = 0;
      this.lastTs = performance.now();
      requestAnimationFrame(this.tick);
    }
    this.changed(false);
  }
  private tick = (now: number) => {
    if (!this.playing || this.disposed) return;
    const dt = (now - this.lastTs) * this.rate;
    this.lastTs = now;
    let nt = this.T + dt;
    if (this.loopA != null && this.loopB != null && this.loopB > this.loopA && this.T <= this.loopB && nt > this.loopB) nt = this.loopA + (nt - this.loopB);
    const l = this.laps[this.sel];
    const end = l.t1 - this.T0;
    if (nt >= end) {
      if (this.endMode === "loop") nt = l.t0 - this.T0 + (nt - end);
      else if (this.endMode === "stop") {
        this.setT(end - 1);
        this.play(false);
        return;
      }
    }
    if (nt >= this.TD) {
      this.setT(this.TD);
      this.play(false);
      return;
    }
    this.setT(nt);
    requestAnimationFrame(this.tick);
  };
  stepFix(dir: 1 | -1) {
    const { L } = this.curState();
    const rel = this.T + this.T0 - L.t0;
    const ts = L.ch.t;
    let i = ts.findIndex((v) => v > rel + 1);
    if (dir < 0) {
      i = ts.length - 1;
      for (let j = ts.length - 1; j >= 0; j--) if (ts[j] < rel - 1) { i = j; break; }
    }
    if (i >= 0) this.setT(L.t0 - this.T0 + ts[i]);
  }
  cycleEnd() {
    this.set("endMode", this.endMode === "go" ? "stop" : this.endMode === "stop" ? "loop" : "go");
  }
  cycleRate(d: 1 | -1) {
    const k = RATES.indexOf(this.rate);
    this.set("rate", RATES[Math.max(0, Math.min(RATES.length - 1, k + d))]);
  }

  setNoteOpen(on: boolean) {
    this.noteOpen = on;
    this.changed(false);
  }

  /* ================= loops ================= */
  setLoopA() { this.loopA = this.T; if (this.loopB != null && this.loopB <= this.loopA) this.loopB = null; this.changed(false); }
  setLoopB() { this.loopB = this.T; if (this.loopA != null && this.loopA >= this.loopB) this.loopA = null; this.changed(false); }
  clearLoop() { this.loopA = this.loopB = null; this.changed(false); }
  loopSegment() {
    if (this.curSeg < 0) { this.toast("Move the playhead onto a corner or straight first."); return; }
    const { L } = this.curState();
    const s = this.session.segs[this.curSeg];
    const ps = pieces(s.a, s.len, this.RL);
    const tA = interp(L.ch.u, L.ch.t, ps[0][0]) - 500;
    const tB = interp(L.ch.u, L.ch.t, ps[ps.length - 1][1]) + 500;
    this.loopA = Math.max(0, L.t0 - this.T0 + Math.max(0, tA));
    this.loopB = L.t0 - this.T0 + Math.min(L.dur, tB);
    this.setT(this.loopA);
    this.changed(false);
    this.toast("Looping " + s.name + ". Press X to clear the loop.");
  }

  /* ================= camera ================= */
  /** Size the canvas box and work out the screen transform for the current view. */
  fit(W: number, H: number) {
    this.W = W;
    this.H = H;
    const { bbox } = this.session;
    this.scFit = Math.min((W - 2 * Player.PADX) / (bbox.x[1] - bbox.x[0]), (H - Player.PADT - Player.PADB) / (bbox.y[1] - bbox.y[0]));
    this.cyScreen = (Player.PADT + H - Player.PADB) / 2;
    this.sc = this.scFit * this.view.z;
    this.ox = W / 2 - this.sc * this.view.cx;
    this.oy = this.cyScreen + this.sc * this.view.cy;
  }
  X = (x: number) => this.ox + this.sc * x;
  Y = (y: number) => this.oy - this.sc * y;
  get centre(): View {
    const { bbox } = this.session;
    return { cx: (bbox.x[0] + bbox.x[1]) / 2, cy: (bbox.y[0] + bbox.y[1]) / 2, z: 1 };
  }
  private get diag() {
    const { bbox } = this.session;
    return Math.hypot(bbox.x[1] - bbox.x[0], bbox.y[1] - bbox.y[0]);
  }
  flyTo(cx: number, cy: number, z: number, dur = 800) {
    z = Math.max(1, Math.min(14, z));
    this.anim = { f: { ...this.view }, t: { cx, cy, z }, t0: performance.now(), dur };
    this.schedule();
  }
  private stepCam(now: number): boolean {
    let more = false;
    const v = this.view;
    if (this.anim) {
      const { f, t, t0, dur } = this.anim;
      const p = Math.min(1, (now - t0) / dur);
      const e = ease(p);
      const dist = Math.hypot(t.cx - f.cx, t.cy - f.cy) / this.diag;
      const dip = Math.sin(Math.PI * e) * Math.min(0.8, dist * 1.8);
      v.cx = f.cx + (t.cx - f.cx) * e;
      v.cy = f.cy + (t.cy - f.cy) * e;
      v.z = Math.max(1, Math.exp(Math.log(f.z) + (Math.log(t.z) - Math.log(f.z)) * e - dip));
      if (p >= 1) {
        v.cx = t.cx; v.cy = t.cy; v.z = t.z;
        this.anim = null;
      } else more = true;
    } else if (this.follow) {
      const { L, rel } = this.curState();
      const fp = posAt(L, rel);
      const dx = fp[0] - v.cx;
      const dy = fp[1] - v.cy;
      const dz = this.followZ - v.z;
      v.cx += dx * 0.18; v.cy += dy * 0.18; v.z += dz * 0.12;
      more = Math.abs(dx) * this.sc > 0.4 || Math.abs(dy) * this.sc > 0.4 || Math.abs(dz) > 0.01;
    }
    return more;
  }
  /** The user took the camera: drop any animation, follow and focus. */
  takeCamera() {
    this.anim = null;
    this.focus = null;
    if (this.follow) { this.follow = false; this.ui.bump(); }
  }
  setFollow(on: boolean) {
    this.follow = on;
    if (on) { this.anim = null; this.focus = null; }
    this.changed(false);
  }
  flyFit() {
    this.focus = null;
    this.follow = false;
    const c = this.centre;
    this.flyTo(c.cx, c.cy, 1, 750);
    this.ui.bump();
  }
  zoomBy(f: number) {
    this.anim = null;
    if (this.follow) this.follow = false;
    this.flyTo(this.view.cx, this.view.cy, this.view.z * f, 450);
    this.ui.bump();
  }
  /** Zoom to fit an arc of the loop. */
  zoomArc(a: number, len: number, fo: Focus) {
    const p = this.session.geo.arcW(a, len);
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
    p.forEach((q) => {
      x0 = Math.min(x0, q[0]); x1 = Math.max(x1, q[0]);
      y0 = Math.min(y0, q[1]); y1 = Math.max(y1, q[1]);
    });
    const bw = Math.max(x1 - x0, 18);
    const bh = Math.max(y1 - y0, 18);
    const k = 1.5;
    const z = Math.min((this.W - 2 * Player.PADX) / (bw * k), (this.H - Player.PADT - Player.PADB) / (bh * k)) / this.scFit;
    this.focus = fo;
    this.follow = false;
    this.flyTo((x0 + x1) / 2, (y0 + y1) / 2, z, 850);
    this.ui.bump();
  }
  seekToS(s: number, leadMs = 0) {
    const L = this.laps[this.sel];
    this.setT(L.t0 - this.T0 + Math.max(0, interp(L.ch.u, L.ch.t, s) - leadMs));
  }
  focusSeg(i: number, seek = true) {
    const s = this.session.segs[i];
    if (!s) return;
    this.zoomArc(s.a, s.len, { a: s.a, len: s.len, type: s.type, name: s.name });
    if (seek) this.seekToS(s.a, 300);
  }
  focusZone(i: number, seek = true) {
    const z = this.session.zones[i];
    if (!z) return;
    const a = wrap(z.s0 - 18, this.RL);
    this.zoomArc(a, z.len + 34, { a: z.s0, len: z.len, type: "b", name: z.name });
    if (seek) this.seekToS(z.s0, 700);
  }
  private curU() {
    const { L, rel } = this.curState();
    return wrap(interp(L.ch.t, L.ch.u, rel), this.RL);
  }
  jumpZone(dir: 1 | -1) {
    const zs = this.session.zones;
    if (!zs.length) return;
    const u = this.curU();
    let idx = -1;
    if (dir > 0) {
      idx = zs.findIndex((z) => z.s0 > u + 3);
      if (idx < 0) idx = 0;
    } else {
      for (let i = zs.length - 1; i >= 0; i--) if (zs[i].s0 < u - 3) { idx = i; break; }
      if (idx < 0) idx = zs.length - 1;
    }
    if (this.view.z > 1.3) this.focusZone(idx);
    else this.seekToS(zs[idx].s0, 700);
  }
  jumpSeg(dir: 1 | -1) {
    const n = this.session.segs.length;
    if (!n) return;
    const k = ((this.curSeg < 0 ? 0 : this.curSeg) + dir + n) % n;
    if (this.view.z > 1.3) this.focusSeg(k);
    else this.seekToS(this.session.segs[k].a, 300);
  }

  /* ================= what the user marks ================= */
  /** Apply a change to the user's notes, rebuild what depends on them, and save. */
  update(fn: (u: UserState) => void) {
    const u = this.user;
    fn(u);
    this.analysis.recompute(u);
    saveUserState(this.session.D.meta.sessId, u);
    this.changed();
  }
  setSetup(k: string, v: string | number | null) {
    this.update((u) => {
      if (v == null || v === "" || (typeof v === "number" && isNaN(v))) delete u.setup[k];
      else u.setup[k] = v;
    });
  }
  setFlag(n: number, patch: LapFlag) {
    this.update((u) => {
      const f = { ...(u.flags[n] || {}), ...patch };
      if (!f.f) delete f.f;
      if (!f.x) delete f.x;
      if (Object.keys(f).length) u.flags[n] = f;
      else delete u.flags[n];
    });
  }
  skipLaps(ns: number[]) {
    this.update((u) => {
      ns.forEach((n) => {
        u.flags[n] = { ...(u.flags[n] || {}), x: 1 };
      });
    });
  }
  /** Pin a cone to a moment on the session clock. Returns false if that moment is not on a timed lap. */
  addConeAt(tt: number): boolean {
    const l = this.laps[this.session.lapIndexAt(tt)];
    if (l.kind !== "lap") {
      this.toast(l.kind === "partial" ? "That is the unfinished final lap. Cones are only counted on completed laps." : "Cones are counted on timed laps. Move the playhead onto one.");
      return false;
    }
    this.update((u) => {
      u.ce.push({ id: Date.now().toString(36) + Math.random().toString(36).slice(2, 5), t: Math.round(tt) });
      u.ce.sort((a, b) => a.t - b.t);
    });
    const w = this.coneWhere({ t: tt });
    this.toast("Cone on " + l.name + " at " + clock(w.rel) + (w.seg ? " in " + w.seg : "") + ". " + this.analysis.conesOf(l) + " on this lap.");
    return true;
  }
  coneKey(d: 1 | -1) {
    const L = this.laps[this.sel];
    if (L.kind !== "lap") {
      this.toast(L.kind === "partial" ? "That is the unfinished final lap. Cones are only counted on completed laps." : "Cones are counted on timed laps. Move the playhead onto one.");
      return;
    }
    if (d > 0) { this.addConeAt(this.T); return; }
    const ev = this.analysis.coneEvIn(L);
    const n = this.session.lapNo(L);
    if (ev.length) {
      let b = ev[0];
      ev.forEach((e) => { if (Math.abs(e.t - this.T) < Math.abs(b.t - this.T)) b = e; });
      this.update((u) => { this.removeTimedCone(u, u.ce.indexOf(b)); });
      this.toast("Removed the cone nearest the playhead on " + L.name + ".");
    } else if ((this.user.cones[n] || 0) > 0) {
      this.setCone(n, -1);
      this.toast(L.name + ": removed an untimed cone.");
    } else this.toast(L.name + " has no cones to remove.");
  }
  /** The table's + and −: a timed hit if the playhead is on that lap, otherwise an untimed count. */
  setCone(n: number, d: 1 | -1) {
    const l = this.laps[n - 1];
    if (!l || l.kind !== "lap") return;
    if (d > 0) {
      if (this.T + this.T0 >= l.t0 && this.T + this.T0 < l.t1) { this.addConeAt(this.T); return; }
      this.update((u) => { u.cones[n] = (+u.cones[n] || 0) + 1; });
      this.toast(l.name + ": cone counted without a time, because the playhead is on another lap.");
      return;
    }
    const ev = this.analysis.coneEvIn(l);
    this.update((u) => {
      if (ev.length) { this.removeTimedCone(u, u.ce.indexOf(ev[ev.length - 1])); return; }
      const c = Math.max(0, (+u.cones[n] || 0) - 1);
      if (c) u.cones[n] = c;
      else delete u.cones[n];
    });
  }
  deleteCone(i: number) {
    this.update((u) => { this.removeTimedCone(u, i); });
  }
  private removeTimedCone(user: UserState, index: number) {
    const event = user.ce[index];
    if (!event) return;
    if (event.id.startsWith("export:cone:"))
      user.dismissedCones = [...new Set([...(user.dismissedCones ?? []), event.id])];
    user.ce.splice(index, 1);
  }
  addMark(text: string, category: "note" | "braking" | "cone" | "offcourse" | "video" = "note") {
    this.update((u) => {
      u.marks.push({ id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), t: Math.round(this.T), text, category,
        ...(this.videoUrl ? { video: { key: this.videoKey, name: this.videoName, time: this.videoCurrentTime } } : {}) });
      u.marks.sort((a, b) => a.t - b.t);
    });
    this.toast("Note added at " + clock(this.T));
  }
  seekMark(id: string) {
    const mark = this.user.marks.find(m => m.id === id);
    if (!mark) return;
    this.play(false); this.setT(mark.t);
    if (mark.video && typeof window !== "undefined") window.dispatchEvent(new CustomEvent("driveday:bookmark", { detail: { run: this.session.D.meta.sessId, ...mark.video } }));
  }
  deleteMark(i: number) {
    this.update((u) => { u.marks.splice(i, 1); });
  }
}
