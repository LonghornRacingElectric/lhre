"use client";

import { sectorClass } from "@/lib/analysis";
import { clock, fmt } from "@/lib/format";
import { interp } from "@/lib/math";
import { cvS, cvT, sU, tU } from "@/lib/units";
import { usePlayer, useFrame, useUi } from "./context";

/* The overlay on the map: lap, clock, sector splits, gap to the ghost, and the car's speed and pedals. */

const SEC_VAR = ["--s1", "--s2", "--s3"];

export function HudLeft() {
  const p = usePlayer();
  useUi(p);
  useFrame(p);
  const { L, rel } = p.curState();
  const a = p.analysis;
  const R = p.refLap();
  const nm = L.kind === "out" ? "Out lap" : L.kind === "partial" ? L.name.replace(" (in progress)", "") : L.name;
  const cones = a.conesOf(L);

  let secs = null;
  if (L.sec && L.sec[0] != null) {
    const e0 = L.sec[0] as number;
    const e1 = e0 + (L.sec[1] as number);
    const act = rel < e0 ? 0 : rel < e1 ? 1 : 2;
    const starts = [0, e0, e1];
    secs = (
      <div className="secs">
        {[0, 1, 2].map((k) => {
          const done = (k === 0 && rel >= e0) || (k === 1 && rel >= e1) || (k === 2 && L.sec[2] != null && rel >= L.dur - 1);
          const state = done ? "done" : k === act ? "act" : "fut";
          const val = done ? (L.sec[k] as number) : k === act ? rel - starts[k] : null;
          return (
            <div key={k} className={"sch " + state}>
              <span><i style={{ background: `var(${SEC_VAR[k]})` }} />S{k + 1}</span>
              <span className={"c0 " + (done ? "c " + sectorClass(a, L, k) : "")}>{val != null ? fmt(val, 2) : "—"}</span>
            </div>
          );
        })}
      </div>
    );
  }
  let gap = null;
  if (R && R !== L) {
    const tc0 = interp(L.ch.u, L.ch.t, 0);
    const tr0 = interp(R.ch.u, R.ch.t, 0);
    const u = interp(L.ch.t, L.ch.u, rel);
    const g = rel - tc0 - (interp(R.ch.u, R.ch.t, u) - tr0);
    gap = (
      <div className={"gap " + (g > 0 ? "pos" : "neg")}>
        {g >= 0 ? "+" : "−"}{(Math.abs(g) / 1000).toFixed(2)}<small>vs {R.name}</small>
      </div>
    );
  }
  const seg = p.curSeg >= 0 ? p.session.segs[p.curSeg] : null;
  const zone = p.curZone >= 0 ? p.session.zones[p.curZone] : null;
  const alerts = a.activeAlerts(L, rel);
  return (
    <div className="hud hud-l" id="hudl">
      <div className="lapname">
        {nm}
        {cones ? <span style={{ color: "var(--red)", fontSize: 16 }}> {cones} cone{cones > 1 ? "s" : ""}</span> : null}
      </div>
      <div className="clock">{clock(rel)}</div>
      {secs}
      {gap}
      {seg && (
        <div className="seginfo">
          {seg.name}{seg.type === "c" ? (seg.dir > 0 ? " · left" : " · right") : ""}
          {zone && <> · <b>{zone.name} braking</b></>}
        </div>
      )}
      {alerts.map((x) => (
        <div key={x.d.k} className="alertb">⚠ {x.d.label} {x.d.temp ? cvT(p.units, x.v).toFixed(0) + tU(p.units) : x.v.toFixed(2) + " V"}</div>
      ))}
    </div>
  );
}

export function HudRight() {
  const p = usePlayer();
  useUi(p);
  useFrame(p);
  const { L, rel } = p.curState();
  const V = (k: "spd" | "thr" | "brkp" | "gacc" | "kw" | "rpm") => interp(L.ch.t, L.ch[k], rel);
  const g = V("gacc");
  return (
    <div className="hud hud-r" id="hudr">
      <div className="big">{cvS(p.units, V("spd")).toFixed(0)}<small>{sU(p.units).toUpperCase()}</small></div>
      <div className="bars">
        <div>THR<em><i style={{ width: V("thr").toFixed(0) + "%", background: "var(--green)" }} /></em></div>
        <div>BRK<em><i style={{ width: V("brkp").toFixed(0) + "%", background: "var(--red)" }} /></em></div>
      </div>
      <div className="mono">{g >= 0 ? "+" : "−"}{Math.abs(g).toFixed(2)} g · {V("kw").toFixed(0)} kW · {Math.round(V("rpm"))} rpm</div>
    </div>
  );
}

/** Both halves of the overlay. */
export function Hud() {
  return (
    <>
      <HudLeft />
      <HudRight />
    </>
  );
}
