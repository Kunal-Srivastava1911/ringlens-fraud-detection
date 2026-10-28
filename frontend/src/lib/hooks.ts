import { useCallback, useEffect, useRef, useState } from "react";

/** Tracks an element's content size (for canvas/WebGL views that need explicit pixels).
 *  Returns a callback ref, so it also works for elements that mount after the first render. */
export function useSize<T extends HTMLElement>() {
  const [size, setSize] = useState({ width: 0, height: 0 });
  const observer = useRef<ResizeObserver | null>(null);
  const ref = useCallback((el: T | null) => {
    observer.current?.disconnect();
    observer.current = null;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      const { width, height } = e.contentRect;
      setSize({ width: Math.floor(width), height: Math.floor(height) });
    });
    ro.observe(el);
    observer.current = ro;
  }, []);
  return [ref, size] as const;
}

/** Re-renders every `ms` milliseconds (for relative timestamps and rate meters). */
export function useTick(ms = 1000) {
  const [, set] = useState(0);
  useEffect(() => {
    const id = setInterval(() => set((n) => n + 1), ms);
    return () => clearInterval(id);
  }, [ms]);
}
