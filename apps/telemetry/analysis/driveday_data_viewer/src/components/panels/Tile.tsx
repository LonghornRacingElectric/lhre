"use client";

import { useState, type ReactNode } from "react";

/** A labelled reading, optionally with a bar under it (meter is 0 to 100 and a colour variable). */
export function Tile({ label, value, unit, meter }: { label: string; value: ReactNode; unit?: string; meter?: [number, string] }) {
  return (
    <div className="tile">
      <span>{label}</span>
      <b>{value}{unit ? <small>{unit}</small> : null}</b>
      {meter && (
        <div className="meter">
          <i style={{ width: Math.max(0, Math.min(100, meter[0])).toFixed(0) + "%", background: `var(${meter[1]})` }} />
        </div>
      )}
    </div>
  );
}

type Kind = "number" | "text" | "area" | "select";

/** A setup box. It keeps what is typed as text (so "1." survives) and reports the parsed value on every keystroke. */
export function Field({ label, k, value, onValue, kind = "number", placeholder, wide, options }: {
  label: string;
  k: string;
  value: string | number | undefined;
  onValue: (v: string | number | null) => void;
  kind?: Kind;
  placeholder?: string;
  wide?: boolean;
  options?: string[];
}) {
  const [text, setText] = useState(value == null ? "" : String(value));
  const change = (v: string) => {
    setText(v);
    if (v === "") onValue(null);
    else if (kind === "number") onValue(isNaN(parseFloat(v)) ? null : parseFloat(v));
    else onValue(v);
  };
  let control: ReactNode;
  if (kind === "area") control = <textarea rows={3} data-k={k} value={text} onChange={(e) => change(e.target.value)} />;
  else if (kind === "select") {
    control = (
      <select data-k={k} value={text} onChange={(e) => change(e.target.value)}>
        {(options ?? []).map((o) => <option key={o} value={o}>{o || "Not set"}</option>)}
      </select>
    );
  } else {
    control = (
      <input
        data-k={k}
        type={kind}
        step={kind === "number" ? "any" : undefined}
        inputMode={kind === "number" ? "decimal" : undefined}
        placeholder={placeholder}
        autoComplete="off"
        value={text}
        onChange={(e) => change(e.target.value)}
      />
    );
  }
  return (
    <label className={"fld" + (wide ? " wide" : "")}>
      <span>{label}</span>
      {control}
    </label>
  );
}
