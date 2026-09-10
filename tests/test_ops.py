import json
import uuid
from datetime import UTC, datetime, timedelta

from panelist.cli import main as cli_main
from panelist.models import Task
from tests.helpers import grade, h, make_expert, make_tasks, review, setup_rubric


def _claim(client, key, task_id):
    r = client.post(f"/tasks/{task_id}/claim", headers=h(key))
    assert r.status_code == 200, r.text
    return r.json()


def _seed(client, admin_key, reviewer_key):
    """Five tasks: approved, assigned, queued, in adjudication, approved after the period close."""
    rubric = setup_rubric(client, admin_key)
    ids = make_tasks(
        client,
        admin_key,
        rubric,
        [
            {"external_ref": "done-1"},
            {"external_ref": "held"},
            {"external_ref": "waiting", "required_tags": ["law"]},
            {"external_ref": "split", "required_grades": 2},
            {"external_ref": "done-2"},
        ],
    )
    a, ka = make_expert(client, admin_key, "A", ["python"])
    _, kb = make_expert(client, admin_key, "B", ["python"])
    c, _ = make_expert(client, admin_key, "C", ["law"])

    _claim(client, ka, ids[0])
    review(client, reviewer_key, grade(client, ka, ids[0])["id"], "approve")

    _claim(client, ka, ids[3])
    grade(client, ka, ids[3], {"accuracy": 5, "clarity": 5, "safety": 5})
    _claim(client, kb, ids[3])
    grade(client, kb, ids[3], {"accuracy": 1, "clarity": 1, "safety": 1})

    _claim(client, kb, ids[1])
    client.post("/payouts/periods/close", json={"label": "2026-09-A"}, headers=h(admin_key))

    _claim(client, ka, ids[4])
    review(client, reviewer_key, grade(client, ka, ids[4])["id"], "approve")

    client.patch(
        f"/experts/{c['id']}/status", json={"status": "paused"}, headers=h(admin_key)
    ).raise_for_status()
    client.get("/deliveries/export", headers=h(admin_key)).raise_for_status()
    return ids, a, c


def test_overview_counts_the_seeded_world(client, admin_key, reviewer_key):
    ids, _, paused = _seed(client, admin_key, reviewer_key)
    o = client.get("/ops/overview", headers=h(reviewer_key)).json()

    assert o["queued_by_tag"] == {"law": 1}
    assert o["tasks_by_status"] == {
        "queued": 1,
        "assigned": 1,
        "submitted": 0,
        "adjudication": 1,
        "approved": 2,
        "rejected": 0,
    }
    assert o["expired_leases"] == 0
    assert [(e["name"], e["tier"]) for e in o["paused_experts"]] == [("C", "junior")]
    assert o["adjudication_backlog"] == 1
    assert o["period"] == {
        "last_label": "2026-09-A",
        "closed_at": o["period"]["closed_at"],
        "unbilled_payouts": 1,
        "unbilled_cents": 300,
        "withheld_cents": 0,
    }
    assert o["last_delivery"]["version"] == 1 and o["last_delivery"]["row_count"] == 2
    assert o["paused_experts"][0]["id"] == paused["id"]
    queued = client.get(f"/tasks/{ids[2]}", headers=h(admin_key)).json()
    assert queued["status"] == "queued" and queued["external_ref"] == "waiting"


def test_metrics_expose_the_new_subsystems(client, admin_key, reviewer_key):
    _seed(client, admin_key, reviewer_key)
    body = client.get("/metrics", headers=h(admin_key)).text
    lines = {line.split(" ")[0]: line.split(" ")[-1] for line in body.splitlines()}
    assert lines["panelist_adjudication_backlog"] == "1.0"
    assert lines["panelist_paused_experts"] == "1.0"
    assert lines['panelist_experts_by_tier{tier="junior"}'] == "3.0"
    assert lines['panelist_experts_by_tier{tier="lead"}'] == "0.0"


def test_tick_reclaims_leases_and_reports_reminders(client, admin_key, reviewer_key, db, capsys):
    ids, *_ = _seed(client, admin_key, reviewer_key)
    held = db.get(Task, uuid.UUID(ids[1]))
    held.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert client.get("/ops/overview", headers=h(reviewer_key)).json()["expired_leases"] == 1

    assert cli_main(["tick"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] == 1
    assert out["experts_scored"] == 3 and out["tier_changes"] == []
    assert out["uncalibrated_experts"] == 3
    assert out["adjudication_backlog"] == 1
    assert out["unbilled_payouts"] == 1 and out["unbilled_cents"] == 300
    assert out["days_since_period_close"] == 0.0
    assert out["reminders"] == [
        "3 experts have fewer than 5 calibration signals",
        "1 payouts worth 300 cents are not in a statement; last period closed 0.0 days ago",
        "1 tasks are waiting for adjudication",
    ]

    after = client.get("/ops/overview", headers=h(reviewer_key)).json()
    assert after["expired_leases"] == 0
    assert after["queued_by_tag"] == {"law": 1, "python": 1}


def test_audit_export_is_admin_only_and_filterable(client, admin_key, reviewer_key):
    _seed(client, admin_key, reviewer_key)
    assert client.get("/ops/audit.csv", headers=h(reviewer_key)).status_code == 403

    r = client.get("/ops/audit.csv", headers=h(admin_key))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = r.text.splitlines()
    assert rows[0] == "id,created_at,actor,action,entity_type,entity_id,payload"
    actions = {row.split(",")[3] for row in rows[1:]}
    assert {
        "tasks.created",
        "grade.submitted",
        "consensus.adjudicating",
        "period.closed",
        "delivery.exported",
    } <= actions

    filtered = client.get("/ops/audit.csv?action=period.closed", headers=h(admin_key)).text
    body = filtered.splitlines()[1:]
    assert len(body) == 1 and "2026-09-A" in body[0]
