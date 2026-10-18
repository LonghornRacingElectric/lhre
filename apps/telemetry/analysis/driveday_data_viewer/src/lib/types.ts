/* The session export written by viewer_tool's trackside-live exporter, and the run data this app builds from it. */

export interface LiveSample {
  t: number;
  source?: string;
  lat: number;
  lon: number;
  speed?: number;
  hv_pack_v?: number;
  hv_c?: number;
  power_kw?: number;
  values?: Record<string, number>;
  cellTemps?: number[];
}

export interface LiveLapExport {
  id: string;
  label: string;
  kind: string;
  startMs: number;
  endMs: number;
  durationMs: number;
  sectors: number[];
  energyWh: number;
  energyOutWh?: number;
  energyInWh?: number;
  distanceM: number;
  avgSpeedMps: number | null;
  samples: LiveSample[];
  maxCellTempC?: number;
  notes?: string;
}

export interface SessionInfoExport {
  id?: string;
  name?: string;
  driver?: string;
  venue?: string;
  eventType?: string;
  [k: string]: unknown;
}

export interface SessionExport {
  flags?: { id?: string | number; t: number; label: string; saved?: boolean }[];
  version?: number;
  savedAt?: number;
  source?: string;
  topic?: string;
  name?: string;
  targetLaps?: number;
  targetEnergyKwh?: number;
  soeCutoffCellV?: number;
  totalEnergyWh?: number;
  laps: LiveLapExport[];
  selectedLapIds?: string[];
  sampleTail: LiveSample[];
  currentLap?: { lapSamples?: LiveSample[] } | null;
  hasSectors?: boolean;
  sessionInfo?: SessionInfoExport | null;
  metadata?: Record<string, string>;
}

/* ---------- converted run ---------- */

/** One GPS fix on a lap: x, y (metres), speed (m/s), arc length on the track (m), distance off the line (m). */
export type Pt = [number, number, number, number, number];
export type XY = [number, number];

export const CH_KEYS = [
  "thr", "brk", "brkr", "steer", "rpm", "trq", "dcv", "dca", "kw", "packv", "soc",
  "motT", "invT", "gateT", "coolT", "cellT", "cellV", "modA", "modB", "modC", "vmaxC", "vavgC",
] as const;
export type ChKey = (typeof CH_KEYS)[number];

/** Per-sample channels as stored. `t` is ms from the lap start, `u` is metres along the lap. */
export type BaseChannels = { t: number[]; u: number[]; cells: number[][] } & Record<ChKey, number[]>;

/** Stored channels plus the ones derived when a run is opened. */
export type Channels = BaseChannels & {
  spd: number[]; // mph, from GPS
  x: number[];
  y: number[];
  brkp: number[]; // front brake, % of the run's peak
  brkrp: number[];
  acc: number[]; // m/s², from GPS speed
  gacc: number[];
  glat: number[]; // signed GPS lateral g; positive left, NaN when unavailable
  cellCols: number[][];
  cellAvg: number[];
};

export type LapKind = "lap" | "out" | "partial" | "theo";

export interface RawLap {
  name: string;
  kind: LapKind;
  t0: number;
  t1: number;
  dur: number;
  sec: (number | null)[];
  pts: Pt[];
  dist: number;
  vmax: number;
  vavg: number;
  dev: number;
  wh: number | null;
  whOut: number | null;
  whIn: number | null;
  ch: BaseChannels;
}

export interface Lap extends Omit<RawLap, "ch" | "sec"> {
  ch: Channels;
  /** Sector times in ms. The third is missing on a lap that is still in progress. */
  sec: (number | null)[];
  /** Smoothed curve through the fixes, four short steps per fix interval. */
  curve: XY[][];
  /** Smoothed positions used only for drawing and playback; pts keeps imported fixes. */
  playbackPts?: Pt[];
  theo?: boolean;
  /** Cached gain-against-ghost, keyed by the ghost it was built for. */
  gainKey?: string;
  gain?: number[];
  gainScale?: number;
}

export type TrackDirection = "cw" | "ccw" | "unknown";

export interface RunMeta {
  /** Inferred from GPS circulation, not a course number or venue name. */
  direction?: TrackDirection;
  cellIdx: number[];
  sessId: string;
  cutoff: number;
  planLaps: number;
  planKwh: number;
  name: string;
  driver: string;
  venue: string;
  event: string;
  trackId: string;
  trackName: string;
  sf?: number;
}

export interface RunData {
  /** Pre-layout processed run for exact detachment; source telemetry is shared until stored. */
  layoutOriginal?: RunData;
  /** Explicit per-run opt-out, also respected by later layout imports. */
  layoutDetached?: boolean;
  /** Exported cone hits assigned to completed laps; t is absolute logger time. */
  coneEvents?: ConeEvent[];
  ref: XY[];
  cum: number[];
  /** Length of the loop, or of the course between the start line and the finish line when `open`. */
  RL: number;
  /** The start and finish lines are in different places: the reference line is a path, not a closed loop. */
  open?: boolean;
  laps: RawLap[];
  meta: RunMeta;
  warnings: string[];
  /** The road edges, when the run is on a base layout that has them (same frame as `ref`). */
  edges?: RoadEdges;
  /** How far the base layout was slid to sit on this run's GPS, in metres. Another run's laps are moved by the difference
   *  when they are laid over this one, so both line up with the same road. */
  layoutShift?: XY;
}

/** A road drawn at its real width: the corrected centre line and the two edges, in metres in the track's frame. */
export interface RoadEdges {
  centre: XY[];
  left: XY[];
  right: XY[];
  width: number;
}

/** A track layout shared by every run driven on it. */
export interface Track {
  id: string;
  name: string;
  date: string;
  event: string;
  num: number;
  venue: string;
  lat0: number;
  lon0: number;
  ref: XY[];
  cum: number[];
  RL: number;
  gates: number[];
  /** A point-to-point course: `ref` runs from the start line to the finish line and is not closed. */
  open?: boolean;
  created: number;
  from: string;
  /** Shipped with the app (src/lib/layouts), not saved from an import. Preferred over saved tracks when both match. */
  builtin?: boolean;
  /** Road edges for drawing, when the layout has them. */
  edges?: RoadEdges;
}

export interface RunListEntry {
  direction?: TrackDirection;
  id: string;
  name: string;
  driver: string;
  venue: string;
  event: string;
  track: string;
  trackId: string;
  ts: number;
  laps: number;
  best: number | null;
  added: number;
}

/* ---------- per-run notes the user adds ---------- */

export interface LapFlag {
  f?: string;
  x?: number;
}
export interface Mark {
  id: string;
  t: number;
  text: string;
}
export interface ConeEvent {
  id: string;
  t: number;
}
export interface UserState {
  /** Imported hits explicitly removed by the user, so reopening does not restore them. */
  dismissedCones?: string[];
  setup: Record<string, string | number>;
  /** Cone counts that were entered without a time, keyed by lap number. */
  cones: Record<string, number>;
  flags: Record<string, LapFlag>;
  marks: Mark[];
  ce: ConeEvent[];
}

export const emptyUserState = (): UserState => ({ setup: {}, cones: {}, flags: {}, marks: [], ce: [] });
