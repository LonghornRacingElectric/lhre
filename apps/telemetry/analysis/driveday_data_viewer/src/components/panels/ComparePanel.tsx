"use client";

import { fmt, MPH } from "@/lib/format";
import { cvS, sU } from "@/lib/units";
import { useFrame, usePlayer, useUi } from "../context";

type Kind = "t" | "n1" | "n0";

/** The lap being watched against the ghost lap, side by side. */
export function ComparePanel() {
  const p = usePlayer();
  useUi(p);
  useFrame(p);
  const L = p.laps[p.sel];
  const R = p.refLap();
  if (!R || R === L) {
    return <div className="empty">Pick a ghost under the timeline (Lap…, Best or Best sectors) that is a different lap from the one you are watching. The two are compared here, on the map and in the charts.</div>;
  }
  const u = p.units;
  const mx = (a: number[]) => Math.max(...a);
  const rows: [string, number | null, number | null, Kind][] = [
    ["Lap time", L.dur, R.dur, "t"],
    ["S1", L.sec[0], R.sec[0], "t"],
    ["S2", L.sec[1], R.sec[1], "t"],
    ["S3", L.sec[2], R.sec[2], "t"],
    [`Top speed (${sU(u)})`, cvS(u, L.vmax * MPH), cvS(u, R.vmax * MPH), "n1"],
    [`Average speed (${sU(u)})`, cvS(u, L.vavg * MPH), cvS(u, R.vavg * MPH), "n1"],
    ["Peak power (kW)", mx(L.ch.kw), mx(R.ch.kw), "n0"],
    ["Energy used (Wh)", L.wh, R.wh, "n1"],
  ];
  const show = (v: number | null, k: Kind) => (v == null ? "—" : k === "t" ? fmt(v) : v.toFixed(k === "n1" ? 1 : 0));
  return (
    <>
      <table className="cmpt">
        <thead>
          <tr>
            <th />
            <th><span className="dot" style={{ background: "var(--ink)" }} />{p.session.D.meta.driver || "This run"} · {L.name}</th>
            <th><span className="dot" style={{ background: "var(--mute)" }} />{R.name}</th>
            <th>Diff</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, a, b, k]) => {
            const d = a == null || b == null ? null : a - b;
            let text = "—";
            let cl = "";
            if (d != null) {
              if (k === "t") {
                text = (d >= 0 ? "+" : "−") + (Math.abs(d) / 1000).toFixed(3);
                cl = d > 0 ? "pos" : d < 0 ? "neg" : "";
              } else text = (d >= 0 ? "+" : "−") + Math.abs(d).toFixed(k === "n1" ? 1 : 0);
            }
            return (
              <tr key={label}>
                <td>{label}</td>
                <td>{show(a, k)}</td>
                <td>{show(b, k)}</td>
                <td className={cl}>{text}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="note2">Both laps are lined up at the start/finish line and played together. Red means this lap is slower than the ghost lap, green means faster.</div>
    </>
  );
}
