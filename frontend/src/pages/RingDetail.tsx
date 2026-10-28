import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, CheckCircle2, Landmark, MapPin, ShieldCheck, ShieldX } from "lucide-react";
import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { axisStyle, Chart } from "../components/Chart";
import { RingOrbit } from "../components/RingOrbit";
import { Badge, Button, PageHeader, Panel, RiskBadge, ScoreBar, Skeleton } from "../components/ui";
import { api, type Json, qs } from "../lib/api";
import { fmtDate, fmtDateTime, fmtInt, fmtMoney } from "../lib/format";
import { useUi } from "../lib/store";
import { alpha, scoreColor, type Tokens, useTokens } from "../lib/theme";

const METRICS: [string, string, string][] = [
  ["recirculation", "Patient recirculation", "share of the loop's patients who visited 3+ members"],
  ["compactness", "Temporal compactness", "how quickly patients move from hop to hop"],
  ["internal_share", "Internal flow share", "members' referral volume that stays inside the loop"],
  ["density", "Edge density", "share of possible member-to-member edges present"],
  ["reciprocity", "Reciprocity", "edges that also flow back"],
  ["shared_bank", "Shared bank accounts", "members paid into an account another member uses"],
];

export default function RingDetail() {
  const { id } = useParams();
  const reveal = useUi((s) => s.reveal);
  const nav = useNavigate();
  const t = useTokens();
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["ring", id, reveal], queryFn: () => api.get(`/api/rings/${id}${qs({ reveal })}`) });
  const [note, setNote] = useState("");
  const decide = useMutation({
    mutationFn: (status: string) => api.post(`/api/rings/${id}/decision`, { status, note, analyst: "Analyst" }),
    onSuccess: (r) => {
      toast.success(`Ring ${r.ring} marked ${r.status.replace("_", " ")}`, { description: `${r.documents_written} documents committed in one transaction · ${r.transaction_ms} ms` });
      qc.invalidateQueries({ queryKey: ["ring", id] });
      setNote("");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const radar = useCallback((tk: Tokens) => ({
    radar: {
      indicator: METRICS.map(([, label]) => ({ name: label, max: 1 })),
      radius: "66%",
      axisName: { color: tk.ink2, fontSize: 11 },
      splitLine: { lineStyle: { color: tk.grid } },
      splitArea: { areaStyle: { color: ["transparent"] } },
      axisLine: { lineStyle: { color: tk.grid } },
    },
    tooltip: {},
    series: [{
      type: "radar" as const, symbolSize: 6,
      data: [{ name: data?.ring?._id, value: METRICS.map(([k]) => Math.min(1, k === "internal_share" ? data?.ring?.metrics[k] / 0.3 : data?.ring?.metrics[k] ?? 0)) }],
      lineStyle: { color: tk.critical, width: 2 }, itemStyle: { color: tk.critical }, areaStyle: { color: alpha(tk.critical, 0.25) },
    }],
  }), [data]);

  const timeline = useCallback((tk: Tokens) => ({
    grid: { left: 40, right: 10, top: 14, bottom: 28 },
    tooltip: { trigger: "axis" as const },
    xAxis: { type: "category" as const, data: (data?.timeline ?? []).map((w: Json) => w.week), ...axisStyle(tk), splitLine: { show: false } },
    yAxis: { type: "value" as const, ...axisStyle(tk), minInterval: 1 },
    series: [{ name: "Internal referral claims", type: "bar" as const, data: (data?.timeline ?? []).map((w: Json) => w.claims), itemStyle: { color: tk.critical, borderRadius: [3, 3, 0, 0] } }],
  }), [data]);

  if (isLoading || !data) return <Skeleton className="h-[600px]" />;
  const r = data.ring;
  const byNpi = Object.fromEntries(data.members.map((m: Json) => [m._id, m]));
  const name = (n: string) => byNpi[n]?.name ?? n;
  const members = r.members.map((m: string) => ({ npi: m, name: byNpi[m]?.name, specialty: byNpi[m]?.specialty, score: byNpi[m]?.score }));

  return (
    <div className="space-y-4">
      <Link to="/rings" className="inline-flex items-center gap-1 text-xs text-muted hover:text-ink"><ArrowLeft size={13} /> All rings</Link>
      <PageHeader
        eyebrow={`Ring dossier · ${r.detected ? "detected" : "candidate"}`}
        title={<span className="flex items-center gap-3"><span className="font-mono">{r._id}</span><Badge tone="critical" className="text-xs">suspicion {r.suspicion.toFixed(2)}</Badge></span>}
        subtitle={`${r.size} providers in ${r.member_info?.[0]?.metro} referring ${fmtInt(r.patients)} patients around a closed loop: ${fmtInt(r.internal_referrals)} internal referrals worth ${fmtMoney(r.internal_amount)}, ${fmtDate(r.first_activity)} → ${fmtDate(r.last_activity)}.`}
        actions={reveal && data.truth ? (
          data.truth.jaccard >= 0.5
            ? <Badge tone="good" className="px-2.5 py-1 text-xs"><CheckCircle2 size={13} /> Planted ring {data.truth.ring._id} · Jaccard {data.truth.jaccard}{data.truth.ring.camouflaged ? " · camouflaged" : ""}</Badge>
            : <Badge className="px-2.5 py-1 text-xs">Not a planted ring</Badge>
        ) : undefined}
      />

      <div className="grid gap-4 xl:grid-cols-5">
        <Panel className="xl:col-span-3" title="Referral loop" subtitle="Arrow width = referral volume · particles = patients and money in motion · click a provider to open it">
          <RingOrbit members={members} flows={r.flows} hub={r.hub} height={460} onSelect={(n) => nav(`/providers/${n}`)} />
        </Panel>
        <div className="space-y-4 xl:col-span-2">
          <Panel title="Ring analysis" subtitle="What separates this loop from legitimate co-management">
            <Chart option={radar} height={250} />
            <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
              {METRICS.map(([k, label, help]) => (
                <div key={k} title={help}>
                  <dt className="text-muted">{label}</dt>
                  <dd className="tnum font-semibold text-ink">{(r.metrics[k] * 100).toFixed(0)}%</dd>
                </div>
              ))}
              <div><dt className="text-muted">Median gap between hops</dt><dd className="tnum font-semibold text-ink">{r.metrics.median_gap_days ?? "—"} days</dd></div>
              <div><dt className="text-muted">Referrals per patient</dt><dd className="tnum font-semibold text-ink">{r.metrics.referrals_per_patient}</dd></div>
            </dl>
          </Panel>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Weekly activity" subtitle="Claims referred between members">
          <Chart option={timeline} height={220} />
        </Panel>
        <Panel title="Heaviest flows" subtitle="Member → member">
          <ul className="space-y-2">
            {r.flows.slice(0, 7).map((f: Json) => {
              const max = r.flows[0].count;
              return (
                <li key={f.source + f.target} className="text-xs">
                  <div className="flex items-center gap-1.5 text-ink-2">
                    <span className="truncate">{name(f.source)}</span><ArrowRight size={11} className="shrink-0 text-critical" /><span className="truncate">{name(f.target)}</span>
                    <span className="tnum ml-auto shrink-0 font-semibold text-ink">{f.count} · {fmtMoney(f.amount, true)}</span>
                  </div>
                  <div className="mt-1 h-1.5 rounded-full bg-surface-3"><div className="h-full rounded-full bg-critical" style={{ width: `${(f.count / max) * 100}%` }} /></div>
                </li>
              );
            })}
          </ul>
        </Panel>
        <Panel title="Patient journeys" subtitle="The same member travelling the loop">
          <ul className="space-y-3">
            {data.journeys.slice(0, 5).map((j: Json) => (
              <li key={j._id} className="text-xs">
                <div className="mb-1 flex items-center justify-between"><span className="font-mono text-ink">{j._id}</span><span className="text-muted">{j.n} hops</span></div>
                <div className="flex flex-wrap items-center gap-1 text-[11px]">
                  {j.hops.slice(0, 8).map((h: Json, i: number) => (
                    <span key={i} className="flex items-center gap-1">
                      {i === 0 && <span className="rounded bg-surface-3 px-1.5 py-0.5 text-ink-2">{name(h.from).replace("Dr. ", "").split(" ").slice(-1)[0]}</span>}
                      <ArrowRight size={10} className="text-critical" />
                      <span className="rounded bg-surface-3 px-1.5 py-0.5 text-ink-2" title={fmtDate(h.date)}>{name(h.to).replace("Dr. ", "").split(" ").slice(-1)[0]}</span>
                    </span>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" title="Members" pad={false}>
          <table className="w-full text-[13px]">
            <thead><tr className="border-b border-line text-left text-[11px] uppercase tracking-wider text-muted">
              <th className="px-5 py-2 font-medium">Provider</th><th className="px-2 py-2 font-medium">Identity</th><th className="px-2 py-2 font-medium">Risk</th><th className="px-5 py-2 font-medium">Score</th></tr></thead>
            <tbody>
              {r.member_info.map((m: Json) => {
                const s = byNpi[m.npi];
                return (
                  <tr key={m.npi} onClick={() => nav(`/providers/${m.npi}`)} className="cursor-pointer border-b border-line/60 transition hover:bg-surface-2">
                    <td className="px-5 py-2.5"><div className="font-medium text-ink">{m.name} {m.npi === r.hub && <Badge tone="graph">hub</Badge>}</div><div className="text-xs text-muted">{m.specialty} · {m.npi}</div></td>
                    <td className="px-2 py-2.5"><div className="flex gap-1">{m.shares_bank && <Badge tone="critical"><Landmark size={10} />bank</Badge>}{m.shares_address && <Badge><MapPin size={10} />address</Badge>}</div></td>
                    <td className="px-2 py-2.5">{s && <RiskBadge risk={s.risk} />}</td>
                    <td className="px-5 py-2.5">{s && <ScoreBar score={s.score} color={scoreColor(t, s.score)} />}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Panel>
        <Panel title="Case decision" subtitle="Ring + every member's case + audit log commit atomically (multi-document ACID transaction, w: majority)">
          <div className="mb-3 text-xs text-ink-2">Current status: <b className="text-ink">{(r.status ?? "new").replace("_", " ")}</b></div>
          <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note for the audit log…" rows={3} className="w-full resize-none rounded-lg border border-line bg-surface-2 p-2.5 text-[13px] text-ink outline-none focus:border-accent" />
          <div className="mt-3 grid grid-cols-2 gap-2">
            <Button variant="danger" onClick={() => decide.mutate("confirmed_fraud")} disabled={decide.isPending}><ShieldX size={14} />Confirm fraud</Button>
            <Button onClick={() => decide.mutate("cleared")} disabled={decide.isPending}><ShieldCheck size={14} />Clear</Button>
            <Button variant="ghost" className="col-span-2" onClick={() => decide.mutate("investigating")} disabled={decide.isPending}>Mark investigating</Button>
          </div>
          <ul className="mt-4 space-y-2 border-t border-line pt-3">
            {(data.audit ?? []).map((a: Json) => (
              <li key={a._id} className="text-xs"><span className="text-muted">{fmtDateTime(a.at)}</span> · <b className="text-ink">{a.to?.replace("_", " ")}</b>{a.note && <span className="text-ink-2"> · {a.note}</span>}</li>
            ))}
          </ul>
        </Panel>
      </div>
    </div>
  );
}
