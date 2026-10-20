/** Offset is the video time corresponding to telemetry session time zero, in seconds. */
export function videoTime(telemetryMs: number, offset: number, duration: number) {
  const time = telemetryMs / 1000 + offset;
  return { time: Math.max(0, Math.min(duration, time)), inRange: time >= 0 && time < duration };
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
