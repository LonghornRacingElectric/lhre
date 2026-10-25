"use client";

import { useEffect, useRef, useState } from "react";
import { lsGet, lsSet } from "@/lib/driveday/storage";
import { videoCodec, videoTime, syncFromCheckpoints, type SyncCheckpoint } from "@/lib/driveday/video";
import { defaultLine, type LineCalibration } from "@/lib/driveday/video-overlay";
import { compressVideo, playbackCopyName, type CompressionProgress } from "@/lib/driveday/video-compression";
import { usePlayer, useUi } from "../context";
import { VideoOverlay, type OverlaySettings } from "./VideoOverlay";

const timeLabel = (seconds: number) => {
  if (!Number.isFinite(seconds)) return "0:00";
  const t = Math.max(0, seconds);
  return Math.floor(t / 60) + ":" + String(Math.floor(t % 60)).padStart(2, "0") + "." + Math.floor(t % 1 * 10);
};

export function VideoPanel({ visible = true }: { visible?: boolean } = {}) {
  const p = usePlayer();
  useUi(p);
  const video = useRef<HTMLVideoElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const key = "video-offset:" + p.session.D.meta.sessId + ":" + p.videoKey;
  const [offset, setOffset] = useState(() => lsGet<number>(key, 0));
  const syncKey = "video-sync:" + p.session.D.meta.sessId + ":" + p.videoKey;
  const [checkpoints, setCheckpoints] = useState<SyncCheckpoint[]>(() => lsGet<SyncCheckpoint[]>(syncKey, []));
  const [syncRate, setSyncRate] = useState(() => { try { return syncFromCheckpoints(lsGet<SyncCheckpoint[]>(syncKey, [])).rate; } catch { return 1; } });
  const [syncError, setSyncError] = useState("");
  const [cameraPresets, setCameraPresets] = useState<Record<string, LineCalibration>>(() => ({ "Default onboard": defaultLine, ...lsGet<Record<string, LineCalibration>>("camera-presets", {}) }));
  const [cameraName, setCameraName] = useState("");
  const [cameraSelection, setCameraSelection] = useState("");
  const [linked, setLinked] = useState(p.videoLinked);
  const [ready, setReady] = useState(false);
  const [duration, setDuration] = useState(0);
  const [current, setCurrent] = useState(0);
  const [codec, setCodec] = useState(p.videoCodecLabel);
  const [error, setError] = useState("");
  const [blocked, setBlocked] = useState(false);
  const [outside, setOutside] = useState(false);
  const [compression, setCompression] = useState<CompressionProgress | null>(null);
  const [compressionError, setCompressionError] = useState("");
  const [copyBytes, setCopyBytes] = useState(0);
  const originalFile = useRef<File | null>(null);
  const compressionAbort = useRef<AbortController | null>(null);
  const restoreFrame = useRef<number | null>(null);
  const overlayKey = "video-overlay:" + p.session.D.meta.sessId + ":" + p.videoKey;
  const defaults: OverlaySettings = { ...defaultLine, hud: true, map: true, line: false, ghost: false, position: "left" };
  const [overlays, setOverlays] = useState<OverlaySettings>(() => ({ ...defaults, ...lsGet<Partial<OverlaySettings>>(overlayKey, {}) }));
  const [aspect, setAspect] = useState(16 / 9);
  const [frameSize, setFrameSize] = useState({ width: 0, height: 0 });
  const changeOverlay = (patch: Partial<OverlaySettings>) => {
    const next = { ...overlays, ...patch };
    setOverlays(next); lsSet(overlayKey, next);
  };
  useEffect(() => {
    setOverlays({ ...defaultLine, hud: true, map: true, line: false, ghost: false, position: "left", ...lsGet<Partial<OverlaySettings>>(overlayKey, {}) });
  }, [overlayKey]);
  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const fit = () => {
      const width = Math.min(element.clientWidth, element.clientHeight * aspect);
      setFrameSize({ width, height: width / aspect });
    };
    const observer = new ResizeObserver(fit);
    observer.observe(element); fit();
    return () => observer.disconnect();
  }, [aspect, p.videoUrl]);
  const playPending = useRef(false);
  const sourceToken = useRef(0);
  useEffect(() => () => { sourceToken.current++; compressionAbort.current?.abort(); }, []);

  const automaticallyCompress = async (file: File, token: number) => {
    compressionAbort.current?.abort();
    const controller = new AbortController(); compressionAbort.current = controller;
    setCompressionError(""); setCopyBytes(0);
    try {
      const blob = await compressVideo(file, controller.signal, value => { if (token === sourceToken.current) setCompression(value); });
      if (token !== sourceToken.current || controller.signal.aborted) return;
      const nextUrl = URL.createObjectURL(blob);
      restoreFrame.current = video.current?.currentTime ?? 0;
      video.current?.pause();
      const previousUrl = p.videoUrl;
      p.videoUrl = nextUrl; p.videoCodecLabel = "H.264";
      setCodec("H.264"); setReady(false); setError(""); setBlocked(false); setCopyBytes(blob.size);
      p.changed();
      if (previousUrl) URL.revokeObjectURL(previousUrl);
    } catch (cause) {
      if (token === sourceToken.current) setCompressionError(controller.signal.aborted ? "Compression cancelled. The original video is still selected." : (cause as Error).message);
    } finally {
      if (compressionAbort.current === controller) { compressionAbort.current = null; setCompression(null); }
    }
  };

  const saveOffset = (value: number) => {
    if (!Number.isFinite(value)) return;
    setOffset(value);
    lsSet(key, value);
    p.schedule();
  };
  const preview = () => {
    p.play(false);
    video.current?.pause();
    p.videoLinked = false;
    setLinked(false);
    setOutside(false);
  };
  const align = () => {
    const v = video.current;
    if (!v || !ready) return;
    p.play(false);
    v.pause();
    const points = [{ telemetry: p.T, video: v.currentTime }];
    const model = syncFromCheckpoints(points);
    setCheckpoints(points); lsSet(syncKey, points); setSyncRate(1); setOffset(model.offset); lsSet(key, model.offset); setSyncError("");
    p.videoLinked = true;
    setLinked(true);
  };
  const secondCheckpoint = () => {
    const v = video.current;
    if (!v || !checkpoints.length) return;
    try {
      const points = [checkpoints[0], { telemetry: p.T, video: v.currentTime }];
      const model = syncFromCheckpoints(points);
      p.play(false); v.pause(); setCheckpoints(points); lsSet(syncKey, points);
      setSyncRate(model.rate); setOffset(model.offset); lsSet(key, model.offset); setSyncError("");
      p.videoLinked = true; setLinked(true);
    } catch (error) { setSyncError((error as Error).message); }
  };
  useEffect(() => {
    const saved = lsGet<SyncCheckpoint[]>(syncKey, []);
    setCheckpoints(saved); setSyncError("");
    try { const model = syncFromCheckpoints(saved); setSyncRate(model.rate); setOffset(lsGet<number>(key, model.offset)); } catch { setSyncRate(1); setOffset(lsGet<number>(key, 0)); }
  }, [syncKey, key]);
  useEffect(() => {
    const jump = (event: Event) => {
      const detail = (event as CustomEvent<{ run: string; key: string; name: string; time: number }>).detail;
      if (detail.run !== p.session.D.meta.sessId) return;
      const v = video.current;
      if (!v || detail.key !== p.videoKey) { p.toast("Open " + detail.name + " to view this video bookmark."); return; }
      if (!linked && Number.isFinite(v.duration)) { v.pause(); v.currentTime = Math.max(0, Math.min(v.duration, detail.time)); }
    };
    window.addEventListener("driveday:bookmark", jump);
    return () => window.removeEventListener("driveday:bookmark", jump);
  }, [p, linked]);

  useEffect(() => p.addPainter("video", () => {
    const v = video.current;
    if (!v || !visible || !linked || !ready || error || v.seeking) return;
    const target = videoTime(p.T, offset, v.duration, syncRate);
    setOutside(!target.inRange);
    if (Math.abs(v.currentTime - target.time) > (p.playing ? 0.4 : 0.04)) v.currentTime = target.time;
    if (v.playbackRate !== p.rate * syncRate) v.playbackRate = p.rate * syncRate;
    p.videoCurrentTime = target.time;
    if (p.playing && target.inRange && v.paused && !blocked && !playPending.current) {
      playPending.current = true;
      void v.play().catch(e => {
        if (e.name !== "AbortError") { setBlocked(true); p.play(false); }
      }).finally(() => { playPending.current = false; });
    } else if ((!p.playing || !target.inRange) && !v.paused) v.pause();
  }, true), [p, offset, linked, ready, error, blocked, visible, syncRate]);
  useEffect(() => { if (!visible) video.current?.pause(); }, [visible]);
  useEffect(() => {
    const element = video.current;
    return () => element?.pause();
  }, [p.videoUrl]);

  return <div className="video-panel" onKeyDown={e => e.stopPropagation()}>
    <input ref={fileInput} type="file" accept="video/*,.mp4,.webm,.mov" hidden onChange={e => {
      const file = e.target.files?.[0];
      if (!file) return;
      const token = ++sourceToken.current;
      compressionAbort.current?.abort(); originalFile.current = file; restoreFrame.current = null;
      preview();
      setReady(false); setDuration(0); setCurrent(0); setError(""); setBlocked(false); setCodec("");
      if (p.videoUrl) URL.revokeObjectURL(p.videoUrl);
      p.videoUrl = URL.createObjectURL(file);
      p.videoName = file.name;
      p.videoKey = file.name + ":" + file.size + ":" + file.lastModified;
      p.videoCodecLabel = "";
      setOffset(lsGet<number>("video-offset:" + p.session.D.meta.sessId + ":" + p.videoKey, 0));
      void videoCodec(file).then(value => { if (token === sourceToken.current) { p.videoCodecLabel = value; setCodec(value); } }).catch(() => {});
      p.changed();
      void automaticallyCompress(file, token);
      e.target.value = "";
    }} />
    {!p.videoUrl ? <div className="video-empty">
      <span className="video-empty-icon" aria-hidden="true">▶</span>
      <h4>Watch your drive</h4>
      <p>Add an onboard video, then match a frame to the telemetry.</p>
      <button className="btn primary" onClick={() => fileInput.current?.click()}>Open video</button>
      <small>Local files only · MP4, MOV or WebM</small>
    </div> : <>
      <div className="video-filebar"><div><strong title={p.videoName}>{p.videoName}</strong><small>{codec || "Local video"}{duration > 0 ? " · " + timeLabel(duration) : ""}</small></div>
        <button className="btn" onClick={() => fileInput.current?.click()}>Replace</button>
      </div>
      {compression && <div className="video-compression" role="status"><div><strong>{({ connecting: "Connecting to local compressor", preparing: "Preparing video locally", compressing: "Compressing playback copy", loading: "Loading playback copy" })[compression.stage]}{compression.percent != null ? " · " + Math.round(compression.percent) + "%" : "…"}</strong><progress aria-label="Video compression progress" max={100} value={compression.percent ?? undefined} /><small>Stays on this computer · original unchanged</small></div><button className="btn" onClick={() => compressionAbort.current?.abort()}>Cancel</button></div>}
      {compressionError && <div className="video-compression-error" role="status"><span>{compressionError}</span><button className="btn" onClick={() => { if (originalFile.current) void automaticallyCompress(originalFile.current, sourceToken.current); }}>Retry compression</button>{compressionError.startsWith("Start the local") && <details><summary>Start video helper</summary><p>Run this from the LAPDOG folder, then retry:</p><code>npm run video-helper{typeof window !== "undefined" && !["localhost", "127.0.0.1"].includes(window.location.hostname) ? " -- --origin " + window.location.origin : ""}</code></details>}</div>}
      {copyBytes > 0 && !compression && <div className="video-copy-ready"><span>Playback copy · {(copyBytes / 1024 / 1024).toFixed(1)} MB{originalFile.current ? " from " + (originalFile.current.size / 1024 / 1024).toFixed(1) + " MB" : ""}</span><a className="btn" href={p.videoUrl} download={playbackCopyName(p.videoName)}>Save copy</a></div>}
      <div ref={stage} className="video-stage" style={{ aspectRatio: aspect }} aria-busy={!ready && !error}>
        <video key={p.videoUrl} ref={video} src={p.videoUrl} controls={!linked} playsInline preload="auto"
          onLoadedMetadata={e => { setDuration(e.currentTarget.duration); if (e.currentTarget.videoHeight) setAspect(e.currentTarget.videoWidth / e.currentTarget.videoHeight); if (restoreFrame.current != null) { e.currentTarget.currentTime = Math.max(0, Math.min(e.currentTarget.duration, restoreFrame.current)); restoreFrame.current = null; } }}
          onLoadedData={() => { setReady(true); p.schedule(); }}
          onTimeUpdate={e => { setCurrent(e.currentTarget.currentTime); p.videoCurrentTime = e.currentTarget.currentTime; }}
          onSeeked={e => { setCurrent(e.currentTarget.currentTime); p.videoCurrentTime = e.currentTarget.currentTime; p.schedule(); }}
          onError={() => { setReady(false); p.play(false); setError("This browser could not decode the video."); }} />
        {ready && linked && !outside && !error && <div className="video-overlay-frame" style={{ width: frameSize.width, height: frameSize.height }}>
          <VideoOverlay settings={overlays} onCenter={center => changeOverlay({ center })} />
        </div>}
        {!ready && !error && <div className="video-stage-message" role="status">Loading video preview…</div>}
        {error && <div className="video-stage-message video-error" role="alert"><strong>Video unavailable</strong><span>{error}</span>
          <span>{codec.includes("HEVC") ? "This GoPro recording uses HEVC / H.265. Open an H.264 MP4 copy, or use a browser with HEVC support." : "Try an H.264 MP4 copy of this recording."}</span>
          <button className="btn" onClick={() => fileInput.current?.click()}>Open another video</button>
        </div>}
        {ready && linked && outside && <div className="video-boundary">{p.T / 1000 * syncRate + offset < 0 ? "Recording starts later" : "End of recording"}</div>}
        <div className="video-fullscreen-controls"><button className="btn" onClick={() => p.play(!p.playing)}>{p.playing ? "Pause video" : "Play video"}</button><button className="btn" onClick={() => void document.exitFullscreen().catch(() => {})}>Exit fullscreen</button></div>
      </div>
      {!error && <>
        <div className="video-modebar"><span className={"video-badge " + (linked ? "synced" : "")}>{linked ? "Synced to telemetry" : "Video preview"}</span><span className="video-clock">{timeLabel(current)} / {timeLabel(duration)}</span></div>
        {linked ? <>
          <div className="video-playbar">
            <button className="btn" onClick={preview}>Adjust sync</button>
            <button className="btn" disabled={!ready} title="Fullscreen video with overlays" onClick={() => void stage.current?.requestFullscreen().catch(() => {})}>Fullscreen</button>
          </div>
          <details className="video-overlay-controls"><summary>Video settings</summary>
            <details className="review-details"><summary>Sync checkpoints and clock drift</summary><p className="note2">Align one event near the start, then use Adjust sync to pause video and telemetry at the same event near the end. Set a second checkpoint to correct clock drift.</p>
              {checkpoints.map((point, i) => <p key={i}>Checkpoint {i + 1}: telemetry {timeLabel(point.telemetry / 1000)} → video {timeLabel(point.video)}</p>)}
              <p>Clock correction: {((syncRate - 1) * 100).toFixed(3)}%</p>
              <button className="btn" onClick={() => { preview(); }}>Adjust sync for checkpoint</button>
              <button className="btn" onClick={() => { if (checkpoints[0]) { const first = [checkpoints[0]]; const model = syncFromCheckpoints(first); setCheckpoints(first); lsSet(syncKey, first); setSyncRate(1); setOffset(model.offset); lsSet(key, model.offset); } }}>Remove drift correction</button>
            </details>
            <details className="video-fine"><summary>Fine-tune sync</summary><div className="video-nudge"><button className="btn" onClick={() => saveOffset(offset - 0.1)}>−0.1 s</button><label>Offset (seconds)<input type="number" step="0.1" value={Math.round(offset * 1000) / 1000} onChange={e => saveOffset(+e.target.value)} /></label><button className="btn" onClick={() => saveOffset(offset + 0.1)}>+0.1 s</button></div></details>
            <div className="video-overlay-toggles">
              <label><input type="checkbox" checked={overlays.hud} onChange={e => changeOverlay({ hud: e.target.checked })} /> Speed & pedals</label>
              <label><input type="checkbox" checked={overlays.map} onChange={e => changeOverlay({ map: e.target.checked })} /> Track map</label>
              <label><input type="checkbox" checked={overlays.line} onChange={e => changeOverlay({ line: e.target.checked })} /> Racing line</label>
            </div>
            <label className="video-overlay-field">HUD corner<select value={overlays.position} onChange={e => changeOverlay({ position: e.target.value as "left" | "right" })}><option value="left">Bottom left</option><option value="right">Bottom right</option></select></label>
            {overlays.line && <div className="video-line-settings">
              <details className="review-details"><summary>Camera calibration presets</summary><label>Mounting position<select aria-label="Camera preset" value={cameraSelection} onChange={e => { setCameraSelection(e.target.value); const preset = cameraPresets[e.target.value]; if (preset) changeOverlay(preset); }}><option value="">Choose saved camera</option>{Object.keys(cameraPresets).map(name => <option key={name} value={name}>{name}</option>)}</select></label><input aria-label="Camera preset name" value={cameraName} onChange={e => setCameraName(e.target.value)} placeholder="e.g. GoPro nose mount" /><button className="btn" disabled={!cameraName.trim()} onClick={() => { const name = cameraName.trim(); const preset = { center: overlays.center, horizon: overlays.horizon, scale: overlays.scale, lookahead: overlays.lookahead }; const next = { ...cameraPresets, [name]: preset }; setCameraPresets(next); lsSet("camera-presets", next); setCameraSelection(name); setCameraName(""); }}>Save camera calibration</button><p className="note2">Reusable manual camera positioning; it does not track road features automatically.</p></details>
              <label className="video-overlay-field">Line source<select value={overlays.ghost ? "ghost" : "current"} onChange={e => changeOverlay({ ghost: e.target.value === "ghost" })}><option value="current">Watched lap</option><option value="ghost" disabled={!p.refLap()}>Ghost lap</option></select></label>
              <label>Horizon<input aria-label="Racing line horizon" type="range" min="0.1" max="0.7" step="0.01" value={overlays.horizon} onInput={e => changeOverlay({ horizon: +e.currentTarget.value })} /></label>
              <label>Horizontal position<input aria-label="Racing line horizontal position" type="range" min="0.1" max="0.9" step="0.01" value={overlays.center} onInput={e => changeOverlay({ center: +e.currentTarget.value })} /></label>
              <label>Perspective scale<input aria-label="Racing line perspective" type="range" min="0.2" max="3" step="0.05" value={overlays.scale} onInput={e => changeOverlay({ scale: +e.currentTarget.value })} /></label>
              <button className="btn" onClick={() => changeOverlay(defaultLine)}>Reset line position</button>
              <p>Recorded GPS line projected ahead of the car. Green: throttle · yellow: lift · red: braking. Drag it sideways or adjust the sliders to fit the camera. This is an approximate preview, not a tracked or ideal racing line; camera movement, hills and GPS error affect alignment.</p>
            </div>}
          </details>
        </> : <div className="video-sync-card"><p>Pause video and the timeline at the same moment.</p><button className="btn primary" disabled={!ready} onClick={align}>Sync this frame / first checkpoint</button>{checkpoints.length > 0 && <button className="btn" disabled={!ready} onClick={secondCheckpoint}>Set second sync checkpoint</button>}{syncError && <p role="alert">{syncError}</p>}</div>}
        {blocked && <div className="video-warning" role="status">Playback needs a click to start.<button className="btn" onClick={() => {
          const v = video.current;
          if (!v) return;
          void v.play().then(() => { setBlocked(false); p.play(true); }).catch(() => setBlocked(true));
        }}>Resume video</button></div>}
      </>}
    </>}
  </div>;
}
