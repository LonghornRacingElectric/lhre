import { describe, expect, it } from "vitest";
import { availableTireSetId, loadTireSetPreference, saveTireSetPreference } from "../../src/lib/trackside/tireSetPreferences";

function storageFixture() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
  };
}

describe("tire-set preferences", () => {
  it("starts unassigned and remembers each car independently across reads", () => {
    const storage = storageFixture();
    expect(loadTireSetPreference("orion", storage)).toBe("");
    expect(loadTireSetPreference("angelique", storage)).toBe("");
    saveTireSetPreference("orion", "42", storage);
    saveTireSetPreference("angelique", "8fb4fe01-aacb-4b36-bc6c-1bfdb84fd9b9", storage);
    expect(loadTireSetPreference("orion", storage)).toBe("42");
    expect(loadTireSetPreference("angelique", storage)).toBe("8fb4fe01-aacb-4b36-bc6c-1bfdb84fd9b9");
    saveTireSetPreference("orion", "", storage);
    expect(loadTireSetPreference("orion", storage)).toBe("");
    expect(loadTireSetPreference("angelique", storage)).toBe("8fb4fe01-aacb-4b36-bc6c-1bfdb84fd9b9");
  });

  it("resolves remembered IDs against the supplied catalogue without losing them while loading", () => {
    const storage = storageFixture();
    saveTireSetPreference("orion", "backend-id", storage);
    const id = loadTireSetPreference("orion", storage);
    expect(availableTireSetId(id, [])).toBe("");
    expect(loadTireSetPreference("orion", storage)).toBe("backend-id");
    expect(availableTireSetId(id, [{ id: "backend-id", label: "Set A" }])).toBe("backend-id");
    expect(availableTireSetId(id, [{ id: "replacement-id", label: "Set B" }])).toBe("");
    expect(availableTireSetId("", [{ id: "backend-id", label: "Set A" }])).toBe("");
  });

  it("ignores old sample preferences", () => {
    const storage = storageFixture();
    storage.setItem("trackside-preview-tire-set:orion", "sample-set-01");
    expect(loadTireSetPreference("orion", storage)).toBe("");
  });

  it("keeps session creation usable when storage is blocked", () => {
    const blocked = {
      getItem: (): string | null => { throw new Error("Storage blocked"); },
      setItem: () => { throw new Error("Storage blocked"); },
    };
    expect(loadTireSetPreference("orion", blocked)).toBe("");
    expect(() => saveTireSetPreference("orion", "42", blocked)).not.toThrow();
    expect(loadTireSetPreference("orion")).toBe("");
    expect(() => saveTireSetPreference("orion", "")).not.toThrow();
  });
});
