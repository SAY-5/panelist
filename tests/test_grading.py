import json
import uuid
from pathlib import Path

from panelist.cli import main as cli_main
from panelist.models import Grade, GradeScore, Task, TaskStatus
from tests.helpers import claim, grade, h, make_expert, make_tasks, review, setup_rubric


def test_scores_are_validated_against_rubric(client, admin_key):
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, rubric, [{}])
    _, key = make_expert(client, admin_key, "A", ["python"])
    claim(client, key)

    def post(scores):
        return client.post(
            "/grades", json={"task_id": tid, "scores": scores, "rationale": "r"}, headers=h(key)
        )

    r = post({"accuracy": 4, "clarity": 4})
    assert r.status_code == 422 and "missing=['safety']" in r.json()["detail"]
    r = post({"accuracy": 4, "clarity": 4, "safety": 5, "tone": 3})
    assert r.status_code == 422 and "unknown=['tone']" in r.json()["detail"]
    r = post({"accuracy": 9, "clarity": 4, "safety": 5})
    assert r.status_code == 422 and "within [1, 5]" in r.json()["detail"]
    assert post({"accuracy": 4, "clarity": 4, "safety": 5}).status_code == 201


def test_grade_stores_normalized_rows_and_snapshot(client, admin_key, db):
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, rubric, [{}])
    _, key = make_expert(client, admin_key, "A", ["python"])
    claim(client, key)
    out = grade(client, key, tid, {"accuracy": 5, "clarity": 3, "safety": 4})
    # weights: accuracy 2, clarity 1, safety 1 -> (10 + 3 + 4) / 4
    assert out["weighted_score"] == 4.25
    g = db.get(Grade, uuid.UUID(out["id"]))
    assert g.scores_snapshot == {"accuracy": 5.0, "clarity": 3.0, "safety": 4.0}
    rows = db.query(GradeScore).filter_by(grade_id=g.id).all()
    assert {r.criterion.key: r.score for r in rows} == g.scores_snapshot
    assert db.get(Task, uuid.UUID(tid)).status == TaskStatus.submitted


def test_cannot_grade_unassigned_task(client, admin_key):
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, rubric, [{}])
    _, key = make_expert(client, admin_key, "A", ["python"])
    r = client.post(
        "/grades",
        json={
            "task_id": tid,
            "scores": {"accuracy": 1, "clarity": 1, "safety": 1},
            "rationale": "r",
        },
        headers=h(key),
    )
    assert r.status_code == 409


def test_multi_grade_task_returns_to_queue_until_complete(client, admin_key, db):
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, rubric, [{"required_grades": 2}])
    _, a = make_expert(client, admin_key, "A", ["python"])
    _, b = make_expert(client, admin_key, "B", ["python"])
    claim(client, a)
    grade(client, a, tid)
    task = db.get(Task, uuid.UUID(tid))
    db.refresh(task)
    assert task.status == TaskStatus.queued and task.grades_received == 1
    claim(client, b)
    grade(client, b, tid)
    db.refresh(task)
    assert task.status == TaskStatus.submitted and task.grades_received == 2


def test_expert_view_hides_attention_fields(client, admin_key, settings):
    settings.attention_fraction = 1.0
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(
        client,
        admin_key,
        rubric,
        [
            {
                "is_attention_check": True,
                "expected_scores": {"accuracy": 5, "clarity": 5, "safety": 5},
            }
        ],
    )
    _, key = make_expert(client, admin_key, "A", ["python"])
    got = claim(client, key)
    assert got["id"] == tid
    assert "is_attention_check" not in got and "expected_scores" not in got
    admin_view = client.get(f"/tasks/{tid}", headers=h(admin_key)).json()
    assert admin_view["is_attention_check"] is True


def test_rejected_single_grader_task_returns_to_the_queue(client, admin_key, reviewer_key, capsys):
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, rubric, [{"external_ref": "again"}])
    _, a = make_expert(client, admin_key, "A", ["python"])
    _, b = make_expert(client, admin_key, "B", ["python"])
    c_expert, c = make_expert(client, admin_key, "C", ["python"])
    for key in (a, b):
        assert claim(client, key)["id"] == tid
        g = grade(client, key, tid, {"accuracy": 1, "clarity": 1, "safety": 1})
        review(client, reviewer_key, g["id"], "reject", "off")
        task = client.get(f"/tasks/{tid}", headers=h(admin_key)).json()
        assert task["status"] == "queued" and task["grades_received"] == 0
        assert claim(client, key) is None  # never served back to the expert it rejected

    assert cli_main(["tick"]) == 0
    tick = json.loads(capsys.readouterr().out)
    assert tick["rejected_twice"] == 1
    assert "1 tasks have been rejected twice and are back in the queue" in tick["reminders"]

    assert claim(client, c)["id"] == tid
    third = grade(client, c, tid)
    assert client.get(f"/tasks/{tid}", headers=h(admin_key)).json()["status"] == "submitted"
    review(client, reviewer_key, third["id"], "approve")
    assert client.get(f"/tasks/{tid}", headers=h(admin_key)).json()["status"] == "approved"
    export = client.get("/deliveries/export", headers=h(admin_key)).json()
    rows = [json.loads(line) for line in Path(export["location"]).read_text().splitlines()]
    assert [r["expert_id"] for r in rows] == [c_expert["id"]]
    assert rows[0]["consensus"] is None  # the two rejected grades never formed a round
