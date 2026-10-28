import { useMutation, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowRight, Banknote, CornerDownRight, FileWarning, Play, ShieldAlert, Users, Waypoints, XCircle } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useCallback, useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { axisStyle, Chart, legendStyle } from "../components/Chart";
import { RingOrbit } from "../components/RingOrbit";
import { Badge, Button, Empty, Kpi, PageHeader, Panel, RiskBadge, ScoreBar, Skeleton } from "../components/ui";
import { api, type Json } from "../lib/api";
import { fmtInt, fmtMoney, timeAgo } from "../lib/format";
import { useTick } from "../lib/hooks";
import { useLive } from "../lib/store";
import { alpha, scoreColor, type Tokens, useTokens } from "../lib/theme";

export default function CommandCenter() {
  const { data, isLoading } = useQuery({ queryKey: ["overview"], queryFn: () => api.get("/api/overview"), refetchInterval: 20000 });
  const job = useLive((s) => s.job);
  const run = useMutation({
    mutationFn: () => api.post("/api/jobs/analyze"),
    onSuccess: () => toast("Analytics started", { description: "MongoDB signals → barrier → Neo4j GDS → fusion → evaluation" }),
    onError: (e: Error) => toast.error(e.message),
  });
  const running = job && job.status === "running";

  if (isLoading || !data) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-80" />
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-28" />)}</div>
        <Skeleton className="h-80" />
      </div>
    );
  }
  const k = data.kpis;
  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Healthcare fraud intelligence"
        title={<>Command Center <span className="gradient-text">·</span> <span className="text-ink-2">referral-ring analytics</span></>}
        subtitle="MongoDB holds every claim and computes statistical signals; Neo4j holds the referral network and finds rings. The score you see fuses both."
        actions={
          <>
            <span className="text-xs text-muted">Last analysis {timeAgo(data.last_analysis?.finished_at)}{data.last_analysis?.seconds ? ` · ${data.last_analysis.seconds}s` : ""}</span>
            <Button variant="primary" onClick={() => run.mutate()} disabled={!!running || run.isPending}>
              <Play size={14} /> {running ? `Running: ${job.step}` : "Run analytics"}
            </Button>
          </>
        }
      />
      <AnimatePresence>{running && <JobProgress job={job} />}</AnimatePresence>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Claims under watch" value={k.claims} icon={FileWarning} sub={`${fmtInt(k.patients)} members · 18 months`} />
        <Kpi label="Billed" value={k.billed} format={(n) => fmtMoney(n, true)} icon={Banknote} tone="fused" sub={`${fmtInt(k.providers)} providers`} />
        <Kpi label="Flagged providers" value={k.flagged_providers} icon={ShieldAlert} tone="critical" glow={k.critical > 0} sub={`${k.critical} critical`} />
        <Kpi label="Referral rings" value={k.rings} icon={Waypoints} tone="graph" sub={`${fmtMoney(k.ring_amount, true)} circulated`} />
        <Kpi label="Billed by flagged providers" value={k.at_risk} format={(n) => fmtMoney(n, true)} icon={Users} tone="violet" sub="exposure under review" />
        <Kpi label="Flagged claims" value={k.flagged_claims} icon={FileWarning} sub="served by a partial index" />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" title="Claims per day" subtitle="Last 180 days by service date · flagged claims carry a detector flag">
          <DailyChart daily={data.daily} />
        </Panel>
        <LiveTicker />
      </div>

      <div className="grid gap-4 xl:grid-cols-5">
        <Panel className="xl:col-span-3" title="Statistical vs graph evidence" subtitle="Each dot is a provider. Neither store sees everything: lone upcoders sit on the right, camouflaged rings at the top.">
          <Quadrant points={data.quadrant} />
        </Panel>
        <Panel className="xl:col-span-2" title="Highest-risk providers" actions={<Link to="/providers" className="flex items-center gap-1 text-xs text-accent hover:underline">All providers <ArrowRight size={12} /></Link>}>
          <TopTable rows={data.top} />
        </Panel>
      </div>

      <Panel title="Detected referral rings" subtitle="Closed referral loops (strongly connected components) that pass the ring analysis. Particles show patients and money circulating."
        actions={<Link to="/rings" className="flex items-center gap-1 text-xs text-accent hover:underline">Ring triage <ArrowRight size={12} /></Link>}>
        <RingStrip rings={data.rings} quadrant={data.quadrant} />
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Fused risk score distribution" subtitle="Providers per 5-point bucket · flag threshold 50">
          <Histogram hist={data.histogram} />
        </Panel>
        <Panel title="Flagged providers by specialty">
          <Specialties rows={data.specialties} />
        </Panel>
      </div>
    </div>
  );
}

export function JobProgress({ job }: { job: Json }) {
  const steps = ["barrier", "mongo", "graph", "score", "evaluate"];
  const labels: Record<string, string> = { barrier: "CDC barrier", mongo: "MongoDB aggregation", graph: "Neo4j GDS", score: "Fusion", evaluate: "Evaluation" };
  return (
    <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="panel overflow-hidden px-5 py-3">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span className="font-semibold text-ink">Analytics run</span>
        {steps.map((s) => {
          const st = job.steps?.[s]?.status;
          return (
            <span key={s} className={clsx("flex items-center gap-1.5 rounded-full border px-2.5 py-1", st === "done" ? "border-transparent bg-[color-mix(in_srgb,var(--good)_15%,transparent)] text-good-ink" : st === "running" ? "border-accent text-accent" : "border-line text-muted")}>
              {st === "running" && <span className="live-dot size-1.5 rounded-full bg-accent text-accent" />}
              {labels[s]}
              {job.steps?.[s]?.seconds != null && <span className="tnum opacity-75">{job.steps[s].seconds}s</span>}
            </span>
          );
        })}
      </div>
    </motion.div>
  );
}

function DailyChart({ daily }: { daily: Json[] }) {
  const option = useCallback((t: Tokens) => ({
    grid: { left: 44, right: 12, top: 30, bottom: 28 },
    legend: { ...legendStyle(t), top: 0, right: 0 },
    tooltip: { trigger: "axis" as const, axisPointer: { type: "line" as const, lineStyle: { color: t.axis } } },
    xAxis: { type: "category" as const, data: daily.map((d) => d.date), ...axisStyle(t), splitLine: { show: false }, axisLabel: { ...axisStyle(t).axisLabel, formatter: (v: string) => v.slice(5) } },
    yAxis: { type: "value" as const, ...axisStyle(t) },
    series: [
      { name: "Claims", type: "line" as const, data: daily.map((d) => d.claims), smooth: 0.3, symbol: "none", lineStyle: { width: 2, color: t.accent },
        areaStyle: { color: { type: "linear" as const, x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: alpha(t.accent, 0.35) }, { offset: 1, color: alpha(t.accent, 0) }] } }, itemStyle: { color: t.accent } },
      { name: "Flagged claims", type: "bar" as const, data: daily.map((d) => d.flagged), barWidth: "60%", itemStyle: { color: t.serious, borderRadius: [3, 3, 0, 0] } },
    ],
  }), [daily]);
  return <Chart option={option} height={270} />;
}

function LiveTicker() {
  useTick(1000);
  const events = useLive((s) => s.events);
  const times = useLive((s) => s.claimTimes);
  const claims = events.filter((e) => e.type === "claim.ingested" || e.type === "claim.rejected").slice(0, 9);
  const rate = times.filter((t) => Date.now() - t < 5000).length / 5;
  const nav = useNavigate();
  return (
    <Panel title={<span className="flex items-center gap-2"><span className={clsx("live-dot size-2 rounded-full", rate > 0 ? "bg-good text-good" : "bg-neutral text-neutral")} />Live intake</span>}
      subtitle="Payer feeds → staging → $jsonSchema → claims"
      actions={<span className="tnum rounded-md bg-surface-2 px-2 py-1 text-xs font-semibold text-ink">{rate.toFixed(1)} claims/s</span>}>
      {claims.length === 0 ? (
        <Empty>
          <div className="text-center">
            <p>No live traffic yet.</p>
            <Button size="sm" className="mt-3" onClick={() => nav("/pipeline")}>Start the claim feed <ArrowRight size={12} /></Button>
          </div>
        </Empty>
      ) : (
        <ul className="space-y-1.5">
          {/* Entrance-only animation: at several events per second a layout animation never settles. */}
          {claims.map((e) => (
              <motion.li key={e.id} initial={{ opacity: 0, x: -12 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.25 }}
                className={clsx("flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs", e.type === "claim.rejected" ? "border-[color-mix(in_srgb,var(--critical)_35%,transparent)] bg-[color-mix(in_srgb,var(--critical)_8%,transparent)]" : "border-line bg-surface-2/60")}>
                {e.type === "claim.rejected" ? (
                  <>
                    <XCircle size={13} className="shrink-0 text-critical" />
                    <span className="truncate text-ink-2">Rejected · {e.data.reason}</span>
                  </>
                ) : (
                  <>
                    <Badge tone={e.data.format === "837P" ? "graph" : "stat"}>{e.data.format}</Badge>
                    <span className="truncate text-ink-2">{e.data.specialty}</span>
                    {e.data.referring && <CornerDownRight size={12} className="shrink-0 text-muted" />}
                    <span className="tnum ml-auto font-medium text-ink">{fmtMoney(e.data.amount)}</span>
                  </>
                )}
              </motion.li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function Quadrant({ points }: { points: Json[] }) {
  const nav = useNavigate();
  const option = useCallback((t: Tokens) => {
    const groups = [
      { name: "Both stores", color: t.fused, f: (p: Json) => p.stat >= 50 && p.graph >= 50 },
      { name: "Statistics only", color: t.stat, f: (p: Json) => p.stat >= 50 && p.graph < 50 },
      { name: "Graph only", color: t.graph, f: (p: Json) => p.stat < 50 && p.graph >= 50 },
    ];
    const below = points.filter((p) => p.stat < 50 && p.graph < 50);
    return {
      grid: { left: 48, right: 20, top: 34, bottom: 44 },
      legend: { ...legendStyle(t), top: 0, left: 0 },
      tooltip: { trigger: "item" as const, formatter: (p: Json) => `<b>${p.data[3]}</b><br/>statistical ${Math.round(p.data[0])} · graph ${Math.round(p.data[1])}<br/>fused <b>${Math.round(p.data[2])}</b>` },
      xAxis: { type: "value" as const, min: 0, max: 100, name: "Statistical-only score (MongoDB)", nameLocation: "middle" as const, nameGap: 28, ...axisStyle(t) },
      yAxis: { type: "value" as const, min: 0, max: 100, name: "Graph-only score (Neo4j)", nameLocation: "middle" as const, nameGap: 34, ...axisStyle(t) },
      series: [
        { name: "Below threshold", type: "scatter" as const, symbolSize: 5, itemStyle: { color: alpha(t.dark ? "#b9b7ae" : "#6f6d68", 0.35) },
          data: below.map((p) => [p.stat, p.graph, p.score, p.name, p.npi]), emphasis: { scale: 2 },
          markLine: { silent: true, symbol: "none", lineStyle: { color: t.axis, type: "dashed" as const }, label: { color: t.muted, fontSize: 10 },
            data: [{ xAxis: 50, label: { formatter: "flag threshold" } }, { yAxis: 50, label: { formatter: "" } }] } },
        ...groups.map((g) => ({
          name: g.name, type: "scatter" as const, symbolSize: (d: number[]) => 8 + d[2] / 12,
          itemStyle: { color: g.color, borderColor: t.surface, borderWidth: 1.5, shadowBlur: 10, shadowColor: alpha(g.color, 0.6) },
          data: points.filter(g.f).map((p) => [p.stat, p.graph, p.score, p.name, p.npi]), emphasis: { scale: 1.4 },
        })),
      ],
    };
  }, [points]);
  return <Chart option={option} height={360} onClick={(p) => { const d = p.data as Json; if (d?.[4]) nav(`/providers/${d[4]}`); }} />;
}

function TopTable({ rows }: { rows: Json[] }) {
  const t = useTokens();
  return (
    <div className="-mx-2 space-y-0.5">
      {rows.slice(0, 9).map((r, i) => (
        <Link key={r._id} to={`/providers/${r._id}`} className="group flex items-center gap-3 rounded-lg px-2 py-2 transition hover:bg-surface-2">
          <span className="tnum w-4 text-right text-xs text-muted">{i + 1}</span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-[13px] font-medium text-ink">{r.name}</span>
              {r.ring_id && <Badge tone="critical">{r.ring_id}</Badge>}
            </div>
            <div className="truncate text-[11.5px] text-muted">{r.reasons?.[0] ?? r.specialty}</div>
          </div>
          <RiskBadge risk={r.risk} className="hidden sm:inline-flex" />
          <ScoreBar score={r.score} color={scoreColor(t, r.score)} />
        </Link>
      ))}
    </div>
  );
}

function RingStrip({ rings, quadrant }: { rings: Json[]; quadrant: Json[] }) {
  const nav = useNavigate();
  const byNpi = useMemo(() => Object.fromEntries(quadrant.map((q) => [q.npi, q])), [quadrant]);
  if (!rings.length) return <Empty>No rings detected yet. Run analytics.</Empty>;
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
      {rings.map((r) => (
        <button key={r._id} onClick={() => nav(`/rings/${r._id}`)} className="group rounded-xl border border-line bg-surface-2/50 p-3 text-left transition hover:border-[color-mix(in_srgb,var(--critical)_50%,transparent)] hover:bg-surface-2">
          <div className="flex items-center justify-between">
            <span className="font-mono text-[12px] font-semibold text-ink">{r._id}</span>
            <Badge tone="critical">suspicion {r.suspicion.toFixed(2)}</Badge>
          </div>
          <RingOrbit compact height={150} hub={r.hub} members={r.members.map((m: string) => ({ npi: m, score: byNpi[m]?.score, name: byNpi[m]?.name }))} flows={r.flows} />
          <div className="mt-1 grid grid-cols-3 gap-1 text-[11px]">
            <Stat label="providers" value={r.size} />
            <Stat label="circulated" value={fmtMoney(r.internal_amount, true)} />
            <Stat label="recirculation" value={`${Math.round(r.metrics.recirculation * 100)}%`} />
          </div>
        </button>
      ))}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div className="tnum font-semibold text-ink">{value}</div>
      <div className="text-muted">{label}</div>
    </div>
  );
}

function Histogram({ hist }: { hist: Json[] }) {
  const option = useCallback((t: Tokens) => ({
    grid: { left: 40, right: 8, top: 12, bottom: 28 },
    tooltip: { trigger: "axis" as const, formatter: (p: Json) => `score ${p[0].name}–${Number(p[0].name) + 5}: <b>${p[0].value}</b> providers` },
    xAxis: { type: "category" as const, data: hist.map((h) => String(h.bucket)), ...axisStyle(t), splitLine: { show: false } },
    yAxis: { type: "log" as const, logBase: 10, min: 1, ...axisStyle(t), name: "providers (log)", nameTextStyle: { color: t.muted, fontSize: 10, align: "left" as const } },
    series: [{
      type: "bar" as const, barCategoryGap: "12%",
      data: hist.map((h) => ({ value: Math.max(h.count, 0.9), itemStyle: { color: scoreColor(t, h.bucket + 2.5), borderRadius: [4, 4, 0, 0] } })),
    }],
  }), [hist]);
  return (
    <>
      <Chart option={option} height={220} />
      <div className="mt-2 flex flex-wrap gap-2">
        <RiskBadge risk="low" /><RiskBadge risk="elevated" /><RiskBadge risk="high" /><RiskBadge risk="critical" />
      </div>
    </>
  );
}

function Specialties({ rows }: { rows: Json[] }) {
  const data = useMemo(() => rows.filter((r) => r.flagged > 0).slice(0, 10).reverse(), [rows]);
  const option = useCallback((t: Tokens) => ({
    grid: { left: 130, right: 40, top: 6, bottom: 20 },
    tooltip: { trigger: "axis" as const, axisPointer: { type: "shadow" as const } },
    xAxis: { type: "value" as const, ...axisStyle(t), minInterval: 1 },
    yAxis: { type: "category" as const, data: data.map((r) => r.specialty), ...axisStyle(t), axisLabel: { color: t.ink2, fontSize: 11 } },
    series: [{ type: "bar" as const, name: "Flagged providers", data: data.map((r) => r.flagged), barWidth: 12,
      itemStyle: { color: t.accent, borderRadius: [0, 4, 4, 0] }, label: { show: true, position: "right" as const, color: t.ink2, fontSize: 11, formatter: (p: Json) => `${p.value} / ${data[p.dataIndex].providers}` } }],
  }), [data]);
  return data.length ? <Chart option={option} height={250} /> : <Empty>No flagged providers.</Empty>;
}
