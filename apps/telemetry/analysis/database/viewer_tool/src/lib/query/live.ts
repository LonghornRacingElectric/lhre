import { NextRequest, NextResponse } from "next/server";
import { buildNormalizedSensorRoutes } from "../kafka/kafkaConsumer";
import { normalizeLivePayload } from "../motec/live";
import { gatewayRequest } from "./gateway";
import type { GatewayRow } from "./client";

const logicalTopics = new Set(["sensor_data", "car_visualization", "driver_input_visualizer", "map", "live_banner", "dashboard_screen", "timing_deltas", "shutdown_screen", "thermal_headroom", "energy_budget"]);
const sourceFor = (topic: string) => topic === "sensor_data" ? topic : logicalTopics.has(topic) ? "grafana_data_artemis" : topic;

// Preserve existing viewer SSE shapes while the gateway owns Kafka access.
export async function gatewayLive(req: NextRequest, mode: "kafka" | "motec" | "status") {
  const requested = (req.nextUrl.searchParams.get("topics") || req.nextUrl.searchParams.get("topic") || (mode === "status" ? "car_status" : mode === "motec" ? "grafana_data_artemis_derived" : "sensor_data")).split(",").filter(Boolean);
  const sources = [...new Set(requested.map(sourceFor))];
  const encoder = new TextEncoder();
  const latest: Record<string, unknown> = { car_type: "artemis" };
  const seen = new Map<string, bigint>();
  const abort = new AbortController();
  let heartbeat: ReturnType<typeof setInterval> | undefined;
  const stop = () => { clearInterval(heartbeat); abort.abort(); };
  req.signal.addEventListener("abort", stop, { once: true });
  const activeReaders: ReadableStreamDefaultReader<Uint8Array>[] = [];
  const upstreams = await Promise.all(sources.map(async topic => {
    const query = { car: "artemis", signals: ["*"], agg: "raw", t0: new Date(Date.now() - 2000).toISOString(), t1: "live", window_ms: 2000, page_size: 10000 };
    const path = topic === "sensor_data" ? `/v1/stream?query=${encodeURIComponent(JSON.stringify(query))}` : `/v1/topics/${encodeURIComponent(topic)}`;
    return { topic, response: await gatewayRequest(new NextRequest(req.url, { headers: req.headers, signal: abort.signal }), path) };
  }));
  const failure = upstreams.find(s => !s.response.ok);
  if (failure) { stop(); return failure.response; }
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const send = (event: unknown) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
      heartbeat = setInterval(() => {
        try { controller.enqueue(encoder.encode(": heartbeat\n\n")); } catch { stop(); }
      }, 15000);
      const tasks = upstreams.map(async ({ topic, response }) => {
        const reader = response.body!.getReader(); activeReaders.push(reader);
        const decoder = new TextDecoder(); let buffer = "";
        try {
          while (!abort.signal.aborted) {
            const { value, done } = await reader.read(); if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let boundary: number;
            while ((boundary = buffer.indexOf("\n\n")) >= 0) {
              const chunk = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
              for (const line of chunk.split("\n")) {
                if (!line.startsWith("data: ")) continue;
                const event = JSON.parse(line.slice(6));
                if (event.type === "gap") { send(mode === "motec" ? { type: "status", ok: false, message: event.message } : { kind: "status", ok: false, message: event.message }); continue; }
                let payload: Record<string, unknown>;
                let timestamp: string;
                if (topic === "sensor_data") {
                  let changed = false;
                  for (const row of (event.page?.data || []) as GatewayRow[]) {
                    const ns = BigInt(row.sample_time_ns);
                    if (ns <= (seen.get(row.signal) ?? BigInt(0))) continue;
                    seen.set(row.signal, ns); changed = true;
                    const [group, field] = row.signal.split(".");
                    ((latest[group] ||= {}) as Record<string, unknown>)[field] = row.value;
                    latest.time = Math.max(Number(latest.time || 0), Date.parse(row.ts));
                  }
                  if (!changed) continue;
                  payload = latest; timestamp = String(latest.time);
                } else {
                  payload = JSON.parse(event.payload); timestamp = event.timestamp;
                }
                if (mode === "status") { send(payload); continue; }
                if (mode === "motec") {
                  const sample = normalizeLivePayload(payload, "artemis");
                  if (sample) send({ type: "sample", topic, transport: topic === "sensor_data" ? "protobuf" : "json", sample });
                  continue;
                }
                const normalized = topic === "sensor_data" || topic === "grafana_data_artemis" ? buildNormalizedSensorRoutes(payload, "artemis") : {};
                for (const target of requested.filter(t => sourceFor(t) === topic)) {
                  send({ topic: target, partition: event.partition ?? 0, offset: event.offset ?? timestamp, timestamp, headers: { car_type: "artemis" }, payload: JSON.stringify(normalized[target] ?? payload) });
                }
              }
            }
          }
        } finally { reader.releaseLock(); }
      });
      Promise.all(tasks).catch(() => { if (!abort.signal.aborted) { try { send({ kind: "status", ok: false, message: "Gateway live feed disconnected" }); } catch {} } }).finally(() => { stop(); req.signal.removeEventListener("abort", stop); try { controller.close(); } catch {} });
    },
    cancel() { stop(); for (const reader of activeReaders) void reader.cancel().catch(() => {}); },
  });
  return new NextResponse(stream, { headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store", "X-Accel-Buffering": "no" } });
}
