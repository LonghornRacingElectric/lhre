"use client";

import { interp } from "@/lib/math";
import { heat } from "@/lib/draw/palette";
import type { ChKey } from "@/lib/types";
import { cvS, cvT, sU, tU } from "@/lib/units";
import { useFrame, usePlayer, useUi } from "../context";
import { Tile } from "./Tile";

type Ch = ChKey | "spd" | "gacc" | "brkp" | "brkrp";

const heatHsl = (v: number, lo: number, hi: number) => heat(v, lo, hi);

const WHEELS: [string, string, number, number][] = [
  ["FL", "0", 37, 146],
  ["FR", "1", 243, 146],
  ["RL", "2", 35, 374],
  ["RR", "3", 245, 374],
];

/** A top-down car coloured by component temperature, with the live readings beside it. */
export function CarPanel() {
  const p = usePlayer();
  useUi(p);
  useFrame(p);
  const { L, rel } = p.curState();
  const V = (k: Ch) => interp(L.ch.t, L.ch[k] as number[], rel);
  const u = p.units;
  const T = (k: ChKey) => cvT(u, V(k));
  const sp = p.user.setup;
  const bf = V("brkp") / 100;
  const br = V("brkrp") / 100;
  const kw = V("kw");
  const gacc = V("gacc");
  const accPct = (Math.abs(gacc) / (p.session.accScale / 9.80665)) * 100;
  const stroke = { stroke: "var(--mute)", strokeWidth: 1.5 };
  return (
    <>
      <div className="carwrap">
        <svg id="car" viewBox="0 0 280 440" role="img" aria-label="Top-down car coloured by component temperature">
          <g fill="none" stroke="var(--mute)" strokeWidth={2}>
            <path d="M60 98 L118 106 M220 98 L162 106 M58 322 L118 318 M222 322 L162 318" />
            <path d="M62 319 L218 319" strokeWidth={3} />
          </g>
          <rect x={92} y={14} width={96} height={8} rx={3} fill="var(--track)" />
          <path d="M128 26 L152 26 L160 108 L120 108Z" fill="var(--panel)" {...stroke} />
          <rect x={104} y={100} width={72} height={196} rx={16} fill="var(--panel)" {...stroke} />
          <rect x={62} y={168} width={36} height={104} rx={9} style={{ fill: heatHsl(V("coolT"), 25, 70) }} {...stroke} />
          <rect x={182} y={168} width={36} height={104} rx={9} style={{ fill: heatHsl(V("coolT"), 25, 70) }} {...stroke} />
          <rect x={114} y={220} width={52} height={68} rx={6} style={{ fill: heatHsl(V("cellT"), 22, 48) }} {...stroke} />
          <rect x={116} y={304} width={48} height={32} rx={6} style={{ fill: heatHsl(V("motT"), 25, 90) }} {...stroke} />
          <rect x={124} y={342} width={32} height={26} rx={5} style={{ fill: heatHsl(V("invT"), 25, 100) }} {...stroke} />
          <rect x={82} y={398} width={116} height={10} rx={3} fill="var(--track)" />
          <ellipse cx={140} cy={168} rx={17} ry={30} fill="var(--bg)" {...stroke} />
          <g transform={`translate(140 138) rotate(${V("steer").toFixed(1)})`}>
            <circle r={12} fill="none" stroke="var(--ink)" strokeWidth={3} />
            <path d="M-12 0 H12 M0 0 V12" stroke="var(--ink)" strokeWidth={3} />
            <circle cy={-12} r={2.6} fill="var(--s3)" />
          </g>
          <g fill="var(--track)" {...stroke} strokeDasharray="4 3">
            <rect x={18} y={62} width={38} height={66} rx={8} />
            <rect x={224} y={62} width={38} height={66} rx={8} />
            <rect x={12} y={282} width={46} height={74} rx={9} />
            <rect x={222} y={282} width={46} height={74} rx={9} />
          </g>
          <g style={{ fill: "var(--red)" }}>
            <circle cx={62} cy={95} r={8} style={{ opacity: 0.1 + 0.9 * bf }} />
            <circle cx={218} cy={95} r={8} style={{ opacity: 0.1 + 0.9 * bf }} />
            <circle cx={64} cy={319} r={8} style={{ opacity: 0.1 + 0.9 * br }} />
            <circle cx={216} cy={319} r={8} style={{ opacity: 0.1 + 0.9 * br }} />
          </g>
          <g fontFamily="IBM Plex Mono,monospace" fontSize={9} textAnchor="middle" fill="var(--mute)">
            {WHEELS.map(([k, , x, y]) => {
              const hot = sp["hot" + k] as number | undefined;
              const cold = sp["cold" + k] as number | undefined;
              const has = hot != null || cold != null;
              return (
                <g key={k}>
                  <text x={x} y={y} style={{ fill: has ? "var(--ink)" : "var(--mute)" }}>{hot != null ? hot : cold != null ? cold : "—"} psi</text>
                  <text x={x} y={y + 11} fontSize={7}>{hot != null && cold != null ? "cold " + cold : hot != null ? "hot, entered" : cold != null ? "cold, entered" : "not logged"}</text>
                </g>
              );
            })}
          </g>
          <g fontFamily="IBM Plex Mono,monospace" textAnchor="middle" fill="#fff" stroke="rgba(0,0,0,.5)" strokeWidth={2.4} paintOrder="stroke" fontWeight={600}>
            <text x={140} y={238} fontSize={8}>ACCUM</text>
            <text x={140} y={256} fontSize={15}>{V("soc").toFixed(0)}%</text>
            <text x={140} y={276} fontSize={9}>{V("packv").toFixed(0)} V</text>
            <text x={140} y={318} fontSize={8}>MOTOR</text>
            <text x={140} y={330} fontSize={9}>{Math.round(V("rpm"))} rpm</text>
            <text x={140} y={354} fontSize={8}>INV</text>
            <text x={140} y={364} fontSize={9}>{T("invT").toFixed(0) + tU(u)}</text>
            <text x={80} y={214} fontSize={8}>COOL</text>
            <text x={80} y={228} fontSize={10}>{T("coolT").toFixed(0)}°</text>
            <text x={200} y={214} fontSize={8}>COOL</text>
            <text x={200} y={228} fontSize={10}>{T("coolT").toFixed(0)}°</text>
          </g>
        </svg>
        <div className="tiles" id="tiles">
          <Tile label="Speed (GPS)" value={cvS(u, V("spd")).toFixed(1)} unit={sU(u)} />
          <Tile label="Accel (GPS)" value={(gacc >= 0 ? "+" : "−") + Math.abs(gacc).toFixed(2)} unit="g" meter={[accPct, gacc >= 0 ? "--green" : "--red"]} />
          <Tile label="Motor speed" value={Math.round(V("rpm"))} unit="rpm" />
          <Tile label="Torque" value={V("trq").toFixed(0)} unit="Nm" />
          <Tile label={kw < -0.5 ? "DC power (regen)" : "DC power"} value={kw.toFixed(1)} unit="kW" />
          <Tile label="DC current" value={V("dca").toFixed(0)} unit="A" />
          <Tile label="Pack voltage" value={V("packv").toFixed(0)} unit="V" />
          <Tile label="Throttle" value={V("thr").toFixed(0)} unit="%" meter={[V("thr"), "--green"]} />
          <Tile label="Front brake" value={V("brkp").toFixed(0)} unit="% of peak" meter={[V("brkp"), "--red"]} />
          <Tile label="Steering" value={V("steer").toFixed(0)} unit="°" />
          <Tile label="State of charge" value={V("soc").toFixed(1)} unit="%" meter={[V("soc"), "--blue"]} />
          <Tile label="Lowest cell" value={V("cellV").toFixed(2)} unit="V" />
          <Tile label="Hottest cell" value={T("cellT").toFixed(1)} unit={tU(u)} />
          <Tile label="Motor" value={T("motT").toFixed(0)} unit={tU(u)} />
          <Tile label="Inverter hotspot" value={T("invT").toFixed(0)} unit={tU(u)} />
          <Tile label="Gate driver" value={T("gateT").toFixed(0)} unit={tU(u)} />
          <Tile label="Coolant" value={T("coolT").toFixed(0)} unit={tU(u)} />
          <Tile label="Pack modules A · B · C" value={T("modA").toFixed(0) + " · " + T("modB").toFixed(0) + " · " + T("modC").toFixed(0)} unit={tU(u)} />
        </div>
      </div>
      <div className="note2">Follows the playhead. Colours run cool blue to hot red as a display scale, not a limit. Tire pressure and wheel speed are not in this log, so the corners show the pressures you enter in Setup.</div>
    </>
  );
}
