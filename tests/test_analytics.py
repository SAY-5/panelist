from tests.helpers import claim, grade, h, make_expert, make_tasks, review, setup_rubric


def _two_graders(client, admin_key):
    rubric = setup_rubric(client, admin_key)
    ids = make_tasks(
        client, admin_key, rubric, [{"required_grades": 2}, {"required_grades": 2}, {}]
    )
    a, ka = make_expert(client, admin_key, "A", ["python"])
    b, kb = make_expert(client, admin_key, "B", ["python"])
    scores_a = [
        {"accuracy": 5, "clarity": 4, "safety": 5},
        {"accuracy": 2, "clarity": 3, "safety": 4},
        {"accuracy": 3, "clarity": 3, "safety": 3},
    ]
    scores_b = [
        {"accuracy": 5, "clarity": 2, "safety": 5},
        {"accuracy": 3, "clarity": 3, "safety": 4},
    ]
    grades_a = []
    for s in scores_a:
        t = claim(client, ka)
        grades_a.append(grade(client, ka, t["id"], s))
    for s in scores_b:
        t = claim(client, kb)
        grade(client, kb, t["id"], s)
    return rubric, ids, a, b, grades_a


def test_criterion_means(client, admin_key, reviewer_key):
    rubric, *_ = _two_graders(client, admin_key)
    stats = client.get("/analytics/criteria", headers=h(reviewer_key)).json()
    by_key = {s["criterion_key"]: s for s in stats}
    assert set(by_key) == {"accuracy", "clarity", "safety"}
    assert by_key["accuracy"]["n"] == 5
    assert abs(by_key["accuracy"]["mean"] - (5 + 2 + 3 + 5 + 3) / 5) < 1e-9
    assert by_key["safety"]["stddev"] is not None
    assert [s["criterion_key"] for s in stats] == ["accuracy", "clarity", "safety"]


def test_pair_agreement(client, admin_key, reviewer_key):
    _, _, a, b, _ = _two_graders(client, admin_key)
    r = client.get(
        f"/analytics/agreement?expert_a={a['id']}&expert_b={b['id']}", headers=h(reviewer_key)
    ).json()
    # shared tasks 1 and 2, 3 criteria each: diffs = [0,2,0, 1,0,0]
    assert r["shared_tasks"] == 2 and r["compared_scores"] == 6
    assert abs(r["exact_agreement"] - 4 / 6) < 1e-9
    assert abs(r["mean_abs_diff"] - 3 / 6) < 1e-9
    assert abs(r["within_one"] - 5 / 6) < 1e-9
    empty = client.get(
        f"/analytics/agreement?expert_a={a['id']}&expert_b={a['id']}", headers=h(reviewer_key)
    ).json()
    assert empty["shared_tasks"] == 3 and empty["exact_agreement"] == 1.0


def test_task_and_global_agreement(client, admin_key, reviewer_key):
    _, ids, *_ = _two_graders(client, admin_key)
    r = client.get(f"/analytics/tasks/{ids[0]}/agreement", headers=h(reviewer_key)).json()
    assert r["grades"] == 2
    assert r["criteria"]["clarity"]["range"] == 2
    assert abs(r["mean_pairwise_abs_diff"] - 2 / 3) < 1e-9
    g = client.get("/analytics/agreement/global", headers=h(reviewer_key)).json()
    assert g["multi_graded_tasks"] == 2 and g["compared_pairs"] == 6
    assert abs(g["mean_abs_diff"] - 0.5) < 1e-9


def test_expert_reliability(client, admin_key, reviewer_key):
    _, _, a, b, grades_a = _two_graders(client, admin_key)
    review(client, reviewer_key, grades_a[0]["id"], "approve")
    review(client, reviewer_key, grades_a[1]["id"], "reject", "inconsistent")
    r = client.get(f"/analytics/experts/{a['id']}/reliability", headers=h(reviewer_key)).json()
    assert r["grades_total"] == 3
    assert r["grades_approved"] == 1 and r["grades_rejected"] == 1
    assert r["approval_rate"] == 0.5
    assert r["attention_pass_rate"] is None
    # deviations of A from B on shared criteria: [0,2,0,1,0,0] -> 0.5
    assert abs(r["mean_abs_deviation_from_consensus"] - 0.5) < 1e-9
