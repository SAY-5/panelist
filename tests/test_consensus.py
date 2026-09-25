import json
from pathlib import Path

from tests.helpers import claim, grade, h, make_expert, make_tasks, review, setup_rubric


def _delivered(client, admin_key):
    export = client.get("/deliveries/export", headers=h(admin_key)).json()
    body = Path(export["location"]).read_text()
    return [json.loads(line) for line in body.splitlines()]


def _grade_round(client, admin_key, task_id, scores_by_expert):
    out = {}
    for name, scores in scores_by_expert.items():
        _, key = make_expert(client, admin_key, name, ["python"])
        assert claim(client, key)["id"] == task_id
        out[name] = grade(client, key, task_id, scores)
    return out


def test_agreement_under_tolerance_auto_resolves(client, admin_key, reviewer_key):
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, rubric, [{"required_grades": 3}])
    grades = _grade_round(
        client,
        admin_key,
        tid,
        {
            "A": {"accuracy": 4, "clarity": 4, "safety": 4},
            "B": {"accuracy": 4, "clarity": 5, "safety": 4},
            "C": {"accuracy": 5, "clarity": 4, "safety": 4},
        },
    )
    assert client.get(f"/tasks/{tid}", headers=h(admin_key)).json()["status"] == "submitted"
    assert client.get("/adjudications", headers=h(reviewer_key)).json() == []

    for g in grades.values():
        review(client, reviewer_key, g["id"], "approve")
    rows = _delivered(client, admin_key)
    assert len(rows) == 1
    assert rows[0]["scores"] == {"accuracy": 4.0, "clarity": 5.0, "safety": 4.0}
    assert rows[0]["consensus"] == {"status": "agreed", "graders": 3, "spread": 0.5}


def test_disagreement_routes_to_adjudication(client, admin_key, reviewer_key):
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, rubric, [{"required_grades": 2, "external_ref": "t-1"}])
    grades = _grade_round(
        client,
        admin_key,
        tid,
        {
            "A": {"accuracy": 5, "clarity": 5, "safety": 5},
            "B": {"accuracy": 1, "clarity": 1, "safety": 1},
        },
    )
    assert client.get(f"/tasks/{tid}", headers=h(admin_key)).json()["status"] == "adjudication"

    queue = client.get("/adjudications", headers=h(reviewer_key)).json()
    assert len(queue) == 1
    entry = queue[0]
    assert entry["task_id"] == tid and entry["external_ref"] == "t-1"
    assert entry["grade_count"] == 2 and entry["spread"] == 4.0 and entry["tolerance"] == 1.0
    assert {g["expert_name"] for g in entry["grades"]} == {"A", "B"}

    assert client.get("/grades", headers=h(reviewer_key)).json() == []
    blocked = client.post(
        "/reviews",
        json={"grade_id": grades["A"]["id"], "decision": "approve"},
        headers=h(reviewer_key),
    )
    assert blocked.status_code == 409 and "adjudication" in blocked.json()["detail"]
    forbidden = client.post(
        f"/adjudications/{tid}",
        json={"delivered_grade_id": grades["A"]["id"], "reason": "A is right"},
        headers=h(reviewer_key),
    )
    assert forbidden.status_code == 403 and "adjudications:write" in forbidden.json()["detail"]


def test_adjudicated_grade_is_the_delivered_grade(client, admin_key, reviewer_key, senior_key):
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, rubric, [{"required_grades": 2}])
    grades = _grade_round(
        client,
        admin_key,
        tid,
        {
            "A": {"accuracy": 5, "clarity": 5, "safety": 5},
            "B": {"accuracy": 1, "clarity": 1, "safety": 1},
        },
    )
    r = client.post(
        f"/adjudications/{tid}",
        json={"delivered_grade_id": grades["B"]["id"], "reason": "the response is unsafe"},
        headers=h(senior_key),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "adjudicated"
    assert r.json()["delivered_grade_id"] == grades["B"]["id"]

    assert client.get(f"/tasks/{tid}", headers=h(admin_key)).json()["status"] == "approved"
    assert client.get("/adjudications", headers=h(reviewer_key)).json() == []

    rows = _delivered(client, admin_key)
    assert len(rows) == 1
    assert rows[0]["scores"] == {"accuracy": 1.0, "clarity": 1.0, "safety": 1.0}
    assert rows[0]["consensus"]["status"] == "adjudicated"

    again = client.post(
        f"/adjudications/{tid}",
        json={"delivered_grade_id": grades["A"]["id"], "reason": "changed my mind"},
        headers=h(senior_key),
    )
    assert again.status_code == 409


def test_outvoted_payout_rule_is_applied(client, admin_key, senior_key, settings):
    rubric = setup_rubric(client, admin_key)
    ids = make_tasks(client, admin_key, rubric, [{"required_grades": 2} for _ in range(3)])
    _, ka = make_expert(client, admin_key, "A", ["python"])
    _, kb = make_expert(client, admin_key, "B", ["python"])
    chosen = {}
    for tid in ids:
        for key, scores in (
            (ka, {"accuracy": 5, "clarity": 5, "safety": 5}),
            (kb, {"accuracy": 1, "clarity": 1, "safety": 1}),
        ):
            assert claim(client, key)["id"] == tid
            g = grade(client, key, tid, scores)
            if key == ka:
                chosen[tid] = g["id"]

    def decide(tid):
        r = client.post(
            f"/adjudications/{tid}",
            json={"delivered_grade_id": chosen[tid], "reason": "A matches the reference"},
            headers=h(senior_key),
        )
        assert r.status_code == 200, r.text
        return r.json()

    settings.consensus_outvoted_payout = "partial"
    settings.consensus_outvoted_rate = 0.5
    partial = decide(ids[0])
    assert partial["outvoted_rule"] == "partial"
    assert partial["delivered_amount_cents"] == 300
    assert [o["amount_cents"] for o in partial["outvoted"]] == [150]

    settings.consensus_outvoted_payout = "none"
    nothing = decide(ids[1])
    assert [o["payout_id"] for o in nothing["outvoted"]] == [None]
    assert [o["amount_cents"] for o in nothing["outvoted"]] == [None]

    settings.consensus_outvoted_payout = "full"
    full = decide(ids[2])
    assert [o["amount_cents"] for o in full["outvoted"]] == [300]

    ledger = client.get("/payouts/ledger", headers=h(admin_key)).json()
    by_expert = {row["expert_name"]: row for row in ledger["rows"]}
    assert by_expert["A"]["payout_count"] == 3 and by_expert["A"]["total_cents"] == 900
    assert by_expert["B"]["payout_count"] == 2 and by_expert["B"]["total_cents"] == 450


def test_review_waits_for_every_required_grade(client, admin_key, reviewer_key):
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, rubric, [{"required_grades": 2}])
    _, a = make_expert(client, admin_key, "A", ["python"])
    _, b = make_expert(client, admin_key, "B", ["python"])
    assert claim(client, a)["id"] == tid
    first = grade(client, a, tid)
    early = client.post(
        "/reviews", json={"grade_id": first["id"], "decision": "approve"}, headers=h(reviewer_key)
    )
    assert early.status_code == 409 and "waiting for 2 grades" in early.json()["detail"]
    assert client.get("/grades", headers=h(reviewer_key)).json() == []
    task = client.get(f"/tasks/{tid}", headers=h(admin_key)).json()
    assert task["status"] == "queued" and task["grades_received"] == 1
    assert claim(client, b)["id"] == tid
    grade(client, b, tid)
    review(client, reviewer_key, first["id"], "approve")
