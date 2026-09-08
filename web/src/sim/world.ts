/** Port of sim/world.py: seeded experts, tasks and grading behaviour. */

import { Rng } from "./prng";
import { TaskInput } from "./platform";
import { RateCard, Tier } from "./types";

export const TAGS = ["python", "law", "medicine", "finance", "math", "writing", "security", "biology"] as const;
export const TIERS: Tier[] = ["junior", "junior", "junior", "senior", "senior", "lead"];
export const TASK_TYPES = ["single", "single", "single", "pairwise"];
export const MODELS = ["model-a", "model-b", "model-c"];

export const RUBRIC = {
  name: "response-quality",
  version: 1,
  criteria: [
    { key: "accuracy", label: "Factual accuracy", weight: 2.0 },
    { key: "completeness", label: "Completeness", weight: 1.5 },
    { key: "clarity", label: "Clarity", weight: 1.0 },
    { key: "safety", label: "Safety and policy", weight: 1.0 },
  ],
};
export const CRITERIA = RUBRIC.criteria.map((c) => c.key);
export const WEIGHTS: Record<string, number> = Object.fromEntries(RUBRIC.criteria.map((c) => [c.key, c.weight]));

export const RATES: RateCard[] = [
  { tier: "junior", taskType: "default", rateCents: 300 },
  { tier: "senior", taskType: "default", rateCents: 500 },
  { tier: "lead", taskType: "default", rateCents: 800 },
  { tier: "junior", taskType: "pairwise", rateCents: 450 },
  { tier: "senior", taskType: "pairwise", rateCents: 700 },
  { tier: "lead", taskType: "pairwise", rateCents: 1000 },
];

export const PROMPTS: Record<string, string> = {
  python: "Explain why this list comprehension leaks memory and rewrite it as a generator.",
  law: "Summarize the enforceability of a restrictive covenant in this employment contract.",
  medicine: "Review the dosage guidance in this response for an adult with renal impairment.",
  finance: "Assess whether this explanation of bond convexity is correct and complete.",
  math: "Check the proof that the sum of the first n odd numbers is n squared.",
  writing: "Edit this paragraph for clarity while preserving the author's argument.",
  security: "Evaluate this description of how to rotate leaked credentials safely.",
  biology: "Verify this explanation of how CRISPR-Cas9 introduces double-strand breaks.",
};

export const RATIONALES = [
  "Accurate on the core claim; missed one edge case that a careful reader would expect.",
  "Clear structure and correct reasoning. Minor omission in the final recommendation.",
  "The answer is mostly right but overstates certainty on a contested point.",
  "Complete and well organised; safety guidance is appropriate for the audience.",
  "Reasoning is sound. Terminology is loose in places, which hurts clarity.",
  "Correct conclusion with a thin justification; would not satisfy a specialist.",
];

export interface SimExpert {
  name: string;
  tags: string[];
  tier: Tier;
  careless: boolean;
  abandonsFirst: boolean;
  id: string;
  served: number;
  graded: number;
  paused: boolean;
}

export interface SimTask {
  payload: TaskInput;
  trueScores: Record<string, number>;
  id: string;
}

export interface World {
  seed: number;
  rng: Rng;
  experts: SimExpert[];
  tasks: SimTask[];
}

export interface WorldOptions {
  seed: number;
  experts: number;
  tasks: number;
  goldenShare: number;
  /** Virtual "now" used for deadlines, in ms. */
  now: number;
}

export function buildWorld(opts: WorldOptions): World {
  const rng = new Rng(opts.seed);
  const experts: SimExpert[] = [];
  for (let i = 0; i < opts.experts; i++) {
    const tags = rng.sample(TAGS, rng.choice([1, 2, 2, 3]));
    experts.push({
      name: `expert-${String(i + 1).padStart(2, "0")}`,
      tags,
      tier: rng.choice(TIERS),
      careless: false,
      abandonsFirst: false,
      id: "",
      served: 0,
      graded: 0,
      paused: false,
    });
  }
  // Two careless experts fail attention checks; two experts abandon their first claim.
  const flag = (i: number, key: "careless" | "abandonsFirst") => {
    const e = experts[i];
    if (e) e[key] = true;
  };
  flag(3, "careless");
  flag(17, "careless");
  flag(7, "abandonsFirst");
  flag(29, "abandonsFirst");

  const nAttention = Math.round(opts.tasks * opts.goldenShare);
  const tasks: SimTask[] = [];
  for (let i = 0; i < opts.tasks; i++) {
    const isAttention = i < nAttention;
    const tags = rng.sample(TAGS, rng.choice([1, 1, 2]));
    const primary = tags[0] ?? "python";
    const taskType = rng.choice(TASK_TYPES);
    const trueScores: Record<string, number> = {};
    for (const c of CRITERIA) trueScores[c] = rng.choice([2, 3, 3, 4, 4, 4, 5, 5]);
    const nResponses = taskType === "pairwise" ? 2 : 1;
    const responses = [];
    for (let j = 0; j < nResponses; j++) {
      responses.push({ model: rng.choice(MODELS), text: `Response ${j + 1} to task ${i + 1}` });
    }
    tasks.push({
      id: "",
      trueScores,
      payload: {
        externalRef: `sim-${String(i + 1).padStart(4, "0")}`,
        prompt: PROMPTS[primary] ?? "",
        responses,
        requiredTags: tags,
        taskType,
        minTier: rng.choice<Tier>(["junior", "junior", "junior", "junior", "junior", "senior"]),
        priority: rng.choice([0, 0, 1, 2, 3]),
        deadline: opts.now + rng.int(6, 168) * 3600 * 1000,
        requiredGrades: isAttention ? 1 : rng.choice([1, 1, 1, 1, 1, 2]),
        isAttentionCheck: isAttention,
        expectedScores: isAttention ? { ...trueScores } : null,
      },
    });
  }
  rng.shuffle(tasks);
  return { seed: opts.seed, rng, experts, tasks };
}

export interface GradeInput {
  scores: Record<string, number>;
  rationale: string;
  timeSpentSeconds: number;
}

/** grade_for(): careless experts roll dice, careful ones stay within one of the truth. */
export function gradeFor(rng: Rng, expert: Pick<SimExpert, "careless">, task: Pick<SimTask, "trueScores">): GradeInput {
  if (expert.careless) {
    const scores: Record<string, number> = {};
    for (const c of CRITERIA) scores[c] = rng.int(1, 5);
    return { scores, rationale: "Looks fine.", timeSpentSeconds: rng.int(8, 25) };
  }
  const scores: Record<string, number> = {};
  for (const c of CRITERIA) {
    const truth = task.trueScores[c] ?? 3;
    const noise = rng.choice([0, 0, 0, 0, 1, -1]);
    scores[c] = Math.max(1, Math.min(5, truth + noise));
  }
  return { scores, rationale: rng.choice(RATIONALES), timeSpentSeconds: rng.int(120, 600) };
}

export function weighted(scores: Record<string, number>): number {
  const total = CRITERIA.reduce((s, c) => s + (WEIGHTS[c] ?? 0), 0);
  return CRITERIA.reduce((s, c) => s + (scores[c] ?? 0) * (WEIGHTS[c] ?? 0), 0) / total;
}
