"use client";

import { useMemo, useState } from "react";
import type { LiveSample, RunData } from "@/lib/types";

const CHANNELS: [string, string, string][] = [
  ["accel_pedal_travel", "Throttle", "fraction"], ["brake_pressure_f", "Front brake pressure", "raw"],
  ["brake_pressure_rbll", "Rear brake pressure", "raw"], ["steer_col_angle", "Steering", "raw"],
  ["motor_speed", "Motor speed", "RPM"], ["torque_feedback", "Torque feedback", "raw"],
  ["dc_bus_v", "DC bus voltage", "V"], ["dc_bus_current", "DC bus current", "A"],
  ["max_cell_temp", "Maximum cell temperature", "°C"], ["motor_temp", "Motor temperature", "°C"],
];

/** No Player, GPS coordinates or distance axis: values remain tied to logger time. */
export function TelemetryOnlyPanel({ data, panel }: { data: RunData; panel: string }) {
  const telemetry = data.telemetryOnly!;
  const [selected, setSelected] = useState(0);
  const [channel, setChannel] = useState(CHANNELS[0][0]);
  const channels = useMemo(() => {
    const known = new Set(CHANNELS.map(c => c[0]));
    const extra = new Set<string>();
    let cellCount = 0;
    for (const sample of telemetry.samples) {
      Object.entries(sample.values ?? {}).forEach(([key, v]) => { if (Number.isFinite(v) && !known.has(key)) extra.add(key); });
      cellCount = Math.max(cellCount, sample.cellTemps?.length ?? 0);
    }
    return [...CHANNELS, ...[...extra].sort().map(key => [key, key, "raw"]),
      ...Array.from({ length: cellCount }, (_, i) => [`cell:${i}`, `Cell ${i + 1} temperature`, "°C"])];
  }, [telemetry.samples]);
  const interval = telemetry.intervals[Math.min(selected, telemetry.intervals.length - 1)];
  const samples = telemetry.samples.filter(s => s.t >= interval.start && s.t <= interval.end);
  const definition = channels.find(c => c[0] === channel)!;
  const value = (s: LiveSample) => channel.startsWith("cell:") ? s.cellTemps?.[Number(channel.slice(5))] : s.values?.[channel];
  const valid = samples.filter(s => Number.isFinite(value(s)));
  const values = valid.map(s => value(s)!);
  const min = values.length ? Math.min(...values) : 0, max = values.length ? Math.max(...values) : 1;
  const span = max - min || 1, duration = interval.end - interval.start;
  // Start a new stroke across logging outages instead of implying measurements between them.
  const paths: string[] = [];
  let previous: number | null = null;
  for (const s of samples) {
    const v = value(s);
    if (!Number.isFinite(v)) { previous = null; continue; }
    const xy = `${40 + 580 * (s.t - interval.start) / duration},${180 - 150 * (v! - min) / span}`;
    if (previous === null || s.t - previous > 2000) paths.push("M" + xy); else paths[paths.length - 1] += " L" + xy;
    previous = s.t;
  }
  if (!["laps", "trace", "car", "cells", "kw", "pv", "temp", "celltime", "pedal"].includes(panel)) return <div className="empty">This panel needs a GPS-supported run. Sectors, corners, GPS speed and distance comparisons are unavailable. Select Laps or Telemetry to review recorded channels against time.</div>;
  return <div className="telemetry-only">
    <p className="gps-warning">GPS unavailable. Channels are shown against time. Logged intervals are not validated driving laps.</p>
    {panel === "laps" ? <table><thead><tr><th>Logged interval</th><th>Time</th><th>GPS issue</th></tr></thead><tbody>{telemetry.intervals.map((row, i) => <tr key={i}><td>{row.label}</td><td>{((row.end - row.start) / 1000).toFixed(3)} s</td><td>{row.reason}</td></tr>)}</tbody></table> : <>
      <label>Logged interval<select aria-label="Logged interval" value={selected} onChange={e => setSelected(Number(e.target.value))}>{telemetry.intervals.map((row, i) => <option key={i} value={i}>{row.label} · {((row.end - row.start) / 1000).toFixed(3)} s</option>)}</select></label>
      <p>{interval.reason}</p>
      <label>Recorded channel<select aria-label="Recorded channel" value={channel} onChange={e => setChannel(e.target.value)}>{channels.map(([key, label, unit]) => <option key={key} value={key}>{label} ({unit})</option>)}</select></label>
      {values.length ? <><svg viewBox="0 0 640 220" role="img" aria-label={`${definition[1]} against elapsed seconds; GPS unavailable`}>
        <path d="M40,15 V180 H620" fill="none" stroke="var(--mute)" />
        {paths.map((path, i) => <path key={i} d={path} fill="none" stroke="var(--s1)" strokeWidth="2" />)}
        <text x="4" y="25" fill="var(--ink)" fontSize="11">{max.toFixed(1)}</text><text x="4" y="180" fill="var(--ink)" fontSize="11">{min.toFixed(1)}</text>
        <text x="40" y="205" fill="var(--ink)" fontSize="12">0 s</text><text x="540" y="205" fill="var(--ink)" fontSize="12">{(duration / 1000).toFixed(1)} s</text>
      </svg><p>{values.length} recorded values · gaps over 2 seconds are left disconnected.</p></> : <p>No recorded values for this channel in this interval.</p>}
    </>}
  </div>;
}
