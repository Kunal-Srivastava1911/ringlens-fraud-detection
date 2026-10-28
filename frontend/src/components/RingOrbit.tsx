import { useEffect, useRef } from "react";
import { alpha, scoreColor, useTokens } from "../lib/theme";

export interface OrbitMember {
  npi: string;
  name?: string;
  specialty?: string;
  score?: number;
}
export interface OrbitFlow {
  source: string;
  target: string;
  count: number;
  amount?: number;
}

/**
 * Members sit on a circle in loop order; each referral pair is a curved arrow whose width
 * follows its volume, and particles stream along it at a rate proportional to the count:
 * patients and money visibly circulating. Drawn on a canvas for speed.
 */
export function RingOrbit({ members, flows, hub, height = 360, compact = false, onSelect }: {
  members: OrbitMember[];
  flows: OrbitFlow[];
  hub?: string;
  height?: number;
  compact?: boolean;
  onSelect?: (npi: string) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const t = useTokens();
  const hit = useRef<{ npi: string; x: number; y: number; r: number }[]>([]);
  const hover = useRef<string | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current!;
    const ctx = canvas.getContext("2d")!;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0;
    let width = 0;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const maxCount = Math.max(1, ...flows.map((f) => f.count));
    // Particles: each flow gets a pool proportional to its volume.
    const particles = flows.flatMap((f, fi) => {
      const n = compact ? Math.max(1, Math.round((f.count / maxCount) * 4)) : Math.max(2, Math.round((f.count / maxCount) * 10));
      return Array.from({ length: n }, (_, i) => ({ fi, p: i / n, speed: 0.0025 + Math.random() * 0.002 }));
    });

    const resize = () => {
      width = canvas.parentElement!.clientWidth;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas.parentElement!);

    const positions = () => {
      const cx = width / 2;
      const cy = height / 2;
      const R = Math.min(width, height) / 2 - (compact ? 26 : 70);
      const pos = new Map<string, { x: number; y: number }>();
      members.forEach((m, i) => {
        const a = -Math.PI / 2 + (i / members.length) * Math.PI * 2;
        pos.set(m.npi, { x: cx + R * Math.cos(a), y: cy + R * Math.sin(a) });
      });
      return { pos, cx, cy, R };
    };

    const curve = (a: { x: number; y: number }, b: { x: number; y: number }, cx: number, cy: number) => {
      // Bow each edge outward from the centre, so A->B and B->A separate.
      const mx = (a.x + b.x) / 2;
      const my = (a.y + b.y) / 2;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const len = Math.hypot(dx, dy) || 1;
      let nx = -dy / len;
      let ny = dx / len;
      if ((mx - cx) * nx + (my - cy) * ny < 0) {
        nx = -nx;
        ny = -ny;
      }
      const bow = len * 0.18;
      return { c: { x: mx + nx * bow, y: my + ny * bow } };
    };
    const at = (a: { x: number; y: number }, c: { x: number; y: number }, b: { x: number; y: number }, p: number) => ({
      x: (1 - p) * (1 - p) * a.x + 2 * (1 - p) * p * c.x + p * p * b.x,
      y: (1 - p) * (1 - p) * a.y + 2 * (1 - p) * p * c.y + p * p * b.y,
    });

    const draw = () => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      const { pos, cx, cy, R } = positions();

      // Faint orbit guide.
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.strokeStyle = alpha(t.critical, t.dark ? 0.12 : 0.1);
      ctx.setLineDash([3, 6]);
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.setLineDash([]);

      const geom = flows.map((f) => {
        const a = pos.get(f.source);
        const b = pos.get(f.target);
        if (!a || !b) return null;
        return { a, b, ...curve(a, b, cx, cy), w: 0.8 + (f.count / maxCount) * (compact ? 2.5 : 5) };
      });
      geom.forEach((g, i) => {
        if (!g) return;
        const focus = hover.current && (flows[i].source === hover.current || flows[i].target === hover.current);
        ctx.beginPath();
        ctx.moveTo(g.a.x, g.a.y);
        ctx.quadraticCurveTo(g.c.x, g.c.y, g.b.x, g.b.y);
        ctx.strokeStyle = alpha(t.critical, focus ? 0.85 : hover.current ? 0.12 : 0.38);
        ctx.lineWidth = g.w;
        ctx.stroke();
        // Arrowhead just before the target node.
        const tip = at(g.a, g.c, g.b, 0.86);
        const prev = at(g.a, g.c, g.b, 0.8);
        const ang = Math.atan2(tip.y - prev.y, tip.x - prev.x);
        const s = compact ? 5 : 8;
        ctx.beginPath();
        ctx.moveTo(tip.x, tip.y);
        ctx.lineTo(tip.x - s * Math.cos(ang - 0.45), tip.y - s * Math.sin(ang - 0.45));
        ctx.lineTo(tip.x - s * Math.cos(ang + 0.45), tip.y - s * Math.sin(ang + 0.45));
        ctx.closePath();
        ctx.fillStyle = alpha(t.critical, focus ? 0.95 : 0.55);
        ctx.fill();
      });

      if (!reduced) {
        for (const pt of particles) {
          const g = geom[pt.fi];
          if (!g) continue;
          pt.p = (pt.p + pt.speed) % 1;
          const q = at(g.a, g.c, g.b, pt.p);
          ctx.beginPath();
          ctx.arc(q.x, q.y, compact ? 1.6 : 2.4, 0, Math.PI * 2);
          ctx.fillStyle = t.dark ? "#ffd0c4" : t.critical;
          ctx.shadowColor = t.critical;
          ctx.shadowBlur = compact ? 6 : 12;
          ctx.fill();
          ctx.shadowBlur = 0;
        }
      }

      hit.current = [];
      members.forEach((m) => {
        const p = pos.get(m.npi)!;
        const isHub = m.npi === hub;
        const r = (compact ? 7 : 13) * (isHub ? 1.35 : 1);
        const col = scoreColor(t, m.score ?? 0);
        hit.current.push({ npi: m.npi, x: p.x, y: p.y, r: r + 6 });
        ctx.beginPath();
        ctx.arc(p.x, p.y, r + (isHub ? 7 : 4), 0, Math.PI * 2);
        ctx.fillStyle = alpha(col, isHub ? 0.22 : 0.14);
        ctx.fill();
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.fillStyle = t.surface;
        ctx.fill();
        ctx.lineWidth = isHub ? 3 : 2;
        ctx.strokeStyle = col;
        ctx.stroke();
        ctx.fillStyle = col;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r * 0.45, 0, Math.PI * 2);
        ctx.fill();
        if (!compact) {
          const dx = p.x - cx;
          const dy = p.y - cy;
          const d = Math.hypot(dx, dy) || 1;
          const lx = p.x + (dx / d) * (r + 16);
          const ly = p.y + (dy / d) * (r + 16);
          ctx.textAlign = Math.abs(dx) < 20 ? "center" : dx > 0 ? "left" : "right";
          ctx.textBaseline = "middle";
          ctx.font = "600 12px Inter Variable, system-ui, sans-serif";
          ctx.fillStyle = t.ink;
          ctx.fillText((m.name ?? m.npi) + (isHub ? "  ★ hub" : ""), lx, ly - 7);
          ctx.font = "11px Inter Variable, system-ui, sans-serif";
          ctx.fillStyle = t.muted;
          ctx.fillText(`${m.specialty ?? ""} · score ${Math.round(m.score ?? 0)}`, lx, ly + 8);
        }
      });
      if (!reduced) raf = requestAnimationFrame(draw);
    };
    draw();

    const pick = (e: MouseEvent) => {
      const r = canvas.getBoundingClientRect();
      const x = e.clientX - r.left;
      const y = e.clientY - r.top;
      return hit.current.find((h) => Math.hypot(h.x - x, h.y - y) < h.r)?.npi ?? null;
    };
    const onMove = (e: MouseEvent) => {
      hover.current = pick(e);
      canvas.style.cursor = hover.current && onSelect ? "pointer" : "default";
      if (reduced) draw();
    };
    const onClick = (e: MouseEvent) => {
      const n = pick(e);
      if (n && onSelect) onSelect(n);
    };
    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("click", onClick);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      canvas.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("click", onClick);
    };
  }, [members, flows, hub, height, compact, t, onSelect]);

  return (
    <div className="relative w-full" style={{ height }}>
      <canvas ref={canvasRef} className="absolute inset-0" aria-label="Referral ring diagram" role="img" />
    </div>
  );
}
