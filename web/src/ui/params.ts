import { DEFAULT_DEMO, PRODUCTION_SETTINGS } from "../sim";

/**
 * Run parameters read off the options this page actually runs, so a copy change cannot
 * drift from the simulation. `sim/demo.py` raises the served check share and lowers the
 * pause threshold for the demo; the production defaults sit in PRODUCTION_SETTINGS.
 */
export const RUN = {
  seed: DEFAULT_DEMO.seed,
  experts: DEFAULT_DEMO.experts,
  tasks: DEFAULT_DEMO.tasks,
  golden: Math.round(DEFAULT_DEMO.tasks * DEFAULT_DEMO.goldenShare),
  leaseSeconds: DEFAULT_DEMO.settings.leaseSeconds,
  /** One task in this many is a golden check in the full run. */
  goldenOneIn: Math.round(1 / DEFAULT_DEMO.goldenShare),
  /** One serve in this many prefers a check here. */
  serveOneIn: Math.round(1 / DEFAULT_DEMO.settings.attentionFraction),
  /** And at the production default. */
  productionServeOneIn: Math.round(1 / PRODUCTION_SETTINGS.attentionFraction),
} as const;
