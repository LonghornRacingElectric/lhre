import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { convertSession } from "../src/lib/convert";
import { convertDriverSession } from "../src/lib/layout-import";
import { gpsProblem, recordingSamples } from "../src/lib/gps-quality";
import { TelemetryOnlyPanel } from "../src/components/TelemetryOnlyPanel";
import { Workspace } from "../src/components/Workspace";
import type { RunData } from "../src/lib/types";
import { synthSession } from "./fixtures/synth";

const source = () => synthSession({ laps: 3, sessionId: "gps-quality" });

describe("unreliable GPS recordings", () => {
  it("keeps frozen GPS out of course detection while retaining every telemetry sample", () => {
    const json = source();
    json.sampleTail = json.sampleTail.map(s => ({ ...s, lat: 30.3, lon: -97.7 }));
    const before = JSON.stringify(json);
    expect(() => convertSession(json, "frozen.json")).toThrow(/GPS/);
    const entries = convertDriverSession(json, "frozen.json", []);
    expect(entries).toHaveLength(1);
    const data = entries[0].D;
    expect(data.telemetryOnly?.samples).toEqual(recordingSamples(json));
    expect(data.telemetryOnly?.intervals).toHaveLength(3);
    expect(data.laps).toEqual([]);
    expect(data.ref).toEqual([]);
    expect(data.edges).toBeUndefined();
    expect(data.telemetryOnly?.intervals.every(r => /Frozen/.test(r.reason))).toBe(true);
    expect(JSON.stringify(json)).toBe(before);
  });
  it("separates a missing-GPS interval without drawing its return as an unfinished lap", () => {
    const json = source(), last = json.laps[2];
    json.sampleTail = json.sampleTail.map(s => s.t >= last.startMs && s.t <= last.endMs ? { ...s, lat: 0, lon: 0 } : s);
    const entries = convertDriverSession(json, "missing.json", []);
    const valid = entries.find(e => !e.D.telemetryOnly)!.D;
    const telemetry = entries.find(e => e.D.telemetryOnly)!.D;
    expect(valid.laps.filter(l => l.kind === "lap")).toHaveLength(2);
    expect(valid.laps.some(l => l.kind === "partial")).toBe(false);
    expect(telemetry.telemetryOnly!.intervals[0].start).toBe(last.startMs);
    expect(telemetry.telemetryOnly!.samples).toEqual(recordingSamples(json));
    expect(valid.gpsIssues).toHaveLength(1);
  });
  it("does not bridge long GPS outages even when telemetry continues", () => {
    const json = source(), lap = json.laps[1];
    json.sampleTail = json.sampleTail.map(s => s.t > lap.startMs + 3000 && s.t < lap.startMs + 8000 ? { ...s, lat: 0, lon: 0 } : s);
    const converted = convertSession(json, "outage.json");
    expect(converted.flatMap(e => e.D.laps).some(l => l.name === lap.label)).toBe(false);
    const retained = convertDriverSession(json, "outage.json", []).find(e => e.D.telemetryOnly)!.D.telemetryOnly!;
    expect(retained.intervals[0].reason).toMatch(/outage/);
  });
  it("rejects GPS jumps and a frozen coordinate during drivetrain activity", () => {
    const json = source(), lap = json.laps[0];
    const samples = recordingSamples(json).filter(s => s.t >= lap.startMs && s.t <= lap.endMs);
    const jumped = samples.map((s, i) => i === 25 ? { ...s, lat: s.lat + 0.02 } : s);
    expect(gpsProblem(jumped, lap.startMs, lap.endMs)).toMatch(/jump/);
    const frozen = samples.map((s, i) => i > 20 ? { ...s, lat: samples[20].lat, lon: samples[20].lon,
      values: { ...s.values, accel_pedal_travel: 1, motor_speed: 2000 } } : s);
    expect(gpsProblem(frozen, lap.startMs, lap.endMs)).toMatch(/Frozen GPS/);
  });
  it("shows time charts and interval durations without creating GPS, sectors or a Player", () => {
    const json = source();
    json.sampleTail = json.sampleTail.map(s => ({ ...s, lat: 0, lon: 0 }));
    const data = convertDriverSession(json, "no-gps.json", [])[0].D;
    const html = renderToStaticMarkup(createElement(TelemetryOnlyPanel, { data, panel: "trace" }));
    expect(html).toContain("elapsed seconds");
    expect(html).toContain("recorded values");
    const workspace = renderToStaticMarkup(createElement<{ telemetry?: RunData }>(Workspace, { telemetry: data }));
    expect(workspace).toContain("GPS route unavailable");
    expect(workspace).not.toContain('id="cv"');
  });
});
