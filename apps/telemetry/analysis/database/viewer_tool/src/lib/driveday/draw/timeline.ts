import type { Player } from "../player";
import { sU } from "../units";
import { KMH_PER_MPH } from "../format";
import { MONO, palette, setupCanvas } from "./palette";
import { speedEnvelope } from "../timeline-window";

/* The session timeline: speed profile behind lap chapters, with sector ticks, cones, notes, alerts, the loop and the playhead. */

export const TPAD = 8;
export const TOPH = 58;
export const STRIP_Y = 62;
export const STRIP_H = 24;

export const timeX = (p: Player, ms: number, w: number) => TPAD + ((p.timelineTime(ms) - p.timelineRange.start) / p.timelineRange.span) * (w - 2 * TPAD);

export function drawTimeline(p: Player, canvas: HTMLCanvasElement) {
  const c = setupCanvas(canvas);
  if (!c) return;
  const { g, w, h: physicalHeight } = c;
  const pal = palette();
  const h = physicalHeight;
  const stripH = h < 70 ? 16 : 24;
  const topH = h < 70 ? Math.max(14, h - stripH - 18) : h - 50, stripY = topH + 4;
  const { session: s, analysis: a } = p;
  const { TLAPS, T0, laps } = s;
  const ranked = laps.filter(l => l.kind === "lap" && a.valid(l));
  const slowest = Math.max(...ranked.map(l => l.dur));
  const vmaxMph = Math.max(1, s.vmaxMph);
  const cur = laps[p.sel];
  const range = p.timelineRange;
  const tx = (ms: number) => timeX(p, ms, w);
  const visibleLaps = TLAPS.filter(l => l.kind !== "out");
  const traces = visibleLaps.filter(l => p.timelineTime(l.t1 - T0) >= range.start && p.timelineTime(l.t0 - T0) <= range.end).map(l => ({ l, indices: speedEnvelope(l.ch.t, l.ch.spd, p.timelineSessionTime(range.start) - l.t0 + T0, p.timelineSessionTime(range.end) - l.t0 + T0, w) }));
  g.save(); g.beginPath(); g.rect(TPAD, 0, w - 2 * TPAD, h); g.clip();

  /* speed profile, like a heat strip behind the chapters */
  g.font = MONO(pal);
  g.textAlign = "left";
  g.textBaseline = "alphabetic";
  (p.units.spd === "kph" ? [30, 60] : [20, 40]).forEach((d) => {
    const v = d / (p.units.spd === "kph" ? KMH_PER_MPH : 1);
    if (v < vmaxMph && h >= 70) {
      const y = topH - (v / vmaxMph) * (topH - 6);
      g.strokeStyle = pal.line;
      g.lineWidth = 1;
      g.beginPath();
      g.moveTo(TPAD, y);
      g.lineTo(w - TPAD, y);
      g.stroke();
      g.fillStyle = pal.mute;
      g.fillText(d + " " + sU(p.units), TPAD + 2, y - 2);
    }
  });
  g.beginPath();
  g.moveTo(tx(0), topH);
  traces.forEach(({ l, indices }) => indices.forEach(i => g.lineTo(tx(l.t0 - T0 + l.ch.t[i]), topH - (l.ch.spd[i] / vmaxMph) * (topH - 6))));
  g.lineTo(tx(p.TD), topH);
  g.closePath();
  g.globalAlpha = 0.5;
  g.fillStyle = pal.blue;
  g.fill();
  g.globalAlpha = 1;
  g.strokeStyle = pal.blue;
  g.lineWidth = 1.8;
  g.beginPath();
  let started = false;
  traces.forEach(({ l, indices }) =>
    indices.forEach((i) => {
      const t = l.ch.t[i];
      const x = tx(l.t0 - T0 + t);
      const y = topH - (l.ch.spd[i] / vmaxMph) * (topH - 6);
      if (started) g.lineTo(x, y);
      else g.moveTo(x, y);
      started = true;
    }),
  );
  g.stroke();

  /* chapters */
  g.textAlign = "center";
  g.textBaseline = "middle";
  visibleLaps.forEach((l) => {
    if (p.timelineTime(l.t1 - T0) < range.start || p.timelineTime(l.t0 - T0) > range.end) return;
    const x0 = tx(l.t0 - T0);
    const x1 = tx(l.t1 - T0);
    const isCur = l === cur;
    let col = pal.track;
    if (l.kind === "lap") col = Math.abs(l.dur - a.bestLap) < 1 ? pal.purple : Math.abs(l.dur - slowest) < 1 ? pal.yellow : pal.green;
    g.globalAlpha = isCur ? 1 : 0.85;
    g.fillStyle = col;
    g.fillRect(x0 + 0.5, stripY, Math.max(1, x1 - x0 - 1), stripH);
    g.globalAlpha = 1;
    if (isCur) {
      g.strokeStyle = pal.ink;
      g.lineWidth = 2;
      g.strokeRect(x0 + 1, stripY + 1, Math.max(1, x1 - x0 - 2), stripH - 2);
    }
    if (l.sec) {
      g.strokeStyle = pal.bg;
      g.globalAlpha = 0.8;
      g.lineWidth = 1;
      [l.sec[0], l.sec[0] != null && l.sec[1] != null ? l.sec[0] + l.sec[1] : null].forEach((m) => {
        if (m == null) return;
        const x = tx(l.t0 - T0 + m);
        g.beginPath();
        g.moveTo(x, stripY + stripH - 7);
        g.lineTo(x, stripY + stripH);
        g.stroke();
      });
      g.globalAlpha = 1;
    }
    const n = laps.indexOf(l) + 1;
    const label = l.kind === "out" ? "OUT" : l.kind === "partial" ? "IN PROGRESS" : l.name;
    if (x1 - x0 >= (l.kind === "out" ? 30 : 18) || (n % 5 === 0 && l.kind === "lap")) {
      g.fillStyle = isCur ? pal.bg : pal.ink;
      g.globalAlpha = isCur ? 1 : 0.85;
      g.font = "600 11px " + pal.fMono;
      const visibleStart = Math.max(TPAD, x0), visibleEnd = Math.min(w - TPAD, x1);
      g.fillText(fitLabel(g, label, visibleEnd - visibleStart - 4), (visibleStart + visibleEnd) / 2, stripY + stripH / 2 - 2);
      g.globalAlpha = 1;
    }
    /* cones counted without a time */
    const cn = l.kind === "lap" ? +p.user.cones[n] || 0 : 0;
    if (cn) {
      g.fillStyle = pal.s2;
      g.beginPath();
      g.arc(x1 - 5, stripY - 4, 3.5, 0, 7);
      g.fill();
    }
  });

  drawExtras(p, g, w, pal, stripY, stripH);

  /* time ticks */
  g.fillStyle = pal.mute;
  g.font = MONO(pal);
  g.textBaseline = "alphabetic";
  const tick = range.span <= 10000 ? 1 : range.span <= 60000 ? 5 : range.span <= 300000 ? 15 : 60;
  for (let sec = Math.ceil(range.start / 1000 / tick) * tick; sec * 1000 <= range.end; sec += tick) {
    g.textAlign = sec === 0 ? "left" : "center";
    const sourceSec = Math.floor(p.timelineSessionTime(sec * 1000) / 1000);
    g.fillText(Math.floor(sourceSec / 60) + ":" + String(sourceSec % 60).padStart(2, "0"), TPAD + (sec * 1000 - range.start) / range.span * (w - 2 * TPAD), h - 4);
  }
  /* hover and playhead */
  if (p.hoverT != null) {
    g.strokeStyle = pal.mute;
    g.globalAlpha = 0.7;
    g.lineWidth = 1;
    g.beginPath();
    g.moveTo(tx(p.hoverT), 2);
    g.lineTo(tx(p.hoverT), h - 14);
    g.stroke();
    g.globalAlpha = 1;
  }
  const px = tx(p.T);
  g.strokeStyle = pal.ink;
  g.lineWidth = 2;
  g.beginPath();
  g.moveTo(px, 0);
  g.lineTo(px, stripY + stripH + 3);
  g.stroke();
  g.fillStyle = pal.panel;
  g.lineWidth = 2.5;
  g.beginPath();
  g.arc(px, stripY + stripH + 8, 6, 0, 7);
  g.fill();
  g.stroke();
  g.restore();
}

/** Skipped laps, flags, loop, cone hits, notes, alerts. */
function drawExtras(p: Player, g: CanvasRenderingContext2D, w: number, pal: ReturnType<typeof palette>, stripY: number, stripH: number) {
  const { session: s, analysis: a } = p;
  const { TLAPS, T0, laps } = s;
  const tx = (ms: number) => timeX(p, ms, w);
  TLAPS.forEach((l) => {
    if (l.kind !== "lap") return;
    const x0 = tx(l.t0 - T0);
    const x1 = tx(l.t1 - T0);
    const f = p.user.flags[laps.indexOf(l) + 1];
    if (!a.valid(l)) {
      g.save();
      g.beginPath();
      g.rect(x0, stripY, Math.max(1, x1 - x0), stripH);
      g.clip();
      g.fillStyle = pal.bg;
      g.globalAlpha = 0.62;
      g.fillRect(x0, stripY, x1 - x0, stripH);
      g.globalAlpha = 0.7;
      g.strokeStyle = pal.mute;
      g.lineWidth = 1;
      for (let x = x0 - stripH; x < x1; x += 6) {
        g.beginPath();
        g.moveTo(x, stripY + stripH);
        g.lineTo(x + stripH, stripY);
        g.stroke();
      }
      g.restore();
      g.globalAlpha = 1;
    }
    if (f && f.f) {
      g.fillStyle = pal.yellow;
      g.beginPath();
      g.arc((x0 + x1) / 2, stripY + stripH - 4, 2.6, 0, 7);
      g.fill();
    }
  });
  if (p.loopA != null || p.loopB != null) {
    const A = p.loopA != null ? tx(p.loopA) : TPAD;
    const B = p.loopB != null ? tx(p.loopB) : w - TPAD;
    const hh = stripY + stripH + 4;
    if (p.loopA != null && p.loopB != null) {
      g.fillStyle = pal.s1;
      g.globalAlpha = 0.16;
      g.fillRect(A, 0, Math.max(1, B - A), hh);
      g.globalAlpha = 1;
    }
    g.fillStyle = pal.s1;
    g.font = "700 10px " + pal.fMono;
    g.textBaseline = "top";
    if (p.loopA != null) {
      g.fillRect(A - 1, 0, 2, hh);
      g.textAlign = "left";
      g.fillText("A", A + 3, 2);
    }
    if (p.loopB != null) {
      g.fillRect(B - 1, 0, 2, hh);
      g.textAlign = "right";
      g.fillText("B", B - 3, 2);
    }
  }
  /* cone hits, as orange dots above the lap strip */
  p.user.ce.filter(e => p.timelineAxis.includes(e.t)).forEach((e) => {
    g.fillStyle = pal.s2;
    g.strokeStyle = pal.panel;
    g.lineWidth = 2;
    g.beginPath();
    g.arc(tx(e.t), stripY - 7, 6, 0, 7);
    g.fill();
    g.stroke();
  });
  p.user.marks.filter(m => p.timelineAxis.includes(m.t)).forEach((m) => {
    const x = tx(m.t);
    const y = stripY - 9;
    g.fillStyle = pal.purple;
    g.strokeStyle = pal.panel;
    g.lineWidth = 2;
    g.beginPath();
    g.roundRect(x - 7, y - 6, 14, 10, 3);
    g.fill();
    g.stroke();
    g.beginPath();
    g.moveTo(x - 3, y + 3);
    g.lineTo(x - 3, y + 7);
    g.lineTo(x + 2, y + 3);
    g.fill();
    g.fillStyle = pal.ink;
    [-3, 0, 3].forEach(dx => {
      g.beginPath(); g.arc(x + dx, y - 1, 0.8, 0, 7); g.fill();
    });
  });
  a.alerts.filter(al => p.timelineAxis.includes(al.t)).forEach((al) => {
    const x = tx(al.t);
    const y = stripY + stripH;
    g.fillStyle = pal.red;
    g.beginPath();
    g.moveTo(x - 4, y + 14);
    g.lineTo(x + 4, y + 14);
    g.lineTo(x, y + 7);
    g.closePath();
    g.fill();
  });
}

/** Truncate rather than stretching text to fit a narrow lap chapter. */
function fitLabel(g: CanvasRenderingContext2D, text: string, width: number): string {
  if (g.measureText(text).width <= width) return text;
  let short = text;
  while (short.length && g.measureText(short + "…").width > width) short = short.slice(0, -1);
  return short ? short + "…" : "";
}
