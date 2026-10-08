import { med, pctl, theil } from "./math";
import type { Session } from "./session";
import type { UserState } from "./types";

/** The BMS state of charge is still settling on the first two laps, so the fit starts at lap 3. */
export const SETTLE = 2;

export interface EnergyModel {
  reserve: number;
  planLaps: number;
  planKwh: number;
  cut: number;
  cap: number | null; // Wh
  socNow: number;
  pace: number; // Wh per lap, last 5
  paceAll: number;
  usable: number | null;
  left: number | null; // laps to the reserve
  vNow: number;
  vLeft: number | null; // laps to the cell-voltage floor
  remain: number;
  need: number; // kWh for the plan
  perLap: number | null; // % SOC per lap
  socLap: number[];
}

const setupNum = (u: UserState, k: string, d: number) => {
  const v = u.setup[k];
  return v != null && v !== "" && !isNaN(+v) ? +v : d;
};

export function energyModel(s: Session, u: UserState): EnergyModel {
  const { runs, D } = s;
  const socLap = runs.map((l) => med(l.ch.soc));
  const vLap = runs.map((l) => pctl(l.ch.cellV, 0.1));
  const cumWh: number[] = [];
  runs.reduce((a, l, i) => {
    cumWh[i] = a + (l.wh ?? 0);
    return cumWh[i];
  }, 0);

  const reserve = setupNum(u, "reserve", 10);
  const planLaps = setupNum(u, "planLaps", D.meta.planLaps);
  const planKwh = setupNum(u, "planKwh", D.meta.planKwh);
  const cut = D.meta.cutoff;
  const idx = runs.map((_, i) => i).filter((i) => i >= SETTLE);
  const slope = theil(idx.map((i) => cumWh[i]), idx.map((i) => socLap[i]));
  const cap = slope < 0 ? -100 / slope : null;
  const socNow = med(socLap.slice(-3));
  const last5 = runs.slice(-5);
  const pace = last5.reduce((a, l) => a + (l.wh ?? 0), 0) / Math.max(1, last5.length);
  const after = runs.slice(SETTLE);
  const paceAll = after.reduce((a, l) => a + (l.wh ?? 0), 0) / Math.max(1, after.length);
  const usable = cap ? Math.max(0, ((socNow - reserve) / 100) * cap) : null;
  const left = usable != null && pace > 0 ? Math.floor(usable / pace) : null;
  const vs = theil(idx, idx.map((i) => vLap[i]));
  const vNow = med(vLap.slice(-3));
  const vLeft = vs < 0 ? Math.floor((vNow - cut) / -vs) : null;
  const remain = Math.max(0, planLaps - runs.length);
  const need = (planLaps * pace) / 1000;
  return { reserve, planLaps, planKwh, cut, cap, socNow, pace, paceAll, usable, left, vNow, vLeft, remain, need, perLap: cap ? (pace / cap) * 100 : null, socLap };
}
