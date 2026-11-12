export interface CompressionProgress { stage: "connecting" | "preparing" | "compressing" | "loading"; percent: number | null }
const HELPER = "http://127.0.0.1:43129";

export const playbackCopyName = (name: string) => name.replace(/\.[^.]+$/, "") + "-playback.mp4";

let previousConversion: Promise<unknown> = Promise.resolve();
export function compressVideo(file: File, signal: AbortSignal, progress: (value: CompressionProgress) => void): Promise<Blob> {
  const previous = previousConversion;
  const next = (async () => {
    await previous.catch(() => {});
    if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
    return compressVideoNow(file, signal, progress);
  })();
  previousConversion = next.catch(() => {});
  return next;
}

async function compressVideoNow(file: File, signal: AbortSignal, progress: (value: CompressionProgress) => void): Promise<Blob> {
  progress({ stage: "connecting", percent: null });
  let health: { token: string; maxBytes: number };
  try {
    const response = await fetch(HELPER + "/health", { signal: AbortSignal.timeout(3000) });
    if (!response.ok) throw new Error();
    health = await response.json();
    if (!health.token || !health.maxBytes) throw new Error();
  } catch {
    if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
    throw new Error("Start the local video helper to compress this recording. If the browser asks for local-network access, allow it for LAPDOG.");
  }
  if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
  if (!file.size || file.size > health.maxBytes) throw new Error("Automatic conversion supports nonempty videos up to 8 GB.");
  const headers = { Authorization: "Bearer " + health.token };
  // Finish learning the job ID even if selection changes, so cancellation can delete it.
  const create = await fetch(HELPER + "/jobs", { method: "POST", headers, signal: AbortSignal.timeout(5000) });
  const created = await create.json();
  if (!create.ok) throw new Error(created.error || "Could not start local compression.");
  const path = HELPER + "/jobs/" + encodeURIComponent(created.id);
  try {
    if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
    progress({ stage: "preparing", percent: 0 });
    await new Promise<void>((resolve, reject) => {
      const request = new XMLHttpRequest();
      const abort = () => request.abort();
      signal.addEventListener("abort", abort, { once: true });
      const finish = (error?: Error) => { signal.removeEventListener("abort", abort); if (error) reject(error); else resolve(); };
      request.open("PUT", path + "/source");
      request.setRequestHeader("Authorization", headers.Authorization);
      request.setRequestHeader("Content-Type", "application/octet-stream");
      request.upload.onprogress = event => progress({ stage: "preparing", percent: event.lengthComputable ? event.loaded / event.total * 100 : null });
      request.onerror = () => finish(new Error("The local video helper disconnected."));
      request.onabort = () => finish(new DOMException("Cancelled", "AbortError"));
      request.onload = () => { if (request.status === 202) finish(); else { let message = "Could not prepare this video locally."; try { message = JSON.parse(request.responseText).error || message; } catch {} finish(new Error(message)); } };
      if (signal.aborted) { finish(new DOMException("Cancelled", "AbortError")); return; }
      request.send(file);
    });
    for (;;) {
      const response = await fetch(path, { headers, signal });
      const job = await response.json();
      if (!response.ok || job.state === "error") throw new Error(job.error || "Video compression failed.");
      if (job.state === "ready") break;
      progress({ stage: "compressing", percent: job.progress });
      await new Promise<void>((resolve, reject) => {
        const abort = () => { clearTimeout(timer); reject(new DOMException("Cancelled", "AbortError")); };
        const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, 500);
        signal.addEventListener("abort", abort, { once: true });
        if (signal.aborted) abort();
      });
    }
    progress({ stage: "loading", percent: 100 });
    const response = await fetch(path + "/output", { headers, signal });
    if (!response.ok) throw new Error("Could not load the compressed playback copy.");
    return await response.blob();
  } finally {
    await fetch(path, { method: "DELETE", headers, signal: AbortSignal.timeout(5000) }).catch(() => {});
  }
}
