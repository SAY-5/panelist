from tests.helpers import claim, grade, h, make_expert, make_tasks, review, setup_rubric

GOLD = {"accuracy": 5, "clarity": 5, "safety": 5}


def _band(settings, window=10, min_samples=3, promote_at=0.9, demote_at=0.6):
    settings.calibration_window = window
    settings.calibration_min_samples = min_samples
    settings.calibration_promote_at = promote_at
    settings.calibration_demote_at = demote_at


def _cycle(client, admin_key, reviewer_key, rubric, key, decisions):
    ids = make_tasks(client, admin_key, rubric, [{} for _ in decisions])
    for tid, decision in zip(ids, decisions, strict=True):
        assert claim(client, key)["id"] == tid
        g = grade(client, key, tid)
        review(client, reviewer_key, g["id"], decision, None if decision == "approve" else "off")


def _expert(client, admin_key, expert_id):
    return client.get(f"/experts/{expert_id}", headers=h(admin_key)).json()


def _changes(client, admin_key, expert_id):
    cal = client.get(f"/experts/{expert_id}/calibration", headers=h(admin_key)).json()
    return [(c["from_tier"], c["to_tier"]) for c in cal["changes"]]


def test_promotion_after_agreeing_grades(client, admin_key, reviewer_key, settings):
    _band(settings)
    rubric = setup_rubric(client, admin_key)
    expert, key = make_expert(client, admin_key, "A", ["python"])
    _cycle(client, admin_key, reviewer_key, rubric, key, ["approve", "approve"])
    e = _expert(client, admin_key, expert["id"])
    assert e["tier"] == "junior" and e["calibration_samples"] == 2  # below min samples

    _cycle(client, admin_key, reviewer_key, rubric, key, ["approve"])
    e = _expert(client, admin_key, expert["id"])
    assert e["tier"] == "senior" and e["calibration_score"] == 1.0 and e["calibration_samples"] == 3
    assert _changes(client, admin_key, expert["id"]) == [("junior", "senior")]

    _cycle(client, admin_key, reviewer_key, rubric, key, ["approve"] * 3)
    assert _expert(client, admin_key, expert["id"])["tier"] == "lead"
    _cycle(client, admin_key, reviewer_key, rubric, key, ["approve"])
    assert _changes(client, admin_key, expert["id"]) == [("junior", "senior"), ("senior", "lead")]


def test_demotion_after_disagreement_streak(client, admin_key, reviewer_key, settings):
    _band(settings)
    settings.attention_fraction = 1.0
    rubric = setup_rubric(client, admin_key)
    make_tasks(client, admin_key, rubric, [{"is_attention_check": True, "expected_scores": GOLD}])
    expert, key = make_expert(client, admin_key, "S", ["python"], tier="senior")

    golden = claim(client, key)
    grade(client, key, golden["id"], {"accuracy": 1, "clarity": 1, "safety": 1})
    cal = client.get(f"/experts/{expert['id']}/calibration", headers=h(admin_key)).json()
    assert cal["score"] == 0.0 and cal["samples"] == 1 and cal["tier"] == "senior"

    _cycle(client, admin_key, reviewer_key, rubric, key, ["reject", "reject"])
    e = _expert(client, admin_key, expert["id"])
    assert e["tier"] == "junior" and e["calibration_score"] == 0.0 and e["calibration_samples"] == 3
    assert e["status"] == "active"  # one failed check is below the attention minimum
    assert _changes(client, admin_key, expert["id"]) == [("senior", "junior")]


def test_no_flapping_across_the_boundary(client, admin_key, reviewer_key, settings):
    _band(settings, window=4, min_samples=4, promote_at=1.0, demote_at=0.5)
    rubric = setup_rubric(client, admin_key)
    expert, key = make_expert(client, admin_key, "A", ["python"])

    _cycle(client, admin_key, reviewer_key, rubric, key, ["approve"] * 4)
    assert _expert(client, admin_key, expert["id"])["tier"] == "senior"

    # 0.75 sits between the demote edge (0.5) and the promote edge (1.0): no move either way
    _cycle(client, admin_key, reviewer_key, rubric, key, ["reject"])
    e = _expert(client, admin_key, expert["id"])
    assert e["tier"] == "senior" and e["calibration_score"] == 0.75
    _cycle(client, admin_key, reviewer_key, rubric, key, ["approve"])
    e = _expert(client, admin_key, expert["id"])
    assert e["tier"] == "senior" and e["calibration_score"] == 0.75

    _cycle(client, admin_key, reviewer_key, rubric, key, ["reject"])
    e = _expert(client, admin_key, expert["id"])
    assert e["tier"] == "junior" and e["calibration_score"] == 0.5

    _cycle(client, admin_key, reviewer_key, rubric, key, ["approve", "approve"])
    e = _expert(client, admin_key, expert["id"])
    assert e["tier"] == "junior" and e["calibration_score"] == 0.75
    assert _changes(client, admin_key, expert["id"]) == [("junior", "senior"), ("senior", "junior")]


def test_routing_honors_calibrated_tier(client, admin_key, reviewer_key, settings):
    _band(settings, min_samples=2)
    rubric = setup_rubric(client, admin_key)
    senior_tasks = make_tasks(
        client, admin_key, rubric, [{"min_tier": "senior", "priority": 5} for _ in range(3)]
    )
    expert, key = make_expert(client, admin_key, "A", ["python"])

    _cycle(client, admin_key, reviewer_key, rubric, key, ["approve", "approve"])
    assert _expert(client, admin_key, expert["id"])["tier"] == "senior"

    assert claim(client, key)["id"] == senior_tasks[0]
    g = grade(client, key, senior_tasks[0])
    review(client, reviewer_key, g["id"], "reject", "off")
    assert claim(client, key)["id"] == senior_tasks[1]
    g = grade(client, key, senior_tasks[1])
    review(client, reviewer_key, g["id"], "reject", "off")
    assert _expert(client, admin_key, expert["id"])["tier"] == "junior"

    assert claim(client, key) is None
    r = client.post(f"/tasks/{senior_tasks[2]}/claim", headers=h(key))
    assert r.status_code == 403 and "higher tier" in r.json()["detail"]
