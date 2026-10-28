import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft, Landmark, MapPin } from "lucide-react";
import { motion } from "motion/react";
import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { axisStyle, Chart, legendStyle } from "../components/Chart";
import { EgoGraph } from "../components/Galaxy";
import { Badge, Button, Empty, Mono, PageHeader, Panel, RiskBadge, Segmented, Skeleton } from "../components/ui";
import { api, type Json, qs } from "../lib/api";
import { FRAUD_TYPE_LABELS, fmtDate, fmtDateTime, fmtInt, fmtMoney, fmtPct, GRAPH_SIGNALS, SIGNAL_LABELS } from "../lib/format";
import { useSize } from "../lib/hooks";
import { useUi } from "../lib/store";
import { scoreColor, type Tokens, useTokens } from "../lib/theme";

const EM = ["99211", "99212", "99213", "99214", "99215"];

export default function ProviderDetail() {
  const { npi } = useParams();
  const reveal = useUi((s) => s.reveal);
  const t = useTokens();
  const nav = useNavigate();
  const { data, isLoading } = useQuery({ queryKey: ["provider", npi, reveal], queryFn: () => api.get(`/api/providers/${npi}${qs({ reveal })}`) });
  const { data: network } = useQuery({ queryKey: ["network", npi], queryFn: () => api.get(`/api/providers/${npi}/network`) });
  const [egoBox, egoSize] = useSize<HTMLDivElement>();

  if (isLoading || !data) return <Skeleton className="h-[640px]" />;
  const p = data.provider;
  const s = data.score ?? { score: 0, risk: "low", signals: {}, attributions: {}, reasons: [] };
  const stat = data.statistical ?? {};
  const graph = data.graph ?? {};

  return (
    <div className="space-y-4">
      <Link to="/providers" className="inline-flex items-center gap-1 text-xs text-muted hover:text-ink"><ArrowLeft size={13} /> Providers</Link>
      <PageHeader
        eyebrow={`Provider dossier · NPI ${p._id}`}
        title={<span className="flex flex-wrap items-center gap-3">{p.name}<RiskBadge risk={s.risk} />{graph.ring_id && <Link to={`/rings/${graph.ring_id}`}><Badge tone="critical">{graph.ring_id}{graph.ring_hub ? " · hub" : ""}</Badge></Link>}</span>}
        subtitle={`${p.specialty} · ${p.address.line1}, ${p.address.city}, ${p.address.state} ${p.address.zip} · enrolled ${fmtDate(p.enrolled_on)}`}
        actions={reveal && (
          <div className="flex flex-wrap gap-1">
            {data.truth ? data.truth.fraud_types.map((x: string) => <Badge key={x} tone="graph">planted: {FRAUD_TYPE_LABELS[x] ?? x}</Badge>) : <Badge>ground truth: legitimate</Badge>}
          </div>
        )}
      />

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Fused fraud score" subtitle="noisy-OR over normalized signals">
          <div className="flex items-center gap-5">
            <Gauge score={s.score} color={scoreColor(t, s.score)} />
            <div className="space-y-2 text-xs">
              <Row label="Statistical only" value={Math.round(s.score_statistical ?? 0)} cls="text-stat" />
              <Row label="Graph only" value={Math.round(s.score_graph ?? 0)} cls="text-graph" />
              <Row label="Rank" value={`#${s.rank ?? "—"}`} />
              <Row label="Claims" value={fmtInt(s.claims)} />
              <Row label="Billed" value={fmtMoney(s.total_billed, true)} />
            </div>
          </div>
          <ul className="mt-4 space-y-2 border-t border-line pt-3">
            {s.reasons.length ? s.reasons.map((r: string) => (
              <li key={r} className="flex gap-2 text-[12.5px] text-ink-2"><span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-accent" />{r}</li>
            )) : <li className="text-xs text-muted">No fraud signals fired.</li>}
          </ul>
        </Panel>
        <Panel className="xl:col-span-2" title="Why this score" subtitle="Each signal's share of the score (log-space attribution). Blue signals come from MongoDB aggregations, orange from Neo4j graph algorithms.">
          <Attribution signals={s.signals} attributions={s.attributions} />
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Billing against peers" subtitle={`Monthly billed vs ${p.specialty} interquartile band ($percentile)`}>
          <BillingBand band={data.billing_band} />
        </Panel>
        <Panel title="Office-visit level mix" subtitle="Share of E/M lines by level vs specialty peers (upcoding shows as a shift to 99215)">
          <EmMix mix={data.em_mix} />
        </Panel>
        <Panel title="Peer position" subtitle={`Average claim among ${data.peers.avg_claim.length} ${p.specialty} providers`}>
          <PeerHistogram values={data.peers.avg_claim} mine={stat.avg_claim} />
          <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
            <div><div className="tnum font-semibold text-ink">{(stat.z_avg_claim ?? 0).toFixed(2)}</div><div className="text-muted">z avg claim</div></div>
            <div><div className="tnum font-semibold text-ink">{(stat.z_top_em ?? 0).toFixed(2)}</div><div className="text-muted">z top-level E/M</div></div>
            <div><div className="tnum font-semibold text-ink">{stat.max_velocity_spike ?? "—"}×</div><div className="text-muted">velocity spike</div></div>
          </div>
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" title="Referral neighbourhood" subtitle="Neo4j ego network: heaviest REFERS_TO edges in and out; dashed violet = shared patients without referral" pad={false}>
          <div ref={egoBox} className="h-[380px]">
            {network && egoSize.width > 0 ? (network.nodes.length > 1 ? <EgoGraph data={network} t={t} width={egoSize.width} height={egoSize.height} onSelect={(id) => id !== npi && nav(`/providers/${id}`)} /> : <Empty>No referral relationships.</Empty>) : <Skeleton className="m-4 h-[340px]" />}
          </div>
        </Panel>
        <div className="space-y-4">
          <Panel title="Graph signals">
            <div className="grid grid-cols-2 gap-3 text-xs">
              <Row label="PageRank" value={(graph.pagerank ?? 0).toFixed(3)} />
              <Row label="Betweenness z" value={(graph.betweenness_z ?? 0).toFixed(2)} />
              <Row label="Community" value={`${graph.community ?? "—"} (${graph.community_size ?? 0})`} />
              <Row label="Referrals out / in" value={`${fmtInt(graph.out_refs)} / ${fmtInt(graph.in_refs)}`} />
              <Row label="Max shared-patient Jaccard" value={(graph.shared_patients_jaccard ?? 0).toFixed(2)} />
              <Row label="Identity cluster" value={graph.identity_cluster ?? 1} />
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {(stat.shares_bank_with ?? []).length > 0 && <Badge tone="critical"><Landmark size={10} /> bank shared with {stat.shares_bank_with.length}</Badge>}
              {(stat.shares_address_with ?? []).length > 0 && <Badge><MapPin size={10} /> address shared with {stat.shares_address_with.length}</Badge>}
            </div>
          </Panel>
          <Panel title="Score history"><History points={data.history} /></Panel>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" title="Claims" pad={false}><ClaimTables flagged={data.flagged_claims} recent={data.recent_claims} /></Panel>
        <CaseCard npi={p._id} caseDoc={data.case} audit={data.audit} />
      </div>
    </div>
  );
}

function Row({ label, value, cls }: { label: string; value: React.ReactNode; cls?: string }) {
  return <div className="flex items-center justify-between gap-3"><span className="text-muted">{label}</span><span className={clsx("tnum font-semibold text-ink", cls)}>{value}</span></div>;
}

function Gauge({ score, color }: { score: number; color: string }) {
  const r = 52;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative size-[132px] shrink-0">
      <svg viewBox="0 0 132 132" className="-rotate-90">
        <circle cx="66" cy="66" r={r} fill="none" stroke="var(--surface-3)" strokeWidth="10" />
        <motion.circle cx="66" cy="66" r={r} fill="none" stroke={color} strokeWidth="10" strokeLinecap="round"
          strokeDasharray={c} initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c * (1 - score / 100) }} transition={{ duration: 1.2, ease: [0.16, 1, 0.3, 1] }}
          style={{ filter: `drop-shadow(0 0 8px ${color})` }} />
      </svg>
      <div className="absolute inset-0 grid place-items-center">
        <div className="text-center"><div className="tnum text-[34px] font-semibold leading-none text-ink">{Math.round(score)}</div><div className="mt-1 text-[10px] uppercase tracking-wider text-muted">of 100</div></div>
      </div>
    </div>
  );
}

function Attribution({ signals, attributions }: { signals: Json; attributions: Json }) {
  const keys = Object.keys(SIGNAL_LABELS).sort((a, b) => (attributions[b] ?? 0) - (attributions[a] ?? 0));
  const max = Math.max(1, ...keys.map((k) => attributions[k] ?? 0));
  return (
    <div className="space-y-2.5">
      {keys.map((k, i) => {
        const v = attributions[k] ?? 0;
        const g = GRAPH_SIGNALS.has(k);
        return (
          <div key={k} className="grid grid-cols-[170px_1fr_80px] items-center gap-3 text-xs">
            <span className="flex items-center gap-1.5 text-ink-2"><span className={clsx("size-2 rounded-sm", g ? "bg-graph" : "bg-stat")} />{SIGNAL_LABELS[k]}</span>
            <div className="h-5 rounded-md bg-surface-2">
              <motion.div className={clsx("h-full rounded-md", g ? "bg-graph" : "bg-stat")} initial={{ width: 0 }} animate={{ width: `${(v / max) * 100}%` }} transition={{ duration: 0.8, delay: i * 0.04 }} style={{ opacity: v > 0 ? 1 : 0 }} />
            </div>
            <span className="tnum text-right"><b className="text-ink">{v > 0 ? `+${v.toFixed(1)}` : "0"}</b> <span className="text-muted">s={(signals[k] ?? 0).toFixed(2)}</span></span>
          </div>
        );
      })}
    </div>
  );
}

function BillingBand({ band }: { band: Json[] }) {
  const option = useCallback((t: Tokens) => ({
    grid: { left: 52, right: 10, top: 28, bottom: 26 },
    legend: { ...legendStyle(t), top: 0, left: 0, data: ["Peer median", "This provider"] },
    tooltip: { trigger: "axis" as const, valueFormatter: (v: number) => fmtMoney(v) },
    xAxis: { type: "category" as const, data: band.map((b) => b.month), ...axisStyle(t), splitLine: { show: false }, axisLabel: { ...axisStyle(t).axisLabel, formatter: (v: string) => v.slice(2) } },
    yAxis: { type: "value" as const, ...axisStyle(t), axisLabel: { ...axisStyle(t).axisLabel, formatter: (v: number) => fmtMoney(v, true) } },
    series: [
      { name: "p25", type: "line" as const, data: band.map((b) => b.p25), stack: "band", lineStyle: { opacity: 0 }, symbol: "none", tooltip: { show: false } },
      { name: "Peer interquartile range", type: "line" as const, data: band.map((b) => b.p75 - b.p25), stack: "band", lineStyle: { opacity: 0 }, symbol: "none", areaStyle: { color: t.surface3 }, tooltip: { show: false } },
      { name: "Peer median", type: "line" as const, data: band.map((b) => b.p50), symbol: "none", lineStyle: { color: t.muted, width: 1.5, type: "dashed" as const }, itemStyle: { color: t.muted } },
      { name: "This provider", type: "line" as const, data: band.map((b) => b.provider), symbol: "circle", symbolSize: 6, lineStyle: { color: t.accent, width: 2 }, itemStyle: { color: t.accent } },
    ],
  }), [band]);
  return <Chart option={option} height={230} />;
}

function EmMix({ mix }: { mix: Json }) {
  const share = (o: Json) => { const total = EM.reduce((s, k) => s + (o[k] ?? 0), 0) || 1; return EM.map((k) => (o[k] ?? 0) / total); };
  const option = useCallback((t: Tokens) => ({
    grid: { left: 40, right: 8, top: 28, bottom: 24 },
    legend: { ...legendStyle(t), top: 0, left: 0 },
    tooltip: { trigger: "axis" as const, valueFormatter: (v: number) => fmtPct(v, 1) },
    xAxis: { type: "category" as const, data: EM, ...axisStyle(t), splitLine: { show: false } },
    yAxis: { type: "value" as const, ...axisStyle(t), axisLabel: { ...axisStyle(t).axisLabel, formatter: (v: number) => fmtPct(v) } },
    series: [
      { name: "Specialty peers", type: "bar" as const, data: share(mix.peers), itemStyle: { color: t.muted, borderRadius: [3, 3, 0, 0] }, barGap: "12%" },
      { name: "This provider", type: "bar" as const, data: share(mix.provider), itemStyle: { color: t.accent, borderRadius: [3, 3, 0, 0] } },
    ],
  }), [mix]);
  if (!Object.keys(mix.provider ?? {}).length) return <Empty>This provider bills no office visits.</Empty>;
  return <Chart option={option} height={230} />;
}

function PeerHistogram({ values, mine }: { values: number[]; mine?: number }) {
  const option = useCallback((t: Tokens) => {
    const v = values.filter((x) => x != null).map(Math.log10);
    const lo = Math.min(...v);
    const hi = Math.max(...v);
    const bins = 18;
    const w = (hi - lo) / bins || 1;
    const counts = Array(bins).fill(0);
    v.forEach((x) => counts[Math.min(bins - 1, Math.floor((x - lo) / w))]++);
    const mineBin = mine ? Math.min(bins - 1, Math.max(0, Math.floor((Math.log10(mine) - lo) / w))) : -1;
    return {
      grid: { left: 30, right: 8, top: 10, bottom: 26 },
      tooltip: { trigger: "axis" as const, formatter: (p: Json) => `${fmtMoney(10 ** (lo + p[0].dataIndex * w))}–${fmtMoney(10 ** (lo + (p[0].dataIndex + 1) * w))}: <b>${p[0].value}</b> providers` },
      xAxis: { type: "category" as const, data: counts.map((_, i) => fmtMoney(10 ** (lo + i * w), true)), ...axisStyle(t), splitLine: { show: false }, axisLabel: { ...axisStyle(t).axisLabel, interval: 4 } },
      yAxis: { type: "value" as const, ...axisStyle(t), minInterval: 1 },
      series: [{ type: "bar" as const, barCategoryGap: "8%", data: counts.map((c, i) => ({ value: c, itemStyle: { color: i === mineBin ? t.accent : t.surface3, borderRadius: [3, 3, 0, 0] } })),
        markPoint: mineBin >= 0 ? { symbol: "pin", symbolSize: 34, itemStyle: { color: t.accent }, label: { color: "#fff", fontSize: 9, formatter: "you" }, data: [{ coord: [mineBin, counts[mineBin]] }] } : undefined }],
    };
  }, [values, mine]);
  return <Chart option={option} height={170} />;
}

function History({ points }: { points: Json[] }) {
  const option = useCallback((t: Tokens) => ({
    grid: { left: 30, right: 8, top: 8, bottom: 20 },
    tooltip: { trigger: "axis" as const },
    xAxis: { type: "category" as const, data: points.map((p) => fmtDateTime(p.ts)), ...axisStyle(t), axisLabel: { show: false }, splitLine: { show: false } },
    yAxis: { type: "value" as const, min: 0, max: 100, ...axisStyle(t) },
    series: [{ name: "Fused score", type: "line" as const, data: points.map((p) => p.score), step: "end" as const, symbol: "circle", symbolSize: 5, lineStyle: { color: t.accent, width: 2 }, itemStyle: { color: t.accent } }],
  }), [points]);
  if (points.length < 2) return <p className="text-xs text-muted">One scoring run so far; the history fills in with each analytics run.</p>;
  return <Chart option={option} height={120} />;
}

function ClaimTables({ flagged, recent }: { flagged: Json[]; recent: Json[] }) {
  const [tab, setTab] = useState<"flagged" | "recent">(flagged.length ? "flagged" : "recent");
  const rows = tab === "flagged" ? flagged : recent;
  return (
    <>
      <div className="flex items-center justify-between px-5 pt-4">
        <Segmented value={tab} onChange={setTab} size="sm" options={[{ value: "flagged", label: `Flagged (${flagged.length})` }, { value: "recent", label: "Most recent" }]} />
        <span className="text-[11px] text-muted">{tab === "flagged" ? "partial index: flags × service_date where flagged" : "ESR index: rendering_npi, service_date desc"}</span>
      </div>
      {rows.length === 0 ? <Empty>No claims.</Empty> : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[640px] text-xs">
            <thead><tr className="border-b border-line text-left text-[10.5px] uppercase tracking-wider text-muted"><th className="px-5 py-2 font-medium">Claim</th><th className="font-medium">Service</th><th className="font-medium">Member</th><th className="font-medium">Procedures</th><th className="text-right font-medium">Charge</th><th className="px-5 font-medium">Flags</th></tr></thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c._id} className="border-b border-line/50">
                  <td className="px-5 py-2"><Link to={`/claims?open=${c._id}`} className="hover:text-accent"><Mono>{c._id.slice(0, 16)}…</Mono></Link></td>
                  <td className="py-2 text-ink-2">{fmtDate(c.service_date)}</td>
                  <td className="py-2"><Mono className="text-ink-2">{c.patient_id}</Mono></td>
                  <td className="py-2 text-ink-2">{c.line_items.map((l: Json) => l.cpt + (l.units > 1 ? `×${l.units}` : "")).join(", ")}</td>
                  <td className="tnum py-2 text-right text-ink">{fmtMoney(c.total_charge)}</td>
                  <td className="px-5 py-2"><div className="flex gap-1">{(c.flags ?? []).map((f: string) => <Badge key={f} tone="critical">{f}</Badge>)}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function CaseCard({ npi, caseDoc, audit }: { npi: string; caseDoc: Json; audit: Json[] }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState(caseDoc?.status ?? "open");
  const [note, setNote] = useState("");
  const save = useMutation({
    mutationFn: () => api.post(`/api/cases/${npi}`, { status, note }),
    onSuccess: () => { toast.success("Case updated", { description: "Case and audit entry committed in one transaction" }); setNote(""); qc.invalidateQueries({ queryKey: ["provider", npi] }); },
    onError: (e: Error) => toast.error(e.message),
  });
  return (
    <Panel title="Case review" subtitle="Status + audit entry are written in one ACID transaction">
      <Segmented value={status} onChange={setStatus} size="sm" options={[{ value: "open", label: "Open" }, { value: "investigating", label: "Investigating" }, { value: "confirmed_fraud", label: "Fraud" }, { value: "cleared", label: "Cleared" }]} />
      <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} placeholder="Note…" className="mt-3 w-full resize-none rounded-lg border border-line bg-surface-2 p-2.5 text-[13px] text-ink outline-none focus:border-accent" />
      <Button variant="primary" className="mt-2 w-full" onClick={() => save.mutate()} disabled={save.isPending}>Save decision</Button>
      <ol className="mt-4 space-y-2 border-t border-line pt-3">
        {audit.length === 0 && <li className="text-xs text-muted">No decisions yet.</li>}
        {audit.map((a) => (
          <li key={a._id} className="text-xs"><span className="text-muted">{fmtDateTime(a.at)}</span> · {a.from ? `${a.from.replace("_", " ")} → ` : ""}<b className="text-ink">{a.to.replace("_", " ")}</b>{a.note && <div className="text-ink-2">{a.note}</div>}</li>
        ))}
      </ol>
    </Panel>
  );
}
