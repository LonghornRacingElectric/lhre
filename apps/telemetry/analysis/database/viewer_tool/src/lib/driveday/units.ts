import { KMH_PER_MPH } from "./format";

export interface Units {
  spd: "mph" | "kph";
  temp: "C" | "F";
}

export const defaultUnits = (): Units => ({ spd: "mph", temp: "C" });

/** Speeds are stored in mph; convert for display. */
export const cvS = (u: Units, v: number) => (u.spd === "kph" ? v * KMH_PER_MPH : v);
export const sU = (u: Units) => (u.spd === "kph" ? "km/h" : "mph");
/** Temperatures are stored in °C. */
export const cvT = (u: Units, v: number) => (u.temp === "F" ? (v * 9) / 5 + 32 : v);
/** A temperature difference only scales. */
export const cvTd = (u: Units, v: number) => (u.temp === "F" ? (v * 9) / 5 : v);
export const tU = (u: Units) => (u.temp === "F" ? "°F" : "°C");
