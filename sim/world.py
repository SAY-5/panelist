"""Seeded generation of experts, tasks and grading behaviour."""

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

TAGS = ["python", "law", "medicine", "finance", "math", "writing", "security", "biology"]
TIERS = ["junior", "junior", "junior", "senior", "senior", "lead"]
TASK_TYPES = ["single", "single", "single", "pairwise"]
MODELS = ["model-a", "model-b", "model-c"]

RUBRIC = {
    "name": "response-quality",
    "version": 1,
    "criteria": [
        {"key": "accuracy", "label": "Factual accuracy", "weight": 2.0},
        {"key": "completeness", "label": "Completeness", "weight": 1.5},
        {"key": "clarity", "label": "Clarity", "weight": 1.0},
        {"key": "safety", "label": "Safety and policy", "weight": 1.0},
    ],
}
CRITERIA = [c["key"] for c in RUBRIC["criteria"]]
WEIGHTS = {c["key"]: c["weight"] for c in RUBRIC["criteria"]}

RATES = [
    {"tier": "junior", "task_type": "default", "rate_cents": 300},
    {"tier": "senior", "task_type": "default", "rate_cents": 500},
    {"tier": "lead", "task_type": "default", "rate_cents": 800},
    {"tier": "junior", "task_type": "pairwise", "rate_cents": 450},
    {"tier": "senior", "task_type": "pairwise", "rate_cents": 700},
    {"tier": "lead", "task_type": "pairwise", "rate_cents": 1000},
]

PROMPTS = {
    "python": "Explain why this list comprehension leaks memory and rewrite it as a generator.",
    "law": "Summarize the enforceability of a restrictive covenant in this employment contract.",
    "medicine": "Review the dosage guidance in this response for an adult with renal impairment.",
    "finance": "Assess whether this explanation of bond convexity is correct and complete.",
    "math": "Check the proof that the sum of the first n odd numbers is n squared.",
    "writing": "Edit this paragraph for clarity while preserving the author's argument.",
    "security": "Evaluate this description of how to rotate leaked credentials safely.",
    "biology": "Verify this explanation of how CRISPR-Cas9 introduces double-strand breaks.",
}

RATIONALES = [
    "Accurate on the core claim; missed one edge case that a careful reader would expect.",
    "Clear structure and correct reasoning. Minor omission in the final recommendation.",
    "The answer is mostly right but overstates certainty on a contested point.",
    "Complete and well organised; safety guidance is appropriate for the audience.",
    "Reasoning is sound. Terminology is loose in places, which hurts clarity.",
    "Correct conclusion with a thin justification; would not satisfy a specialist.",
]


@dataclass
class SimExpert:
    name: str
    tags: list[str]
    tier: str
    careless: bool = False
    abandons_first: bool = False
    id: str = ""
    key: str = ""
    served: int = 0
    graded: int = 0
    paused: bool = False


@dataclass
class SimTask:
    payload: dict
    true_scores: dict[str, int]
    id: str = ""


@dataclass
class World:
    seed: int
    experts: list[SimExpert] = field(default_factory=list)
    tasks: list[SimTask] = field(default_factory=list)
    rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    def by_id(self) -> dict[str, SimTask]:
        return {t.id: t for t in self.tasks}


def build_world(seed: int, n_experts: int, n_tasks: int, attention_fraction: float) -> World:
    w = World(seed)
    rng = w.rng
    for i in range(n_experts):
        tags = rng.sample(TAGS, k=rng.choice([1, 2, 2, 3]))
        w.experts.append(SimExpert(name=f"expert-{i + 1:02d}", tags=tags, tier=rng.choice(TIERS)))
    # Two careless experts fail attention checks; two experts abandon their first claim.
    w.experts[3].careless = True
    w.experts[17].careless = True
    w.experts[7].abandons_first = True
    w.experts[29].abandons_first = True

    n_attention = round(n_tasks * attention_fraction)
    now = datetime.now(UTC)
    for i in range(n_tasks):
        is_attention = i < n_attention
        tags = rng.sample(TAGS, k=rng.choice([1, 1, 2]))
        primary = tags[0]
        task_type = rng.choice(TASK_TYPES)
        true_scores = {c: rng.choice([2, 3, 3, 4, 4, 4, 5, 5]) for c in CRITERIA}
        n_responses = 2 if task_type == "pairwise" else 1
        responses = [
            {"model": rng.choice(MODELS), "text": f"Response {j + 1} to task {i + 1}"}
            for j in range(n_responses)
        ]
        payload = {
            "external_ref": f"sim-{i + 1:04d}",
            "prompt": PROMPTS[primary],
            "responses": responses,
            "required_tags": tags,
            "task_type": task_type,
            "min_tier": rng.choice(["junior"] * 5 + ["senior"]),
            "priority": rng.choice([0, 0, 1, 2, 3]),
            "deadline": (now + timedelta(hours=rng.randint(6, 168))).isoformat(),
            "required_grades": 1 if is_attention else rng.choice([1] * 5 + [2]),
            "is_attention_check": is_attention,
            "expected_scores": true_scores if is_attention else None,
        }
        w.tasks.append(SimTask(payload=payload, true_scores=true_scores))
    rng.shuffle(w.tasks)
    return w


def grade_for(rng: random.Random, expert: SimExpert, task: SimTask) -> dict:
    if expert.careless:
        scores = {c: rng.randint(1, 5) for c in CRITERIA}
        rationale = "Looks fine."
        time_spent = rng.randint(8, 25)
    else:
        scores = {}
        for c, truth in task.true_scores.items():
            noise = rng.choice([0, 0, 0, 0, 1, -1])
            scores[c] = max(1, min(5, truth + noise))
        rationale = rng.choice(RATIONALES)
        time_spent = rng.randint(120, 600)
    return {"scores": scores, "rationale": rationale, "time_spent_seconds": time_spent}


def weighted(scores: dict[str, float]) -> float:
    return sum(scores[c] * WEIGHTS[c] for c in CRITERIA) / sum(WEIGHTS.values())
