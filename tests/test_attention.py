import uuid

from panelist.auth import issue_key
from panelist.models import Expert, ExpertStatus, Payout, PayoutStatus, Role
from tests.helpers import claim, grade, h, make_expert, make_tasks, review, setup_rubric

GOLD = {"accuracy": 5, "clarity": 5, "safety": 5}
# The keyed hash that schedules checks reads the expert id, so the tests that count checks fix it.
EXPERT_ID = uuid.UUID(int=1)


def _golden(n):
    return [{"is_attention_check": True, "expected_scores": GOLD, "priority": 1} for _ in range(n)]


def _expert_with_id(db, expert_id: uuid.UUID) -> str:
    """Create a python expert with a known id and return its API key."""
    db.add(Expert(id=expert_id, name="A", tags=["python"]))
    _, key = issue_key(db, Role.expert, expert_id=expert_id)
    db.commit()
    return key


def _served_checks(client, admin_key, key, serves):
    """Claim `serves` times and report which of them were attention checks."""
    out = []
    for _ in range(serves):
        t = claim(client, key)
        assert t is not None
        out.append(
            client.get(f"/tasks/{t['id']}", headers=h(admin_key)).json()["is_attention_check"]
        )
    return out


def test_attention_checks_are_served_at_the_configured_share(client, admin_key, settings, db):
    settings.attention_fraction = 0.5
    settings.attention_key = "panelist"
    rubric = setup_rubric(client, admin_key)
    make_tasks(client, admin_key, rubric, [{} for _ in range(40)] + _golden(40))
    key = _expert_with_id(db, EXPERT_ID)
    served = _served_checks(client, admin_key, key, 40)
    # A keyed hash decides each serve, so the share holds without a countable cadence; for this
    # id and key it serves 21 checks in 40.
    assert sum(served) == 21
    every_other = [i % 2 == 1 for i in range(40)]
    assert served != every_other


def test_gaming_the_old_cadence_does_not_dodge_the_checks(client, admin_key, settings, db):
    """An expert careful only on the serves a 1/f cadence would predict still gets paused."""
    settings.attention_fraction = 0.5
    settings.attention_min_checks = 2
    settings.attention_key = "panelist"
    rubric = setup_rubric(client, admin_key)
    make_tasks(client, admin_key, rubric, [{} for _ in range(30)] + _golden(30))
    key = _expert_with_id(db, EXPERT_ID)
    careless = {"accuracy": 1, "clarity": 1, "safety": 1}
    off_cadence_checks = 0
    for serve in range(1, 31):
        r = client.post("/tasks/next", headers=h(key))
        if r.status_code in (204, 423):  # queue empty, or the guard has already tripped
            break
        assert r.status_code == 200, r.text
        t = r.json()
        predicted = serve % 2 == 0
        is_check = client.get(f"/tasks/{t['id']}", headers=h(admin_key)).json()[
            "is_attention_check"
        ]
        if is_check and not predicted:
            off_cadence_checks += 1
        grade(client, key, t["id"], scores=GOLD if predicted else careless)
    db.expire_all()
    # At least one check landed on a serve the cadence would not have predicted, and the
    # careless grade on it is what pauses the expert.
    assert off_cadence_checks >= 1
    assert db.get(Expert, EXPERT_ID).status == ExpertStatus.paused


def test_failed_checks_pause_expert_and_withhold_payouts(
    client, admin_key, reviewer_key, settings, db
):
    settings.attention_fraction = 1.0
    settings.attention_min_checks = 3
    settings.attention_threshold = 0.7
    settings.attention_tolerance = 1.0
    rubric = setup_rubric(client, admin_key)
    make_tasks(client, admin_key, rubric, _golden(3) + [{}])
    expert, key = make_expert(client, admin_key, "Sloppy", ["python"])

    # First grade is fine and gets approved: creates a pending payout
    t1 = claim(client, key)
    g1 = grade(client, key, t1["id"], {"accuracy": 4, "clarity": 5, "safety": 5})
    r1 = review(client, reviewer_key, g1["id"])
    assert r1["payout_status"] == "pending"

    # Two bad grades: rolling rate 1/3 < 0.7 with 3 checks -> paused
    for _ in range(2):
        t = claim(client, key)
        assert t is not None
        grade(client, key, t["id"], {"accuracy": 1, "clarity": 2, "safety": 1})

    e = db.get(Expert, uuid.UUID(expert["id"]))
    db.refresh(e)
    assert e.status == ExpertStatus.paused
    summary = client.get(f"/experts/{expert['id']}/attention", headers=h(admin_key)).json()
    assert summary["paused"] is True
    assert summary["checks_total"] == 3 and summary["checks_passed"] == 1
    assert abs(summary["rolling_pass_rate"] - 1 / 3) < 1e-9

    # Existing pending payout was withheld
    p = db.scalar(db.query(Payout).filter_by(expert_id=e.id).statement)
    db.refresh(p)
    assert p.status == PayoutStatus.withheld

    # Further claims refused
    r = client.post("/tasks/next", headers=h(key))
    assert r.status_code == 423

    # New approvals while paused are withheld too
    unreviewed = client.get("/grades", headers=h(reviewer_key)).json()
    out = review(client, reviewer_key, unreviewed[0]["id"])
    assert out["payout_status"] == "withheld"

    # Reinstatement by an admin releases withheld payouts
    r = client.patch(
        f"/experts/{expert['id']}/status", json={"status": "active"}, headers=h(admin_key)
    )
    assert r.status_code == 200
    statuses = {
        p["status"]
        for p in client.get(f"/payouts?expert_id={expert['id']}", headers=h(admin_key)).json()
    }
    assert statuses == {"pending"}


def test_passing_checks_keep_expert_active(client, admin_key, settings, db):
    settings.attention_fraction = 1.0
    settings.attention_min_checks = 2
    rubric = setup_rubric(client, admin_key)
    make_tasks(client, admin_key, rubric, _golden(4))
    expert, key = make_expert(client, admin_key, "Careful", ["python"])
    for _ in range(4):
        t = claim(client, key)
        grade(client, key, t["id"], {"accuracy": 5, "clarity": 4, "safety": 5})  # within tolerance
    e = db.get(Expert, uuid.UUID(expert["id"]))
    db.refresh(e)
    assert e.status == ExpertStatus.active
    s = client.get(f"/experts/{expert['id']}/attention", headers=h(admin_key)).json()
    assert s["checks_passed"] == 4 and s["rolling_pass_rate"] == 1.0


def test_rolling_window_forgets_old_failures(client, admin_key, settings, db):
    settings.attention_fraction = 1.0
    settings.attention_min_checks = 2
    settings.attention_window = 2
    settings.attention_threshold = 0.9
    rubric = setup_rubric(client, admin_key)
    make_tasks(client, admin_key, rubric, _golden(4))
    expert, key = make_expert(client, admin_key, "Recovering", ["python"])
    t = claim(client, key)
    grade(client, key, t["id"], {"accuracy": 1, "clarity": 1, "safety": 1})  # fail, only 1 check
    e = db.get(Expert, uuid.UUID(expert["id"]))
    db.refresh(e)
    assert e.status == ExpertStatus.active
    for _ in range(2):
        t = claim(client, key)
        grade(client, key, t["id"], GOLD)
    s = client.get(f"/experts/{expert['id']}/attention", headers=h(admin_key)).json()
    assert s["rolling_window"] == 2 and s["rolling_pass_rate"] == 1.0 and s["checks_total"] == 3


def test_attention_task_is_reused_across_experts(client, admin_key, reviewer_key, settings, db):
    settings.attention_fraction = 1.0
    rubric = setup_rubric(client, admin_key)
    (gold_id,) = make_tasks(client, admin_key, rubric, _golden(1))
    _, a = make_expert(client, admin_key, "A", ["python"])
    _, b = make_expert(client, admin_key, "B", ["python"])
    assert claim(client, a)["id"] == gold_id
    ga = grade(client, a, gold_id, GOLD)
    assert claim(client, a) is None  # never the same golden task twice for one expert
    assert claim(client, b)["id"] == gold_id
    grade(client, b, gold_id, GOLD)
    review(client, reviewer_key, ga["id"])  # approval pays but leaves the task in the queue
    t = client.get(f"/tasks/{gold_id}", headers=h(admin_key)).json()
    assert t["status"] == "queued" and t["grades_received"] == 2
    _, c = make_expert(client, admin_key, "C", ["python"])
    assert claim(client, c)["id"] == gold_id
