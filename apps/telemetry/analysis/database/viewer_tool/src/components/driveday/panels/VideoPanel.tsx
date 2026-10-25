"use client";

import { useEffect, useRef, useState } from "react";
import { lsGet, lsSet } from "@/lib/driveday/storage";
import { videoCodec, videoTime } from "@/lib/driveday/video";
import { defaultLine } from "@/lib/driveday/video-overlay";
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
  const [linked, setLinked] = useState(p.videoLinked);
  const [ready, setReady] = useState(false);
  const [duration, setDuration] = useState(0);
  const [current, setCurrent] = useState(0);
  const [codec, setCodec] = useState(p.videoCodecLabel);
  const [error, setError] = useState("");
  const [blocked, setBlocked] = useState(false);
  const [outside, setOutside] = useState(false);
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
  useEffect(() => () => { sourceToken.current++; }, []);

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
    saveOffset(v.currentTime - p.T / 1000);
    p.videoLinked = true;
    setLinked(true);
  };

  useEffect(() => p.addPainter("video", () => {
    const v = video.current;
    if (!v || !visible || !linked || !ready || error || v.seeking) return;
    const target = videoTime(p.T, offset, v.duration);
    setOutside(!target.inRange);
    if (Math.abs(v.currentTime - target.time) > (p.playing ? 0.4 : 0.04)) v.currentTime = target.time;
    if (v.playbackRate !== p.rate) v.playbackRate = p.rate;
    if (p.playing && target.inRange && v.paused && !blocked && !playPending.current) {
      playPending.current = true;
      void v.play().catch(e => {
        if (e.name !== "AbortError") { setBlocked(true); p.play(false); }
      }).finally(() => { playPending.current = false; });
    } else if ((!p.playing || !target.inRange) && !v.paused) v.pause();
  }, true), [p, offset, linked, ready, error, blocked, visible]);
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
      <div ref={stage} className="video-stage" style={{ aspectRatio: aspect }} aria-busy={!ready && !error}>
        <video key={p.videoUrl} ref={video} src={p.videoUrl} controls={!linked} playsInline preload="auto"
          onLoadedMetadata={e => { setDuration(e.currentTarget.duration); if (e.currentTarget.videoHeight) setAspect(e.currentTarget.videoWidth / e.currentTarget.videoHeight); }}
          onLoadedData={() => { setReady(true); p.schedule(); }}
          onTimeUpdate={e => setCurrent(e.currentTarget.currentTime)}
          onSeeked={e => { setCurrent(e.currentTarget.currentTime); p.schedule(); }}
          onError={() => { setReady(false); p.play(false); setError("This browser could not decode the video."); }} />
        {ready && linked && !outside && !error && <div className="video-overlay-frame" style={{ width: frameSize.width, height: frameSize.height }}>
          <VideoOverlay settings={overlays} onCenter={center => changeOverlay({ center })} />
        </div>}
        {!ready && !error && <div className="video-stage-message" role="status">Loading video preview…</div>}
        {error && <div className="video-stage-message video-error" role="alert"><strong>Video unavailable</strong><span>{error}</span>
          <span>{codec.includes("HEVC") ? "This GoPro recording uses HEVC / H.265. Open an H.264 MP4 copy, or use a browser with HEVC support." : "Try an H.264 MP4 copy of this recording."}</span>
          <button className="btn" onClick={() => fileInput.current?.click()}>Open another video</button>
        </div>}
        {ready && linked && outside && <div className="video-boundary">{p.T / 1000 + offset < 0 ? "Recording starts later" : "End of recording"}</div>}
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
            <details className="video-fine"><summary>Fine-tune sync</summary><div className="video-nudge"><button className="btn" onClick={() => saveOffset(offset - 0.1)}>−0.1 s</button><label>Offset (seconds)<input type="number" step="0.1" value={Math.round(offset * 1000) / 1000} onChange={e => saveOffset(+e.target.value)} /></label><button className="btn" onClick={() => saveOffset(offset + 0.1)}>+0.1 s</button></div></details>
            <div className="video-overlay-toggles">
              <label><input type="checkbox" checked={overlays.hud} onChange={e => changeOverlay({ hud: e.target.checked })} /> Speed & pedals</label>
              <label><input type="checkbox" checked={overlays.map} onChange={e => changeOverlay({ map: e.target.checked })} /> Track map</label>
              <label><input type="checkbox" checked={overlays.line} onChange={e => changeOverlay({ line: e.target.checked })} /> Racing line</label>
            </div>
            <label className="video-overlay-field">HUD corner<select value={overlays.position} onChange={e => changeOverlay({ position: e.target.value as "left" | "right" })}><option value="left">Bottom left</option><option value="right">Bottom right</option></select></label>
            {overlays.line && <div className="video-line-settings">
              <label className="video-overlay-field">Line source<select value={overlays.ghost ? "ghost" : "current"} onChange={e => changeOverlay({ ghost: e.target.value === "ghost" })}><option value="current">Watched lap</option><option value="ghost" disabled={!p.refLap()}>Ghost lap</option></select></label>
              <label>Horizon<input aria-label="Racing line horizon" type="range" min="0.1" max="0.7" step="0.01" value={overlays.horizon} onInput={e => changeOverlay({ horizon: +e.currentTarget.value })} /></label>
              <label>Horizontal position<input aria-label="Racing line horizontal position" type="range" min="0.1" max="0.9" step="0.01" value={overlays.center} onInput={e => changeOverlay({ center: +e.currentTarget.value })} /></label>
              <label>Perspective scale<input aria-label="Racing line perspective" type="range" min="0.2" max="3" step="0.05" value={overlays.scale} onInput={e => changeOverlay({ scale: +e.currentTarget.value })} /></label>
              <button className="btn" onClick={() => changeOverlay(defaultLine)}>Reset line position</button>
              <p>Recorded GPS line projected ahead of the car. Green: throttle · yellow: lift · red: braking. Drag it sideways or adjust the sliders to fit the camera. This is an approximate preview, not a tracked or ideal racing line; camera movement, hills and GPS error affect alignment.</p>
            </div>}
          </details>
        </> : <div className="video-sync-card"><p>Pause video and the timeline at the same moment.</p><button className="btn primary" disabled={!ready} onClick={align}>Sync this frame</button></div>}
        {blocked && <div className="video-warning" role="status">Playback needs a click to start.<button className="btn" onClick={() => {
          const v = video.current;
          if (!v) return;
          void v.play().then(() => { setBlocked(false); p.play(true); }).catch(() => setBlocked(true));
        }}>Resume video</button></div>}
      </>}
    </>}
  </div>;
}
