'use client';

import { useEffect, useState } from "react";
import { Flag, X } from "lucide-react";
import { EVENT_TYPES, suggestSessionName } from "@/lib/trackside/sessionRegistry";
import { availableTireSetId, loadTireSetPreference, type TireSetCar, type TireSetOption } from "@/lib/trackside/tireSetPreferences";
import type { SourceDef } from "@/lib/trackside/types";

export function NewSessionModal({
  sources,
  defaultCar,
  defaultVenue,
  defaultDriver,
  tireSets = [],
  onStart,
  onClose,
}: {
  sources: SourceDef[];
  defaultCar: "orion" | "angelique";
  defaultVenue: string;
  defaultDriver: string;
  tireSets?: readonly TireSetOption[];
  onStart: (draft: { name: string; car: "orion" | "angelique"; driver: string; eventType: string; venue: string; tireSetId: string }) => void;
  onClose: () => void;
}) {
  const [car, setCar] = useState<"orion" | "angelique">(defaultCar);
  const [tireSetDrafts, setTireSetDrafts] = useState<Record<TireSetCar, string>>(() => ({
    orion: loadTireSetPreference("orion"),
    angelique: loadTireSetPreference("angelique"),
  }));
  const [driver, setDriver] = useState(defaultDriver);
  const [eventType, setEventType] = useState<string>(EVENT_TYPES[0]);
  const [venue, setVenue] = useState(defaultVenue);
  const [name, setName] = useState("");
  const suggestedName = suggestSessionName(venue, eventType);
  const tireSetId = availableTireSetId(tireSetDrafts[car], tireSets);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modalOverlay" onMouseDown={onClose}>
      <div className="modalCard" role="dialog" aria-modal="true" aria-labelledby="new-session-title" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modalHead">
          <h3 id="new-session-title">New session</h3>
          <button className="tool iconOnly" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>
        <div className="trackForm">
          <label>
            <span>Session name</span>
            <input value={name} placeholder={suggestedName} autoFocus onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            <span>Car</span>
            <select value={car} onChange={(e) => setCar(e.target.value as "orion" | "angelique")}>
              {sources.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
          </label>
          <label className="tireSetField">
            <span>Tire set <span className="muted">(optional)</span></span>
            <select
              value={tireSetId}
              onChange={(e) => setTireSetDrafts((prev) => ({ ...prev, [car]: e.target.value }))}
              aria-describedby="new-session-tire-set-help"
            >
              <option value="">Unassigned</option>
              {tireSets.map((set) => <option key={set.id} value={set.id}>{set.label}</option>)}
            </select>
          </label>
          <small id="new-session-tire-set-help" className="muted">
            {tireSets.length === 0
              ? "No tire sets available yet."
              : "Starting a session remembers this car’s choice on this device."}
          </small>
          <label>
            <span>Event type</span>
            <select value={eventType} onChange={(e) => setEventType(e.target.value)}>
              {EVENT_TYPES.map((ev) => <option key={ev} value={ev}>{ev}</option>)}
            </select>
          </label>
          <label>
            <span>Driver</span>
            <input value={driver} placeholder="Name" onChange={(e) => setDriver(e.target.value)} />
          </label>
          <label>
            <span>Venue / track</span>
            <input value={venue} onChange={(e) => setVenue(e.target.value)} />
          </label>
          <small className="muted">
            Starts now and clears the current run. The name, driver, event and venue tag this
            session so its log CSV auto-fills in Log Sync.
          </small>
        </div>
        <div className="modalFoot">
          <button type="button" className="tool" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="primary"
            onClick={() => onStart({ name: name.trim() || suggestedName, car, driver, eventType, venue, tireSetId })}
          >
            <Flag size={15} /> Start session
          </button>
        </div>
      </div>
    </div>
  );
}

