import { create } from "zustand";
import type { Json } from "./api";

type Theme = "dark" | "light";

function read<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : (JSON.parse(v) as T);
  } catch {
    return fallback;
  }
}
function write(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable: the preference just doesn't persist */
  }
}

interface UiState {
  theme: Theme;
  reveal: boolean; // show planted ground truth next to detections
  cinematic: boolean; // bloom + particles in the 3D views
  paletteOpen: boolean;
  setTheme: (t: Theme) => void;
  toggleReveal: () => void;
  toggleCinematic: () => void;
  setPaletteOpen: (open: boolean) => void;
}

const storedTheme = (() => {
  try {
    const t = localStorage.getItem("rl-theme");
    return t === "light" || t === "dark" ? t : "dark";
  } catch {
    return "dark";
  }
})() as Theme;

export const useUi = create<UiState>((set) => ({
  theme: storedTheme,
  reveal: read("rl-reveal", false),
  cinematic: read("rl-cinematic", true),
  paletteOpen: false,
  setTheme: (theme) => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("rl-theme", theme);
    } catch {
      /* ignore */
    }
    set({ theme });
  },
  toggleReveal: () =>
    set((s) => {
      write("rl-reveal", !s.reveal);
      return { reveal: !s.reveal };
    }),
  toggleCinematic: () =>
    set((s) => {
      write("rl-cinematic", !s.cinematic);
      return { cinematic: !s.cinematic };
    }),
  setPaletteOpen: (paletteOpen) => set({ paletteOpen }),
}));

export interface LiveEvent {
  id: number;
  type: string;
  at: number;
  data: Json;
}

interface LiveState {
  connected: boolean;
  events: LiveEvent[]; // newest first, capped
  claimTimes: number[]; // arrival times of recent claim events, for the rate meter
  lastBatch: Json | null;
  pipelineState: string | null;
  job: Json | null;
  push: (e: Omit<LiveEvent, "id">) => void;
  setConnected: (c: boolean) => void;
}

let seq = 0;
export const useLive = create<LiveState>((set) => ({
  connected: false,
  events: [],
  claimTimes: [],
  lastBatch: null,
  pipelineState: null,
  job: null,
  setConnected: (connected) => set({ connected }),
  push: (e) =>
    set((s) => {
      const ev = { ...e, id: ++seq };
      const now = Date.now();
      const patch: Partial<LiveState> = { events: [ev, ...s.events].slice(0, 250) };
      if (e.type === "claim.ingested" || e.type === "claim.rejected") {
        patch.claimTimes = [...s.claimTimes.filter((t) => now - t < 10_000), now];
      }
      if (e.type === "cdc.batch") {
        patch.lastBatch = e.data;
        patch.pipelineState = e.data?.state ?? s.pipelineState;
      }
      if (e.type === "pipeline.state") patch.pipelineState = e.data?.state ?? s.pipelineState;
      if (e.type === "job.progress") patch.job = e.data;
      return patch;
    }),
}));
