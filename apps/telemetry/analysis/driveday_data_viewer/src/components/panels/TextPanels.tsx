"use client";

import { ALERT_DEFS } from "@/lib/analysis";
import { buildDebrief } from "@/lib/debrief";
import { clock } from "@/lib/format";
import { fmtLimit } from "../format";
import { usePlayer, useUi } from "../context";
import { Field } from "./Tile";

export function AlertsPanel() {
  const p = usePlayer();
  useUi(p);
  const a = p.analysis;
  const any = ALERT_DEFS.some((d) => a.limit(d) != null);
  return (
    <>
      <p className="pdesc">Moments where a temperature or voltage crossed a limit you set in Setup. Click a row to jump there.</p>
      <div className="scroll">
        {!any ? (
          <div className="empty">No limits set. Add them under Setup, in the Alert limits group, and every time one is crossed it is marked on the timeline and listed here.</div>
        ) : !a.alerts.length ? (
          <div className="empty">No limit was crossed during this run.</div>
        ) : (
          <table className="tb2">
            <thead><tr><th>When</th><th>Lap</th><th>Channel</th><th>Peak</th><th>Limit</th></tr></thead>
            <tbody>
              {a.alerts.map((x, i) => (
                <tr
                  key={i}
                  tabIndex={0}
                  onClick={() => p.setT(Math.max(0, x.t - 800))}
                  onKeyDown={(e) => e.key === "Enter" && p.setT(Math.max(0, x.t - 800))}
                >
                  <td>{clock(x.t)}</td>
                  <td>{x.lap.name.replace(" (in progress)", "")}</td>
                  <td>{x.d.label}</td>
                  <td>{fmtLimit(p.units, x.d, x.val)}</td>
                  <td>{fmtLimit(p.units, x.d, x.lim)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

export function NotesPanel() {
  const p = usePlayer();
  useUi(p);
  const { marks } = p.user;
  return (
    <>
      <p className="pdesc">Comments pinned to moments on the timeline. They show as purple markers above the laps.</p>
      {!marks.length ? (
        <div className="empty">No notes yet. Press N anywhere, or use + Note under the timeline, to pin a comment to the moment you are watching.</div>
      ) : (
        <ul className="notes">
          {marks.map((m, i) => (
            <li key={m.id} tabIndex={0} onClick={() => p.setT(m.t)} onKeyDown={(e) => e.key === "Enter" && p.setT(m.t)}>
              <span className="nt">{clock(m.t)} · {p.lapLabelAt(m.t)}</span>
              <span className="ntx">{m.text}</span>
              <button className="x" type="button" aria-label="Delete note" onClick={(e) => { e.stopPropagation(); p.deleteMark(i); }}>×</button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export function DebriefPanel() {
  const p = usePlayer();
  useUi(p);
  const items = buildDebrief(p.analysis, p.units);
  return (
    <>
      <p className="pdesc">What stands out in this run, from the laps you have not skipped. Items with a corner or braking zone zoom the map there when clicked.</p>
      <ul className="deb">
        {items.map((it, i) => {
          const go = it.seg != null || it.zone != null;
          return (
            <li
              key={i}
              className={it.k + (go ? " go" : "")}
              onClick={() => {
                if (it.seg != null) p.focusSeg(it.seg);
                else if (it.zone != null) p.focusZone(it.zone);
              }}
            >
              <i />
              <span>{it.t}</span>
              {it.skip && (
                <button
                  className="btn"
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    p.skipLaps(it.skip!);
                    p.toast("Skipped " + it.skip!.map((n) => "Lap " + n).join(", "));
                  }}
                >
                  Skip {it.skip.length > 1 ? "them" : "it"}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </>
  );
}

type Spec = [key: string, label: string, kind?: "number" | "text" | "area" | "select", options?: string[]];
const FORM: [string, Spec[]][] = [
  ["Conditions", [["driver", "Driver", "text"], ["airT", "Air temp (°C)"], ["surf", "Surface", "select", ["", "Dry", "Damp", "Wet"]], ["surfT", "Surface temp (°C)"], ["wind", "Wind and other", "text"]]],
  ["Tire pressure, cold", [["coldFL", "Front left (psi)"], ["coldFR", "Front right (psi)"], ["coldRL", "Rear left (psi)"], ["coldRR", "Rear right (psi)"], ["compound", "Compound", "text"]]],
  ["Tire pressure, hot after the run", [["hotFL", "Front left (psi)"], ["hotFR", "Front right (psi)"], ["hotRL", "Rear left (psi)"], ["hotRR", "Rear right (psi)"]]],
  ["Alignment and chassis", [["camF", "Front camber (°)"], ["camR", "Rear camber (°)"], ["toeF", "Front toe (°)"], ["toeR", "Rear toe (°)"], ["bias", "Front brake bias (%)"], ["tqLimit", "Torque limit (Nm)"], ["aero", "Aero", "text"], ["susp", "Springs and dampers", "text"]]],
  ["Alert limits (leave blank for none)", [["aCellT", "Hottest cell above (°C)"], ["aInvT", "Inverter hotspot above (°C)"], ["aMotT", "Motor above (°C)"], ["aCoolT", "Coolant above (°C)"], ["aCellV", "Lowest cell below (V)"], ["aPackV", "Pack voltage below (V)"]]],
  ["Notes", [["notes", "Driver feedback and run notes", "area"], ["next", "Changes for the next run", "area"]]],
];

export function SetupPanel() {
  const p = usePlayer();
  useUi(p);
  return (
    <>
      <div className="penrow" style={{ paddingBottom: 0 }}><span className="mono">Saved in this browser only</span></div>
      <div className="form">
        {FORM.map(([title, fields]) => (
          <div className="fg" key={title}>
            <h4>{title}</h4>
            <div className="fgrid">
              {fields.map(([k, label, kind, options]) => (
                <Field
                  key={k}
                  k={k}
                  label={label}
                  kind={kind}
                  options={options}
                  wide={kind === "area"}
                  placeholder={k === "driver" ? p.session.D.meta.driver : undefined}
                  value={p.user.setup[k] as string | number | undefined}
                  onValue={(v) => p.setSetup(k, v)}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

export function AboutPanel() {
  return (
    <div className="note2" style={{ paddingTop: 12 }}>
      <b>Timeline.</b> Everything follows the playhead along the whole run, out lap first. Click or drag the timeline, press Play, or click the track or any chart to jump. Laps are the chapters, with sector marks inside them and an orange marker for every cone hit.
      <br /><br />
      <b>Sectors.</b> By default the shared track layout is split into three equal-distance sectors. The Sectors menu can save custom gates or suggest corner-based boundaries, and every other lap is projected onto it so each is timed through the same two gates. Crossings are interpolated between GPS fixes. Missing crossings show as unavailable, never equal-time estimates. When all crossings are available, the sectors add up to the lap time: the first one starts at the logged lap start and the last one ends at the logged lap end.
      <br /><br />
      <b>Colours.</b> Purple is the session best, green is faster than the base lap, yellow is slower. Adjusted time adds the cone penalty to real time.
      <br /><br />
      <b>Ghost.</b> Best is the fastest unskipped logged lap. Replay starts both laps at logged time zero and stops each at its logged finish; a faster lap can still be behind in an individual section. Best sectors stitches the fastest measured sectors using the same boundaries.
      <br /><br />
      <b>Samples and smoothing.</b> Imports combine the session tail and full lap samples at their original timestamps, including 20 Hz exports. Playback positions and derived speed/acceleration use a centred 0.5 s average; imported fixes, telemetry channels and logged lap times are retained. Lateral g is a GPS curvature estimate, not an IMU reading. GPS noise and gaps affect its accuracy.
      <br /><br />
      <b>Start and finish lines.</b> They are set for you: the start line is where the car was when the timed laps began, and the finish line is where it was when they ended. Lap times are always the ones the logger recorded.
      <br /><br />
      <b>Courses.</b> If the runs in one file start or finish in different places, or some go the other way, each group opens as its own run, because laps on different lines cannot be compared. Short, slow drives back to the line are left out.
      <br /><br />
      <b>Skipping laps.</b> A skipped lap stays on the timeline but is left out of the bests, the theoretical best, the debrief and the consistency view.
      <br /><br />
      <b>Keys.</b> C adds a cone, N pins a note, I and O set a loop, X clears it, R toggles the race replay, B and [ ] jump between brake zones and corners.
    </div>
  );
}
