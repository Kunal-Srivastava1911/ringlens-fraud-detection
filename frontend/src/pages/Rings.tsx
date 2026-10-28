import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { RingOrbit } from "../components/RingOrbit";
import { Badge, Empty, PageHeader, Panel, Segmented, Skeleton } from "../components/ui";
import { api, type Json, qs } from "../lib/api";
import { fmtDate, fmtMoney } from "../lib/format";
import { useUi } from "../lib/store";

export default function Rings() {
  const reveal = useUi((s) => s.reveal);
  const [view, setView] = useState<"detected" | "all">("detected");
  const nav = useNavigate();
  const { data: rings, isLoading } = useQuery({
    queryKey: ["rings", view === "all", reveal],
    queryFn: () => api.get(`/api/rings${qs({ include_candidates: view === "all", reveal })}`),
  });
  const { data: scores } = useQuery({ queryKey: ["providers-all"], queryFn: () => api.get("/api/providers?limit=2000") });
  const byNpi = useMemo(() => Object.fromEntries((scores?.rows ?? []).map((r: Json) => [r._id, r])), [scores]);

  return (
    <div>
      <PageHeader
        eyebrow="Neo4j · strongly connected components"
        title="Referral rings"
        subtitle="Candidates are SCCs of 3+ providers on the strong referral graph (edges repeated across several patients). Each is scored on how patients actually move: rings recirculate the same patients; legitimate co-managing specialists refer each patient once."
        actions={<Segmented value={view} onChange={setView} options={[{ value: "detected", label: "Detected rings" }, { value: "all", label: "All candidates" }]} />}
      />
      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-96" />)}</div>
      ) : !rings?.length ? (
        <Empty>No rings detected.</Empty>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {rings.map((r: Json) => (
            <Panel key={r._id} pad={false} className={r.detected ? "" : "opacity-75"}>
              <button onClick={() => nav(`/rings/${r._id}`)} className="block w-full p-4 text-left">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm font-semibold text-ink">{r._id}</span>
                    {r.detected ? <Badge tone="critical">ring</Badge> : <Badge>candidate · below threshold</Badge>}
                    {r.status && r.status !== "new" && <Badge tone="accent">{r.status.replace("_", " ")}</Badge>}
                  </div>
                  <span className="tnum text-sm font-semibold text-ink">{r.suspicion.toFixed(2)}</span>
                </div>
                {reveal && r.truth_match && (
                  <div className="mt-1.5 flex items-center gap-1.5 text-[11px]">
                    {r.truth_match.jaccard >= 0.5
                      ? <><CheckCircle2 size={12} className="text-good-ink" /><span className="text-good-ink">matches planted {r.truth_match.ring} (Jaccard {r.truth_match.jaccard})</span></>
                      : <><CircleDashed size={12} className="text-muted" /><span className="text-muted">no planted ring: legitimate loop</span></>}
                  </div>
                )}
                <RingOrbit compact height={210} hub={r.hub} flows={r.flows}
                  members={r.members.map((m: string) => ({ npi: m, name: byNpi[m]?.name, specialty: byNpi[m]?.specialty, score: byNpi[m]?.score }))} />
                <div className="grid grid-cols-4 gap-2 border-t border-line pt-3 text-[11px]">
                  <M label="providers" v={r.size} />
                  <M label="recirculation" v={`${Math.round(r.metrics.recirculation * 100)}%`} />
                  <M label="circulated" v={fmtMoney(r.internal_amount, true)} />
                  <M label="patients" v={r.patients} />
                </div>
                <div className="mt-2 text-[11px] text-muted">{r.metro ?? r.member_info?.[0]?.metro} · active {fmtDate(r.first_activity)} → {fmtDate(r.last_activity)}</div>
              </button>
            </Panel>
          ))}
        </div>
      )}
    </div>
  );
}

function M({ label, v }: { label: string; v: string | number }) {
  return <div><div className="tnum font-semibold text-ink">{v}</div><div className="text-muted">{label}</div></div>;
}
