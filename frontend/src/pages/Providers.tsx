import { keepPreviousData, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Search } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge, Empty, PageHeader, Panel, RiskBadge, ScoreBar, Segmented, Skeleton } from "../components/ui";
import { api, type Json, qs } from "../lib/api";
import { FRAUD_TYPE_LABELS, fmtInt, fmtMoney } from "../lib/format";
import { useUi } from "../lib/store";
import { scoreColor, useTokens } from "../lib/theme";

export default function Providers() {
  const t = useTokens();
  const reveal = useUi((s) => s.reveal);
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [risk, setRisk] = useState("all");
  const [specialty, setSpecialty] = useState("");
  const [sort, setSort] = useState("score");
  const [ringOnly, setRingOnly] = useState(false);
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: () => api.get("/api/meta") });
  const { data, isLoading } = useQuery({
    queryKey: ["providers", q, risk, specialty, sort, ringOnly, reveal],
    queryFn: () => api.get(`/api/providers${qs({ q, risk: risk === "all" ? undefined : risk, specialty, sort, ring: ringOnly ? true : undefined, reveal, limit: 300 })}`),
    placeholderData: keepPreviousData,
  });

  return (
    <div>
      <PageHeader eyebrow="Fused score · MongoDB + Neo4j" title="Providers" subtitle="Ranked by the fused fraud score. The statistical-only and graph-only columns show what each store would have seen alone." />
      <Panel pad={false}>
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-4">
          <div className="flex h-9 min-w-[240px] flex-1 items-center gap-2 rounded-lg border border-line bg-surface-2 px-3">
            <Search size={15} className="text-muted" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, NPI or specialty" className="w-full bg-transparent text-[13px] text-ink outline-none placeholder:text-muted" />
          </div>
          <Segmented value={risk} onChange={setRisk} options={[{ value: "all", label: "All" }, { value: "critical", label: "Critical" }, { value: "high", label: "High" }, { value: "elevated", label: "Elevated" }, { value: "low", label: "Low" }]} />
          <select value={specialty} onChange={(e) => setSpecialty(e.target.value)} className="h-9 rounded-lg border border-line bg-surface-2 px-2 text-[13px] text-ink">
            <option value="">All specialties</option>
            {(meta?.specialties ?? []).map((s: string) => <option key={s}>{s}</option>)}
          </select>
          <select value={sort} onChange={(e) => setSort(e.target.value)} className="h-9 rounded-lg border border-line bg-surface-2 px-2 text-[13px] text-ink">
            <option value="score">Sort: score</option><option value="billed">Sort: billed</option><option value="claims">Sort: claims</option><option value="delta">Sort: score change</option><option value="name">Sort: name</option>
          </select>
          <button onClick={() => setRingOnly(!ringOnly)} className={clsx("h-9 rounded-lg border px-3 text-xs font-medium", ringOnly ? "border-transparent bg-[color-mix(in_srgb,var(--critical)_15%,transparent)] text-critical" : "border-line bg-surface-2 text-ink-2")}>Ring members</button>
          <span className="ml-auto text-xs text-muted">{data ? `${fmtInt(data.total)} providers` : ""}</span>
        </div>
        {isLoading ? <div className="space-y-2 p-4">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-10" />)}</div> : !data?.rows.length ? <Empty>No providers match.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-[13px]">
              <thead>
                <tr className="border-b border-line text-left text-[11px] uppercase tracking-wider text-muted">
                  <th className="px-5 py-2.5 font-medium">#</th><th className="py-2.5 font-medium">Provider</th><th className="py-2.5 font-medium">Risk</th>
                  <th className="py-2.5 font-medium">Fused</th><th className="py-2.5 text-right font-medium">Stat. only</th><th className="py-2.5 text-right font-medium">Graph only</th>
                  <th className="py-2.5 text-right font-medium">Claims</th><th className="py-2.5 text-right font-medium">Billed</th><th className="py-2.5 pl-4 font-medium">Top reason</th>
                  {reveal && <th className="px-5 py-2.5 font-medium text-violet">Ground truth</th>}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r: Json) => (
                  <tr key={r._id} onClick={() => nav(`/providers/${r._id}`)} className="cursor-pointer border-b border-line/50 transition hover:bg-surface-2">
                    <td className="tnum px-5 py-2.5 text-xs text-muted">{r.rank}</td>
                    <td className="py-2.5">
                      <div className="flex items-center gap-1.5 font-medium text-ink">{r.name}{r.ring_id && <Badge tone="critical">{r.ring_id}</Badge>}{r.case_status && <Badge tone="accent">{r.case_status.replace("_", " ")}</Badge>}</div>
                      <div className="text-xs text-muted">{r.specialty} · {r.metro} · {r._id}</div>
                    </td>
                    <td className="py-2.5"><RiskBadge risk={r.risk} /></td>
                    <td className="py-2.5"><ScoreBar score={r.score} color={scoreColor(t, r.score)} /></td>
                    <td className="tnum py-2.5 text-right text-stat">{Math.round(r.score_statistical)}</td>
                    <td className="tnum py-2.5 text-right text-graph">{Math.round(r.score_graph)}</td>
                    <td className="tnum py-2.5 text-right text-ink-2">{fmtInt(r.claims)}</td>
                    <td className="tnum py-2.5 text-right text-ink-2">{fmtMoney(r.total_billed, true)}</td>
                    <td className="max-w-[320px] truncate py-2.5 pl-4 text-xs text-ink-2">{r.reasons?.[0] ?? "—"}</td>
                    {reveal && (
                      <td className="px-5 py-2.5">
                        {r.truth?.length ? <div className="flex flex-wrap gap-1">{r.truth.map((x: string) => <Badge key={x} tone="graph">{FRAUD_TYPE_LABELS[x] ?? x}</Badge>)}</div> : <span className="text-xs text-muted">legitimate</span>}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
