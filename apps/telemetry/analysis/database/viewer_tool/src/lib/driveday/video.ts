/** Offset is the video time corresponding to telemetry session time zero, in seconds. */
export function videoTime(telemetryMs: number, offset: number, duration: number, rate = 1) {
  const time = telemetryMs / 1000 * rate + offset;
  return { time: Math.max(0, Math.min(duration, time)), inRange: time >= 0 && time < duration };
}

export interface SyncCheckpoint { telemetry: number; video: number }
export function syncFromCheckpoints(points: SyncCheckpoint[]): { offset: number; rate: number } {
  if (!points.length || points.some(p => !Number.isFinite(p.telemetry) || !Number.isFinite(p.video) || p.telemetry < 0 || p.video < 0)) throw new Error("Choose valid video and telemetry times.");
  const sorted = [...points].sort((a, b) => a.telemetry - b.telemetry);
  const first = sorted[0], last = sorted.at(-1)!;
  const rate = sorted.length === 1 ? 1 : (last.video - first.video) / ((last.telemetry - first.telemetry) / 1000);
  if (sorted.length > 1 && (last.telemetry - first.telemetry < 1000 || !(rate > 0))) throw new Error("The second checkpoint must be later in both video and telemetry, at least one second apart.");
  if (rate < 0.8 || rate > 1.2) throw new Error("These checkpoints imply more than 20% clock drift. Check that they mark the same events.");
  return { rate, offset: first.video - first.telemetry / 1000 * rate };
}

/** Read small MP4 header/tail slices, never the entire multi-GB camera recording. */
export async function videoCodec(file: Blob): Promise<string> {
  const slices = [file.slice(0, 1024 * 1024), file.slice(Math.max(0, file.size - 1024 * 1024))];
  for (const slice of slices) {
    const bytes = new Uint8Array(await slice.arrayBuffer());
    const text = new TextDecoder("latin1").decode(bytes);
    for (const [tag, label] of [["hvc1", "HEVC / H.265"], ["hev1", "HEVC / H.265"], ["avc1", "H.264"], ["av01", "AV1"]]) {
      let i = text.indexOf(tag);
      while (i >= 4) {
        const size = new DataView(bytes.buffer).getUint32(i - 4);
        if (size >= 86 && size < 1024 * 1024) return label;
        i = text.indexOf(tag, i + 4);
      }
    }
  }
  return "";
}
