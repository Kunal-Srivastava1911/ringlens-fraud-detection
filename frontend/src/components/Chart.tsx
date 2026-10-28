import * as echarts from "echarts";
import { useEffect, useRef } from "react";
import type { Tokens } from "../lib/theme";
import { useTokens } from "../lib/theme";

type Option = echarts.EChartsOption;

/** ECharts wrapper: resizes with its box, rebuilds when the theme changes. */
export function Chart({
  option,
  height = 260,
  onClick,
  className,
}: {
  // Option builders return plain objects; ECharts validates them at runtime.
  option: (t: Tokens) => object;
  height?: number | string;
  onClick?: (p: echarts.ECElementEvent) => void;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);
  const t = useTokens();
  const clickRef = useRef(onClick);
  clickRef.current = onClick;

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    inst.current = chart;
    chart.on("click", (p) => clickRef.current?.(p as echarts.ECElementEvent));
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
      inst.current = null;
    };
  }, []);

  useEffect(() => {
    inst.current?.setOption({ ...base(t), ...option(t) } as Option, { notMerge: true });
  }, [option, t]);

  return <div ref={ref} className={className} style={{ height, width: "100%" }} />;
}

/** Shared chrome: recessive grid and axes, text in ink tokens, tooltips on surface. */
export function base(t: Tokens): Option {
  return {
    backgroundColor: "transparent",
    textStyle: { fontFamily: "Inter Variable, system-ui, sans-serif", color: t.ink2 },
    animationDuration: 700,
    animationEasing: "cubicOut",
    tooltip: {
      backgroundColor: t.surface,
      borderColor: t.border,
      borderWidth: 1,
      textStyle: { color: t.ink, fontSize: 12 },
      extraCssText: "border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.25);",
    },
  };
}

export function axisStyle(t: Tokens) {
  return {
    axisLine: { lineStyle: { color: t.axis } },
    axisTick: { show: false },
    axisLabel: { color: t.muted, fontSize: 11 },
    splitLine: { lineStyle: { color: t.grid } },
    nameTextStyle: { color: t.muted, fontSize: 11 },
  };
}

export function legendStyle(t: Tokens) {
  return { textStyle: { color: t.ink2, fontSize: 12 }, icon: "roundRect", itemWidth: 12, itemHeight: 8, itemGap: 16 };
}
