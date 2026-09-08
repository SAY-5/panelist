import json
from pathlib import Path

from tests.helpers import RUBRIC, claim, grade, h, make_expert, make_tasks, review, setup_rubric

V2_CRITERIA = RUBRIC["criteria"] + [{"key": "tone", "label": "Tone", "weight": 1.0}]
V2_SCORES = {"accuracy": 4, "clarity": 4, "safety": 5, "tone": 3}


def publish(client, admin, rubric_id, criteria=V2_CRITERIA):
    r = client.post(f"/rubrics/{rubric_id}/versions", json={"criteria": criteria}, headers=h(admin))
    assert r.status_code == 201, r.text
    return r.json()


def test_publish_migrates_queued_and_keeps_in_flight_pinned(client, admin_key):
    v1 = setup_rubric(client, admin_key)
    ids = make_tasks(client, admin_key, v1, [{}, {}, {}])
    _, key = make_expert(client, admin_key, "A", ["python"])
    held = claim(client, key)
    assert held["rubric"]["version"] == 1

    out = publish(client, admin_key, v1)
    v2 = out["rubric"]["id"]
    assert out["rubric"]["version"] == 2 and out["previous_version"] == 1
    assert out["migrated_queued"] == 2 and out["open_on_previous"] == 1
    assert [c["key"] for c in out["rubric"]["criteria"]] == [
        "accuracy",
        "clarity",
        "safety",
        "tone",
    ]
    old = client.get(f"/rubrics/{v1}", headers=h(key)).json()
    assert old["superseded_by_id"] == v2 and old["superseded_at"] is not None

    task = client.get(f"/tasks/{held['id']}", headers=h(admin_key)).json()
    assert task["rubric_id"] == v1 and task["pinned_rubric_id"] == v1
    for tid in ids:
        if tid != held["id"]:
            assert client.get(f"/tasks/{tid}", headers=h(admin_key)).json()["rubric_id"] == v2

    # the in-flight task is graded against the pinned version, keys of v1 only
    g1 = grade(client, key, held["id"])
    assert g1["rubric_id"] == v1

    nxt = claim(client, key)
    assert nxt["rubric"]["version"] == 2
    r = client.post(
        "/grades",
        json={
            "task_id": nxt["id"],
            "scores": {"accuracy": 4, "clarity": 4, "safety": 5},
            "rationale": "r",
        },
        headers=h(key),
    )
    assert r.status_code == 422 and "missing=['tone']" in r.json()["detail"]
    g2 = grade(client, key, nxt["id"], V2_SCORES)
    assert g2["rubric_id"] == v2 and g2["weighted_score"] == 4.0


def test_grade_against_superseded_version_is_rejected(client, admin_key):
    v1 = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, v1, [{}])
    _, key = make_expert(client, admin_key, "A", ["python"])
    v2 = publish(client, admin_key, v1, RUBRIC["criteria"])["rubric"]["id"]
    assert claim(client, key)["rubric"]["id"] == v2

    body = {"task_id": tid, "scores": {"accuracy": 4, "clarity": 4, "safety": 5}, "rationale": "r"}
    r = client.post("/grades", json={**body, "rubric_id": v1}, headers=h(key))
    assert r.status_code == 409
    assert r.json()["detail"] == "rubric version 1 is superseded; task is pinned to version 2"
    assert client.post("/grades", json={**body, "rubric_id": v2}, headers=h(key)).status_code == 201

    r = client.post(
        "/tasks",
        json={
            "tasks": [
                {
                    "prompt": "p",
                    "responses": [{"text": "r"}],
                    "required_tags": ["python"],
                    "rubric_id": v1,
                }
            ]
        },
        headers=h(admin_key),
    )
    assert r.status_code == 409 and "superseded" in r.json()["detail"]
    r = client.post(f"/rubrics/{v1}/versions", json={"criteria": V2_CRITERIA}, headers=h(admin_key))
    assert r.status_code == 409


def test_analytics_and_delivery_group_by_rubric_version(client, admin_key, reviewer_key):
    v1 = setup_rubric(client, admin_key)
    make_tasks(client, admin_key, v1, [{}, {}])
    _, key = make_expert(client, admin_key, "A", ["python"])
    t1 = claim(client, key)
    g1 = grade(client, key, t1["id"], {"accuracy": 5, "clarity": 4, "safety": 5})
    v2 = publish(client, admin_key, v1)["rubric"]["id"]
    t2 = claim(client, key)
    g2 = grade(client, key, t2["id"], {"accuracy": 3, "clarity": 3, "safety": 3, "tone": 2})

    stats = client.get("/analytics/criteria", headers=h(reviewer_key)).json()
    by = {(s["rubric_version"], s["criterion_key"]): s for s in stats}
    assert by[(1, "accuracy")]["mean"] == 5 and by[(1, "accuracy")]["n"] == 1
    assert by[(2, "accuracy")]["mean"] == 3 and by[(2, "tone")]["n"] == 1
    assert (1, "tone") not in by
    only_v2 = client.get(f"/analytics/criteria?rubric_id={v2}", headers=h(reviewer_key)).json()
    assert {s["rubric_version"] for s in only_v2} == {2} and len(only_v2) == 4

    review(client, reviewer_key, g1["id"])
    review(client, reviewer_key, g2["id"])
    d = client.get("/deliveries/export", headers=h(admin_key)).json()
    rows = [json.loads(line) for line in Path(d["location"]).read_text().splitlines()]
    assert [(r["rubric"]["version"], r["rubric"]["id"]) for r in rows] == [(1, v1), (2, v2)]
    assert set(rows[1]["scores"]) == {"accuracy", "clarity", "safety", "tone"}
