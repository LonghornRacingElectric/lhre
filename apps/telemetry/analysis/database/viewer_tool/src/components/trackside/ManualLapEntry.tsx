'use client';
import { useState } from "react";
import { Plus } from "lucide-react";
import { parseLapTimeInput } from "@/lib/trackside/lapTime";

// Type a lap time the car's timing did not capture (official timing, a
// stopwatch, a lap logged before the page was open). Same parser as the
// click-to-edit lap-time cell, so "1:23.45" and "83.45" both work.
export function ManualLapEntry({ disabled, onAdd }: { disabled?: boolean; onAdd: (durationMs: number) => void }) {
  const [text, setText] = useState("");
  const ms = parseLapTimeInput(text);
  const ok = ms != null && ms > 0;
  return (
    <form
      style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 8 }}
      onSubmit={(e) => { e.preventDefault(); if (ok) { onAdd(ms); setText(""); } }}
    >
      <input
        value={text}
        disabled={disabled}
        placeholder="m:ss.ss"
        aria-label="Manual lap time"
        inputMode="decimal"
        style={{ width: 96 }}
        onChange={(e) => setText(e.target.value)}
      />
      <button type="submit" className="tool" disabled={disabled || !ok} title="Add a lap by its time (official timing or a stopwatch). No samples or energy; counts toward laps and the average.">
        <Plus size={14} /> Add time
      </button>
    </form>
  );
}
