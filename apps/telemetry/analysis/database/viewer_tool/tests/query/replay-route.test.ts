import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "../../src/app/api/replay/stream/route";

const queries = vi.hoisted(() => ({ day: vi.fn(), sample: vi.fn(), next: vi.fn() }));
vi.mock("@/lib/prisma/telemetry", () => ({ default: { drive_day: { findUnique: queries.day } } }));
vi.mock("@/lib/prisma/carPrisma", () => ({
  normalizeCar: (car: string | null) => car,
  replayBounds: (_car: string, day: { start_time: bigint; end_time: bigint }) => [day.start_time, day.end_time],
  resolveCarFromCarId: vi.fn(),
  findReplaySampleAtOrAfter: queries.sample,
  findNextSampleTime: queries.next,
}));
const stamp = 1794484800000;
const url = `http://localhost/api/replay/stream?eventId=1&car=artemis&startAtTimeMs=${stamp}`;
const payload = (event: string) => JSON.parse(event.split("\n").find(line => line.startsWith("data: "))!.slice(6));

beforeEach(() => {
  vi.resetAllMocks();
  queries.day.mockResolvedValue({ start_time: BigInt(stamp), end_time: BigInt(stamp + 1000) });
  queries.sample.mockResolvedValue({ time: BigInt(stamp), session_start_ms: 42n,
    controls: { apps1_travel: 40 },
    dynamics: { gps: [30.28, -97.73], gps_precise: [30.28123456789, -97.73123456789, 250, .01, 2, 1794484800] } });
  queries.next.mockResolvedValue(null);
});
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); });

it("maps native Artemis throttle and precise GPS into replay widgets", async () => {
  const response = await GET(new NextRequest(url));
  expect(response.status).toBe(200);
  const sample = payload(await response.text());
  expect(sample.driver_input_visualizer.controls.throttlePct).toBe(40);
  expect(sample.map_data.dynamics.gps).toEqual([30.28123456789, -97.73123456789]);
});

it("stops replay queries after reaching the end of a playing stream", async () => {
  vi.useFakeTimers();
  queries.next.mockResolvedValueOnce(BigInt(stamp + 1)).mockResolvedValue(null);
  const response = await GET(new NextRequest(url + "&playing=1&tickMs=25"));
  const reader = response.body!.getReader();
  await reader.read(); // connected comment
  await reader.read(); // first sample
  await vi.advanceTimersByTimeAsync(25);
  const last = await reader.read();
  expect(payload(new TextDecoder().decode(last.value)).is_end).toBe(true);
  expect((await reader.read()).done).toBe(true);
  await vi.advanceTimersByTimeAsync(1000);
  expect(queries.sample).toHaveBeenCalledTimes(2);
});
