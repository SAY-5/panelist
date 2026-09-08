"""Small API-level helpers shared by the test modules."""

from fastapi.testclient import TestClient

RUBRIC = {
    "name": "helpfulness",
    "version": 1,
    "criteria": [
        {"key": "accuracy", "label": "Accuracy", "weight": 2.0},
        {"key": "clarity", "label": "Clarity", "weight": 1.0},
        {"key": "safety", "label": "Safety", "weight": 1.0},
    ],
}

RATES = [
    {"tier": "junior", "task_type": "default", "rate_cents": 300},
    {"tier": "senior", "task_type": "default", "rate_cents": 500},
    {"tier": "lead", "task_type": "default", "rate_cents": 800},
    {"tier": "senior", "task_type": "pairwise", "rate_cents": 650},
]


def h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def setup_rubric(client: TestClient, admin: str) -> str:
    r = client.post("/rubrics", json=RUBRIC, headers=h(admin))
    assert r.status_code == 201, r.text
    r2 = client.put("/rate-cards", json=RATES, headers=h(admin))
    assert r2.status_code == 204, r2.text
    return r.json()["id"]


def make_expert(client: TestClient, admin: str, name: str, tags, tier="junior", **extra):
    r = client.post(
        "/experts", json={"name": name, "tags": tags, "tier": tier, **extra}, headers=h(admin)
    )
    assert r.status_code == 201, r.text
    expert = r.json()
    k = client.post(f"/experts/{expert['id']}/api-key", headers=h(admin))
    assert k.status_code == 201, k.text
    return expert, k.json()["key"]


def make_tasks(client: TestClient, admin: str, rubric_id: str, specs: list[dict]) -> list[str]:
    tasks = []
    for i, spec in enumerate(specs):
        base = {
            "prompt": f"Prompt {i}",
            "responses": [{"model": "model-a", "text": f"Answer {i}"}],
            "required_tags": ["python"],
            "rubric_id": rubric_id,
        }
        base.update(spec)
        tasks.append(base)
    r = client.post("/tasks", json={"tasks": tasks}, headers=h(admin))
    assert r.status_code == 201, r.text
    return r.json()["ids"]


def claim(client: TestClient, key: str):
    r = client.post("/tasks/next", headers=h(key))
    if r.status_code == 204:
        return None
    assert r.status_code == 200, r.text
    return r.json()


def grade(client: TestClient, key: str, task_id: str, scores=None, rationale="Solid answer."):
    scores = scores or {"accuracy": 4, "clarity": 4, "safety": 5}
    r = client.post(
        "/grades",
        json={
            "task_id": task_id,
            "scores": scores,
            "rationale": rationale,
            "time_spent_seconds": 90,
        },
        headers=h(key),
    )
    assert r.status_code == 201, r.text
    return r.json()


def review(client: TestClient, key: str, grade_id: str, decision="approve", reason=None):
    r = client.post(
        "/reviews",
        json={"grade_id": grade_id, "decision": decision, "reason": reason},
        headers=h(key),
    )
    assert r.status_code == 201, r.text
    return r.json()
