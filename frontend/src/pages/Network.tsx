import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Box, Search, Sparkles, Square, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { type ColorBy, Galaxy, type GalaxyHandle } from "../components/Galaxy";
import { Badge, RiskBadge, Segmented, Skeleton } from "../components/ui";
import { api, type Json } from "../lib/api";
import { fmtInt, fmtMoney } from "../lib/format";
import { useSize } from "../lib/hooks";
import { useUi } from "../lib/store";
import { useTokens } from "../lib/theme";

export default function NetworkPage() {
  const t = useTokens();
  const { cinematic, toggleCinematic } = useUi();
  const [mode, setMode] = useState<"3d" | "2d">("3d");
  const [colorBy, setColorBy] = useState<ColorBy>("risk");
  const [ringsOnly, setRingsOnly] = useState(false);
  const [showShared, setShowShared] = useState(true);
  const [minCount, setMinCount] = useState(3);
  const [selected, setSelected] = useState<Json | null>(null);
  const [query, setQuery] = useState("");
  const [box, size] = useSize<HTMLDivElement>();
  const galaxy = useRef<GalaxyHandle>(null);
  const { data, isLoading } = useQuery({ queryKey: ["galaxy", minCount], queryFn: () => api.get(`/api/graph/galaxy?min_count=${minCount}`) });

  const matches = useMemo(() => {
    if (!data || query.length < 2) return [];
    const q = query.toLowerCase();
    return data.nodes.filter((n: Json) => (n.name ?? "").toLowerCase().includes(q) || n.id.startsWith(q)).slice(0, 7);
  }, [data, query]);
  const stats = useMemo(() => data && {
    nodes: data.nodes.length, links: data.links.length,
    rings: new Set(data.nodes.filter((n: Json) => n.ring).map((n: Json) => n.ring)).size,
    ringEdges: data.links.filter((l: Json) => l.ring).length,
  }, [data]);

  return (
    <div className="relative h-full" ref={box}>
      {isLoading || !data ? (
        <div className="grid h-full place-items-center"><Skeleton className="size-72 rounded-full" /></div>
      ) : (
        size.width > 0 && (
          <Galaxy ref={galaxy} data={data} mode={mode} colorBy={colorBy} ringsOnly={ringsOnly} showShared={showShared}
            cinematic={cinematic} t={t} width={size.width} height={size.height} selected={selected?.id} onSelect={setSelected} />
        )
      )}

      <div className="pointer-events-none absolute inset-0 p-4 sm:p-5">
        <div className="pointer-events-auto flex max-w-[560px] flex-col gap-3">
          <div className="panel p-4">
            <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">Neo4j · referral network</div>
            <h1 className="mt-1 text-xl font-semibold text-ink">Provider referral galaxy</h1>
            <p className="mt-1 text-xs text-ink-2">
              Every provider is a node; every repeated referral relationship an edge (REFERS_TO, aggregated from per-claim REFERRED edges).
              Ring edges glow red with patients and money flowing around the loop.
            </p>
            {stats && (
              <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] text-muted">
                <span><b className="tnum text-ink">{fmtInt(stats.nodes)}</b> providers</span>
                <span><b className="tnum text-ink">{fmtInt(stats.links)}</b> edges ≥ {minCount} referrals</span>
                <span><b className="tnum text-critical">{stats.rings}</b> rings · {stats.ringEdges} ring edges</span>
              </div>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Segmented value={mode} onChange={setMode} size="sm" options={[{ value: "3d", label: <span className="flex items-center gap-1"><Box size={11} />3D</span> }, { value: "2d", label: <span className="flex items-center gap-1"><Square size={11} />2D</span> }]} />
              <Segmented value={colorBy} onChange={setColorBy} size="sm" options={[{ value: "risk", label: "Risk" }, { value: "kind", label: "Practice type" }]} />
              <Segmented value={String(minCount)} onChange={(v) => setMinCount(Number(v))} size="sm" options={[{ value: "1", label: "all" }, { value: "3", label: "≥3" }, { value: "8", label: "≥8" }]} />
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              <Toggle on={ringsOnly} onClick={() => setRingsOnly(!ringsOnly)}>Rings only</Toggle>
              <Toggle on={showShared} onClick={() => setShowShared(!showShared)}>Shared-patient links</Toggle>
              <Toggle on={cinematic} onClick={toggleCinematic}><Sparkles size={11} /> Cinematic</Toggle>
            </div>
          </div>

          <div className="panel relative">
            <div className="flex items-center gap-2 px-3">
              <Search size={15} className="text-muted" />
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Fly to a provider (name or NPI)…" className="h-10 w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted" />
            </div>
            {matches.length > 0 && (
              <ul className="border-t border-line p-1">
                {matches.map((n: Json) => (
                  <li key={n.id}>
                    <button onClick={() => { galaxy.current?.focus(n.id); setQuery(""); }} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs hover:bg-surface-2">
                      <span className="font-medium text-ink">{n.name}</span><span className="text-muted">{n.specialty} · {n.metro}</span>
                      {n.ring && <Badge tone="critical" className="ml-auto">{n.ring}</Badge>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="pointer-events-auto absolute bottom-5 left-5"><Legend colorBy={colorBy} /></div>

        <AnimatePresence>
          {selected && (
            <motion.div initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 24 }} className="pointer-events-auto absolute right-5 top-5 w-[320px]">
              <NodePanel node={selected} onClose={() => setSelected(null)} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

function Toggle({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className={`flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-medium transition ${on ? "border-transparent bg-accent-soft text-accent" : "border-line bg-surface-2 text-muted hover:text-ink-2"}`}>
      {children}
    </button>
  );
}

function Legend({ colorBy }: { colorBy: ColorBy }) {
  const items = colorBy === "risk"
    ? [["var(--critical)", "Ring member / critical"], ["var(--serious)", "High risk"], ["var(--warning)", "Elevated"], ["var(--neutral)", "Low risk"], ["var(--violet)", "Shared patients (no referral)"]]
    : [["var(--s-stat)", "Primary care"], ["var(--s-graph)", "Specialist"], ["var(--s-fused)", "Ancillary (lab, imaging, PT, DME)"]];
  return (
    <div className="panel space-y-1.5 px-3 py-2.5 text-[11px]">
      {items.map(([c, l]) => (
        <div key={l} className="flex items-center gap-2 text-ink-2"><span className="size-2.5 rounded-full" style={{ background: c }} />{l}</div>
      ))}
      <div className="pt-1 text-muted">Node size: weighted PageRank</div>
    </div>
  );
}

function NodePanel({ node, onClose }: { node: Json; onClose: () => void }) {
  const { data } = useQuery({ queryKey: ["provider", node.id, false], queryFn: () => api.get(`/api/providers/${node.id}`) });
  const s = data?.score;
  return (
    <div className="panel p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-[15px] font-semibold text-ink">{node.name}</div>
          <div className="text-xs text-muted">{node.specialty} · {node.metro} · NPI {node.id}</div>
        </div>
        <button onClick={onClose} className="text-muted hover:text-ink"><X size={16} /></button>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <span className="text-3xl font-semibold text-ink">{Math.round(node.score)}</span>
        <RiskBadge risk={node.risk} />
        {node.ring && <Badge tone="critical">{node.ring}</Badge>}
      </div>
      {s && (
        <>
          <ul className="mt-3 space-y-1.5 text-xs text-ink-2">
            {(s.reasons ?? []).slice(0, 4).map((r: string) => <li key={r} className="flex gap-1.5"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-accent" />{r}</li>)}
            {!s.reasons?.length && <li className="text-muted">No fraud signals fired.</li>}
          </ul>
          <div className="mt-3 grid grid-cols-3 gap-2 text-[11px]">
            <div><div className="tnum font-semibold text-ink">{fmtInt(s.claims)}</div><div className="text-muted">claims</div></div>
            <div><div className="tnum font-semibold text-ink">{fmtMoney(s.total_billed, true)}</div><div className="text-muted">billed</div></div>
            <div><div className="tnum font-semibold text-ink">{data.graph?.pagerank?.toFixed(2) ?? "—"}</div><div className="text-muted">PageRank</div></div>
          </div>
        </>
      )}
      <Link to={`/providers/${node.id}`} className="mt-4 flex items-center justify-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-white hover:brightness-110">
        Open dossier <ArrowRight size={13} />
      </Link>
    </div>
  );
}
