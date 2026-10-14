"use client";

import { useEffect, useRef, useState } from "react";
import { clock, fmt } from "@/lib/format";
import { RATES, type Cmp, type EndMode } from "@/lib/player";
import { Menu } from "./Menu";
import { Timeline } from "./Timeline";
import { useFrame, usePlayer, useUi } from "./context";

const GHOSTS: [Cmp, string, string][] = [
  ["theo", "Best sectors", "A lap stitched together from your fastest S1, S2 and S3"],
  ["best", "Best", "Your fastest lap"],
  ["lap", "Lap…", "Any lap from this run"],
];

function Clock() {
  const p = usePlayer();
  useFrame(p);
  return (
    <span className="time" id="clock">
      {clock(p.T)} <small>/ {clock(p.TD).replace(/\.\d$/, "")}</small>
    </span>
  );
}

function NoteBar() {
  const p = usePlayer();
  useUi(p);
  const [text, setText] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (p.noteOpen) {
      setText("");
      input.current?.focus();
    }
  }, [p.noteOpen, p]);
  if (!p.noteOpen) return null;
  const close = () => p.setNoteOpen(false);
  const save = () => {
    const v = text.trim();
    if (v) p.addMark(v);
    close();
  };
  return (
    <div className="notebar" id="notebar">
      <span className="mono">at {clock(p.T)} · {p.lapLabelAt(p.T)}</span>
      <input
        ref={input}
        type="text"
        maxLength={200}
        placeholder="What happened here?"
        aria-label="Note text"
        autoComplete="off"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            save();
          } else if (e.key === "Escape") {
            e.preventDefault();
            close();
          }
        }}
      />
      <button className="btn primary" type="button" onClick={save}>Save</button>
      <button className="btn" type="button" onClick={close}>Cancel</button>
    </div>
  );
}

/** Play controls, ghost choice, loops, and the timeline underneath. */
export function Transport({ height = 108, onHeight, docked = false, onPlacement }: { height?: number; onHeight?: (height: number) => void; docked?: boolean; onPlacement?: () => void } = {}) {
  const p = usePlayer();
  useUi(p);
  const [keys, setKeys] = useState(false);
  const resize = useRef<{ y: number; height: number } | null>(null);
  const { laps, TLAPS } = p.session;
  const pickGhost = (c: Cmp) => {
    if (p.cmp === c) p.set("cmp", "none");
    else {
      p.set("cmp", c);
      if (c === "lap") p.toast("Ghost: " + laps[p.ghostLapI].name + (p.ghostLapI === p.sel ? ". It is the lap you are watching, so pick another to compare." : ""));
    }
  };
  return (
    <div className="transport panel" style={{ ["--timeline-height" as string]: height + "px" }}>
      {onHeight && <div className="timeline-resize" role="separator" aria-orientation="horizontal" aria-label="Resize timeline height" aria-valuenow={height} aria-valuemin={48} aria-valuemax={240} tabIndex={0} title="Drag up or down to resize the timeline. Double-click for a compact strip."
        onPointerDown={e => { resize.current = { y: e.clientY, height }; e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId); e.preventDefault(); }}
        onPointerMove={e => { if (resize.current) onHeight(Math.max(48, Math.min(240, resize.current.height + resize.current.y - e.clientY))); }}
        onPointerUp={() => { resize.current = null; }} onPointerCancel={() => { resize.current = null; }} onDoubleClick={() => onHeight(48)}
        onKeyDown={e => { if (e.key === "ArrowUp" || e.key === "ArrowDown") { onHeight(Math.max(48, Math.min(240, height + (e.key === "ArrowUp" ? 12 : -12)))); e.preventDefault(); e.stopPropagation(); } }}><span /></div>}
      <div className="trow">
        <button className="btn" type="button" title="Previous lap (Shift+Left)" aria-label="Previous lap" onClick={() => p.stepLap(-1)}>⏮</button>
        <button className="btn play" type="button" title="Play or pause (Space)" onClick={() => p.play(!p.playing)}>{p.playing ? "Pause" : "Play"}</button>
        <button className="btn" type="button" title="Next lap (Shift+Right)" aria-label="Next lap" onClick={() => p.stepLap(1)}>⏭</button>
        <Clock />
        <span className="sp" />
        <Menu label={"Ghost: " + (p.cmp === "best" ? "Best" : p.cmp === "theo" ? "Best sectors" : p.cmp === "lap" ? "Lap" : p.cmp === "external" ? "Other file" : "Off")} right>
        <div className="seg" role="group" aria-label="Ghost lap. Click the active one again to turn it off.">
          {GHOSTS.map(([c, label, title]) => (
            <button key={c} aria-pressed={p.cmp === c} title={title} onClick={() => pickGhost(c)}>{label}</button>
          ))}
        </div>
        {p.cmp === "lap" && (
          <span className="rivalpick">
            <select aria-label="Lap to use as the ghost" value={p.ghostLapI} onChange={(e) => p.set("ghostLapI", +e.target.value)}>
              {TLAPS.filter((l) => l.kind !== "partial").map((l) => {
                const i = laps.indexOf(l);
                return <option key={i} value={i}>{l.kind === "out" ? "Out lap" : l.name} · {fmt(l.dur)}</option>;
              })}
            </select>
          </span>
        )}
        {p.cmp === "external" && <button className="btn" title="Turn off the other-file ghost" onClick={() => p.set("cmp", "none")}>Other file ×</button>}
        </Menu>
        <button className="btn" type="button" title="Pin a note to the playhead (N)" onClick={() => p.setNoteOpen(true)}>+ Note</button>
        <button className="btn" type="button" title="Add a cone to this lap (C). Shift+C removes one." onClick={() => p.coneKey(1)}>+ Cone</button>
        <Menu label={p.rate + "×"} right>
          <div className="mrow">
            <span className="lab">Speed</span>
            <select className="mini" aria-label="Playback speed" value={p.rate} onChange={(e) => p.set("rate", +e.target.value)}>
              {RATES.map((r) => <option key={r} value={r}>{r}×</option>)}
            </select>
          </div>
          <div className="mrow">
            <span className="lab">At lap end</span>
            <select className="mini" aria-label="At the end of a lap" value={p.endMode} onChange={(e) => p.set("endMode", e.target.value as EndMode)}>
              <option value="go">Keep playing</option>
              <option value="stop">Stop</option>
              <option value="loop">Loop the lap</option>
            </select>
          </div>
        </Menu>
        <Menu label={p.loopA != null || p.loopB != null ? "More •" : "More"} right>
          {onPlacement && <button className="btn" aria-label={docked ? "Expand timeline across workspace" : "Dock timeline into track"} onClick={onPlacement}>{docked ? "Undock timeline" : "Dock timeline into track"}</button>}
          <div className="mrow">
            <span className="lab">Loop</span>
            <div className="seg" role="group" aria-label="A to B loop">
              <button type="button" aria-pressed={p.loopA != null} title="Loop start at the playhead (I)" onClick={() => p.setLoopA()}>A</button>
              <button type="button" aria-pressed={p.loopB != null} title="Loop end at the playhead (O)" onClick={() => p.setLoopB()}>B</button>
              <button type="button" title="Loop the current corner or straight (;)" onClick={() => p.loopSegment()}>Corner</button>
              <button type="button" title="Clear the loop (X)" onClick={() => p.clearLoop()}>Clear</button>
            </div>
          </div>
          <div className="mrow">
            <button className="btn" type="button" aria-pressed={keys} title="Show the keyboard shortcuts" onClick={() => setKeys(!keys)}>Keyboard shortcuts</button>
          </div>
        </Menu>
      </div>
      <Timeline />
      <NoteBar />
      <div className={"hint" + (keys ? " on" : "")}>
        <kbd>Space</kbd> play · <kbd>←</kbd><kbd>→</kbd> 2 s · <kbd>Shift</kbd>+<kbd>←</kbd><kbd>→</kbd> lap · <kbd>,</kbd><kbd>.</kbd> one GPS fix · <kbd>L</kbd> lap end · <kbd>B</kbd> brake zone · <kbd>[</kbd><kbd>]</kbd> corner · <kbd>F</kbd> follow · <kbd>D</kbd> panels · <kbd>C</kbd> cone · <kbd>N</kbd> note · <kbd>I</kbd><kbd>O</kbd><kbd>X</kbd> loop · <kbd>R</kbd> race · <kbd>&lt;</kbd><kbd>&gt;</kbd> speed
      </div>
    </div>
  );
}
