import uuid

import pytest

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


@pytest.mark.parametrize("decision", ["approve", "reject"])
@pytest.mark.parametrize("second_already_claimed", [False, True])
def test_early_review_preserves_remaining_grading_work(
    client, admin_key, reviewer_key, decision, second_already_claimed
):
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, rubric, [{"required_grades": 2}])
    _, first_key = make_expert(client, admin_key, "First", ["python"])
    _, second_key = make_expert(client, admin_key, "Second", ["python"])
    assert claim(client, first_key)["id"] == tid
    first_grade = grade(client, first_key, tid)
    if second_already_claimed:
        assert claim(client, second_key)["id"] == tid

    review(client, reviewer_key, first_grade["id"], decision)
    task = client.get(f"/tasks/{tid}", headers=h(admin_key)).json()
    assert task["status"] == ("assigned" if second_already_claimed else "queued")
    if not second_already_claimed:
        assert claim(client, second_key)["id"] == tid
    second_grade = grade(client, second_key, tid)
    review(client, reviewer_key, second_grade["id"], "reject")
    task = client.get(f"/tasks/{tid}", headers=h(admin_key)).json()
    assert task["status"] == ("approved" if decision == "approve" else "rejected")
    assert task["grades_received"] == 2
