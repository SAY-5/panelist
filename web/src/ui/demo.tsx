import { createContext, ReactNode, useContext, useEffect, useState } from "react";
import { DEFAULT_DEMO, DemoSummary, runDemo } from "../sim";

export interface SimulatedRun {
  summary: DemoSummary;
  /** Compute time the port needed for the run. The only measured number here. */
  elapsedMs: number;
}

const Ctx = createContext<SimulatedRun | null>(null);

/**
 * Runs the ported demo once, after the first paint, so the hero counters show numbers this
 * page produced rather than numbers typed into markup. The run is a simulation with its own
 * PRNG and virtual clock: its totals are not measurements of the PostgreSQL service.
 */
export function SimulatedRunProvider({ children }: { children: ReactNode }) {
  const [run, setRun] = useState<SimulatedRun | null>(null);

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

export function useSimulatedRun(): SimulatedRun | null {
  return useContext(Ctx);
}
