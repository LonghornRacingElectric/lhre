"use client";

import { useMemo } from "react";
import { barChart, chartGeo, lineTime, type Col, type LineSeries } from "@/lib/draw/charts";
import { cellStats } from "@/lib/draw/cells";
import type { Player } from "@/lib/player";
import type { Lap } from "@/lib/types";
import { cvT } from "@/lib/units";
import { usePainter, usePlayer, useUi } from "../context";

/* The simple charts: bars per lap and lines over the whole session. Each is a definition plus one shared panel. */

interface ChartDef {
  id: string;
  title: string;
  live: boolean;
  desc: string;
  legend: [string, string][]; // css variable, label
  draw: (p: Player, c: HTMLCanvasElement) => void;
}

function pedalMix(l: Lap): number[] {
  const c = l.ch;
  const n = c.t.length;
  const m = [0, 0, 0, 0];
  let tot = 0;
  for (let i = 0; i < n; i++) {
    const dt = (i < n - 1 ? c.t[i + 1] - c.t[i] : l.dur - c.t[i]) || 1;
    m[c.brkp[i] > 8 ? 3 : c.thr[i] >= 90 ? 0 : c.thr[i] < 3 ? 2 : 1] += dt;
    tot += dt;
  }
  return m.map((v) => (v / tot) * 100);
}

const sel = (p: Player) => p.session.runs.indexOf(p.laps[p.sel]);
const bars = (p: Player, series: { col: Col; vals: number[] }[], fmt: (v: number) => string, hline?: { v: number; label: string }) =>
  (c: HTMLCanvasElement) => barChart(c, { n: p.session.runs.length, series, hline, fmt, selected: sel(p) });

export const CHARTS: ChartDef[] = [
  {
    id: "sect", title: "Sector times", live: false,
    desc: "Seconds in each third of the lap, with the cone penalty stacked on top. Click a bar to jump to that lap.",
    legend: [["--s1", "S1"], ["--s2", "S2"], ["--s3", "S3"], ["--red", "Cone penalty"]],
    draw: (p, c) => {
      const { runs, base } = p.session;
      const a = p.analysis;
      bars(p, [
        ...[0, 1, 2].map((k) => ({ col: (["s1", "s2", "s3"] as Col[])[k], vals: runs.map((l) => (l.sec[k] as number) / 1000) })),
        { col: "red" as Col, vals: runs.map((l) => a.conesOf(l) * a.pen()) },
      ], (v) => v + "s", { v: base.dur / 1000, label: "Lap 1" })(c);
    },
  },
  {
    id: "pedal", title: "Pedal use", live: false,
    desc: "Share of each lap at full throttle, part throttle, off the throttle, and on the brake.",
    legend: [["--green", "Full throttle"], ["--blue", "Part throttle"], ["--track", "Coasting"], ["--red", "Braking"]],
    draw: (p, c) => {
      const pm = p.session.runs.map(pedalMix);
      const cols: Col[] = ["green", "blue", "track", "red"];
      bars(p, cols.map((col, k) => ({ col, vals: pm.map((m) => m[k]) })), (v) => v + "%")(c);
    },
  },
  {
    id: "wh", title: "Energy per lap", live: false,
    desc: "Net energy drawn from the pack on each lap, in watt-hours. The dashed line is the session average.",
    legend: [["--blue", "Net Wh"]],
    draw: (p, c) => {
      const { runs } = p.session;
      const avg = runs.reduce((a, l) => a + (l.wh ?? 0), 0) / runs.length;
      bars(p, [{ col: "blue", vals: runs.map((l) => l.wh ?? 0) }], (v) => String(v), { v: avg, label: "avg " + avg.toFixed(0) + " Wh" })(c);
    },
  },
  {
    id: "kw", title: "Peak power and regen", live: false,
    desc: "Highest DC bus power on each lap, with the strongest regen below zero.",
    legend: [["--purple", "Peak kW"], ["--green", "Peak regen kW"]],
    draw: (p, c) => {
      const { runs } = p.session;
      bars(p, [
        { col: "purple", vals: runs.map((l) => Math.max(...l.ch.kw)) },
        { col: "green", vals: runs.map((l) => Math.min(0, ...l.ch.kw)) },
      ], (v) => String(v))(c);
    },
  },
  {
    id: "soc", title: "State of charge", live: true,
    desc: "Pack state of charge across the timed laps. The playhead and the shaded lap follow the timeline.",
    legend: [["--blue", "SOC %"]],
    draw: (p, c) => lineTime(p, c, { series: [{ col: "blue", arr: (l) => l.ch.soc }], fmt: (v) => v + "%" }),
  },
  {
    id: "pv", title: "Pack voltage", live: true,
    desc: "Pack and DC bus voltage. The dips are sag under hard acceleration.",
    legend: [["--ink", "Pack V"], ["--s2", "DC bus V"]],
    draw: (p, c) => lineTime(p, c, { series: [{ col: "ink", arr: (l) => l.ch.packv }, { col: "s2", arr: (l) => l.ch.dcv }], fmt: (v) => String(v) }),
  },
  {
    id: "cv", title: "Lowest cell voltage", live: true,
    desc: "The weakest cell in the pack. Watch it against your cutoff as the session goes on.",
    legend: [["--red", "Min cell V"]],
    draw: (p, c) => lineTime(p, c, { series: [{ col: "red", arr: (l) => l.ch.cellV }], fmt: (v) => v.toFixed(1) }),
  },
  {
    id: "celltime", title: "Cell temperatures", live: true,
    desc: "All live temperature sensors across the session. The hottest and coolest sensors and the average are picked out.",
    legend: [["--red", "Hottest sensor"], ["--blue", "Coolest sensor"], ["--ink", "Average"], ["--mute", "Other sensors"]],
    draw: (p, c) => {
      const st = cellStats(p);
      const series: LineSeries[] = p.session.D.meta.cellIdx.map((_, j) => ({ col: "mute" as Col, lw: 1, al: 0.4, arr: (l: Lap) => l.ch.cellCols[j] }));
      series.push(
        { col: "blue", lw: 2, arr: (l) => l.ch.cellCols[st.coolJ] },
        { col: "red", lw: 2, arr: (l) => l.ch.cellCols[st.hotJ] },
        { col: "ink", lw: 2, arr: (l) => l.ch.cellAvg },
      );
      lineTime(p, c, { series, fmt: (v) => Math.round(cvT(p.units, v)) + "°" });
    },
  },
  {
    id: "temp", title: "Component temperatures", live: true,
    desc: "Motor, inverter, coolant, hottest cell and pack module C across the session.",
    legend: [["--s2", "Motor"], ["--red", "Inverter"], ["--blue", "Coolant"], ["--green", "Hottest cell"], ["--purple", "Module C"]],
    draw: (p, c) => lineTime(p, c, {
      series: [
        { col: "s2", arr: (l) => l.ch.motT },
        { col: "red", arr: (l) => l.ch.invT },
        { col: "blue", arr: (l) => l.ch.coolT },
        { col: "green", arr: (l) => l.ch.cellT },
        { col: "purple", arr: (l) => l.ch.modC },
      ],
      fmt: (v) => Math.round(cvT(p.units, v)) + "°",
    }),
  },
];

export function ChartPanel({ id }: { id: string }) {
  const p = usePlayer();
  useUi(p);
  const def = useMemo(() => CHARTS.find((c) => c.id === id)!, [id]);
  const ref = usePainter(p, "chart:" + id, (c) => def.draw(p, c), def.live);
  const click = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const c = e.currentTarget;
    const g = chartGeo.get(c);
    if (!g) return;
    const x = e.clientX - c.getBoundingClientRect().left - g.pl;
    if (g.slot != null && g.n != null) {
      const i = Math.floor(x / g.slot);
      if (i >= 0 && i < g.n) p.seekLap(p.laps.indexOf(p.session.runs[i]));
    } else if (g.span != null && g.t0 != null) {
      const s = Math.max(0, Math.min(g.span, (x / g.pw) * g.span));
      p.setT(g.t0 + s * 1000 - p.T0);
    }
  };
  return (
    <>
      <p className="pdesc">{def.desc}</p>
      <div className="lg">
        {def.legend.map(([c, l]) => <span key={l}><i style={{ background: `var(${c})` }} />{l}</span>)}
      </div>
      <canvas ref={ref} className="pc" role="img" aria-label={def.title + " chart"} onPointerDown={click} />
    </>
  );
}
