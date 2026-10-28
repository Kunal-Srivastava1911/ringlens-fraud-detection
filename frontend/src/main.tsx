import "./index.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, Suspense, lazy, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import { Shell } from "./components/Shell";
import { Skeleton } from "./components/ui";
import { connectLive } from "./lib/live";
import { useUi } from "./lib/store";

// Heavy pages (three.js, ECharts) load per route.
const CommandCenter = lazy(() => import("./pages/CommandCenter"));
const NetworkPage = lazy(() => import("./pages/Network"));
const Rings = lazy(() => import("./pages/Rings"));
const RingDetail = lazy(() => import("./pages/RingDetail"));
const Providers = lazy(() => import("./pages/Providers"));
const ProviderDetail = lazy(() => import("./pages/ProviderDetail"));
const Claims = lazy(() => import("./pages/Claims"));
const Pipeline = lazy(() => import("./pages/Pipeline"));
const Lab = lazy(() => import("./pages/Lab"));
const Internals = lazy(() => import("./pages/Internals"));

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 15_000, refetchOnWindowFocus: false, retry: 1 } },
});

function App() {
  const theme = useUi((s) => s.theme);
  useEffect(() => connectLive(queryClient), []);
  return (
    <BrowserRouter>
      <Shell>
        <Suspense fallback={<PageLoading />}>
          <Routes>
            <Route path="/" element={<CommandCenter />} />
            <Route path="/network" element={<NetworkPage />} />
            <Route path="/rings" element={<Rings />} />
            <Route path="/rings/:id" element={<RingDetail />} />
            <Route path="/providers" element={<Providers />} />
            <Route path="/providers/:npi" element={<ProviderDetail />} />
            <Route path="/claims" element={<Claims />} />
            <Route path="/pipeline" element={<Pipeline />} />
            <Route path="/lab" element={<Lab />} />
            <Route path="/internals" element={<Internals />} />
          </Routes>
        </Suspense>
      </Shell>
      <Toaster theme={theme} position="bottom-right" richColors closeButton />
    </BrowserRouter>
  );
}

function PageLoading() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-8 w-72" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-28" />)}
      </div>
      <Skeleton className="h-80" />
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
