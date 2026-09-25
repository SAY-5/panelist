import hashlib
import json
from pathlib import Path

from tests.helpers import claim, grade, h, make_expert, make_tasks, review, setup_rubric

GOLD = {"accuracy": 5, "clarity": 5, "safety": 5}


def _seed(client, admin_key, reviewer_key, settings):
    settings.attention_fraction = 0.25  # the fourth serve is the golden task
    rubric = setup_rubric(client, admin_key)
    ids = make_tasks(
        client,
        admin_key,
        rubric,
        [
            {"external_ref": "t-1"},
            {"external_ref": "t-2"},
            {"external_ref": "t-3"},
            {"external_ref": "gold", "is_attention_check": True, "expected_scores": GOLD},
        ],
    )
    _, key = make_expert(client, admin_key, "A", ["python"], tier="senior")
    grades = []
    for _ in range(4):
        t = claim(client, key)
        grades.append(grade(client, key, t["id"], rationale=f"Reasoned about {t['id']}"))
    review(client, reviewer_key, grades[0]["id"], "approve")
    review(client, reviewer_key, grades[1]["id"], "reject", "no")
    review(client, reviewer_key, grades[2]["id"], "approve")
    review(client, reviewer_key, grades[3]["id"], "approve")  # attention check, excluded
    return ids


def test_export_is_versioned_checksummed_and_reproducible(
    client, admin_key, reviewer_key, settings
):
    _seed(client, admin_key, reviewer_key, settings)
    first = client.post("/deliveries", headers=h(admin_key)).json()
    second = client.post("/deliveries", headers=h(admin_key)).json()
    assert first["version"] == 1 and second["version"] == 2
    assert first["row_count"] == second["row_count"] == 2
    assert first["checksum"] == second["checksum"]
    assert first["location"] != second["location"]

    body = Path(first["location"]).read_bytes()
    assert hashlib.sha256(body).hexdigest() == first["checksum"]
    assert len(body) == first["size_bytes"]
    rows = [json.loads(line) for line in body.decode().splitlines()]
    assert [r["external_ref"] for r in rows] == ["t-1", "t-3"]
    assert {r["external_ref"] for r in rows} == {"t-1", "t-3"}
    assert all(set(r["scores"]) == {"accuracy", "clarity", "safety"} for r in rows)
    assert all(r["expert_tier"] == "senior" and r["rationale"] for r in rows)
    assert rows[0]["rubric"]["name"] == "helpfulness"

    listing = client.get("/deliveries", headers=h(reviewer_key)).json()
    assert [d["version"] for d in listing] == [1, 2]
    verified = client.get("/deliveries/2/verify", headers=h(reviewer_key)).json()
    assert verified["match"] is True and verified["rows_read"] == 2


def test_export_changes_when_new_grade_is_approved(client, admin_key, reviewer_key, settings):
    _seed(client, admin_key, reviewer_key, settings)
    before = client.post("/deliveries", headers=h(admin_key)).json()
    rubric_id = json.loads(Path(before["location"]).read_text().splitlines()[0])["rubric"]["id"]
    (tid,) = make_tasks(client, admin_key, rubric_id, [{"external_ref": "t-9"}])
    _, key = make_expert(client, admin_key, "B", ["python"])
    # t-2 was rejected and is back in the queue ahead of t-9, so claim t-9 by id
    assert client.post(f"/tasks/{tid}/claim", headers=h(key)).status_code == 200
    g = grade(client, key, tid)
    review(client, reviewer_key, g["id"])
    after = client.post("/deliveries", headers=h(admin_key)).json()
    assert after["row_count"] == 3 and after["checksum"] != before["checksum"]


def test_empty_export(client, admin_key):
    d = client.post("/deliveries", headers=h(admin_key)).json()
    assert d["row_count"] == 0 and d["size_bytes"] == 0
    assert d["checksum"] == hashlib.sha256(b"").hexdigest()


def test_export_to_s3_stores_the_object_and_verify_reads_it_back(
    client, admin_key, reviewer_key, settings, monkeypatch
):
    import boto3
    from moto import mock_aws

    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(var, "testing")
    settings.delivery_s3_bucket = "panelist-test"
    settings.aws_endpoint_url = ""
    with mock_aws():
        s3 = boto3.client("s3", region_name=settings.aws_region)
        s3.create_bucket(Bucket="panelist-test")
        _seed(client, admin_key, reviewer_key, settings)
        d = client.post("/deliveries", headers=h(admin_key)).json()
        key = f"deliveries/panelist-grades-v1-{d['checksum'][:12]}.jsonl"
        assert d["location"] == f"s3://panelist-test/{key}"
        obj = s3.get_object(Bucket="panelist-test", Key=key)
        body = obj["Body"].read()
        assert hashlib.sha256(body).hexdigest() == d["checksum"] and len(body) == d["size_bytes"]
        assert obj["Metadata"] == {"sha256": d["checksum"], "version": "1"}
        assert body.count(b"\n") == d["row_count"] == 2

        v = client.get("/deliveries/1/verify", headers=h(reviewer_key)).json()
        assert v["match"] is True and v["checksum"] == d["checksum"] and v["rows_read"] == 2
        s3.put_object(Bucket="panelist-test", Key=key, Body=body + b'{"tampered":true}\n')
        v = client.get("/deliveries/1/verify", headers=h(reviewer_key)).json()
        assert v["match"] is False and v["rows_read"] == 3
        assert v["stored_checksum"] == d["checksum"] and v["checksum"] != d["checksum"]
    assert client.get("/deliveries/9/verify", headers=h(reviewer_key)).status_code == 404
