import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Command } from "cmdk";
import {
  Activity, Boxes, Database, Eye, EyeOff, FileText, FlaskConical, LayoutDashboard, Moon, Network, Search, Sparkles,
  Sun, Users, Waypoints,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type ReactNode, useEffect, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { api, qs } from "../lib/api";
import { useLive, useUi } from "../lib/store";

const NAV = [
  { to: "/", label: "Command Center", icon: LayoutDashboard },
  { to: "/network", label: "Network", icon: Network },
  { to: "/rings", label: "Rings", icon: Waypoints },
  { to: "/providers", label: "Providers", icon: Users },
  { to: "/claims", label: "Claims", icon: FileText },
  { to: "/pipeline", label: "Pipeline", icon: Activity },
  { to: "/lab", label: "Evaluation Lab", icon: FlaskConical },
  { to: "/internals", label: "DB Internals", icon: Database },
];

export function Shell({ children }: { children: ReactNode }) {
  const loc = useLocation();
  const fullBleed = loc.pathname === "/network";
  return (
    <div className="relative flex h-full">
      <div className="backdrop" />
      <Sidebar />
      <div className="relative z-10 flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main className={clsx("min-h-0 flex-1", fullBleed ? "overflow-hidden" : "overflow-y-auto")}>
          <AnimatePresence mode="wait">
            <motion.div
              key={loc.pathname}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className={clsx(fullBleed ? "h-full" : "mx-auto max-w-[1520px] px-4 py-6 sm:px-6 lg:px-8")}
            >
              {children}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
      <CommandPalette />
    </div>
  );
}

function Sidebar() {
  return (
    <aside className="relative z-20 hidden w-[232px] shrink-0 flex-col border-r border-line bg-[var(--glass)] backdrop-blur-xl md:flex">
      <div className="flex items-center gap-2.5 px-5 pb-6 pt-5">
        <Logo />
        <div>
          <div className="text-[15px] font-semibold tracking-tight text-ink">RingLens</div>
          <div className="text-[10.5px] text-muted">Fraud intelligence console</div>
        </div>
      </div>
      <nav className="flex flex-col gap-0.5 px-3">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              clsx(
                "group relative flex items-center gap-3 rounded-lg px-3 py-2 text-[13px] font-medium transition",
                isActive ? "text-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
              )
            }
          >
            {({ isActive }) => (
              <>
                {isActive && (
                  <motion.span layoutId="nav-active" className="absolute inset-0 rounded-lg border border-line bg-surface shadow-sm" transition={{ type: "spring", duration: 0.4 }} />
                )}
                {isActive && <span className="absolute left-0 top-2 bottom-2 w-[3px] rounded-full bg-accent" />}
                <Icon size={16} className={clsx("relative", isActive ? "text-accent" : "text-muted group-hover:text-ink-2")} />
                <span className="relative">{label}</span>
              </>
            )}
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto space-y-2 p-4">
        <StackCard />
      </div>
    </aside>
  );
}

function StackCard() {
  const { data } = useQuery({ queryKey: ["health"], queryFn: () => api.get("/api/health"), refetchInterval: 15000 });
  const row = (ok: boolean, label: string, detail?: string) => (
    <div className="flex items-center gap-2 text-[11px]">
      <span className={clsx("size-1.5 rounded-full", ok ? "bg-good" : "bg-critical")} />
      <span className="text-ink-2">{label}</span>
      <span className="ml-auto truncate text-muted">{detail}</span>
    </div>
  );
  return (
    <div className="rounded-xl border border-line bg-surface-2/60 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-muted">
        <Boxes size={12} /> Polyglot stack
      </div>
      <div className="space-y-1.5">
        {row(!!data?.mongo, "MongoDB 8.0", data?.mongo_topology)}
        {row(!!data?.neo4j, "Neo4j + GDS", data?.gds ? `GDS ${data.gds}` : undefined)}
      </div>
    </div>
  );
}

export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <defs>
        <linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--accent)" />
          <stop offset="1" stopColor="var(--violet)" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill="var(--surface-2)" stroke="var(--border)" />
      <circle cx="16" cy="16" r="8.5" fill="none" stroke="url(#lg)" strokeWidth="2.2">
        <animateTransform attributeName="transform" type="rotate" from="0 16 16" to="360 16 16" dur="14s" repeatCount="indefinite" />
      </circle>
      <circle cx="16" cy="7.5" r="2.8" fill="var(--accent)" />
      <circle cx="23.4" cy="20.2" r="2.8" fill="var(--violet)" />
      <circle cx="8.6" cy="20.2" r="2.8" fill="var(--critical)" />
    </svg>
  );
}

function TopBar() {
  const { theme, setTheme, reveal, toggleReveal, setPaletteOpen } = useUi();
  const connected = useLive((s) => s.connected);
  const lastBatch = useLive((s) => s.lastBatch);
  const state = useLive((s) => s.pipelineState);
  const claimTimes = useLive((s) => s.claimTimes);
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const rate = claimTimes.filter((t) => Date.now() - t < 5000).length / 5;
  const lag = lastBatch?.batch?.lag_ms_avg;

  return (
    <header className="relative z-20 flex h-14 shrink-0 items-center gap-3 border-b border-line bg-[var(--glass)] px-4 backdrop-blur-xl sm:px-6">
      <div className="md:hidden"><Logo size={26} /></div>
      <button
        onClick={() => setPaletteOpen(true)}
        className="flex h-9 w-full max-w-md items-center gap-2 rounded-lg border border-line bg-surface-2/70 px-3 text-[13px] text-muted transition hover:border-line-strong hover:text-ink-2"
      >
        <Search size={15} />
        <span className="truncate">Search providers, rings, pages…</span>
        <kbd className="ml-auto hidden rounded border border-line bg-surface px-1.5 font-mono text-[10px] sm:inline">Ctrl K</kbd>
      </button>
      <div className="ml-auto flex items-center gap-2">
        <div className="hidden items-center gap-3 rounded-lg border border-line bg-surface-2/70 px-3 py-1.5 text-[11.5px] lg:flex">
          <span className="flex items-center gap-1.5">
            <span className={clsx("live-dot size-2 rounded-full", connected ? "bg-good text-good" : "bg-critical text-critical")} />
            <span className="font-medium text-ink-2">{connected ? "Live" : "Offline"}</span>
          </span>
          <span className="h-3 w-px bg-line" />
          <span className="text-muted">CDC <b className={clsx("font-semibold", state === "paused" || state === "crashed" ? "text-warning" : "text-ink-2")}>{state ?? "—"}</b></span>
          <span className="tnum text-muted">lag <b className="font-semibold text-ink-2">{lag != null ? `${Math.round(lag)} ms` : "—"}</b></span>
          <span className="tnum text-muted">intake <b className="font-semibold text-ink-2">{rate.toFixed(1)}/s</b></span>
        </div>
        <button
          onClick={toggleReveal}
          title="Show the planted ground truth next to detections"
          className={clsx(
            "flex h-9 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium transition",
            reveal ? "border-transparent bg-[color-mix(in_srgb,var(--violet)_18%,transparent)] text-violet" : "border-line bg-surface-2/70 text-ink-2 hover:text-ink",
          )}
        >
          {reveal ? <Eye size={15} /> : <EyeOff size={15} />}
          <span className="hidden sm:inline">Ground truth</span>
        </button>
        <button
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          className="grid size-9 place-items-center rounded-lg border border-line bg-surface-2/70 text-ink-2 transition hover:text-ink"
          title="Toggle theme"
        >
          {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
        </button>
      </div>
    </header>
  );
}

function CommandPalette() {
  const { paletteOpen, setPaletteOpen } = useUi();
  const [q, setQ] = useState("");
  const nav = useNavigate();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen(!useUi.getState().paletteOpen);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setPaletteOpen]);
  const { data: providers } = useQuery({
    queryKey: ["palette-providers", q],
    queryFn: () => api.get(`/api/providers${qs({ q, limit: 8 })}`),
    enabled: paletteOpen && q.length >= 2,
  });
  const { data: rings } = useQuery({ queryKey: ["rings", false], queryFn: () => api.get("/api/rings"), enabled: paletteOpen });
  const go = (to: string) => {
    setPaletteOpen(false);
    setQ("");
    nav(to);
  };
  return (
    <AnimatePresence>
      {paletteOpen && (
        <motion.div className="fixed inset-0 z-50 grid place-items-start justify-center bg-black/40 px-4 pt-[12vh] backdrop-blur-sm" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setPaletteOpen(false)}>
          <motion.div initial={{ scale: 0.97, y: -8 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.97 }} onClick={(e) => e.stopPropagation()} className="panel w-full max-w-xl overflow-hidden">
            <Command shouldFilter={false} className="text-sm">
              <div className="flex items-center gap-2 border-b border-line px-4">
                <Search size={16} className="text-muted" />
                <Command.Input autoFocus value={q} onValueChange={setQ} placeholder="Provider name or NPI, ring id, page…" className="h-12 w-full bg-transparent text-ink outline-none placeholder:text-muted" />
              </div>
              <Command.List className="max-h-[380px] overflow-y-auto p-2">
                <Command.Empty className="px-3 py-6 text-center text-muted">Nothing found.</Command.Empty>
                <Group heading="Pages">
                  {NAV.filter((n) => n.label.toLowerCase().includes(q.toLowerCase())).map((n) => (
                    <Item key={n.to} onSelect={() => go(n.to)} icon={<n.icon size={15} />}>{n.label}</Item>
                  ))}
                </Group>
                {(rings ?? []).filter((r: { _id: string }) => !q || r._id.toLowerCase().includes(q.toLowerCase())).slice(0, 6).length > 0 && (
                  <Group heading="Rings">
                    {(rings ?? []).filter((r: { _id: string }) => !q || r._id.toLowerCase().includes(q.toLowerCase())).slice(0, 6).map((r: { _id: string; size: number; suspicion: number }) => (
                      <Item key={r._id} onSelect={() => go(`/rings/${r._id}`)} icon={<Waypoints size={15} />}>
                        {r._id} <span className="text-muted">· {r.size} providers · suspicion {r.suspicion.toFixed(2)}</span>
                      </Item>
                    ))}
                  </Group>
                )}
                {providers?.rows?.length > 0 && (
                  <Group heading="Providers">
                    {providers.rows.map((p: { _id: string; name: string; specialty: string; score: number }) => (
                      <Item key={p._id} onSelect={() => go(`/providers/${p._id}`)} icon={<Sparkles size={15} />}>
                        {p.name} <span className="text-muted">· {p.specialty} · {p._id} · score {Math.round(p.score)}</span>
                      </Item>
                    ))}
                  </Group>
                )}
              </Command.List>
            </Command>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function Group({ heading, children }: { heading: string; children: ReactNode }) {
  return (
    <Command.Group heading={heading} className="mb-1 [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10.5px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-muted">
      {children}
    </Command.Group>
  );
}

function Item({ children, onSelect, icon }: { children: ReactNode; onSelect: () => void; icon: ReactNode }) {
  return (
    <Command.Item onSelect={onSelect} className="flex cursor-pointer items-center gap-2.5 truncate rounded-lg px-3 py-2 text-ink-2 data-[selected=true]:bg-accent-soft data-[selected=true]:text-ink">
      <span className="text-muted">{icon}</span>
      <span className="truncate">{children}</span>
    </Command.Item>
  );
}
