"use client";

import { useState } from "react";

import { SETUP_FORM as FORM } from "@/lib/driveday/setup";
import { ALERT_DEFS } from "@/lib/driveday/analysis";
import { buildDebrief } from "@/lib/driveday/debrief";
import { clock } from "@/lib/driveday/format";
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
  const [text, setText] = useState("");
  const [category, setCategory] = useState<"note" | "braking" | "cone" | "offcourse" | "video">("note");
  const [filter, setFilter] = useState("all");
  const categories = [["note", "Note"], ["braking", "Braking"], ["cone", "Cone hit"], ["offcourse", "Off course"], ["video", "Video moment"]] as const;
  return (
    <>
      <p className="pdesc">Bookmarks connect the timeline and loaded video. Clicking one jumps to its saved moment; reselect the matching video if it was closed.</p>
      <form className="review-panel" onSubmit={e => { e.preventDefault(); p.addMark(text.trim() || categories.find(([key]) => key === category)![1], category); setText(""); }}><input aria-label="Bookmark label" placeholder="e.g. Late braking into T1" value={text} onChange={e => setText(e.target.value)} /><select aria-label="Bookmark category" value={category} onChange={e => setCategory(e.target.value as typeof category)}>{categories.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select><button className="btn" type="submit">Bookmark this moment</button></form>
      <label className="review-filter">Show<select aria-label="Filter bookmarks" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">All bookmarks</option>{categories.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      {!marks.length ? (
        <div className="empty">No notes yet. Press N anywhere, or use + Note under the timeline, to pin a comment to the moment you are watching.</div>
      ) : (
        <ul className="notes">
          {marks.map((m, i) => (filter === "all" || (m.category ?? "note") === filter) && (
            <li key={m.id} tabIndex={0} onClick={() => p.seekMark(m.id)} onKeyDown={(e) => e.key === "Enter" && p.seekMark(m.id)}>
              <span className="nt">{clock(m.t)} · {p.lapLabelAt(m.t)}</span>
              <span className="ntx">{m.text}<small className="bookmark-meta">{m.category ?? "note"}{m.video ? " · " + m.video.name + " @ " + m.video.time.toFixed(2) + " s" : ""}</small></span>
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
      <b>Sectors.</b> Automatic boundaries aim for balanced driving time and snap to nearby corner/straight transitions. Use the Sectors menu to set S1 and S2 at the playhead or reset the suggestion. Every lap uses the same saved gates; crossings are interpolated between GPS fixes. Measured sectors add up to the logged lap time, and missing crossings show a dash.
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
