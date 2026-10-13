"use client";

import { usePlayer, useUi } from "./context";

/** Driver ghosts stay available beside the map, even while another panel is open. */
export function DriverComparison() {
  const p = usePlayer();
  useUi(p);
  const rivals = p.rivals ?? [];
  return (
    <label className="driver-comparison">
      <span>Compare driver</span>
      <select aria-label="Compare driver on the same layout and direction" value={p.externalGhost ? p.externalGhostKey : ""}
        onChange={e => p.setExternalGhost(e.target.value)} disabled={!rivals.length}>
        <option value="">{rivals.length ? "Choose a driver’s lap…" : "No other runs in this direction"}</option>
        {rivals.map(r => <option key={r.key} value={r.key}>{r.label}</option>)}
      </select>
      {p.externalGhost && <button className="btn" type="button" onClick={() => p.setExternalGhost("")}>Clear</button>}
    </label>
  );
}
