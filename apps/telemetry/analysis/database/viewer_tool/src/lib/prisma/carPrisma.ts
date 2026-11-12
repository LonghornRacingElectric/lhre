import prismaOrion from "@/lib/prisma/orion";
import prismaTelemetry from "@/lib/prisma/telemetry";
import { Prisma } from "../../../.prisma/orion-client";
import { readBuckets, storageContract } from "../motec/artemisStorage";
import { gatewaySessions, gatewayJSON, type GatewayRow } from "../query/client";

export type SupportedCar = "orion" | "artemis";
export type PacketScalar = bigint | number;
export type ReplaySample = {
  time: PacketScalar;
  packet_id?: PacketScalar;
  session_start_ms?: bigint;
  dynamics: Record<string, unknown> | null;
  controls: Record<string, unknown> | null;
};

export function normalizeCar(value: string | null | undefined): SupportedCar | null {
  const normalized = value?.trim().toLowerCase();
  return normalized === "orion" || normalized === "artemis" ? normalized : null;
}

export async function findLatestSampleTime(car: SupportedCar | null | undefined): Promise<PacketScalar | null> {
  if (car === "orion") {
    return (await prismaOrion.packet.findFirst({ orderBy: { time: "desc" }, select: { time: true } }))?.time ?? null;
  }
  const sessions = await gatewaySessions();
  return sessions.length ? BigInt(Math.max(...sessions.map(s => Number(s.end_ms)))) : null;
}

// Only the existing Orion protocol uses packet ranges.
export async function findLatestPacketId(car: SupportedCar | null | undefined): Promise<PacketScalar | null> {
  if (car !== "orion") return null;
  return (await prismaOrion.packet.findFirst({ orderBy: { packet_id: "desc" }, select: { packet_id: true } }))?.packet_id ?? null;
}

export async function findReplaySampleAtOrAfter(
  car: SupportedCar | null | undefined, startMs: PacketScalar, endMs: PacketScalar, atMs: PacketScalar,
): Promise<ReplaySample | null> {
  const lower = BigInt(atMs) > BigInt(startMs) ? BigInt(atMs) : BigInt(startMs);
  if (car === "orion") {
    const row = await prismaOrion.packet.findFirst({ where: { time: { gte: lower, lte: endMs } }, orderBy: { time: "asc" } });
    if (!row) return null;
    const data = await prismaOrion.$queryRaw<Array<{ dynamics: Record<string, unknown> | null; controls: Record<string, unknown> | null }>>(Prisma.sql`
      SELECT (SELECT to_jsonb(d) FROM dynamics d WHERE d.packet_id = ${row.packet_id} LIMIT 1) AS dynamics,
             (SELECT to_jsonb(c) FROM controls c WHERE c.packet_id = ${row.packet_id} LIMIT 1) AS controls
    `);
    return { time: row.time, packet_id: row.packet_id, dynamics: data[0]?.dynamics ?? null, controls: data[0]?.controls ?? null };
  }

  const first = await nextArtemisObservation(lower, BigInt(endMs));
  if (!first) return null;
  const stamp = Date.parse(first.ts);
  const rows = await readBuckets(["dynamics", "controls"], stamp, Math.min(Number(endMs) + 1, stamp + 100), 100);
  const row = rows[0];
  return row ? { time: BigInt(stamp), session_start_ms: row.session_start_ms, dynamics: row.dynamics as Record<string, unknown> ?? null, controls: row.controls as Record<string, unknown> ?? null } : null;
}

export async function findNextSampleTime(car: SupportedCar | null | undefined, time: PacketScalar, endMs: PacketScalar) {
  if (car === "orion") {
    return (await prismaOrion.packet.findFirst({ where: { time: { gt: time, lte: endMs } }, orderBy: { time: "asc" }, select: { time: true } }))?.time ?? null;
  }
  const row = await nextArtemisObservation(BigInt(time) + BigInt(1), BigInt(endMs));
  return row ? BigInt(Date.parse(row.ts)) : null;
}

async function nextArtemisObservation(from: bigint, to: bigint) {
  if (from > to) return null;
  const pools = storageContract().pools.filter(p => ["dynamics", "controls"].includes(p.group)).map(p => p.table);
  const page = await gatewayJSON<{ data: GatewayRow[] }>("/v1/query", { car: "artemis", signals: ["*"], pools, agg: "raw", t0: new Date(Number(from)).toISOString(), t1: new Date(Number(to) + 1).toISOString(), page_size: 1 });
  return page.data[0] ?? null;
}

export async function replayBounds(car: SupportedCar, day: { start_time: PacketScalar | null; end_time: PacketScalar | null; packet_start: PacketScalar | null; packet_end: PacketScalar | null }): Promise<[bigint, bigint] | null> {
  if (car === "orion" && day.packet_start != null && day.packet_end != null) {
    const [first, last] = await Promise.all([
      prismaOrion.packet.findUnique({ where: { packet_id: day.packet_start }, select: { time: true } }),
      prismaOrion.packet.findUnique({ where: { packet_id: day.packet_end }, select: { time: true } }),
    ]);
    if (first && last) return [first.time, last.time];
  }
  return day.start_time != null && day.end_time != null ? [BigInt(day.start_time), BigInt(day.end_time)] : null;
}

export async function resolveCarFromCarId(
  carId: number | null | undefined,
): Promise<SupportedCar | null> {
  if (carId == null) return null;
  const lut = await prismaTelemetry.lut_car.findUnique({
    where: { car_id: carId },
    select: { car_name: true },
  });
  return normalizeCar(lut?.car_name);
}

export async function resolveCarFromEvent(
  eventId: number,
): Promise<SupportedCar | null> {
  const driveDay = await prismaTelemetry.drive_day.findUnique({
    where: { day_id: eventId },
    select: {
      car_id: true,
      car: { select: { car_name: true } },
    },
  });
  if (!driveDay) return null;
  return normalizeCar(driveDay.car?.car_name) ?? (await resolveCarFromCarId(driveDay.car_id));
}
