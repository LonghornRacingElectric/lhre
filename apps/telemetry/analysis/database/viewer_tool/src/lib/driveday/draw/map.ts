import { lapCutJoin, roadOf } from "../geometry";
import { MPH } from "../format";
import { Player } from "../player";
import type { Lap, XY } from "../types";
import { palette, ramp, rampSpd, type Palette } from "./palette";

/* The map: base overlay, the lap being watched, ghost, markers. Painted from the Player's state. */

/** Colour and opacity for the stretch of a lap between fix i and i+1. */
export function segPaint(p: Player, L: Lap, i: number, pal: Palette): [string, number] {
  const { session: s, mode } = p;
  const c = L.ch;
  if (mode === "speed") return [rampSpd(((c.spd[i] + c.spd[i + 1]) / (2 * MPH) - s.vlo) / (s.vhi - s.vlo)), 1];
  if (mode === "gain") {
    const g = p.analysis.gainFor(L, p.refLap(), p.refKey());
    if (!g) return [pal.mute, 0.85];
    const v = g[i];
    const m = Math.min(1, Math.abs(v) / (L.gainScale ?? 1));
    return [v > 0 ? pal.red : pal.green, 0.7 + 0.3 * m];
  }
  if (mode === "pedal") {
    const b = (c.brkp[i] + c.brkp[i + 1]) / 2;
    const t = (c.thr[i] + c.thr[i + 1]) / 2;
    if (b > 8) return [pal.red, 0.8 + 0.2 * Math.min(1, b / 60)];
    if (t > 3) return [pal.green, 0.75 + 0.25 * t / 100];
    return [pal.mute, 0.9];
  }
  if (mode === "acc") {
    const a = (c.acc[i] + c.acc[i + 1]) / 2 / s.accScale;
    return [a < 0 ? pal.red : pal.green, 0.7 + 0.3 * Math.min(1, Math.abs(a))];
  }
  const distance = (c.u[i] + c.u[i + 1]) / 2;
  return [pal.sector[s.sectorAt(s.open ? distance : ((distance % s.RL) + s.RL) % s.RL)], 1];
}

export function drawMap(p: Player, canvas: HTMLCanvasElement) {
  const r = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  if (r.width === 0 || r.height === 0) return;
  canvas.width = Math.round(r.width * dpr);
  canvas.height = Math.round(r.height * dpr);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  p.fit(r.width, r.height);
  ctx.clearRect(0, 0, r.width, r.height);
  p.hits = [];

  const { session: s, analysis: a, X, Y } = p;
  const pal = palette();
  const { L, rel } = p.curState();
  const RL = s.RL;
  const zr = p.sc / p.scFit;
  const wS = Math.min(3, Math.sqrt(zr));
  const tw = Math.min(2.2, Math.sqrt(zr));
  const BL = roadOf(s.laps[p.baseIdx]);
  const path = (pts: ArrayLike<number>[]) => {
    ctx.beginPath();
    pts.forEach((q, i) => (i ? ctx.lineTo(X(q[0]), Y(q[1])) : ctx.moveTo(X(q[0]), Y(q[1]))));
  };
  const segCurve = (cu: XY[]) => {
    ctx.beginPath();
    ctx.moveTo(X(cu[0][0]), Y(cu[0][1]));
    for (let k = 1; k < cu.length; k++) ctx.lineTo(X(cu[k][0]), Y(cu[k][1]));
    ctx.stroke();
  };
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  const gates = (): void => {
    ([[0, s.open ? "Start" : "S/F", pal.ink], [s.gates[0], "S1|S2", pal.s1], [s.gates[1], "S2|S3", pal.s2]].concat(s.open ? [[RL, "Finish", pal.ink]] : []) as [number, string, string][]).forEach((g) => {
      const a3 = s.geo.at(g[0]);
      const nx = -Math.sin(a3[2]);
      const ny = Math.cos(a3[2]);
      ctx.strokeStyle = g[2];
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(X(a3[0]) + nx * 14 * wS, Y(a3[1]) - ny * 14 * wS);
      ctx.lineTo(X(a3[0]) - nx * 14 * wS, Y(a3[1]) + ny * 14 * wS);
      ctx.stroke();
      ctx.fillStyle = pal.ink;
      ctx.font = "600 11px " + pal.fMono;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(g[1], X(a3[0]) + nx * (24 + 8 * wS), Y(a3[1]) - ny * (24 + 8 * wS));
    });
  };

  if (p.layoutOnly) {
    /* the standard layout itself: the shared reference line, split at the sector gates, no laps */
    const ref = s.D.ref;
    const cum = s.D.cum;
    ctx.strokeStyle = pal.track;
    ctx.lineWidth = 15 * wS;
    path(ref);
    ctx.stroke();
    ctx.lineWidth = 7 * wS;
    for (let i = 0; i < ref.length - 1; i++) {
      const k = s.sectorAt((cum[i] + cum[i + 1]) / 2);
      ctx.strokeStyle = pal.sector[k];
      ctx.beginPath();
      ctx.moveTo(X(ref[i][0]), Y(ref[i][1]));
      ctx.lineTo(X(ref[i + 1][0]), Y(ref[i + 1][1]));
      ctx.stroke();
    }
    gates();
    ctx.font = "600 12px " + pal.fMono;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillStyle = pal.ink;
    ctx.fillText((s.D.meta.trackName || "Track layout") + " · " + RL.toFixed(0) + " m", 16, p.H - Player.PADB + 34);
    [0, 1, 2].forEach((k) => {
      const x = 16 + k * 84;
      const y = p.H - Player.PADB + 54;
      ctx.fillStyle = pal.sector[k];
      ctx.fillRect(x, y - 5, 14, 10);
      ctx.fillStyle = pal.ink;
      const edges = [0, ...s.gates, RL];
      ctx.fillText("S" + (k + 1) + " " + (edges[k + 1] - edges[k]).toFixed(0) + " m", x + 20, y);
    });
    return;
  }

  /* Shared layout stays continuous; a measured overlay may begin/end at different manual presses. */
  ctx.strokeStyle = pal.track;
  ctx.lineWidth = 15 * wS;
  path(s.D.ref);
  ctx.stroke();
  ctx.lineWidth = 5 * wS;
  for (let i = 0; i < s.D.ref.length - 1; i++) {
    const k = s.sectorAt(s.D.cum[i]);
    ctx.strokeStyle = pal.sector[k];
    ctx.globalAlpha = p.mode === "sec" ? 0.9 : 0.18;
    ctx.beginPath();
    ctx.moveTo(X(s.D.ref[i][0]), Y(s.D.ref[i][1]));
    ctx.lineTo(X(s.D.ref[i + 1][0]), Y(s.D.ref[i + 1][1]));
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  /* focus glow, brake zone bands */
  if (p.focus) {
    ctx.strokeStyle = p.focus.type === "c" ? pal.s2 : p.focus.type === "s" ? pal.blue : pal.red;
    ctx.globalAlpha = 0.3;
    ctx.lineWidth = 30 * wS;
    path(s.geo.arcW(p.focus.a, p.focus.len));
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
  if (p.showSeg) {
    ctx.strokeStyle = pal.red;
    ctx.globalAlpha = 0.55;
    ctx.lineWidth = 11 * wS;
    s.zones.forEach((z) => {
      path(s.geo.arcW(z.s0, z.len));
      ctx.stroke();
    });
    ctx.globalAlpha = 1;
  }
  const paint = p.mode === "pedal" || p.mode === "acc" || p.mode === "gain";
  if (p.ghostAll && !paint) {
    ctx.strokeStyle = pal.mute;
    ctx.globalAlpha = 0.25;
    ctx.lineWidth = 1;
    s.laps.forEach((l) => {
      if (l.kind === "out" || l === L) return;
      path(l.pts);
      ctx.stroke();
    });
    ctx.globalAlpha = 1;
  }
  ctx.strokeStyle = pal.base;
  ctx.globalAlpha = 0.55;
  ctx.lineWidth = 1;
  ctx.setLineDash([2, 3]);
  path(BL);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
  const pts = L.pts;
  const ct = L.ch.t;
  if (p.ghostAll && paint) {
    ctx.lineWidth = 5 * tw;
    s.laps.forEach((l) => {
      if (l.kind !== "lap" || l === L) return;
      for (let i = 0; i < l.pts.length - 1; i++) {
        const pp = segPaint(p, l, i, pal);
        ctx.globalAlpha = pp[1] * 0.2;
        ctx.strokeStyle = pp[0];
        segCurve(l.curve[i]);
      }
    });
    ctx.globalAlpha = 1;
  }
  if (L.kind === "out") ctx.setLineDash([6, 4]);
  // A solid casing separates the selected lap from the road, ghosts and zone bands.
  ctx.strokeStyle = pal.panel;
  ctx.lineWidth = 10 * tw;
  for (const curve of L.curve) segCurve(curve);
  for (let i = 0; i < pts.length - 1; i++) {
    const pp = segPaint(p, L, i, pal);
    ctx.globalAlpha = pp[1] * (ct[i + 1] <= rel ? 1 : 0.8);
    ctx.strokeStyle = pp[0];
    ctx.lineWidth = 6.5 * tw;
    segCurve(L.curve[i]);
  }
  ctx.globalAlpha = 1;
  ctx.setLineDash([]);

  const join = lapCutJoin(L, s.geo);
  if (join) {
    ctx.strokeStyle = pal.mute;
    ctx.lineWidth = 2.5 * tw;
    ctx.setLineDash([4, 4]);
    path(join.points);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  /* cones hit on the lap being watched, as plain orange dots */
  if (p.showCones && p.user.ce.length) {
    const cur = s.laps.indexOf(L);
    p.user.ce.forEach((e) => {
      const c = p.conePos(e);
      if (c.li !== cur) return;
      const x = X(c.p[0]);
      const y = Y(c.p[1]);
      ctx.fillStyle = pal.s2;
      ctx.strokeStyle = pal.panel;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(x, y, 6.5 * Math.min(2.2, Math.sqrt(zr)), 0, 7);
      ctx.fill();
      ctx.stroke();
      p.hits.push({ x, y, r: 12, fn: () => p.setT(Math.max(0, e.t - 500)) });
    });
  }
  /* race replay: every clean lap at the same time since the start/finish line */
  if (p.raceMode) {
    s.runs.filter((l) => a.valid(l)).forEach((l) => {
      const q = p.replayPosition(l, rel);
      const isSel = l === L;
      const x = X(q[0]);
      const y = Y(q[1]);
      ctx.fillStyle = ramp(s.runs.indexOf(l) / Math.max(1, s.runs.length - 1));
      ctx.globalAlpha = isSel ? 1 : 0.88;
      ctx.strokeStyle = pal.panel;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(x, y, isSel ? 7 : 5, 0, 7);
      ctx.fill();
      ctx.stroke();
      ctx.globalAlpha = 1;
      if (isSel || p.view.z > 2.2) {
        ctx.fillStyle = pal.ink;
        ctx.font = "600 10px " + pal.fMono;
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        ctx.fillText(l.name, x, y - 8);
      }
    });
  }
  /* consistency: brake points (red) and slowest points (blue) of every clean lap */
  if (p.consMode) {
    const li = s.laps.indexOf(L);
    const dots = (arr: { x: number; y: number; li: number }[][], col: string) =>
      arr.forEach((ar) =>
        ar.forEach((q) => {
          ctx.globalAlpha = 0.7;
          ctx.fillStyle = col;
          ctx.beginPath();
          ctx.arc(X(q.x), Y(q.y), 3.4, 0, 7);
          ctx.fill();
          if (q.li === li) {
            ctx.globalAlpha = 1;
            ctx.strokeStyle = pal.ink;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(X(q.x), Y(q.y), 6, 0, 7);
            ctx.stroke();
          }
        }),
      );
    dots(a.cons.brake, pal.red);
    dots(a.cons.apex, pal.blue);
    ctx.globalAlpha = 1;
  }
  // Dense 20 Hz fixes must not cover the colour trace; reveal spaced dots when zoomed in.
  if (zr > 2) {
    ctx.fillStyle = pal.ink;
    let last: XY | null = null;
    pts.forEach((q) => {
      const x = X(q[0]), y = Y(q[1]);
      if (last && Math.hypot(x - last[0], y - last[1]) < 8) return;
      last = [x, y];
      ctx.beginPath();
      ctx.arc(x, y, 1.4, 0, 7);
      ctx.fill();
    });
  }
  gates();

  /* corner labels and brake zone badges */
  if (p.showSeg) {
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = "600 11px " + pal.fMono;
    s.segs.forEach((g, i) => {
      if (g.type !== "c") return;
      const a3 = s.geo.at(g.a + g.len / 2);
      const an = a3[2];
      const nx = g.dir > 0 ? Math.sin(an) : -Math.sin(an);
      const ny = g.dir > 0 ? -Math.cos(an) : Math.cos(an);
      const off = 26 + 6 * wS;
      const px = X(a3[0]) + nx * off;
      const py = Y(a3[1]) - ny * off;
      const act = i === p.curSeg;
      const w2 = ctx.measureText(g.name).width + 12;
      ctx.fillStyle = act ? pal.s2 : pal.panel;
      ctx.strokeStyle = pal.s2;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(px - w2 / 2, py - 9, w2, 18, 9);
      else ctx.rect(px - w2 / 2, py - 9, w2, 18);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = act ? pal.bg : pal.ink;
      ctx.fillText(g.name, px, py + 0.5);
      p.hits.push({ x: px, y: py, r: 16, fn: () => p.focusSeg(i) });
    });
    s.zones.forEach((z, i) => {
      const a3 = s.geo.at(z.s0);
      const px = X(a3[0]);
      const py = Y(a3[1]);
      const act = i === p.curZone;
      ctx.fillStyle = pal.red;
      ctx.strokeStyle = pal.panel;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(px, py, act ? 13 : 11, 0, 7);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#fff";
      ctx.font = "700 10.5px " + pal.fMono;
      ctx.fillText(z.name, px, py + 0.5);
      p.hits.push({ x: px, y: py, r: 14, fn: () => p.focusZone(i) });
    });
  }
  if (p.focus && !p.anim) {
    const a3 = s.geo.at(p.focus.a + p.focus.len / 2);
    const px = X(a3[0]);
    const py = Y(a3[1]) - 46;
    ctx.font = "700 20px " + pal.fDisp;
    ctx.textAlign = "center";
    ctx.fillStyle = pal.ink;
    ctx.strokeStyle = pal.panel;
    ctx.lineWidth = 5;
    ctx.lineJoin = "round";
    const tt = p.focus.name + "  ·  " + p.focus.len.toFixed(0) + " m";
    ctx.strokeText(tt, px, py);
    ctx.fillText(tt, px, py);
  }
  /* ghost and the car */
  for (const marker of p.reviewMarkers) {
    const x = X(marker.point[0]), y = Y(marker.point[1]);
    ctx.fillStyle = marker.color; ctx.strokeStyle = pal.panel; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x, y, 5, 0, 7); ctx.fill(); ctx.stroke();
    ctx.font = "600 10px " + pal.fMono; ctx.textAlign = "left"; ctx.textBaseline = "bottom";
    ctx.fillText(marker.label, x + 8, y - 6);
  }
  const R = p.refLap();
  if (R && R !== L) {
    const gp = p.replayPosition(R, rel);
    ctx.strokeStyle = pal.mute;
    ctx.lineWidth = 2;
    ctx.fillStyle = pal.panel;
    ctx.beginPath();
    ctx.arc(X(gp[0]), Y(gp[1]), 6, 0, 7);
    ctx.fill();
    ctx.stroke();
    if (rel >= R.dur) {
      ctx.fillStyle = pal.ink;
      ctx.font = "600 10px " + pal.fMono;
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      ctx.fillText("Finished · " + R.name, X(gp[0]), Y(gp[1]) - 10);
    }
  }
  const mp = p.replayPosition(L, rel);
  const mx = X(mp[0]);
  const my = Y(mp[1]);
  ctx.fillStyle = pal.panel;
  ctx.strokeStyle = pal.ink;
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.arc(mx, my, 8, 0, 7);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = pal.ink;
  ctx.beginPath();
  ctx.arc(mx, my, 3, 0, 7);
  ctx.fill();
}
