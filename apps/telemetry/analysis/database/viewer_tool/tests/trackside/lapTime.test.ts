import { describe, expect, it } from "vitest";
import { parseLapTimeInput } from "../../src/lib/trackside/lapTime";
import { suggestSessionName } from "../../src/lib/trackside/sessionRegistry";

describe("parseLapTimeInput", () => {
  it("reads m:ss.ss and plain seconds", () => {
    expect(parseLapTimeInput("1:23.45")).toBe(83450);
    expect(parseLapTimeInput(" 83.45 ")).toBe(83450);
    expect(parseLapTimeInput("0:05")).toBe(5000);
  });
  it("rejects junk", () => {
    expect(parseLapTimeInput("")).toBeNull();
    expect(parseLapTimeInput("1:75")).toBeNull();
    expect(parseLapTimeInput("-3")).toBeNull();
    expect(parseLapTimeInput("abc")).toBeNull();
    expect(parseLapTimeInput("1e308")).toBeNull();
    expect(parseLapTimeInput("99999999999999:00")).toBeNull();
  });
});

describe("suggestSessionName", () => {
  it("venue · type N · date, N from this device's registry (empty here)", () => {
    const t = new Date(2026, 9, 16, 9, 30).getTime(); // 2026-10-16 local
    expect(suggestSessionName("COTA", "Autocross", t)).toBe("COTA · Autocross 1 · 2026-10-16");
    expect(suggestSessionName("", "Endurance", t)).toBe("Session · Endurance 1 · 2026-10-16");
  });
});
