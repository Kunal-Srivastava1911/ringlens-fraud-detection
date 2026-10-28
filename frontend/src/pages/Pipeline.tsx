import { useMutation, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Bomb, CheckCircle2, Pause, Play, Radio, Square, Waypoints, XCircle } from "lucide-react";
import { motion } from "motion/react";
import { useCallback, useState } from "react";
import { toast } from "sonner";
import { axisStyle, Chart, legendStyle } from "../components/Chart";
import { Badge, Button, Mono, PageHeader, Panel, Segmented, Skeleton } from "../components/ui";
import { api, type Json } from "../lib/api";
import { fmtDateTime, fmtInt, timeAgo } from "../lib/format";
import { useTick } from "../lib/hooks";
import { useLive } from "../lib/store";
import type { Tokens } from "../lib/theme";
import { JobProgress } from "./CommandCenter";

export default function Pipeline() {
  useTick(1000);
  const { data } = useQuery({ queryKey: ["pipeline"], queryFn: () => api.get("/api/pipeline/status"), refetchInterval: 2000 });
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: () => api.get("/api/meta") });
  const job = useLive((s) => s.job);
  const times = useLive((s) => s.claimTimes);
  const rate = times.filter((t) => Date.now() - t < 5000).length / 5;

  if (!data) return <Skeleton className="h-[640px]" />;
  const rep = data.report;
  const state: string = rep.state ?? "unknown";
  return (
    <div className="space-y-4">
      <PageHeader eyebrow="Change data capture · eventual consistency" title="Pipeline"
        subtitle="MongoDB change streams carry every claim into Neo4j: snapshot handoff, resume tokens saved only after Neo4j commits, idempotent upserts keyed on the claim id. Break it on purpose and watch it recover." />
      {job?.status === "running" && <JobProgress job={job} />}
      <Panel pad={false}><FlowDiagram state={state} rate={rate} paused={!!data.control?.paused} /></Panel>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StateTile state={state} pid={data.pid} />
        <Tile label="Events applied" value={fmtInt(rep.events_applied)} sub={`snapshot ${fmtInt(rep.snapshot?.claims)} claims in ${rep.snapshot?.seconds ?? "—"}s`} />
        <Tile label="Last batch lag" value={data.last_batch ? `${Math.round(data.last_batch.lag_ms_avg)} ms` : "—"} sub={data.last_batch ? `max ${Math.round(data.last_batch.lag_ms_max)} ms · ${data.last_batch.events} events · apply ${data.last_batch.apply_ms} ms` : "no batches yet"} />
        <Tile label="Duplicate edges" value={fmtInt(rep.duplicate_edges)} sub="relationships sharing a claim_id (must be 0)" good={rep.duplicate_edges === 0} />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Store parity" subtitle="MongoDB is the source of truth; Neo4j is a derived view that converges">
          <Parity label="Claims ↔ TREATED edges" a={rep.mongo_claims} b={rep.graph_treated} />
          <Parity label="Referral claims ↔ REFERRED edges" a={rep.mongo_referrals} b={rep.graph_referred} />
          <div className={clsx("mt-4 flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium", rep.in_sync ? "bg-[color-mix(in_srgb,var(--good)_12%,transparent)] text-good-ink" : "bg-[color-mix(in_srgb,var(--warning)_14%,transparent)] text-ink")}>
            {rep.in_sync ? <CheckCircle2 size={14} /> : <Radio size={14} className="text-warning" />}
            {rep.in_sync ? "In sync: every claim is in the graph" : `Converging: graph is ${fmtInt(rep.mongo_claims - rep.graph_treated)} claims behind`}
          </div>
          <div className="mt-3 text-[11px] text-muted">Resume token</div>
          <Mono className="block truncate text-ink-2">{rep.resume_token ?? "—"}</Mono>
          <div className="mt-2 text-[11px] text-muted">Last applied {timeAgo(rep.last_applied_at)} · heartbeat {timeAgo(rep.heartbeat)}</div>
        </Panel>
        <Panel className="xl:col-span-2" title="Replication lag into Neo4j" subtitle="Per batch: applied time − change event wallTime (from the pipeline_metrics time-series collection)">
          <LagChart metrics={data.metrics} />
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Controls data={data} metros={meta?.metros ?? []} />
        <EventConsole />
        <Panel title="Intake (last hour)" subtitle="claims_staging outcomes · TTL removes payloads after 24 h">
          <div className="grid grid-cols-3 gap-2 text-center">
            {["received", "promoted", "rejected"].map((k) => (
              <div key={k} className="rounded-lg bg-surface-2 p-2"><div className={clsx("tnum text-lg font-semibold", k === "rejected" ? "text-critical" : "text-ink")}>{fmtInt(data.staging?.[k] ?? 0)}</div><div className="text-[11px] text-muted">{k}</div></div>
            ))}
          </div>
          <div className="mt-3 text-[11px] font-semibold uppercase tracking-wider text-muted">Recent rejections</div>
          <ul className="mt-2 space-y-1.5">
            {(data.recent_rejections ?? []).slice(0, 6).map((r: Json) => (
              <li key={r._id} className="text-[11.5px]">
                <span className="text-muted">{fmtDateTime(r.received_at)} · {r.format}</span>
                <div className="text-ink-2">{Array.isArray(r.error) ? r.error.map((e: Json) => `${e.path}: ${e.reason}`).join("; ") : String(r.error)}</div>
              </li>
            ))}
            {!data.recent_rejections?.length && <li className="text-xs text-muted">None yet. The live feed sends a malformed claim about 1 time in 80.</li>}
          </ul>
        </Panel>
      </div>
    </div>
  );
}

function Tile({ label, value, sub, good }: { label: string; value: string; sub?: string; good?: boolean }) {
  return (
    <div className="panel p-4">
      <div className="text-xs text-ink-2">{label}</div>
      <div className={clsx("tnum mt-2 text-2xl font-semibold", good === false ? "text-critical" : "text-ink")}>{value}</div>
      {sub && <div className="mt-1 text-[11px] text-muted">{sub}</div>}
    </div>
  );
}

function StateTile({ state, pid }: { state: string; pid?: number }) {
  const tone = state === "streaming" ? "good" : state === "paused" || state === "snapshotting" ? "warning" : "critical";
  return (
    <div className={clsx("panel p-4", tone === "critical" && "glow-critical")}>
      <div className="text-xs text-ink-2">CDC worker</div>
      <div className="mt-2 flex items-center gap-2 text-2xl font-semibold text-ink">
        <span className={clsx("live-dot size-2.5 rounded-full", tone === "good" ? "bg-good text-good" : tone === "warning" ? "bg-warning text-warning" : "bg-critical text-critical")} />
        {state}
      </div>
      <div className="mt-1 text-[11px] text-muted">pid {pid ?? "—"} · supervised (auto-restart)</div>
    </div>
  );
}

function Parity({ label, a, b }: { label: string; a: number; b: number }) {
  const pct = a ? Math.min(1, b / a) : 1;
  return (
    <div className="mb-3">
      <div className="mb-1 flex justify-between text-xs"><span className="text-ink-2">{label}</span><span className="tnum text-ink">{fmtInt(b)} / {fmtInt(a)}</span></div>
      <div className="h-2 overflow-hidden rounded-full bg-surface-3">
        <motion.div className={clsx("h-full rounded-full", pct >= 1 ? "bg-good" : "bg-warning")} animate={{ width: `${pct * 100}%` }} transition={{ duration: 0.6 }} />
      </div>
    </div>
  );
}

function LagChart({ metrics }: { metrics: Json[] }) {
  const option = useCallback((t: Tokens) => ({
    grid: { left: 48, right: 12, top: 30, bottom: 26 },
    legend: { ...legendStyle(t), top: 0, left: 0 },
    tooltip: { trigger: "axis" as const, valueFormatter: (v: number) => `${Math.round(v)} ms` },
    xAxis: { type: "time" as const, ...axisStyle(t), splitLine: { show: false } },
    yAxis: { type: "value" as const, name: "ms", ...axisStyle(t) },
    series: [
      { name: "Average lag", type: "line" as const, showSymbol: false, data: metrics.map((m) => [m.ts, m.lag_ms_avg]), lineStyle: { color: t.accent, width: 2 }, itemStyle: { color: t.accent },
        areaStyle: { color: { type: "linear" as const, x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: t.accent + "55" }, { offset: 1, color: t.accent + "00" }] } } },
      { name: "Max lag", type: "line" as const, showSymbol: false, data: metrics.map((m) => [m.ts, m.lag_ms_max]), lineStyle: { color: t.muted, width: 1.5, type: "dashed" as const }, itemStyle: { color: t.muted } },
    ],
  }), [metrics]);
  if (!metrics.length) return <div className="grid h-[240px] place-items-center text-sm text-muted">No batches in the last 15 minutes: start the claim feed.</div>;
  return <Chart option={option} height={240} />;
}

function Controls({ data, metros }: { data: Json; metros: string[] }) {
  const [rate, setRate] = useState(data.feed?.rate || 15);
  const [size, setSize] = useState(5);
  const [camo, setCamo] = useState<"yes" | "no">("yes");
  const [metro, setMetro] = useState("");
  const feedOn = data.feed?.running;
  const feed = useMutation({
    mutationFn: (on: boolean) => (on ? api.post("/api/simulate/feed", { rate }) : api.del("/api/simulate/feed")),
    onSuccess: (_r, on) => toast(on ? `Claim feed running at ${rate}/s` : "Claim feed stopped"),
  });
  const plant = useMutation({
    mutationFn: () => api.post("/api/simulate/ring", { size, camouflage: camo === "yes", metro: metro || null, analyze: true }),
    onSuccess: (r) => toast.success(`Planted ${r.planted_ring}: ${r.members.length} providers in ${r.metro}`, { description: `${r.claims_submitted} claims through intake · analytics run started` }),
    onError: (e: Error) => toast.error(e.message),
  });
  const ctl = useMutation({
    mutationFn: (a: "pause" | "resume" | "crash") => api.post(`/api/pipeline/${a}`),
    onSuccess: (_r, a) => toast(a === "crash" ? "Crash requested: the worker applies one batch, dies before saving its token, and restarts" : a === "pause" ? "Pipeline paused: MongoDB keeps accepting claims" : "Pipeline resumed from its resume token"),
  });
  return (
    <Panel title="Chaos controls" subtitle="Generate traffic, plant fraud, break the pipeline">
      <div className="space-y-5">
        <div>
          <div className="mb-2 flex items-center justify-between text-xs"><span className="font-medium text-ink">Live claim feed</span><span className="tnum text-muted">{rate}/s · sent {fmtInt(data.feed?.sent)}</span></div>
          <input type="range" min={1} max={100} value={rate} onChange={(e) => setRate(Number(e.target.value))} className="w-full accent-[var(--accent)]" />
          <div className="mt-2 flex gap-2">
            <Button size="sm" variant={feedOn ? "secondary" : "primary"} onClick={() => feed.mutate(true)}><Play size={13} />{feedOn ? "Update rate" : "Start feed"}</Button>
            <Button size="sm" onClick={() => feed.mutate(false)} disabled={!feedOn}><Square size={12} />Stop</Button>
          </div>
        </div>
        <div className="border-t border-line pt-4">
          <div className="mb-2 flex items-center justify-between text-xs"><span className="font-medium text-ink">Plant a referral ring</span><span className="text-muted">{size} providers</span></div>
          <input type="range" min={3} max={8} value={size} onChange={(e) => setSize(Number(e.target.value))} className="w-full accent-[var(--critical)]" />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Segmented value={camo} onChange={setCamo} size="sm" options={[{ value: "yes", label: "Camouflaged" }, { value: "no", label: "Inflated billing" }]} />
            <select value={metro} onChange={(e) => setMetro(e.target.value)} className="h-7 rounded-md border border-line bg-surface-2 px-2 text-[11px] text-ink">
              <option value="">Any metro</option>{metros.map((m) => <option key={m}>{m}</option>)}
            </select>
          </div>
          <Button size="sm" variant="danger" className="mt-2 w-full" onClick={() => plant.mutate()} disabled={plant.isPending}><Waypoints size={13} />{plant.isPending ? "Submitting claims…" : "Plant ring & analyze"}</Button>
        </div>
        <div className="border-t border-line pt-4">
          <div className="mb-2 text-xs font-medium text-ink">CDC worker</div>
          <div className="grid grid-cols-3 gap-2">
            <Button size="sm" onClick={() => ctl.mutate("pause")}><Pause size={12} />Pause</Button>
            <Button size="sm" onClick={() => ctl.mutate("resume")}><Play size={12} />Resume</Button>
            <Button size="sm" variant="danger" onClick={() => ctl.mutate("crash")} title="Apply a batch to Neo4j, then exit before saving the resume token"><Bomb size={12} />Crash</Button>
          </div>
          {data.crashed_at && <p className="mt-2 text-[11px] text-muted">Last crash {timeAgo(data.crashed_at)} with {data.crash_events_in_flight} applied-but-unacknowledged events, all redelivered and absorbed by the idempotent upsert.</p>}
        </div>
      </div>
    </Panel>
  );
}

function EventConsole() {
  const events = useLive((s) => s.events).slice(0, 60);
  const color = (type: string) => type.startsWith("claim.rejected") ? "text-critical" : type.startsWith("ring") ? "text-critical" : type.startsWith("cdc") ? "text-accent" : type.startsWith("job") ? "text-violet" : "text-good-ink";
  const describe = (e: Json) => {
    const d = e.data;
    switch (e.type) {
      case "claim.ingested": return `${d.format} ${d.payer} → ${d.specialty} $${d.amount}${d.referring ? " (referral)" : ""}`;
      case "claim.rejected": return d.reason;
      case "cdc.batch": return d.batch ? `${d.batch.events} events · lag ${Math.round(d.batch.lag_ms_avg)} ms · apply ${d.batch.apply_ms} ms` : d.state;
      case "pipeline.state": return `state → ${d.state}${d.snapshot_progress ? ` (${fmtInt(d.snapshot_progress)})` : ""}`;
      case "pipeline.control": return `paused=${d.paused} crash=${d.crash}`;
      case "job.progress": return `${d.status} · step ${d.step}`;
      case "ring.detected": return `${d._id}: ${d.size} providers, suspicion ${Number(d.suspicion).toFixed(2)}`;
      case "ring.planted": return `${d.ring_id}: ${d.claims} claims`;
      default: return JSON.stringify(d).slice(0, 80);
    }
  };
  return (
    <Panel title="Event stream" subtitle="WebSocket fed by change streams on jobs, rings, cdc_offsets, pipeline_control" pad={false}>
      <div className="h-[392px] overflow-y-auto bg-[color-mix(in_srgb,var(--page)_70%,transparent)] px-4 py-3 font-mono text-[11px] leading-[1.7]">
        {events.length === 0 && <div className="text-muted">waiting for events…</div>}
        {events.map((e) => (
          <div key={e.id} className="flex gap-2">
            <span className="shrink-0 text-muted">{new Date(e.at).toLocaleTimeString("en-US", { hour12: false })}</span>
            <span className={clsx("shrink-0", color(e.type))}>{e.type}</span>
            <span className="truncate text-ink-2">{describe(e)}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

/** Architecture diagram whose packets move at the measured intake rate and stop where the pipeline is broken. */
function FlowDiagram({ state, rate, paused }: { state: string; rate: number; paused: boolean }) {
  const stages = [
    { x: 70, label: "Payer feeds", sub: "JSON · X12 837P", tone: "ink" },
    { x: 205, label: "Staging", sub: "TTL 24 h", tone: "stat" },
    { x: 340, label: "$jsonSchema", sub: "strict · error", tone: "stat" },
    { x: 475, label: "claims", sub: "MongoDB 8.0", tone: "stat" },
    { x: 610, label: "Change stream", sub: "resume token", tone: "accent" },
    { x: 745, label: "CDC worker", sub: "UNWIND · MERGE", tone: "accent" },
    { x: 880, label: "Neo4j", sub: "GDS 2.13", tone: "graph" },
    { x: 1015, label: "Fusion", sub: "noisy-OR score", tone: "fused" },
  ];
  const broken = paused || state === "paused" || state === "crashed";
  const flowing = rate > 0;
  const dots = Math.min(10, Math.max(3, Math.round(rate / 3)));
  const dur = Math.max(1.4, 6 - rate / 10);
  const stroke: Record<string, string> = { ink: "var(--ink-2)", stat: "var(--s-stat)", accent: "var(--accent)", graph: "var(--s-graph)", fused: "var(--s-fused)" };
  return (
    <div className="overflow-x-auto px-2 py-4">
      <svg viewBox="0 0 1085 150" className="min-w-[860px]">
        <defs>
          <filter id="glow"><feGaussianBlur stdDeviation="2.5" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        </defs>
        <rect x={140} y={18} width={402} height={116} rx={14} fill="none" stroke="var(--s-stat)" strokeOpacity={0.35} strokeDasharray="4 5" />
        <text x={150} y={34} fontSize={10.5} fill="var(--s-stat)" fontWeight={600}>MONGODB · SYSTEM OF RECORD</text>
        <rect x={815} y={18} width={130} height={116} rx={14} fill="none" stroke="var(--s-graph)" strokeOpacity={0.35} strokeDasharray="4 5" />
        <text x={825} y={34} fontSize={10.5} fill="var(--s-graph)" fontWeight={600}>NEO4J</text>
        <line x1={70} y1={84} x2={1015} y2={84} stroke="var(--border-strong)" strokeWidth={2} />
        {broken && <line x1={610} y1={84} x2={1015} y2={84} stroke="var(--warning)" strokeWidth={2} strokeDasharray="5 6" />}
        {flowing && Array.from({ length: dots }).map((_, i) => (
          <circle key={`a${i}`} r={4} fill="var(--accent)" filter="url(#glow)">
            <animate attributeName="cx" from={70} to={broken ? 600 : 1015} dur={`${dur}s`} begin={`${(i * dur) / dots}s`} repeatCount="indefinite" />
            <animate attributeName="cy" values="84;84" dur={`${dur}s`} repeatCount="indefinite" />
            {broken && <animate attributeName="opacity" values="1;1;0" keyTimes="0;0.9;1" dur={`${dur}s`} begin={`${(i * dur) / dots}s`} repeatCount="indefinite" />}
          </circle>
        ))}
        {stages.map((s) => (
          <g key={s.label}>
            <circle cx={s.x} cy={84} r={17} fill="var(--surface)" stroke={stroke[s.tone]} strokeWidth={2} />
            <circle cx={s.x} cy={84} r={6} fill={stroke[s.tone]} opacity={s.x >= 610 && broken ? 0.3 : 1} />
            <text x={s.x} y={122} textAnchor="middle" fontSize={12} fontWeight={600} fill="var(--ink)">{s.label}</text>
            <text x={s.x} y={138} textAnchor="middle" fontSize={10.5} fill="var(--muted)">{s.sub}</text>
          </g>
        ))}
        {broken && (
          <g>
            <rect x={566} y={44} width={88} height={20} rx={6} fill="var(--warning)" />
            <text x={610} y={58} textAnchor="middle" fontSize={11} fontWeight={700} fill="#0b0b0b">{state === "crashed" ? "CRASHED" : "PAUSED"}</text>
          </g>
        )}
        {!flowing && <text x={70} y={60} fontSize={11} fill="var(--muted)">idle: start the claim feed to see traffic</text>}
      </svg>
      <div className="flex flex-wrap gap-2 px-4 pt-1">
        <Badge tone="stat">ACID inside MongoDB: majority-acknowledged writes</Badge>
        <Badge tone="graph">BASE across stores: the graph converges</Badge>
        <Badge tone="accent">CAP: intake stays available when Neo4j lags or is down</Badge>
        {state === "streaming" && <Badge tone="good"><CheckCircle2 size={11} />streaming</Badge>}
        {state === "crashed" && <Badge tone="critical"><XCircle size={11} />restarting</Badge>}
      </div>
    </div>
  );
}
