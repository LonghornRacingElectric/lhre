"use client";

import { drawEnergy } from "@/lib/draw/charts";
import { energyModel } from "@/lib/energy";
import { usePainter, usePlayer, useUi } from "../context";
import { Field, Tile } from "./Tile";

/** How many more laps the pack can do, from how fast state of charge and cell voltage have been falling. */
export function EnergyPanel() {
  const p = usePlayer();
  useUi(p);
  const m = energyModel(p.session, p.user);
  const ref = usePainter(p, "energy", (c) => drawEnergy(p, c, m), false);
  const { runs, base } = p.session;
  const sp = p.user.setup;
  let pill = "Not enough data";
  let pillCls = "pill";
  if (m.left != null) {
    const lim = m.vLeft != null ? Math.min(m.left, m.vLeft) : m.left;
    const ok = lim >= m.remain;
    pillCls = "pill " + (ok ? "ok" : "warn");
    pill = (ok ? `Enough for the ${m.remain} plan laps left, ${lim - m.remain} spare` : `Short: ${lim} laps left, plan needs ${m.remain}`) + (m.vLeft != null && m.vLeft < m.left ? " (cell floor limits)" : "");
  }
  return (
    <>
      <div className="enwrap">
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span className="lab">Projection to reserve</span>
          <span className={pillCls}>{pill}</span>
        </div>
        <div className="tiles">
          <Tile label="State of charge" value={m.socNow.toFixed(1)} unit="%" />
          <Tile label="Pack capacity (est.)" value={m.cap ? (m.cap / 1000).toFixed(2) : "—"} unit="kWh" />
          <Tile label="Pace, last 5 laps" value={m.pace.toFixed(1)} unit="Wh/lap" />
          <Tile label={"Pace, laps 3 to " + runs.length} value={m.paceAll.toFixed(1)} unit="Wh/lap" />
          <Tile label={"Laps to " + m.reserve + "% reserve"} value={m.left != null ? m.left : "—"} unit="laps" />
          <Tile label="Lowest cell now" value={m.vNow.toFixed(2)} unit="V" />
          <Tile label={"Laps to " + m.cut.toFixed(2) + " V floor"} value={m.vLeft != null ? m.vLeft : "no trend"} />
          <Tile label={m.planLaps + " laps of this loop"} value={m.need.toFixed(2)} unit="kWh" />
        </div>
        <canvas ref={ref} role="img" aria-label="State of charge per lap with a projection to the reserve level" />
      </div>
      <div className="enin">
        <Field label="Reserve (% SOC)" k="reserve" value={sp.reserve} placeholder="10" onValue={(v) => p.setSetup("reserve", v)} />
        <Field label="Plan laps" k="planLaps" value={sp.planLaps} placeholder={String(p.session.D.meta.planLaps)} onValue={(v) => p.setSetup("planLaps", v)} />
        <Field label="Budget (kWh)" k="planKwh" value={sp.planKwh} placeholder={String(p.session.D.meta.planKwh)} onValue={(v) => p.setSetup("planKwh", v)} />
      </div>
      <div className="note2">
        Capacity comes from how fast state of charge fell against energy used from lap 3 on, using the median slope so single bad readings do not move it. Laps left assume the last-5-lap pace holds. The plan budget of {m.planKwh} kWh was set for a full-length course, so it does not compare directly to this {Math.round(base.dist)} m loop.
      </div>
    </>
  );
}
