import { useMutation, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Database, GitBranch, Play, Timer, Zap } from "lucide-react";
import { motion } from "motion/react";
import { useCallback, useState } from "react";
import { axisStyle, Chart, legendStyle } from "../components/Chart";
import { Badge, Button, Empty, JsonView, Mono, PageHeader, Panel, Segmented, Skeleton } from "../components/ui";
import { api, type Json } from "../lib/api";
import { fmtInt } from "../lib/format";
import type { Tokens } from "../lib/theme";

export default function Internals() {
  const [tab, setTab] = useState<"mongo" | "neo4j" | "bench">("mongo");
  return (
    <div className="space-y-4">
      <PageHeader eyebrow="Show your work" title="Database internals"
        subtitle="The DBMS concepts behind the dashboard, measured live: index plans, collection storage, replica-set state, graph schema and traversal costs."
        actions={<Segmented value={tab} onChange={setTab} options={[{ value: "mongo", label: "MongoDB" }, { value: "neo4j", label: "Neo4j" }, { value: "bench", label: "Traversal benchmark" }]} />} />
      {tab === "mongo" && <MongoTab />}
      {tab === "neo4j" && <NeoTab />}
      {tab === "bench" && <BenchTab />}
    </div>
  );
}

function MongoTab() {
  const { data } = useQuery({ queryKey: ["int-mongo"], queryFn: () => api.get("/api/internals/mongo") });
  if (!data) return <Skeleton className="h-[600px]" />;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Topology" subtitle={`MongoDB ${data.version} · ${data.topology === "sharded" ? "sharded cluster" : `replica set ${data.set_name}`}`}>
          <div className="space-y-2">
            {data.members.map((m: Json) => (
              <div key={m.name} className="flex items-center gap-3 rounded-lg border border-line bg-surface-2 px-3 py-2 text-xs">
                <span className={clsx("live-dot size-2 rounded-full", m.state === "PRIMARY" ? "bg-good text-good" : "bg-accent text-accent")} />
                <Mono className="text-ink">{m.name}</Mono>
                <Badge tone={m.state === "PRIMARY" ? "good" : "accent"} className="ml-auto">{m.state}</Badge>
                <span className="tnum text-muted">lag {m.lag_s ?? 0}s</span>
              </div>
            ))}
          </div>
          <div className="mt-3 space-y-1 text-xs text-ink-2">
            <div>Oplog: <b className="text-ink">{fmtInt(data.oplog_mb)} MB</b>, the window change streams can resume within</div>
            <div>Writes: <b className="text-ink">w: majority, j: true</b> · analytics reads: <b className="text-ink">secondaryPreferred, readConcern majority</b></div>
            <div className="text-muted">A single-member set gives change streams and transactions; the sharded cluster profile adds real replicas and shards.</div>
          </div>
        </Panel>
        <IndexRace />
      </div>
      <Panel title="Collections & indexes" subtitle="$collStats storage and $indexStats usage counters (ops since the server started)" pad={false}>
        <div className="divide-y divide-line">
          {data.collections.map((c: Json) => (
            <div key={c.name} className="grid gap-3 px-5 py-3 lg:grid-cols-[240px_1fr]">
              <div>
                <div className="flex items-center gap-2"><Database size={13} className="text-stat" /><Mono className="font-semibold text-ink">{c.name}</Mono></div>
                <div className="mt-1 text-[11px] text-muted">{fmtInt(c.count)} docs · {c.size_mb} MB data · {c.storage_mb} MB on disk{c.avg_doc_bytes ? ` · ${c.avg_doc_bytes} B/doc` : ""}</div>
                <div className="mt-1 flex gap-1">{c.validator && <Badge tone="stat">$jsonSchema</Badge>}{c.timeseries && <Badge tone="fused">time series</Badge>}</div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {c.indexes.map((i: Json) => (
                  <div key={i.name} className="rounded-lg border border-line bg-surface-2 px-2.5 py-1.5 text-[11px]" title={i.partial ? `partialFilterExpression: ${JSON.stringify(i.partial)}` : ""}>
                    <div className="flex items-center gap-1.5"><Mono className="text-ink">{i.name}</Mono>{i.kind.map((k: string) => <Badge key={k} tone={k === "partial" ? "graph" : k.startsWith("TTL") ? "critical" : k === "multikey" ? "fused" : "neutral"}>{k}</Badge>)}</div>
                    <div className="mt-0.5 text-muted">{Object.entries(i.keys).map(([k, v]) => `${k}:${v}`).join(", ")} · {i.size_mb} MB{i.ops != null ? ` · ${fmtInt(i.ops)} ops` : ""}</div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Panel>
      <PipelineViewer />
    </div>
  );
}

function IndexRace() {
  const { data: queries } = useQuery({ queryKey: ["explain-list"], queryFn: () => api.get("/api/internals/explain") });
  const [key, setKey] = useState("provider_timeline");
  const run = useMutation({ mutationFn: (k: string) => api.post(`/api/internals/explain/${k}`) });
  const r = run.data;
  const max = r ? Math.max(r.indexed.docs_examined, r.collscan.docs_examined, 1) : 1;
  const maxMs = r ? Math.max(r.indexed.ms, r.collscan.ms, 1) : 1;
  const current = queries?.find((q: Json) => q.key === key);
  return (
    <Panel className="xl:col-span-2" title="Index race" subtitle="Same query twice: once with its index, once forced to scan the collection ({$natural: 1}). Numbers are MongoDB's own explain executionStats.">
      <div className="flex flex-wrap items-center gap-2">
        <select value={key} onChange={(e) => setKey(e.target.value)} className="h-9 rounded-lg border border-line bg-surface-2 px-2 text-[13px] text-ink">
          {(queries ?? []).map((q: Json) => <option key={q.key} value={q.key}>{q.label}</option>)}
        </select>
        <Button variant="primary" onClick={() => run.mutate(key)} disabled={run.isPending}><Play size={13} />{run.isPending ? "Racing…" : "Run race"}</Button>
      </div>
      {current && <p className="mt-2 text-xs text-ink-2">{current.why}</p>}
      {r ? (
        <div className="mt-4 space-y-4">
          {([["indexed", "With index", "bg-fused"], ["collscan", "Forced COLLSCAN", "bg-critical"]] as const).map(([k, label, bar]) => (
            <div key={k}>
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="font-semibold text-ink">{label}</span>
                <span className="font-mono text-[11px] text-ink-2">{r[k].stage}{r[k].index ? ` (${r[k].index})` : ""}</span>
              </div>
              <div className="grid grid-cols-[110px_1fr_80px] items-center gap-2 text-[11px]">
                <span className="text-muted">docs examined</span>
                <div className="h-3 rounded-full bg-surface-3"><motion.div className={clsx("h-full rounded-full", bar)} initial={{ width: 0 }} animate={{ width: `${Math.max(0.6, (r[k].docs_examined / max) * 100)}%` }} transition={{ duration: k === "indexed" ? 0.4 : 1.6, ease: "easeOut" }} /></div>
                <span className="tnum text-right font-semibold text-ink">{fmtInt(r[k].docs_examined)}</span>
                <span className="text-muted">execution time</span>
                <div className="h-3 rounded-full bg-surface-3"><motion.div className={clsx("h-full rounded-full", bar)} initial={{ width: 0 }} animate={{ width: `${Math.max(0.6, (r[k].ms / maxMs) * 100)}%` }} transition={{ duration: k === "indexed" ? 0.4 : 1.6, ease: "easeOut" }} /></div>
                <span className="tnum text-right font-semibold text-ink">{r[k].ms} ms</span>
              </div>
            </div>
          ))}
          <div className="flex flex-wrap gap-2 text-xs">
            <Badge tone="fused"><Zap size={11} />{r.collscan.docs_examined > 0 ? `${fmtInt(Math.round(r.collscan.docs_examined / Math.max(1, r.indexed.docs_examined)))}× fewer documents read` : ""}</Badge>
            <Badge><Timer size={11} />{fmtInt(r.indexed.returned)} rows returned · keys examined {fmtInt(r.indexed.keys_examined)}</Badge>
          </div>
          <div className="text-[11px] text-muted">filter <Mono>{JSON.stringify(r.indexed.filter)}</Mono> · sort <Mono>{JSON.stringify(r.indexed.sort)}</Mono></div>
        </div>
      ) : <div className="mt-6 text-sm text-muted">Pick a query and run the race.</div>}
    </Panel>
  );
}

function PipelineViewer() {
  const { data } = useQuery({ queryKey: ["pipelines"], queryFn: () => api.get("/api/internals/pipelines") });
  const [name, setName] = useState("billing_profile");
  if (!data) return null;
  return (
    <Panel title="Aggregation pipelines" subtitle="The exact pipelines that compute the statistical signals ($setWindowFields, $merge into provider_signals)">
      <div className="mb-3 flex flex-wrap gap-1.5">
        {Object.keys(data).map((n) => (
          <button key={n} onClick={() => setName(n)} className={clsx("rounded-md border px-2.5 py-1 font-mono text-[11px]", n === name ? "border-transparent bg-accent-soft text-accent" : "border-line bg-surface-2 text-ink-2")}>{n}</button>
        ))}
      </div>
      <JsonView value={data[name]} highlight={["$setWindowFields", "$merge", "$lookup", "$out", "window"]} />
    </Panel>
  );
}

function NeoTab() {
  const { data } = useQuery({ queryKey: ["int-neo4j"], queryFn: () => api.get("/api/internals/neo4j") });
  if (!data) return <Skeleton className="h-[500px]" />;
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Panel title="Graph store" subtitle={`Neo4j ${data.version} ${data.edition} · Graph Data Science ${data.gds}`}>
        <div className="space-y-2 text-xs">
          {data.nodes.map((n: Json) => <Row key={n.label} k={`(:${n.label})`} v={fmtInt(n.count)} />)}
          <div className="border-t border-line pt-2" />
          {data.relationships.map((r: Json) => <Row key={r.type} k={`[:${r.type}]`} v={fmtInt(r.count)} />)}
        </div>
        <p className="mt-3 text-[11px] text-muted">Index-free adjacency: each node stores direct pointers to its relationships, so expanding one hop costs the same however large the graph grows.</p>
      </Panel>
      <Panel className="xl:col-span-2" title="Constraints" subtitle="Uniqueness on every business key, including relationship claim ids: what makes the CDC upserts idempotent" pad={false}>
        <table className="w-full text-xs">
          <thead><tr className="border-b border-line text-left text-[10.5px] uppercase tracking-wider text-muted"><th className="px-5 py-2 font-medium">Name</th><th className="font-medium">Type</th><th className="font-medium">On</th><th className="px-5 font-medium">Property</th></tr></thead>
          <tbody>
            {data.constraints.map((c: Json) => (
              <tr key={c.name} className="border-b border-line/50"><td className="px-5 py-2"><Mono className="text-ink">{c.name}</Mono></td><td className="text-ink-2">{c.type}</td><td><Badge tone={c.entityType === "RELATIONSHIP" ? "graph" : "stat"}>{c.entityType === "RELATIONSHIP" ? `[:${c.labelsOrTypes}]` : `(:${c.labelsOrTypes})`}</Badge></td><td className="px-5 font-mono text-ink-2">{c.properties.join(", ")}</td></tr>
            ))}
          </tbody>
        </table>
        <div className="p-5 text-xs text-ink-2">
          <div className="mb-2 font-semibold text-ink">GDS projections used per analytics run</div>
          <ul className="grid gap-1.5 sm:grid-cols-2">
            <li><Mono className="text-graph">rl_strong</Mono>: REFERS_TO with count ≥ 6 across ≥ 3 patients → SCC</li>
            <li><Mono className="text-graph">rl_referrals</Mono>: weighted, directed → PageRank, betweenness</li>
            <li><Mono className="text-graph">rl_referrals_u</Mono>: undirected → Louvain</li>
            <li><Mono className="text-graph">rl_bipartite</Mono>: Provider–Patient → node similarity</li>
            <li><Mono className="text-graph">rl_identity</Mono>: Provider–BankAccount → WCC</li>
          </ul>
        </div>
      </Panel>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return <div className="flex justify-between"><Mono className="text-ink-2">{k}</Mono><span className="tnum font-semibold text-ink">{v}</span></div>;
}

function BenchTab() {
  const run = useMutation({ mutationFn: () => api.post("/api/internals/traversal?max_hops=6") });
  const { data: rings } = useQuery({ queryKey: ["rings", false], queryFn: () => api.get("/api/rings") });
  const hub = rings?.[0]?.hub;
  const cycles = useQuery({ queryKey: ["cycles", hub], queryFn: () => api.get(`/api/internals/cycles/${hub}`), enabled: !!hub });
  const rows = run.data?.rows ?? [];
  const option = useCallback((t: Tokens) => ({
    grid: { left: 50, right: 16, top: 34, bottom: 34 },
    legend: { ...legendStyle(t), top: 0, left: 0 },
    tooltip: { trigger: "axis" as const, valueFormatter: (v: number) => `${v.toFixed(2)} ms` },
    xAxis: { type: "category" as const, data: rows.map((r: Json) => `${r.hops} hop${r.hops > 1 ? "s" : ""}`), ...axisStyle(t), splitLine: { show: false } },
    yAxis: { type: "value" as const, name: "ms", ...axisStyle(t) },
    series: [
      { name: "Neo4j (APOC BFS)", type: "line" as const, data: rows.map((r: Json) => r.neo4j_ms), lineStyle: { color: t.graph, width: 2 }, itemStyle: { color: t.graph }, symbolSize: 8 },
      { name: "MongoDB $graphLookup", type: "line" as const, data: rows.map((r: Json) => r.graphlookup_ms), lineStyle: { color: t.stat, width: 2 }, itemStyle: { color: t.stat }, symbolSize: 8 },
      { name: "App-side joins", type: "line" as const, data: rows.map((r: Json) => r.app_join_ms), lineStyle: { color: t.fused, width: 2 }, itemStyle: { color: t.fused }, symbolSize: 8 },
    ],
  }), [rows]);
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Panel className="xl:col-span-2" title="k-hop referral reachability, three ways"
        subtitle="From the busiest referrer: providers reachable within k referrals. All three must agree on the count; the timings are honest wall-clock measurements on this machine."
        actions={<Button variant="primary" size="sm" onClick={() => run.mutate()} disabled={run.isPending}><Play size={12} />{run.isPending ? "Running…" : "Run benchmark"}</Button>}>
        {rows.length ? (
          <>
            <Chart option={option} height={280} />
            <table className="mt-3 w-full text-xs">
              <thead><tr className="text-left text-[10.5px] uppercase tracking-wider text-muted"><th className="py-1 font-medium">Hops</th><th className="font-medium">Reached (Neo4j / $graphLookup / app)</th><th className="text-right font-medium">App round trips</th></tr></thead>
              <tbody>{rows.map((r: Json) => <tr key={r.hops} className="border-t border-line/60"><td className="py-1.5">{r.hops}</td><td className="tnum">{r.reached} / {r.reached_graphlookup} / {r.reached_app}</td><td className="tnum text-right">{r.app_round_trips}</td></tr>)}</tbody>
            </table>
          </>
        ) : <Empty>Run the benchmark to measure.</Empty>}
      </Panel>
      <Panel title="What the numbers say" subtitle="Read honestly">
        <div className="space-y-3 text-[12.5px] leading-relaxed text-ink-2">
          <p>At this graph size (about 1,200 providers, 13,600 referral pairs) <b className="text-ink">all three approaches finish in milliseconds</b>, and reachability saturates within about four hops. Raw traversal speed is not where the graph database wins here.</p>
          <p>The real difference is <b className="text-ink">what can be expressed</b>. Strongly connected components, Louvain communities, PageRank, betweenness and "closed loops through this provider" are single procedure calls or patterns in Neo4j; MongoDB has no native way to ask for cycles at all.</p>
          <div className="rounded-lg border border-line bg-surface-2 p-3">
            <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-ink"><GitBranch size={13} className="text-graph" />Closed loops through ring hub <Mono>{hub ?? "—"}</Mono></div>
            {cycles.data ? (
              <>
                <div className="text-[11px] text-muted">{cycles.data.loops.length} loops found over strong edges in {cycles.data.ms} ms (Cypher variable-length pattern)</div>
                <ul className="mt-1.5 space-y-1">{cycles.data.loops.slice(0, 4).map((l: string[], i: number) => <li key={i} className="truncate font-mono text-[10.5px] text-ink-2">{l.map((n) => n.slice(-4)).join(" → ")}</li>)}</ul>
              </>
            ) : <div className="text-[11px] text-muted">{hub ? "computing…" : "no ring detected"}</div>}
            <div className="mt-2 text-[11px] text-muted">MongoDB equivalent: <i>not expressible natively</i> ($graphLookup follows paths but cannot report that a path returns to its start).</div>
          </div>
        </div>
      </Panel>
    </div>
  );
}
