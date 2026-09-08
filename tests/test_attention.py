import uuid

from panelist.models import Expert, ExpertStatus, Payout, PayoutStatus
from tests.helpers import claim, grade, h, make_expert, make_tasks, review, setup_rubric

GOLD = {"accuracy": 5, "clarity": 5, "safety": 5}


def _golden(n):
    return [{"is_attention_check": True, "expected_scores": GOLD, "priority": 1} for _ in range(n)]


def test_attention_checks_served_at_configured_fraction(client, admin_key, settings):
    settings.attention_fraction = 0.5
    rubric = setup_rubric(client, admin_key)
    make_tasks(client, admin_key, rubric, [{} for _ in range(6)] + _golden(3))
    _, key = make_expert(client, admin_key, "A", ["python"])
    served = []
    for _ in range(6):
        t = claim(client, key)
        served.append(
            client.get(f"/tasks/{t['id']}", headers=h(admin_key)).json()["is_attention_check"]
        )
    assert served == [False, True, False, True, False, True]


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
