import type { QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { fmtMoney } from "./format";
import { useLive } from "./store";

/** Connects to the API's change-stream-fed WebSocket and keeps reconnecting. */
export function connectLive(queryClient: QueryClient): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let retry = 0;
  let invalidateTimer: number | undefined;

  const scheduleRefresh = (keys: string[][], delay = 400) => {
    window.clearTimeout(invalidateTimer);
    invalidateTimer = window.setTimeout(() => keys.forEach((k) => queryClient.invalidateQueries({ queryKey: k })), delay);
  };

  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/live`);
    ws.onopen = () => {
      retry = 0;
      useLive.getState().setConnected(true);
    };
    ws.onclose = () => {
      useLive.getState().setConnected(false);
      if (!closed) window.setTimeout(open, Math.min(8000, 500 * 2 ** retry++));
    };
    ws.onmessage = (msg) => {
      const e = JSON.parse(msg.data);
      if (e.type === "hello") return;
      useLive.getState().push({ type: e.type, at: e.at * 1000, data: e.data });
      if (e.type === "ring.detected") {
        toast.error(`New referral ring detected: ${e.data._id}`, {
          description: `${e.data.size} providers · ${fmtMoney(e.data.internal_amount, true)} circulated · suspicion ${Number(e.data.suspicion).toFixed(2)}`,
          duration: 9000,
        });
      }
      if (e.type === "job.progress" && e.data.status === "done") {
        toast.success("Analytics run complete", {
          description: `${e.data.summary?.rings_detected ?? 0} rings · ${e.data.summary?.flagged ?? 0} flagged providers · ${e.data.seconds}s`,
        });
        queryClient.invalidateQueries();
      }
      if (e.type === "job.progress" && e.data.status === "failed") toast.error("Analytics run failed", { description: e.data.error });
      if (e.type === "ring.planted") {
        toast(`Ring ${e.data.ring_id} planted`, { description: `${e.data.claims} claims submitted through intake in ${e.data.metro}` });
      }
      if (e.type === "cdc.batch") scheduleRefresh([["pipeline"]], 600);
      if (e.type === "pipeline.control" || e.type === "pipeline.state") scheduleRefresh([["pipeline"]], 100);
    };
  };
  open();
  return () => {
    closed = true;
    ws?.close();
  };
}
