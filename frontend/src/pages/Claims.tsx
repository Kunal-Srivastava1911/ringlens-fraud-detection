import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { CheckCircle2, FileJson, Send, X, XCircle } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Button, Empty, JsonView, Mono, PageHeader, Panel, Segmented, Skeleton } from "../components/ui";
import { ApiError, api, type Json, qs } from "../lib/api";
import { fmtDate, fmtMoney } from "../lib/format";
import { useUi } from "../lib/store";

const FLAGS = [
  { value: "", label: "All claims" }, { value: "duplicate", label: "Duplicate" }, { value: "unbundling", label: "Unbundling" },
  { value: "upcoding", label: "Upcoding" }, { value: "after_death", label: "After death" },
];

export default function Claims() {
  const [params, setParams] = useSearchParams();
  const [flag, setFlag] = useState("duplicate");
  const [npi, setNpi] = useState("");
  const [cpt, setCpt] = useState("");
  const open = params.get("open");
  const q = useInfiniteQuery({
    queryKey: ["claims", flag, npi, cpt],
    initialPageParam: "",
    queryFn: ({ pageParam }) => api.get(`/api/claims${qs({ flag, npi, cpt, before: pageParam || undefined, limit: 50 })}`),
    getNextPageParam: (last: Json) => last.next ?? undefined,
  });
  const rows = q.data?.pages.flatMap((p: Json) => p.rows) ?? [];
  const indexHint = flag ? "partial index partial_flagged_queue" : npi ? "ESR index esr_provider_date_charge" : cpt ? "multikey index multikey_cpt_date" : "index submitted_at / service_date";

  return (
    <div className="space-y-4">
      <PageHeader eyebrow="MongoDB · system of record" title="Claims" subtitle="Each claim is one document with its line items embedded (always read together, bounded, never independent); providers and members are referenced by id." />
      <div className="grid gap-4 xl:grid-cols-5">
        <Panel className="xl:col-span-3" pad={false}>
          <div className="flex flex-wrap items-center gap-2 border-b border-line p-4">
            <Segmented value={flag} onChange={setFlag} size="sm" options={FLAGS} />
            <input value={npi} onChange={(e) => setNpi(e.target.value.trim())} placeholder="Rendering NPI" className="h-8 w-36 rounded-lg border border-line bg-surface-2 px-2.5 text-xs text-ink outline-none focus:border-accent" />
            <input value={cpt} onChange={(e) => setCpt(e.target.value.trim())} placeholder="CPT (e.g. 99215)" className="h-8 w-32 rounded-lg border border-line bg-surface-2 px-2.5 text-xs text-ink outline-none focus:border-accent" />
            <span className="ml-auto text-[11px] text-muted">served by {indexHint}</span>
          </div>
          {q.isLoading ? <div className="space-y-2 p-4">{Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} className="h-9" />)}</div> : rows.length === 0 ? <Empty>No claims match.</Empty> : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-xs">
                <thead><tr className="border-b border-line text-left text-[10.5px] uppercase tracking-wider text-muted"><th className="px-4 py-2 font-medium">Claim</th><th className="font-medium">Service</th><th className="font-medium">Provider</th><th className="font-medium">Referred by</th><th className="font-medium">Codes</th><th className="text-right font-medium">Charge</th><th className="px-4 font-medium">Flags</th></tr></thead>
                <tbody>
                  {rows.map((c: Json) => (
                    <tr key={c._id} onClick={() => setParams({ open: c._id })} className={clsx("cursor-pointer border-b border-line/50 transition hover:bg-surface-2", open === c._id && "bg-accent-soft")}>
                      <td className="px-4 py-2"><Mono className="text-ink">{c._id.slice(4, 18)}</Mono><div className="text-[10.5px] text-muted">{c.payer.name} · {c.payer.format}</div></td>
                      <td className="text-ink-2">{fmtDate(c.service_date)}</td>
                      <td><Mono className="text-ink-2">{c.rendering_npi}</Mono><div className="text-[10.5px] text-muted">{c.rendering_specialty}</div></td>
                      <td><Mono className="text-muted">{c.referring_npi ?? "—"}</Mono></td>
                      <td className="text-ink-2">{c.line_items.map((l: Json) => l.cpt + (l.units > 1 ? `×${l.units}` : "")).join(" ")}</td>
                      <td className="tnum text-right text-ink">{fmtMoney(c.total_charge)}</td>
                      <td className="px-4"><div className="flex gap-1">{(c.flags ?? []).map((f: string) => <Badge key={f} tone="critical">{f}</Badge>)}</div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {q.hasNextPage && <div className="p-3 text-center"><Button size="sm" onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage}>Load more (keyset pagination)</Button></div>}
            </div>
          )}
        </Panel>
        <div className="xl:col-span-2"><SubmitClaim /></div>
      </div>
      <AnimatePresence>{open && <ClaimDrawer id={open} onClose={() => setParams({})} />}</AnimatePresence>
    </div>
  );
}

function ClaimDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const reveal = useUi((s) => s.reveal);
  const { data } = useQuery({ queryKey: ["claim", id, reveal], queryFn: () => api.get(`/api/claims/${id}${qs({ reveal })}`) });
  const [view, setView] = useState<"doc" | "source">("doc");
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <>
      <motion.div className="fixed inset-0 z-40 bg-black/40 backdrop-blur-[2px]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose} />
      <motion.aside initial={{ x: "100%" }} animate={{ x: 0 }} exit={{ x: "100%" }} transition={{ type: "spring", damping: 30, stiffness: 260 }}
        className="fixed bottom-0 right-0 top-0 z-50 w-full max-w-[640px] overflow-y-auto border-l border-line bg-surface p-6 shadow-2xl">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">Claim document</div>
            <Mono className="text-sm font-semibold text-ink">{id}</Mono>
          </div>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        {!data ? <Skeleton className="h-96" /> : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 text-xs">
              {(["rendering_npi", "referring_npi"] as const).map((k) => data.claim[k] && (
                <Link key={k} to={`/providers/${data.claim[k]}`} className="rounded-lg border border-line bg-surface-2 p-3 hover:border-accent">
                  <div className="text-muted">{k === "rendering_npi" ? "Rendering provider" : "Referred by"}</div>
                  <div className="font-medium text-ink">{data.providers[data.claim[k]]?.name}</div>
                  <div className="text-muted">{data.providers[data.claim[k]]?.specialty}</div>
                </Link>
              ))}
            </div>
            {reveal && data.truth && <Badge tone="graph">planted fraud: {data.truth.type}</Badge>}
            <div className="flex items-center justify-between">
              <Segmented value={view} onChange={setView} size="sm" options={[{ value: "doc", label: "Stored document" }, { value: "source", label: "Raw payer payload" }]} />
              <span className="text-[11px] text-muted">highlighted: embedded line items</span>
            </div>
            {view === "doc" ? <JsonView value={data.claim} highlight={["line_items"]} /> : data.source ? (
              typeof data.source.payload === "string"
                ? <pre className="max-h-[520px] overflow-auto rounded-xl border border-line bg-surface-2 p-4 font-mono text-[11.5px] leading-relaxed text-graph">{data.source.payload}</pre>
                : <JsonView value={data.source.payload} />
            ) : <Empty>This claim was bulk-seeded; only live-intake claims keep their raw payload (24 h TTL in claims_staging).</Empty>}
          </div>
        )}
      </motion.aside>
    </>
  );
}

function SubmitClaim() {
  const [fmt, setFmt] = useState<"json-a" | "json-b" | "837P">("json-a");
  const [text, setText] = useState("");
  const [payer, setPayer] = useState("PYR-01");
  const [result, setResult] = useState<Json>(null);
  const sample = useQuery({ queryKey: ["sample", fmt], queryFn: () => api.get(`/api/claims-sample/${fmt}`) });
  useEffect(() => {
    if (sample.data) {
      setPayer(sample.data.payer_id);
      setText(typeof sample.data.payload === "string" ? sample.data.payload : JSON.stringify(sample.data.payload, null, 2));
      setResult(null);
    }
  }, [sample.data]);
  const submit = useMutation({
    mutationFn: () => {
      let payload: Json = text;
      if (fmt !== "837P") payload = JSON.parse(text);
      return api.post("/api/claims", { format: fmt, payer_id: payer, payload });
    },
    onSuccess: (r) => setResult(r),
    onError: (e: Error) => setResult(e instanceof ApiError ? e.body : { accepted: false, error: e.message }),
  });
  const breakIt = () => {
    if (fmt === "837P") setText(text.replace(/SV1\*HC:(\w+)([^*]*)\*([\d.]+)\*UN\*\d+/, "SV1*HC:$1$2*$3*UN*0").replace(/NM1\*82\*1\*RENDERING\*{5}XX\*\d+/, "NM1*82*1*RENDERING*****XX*12345"));
    else {
      try {
        const o = JSON.parse(text);
        if (o.services) { o.services[0].units = 0; o.provider.rendering = "12345"; }
        if (o.lines) { o.lines[0].qty = 0; o.rendering_npi = "12345"; }
        setText(JSON.stringify(o, null, 2));
      } catch { /* leave as typed */ }
    }
  };
  return (
    <Panel title="Submit a claim" subtitle="Lands in claims_staging (TTL), is normalized from the payer's format, then MongoDB's $jsonSchema validator decides.">
      <div className="flex flex-wrap items-center gap-2">
        <Segmented value={fmt} onChange={setFmt} size="sm" options={[{ value: "json-a", label: "BlueRiver JSON" }, { value: "json-b", label: "Summit JSON" }, { value: "837P", label: "X12 837P" }]} />
        <Button size="sm" variant="ghost" onClick={breakIt}>Break it</Button>
      </div>
      <textarea value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} rows={14}
        className={clsx("mt-3 w-full resize-y rounded-lg border border-line bg-surface-2 p-3 font-mono text-[11.5px] leading-relaxed outline-none focus:border-accent", fmt === "837P" ? "text-graph" : "text-ink-2")} />
      <Button variant="primary" className="mt-2 w-full" onClick={() => submit.mutate()} disabled={submit.isPending}><Send size={14} />Submit to intake</Button>
      <AnimatePresence mode="wait">
        {result && (
          <motion.div key={JSON.stringify(result).length} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="mt-4">
            {result.accepted ? (
              <div className="rounded-lg border border-[color-mix(in_srgb,var(--good)_40%,transparent)] bg-[color-mix(in_srgb,var(--good)_10%,transparent)] p-3 text-xs">
                <div className="flex items-center gap-1.5 font-semibold text-good-ink"><CheckCircle2 size={14} />Accepted into the system of record</div>
                <div className="mt-1 text-ink-2"><Mono>{result.claim._id}</Mono> · {fmtMoney(result.claim.total_charge)} · the CDC worker will project it into Neo4j within a second.</div>
              </div>
            ) : (
              <div className="rounded-lg border border-[color-mix(in_srgb,var(--critical)_40%,transparent)] bg-[color-mix(in_srgb,var(--critical)_8%,transparent)] p-3 text-xs">
                <div className="flex items-center gap-1.5 font-semibold text-critical"><XCircle size={14} />{result.error}</div>
                {result.validation?.problems?.length > 0 && (
                  <table className="mt-2 w-full">
                    <thead><tr className="text-left text-[10.5px] uppercase tracking-wider text-muted"><th className="py-1 font-medium">Field</th><th className="font-medium">Rule</th><th className="font-medium">Value</th><th className="font-medium">Expected</th></tr></thead>
                    <tbody>
                      {result.validation.problems.map((p: Json, i: number) => (
                        <tr key={i} className="border-t border-line/60 align-top">
                          <td className="py-1.5 pr-2"><Mono className="text-ink">{p.path}</Mono></td>
                          <td className="pr-2 text-ink-2">{p.rule}</td>
                          <td className="pr-2"><Mono className="text-critical">{JSON.stringify(p.value) ?? "—"}</Mono></td>
                          <td><Mono className="text-muted">{p.expected ? JSON.stringify(p.expected) : p.reason}</Mono></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
      <p className="mt-3 flex items-center gap-1.5 text-[11px] text-muted"><FileJson size={12} />Validation runs inside MongoDB at write time (validationLevel strict, validationAction error).</p>
    </Panel>
  );
}
