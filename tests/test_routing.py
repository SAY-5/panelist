import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from panelist.db import session_factory
from panelist.models import Expert, Task, TaskStatus
from panelist.services import routing
from tests.helpers import claim, grade, h, make_expert, make_tasks, setup_rubric


def test_claim_respects_expertise_tags(client, admin_key):
    rubric = setup_rubric(client, admin_key)
    make_tasks(
        client,
        admin_key,
        rubric,
        [{"required_tags": ["python"]}, {"required_tags": ["law"]}, {"required_tags": ["law"]}],
    )
    _, py_key = make_expert(client, admin_key, "Py", ["python"])
    _, law_key = make_expert(client, admin_key, "Law", ["law", "finance"])

    first = claim(client, py_key)
    assert first is not None
    assert "required_tags" not in first  # hidden from experts
    assert claim(client, py_key) is None  # nothing else for python

    got = [claim(client, law_key) for _ in range(3)]
    assert sum(t is not None for t in got) == 2


def test_claim_orders_by_priority_then_deadline(client, admin_key):
    rubric = setup_rubric(client, admin_key)
    soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    later = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    ids = make_tasks(
        client,
        admin_key,
        rubric,
        [
            {"priority": 0, "deadline": later},
            {"priority": 5, "deadline": later},
            {"priority": 5, "deadline": soon},
            {"priority": 0, "deadline": None},
        ],
    )
    _, key = make_expert(client, admin_key, "Ana", ["python"])
    order = [claim(client, key)["id"] for _ in range(4)]
    assert order == [ids[2], ids[1], ids[0], ids[3]]


def test_tier_gate_and_priority_by_tier(client, admin_key):
    rubric = setup_rubric(client, admin_key)
    ids = make_tasks(client, admin_key, rubric, [{"min_tier": "senior"}, {"min_tier": "junior"}])
    _, junior = make_expert(client, admin_key, "J", ["python"], tier="junior")
    _, senior = make_expert(client, admin_key, "S", ["python"], tier="senior")
    assert claim(client, junior)["id"] == ids[1]
    assert claim(client, junior) is None
    assert claim(client, senior)["id"] == ids[0]


def test_direct_claim_conflict_is_blocked(client, admin_key):
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, rubric, [{}])
    _, a = make_expert(client, admin_key, "A", ["python"])
    _, b = make_expert(client, admin_key, "B", ["python"])
    assert client.post(f"/tasks/{tid}/claim", headers=h(a)).status_code == 200
    r = client.post(f"/tasks/{tid}/claim", headers=h(b))
    assert r.status_code == 409
    assert client.post(f"/tasks/{uuid.uuid4()}/claim", headers=h(b)).status_code == 404


def test_direct_claim_enforces_tags(client, admin_key):
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, rubric, [{"required_tags": ["law"]}])
    _, a = make_expert(client, admin_key, "A", ["python"])
    assert client.post(f"/tasks/{tid}/claim", headers=h(a)).status_code == 403


def test_concurrent_claims_never_double_assign(client, admin_key, db):
    rubric = setup_rubric(client, admin_key)
    task_ids = make_tasks(client, admin_key, rubric, [{} for _ in range(12)])
    experts = [make_expert(client, admin_key, f"E{i}", ["python"])[0] for i in range(24)]
    expert_ids = [uuid.UUID(e["id"]) for e in experts]

    def worker(expert_id):
        with session_factory()() as s:
            expert = s.get(Expert, expert_id)
            task = routing.claim_next(s, expert)
            s.commit()
            return str(task.id) if task else None

    with ThreadPoolExecutor(max_workers=24) as pool:
        results = list(pool.map(worker, expert_ids))

    claimed = [r for r in results if r]
    assert len(claimed) == 12
    assert len(set(claimed)) == 12
    assert set(claimed) == set(task_ids)
    assigned = db.scalars(select(Task).where(Task.status == TaskStatus.assigned)).all()
    assert len({t.assigned_expert_id for t in assigned}) == 12


def test_concurrent_http_claims_never_double_assign(client, admin_key):
    from panelist.main import app

    rubric = setup_rubric(client, admin_key)
    make_tasks(client, admin_key, rubric, [{} for _ in range(10)])
    keys = [make_expert(client, admin_key, f"E{i}", ["python"])[1] for i in range(20)]

    def worker(key):
        with TestClient(app) as c:
            r = c.post("/tasks/next", headers=h(key))
            return r.json()["id"] if r.status_code == 200 else None

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = [r for r in pool.map(worker, keys) if r]
    assert len(results) == 10
    assert len(set(results)) == 10


def test_lease_expiry_reclaims_task(client, admin_key, db):
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, rubric, [{}])
    _, a = make_expert(client, admin_key, "A", ["python"])
    _, b = make_expert(client, admin_key, "B", ["python"])
    assert claim(client, a)["id"] == tid
    assert claim(client, b) is None

    task = db.get(Task, uuid.UUID(tid))
    task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    got = claim(client, b)
    assert got is not None and got["id"] == tid
    db.refresh(task)
    assert task.reclaim_count == 1
    assert task.status == TaskStatus.assigned
    # the original holder can no longer submit
    r = client.post(
        "/grades",
        json={
            "task_id": tid,
            "scores": {"accuracy": 3, "clarity": 3, "safety": 3},
            "rationale": "x",
        },
        headers=h(a),
    )
    assert r.status_code == 409


def test_admin_reclaim_endpoint(client, admin_key, db):
    rubric = setup_rubric(client, admin_key)
    ids = make_tasks(client, admin_key, rubric, [{}, {}])
    _, a = make_expert(client, admin_key, "A", ["python"])
    claim(client, a)
    claim(client, a)
    for tid in ids:
        db.get(Task, uuid.UUID(tid)).lease_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    db.commit()
    r = client.post("/tasks/reclaim", headers=h(admin_key))
    assert r.json() == {"reclaimed": 2}
    q = client.get("/tasks/queue", headers=h(admin_key)).json()
    assert q["by_status"]["queued"] == 2
    assert q["queued_by_tag"] == {"python": 2}


def test_release_returns_task_to_queue(client, admin_key):
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, rubric, [{}])
    _, a = make_expert(client, admin_key, "A", ["python"])
    _, b = make_expert(client, admin_key, "B", ["python"])
    claim(client, a)
    assert client.post(f"/tasks/{tid}/release", headers=h(b)).status_code == 409
    assert client.post(f"/tasks/{tid}/release", headers=h(a)).status_code == 204
    assert claim(client, b)["id"] == tid


def test_expert_never_receives_task_already_graded(client, admin_key):
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, rubric, [{"required_grades": 2}])
    _, a = make_expert(client, admin_key, "A", ["python"])
    _, b = make_expert(client, admin_key, "B", ["python"])
    assert claim(client, a)["id"] == tid
    grade(client, a, tid)
    assert claim(client, a) is None
    assert claim(client, b)["id"] == tid


def test_golden_tasks_are_never_served_as_filler(client, admin_key, settings):
    settings.attention_fraction = 0.5
    rubric = setup_rubric(client, admin_key)
    make_tasks(
        client,
        admin_key,
        rubric,
        [{}]
        + [
            {
                "is_attention_check": True,
                "expected_scores": {"accuracy": 5, "clarity": 5, "safety": 5},
            }
            for _ in range(3)
        ],
    )
    _, key = make_expert(client, admin_key, "A", ["python"])
    first = claim(client, key)
    assert (
        client.get(f"/tasks/{first['id']}", headers=h(admin_key)).json()["is_attention_check"]
        is False
    )
    second = claim(client, key)
    assert (
        client.get(f"/tasks/{second['id']}", headers=h(admin_key)).json()["is_attention_check"]
        is True
    )
    grade(client, key, first["id"])
    grade(client, key, second["id"])
    # The regular queue is empty; two golden tasks remain but the third serve is not a check
    assert claim(client, key) is None
