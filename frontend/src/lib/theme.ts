import { useMemo } from "react";
import { useUi } from "./store";

/** Resolved colour tokens for canvas/WebGL/ECharts, which can't read CSS variables. */
export interface Tokens {
  page: string;
  surface: string;
  surface2: string;
  surface3: string;
  ink: string;
  ink2: string;
  muted: string;
  grid: string;
  axis: string;
  border: string;
  accent: string;
  violet: string;
  stat: string;
  graph: string;
  fused: string;
  seq: string[];
  critical: string;
  serious: string;
  warning: string;
  good: string;
  neutral: string;
  dark: boolean;
}

export function readTokens(): Tokens {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string) => cs.getPropertyValue(n).trim();
  return {
    page: v("--page"),
    surface: v("--surface"),
    surface2: v("--surface-2"),
    surface3: v("--surface-3"),
    ink: v("--ink"),
    ink2: v("--ink-2"),
    muted: v("--muted"),
    grid: v("--grid"),
    axis: v("--axis"),
    border: v("--border"),
    accent: v("--accent"),
    violet: v("--violet"),
    stat: v("--s-stat"),
    graph: v("--s-graph"),
    fused: v("--s-fused"),
    seq: [v("--seq-1"), v("--seq-3"), v("--seq-5"), v("--seq-7")],
    critical: v("--critical"),
    serious: v("--serious"),
    warning: v("--warning"),
    good: v("--good"),
    neutral: v("--neutral"),
    dark: document.documentElement.dataset.theme === "dark",
  };
}

export function useTokens(): Tokens {
  const theme = useUi((s) => s.theme);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => readTokens(), [theme]);
}

export const RISK_ORDER = ["critical", "high", "elevated", "low"] as const;
export type Risk = (typeof RISK_ORDER)[number];

export function riskColor(t: Tokens, risk: string): string {
  return risk === "critical" ? t.critical : risk === "high" ? t.serious : risk === "elevated" ? t.warning : t.neutral;
}

export function scoreColor(t: Tokens, score: number): string {
  return score >= 80 ? t.critical : score >= 65 ? t.serious : score >= 50 ? t.warning : t.neutral;
}

export function alpha(hex: string, a: number): string {
  const h = hex.replace("#", "");
  if (h.length !== 6) return hex;
  const n = parseInt(h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}
