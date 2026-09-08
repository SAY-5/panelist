from tests.helpers import h, make_expert, setup_rubric


def test_missing_key_is_401(client):
    assert client.post("/tasks/next").status_code == 401


def test_bad_key_is_401(client):
    assert client.post("/tasks/next", headers=h("pk_nope")).status_code == 401


def test_expert_cannot_create_tasks(client, admin_key):
    rubric = setup_rubric(client, admin_key)
    _, key = make_expert(client, admin_key, "Ana", ["python"])
    r = client.post(
        "/tasks",
        json={
            "tasks": [
                {
                    "prompt": "p",
                    "responses": [{"text": "r"}],
                    "required_tags": ["python"],
                    "rubric_id": rubric,
                }
            ]
        },
        headers=h(key),
    )
    assert r.status_code == 403
    assert "tasks:write" in r.json()["detail"]


def test_reviewer_cannot_claim_or_grade(client, admin_key, reviewer_key):
    assert client.post("/tasks/next", headers=h(reviewer_key)).status_code == 403
    assert (
        client.post(
            "/grades",
            json={"task_id": "00000000-0000-0000-0000-000000000000", "scores": {}, "rationale": "x"},
            headers=h(reviewer_key),
        ).status_code
        == 403
    )


def test_expert_cannot_review_or_export(client, admin_key):
    _, key = make_expert(client, admin_key, "Ana", ["python"])
    r = client.post(
        "/reviews",
        json={"grade_id": "00000000-0000-0000-0000-000000000000", "decision": "approve"},
        headers=h(key),
    )
    assert r.status_code == 403
    assert client.get("/deliveries/export", headers=h(key)).status_code == 403
    assert client.get("/payouts/ledger", headers=h(key)).status_code == 403


def test_admin_key_without_expert_cannot_claim(client, admin_key):
    r = client.post("/tasks/next", headers=h(admin_key))
    assert r.status_code == 403
    assert "not bound to an expert" in r.json()["detail"]


def test_reviewer_key_issued_by_admin_works(client, admin_key):
    r = client.post(
        "/admin/api-keys", json={"role": "reviewer", "label": "qa"}, headers=h(admin_key)
    )
    assert r.status_code == 201
    key = r.json()["key"]
    assert key.startswith("pk_reviewer_")
    assert client.get("/payouts/ledger", headers=h(key)).status_code == 200
    assert client.post("/admin/api-keys", json={"role": "admin"}, headers=h(key)).status_code == 403
