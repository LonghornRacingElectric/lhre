export type TireSetCar = "orion" | "angelique";

export interface TireSetOption {
  id: string;
  label: string;
}

/** Only select IDs present in the supplied catalogue, including after it loads. */
export function availableTireSetId(id: string, tireSets: readonly TireSetOption[]): string {
  return tireSets.some((set) => set.id === id) ? id : "";
}

type PreferenceStorage = Pick<Storage, "getItem" | "setItem">;
// Separate from the old preview keys so sample IDs never become real choices.
const storageKey = (car: TireSetCar) => `trackside-tire-set:${car}`;

function browserStorage(): PreferenceStorage | undefined {
  try {
    return typeof window === "undefined" ? undefined : window.localStorage;
  } catch {
    return undefined;
  }
}

export function loadTireSetPreference(car: TireSetCar, storage = browserStorage()): string {
  try {
    return storage?.getItem(storageKey(car)) ?? "";
  } catch {
    return "";
  }
}

/** A browser preference only; never attached to session records or sent to the backend. */
export function saveTireSetPreference(car: TireSetCar, id: string, storage = browserStorage()): void {
  try {
    storage?.setItem(storageKey(car), id);
  } catch {
    // Starting a session still works when browser storage is unavailable.
  }
}
