import { NextRequest, NextResponse } from 'next/server';
import prisma from '@/lib/prisma/telemetry';

// The active drive day's flags, so Trackside live can show flags it didn't log
// itself (another device, lost local state).
export async function GET() {
  try {
    const activeDay = await prisma.drive_day.findFirst({
      where: { status: 2 },
      orderBy: { day_id: 'desc' },
    });
    if (!activeDay) return NextResponse.json({ flags: [] });
    const rows = await prisma.classifier.findMany({
      where: { type: 'event_flag', day_id: BigInt(activeDay.day_id) },
      orderBy: { start_time: 'asc' },
      select: { start_time: true, notes: true },
    });
    return NextResponse.json({ dayId: activeDay.day_id, flags: rows.map((r) => ({ t: Number(r.start_time), label: r.notes ?? '' })) });
  } catch (error) {
    console.error('Error listing event flags:', error);
    return NextResponse.json({ error: 'Failed to list event flags' }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const activeDay = await prisma.drive_day.findFirst({
      where: { status: 2 },
      orderBy: { day_id: 'desc' },
    });

    if (!activeDay) {
      return NextResponse.json({ message: 'No active drive day found' }, { status: 400 });
    }

    const body = await req.json();
    // Use the client's flag time so the row matches the session JSON; fall back
    // to server time if it's missing or more than 5 min off (bad device clock).
    const t = Number(body.t);
    const startTime = Number.isSafeInteger(t) && Math.abs(t - Date.now()) < 5 * 60_000 ? t : Date.now();

    await prisma.classifier.create({
      data: {
        type: "event_flag",
        start_time: BigInt(startTime),
        day_id: BigInt(activeDay.day_id),
        notes: body.eventFlag,
      },
    });

    return NextResponse.json({ success: true }, { status: 200 });
  } catch (error) {
    console.error('Error flagging event:', error);
    return NextResponse.json({ error: 'Failed to flag event' }, { status: 500 });
  }
}
