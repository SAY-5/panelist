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

from sqlalchemy import select

from panelist.auth import hash_key
from panelist.models import (
    ApiKey,
    AttentionResult,
    Expert,
    Grade,
    RateCard,
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
]

CAREFUL = "Accurate on the core claim; missed one edge case that a careful reader would expect."
CARELESS = "Looks fine."


def _grade(expert, task, scores, rationale=CAREFUL, seconds=200):
    return {
        "action": "grade",
        "expert": expert,
        "task": task,
        "scores": scores,
        "rationale": rationale,
        "time_spent_seconds": seconds,
    }


def _review(task, expert, decision, reason=None):
    return {
        "action": "review",
        "task": task,
        "expert": expert,
        "decision": decision,
        "reason": reason,
    }


STEPS = [
    {"action": "claim", "expert": "A"},
    {"action": "claim", "expert": "B"},
    {"action": "claim", "expert": "C"},
    _grade("A", "t-6", {"accuracy": 5, "clarity": 4, "safety": 5}),
    _grade("B", "t-2", {"accuracy": 4, "clarity": 4, "safety": 4}),
    _grade("C", "t-4", {"accuracy": 2, "clarity": 3, "safety": 2}, CARELESS, 12),
    {"action": "claim", "expert": "A"},
    _grade("A", "gold-1", {"accuracy": 5, "clarity": 5, "safety": 4}),
    {"action": "claim", "expert": "B"},
    _grade("B", "gold-1", {"accuracy": 4, "clarity": 5, "safety": 5}),
    {"action": "claim", "expert": "C"},
    _grade("C", "gold-2", {"accuracy": 1, "clarity": 1, "safety": 1}, CARELESS, 9),
    _review("t-4", "C", "approve"),
    _review("t-6", "A", "approve"),
    _review("t-2", "B", "approve"),
    {"action": "claim", "expert": "C"},
    _grade("C", "t-3", {"accuracy": 5, "clarity": 1, "safety": 5}, CARELESS, 15),
    {"action": "claim", "expert": "C"},
    _grade("C", "gold-3", {"accuracy": 5, "clarity": 5, "safety": 5}, CARELESS, 8),
    {"action": "claim", "expert": "C"},
    _review("t-3", "C", "reject", "spot check failed"),
    # t-3 is back in the queue; its deadline puts it ahead of t-1 for A.
    {"action": "claim", "expert": "A"},
    _grade("A", "t-3", {"accuracy": 4, "clarity": 4, "safety": 4}),
    {"action": "claim", "expert": "B"},
    _grade("B", "t-1", {"accuracy": 4, "clarity": 4, "safety": 4}),
    {"action": "claim", "expert": "A"},
    _grade("A", "gold-2", {"accuracy": 4, "clarity": 4, "safety": 3}),
    {"action": "claim", "expert": "A"},
    _grade("A", "t-5", {"accuracy": 4, "clarity": 3, "safety": 4}),
    {"action": "claim", "expert": "B"},
    _grade("B", "t-5", {"accuracy": 3, "clarity": 4, "safety": 4}),
    _review("t-1", "B", "approve"),
    _review("t-3", "A", "approve"),
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
            expert, task = experts[step["expert"]], tasks[step["task"]]
            g = grading.submit(
                db, expert, task.id, step["scores"], step["rationale"], step["time_spent_seconds"]
            )
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
            expert, task = experts[step["expert"]], tasks[step["task"]]
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
            }
        db.commit()  # each step is its own transaction, as it is over HTTP
        steps.append(out)

    ledger = payouts.ledger(db)
    counts = dict.fromkeys(ledger["totals_by_status"], 0)
    for row in ledger["rows"]:
        counts[row["status"].value] += row["payout_count"]
    return {
        "settings": SETTINGS,
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
