import type { AlertDef } from "@/lib/driveday/analysis";
import { cvT, tU, type Units } from "@/lib/driveday/units";

/** A limit or reading for an alert, in the viewer's units. */
export const fmtLimit = (u: Units, d: AlertDef, v: number) => (d.temp ? cvT(u, v).toFixed(0) + tU(u) : v.toFixed(2) + " V");
