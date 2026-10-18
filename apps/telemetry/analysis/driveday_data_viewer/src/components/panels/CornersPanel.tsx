"use client";

import { segStats, zoneStats } from "@/lib/segments";
import { cvS, sU } from "@/lib/units";
import { useFrame, usePlayer, useUi } from "../context";

const act = (fn: () => void) => ({
  tabIndex: 0,
  onClick: fn,
  onKeyDown: (e: React.KeyboardEvent) => {
    if (e.key === "Enter") fn();
  },
});

/** Times and speeds through each corner, straight and braking zone on the lap being watched. */
export function CornersPanel() {
  const p = usePlayer();
  useUi(p);
  useFrame(p);
  const { L } = p.curState();
  const R = p.refLap();
  const { segs, zones, runs, RL } = p.session;
  const su = sU(p.units);
  const sp = (v: number) => cvS(p.units, v).toFixed(0);
  return (
    <>
      <p className="pdesc">Times and speeds through each segment on the lap you are watching. The gap is against the ghost lap. Click a row to zoom the map there and jump the playhead to it.</p>
      <div className="scroll">
        <table className="tb2">
          <thead>
            <tr><th>Segment</th><th>Length</th><th>Time (s)</th><th>vs ghost</th><th>Entry <span className="usp">{su}</span></th><th>Min <span className="usp">{su}</span></th><th>Exit <span className="usp">{su}</span></th></tr>
          </thead>
          <tbody id="segtb">
            {segs.map((s, i) => {
              const a = segStats(L, s, RL);
              const b = R && R !== L ? segStats(R, s, RL) : null;
              const d = b ? a.t - b.t : null;
              return (
                <tr key={s.name} className={i === p.curSeg ? "on" : ""} {...act(() => p.focusSeg(i))}>
                  <td>
                    <span className={"tg " + s.type}>{s.name}</span>
                    {s.type === "c" && <> <span className="tag">{s.dir > 0 ? "LEFT" : "RIGHT"}</span></>}
                  </td>
                  <td>{s.len.toFixed(0)} m</td>
                  <td>{(a.t / 1000).toFixed(2)}</td>
                  <td><span className={"d " + (d == null ? "z" : d > 0 ? "pos" : "neg")}>{d == null ? "—" : (d >= 0 ? "+" : "−") + Math.abs(d / 1000).toFixed(2)}</span></td>
                  <td>{sp(a.entry)}</td>
                  <td>{sp(a.min)}</td>
                  <td>{sp(a.exit)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="scroll" style={{ borderTop: "1px solid var(--line)" }}>
        <table className="tb2">
          <thead>
            <tr><th>Brake zone</th><th>Starts</th><th>Laps</th><th>Peak brake</th><th>Entry <span className="usp">{su}</span></th><th>Min <span className="usp">{su}</span></th><th>Peak decel</th></tr>
          </thead>
          <tbody id="zonetb">
            {zones.length ? (
              zones.map((z, i) => {
                const a = zoneStats(L, z);
                return (
                  <tr key={z.name} className={i === p.curZone ? "on" : ""} {...act(() => p.focusZone(i))}>
                    <td><span className="tg b">{z.name}</span></td>
                    <td>{z.s0.toFixed(0)} m</td>
                    <td>{z.n}/{runs.length}</td>
                    <td>{a.peak.toFixed(0)}%</td>
                    <td>{sp(a.entry)}</td>
                    <td>{sp(a.min)}</td>
                    <td>{a.dec.toFixed(2)} g</td>
                  </tr>
                );
              })
            ) : (
              <tr><td colSpan={7} style={{ textAlign: "left", color: "var(--mute)" }}>No braking zone repeated on enough laps.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="note2">Corners are where the base path turns tighter than about 18 m radius for at least 6 m. Braking zones are stretches above 8% brake pressure that repeat on at least a quarter of the timed laps. With GPS at about 1.8 Hz some zones are only one or two fixes long, so treat their exact start as a few metres either way.</div>
    </>
  );
}
