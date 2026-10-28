import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef } from "react";
import ForceGraph2D from "react-force-graph-2d";
import ForceGraph3D from "react-force-graph-3d";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import type { Json } from "../lib/api";
import { alpha, riskColor, type Tokens } from "../lib/theme";

export type ColorBy = "risk" | "kind";
export interface GalaxyHandle {
  focus: (id: string) => void;
}

interface Props {
  data: { nodes: Json[]; links: Json[]; shared: Json[] };
  mode: "3d" | "2d";
  colorBy: ColorBy;
  ringsOnly: boolean;
  showShared: boolean;
  cinematic: boolean;
  t: Tokens;
  width: number;
  height: number;
  selected?: string | null;
  onSelect: (node: Json | null) => void;
}

export const Galaxy = forwardRef<GalaxyHandle, Props>(function Galaxy(
  { data, mode, colorBy, ringsOnly, showShared, cinematic, t, width, height, selected, onSelect }, ref,
) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fg = useRef<any>(null);

  const graph = useMemo(() => {
    const ringNodes = new Set(data.nodes.filter((n) => n.ring).map((n) => n.id));
    const keep = (id: string) => !ringsOnly || ringNodes.has(id);
    const nodes = data.nodes.filter((n) => keep(n.id)).map((n) => ({ ...n }));
    const ids = new Set(nodes.map((n) => n.id));
    const links = data.links
      .filter((l) => ids.has(l.source) && ids.has(l.target) && (!ringsOnly || l.ring))
      .map((l) => ({ ...l, kind: "ref" }));
    if (showShared) {
      for (const s of data.shared) if (ids.has(s.source) && ids.has(s.target)) links.push({ ...s, kind: "shared" });
    }
    return { nodes, links };
  }, [data, ringsOnly, showShared]);

  const kindColor = (k: string) => (k === "primary" ? t.stat : k === "specialist" ? t.graph : t.fused);
  const nodeColor = (n: Json) => {
    if (n.id === selected) return t.accent;
    if (colorBy === "kind") return kindColor(n.kind);
    if (n.ring) return t.critical;
    return n.risk === "low" ? (t.dark ? "#4a4945" : "#a9a79f") : riskColor(t, n.risk);
  };
  const linkColor = (l: Json) => {
    if (l.kind === "shared") return alpha(t.violet, 0.7);
    if (l.ring) return t.critical;
    return alpha(t.dark ? "#c3c2b7" : "#52514e", t.dark ? 0.06 : 0.1);
  };

  useImperativeHandle(ref, () => ({
    focus: (id: string) => {
      const node = graph.nodes.find((n) => n.id === id) as Json;
      if (!node || !fg.current) return;
      if (mode === "3d") {
        const d = 120;
        const r = 1 + d / Math.hypot(node.x || 1, node.y || 1, node.z || 1);
        fg.current.cameraPosition({ x: (node.x || 0) * r, y: (node.y || 0) * r, z: (node.z || 0) * r }, node, 1600);
      } else {
        fg.current.centerAt(node.x, node.y, 900);
        fg.current.zoom(4, 900);
      }
      onSelect(node);
    },
  }));

  // Bloom only in cinematic 3D: it is the expensive part on an integrated GPU.
  useEffect(() => {
    if (mode !== "3d" || !fg.current) return;
    const composer = fg.current.postProcessingComposer?.();
    if (!composer) return;
    const passes = composer.passes as Json[];
    for (let i = passes.length - 1; i >= 1; i--) composer.removePass(passes[i]);
    // A high threshold lets only bright risk colours bloom; the grey low-risk mass stays crisp.
    if (cinematic && t.dark) composer.addPass(new UnrealBloomPass(undefined as never, 0.85, 0.4, 0.42));
  }, [cinematic, mode, t.dark, graph]);

  useEffect(() => {
    if (!fg.current) return;
    fg.current.d3Force("charge")?.strength(mode === "3d" ? -38 : -26);
    fg.current.d3Force("link")?.distance((l: Json) => (l.ring ? 18 : 42));
  }, [mode, graph]);

  const common = {
    graphData: graph,
    width,
    height,
    backgroundColor: "rgba(0,0,0,0)",
    nodeId: "id",
    nodeLabel: (n: Json) =>
      `<div style="font:12px Inter Variable,system-ui;padding:6px 8px;background:${t.surface};color:${t.ink};border:1px solid ${t.border};border-radius:8px">
        <b>${n.name ?? n.id}</b><br/><span style="color:${t.muted}">${n.specialty} · ${n.metro}</span><br/>
        score <b>${Math.round(n.score)}</b>${n.ring ? ` · <span style="color:${t.critical}">${n.ring}</span>` : ""}</div>`,
    nodeVal: (n: Json) => 0.5 + Math.min(10, n.pagerank * 2.4) + (n.ring ? 7 : 0) + (n.score >= 50 ? 3 : 0),
    nodeColor,
    linkColor,
    linkDirectionalParticles: (l: Json) => (l.ring && cinematic ? 3 : 0),
    linkDirectionalParticleSpeed: (l: Json) => 0.004 + Math.min(0.01, (l.count || 1) / 4000),
    linkDirectionalParticleWidth: mode === "3d" ? 2.2 : 2.6,
    linkDirectionalParticleColor: () => (t.dark ? "#ffd0c4" : t.critical),
    onNodeClick: (n: Json) => onSelect(n),
    onBackgroundClick: () => onSelect(null),
    cooldownTicks: 220,
    warmupTicks: 40,
    d3AlphaDecay: 0.03,
    d3VelocityDecay: 0.35,
    enableNodeDrag: false,
  };

  if (mode === "3d") {
    return (
      <ForceGraph3D
        ref={fg}
        {...common}
        nodeOpacity={0.92}
        nodeResolution={10}
        linkOpacity={1}
        linkWidth={(l: Json) => (l.ring ? 1.4 : 0)}
        linkDirectionalArrowLength={(l: Json) => (l.ring ? 3.5 : 0)}
        linkDirectionalArrowRelPos={0.92}
        linkDirectionalArrowColor={() => t.critical}
        showNavInfo={false}
      />
    );
  }
  return (
    <ForceGraph2D
      ref={fg}
      {...common}
      linkWidth={(l: Json) => (l.ring ? 1.6 : l.kind === "shared" ? 1.2 : 0.5)}
      linkLineDash={(l: Json) => (l.kind === "shared" ? [3, 3] : null)}
      linkDirectionalArrowLength={(l: Json) => (l.ring ? 4 : 0)}
      linkDirectionalArrowRelPos={0.9}
      nodeRelSize={3.2}
      nodeCanvasObjectMode={() => "after"}
      nodeCanvasObject={(n: Json, ctx: CanvasRenderingContext2D, scale: number) => {
        if (n.ring && cinematic) {
          ctx.beginPath();
          ctx.arc(n.x, n.y, 7, 0, Math.PI * 2);
          ctx.fillStyle = alpha(t.critical, 0.18);
          ctx.fill();
        }
        if (scale > 3.2 && (n.ring || n.score >= 65)) {
          ctx.font = `${10 / scale * 1.4}px Inter Variable, system-ui`;
          ctx.fillStyle = t.ink2;
          ctx.textAlign = "center";
          ctx.fillText(n.name ?? n.id, n.x, n.y + 9);
        }
      }}
    />
  );
});

/** Small 2D ego network for a provider dossier. */
export function EgoGraph({ data, t, width, height, onSelect }: {
  data: { nodes: Json[]; links: Json[] }; t: Tokens; width: number; height: number; onSelect: (id: string) => void;
}) {
  const graph = useMemo(() => ({ nodes: data.nodes.map((n) => ({ ...n })), links: data.links.map((l) => ({ ...l })) }), [data]);
  const maxCount = Math.max(1, ...data.links.map((l) => l.count || 1));
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fg = useRef<any>(null);
  useEffect(() => {
    fg.current?.d3Force("charge")?.strength(-220);
    fg.current?.d3Force("link")?.distance(70);
  }, [graph]);
  return (
    <ForceGraph2D
      ref={fg}
      onEngineStop={() => fg.current?.zoomToFit(500, 40)}
      graphData={graph}
      width={width}
      height={height}
      backgroundColor="rgba(0,0,0,0)"
      nodeRelSize={4}
      linkColor={(l: Json) => (l.kind === "shared" ? alpha(t.violet, 0.8) : l.source?.ring_id && l.source?.ring_id === l.target?.ring_id ? t.critical : alpha(t.ink2, 0.35))}
      linkWidth={(l: Json) => 0.6 + ((l.count || 1) / maxCount) * 3.5}
      linkLineDash={(l: Json) => (l.kind === "shared" ? [4, 3] : null)}
      linkDirectionalArrowLength={(l: Json) => (l.kind === "shared" ? 0 : 5)}
      linkDirectionalArrowRelPos={0.88}
      linkDirectionalParticles={(l: Json) => (l.kind === "shared" ? 0 : 2)}
      linkDirectionalParticleWidth={2}
      linkDirectionalParticleColor={() => t.accent}
      cooldownTicks={150}
      onNodeClick={(n: Json) => onSelect(n.id)}
      nodeLabel={(n: Json) => `${n.name ?? n.id} · ${n.specialty ?? ""} · score ${Math.round(n.score ?? 0)}`}
      nodeCanvasObject={(n: Json, ctx: CanvasRenderingContext2D, scale: number) => {
        const r = n.center ? 9 : 5.5;
        const col = n.center ? t.accent : n.ring_id ? t.critical : n.score >= 50 ? t.warning : t.neutral;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 3, 0, Math.PI * 2);
        ctx.fillStyle = alpha(col, 0.18);
        ctx.fill();
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fillStyle = col;
        ctx.fill();
        // Label the centre, ring members and risky neighbours; the rest stay hover-only.
        if (n.center || n.ring_id || n.score >= 50 || scale > 2.2) {
          const fs = Math.max(3, 11 / scale);
          ctx.font = `${n.center ? 600 : 400} ${fs}px Inter Variable, system-ui`;
          ctx.fillStyle = n.center ? t.ink : t.ink2;
          ctx.textAlign = "center";
          ctx.fillText((n.name ?? n.id).replace("Dr. ", ""), n.x, n.y + r + fs + 1);
        }
      }}
    />
  );
}
