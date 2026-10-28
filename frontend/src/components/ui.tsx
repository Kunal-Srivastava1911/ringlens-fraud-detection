import clsx from "clsx";
import { AlertOctagon, AlertTriangle, CircleDot, Flame, type LucideIcon } from "lucide-react";
import { animate, motion, useMotionValue, useTransform } from "motion/react";
import { type ReactNode, useEffect } from "react";

export function Panel({ children, className, title, subtitle, actions, pad = true }: {
  children: ReactNode; className?: string; title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; pad?: boolean;
}) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      className={clsx("panel relative min-w-0", className)}
    >
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 px-5 pt-4">
          <div className="min-w-0">
            {title && <h2 className="text-[13px] font-semibold tracking-wide text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={clsx(pad && "p-5", pad && (title || actions) && "pt-3")}>{children}</div>
    </motion.section>
  );
}

export function CountUp({ value, format = (n) => Math.round(n).toLocaleString("en-US") }: {
  value: number; format?: (n: number) => string;
}) {
  const mv = useMotionValue(0);
  const text = useTransform(mv, (v) => format(v));
  useEffect(() => {
    const c = animate(mv, value, { duration: 1.1, ease: [0.16, 1, 0.3, 1] });
    return () => c.stop();
  }, [mv, value]);
  return <motion.span className="tnum">{text}</motion.span>;
}

export function Kpi({ label, value, format, sub, icon: Icon, tone = "accent", glow }: {
  label: string; value: number; format?: (n: number) => string; sub?: ReactNode; icon: LucideIcon;
  tone?: "accent" | "critical" | "violet" | "graph" | "fused"; glow?: boolean;
}) {
  const toneClass = {
    accent: "text-accent bg-accent-soft",
    critical: "text-critical bg-[color-mix(in_srgb,var(--critical)_14%,transparent)]",
    violet: "text-violet bg-[color-mix(in_srgb,var(--violet)_14%,transparent)]",
    graph: "text-graph bg-[color-mix(in_srgb,var(--s-graph)_14%,transparent)]",
    fused: "text-fused bg-[color-mix(in_srgb,var(--s-fused)_14%,transparent)]",
  }[tone];
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className={clsx("panel flex flex-col gap-3 p-4", glow && "glow-critical")}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-ink-2">{label}</span>
        <span className={clsx("grid size-8 place-items-center rounded-lg", toneClass)}>
          <Icon size={16} />
        </span>
      </div>
      <div className="text-[26px] font-semibold leading-none tracking-tight text-ink">
        <CountUp value={value} format={format} />
      </div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </motion.div>
  );
}

const RISK_META: Record<string, { label: string; icon: LucideIcon; cls: string }> = {
  critical: { label: "Critical", icon: Flame, cls: "text-critical border-[color-mix(in_srgb,var(--critical)_45%,transparent)] bg-[color-mix(in_srgb,var(--critical)_12%,transparent)]" },
  high: { label: "High", icon: AlertOctagon, cls: "text-serious border-[color-mix(in_srgb,var(--serious)_45%,transparent)] bg-[color-mix(in_srgb,var(--serious)_12%,transparent)]" },
  elevated: { label: "Elevated", icon: AlertTriangle, cls: "text-warning border-[color-mix(in_srgb,var(--warning)_45%,transparent)] bg-[color-mix(in_srgb,var(--warning)_10%,transparent)]" },
  low: { label: "Low", icon: CircleDot, cls: "text-muted border-line bg-surface-2" },
};

/** Status colour never carries meaning alone: every badge has an icon and a label. */
export function RiskBadge({ risk, className }: { risk: string; className?: string }) {
  const m = RISK_META[risk] ?? RISK_META.low;
  const Icon = m.icon;
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold", m.cls, className)}>
      <Icon size={11} strokeWidth={2.5} />
      {m.label}
    </span>
  );
}

export function Badge({ children, className, tone = "neutral" }: {
  children: ReactNode; className?: string; tone?: "neutral" | "accent" | "stat" | "graph" | "fused" | "critical" | "good";
}) {
  const cls = {
    neutral: "border-line bg-surface-2 text-ink-2",
    accent: "border-transparent bg-accent-soft text-accent",
    stat: "border-transparent bg-[color-mix(in_srgb,var(--s-stat)_15%,transparent)] text-stat",
    graph: "border-transparent bg-[color-mix(in_srgb,var(--s-graph)_15%,transparent)] text-graph",
    fused: "border-transparent bg-[color-mix(in_srgb,var(--s-fused)_15%,transparent)] text-fused",
    critical: "border-transparent bg-[color-mix(in_srgb,var(--critical)_15%,transparent)] text-critical",
    good: "border-transparent bg-[color-mix(in_srgb,var(--good)_15%,transparent)] text-good-ink",
  }[tone];
  return <span className={clsx("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium", cls, className)}>{children}</span>;
}

export function Button({ children, onClick, variant = "secondary", size = "md", disabled, className, type = "button", title }: {
  children: ReactNode; onClick?: () => void; variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md"; disabled?: boolean; className?: string; type?: "button" | "submit"; title?: string;
}) {
  const v = {
    primary: "bg-accent text-white hover:brightness-110 shadow-[0_6px_20px_-8px_var(--accent)]",
    secondary: "bg-surface-2 text-ink border border-line hover:bg-surface-3",
    ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
    danger: "bg-critical text-white hover:brightness-110 shadow-[0_6px_20px_-8px_var(--critical)]",
  }[variant];
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-[13px]",
        v,
        className,
      )}
    >
      {children}
    </button>
  );
}

export function Segmented<T extends string>({ value, options, onChange, size = "md" }: {
  value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void; size?: "sm" | "md";
}) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-surface-2 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={clsx(
            "relative rounded-md font-medium transition",
            size === "sm" ? "px-2 py-0.5 text-[11px]" : "px-3 py-1 text-xs",
            value === o.value ? "text-ink" : "text-muted hover:text-ink-2",
          )}
        >
          {value === o.value && (
            <motion.span layoutId={`seg-${options.map((x) => x.value).join()}`} className="absolute inset-0 rounded-md bg-surface shadow-sm" transition={{ type: "spring", duration: 0.35 }} />
          )}
          <span className="relative">{o.label}</span>
        </button>
      ))}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("shimmer rounded-lg", className)} />;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="grid place-items-center py-10 text-sm text-muted">{children}</div>;
}

export function ScoreBar({ score, color }: { score: number; color: string }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-surface-3">
        <motion.div className="h-full rounded-full" style={{ background: color }} initial={{ width: 0 }} animate={{ width: `${Math.min(100, score)}%` }} transition={{ duration: 0.8, ease: "easeOut" }} />
      </div>
      <span className="tnum w-7 text-right text-xs font-semibold text-ink">{Math.round(score)}</span>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions, eyebrow }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">{eyebrow}</div>}
        <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-ink-2">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={clsx("font-mono text-[12px]", className)}>{children}</span>;
}

export function JsonView({ value, highlight }: { value: unknown; highlight?: string[] }) {
  const text = JSON.stringify(value, null, 2);
  const lines = text.split("\n");
  return (
    <pre className="max-h-[520px] overflow-auto rounded-xl border border-line bg-surface-2 p-4 font-mono text-[11.5px] leading-relaxed">
      {lines.map((line, i) => {
        const key = line.match(/^\s*"([^"]+)":/)?.[1];
        const hot = key && highlight?.includes(key);
        return (
          <div key={i} className={clsx(hot && "-mx-4 bg-accent-soft px-4")}>
            {colorize(line)}
          </div>
        );
      })}
    </pre>
  );
}

function colorize(line: string): ReactNode {
  const m = line.match(/^(\s*)("[^"]+")(:\s*)(.*)$/);
  if (!m) return <span className="text-ink-2">{line}</span>;
  const [, ws, key, colon, rest] = m;
  const isStr = rest.startsWith('"');
  const isNum = /^-?\d/.test(rest);
  return (
    <>
      {ws}
      <span className="text-accent">{key}</span>
      <span className="text-muted">{colon}</span>
      <span className={isStr ? "text-fused" : isNum ? "text-graph" : "text-ink-2"}>{rest}</span>
    </>
  );
}
