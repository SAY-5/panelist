/** A small seeded platform for the interactive sections: 8 experts, 36 tasks, 3s leases. */

import { Clock, EPOCH_MS } from "./clock";
import { seedPlatform } from "./demo";
import { Platform } from "./platform";
import { Rng } from "./prng";
import { DEMO_SETTINGS } from "./types";
import { buildWorld, gradeFor, World } from "./world";

export interface Workbench {
  platform: Platform;
  world: World;
  rng: Rng;
  seed: number;
}

export function createWorkbench(seed = 3): Workbench {
  const clock = new Clock(EPOCH_MS);
  const world = buildWorld({ seed, experts: 8, tasks: 36, goldenShare: 0.25, now: clock.now() });
  const platform = seedPlatform(world, DEMO_SETTINGS, clock);
  const rng = new Rng(seed * 31 + 5);
  // A few careful grades up front so the review and delivery sections have material.
  const tasksById = new Map(world.tasks.map((t) => [t.id, t]));
  for (const sim of world.experts.slice(0, 3)) {
    if (sim.careless) continue;
    for (let i = 0; i < 2; i++) {
      const r = platform.claimNext(platform.expert(sim.id));
      if (!r.ok) break;
      const simTask = tasksById.get(r.task.id);
      if (!simTask) break;
      const g = gradeFor(rng, sim, simTask);
      platform.submitGrade(platform.expert(sim.id), r.task.id, g.scores, g.rationale, g.timeSpentSeconds);
      clock.advance(400);
    }
  }
  return { platform, world, rng, seed };
}

export function trueScoresFor(bench: Workbench, taskId: string): Record<string, number> | null {
  return bench.world.tasks.find((t) => t.id === taskId)?.trueScores ?? null;
}
