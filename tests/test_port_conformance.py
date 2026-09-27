"""One fixed scenario, run through the service here and replayed by the browser port.

`tests/fixtures/port_conformance.json` is the contract: this test runs the scenario through the
same service functions the routers call and compares every outcome with the fixture, and
`npm run selfcheck` in `web/` replays the fixture's steps through the TypeScript port and asserts
the same claims, grades, attention verdicts, payouts, totals and JSONL sha256. Set
PANELIST_WRITE_FIXTURE=1 to rewrite the fixture after an intended change on the service side.
"""

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from panelist.auth import hash_key
from panelist.models import (
    ApiKey,
    AttentionResult,
    Expert,
    Grade,
    RateCard,
    Review,
    ReviewDecision,
    Role,
    Rubric,
    RubricCriterion,
    Task,
    Tier,
)
from panelist.services import analytics, delivery, grading, payouts, routing
from panelist.services.errors import ServiceError

FIXTURE = Path(__file__).parent / "fixtures" / "port_conformance.json"

SETTINGS = {
    "lease_seconds": 900,
    "attention_fraction": 0.5,
    "attention_key": "conformance",
    "attention_window": 10,
    "attention_min_checks": 2,
    "attention_threshold": 0.7,
    "attention_tolerance": 1.0,
}

RUBRIC = {
    "id": "00000000-0000-0000-0000-0000000000ff",
    "name": "conformance",
    "version": 1,
    "criteria": [
        {"key": "accuracy", "label": "Accuracy", "weight": 2.0},
        {"key": "clarity", "label": "Clarity", "weight": 1.0},
        {"key": "safety", "label": "Safety", "weight": 1.0},
    ],
}

RATE_CARDS = [
    {"tier": "junior", "task_type": "default", "rate_cents": 300},
    {"tier": "junior", "task_type": "pairwise", "rate_cents": 450},
    {"tier": "senior", "task_type": "default", "rate_cents": 500},
    {"tier": "senior", "task_type": "pairwise", "rate_cents": 650},
    {"tier": "lead", "task_type": "default", "rate_cents": 800},
]

EXPERTS = [
    {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "A",
        "tags": ["python", "law"],
        "tier": "senior",
    },
    {
        "id": "00000000-0000-0000-0000-000000000002",
        "name": "B",
        "tags": ["python"],
        "tier": "junior",
    },
    {"id": "00000000-0000-0000-0000-000000000003", "name": "C", "tags": ["law"], "tier": "junior"},
]

PY = "Explain why this list comprehension leaks memory and rewrite it as a generator."
LAW = "Summarize the enforceability of a restrictive covenant in this employment contract."


def _task(n, ref, prompt, tags, **extra):
    base = {
        "id": f"00000000-0000-0000-0000-0000000001{n:02d}",
        "external_ref": ref,
        "prompt": prompt,
        "responses": [{"model": "model-a", "text": f"Response 1 to {ref}"}],
        "required_tags": tags,
        "task_type": "single",
        "min_tier": "junior",
        "priority": 0,
        "deadline_hours": None,
        "required_grades": 1,
        "is_attention_check": False,
        "expected_scores": None,
    }
    base.update(extra)
    return base


PAIR = [
    {"model": "model-a", "text": "Response 1 to t-2"},
    {"model": "model-b", "text": "Response 2 to t-2"},
]
GOLD = {"is_attention_check": True}

TASKS = [
    _task(1, "t-1", PY, ["python"]),
    _task(2, "t-2", PY, ["python"], task_type="pairwise", priority=2, responses=PAIR),
    _task(
        3,
        "gold-1",
        PY,
        ["python"],
        **GOLD,
        priority=1,
        expected_scores={"accuracy": 5, "clarity": 5, "safety": 5},
    ),
    _task(4, "t-3", LAW, ["law"], deadline_hours=6),
    _task(5, "t-4", LAW, ["law"], deadline_hours=2),
    _task(6, "t-5", PY, ["python"], required_grades=2),
    _task(
        7,
        "gold-2",
        LAW,
        ["law"],
        **GOLD,
        expected_scores={"accuracy": 4, "clarity": 4, "safety": 4},
    ),
    _task(8, "t-6", PY, ["python", "law"], priority=3),
    _task(
        9,
        "gold-3",
        LAW,
        ["law"],
        **GOLD,
        expected_scores={"accuracy": 3, "clarity": 3, "safety": 3},
    ),
    _task(10, "t-7", LAW, ["law"]),
]

CAREFUL = "Accurate on the core claim; missed one edge case that a careful reader would expect."
CARELESS = "Looks fine."


CARELESS_SCORES = {"accuracy": 2.0, "clarity": 1.0, "safety": 2.0}


def _grade(expert):
    """Grade whatever the expert is holding; which task that is belongs to the routing rules."""
    return {"action": "grade", "expert": expert}


def _review(grade, decision, reason=None):
    """`grade` is the 1-based position of the grade step whose grade this reviews."""
    return {"action": "review", "grade": grade, "decision": decision, "reason": reason}


def _typed_scores(expert: str, task: Task) -> tuple[dict[str, float], str, int]:
    """What the expert types into the form for the task they hold.

    C is the careless one: the same low scores everywhere, which fails a golden check. A and
    B read the task, land inside `ATTENTION_TOLERANCE` on a golden one, and differ from each
    other by a point on a regular one so the agreement statistics have something to compare.
    """
    if expert == "C":
        return dict(CARELESS_SCORES), CARELESS, 12
    if task.expected_scores:
        scores = {k: float(v) for k, v in task.expected_scores.items()}
        scores["clarity"] = max(1.0, scores["clarity"] - 1.0)
        return scores, CAREFUL, 150
    shift = 0.0 if expert == "A" else 1.0
    return {"accuracy": 5.0 - shift, "clarity": 4.0, "safety": 5.0 - shift}, CAREFUL, 200


def _approved_grades(db) -> int:
    """Approved grades on non-golden tasks: the 1.0.0 export rule, before consensus filtering."""
    return int(
        db.scalar(
            select(func.count(Grade.id))
            .join(Review, Review.grade_id == Grade.id)
            .join(Task, Task.id == Grade.task_id)
            .where(Review.decision == ReviewDecision.approve, Task.is_attention_check.is_(False))
        )
        or 0
    )


def _held(db, expert: Expert) -> Task:
    task = db.scalar(select(Task).where(Task.assigned_expert_id == expert.id))
    assert task is not None, f"{expert.name} holds no task"
    return task


STEPS = [
    # A and C are served an attention check on their first serve, B is not: the schedule is a
    # keyed hash of the expert id and the serve number, not a countable every-Nth cadence.
    {"action": "claim", "expert": "A"},
    {"action": "claim", "expert": "B"},
    {"action": "claim", "expert": "C"},
    _grade("A"),
    _grade("B"),
    _grade("C"),
    {"action": "claim", "expert": "A"},
    _grade("A"),
    {"action": "claim", "expert": "B"},
    _grade("B"),
    {"action": "claim", "expert": "C"},
    _grade("C"),  # C's first failed check
    _review(3, "approve"),
    _review(1, "approve"),
    _review(2, "approve"),
    {"action": "claim", "expert": "C"},
    _grade("C"),
    _review(7, "reject", "spot check failed"),  # single-grader task, back to the queue
    {"action": "claim", "expert": "C"},
    _grade("C"),
    {"action": "claim", "expert": "C"},
    _grade("C"),  # second failed check: C is paused and their payouts are withheld
    {"action": "claim", "expert": "C"},  # 423, the pause holds
    {"action": "claim", "expert": "A"},
    _grade("A"),  # the requeued task, graded by someone else
    {"action": "claim", "expert": "B"},
    _grade("B"),
    {"action": "claim", "expert": "A"},
    _grade("A"),  # second grade on the two-grader task, so a consensus round is scored
    {"action": "claim", "expert": "A"},
    _grade("A"),
    {"action": "claim", "expert": "B"},  # 204: golden work remains but this serve is not a check
    _review(4, "approve"),
    _review(5, "approve"),
    _review(10, "approve"),
    _review(11, "approve"),
    _review(12, "approve"),
    {"action": "close_period", "label": "2026-09-C"},
    {"action": "export"},
]


def run_scenario(db, settings) -> dict:
    for key, value in SETTINGS.items():
        setattr(settings, key, value)
    # Calibration (3.0.0) is not part of the port, so no tier may move during the scenario.
    settings.calibration_min_samples = 100

    rubric = Rubric(id=uuid.UUID(RUBRIC["id"]), name=RUBRIC["name"], version=RUBRIC["version"])
    rubric.criteria = [RubricCriterion(position=i, **c) for i, c in enumerate(RUBRIC["criteria"])]
    db.add(rubric)
    for card in RATE_CARDS:
        db.add(
            RateCard(
                tier=Tier(card["tier"]), task_type=card["task_type"], rate_cents=card["rate_cents"]
            )
        )
    experts = {
        e["name"]: Expert(
            id=uuid.UUID(e["id"]), name=e["name"], tags=e["tags"], tier=Tier(e["tier"])
        )
        for e in EXPERTS
    }
    db.add_all(experts.values())
    reviewer = ApiKey(
        key_hash=hash_key("pk_reviewer_conformance"), role=Role.reviewer, label="conf"
    )
    db.add(reviewer)
    db.flush()
    now = datetime.now(UTC)
    tasks = {}
    for t in TASKS:
        hours = t["deadline_hours"]
        row = Task(
            id=uuid.UUID(t["id"]),
            external_ref=t["external_ref"],
            prompt=t["prompt"],
            responses=t["responses"],
            required_tags=t["required_tags"],
            task_type=t["task_type"],
            min_tier=Tier(t["min_tier"]),
            rubric_id=rubric.id,
            priority=t["priority"],
            deadline=None if hours is None else now + timedelta(hours=hours),
            required_grades=t["required_grades"],
            is_attention_check=t["is_attention_check"],
            expected_scores=t["expected_scores"],
        )
        db.add(row)
        db.flush()  # one at a time, so `seq` follows the fixture order
        tasks[t["external_ref"]] = row
    db.commit()

    steps = []
    graded: list[tuple[str, str]] = []  # (expert name, task ref) per grade step, in order
    for step in STEPS:
        out = dict(step)
        action = step["action"]
        if action == "claim":
            try:
                got = routing.claim_next(db, experts[step["expert"]])
                out["expect"] = 204 if got is None else got.external_ref
            except ServiceError as e:
                out["expect"] = e.status_code
        elif action == "grade":
            expert = experts[step["expert"]]
            task = _held(db, expert)
            scores, rationale, seconds = _typed_scores(step["expert"], task)
            out |= {
                "task": task.external_ref,
                "scores": scores,
                "rationale": rationale,
                "time_spent_seconds": seconds,
            }
            graded.append((step["expert"], task.external_ref))
            g = grading.submit(db, expert, task.id, scores, rationale, seconds)
            att = db.scalar(select(AttentionResult).where(AttentionResult.grade_id == g.id))
            out["expect"] = {
                "weighted_score": g.weighted_score,
                "attention": None
                if att is None
                else {"passed": att.passed, "max_deviation": att.max_deviation},
                "expert_status": expert.status.value,
                "task_status": task.status.value,
            }
        elif action == "review":
            expert_name, task_ref = graded[step["grade"] - 1]
            out |= {"expert": expert_name, "task": task_ref}
            expert, task = experts[expert_name], tasks[task_ref]
            g = db.scalar(
                select(Grade).where(Grade.task_id == task.id, Grade.expert_id == expert.id)
            )
            _, payout = grading.review(
                db,
                reviewer.id,
                g.id,
                ReviewDecision(step["decision"]),
                step["reason"],
                "reviewer:conf",
            )
            out["expect"] = {
                "task_status": task.status.value,
                "payout_cents": None if payout is None else payout.amount_cents,
                "payout_status": None if payout is None else payout.status.value,
            }
        elif action == "close_period":
            period = payouts.close_period(db, step["label"], "admin:conf")
            totals = payouts.period_totals(db, period)
            out["expect"] = {k: totals[k] for k in ("payout_count", "total_cents", "expert_count")}
        elif action == "export":
            body, count = delivery.build_jsonl(db)
            out["expect"] = {
                "row_count": count,
                "size_bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                # What the 1.0.0 export rule the port implements would deliver: every approved
                # grade, including the grades a consensus round did not pick. The port is held to
                # this number, and to the service's bytes only when the two rules agree.
                "approved_grade_count": _approved_grades(db),
            }
        db.commit()  # each step is its own transaction, as it is over HTTP
        steps.append(out)

    ledger = payouts.ledger(db)
    counts = dict.fromkeys(ledger["totals_by_status"], 0)
    for row in ledger["rows"]:
        counts[row["status"].value] += row["payout_count"]
    return {
        "settings": SETTINGS,
        "attention_schedule": [
            {
                "expert": e["name"],
                "serves": [
                    routing.prefers_attention_check(settings, uuid.UUID(e["id"]), i)
                    for i in range(1, 13)
                ],
            }
            for e in EXPERTS
        ],
        "rubric": RUBRIC,
        "rate_cards": RATE_CARDS,
        "experts": EXPERTS,
        "tasks": TASKS,
        "steps": steps,
        "final": {
            "ledger": {"totals_by_status": ledger["totals_by_status"], "counts_by_status": counts},
            "agreement": analytics.global_agreement(db),
            "task_status": routing.queue_summary(db),
            "criterion_means": [
                {"key": c["criterion_key"], "mean": c["mean"], "stddev": c["stddev"], "n": c["n"]}
                for c in analytics.criterion_means(db)
            ],
        },
    }


def test_service_reproduces_the_port_conformance_fixture(db, settings):
    actual = run_scenario(db, settings)
    if os.environ.get("PANELIST_WRITE_FIXTURE"):
        FIXTURE.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    assert actual == json.loads(FIXTURE.read_text())
