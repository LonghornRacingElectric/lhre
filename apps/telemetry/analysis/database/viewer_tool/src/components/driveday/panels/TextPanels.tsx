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
      <div className="scroll">
        {!any ? (
          <div className="empty">No limits set.</div>
        ) : !a.alerts.length ? (
          <div className="empty">No alerts.</div>
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
      <form className="review-panel" onSubmit={e => { e.preventDefault(); p.addMark(text.trim() || categories.find(([key]) => key === category)![1], category); setText(""); }}><input aria-label="Bookmark label" placeholder="e.g. Late braking into T1" value={text} onChange={e => setText(e.target.value)} /><select aria-label="Bookmark category" value={category} onChange={e => setCategory(e.target.value as typeof category)}>{categories.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select><button className="btn" type="submit">Bookmark this moment</button></form>
      <label className="review-filter">Show<select aria-label="Filter bookmarks" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">All bookmarks</option>{categories.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      {!marks.length ? (
        <div className="empty">No bookmarks.</div>
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
