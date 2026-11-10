import { NextRequest, NextResponse } from "next/server";
import prismaTelemetry from "@/lib/prisma/telemetry";
import {
  findLatestSampleTime,
  findLatestPacketId,
  normalizeCar,
  resolveCarFromCarId,
} from "@/lib/prisma/carPrisma";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const time = Date.now();

  const { searchParams } = new URL(req.url);

  try {
    let car = normalizeCar(searchParams.get("car"));

    if (!car) {
      const latestDay = await prismaTelemetry.drive_day.findFirst({
        orderBy: { day_id: "desc" },
        select: {
          car_id: true,
          car: { select: { car_name: true } },
        },
      });

      car =
        normalizeCar(latestDay?.car?.car_name) ??
        (await resolveCarFromCarId(latestDay?.car_id)) ??
        "orion";
    }

    const latestSampleTime = await findLatestSampleTime(car);
    const latest_sample_ms =
      latestSampleTime != null ? latestSampleTime.toString() : null;
    if (car === "orion") {
      const packet = await findLatestPacketId(car);
      return NextResponse.json({ time, last_packet: packet?.toString() ?? 0, latest_sample_ms, car });
    }
    return NextResponse.json({ time, latest_sample_ms, car });
  } catch (e) {
    console.error("Error in handshake:", e);
    return NextResponse.json(
      { error: "Failed to perform handshake" },
      { status: 500 },
    );
  }
}
