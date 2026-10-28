import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { CheckCircle2, RotateCcw, XCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { axisStyle, Chart, legendStyle } from "../components/Chart";
import { Badge, Button, Empty, PageHeader, Panel, Segmented, Skeleton } from "../components/ui";
import { api, type Json } from "../lib/api";
import { FRAUD_TYPE_LABELS, GRAPH_SIGNALS, SIGNAL_LABELS } from "../lib/format";
import type { Tokens } from "../lib/theme";

const VARIANTS = [
  { key: "statistical", label: "Statistical only", sub: "MongoDB aggregation signals", tone: "stat" },
  { key: "graph", label: "Graph only", sub: "Neo4j GDS signals", tone: "graph" },
  { key: "fused", label: "Fused", sub: "noisy-OR over both", tone: "fused" },
] as const;

export default function Lab() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["evaluation"], queryFn: () => api.get("/api/evaluation") });
  const [weights, setWeights] = useState<Record<string, number> | null>(null);
  const [mode, setMode] = useState("noisy_or");
  const [threshold, setThreshold] = useState(50);
  const timer = useRef<number>(undefined);

  useEffect(() => {
    if (data && !weights) {
      setWeights(data.config.weights);
      setMode(data.config.mode);
      setThreshold(data.config.threshold);
    }
  }, [data, weights]);

  const save = useMutation({
    mutationFn: (body: Json) => api.put("/api/scoring/config", body),
    onSuccess: (r) => { qc.setQueryData(["evaluation"], r); qc.invalidateQueries({ queryKey: ["overview"] }); qc.invalidateQueries({ queryKey: ["providers"] }); },
    onError: (e: Error) => toast.error(e.message),
  });
  // The API re-scores all providers and re-evaluates in well under a second; debounce the sliders.
  const push = (body: Json) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => save.mutate(body), 350);
  };

  if (isLoading || !data) return <Skeleton className="h-[700px]" />;
  const ev = data.evaluation;
  if (!ev) return <Empty>No evaluation yet. Run analytics first.</Empty>;

  return (
    <div className="space-y-4">
      <PageHeader eyebrow="Ground truth · planted fraud" title="Evaluation Lab"
        subtitle={`Detections are scored against fraud deliberately planted by the generator (seed ${ev.dataset?.seed}, ${ev.dataset?.difficulty} difficulty): ${ev.fraudulent_providers} fraudulent providers among ${ev.providers}. Neither store alone is enough; this page measures by how much.`}
        actions={save.isPending ? <Badge tone="accent">re-scoring…</Badge> : <Badge>config v{data.config.version} · {data.config.mode.replace("_", "-")}</Badge>} />

      <div className="grid gap-3 md:grid-cols-3">
        {VARIANTS.map((v) => {
          const m = ev.variants[v.key];
          return (
            <div key={v.key} className={clsx("panel p-4", v.key === "fused" && "ring-1 ring-[color-mix(in_srgb,var(--s-fused)_50%,transparent)]")}>
              <div className="flex items-center justify-between">
                <div><div className={`text-sm font-semibold text-${v.tone}`}>{v.label}</div><div className="text-[11px] text-muted">{v.sub}</div></div>
                <div className="text-right"><div className="tnum text-2xl font-semibold text-ink">{m.f1.toFixed(2)}</div><div className="text-[10.5px] text-muted">F1 @ {ev.threshold}</div></div>
              </div>
              <div className="mt-3 grid grid-cols-4 gap-2 text-[11px]">
                <Metric label="precision" v={m.precision} /><Metric label="recall" v={m.recall} /><Metric label="avg precision" v={m.average_precision} /><Metric label="precision@25" v={m.precision_at["25"]} />
              </div>
              <div className="mt-2 text-[11px] text-muted">TP {m.tp} · FP {m.fp} · FN {m.fn}</div>
            </div>
          );
        })}
      </div>

      <div className="grid gap-4 xl:grid-cols-5">
        <Panel className="xl:col-span-3" title="Precision–recall curves" subtitle="Sweeping the score threshold from 0 to 100; the dot marks the operating threshold">
          <PrCurves variants={ev.variants} threshold={ev.threshold} />
        </Panel>
        <Panel className="xl:col-span-2" title="Tune the fusion" subtitle="Weights are the most each signal can contribute alone. Changes re-score every provider and re-evaluate live."
          actions={<Button size="sm" variant="ghost" onClick={() => { setWeights(data.defaults); setMode("noisy_or"); setThreshold(50); save.mutate({ reset: true }); }}><RotateCcw size={12} />Reset</Button>}>
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <Segmented value={mode} size="sm" onChange={(m) => { setMode(m); push({ mode: m }); }} options={[{ value: "noisy_or", label: "Noisy-OR" }, { value: "weighted_sum", label: "Weighted sum" }]} />
            <label className="flex items-center gap-2 text-xs text-ink-2">threshold
              <input type="range" min={20} max={90} value={threshold} onChange={(e) => { const v = Number(e.target.value); setThreshold(v); push({ threshold: v }); }} className="w-24 accent-[var(--accent)]" />
              <span className="tnum w-6 font-semibold text-ink">{threshold}</span>
            </label>
          </div>
          <div className="space-y-2">
            {weights && Object.keys(SIGNAL_LABELS).map((k) => (
              <div key={k} className="grid grid-cols-[150px_1fr_36px] items-center gap-2 text-xs">
                <span className="flex items-center gap-1.5 text-ink-2"><span className={clsx("size-2 rounded-sm", GRAPH_SIGNALS.has(k) ? "bg-graph" : "bg-stat")} />{SIGNAL_LABELS[k]}</span>
                <input type="range" min={0} max={0.99} step={0.01} value={weights[k]} onChange={(e) => { const next = { ...weights, [k]: Number(e.target.value) }; setWeights(next); push({ weights: next }); }}
                  className={clsx("w-full", GRAPH_SIGNALS.has(k) ? "accent-[var(--s-graph)]" : "accent-[var(--s-stat)]")} />
                <span className="tnum text-right font-semibold text-ink">{weights[k].toFixed(2)}</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Recall by planted fraud type" subtitle="Which store catches which scheme (at the operating threshold)">
          <ByType byType={ev.by_type} />
        </Panel>
        <Panel title="Ablation" subtitle="Change in average precision when one signal is removed. Longer bars = more important.">
          <Ablation rows={ev.ablation} />
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" title="Ring recovery" subtitle={`${ev.rings.recovered}/${ev.rings.planted} planted rings recovered (member Jaccard ≥ 0.5), ${ev.rings.exact} exactly; ${ev.rings.false_rings.length} detected rings match no planted ring`} pad={false}>
          <table className="w-full text-[13px]">
            <thead><tr className="border-b border-line text-left text-[11px] uppercase tracking-wider text-muted"><th className="px-5 py-2 font-medium">Planted</th><th className="py-2 font-medium">Size</th><th className="py-2 font-medium">Billing</th><th className="py-2 font-medium">Source</th><th className="py-2 font-medium">Matched</th><th className="py-2 font-medium">Jaccard</th><th className="px-5 py-2 font-medium" /></tr></thead>
            <tbody>
              {ev.rings.detail.map((r: Json) => (
                <tr key={r.ring_id} className="border-b border-line/50">
                  <td className="px-5 py-2 font-mono text-xs text-ink">{r.ring_id}</td><td className="tnum py-2">{r.size}</td>
                  <td className="py-2">{r.camouflaged ? <Badge>camouflaged</Badge> : <Badge tone="critical">inflated</Badge>}</td>
                  <td className="py-2 text-xs text-ink-2">{r.source}</td><td className="py-2 font-mono text-xs text-ink-2">{r.matched ?? "—"}</td>
                  <td className="tnum py-2">{r.jaccard.toFixed(2)}</td>
                  <td className="px-5 py-2">{r.recovered ? <CheckCircle2 size={15} className="text-good-ink" /> : <XCircle size={15} className="text-critical" />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel title="Claim-level flags" subtitle="Rules written back to claims (partial index)">
          <table className="w-full text-xs">
            <thead><tr className="text-left text-[10.5px] uppercase tracking-wider text-muted"><th className="py-1.5 font-medium">Flag</th><th className="text-right font-medium">Flagged</th><th className="text-right font-medium">Precision</th><th className="text-right font-medium">Recall</th></tr></thead>
            <tbody>
              {Object.entries(ev.claims).map(([k, v]: [string, Json]) => (
                <tr key={k} className="border-t border-line/60"><td className="py-2 text-ink">{k.replace("_", " ")}</td><td className="tnum text-right text-ink-2">{v.flagged}</td>
                  <td className="tnum text-right text-ink">{v.precision != null ? v.precision.toFixed(2) : "—"}</td><td className="tnum text-right text-ink">{v.recall != null ? v.recall.toFixed(2) : "—"}</td></tr>
              ))}
            </tbody>
          </table>
          <p className="mt-3 text-[11px] text-muted">Upcoding recall is low by design: the flag marks only the 99215 lines of providers whose E/M mix is ≥ 2.5 SD above peers.</p>
        </Panel>
      </div>
      <Bench bench={data.bench} />
    </div>
  );
}

function Metric({ label, v }: { label: string; v: number }) {
  return <div><div className="tnum font-semibold text-ink">{v.toFixed(2)}</div><div className="text-muted">{label}</div></div>;
}

function PrCurves({ variants, threshold }: { variants: Json; threshold: number }) {
  const option = useCallback((t: Tokens) => {
    const color: Record<string, string> = { statistical: t.stat, graph: t.graph, fused: t.fused };
    return {
      grid: { left: 46, right: 16, top: 34, bottom: 40 },
      legend: { ...legendStyle(t), top: 0, left: 0 },
      tooltip: { trigger: "item" as const, formatter: (p: Json) => `${p.seriesName}<br/>threshold ${p.data[2]}: precision <b>${p.data[1]}</b>, recall <b>${p.data[0]}</b>` },
      xAxis: { type: "value" as const, min: 0, max: 1, name: "Recall", nameLocation: "middle" as const, nameGap: 26, ...axisStyle(t) },
      yAxis: { type: "value" as const, min: 0, max: 1, name: "Precision", nameLocation: "middle" as const, nameGap: 32, ...axisStyle(t) },
      series: VARIANTS.map((v) => {
        const pts = [...variants[v.key].pr_curve].sort((a: Json, b: Json) => a.recall - b.recall || b.precision - a.precision);
        const op = variants[v.key].pr_curve.find((p: Json) => p.threshold === Math.round(threshold / 2) * 2);
        return {
          name: `${v.label} · AP ${variants[v.key].average_precision.toFixed(2)}`, type: "line" as const, step: "end" as const, symbol: "none",
          data: pts.map((p: Json) => [p.recall, p.precision, p.threshold]), lineStyle: { width: 2.2, color: color[v.key] }, itemStyle: { color: color[v.key] },
          markPoint: op ? { symbol: "circle", symbolSize: 10, itemStyle: { color: color[v.key], borderColor: t.surface, borderWidth: 2 }, label: { show: false }, data: [{ coord: [op.recall, op.precision] }] } : undefined,
        };
      }),
    };
  }, [variants, threshold]);
  return <Chart option={option} height={340} />;
}

function ByType({ byType }: { byType: Json }) {
  const types = Object.keys(byType);
  const option = useCallback((t: Tokens) => ({
    grid: { left: 118, right: 16, top: 30, bottom: 24 },
    legend: { ...legendStyle(t), top: 0, left: 0 },
    tooltip: { trigger: "axis" as const, axisPointer: { type: "shadow" as const }, valueFormatter: (v: number) => `${Math.round(v * 100)}%` },
    xAxis: { type: "value" as const, max: 1, ...axisStyle(t), axisLabel: { ...axisStyle(t).axisLabel, formatter: (v: number) => `${v * 100}%` } },
    yAxis: { type: "category" as const, data: types.map((k) => `${FRAUD_TYPE_LABELS[k] ?? k} (${byType[k].planted})`), ...axisStyle(t), axisLabel: { color: t.ink2, fontSize: 11 } },
    series: [
      { name: "Statistical only", type: "bar" as const, data: types.map((k) => byType[k].stat_detected / byType[k].planted), itemStyle: { color: t.stat, borderRadius: [0, 3, 3, 0] }, barGap: "15%", barWidth: 6 },
      { name: "Graph only", type: "bar" as const, data: types.map((k) => byType[k].graph_detected / byType[k].planted), itemStyle: { color: t.graph, borderRadius: [0, 3, 3, 0] }, barWidth: 6 },
      { name: "Fused", type: "bar" as const, data: types.map((k) => byType[k].recall), itemStyle: { color: t.fused, borderRadius: [0, 3, 3, 0] }, barWidth: 6 },
    ],
  }), [byType, types]);
  return <Chart option={option} height={300} />;
}

function Ablation({ rows }: { rows: Json[] }) {
  const option = useCallback((t: Tokens) => ({
    grid: { left: 150, right: 50, top: 8, bottom: 24 },
    tooltip: { trigger: "axis" as const, axisPointer: { type: "shadow" as const }, valueFormatter: (v: number) => v.toFixed(4) },
    xAxis: { type: "value" as const, ...axisStyle(t), name: "Δ average precision", nameLocation: "middle" as const, nameGap: 22 },
    yAxis: { type: "category" as const, inverse: true, data: rows.map((r) => SIGNAL_LABELS[r.signal] ?? r.signal), ...axisStyle(t), axisLabel: { color: t.ink2, fontSize: 11 } },
    series: [{ type: "bar" as const, barWidth: 12, data: rows.map((r) => ({ value: r.delta, itemStyle: { color: r.group === "graph" ? t.graph : t.stat, borderRadius: 3 } })),
      label: { show: true, position: "right" as const, color: t.ink2, fontSize: 10.5, formatter: (p: Json) => Number(p.value).toFixed(3) } }],
  }), [rows]);
  return <Chart option={option} height={300} />;
}

function Bench({ bench }: { bench: Json[] }) {
  if (!bench?.length) {
    return (
      <Panel title="Held-out seeds" subtitle="Numbers above come from one dataset. Run `ringlens bench --seeds 101-105` to evaluate on fresh datasets the thresholds were never tuned on.">
        <p className="text-xs text-muted">No benchmark has been run yet.</p>
      </Panel>
    );
  }
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      {bench.map((b) => (
        <Panel key={b.difficulty} title={`Held-out seeds ${b.seeds[0]}–${b.seeds[b.seeds.length - 1]} · ${b.difficulty} difficulty`}
          subtitle={`Mean ± standard deviation over ${b.seeds.length} fresh datasets the thresholds were never tuned on · rings ${b.summary.rings.recovered}/${b.summary.rings.planted} recovered, ${b.summary.rings.false_rings} false`} pad={false}>
          <table className="w-full text-[13px]">
            <thead><tr className="border-b border-line text-left text-[11px] uppercase tracking-wider text-muted"><th className="px-5 py-2 font-medium">Detector</th><th className="py-2 font-medium">Precision</th><th className="py-2 font-medium">Recall</th><th className="py-2 font-medium">F1</th><th className="px-5 py-2 font-medium">Avg precision</th></tr></thead>
            <tbody>
              {VARIANTS.map((v) => {
                const s = b.summary[v.key];
                const f = (m: Json) => `${m.mean.toFixed(3)} ± ${m.std.toFixed(3)}`;
                return (
                  <tr key={v.key} className="border-b border-line/50">
                    <td className={`px-5 py-2 font-medium text-${v.tone}`}>{v.label}</td>
                    <td className="tnum py-2">{f(s.precision)}</td><td className="tnum py-2">{f(s.recall)}</td><td className="tnum py-2 font-semibold text-ink">{f(s.f1)}</td><td className="tnum px-5 py-2">{f(s.average_precision)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Panel>
      ))}
    </div>
  );
}
