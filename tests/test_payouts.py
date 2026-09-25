import csv
import io
import uuid

from sqlalchemy import func, select

from panelist.models import Payout, PayoutStatus
from tests.helpers import (
    RATES,
    RUBRIC,
    claim,
    grade,
    h,
    make_expert,
    make_tasks,
    review,
    setup_rubric,
)


def _approved_grade(
    client, admin_key, reviewer_key, rubric, tier="junior", task_type="default", **exp
):
    (tid,) = make_tasks(client, admin_key, rubric, [{"task_type": task_type}])
    expert, key = make_expert(
        client, admin_key, f"{tier}-{uuid.uuid4().hex[:4]}", ["python"], tier=tier, **exp
    )
    claim(client, key)
    g = grade(client, key, tid)
    return expert, g, review(client, reviewer_key, g["id"])


def test_approval_creates_exactly_one_payout_at_tier_rate(client, admin_key, reviewer_key, db):
    rubric = setup_rubric(client, admin_key)
    expert, g, r = _approved_grade(client, admin_key, reviewer_key, rubric, tier="junior")
    assert r["payout_status"] == "pending"
    payouts = db.scalars(select(Payout).where(Payout.grade_id == uuid.UUID(g["id"]))).all()
    assert len(payouts) == 1
    assert payouts[0].amount_cents == 300 and payouts[0].tier.value == "junior"

    # Second review of the same grade is rejected and no second payout appears
    dup = client.post(
        "/reviews", json={"grade_id": g["id"], "decision": "approve"}, headers=h(reviewer_key)
    )
    assert dup.status_code == 409
    assert db.scalar(select(func.count(Payout.id))) == 1


def test_rate_depends_on_tier_and_task_type(client, admin_key, reviewer_key, db):
    rubric = setup_rubric(client, admin_key)
    _, g1, _ = _approved_grade(client, admin_key, reviewer_key, rubric, tier="senior")
    _, g2, _ = _approved_grade(
        client, admin_key, reviewer_key, rubric, tier="senior", task_type="pairwise"
    )
    _, g3, _ = _approved_grade(
        client, admin_key, reviewer_key, rubric, tier="lead", task_type="pairwise"
    )
    _, g4, _ = _approved_grade(
        client, admin_key, reviewer_key, rubric, tier="junior", task_rate_cents=1234
    )
    amounts = {str(p.grade_id): p.amount_cents for p in db.scalars(select(Payout)).all()}
    assert amounts[g1["id"]] == 500  # senior default
    assert amounts[g2["id"]] == 650  # senior pairwise
    assert amounts[g3["id"]] == 800  # lead falls back to default
    assert amounts[g4["id"]] == 1234  # expert override


def test_rejection_creates_no_payout(client, admin_key, reviewer_key, db):
    rubric = setup_rubric(client, admin_key)
    (tid,) = make_tasks(client, admin_key, rubric, [{}])
    _, key = make_expert(client, admin_key, "A", ["python"])
    claim(client, key)
    g = grade(client, key, tid)
    r = review(client, reviewer_key, g["id"], decision="reject", reason="rationale too thin")
    assert r["payout_id"] is None
    assert db.scalar(select(func.count(Payout.id))) == 0
    assert client.get(f"/tasks/{tid}", headers=h(admin_key)).json()["status"] == "rejected"


def test_period_close_totals_match_ledger(client, admin_key, reviewer_key, db):
    rubric = setup_rubric(client, admin_key)
    for tier in ("junior", "senior", "lead", "junior"):
        _approved_grade(client, admin_key, reviewer_key, rubric, tier=tier)
    # one withheld payout must be excluded from the statement
    withheld = db.scalars(select(Payout)).first()
    withheld.status = PayoutStatus.withheld
    db.commit()

    ledger_before = client.get("/payouts/ledger", headers=h(admin_key)).json()
    expected_pending = ledger_before["totals_by_status"]["pending"]
    assert expected_pending == 300 + 500 + 800 + 300 - withheld.amount_cents

    r = client.post("/payouts/periods/close", json={"label": "2026-09"}, headers=h(admin_key))
    assert r.status_code == 201
    period = r.json()
    assert period["payout_count"] == 3
    assert period["total_cents"] == expected_pending
    assert period["expert_count"] == 3

    ledger_after = client.get("/payouts/ledger", headers=h(admin_key)).json()
    assert ledger_after["totals_by_status"]["paid"] == expected_pending
    assert ledger_after["totals_by_status"]["pending"] == 0
    assert ledger_after["totals_by_status"]["withheld"] == withheld.amount_cents
    assert ledger_after["grand_total_cents"] == ledger_before["grand_total_cents"]

    db_sum = db.scalar(
        select(func.sum(Payout.amount_cents)).where(Payout.period_id == uuid.UUID(period["id"]))
    )
    assert int(db_sum) == period["total_cents"]

    # Closing again with the same label is refused; a new period picks up nothing
    assert (
        client.post(
            "/payouts/periods/close", json={"label": "2026-09"}, headers=h(admin_key)
        ).status_code
        == 409
    )
    empty = client.post(
        "/payouts/periods/close", json={"label": "2026-10"}, headers=h(admin_key)
    ).json()
    assert empty["payout_count"] == 0 and empty["total_cents"] == 0


def test_period_csv_export(client, admin_key, reviewer_key):
    rubric = setup_rubric(client, admin_key)
    _approved_grade(client, admin_key, reviewer_key, rubric, tier="senior")
    _approved_grade(client, admin_key, reviewer_key, rubric, tier="junior")
    period = client.post(
        "/payouts/periods/close", json={"label": "w1"}, headers=h(admin_key)
    ).json()
    r = client.get(f"/payouts/periods/{period['id']}/export.csv", headers=h(reviewer_key))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 2
    assert sum(int(row["amount_cents"]) for row in rows) == period["total_cents"] == 800
    assert {row["period"] for row in rows} == {"w1"}
    assert all(row["paid_at"] for row in rows)


def test_approval_without_a_rate_card_is_422(client, admin_key, reviewer_key, db):
    rubric = client.post("/rubrics", json=RUBRIC, headers=h(admin_key)).json()["id"]
    (tid,) = make_tasks(client, admin_key, rubric, [{}])
    _, key = make_expert(client, admin_key, "A", ["python"])
    claim(client, key)
    g = grade(client, key, tid)
    r = client.post(
        "/reviews", json={"grade_id": g["id"], "decision": "approve"}, headers=h(reviewer_key)
    )
    assert r.status_code == 422 and "no rate configured for tier=junior" in r.json()["detail"]
    # nothing from the failed review was kept: the grade is still unreviewed and unpaid
    unreviewed = client.get("/grades", headers=h(reviewer_key)).json()
    assert [x["id"] for x in unreviewed] == [g["id"]]
    assert db.scalar(select(func.count(Payout.id))) == 0
    assert client.put("/rate-cards", json=RATES, headers=h(admin_key)).status_code == 204
    assert review(client, reviewer_key, g["id"])["payout_status"] == "pending"
