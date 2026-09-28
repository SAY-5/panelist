"""Benchmark artifacts: runs accumulate in one file, and a session starts a file of its own."""

import json

import pytest

# Importing sim.bench sets these as environment defaults for the server it measures.
BENCH_SETTINGS = ("ATTENTION_FRACTION", "LEASE_SECONDS", "DELIVERY_S3_BUCKET", "LOG_LEVEL")


def test_runs_append_to_an_artifact_and_a_session_will_not_reuse_one(tmp_path, monkeypatch):
    for name in BENCH_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    from sim import bench, bench_session

    artifact = tmp_path / "bench.json"
    bench.write_artifact(str(artifact), {"rows": [1]})
    bench.write_artifact(str(artifact), {"rows": [2]})
    assert json.loads(artifact.read_text())["runs"] == [{"rows": [1]}, {"rows": [2]}]
    with pytest.raises(SystemExit, match="already holds runs"):
        bench_session.main(["--json", str(artifact)])
