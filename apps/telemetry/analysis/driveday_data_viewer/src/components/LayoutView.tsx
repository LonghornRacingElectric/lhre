"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { layoutName } from "@/lib/layouts";
import { Geo, roadWidth } from "@/lib/geometry";
import type { Track, XY } from "@/lib/types";

const points = (path: XY[]) => path.map(p => `${p[0]},${-p[1]}`).join(" ");

/** Geometry-only view: a saved road needs no timed laps, player or invented telemetry. */
export function LayoutView({ track }: { track: Track }) {
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ x: number; y: number; cx: number; cy: number } | null>(null);
  const bounds = useMemo(() => {
    const path = track.edges?.centre ?? track.ref;
    const xs = path.map(p => p[0]), ys = path.map(p => -p[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    return { cx: (minX + maxX) / 2, cy: (minY + maxY) / 2, w: maxX - minX + 36, h: maxY - minY + 36 };
  }, [track]);
  const [zoom, setZoom] = useState(1);
  const [centre, setCentre] = useState({ x: bounds.cx, y: bounds.cy });
  const [size, setSize] = useState({ w: 1000, h: 600 });
  const [showCentre, setShowCentre] = useState(false);
  const width = bounds.w / zoom, height = bounds.h / zoom;
  const scale = Math.min(size.w / width, size.h / height);
  const changeZoom = (factor: number) => setZoom(z => Math.max(0.5, Math.min(12, z * factor)));
  const fit = () => { setZoom(1); setCentre({ x: bounds.cx, y: bounds.cy }); };
  useEffect(() => {
    if (!svg.current) return;
    const observer = new ResizeObserver(entries => {
      const rect = entries[0].contentRect;
      if (rect.width && rect.height) setSize({ w: rect.width, h: rect.height });
    });
    observer.observe(svg.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const element = svg.current;
    if (!element) return;
    // React wheel handlers can be passive, which would also scroll the workspace.
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      const pixels = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element.clientHeight : 1);
      const next = Math.max(0.5, Math.min(12, zoom * Math.exp(-pixels * 0.0016)));
      const matrix = element.getScreenCTM();
      if (matrix) {
        const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
        const ratio = zoom / next;
        setCentre({ x: point.x + (centre.x - point.x) * ratio, y: point.y + (centre.y - point.y) * ratio });
      }
      setZoom(next);
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, [zoom, centre]);
  const geo = useMemo(() => new Geo(track.ref, track.cum, track.RL), [track]);
  const sectors = useMemo(() => [0, 1, 2].map(i => {
    const a = i * track.RL / 3, b = (i + 1) * track.RL / 3;
    return [geo.at(a).slice(0, 2) as XY, ...track.ref.filter((_, k) => track.cum[k] > a && track.cum[k] < b), geo.at(b).slice(0, 2) as XY];
  }), [track, geo]);
  const start = geo.at(0);
  const arrowSize = 6 / scale;
  const forward: XY = [Math.cos(start[2]), -Math.sin(start[2])];
  const normal: XY = [-forward[1], forward[0]];
  const arrow = [
    [start[0] + forward[0] * arrowSize * 2, -start[1] + forward[1] * arrowSize * 2],
    [start[0] - forward[0] * arrowSize + normal[0] * arrowSize, -start[1] - forward[1] * arrowSize + normal[1] * arrowSize],
    [start[0] - forward[0] * arrowSize - normal[0] * arrowSize, -start[1] - forward[1] * arrowSize - normal[1] * arrowSize],
  ];
  const edges = track.edges ? { ...track.edges, width: roadWidth(track.edges) } : undefined;
  return <section className="layout-view panel" aria-label="Saved track layout">
    <div className="layout-view-tools">
      <div><strong>Saved layout</strong><span>{track.RL.toFixed(0)} m{edges ? ` · ${(edges.width / 0.3048).toFixed(1)} ft wide` : ""}</span></div>
      <label className="chk"><input type="checkbox" checked={showCentre} onChange={e => setShowCentre(e.target.checked)} />Sector centreline</label>
      <div className="layout-view-zoom"><button className="btn" type="button" aria-label="Zoom in on layout" onClick={() => changeZoom(1.5)}>+</button><button className="btn" type="button" aria-label="Zoom out of layout" onClick={() => changeZoom(1 / 1.5)}>−</button><button className="btn" type="button" onClick={fit}>Fit layout</button></div>
    </div>
    <svg ref={svg} role="img" tabIndex={0} aria-label={`${layoutName(track)}: saved centreline and calculated road edges. Drag or use arrow keys to pan, wheel, trackpad or plus and minus to zoom, Home to fit.`}
      viewBox={`${centre.x - width / 2} ${centre.y - height / 2} ${width} ${height}`} className="layout-view-map"
      onPointerDown={e => { if (e.button !== 0) return; e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId); drag.current = { x: e.clientX, y: e.clientY, cx: centre.x, cy: centre.y }; }}
      onPointerMove={e => { if (drag.current) setCentre({ x: drag.current.cx - (e.clientX - drag.current.x) / scale, y: drag.current.cy - (e.clientY - drag.current.y) / scale }); }}
      onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}
      onKeyDown={e => {
        if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) {
          setCentre(c => ({ x: c.x + (e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0) * width * 0.1, y: c.y + (e.key === "ArrowUp" ? -1 : e.key === "ArrowDown" ? 1 : 0) * height * 0.1 }));
        } else if (e.key === "+" || e.key === "=") changeZoom(1.5);
        else if (e.key === "-") changeZoom(1 / 1.5);
        else if (e.key === "Home") fit(); else return;
        e.preventDefault();
      }}>
      <title>{layoutName(track)}</title>
      <polyline points={points(edges?.centre ?? track.ref)} fill="none" stroke="var(--track)" strokeWidth={edges?.width ?? 3} strokeLinejoin="round" />
      {showCentre && sectors.map((path, i) => <polyline key={i} points={points(path)} fill="none" stroke={`var(--s${i + 1})`} strokeWidth="1.2" vectorEffect="non-scaling-stroke" />)}
      {edges && <g fill="none" stroke="var(--base)" strokeWidth="1" vectorEffect="non-scaling-stroke"><polyline points={points(edges.left)} vectorEffect="non-scaling-stroke" /><polyline points={points(edges.right)} vectorEffect="non-scaling-stroke" /></g>}
      <polygon points={arrow.map(p => p.join(",")).join(" ")} fill="var(--ink)" />
      <text x={start[0] + 12 / scale} y={-start[1] - 12 / scale} fontSize={12 / scale} fill="var(--ink)">S/F</text>
    </svg>
    <div className="layout-view-caption"><span>Estimated road edges · {track.from}</span><span>Drag to pan · Scroll to zoom</span></div>
  </section>;
}
