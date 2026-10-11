import type { AlertDef } from "@/lib/analysis";
import { cvT, tU, type Units } from "@/lib/units";

/** A limit or reading for an alert, in the viewer's units. */
export const fmtLimit = (u: Units, d: AlertDef, v: number) => (d.temp ? cvT(u, v).toFixed(0) + tU(u) : v.toFixed(2) + " V");
