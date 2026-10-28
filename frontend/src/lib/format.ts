export const fmtInt = (n: number | null | undefined) => (n ?? 0).toLocaleString("en-US");

export function fmtMoney(n: number | null | undefined, compact = false): string {
  const v = n ?? 0;
  if (compact && Math.abs(v) >= 1000) {
    return "$" + new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v);
  }
  return "$" + Math.round(v).toLocaleString("en-US");
}

export const fmtCompact = (n: number | null | undefined) =>
  new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n ?? 0);

export const fmtPct = (n: number | null | undefined, digits = 0) => `${((n ?? 0) * 100).toFixed(digits)}%`;

export function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  return new Date(s).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function fmtDateTime(s: string | null | undefined): string {
  if (!s) return "—";
  return new Date(s).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function timeAgo(s: string | null | undefined): string {
  if (!s) return "never";
  const sec = (Date.now() - new Date(s).getTime()) / 1000;
  if (sec < 5) return "just now";
  if (sec < 60) return `${Math.round(sec)}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}

export const SIGNAL_LABELS: Record<string, string> = {
  ring: "Referral ring",
  centrality: "Network centrality",
  shared_patients: "Shared patients",
  billing: "Peer-relative billing",
  upcoding: "Upcoding",
  velocity: "Billing velocity",
  phantom: "Services after death",
  claim_integrity: "Duplicates / unbundling",
  shared_identity: "Shared identity",
};
export const GRAPH_SIGNALS = new Set(["ring", "centrality", "shared_patients"]);

export const FRAUD_TYPE_LABELS: Record<string, string> = {
  referral_ring: "Referral ring",
  broker: "Broker",
  patient_sharing: "Patient sharing",
  upcoding: "Upcoding",
  phantom_billing: "Phantom billing",
  unbundling: "Unbundling",
  duplicate_billing: "Duplicate billing",
  shell_identity: "Shell identity",
};
