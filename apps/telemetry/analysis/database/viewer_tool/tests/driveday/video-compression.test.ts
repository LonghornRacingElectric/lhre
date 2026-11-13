import { afterEach, expect, it, vi } from "vitest";
import { compressVideo, playbackCopyName } from "../../src/lib/driveday/video-compression";

afterEach(() => vi.unstubAllGlobals());

function transport(stall = false) {
  const calls: { path: string; method: string }[] = [];
  const fetch = vi.fn(async (url: string, options?: RequestInit) => {
    const path = new URL(url).pathname, method = options?.method ?? "GET"; calls.push({ path, method });
    if (path === "/health") return new Response(JSON.stringify({ token: "local-test", maxBytes: 1000000 }));
    if (path === "/jobs") return new Response(JSON.stringify({ id: "job-1" }), { status: 201 });
    if (path.endsWith("/output")) return new Response(new Blob(["compressed"], { type: "video/mp4" }));
    return new Response(JSON.stringify({ state: "ready", ok: true }));
  });
  class Request {
    upload = { onprogress: null as ((event: { lengthComputable: boolean; loaded: number; total: number }) => void) | null };
    status = 202; responseText = "";
    onerror: (() => void) | null = null; onabort: (() => void) | null = null; onload: (() => void) | null = null;
    open() {} setRequestHeader() {}
    send(file: File) { expect(file.name).toBe("drive.mov"); this.upload.onprogress?.({ lengthComputable: true, loaded: file.size, total: file.size }); if (!stall) queueMicrotask(() => this.onload?.()); }
    abort() { this.onabort?.(); }
  }
  vi.stubGlobal("fetch", fetch); vi.stubGlobal("XMLHttpRequest", Request);
  return calls;
}

it("automatically returns a playable copy and cleans up the local job", async () => {
  const calls = transport(); const progress = vi.fn();
  const original = new File(["source"], "drive.mov");
  const result = await compressVideo(original, new AbortController().signal, progress);
  expect(await result.text()).toBe("compressed");
  expect(await original.text()).toBe("source");
  expect(calls.at(-1)).toEqual({ path: "/jobs/job-1", method: "DELETE" });
  expect(progress).toHaveBeenCalledWith({ stage: "loading", percent: 100 });
  expect(playbackCopyName(original.name)).toBe("drive-playback.mp4");
});

it("cancels the local transfer and deletes the job before the next selection", async () => {
  const calls = transport(true); const controller = new AbortController();
  const progress = vi.fn(value => { if (value.stage === "preparing" && value.percent === 100) controller.abort(); });
  await expect(compressVideo(new File(["source"], "drive.mov"), controller.signal, progress)).rejects.toMatchObject({ name: "AbortError" });
  expect(calls.at(-1)?.method).toBe("DELETE");
});

it("explains unavailable local helpers rather than reporting an unrelated decoder error", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
  await expect(compressVideo(new File(["source"], "drive.mov"), new AbortController().signal, vi.fn())).rejects.toThrow("Start the local video helper");
});
