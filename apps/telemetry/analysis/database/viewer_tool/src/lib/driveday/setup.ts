export type SetupSpec = [key: string, label: string, kind?: "number" | "text" | "area" | "select", options?: string[]];
export const SETUP_FORM: [string, SetupSpec[]][] = [
  ["Conditions", [["driver", "Driver", "text"], ["airT", "Air temp (°C)"], ["surf", "Surface", "select", ["", "Dry", "Damp", "Wet"]], ["surfT", "Surface temp (°C)"], ["wind", "Wind and other", "text"]]],
  ["Tire pressure, cold", [["coldFL", "Front left (psi)"], ["coldFR", "Front right (psi)"], ["coldRL", "Rear left (psi)"], ["coldRR", "Rear right (psi)"], ["compound", "Compound", "text"]]],
  ["Tire pressure, hot after the run", [["hotFL", "Front left (psi)"], ["hotFR", "Front right (psi)"], ["hotRL", "Rear left (psi)"], ["hotRR", "Rear right (psi)"]]],
  ["Alignment and chassis", [["camF", "Front camber (°)"], ["camR", "Rear camber (°)"], ["toeF", "Front toe (°)"], ["toeR", "Rear toe (°)"], ["bias", "Front brake bias (%)"], ["tqLimit", "Torque limit (Nm)"], ["aero", "Aero", "text"], ["susp", "Springs and dampers", "text"]]],
  ["Alert limits (leave blank for none)", [["aCellT", "Hottest cell above (°C)"], ["aInvT", "Inverter hotspot above (°C)"], ["aMotT", "Motor above (°C)"], ["aCoolT", "Coolant above (°C)"], ["aCellV", "Lowest cell below (V)"], ["aPackV", "Pack voltage below (V)"]]],
  ["Notes", [["notes", "Driver feedback and run notes", "area"], ["next", "Changes for the next run", "area"]]],
];

export const setupLabels: Record<string, string> = Object.fromEntries(SETUP_FORM.flatMap(([, fields]) => fields.map(([key, label]) => [key, label])));
export const numericSetupKeys = new Set(SETUP_FORM.flatMap(([, fields]) => fields.filter(([, , kind]) => !kind || kind === "number").map(([key]) => key)));
