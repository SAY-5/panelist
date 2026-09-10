import { createContext, ReactNode, useContext, useEffect, useState } from "react";
import { DEFAULT_DEMO, DemoSummary, runDemo } from "../sim";

export interface MeasuredRun {
  summary: DemoSummary;
  /** Wall time the port needed for the 500-task run, measured in this browser. */
  elapsedMs: number;
}

const Ctx = createContext<MeasuredRun | null>(null);

/**
 * Runs the ported demo once, after the first paint, so the hero counters show
 * numbers this page actually produced rather than numbers typed into markup.
 */
export function MeasuredRunProvider({ children }: { children: ReactNode }) {
  const [run, setRun] = useState<MeasuredRun | null>(null);

  useEffect(() => {
    const id = requestAnimationFrame(() => {
      const started = performance.now();
      const { summary } = runDemo(DEFAULT_DEMO);
      setRun({ summary, elapsedMs: performance.now() - started });
    });
    return () => cancelAnimationFrame(id);
  }, []);

  return <Ctx.Provider value={run}>{children}</Ctx.Provider>;
}

export function useMeasuredRun(): MeasuredRun | null {
  return useContext(Ctx);
}
