"""Claim-path benchmark: what the claim endpoint costs, and what contention adds to it.

The demo reports a claim latency measured with every expert hammering one in-process worker,
which mixes the cost of the request with the cost of queueing behind other claimants. This
runs the same endpoint twice against the same database: once with a single claimant, once
with `--claimants` of them, and prints both. Latency is load sensitive, so the header records
the machine, the commit, the PostgreSQL version and the load average at the start of the run. It
seeds its own rubric, experts and tasks and leaves them behind, so it runs against an empty
database or one that already holds the demo's rows.

    DATABASE_URL=postgresql+psycopg://panelist:panelist@localhost:5439/panelist \
        uv run python -m sim.bench --claimants 40 --tasks 600

With `--base-url` it measures a server that is already running instead of starting one in
this process, which is how the multi-worker row in the README was produced:

    uv run uvicorn panelist.main:app --port 8767 --workers 4
    ... uv run python -m sim.bench --base-url http://127.0.0.1:8767
"""

import argparse
import os
import secrets
import statistics
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import uvicorn
from sqlalchemy import text

os.environ.setdefault("ATTENTION_FRACTION", "0")  # no golden serves in the measurement
os.environ.setdefault("LEASE_SECONDS", "900")
os.environ.setdefault("DELIVERY_S3_BUCKET", "")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from panelist import __version__  # noqa: E402
from panelist.cli import bootstrap  # noqa: E402
from panelist.db import session_factory  # noqa: E402
from panelist.main import app  # noqa: E402
from sim.world import RATES, RUBRIC  # noqa: E402

PORT = int(os.environ.get("BENCH_PORT", "8766"))
BASE = f"http://127.0.0.1:{PORT}"
TAG = "python"
WORKERS = "one in-process uvicorn worker; latency is measured client side in this process"


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


def wait_for(base: str) -> None:
    for _ in range(100):
        try:
            if httpx.get(f"{base}/healthz", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise SystemExit(f"no API at {base}")


def start_api() -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            if httpx.get(f"{BASE}/healthz", timeout=1).status_code == 200:
                return server
        except httpx.HTTPError:
            time.sleep(0.1)
    raise SystemExit("API failed to start")


def client(key: str, base: str = BASE) -> httpx.Client:
    return httpx.Client(base_url=base, headers={"X-API-Key": key}, timeout=30)


def seed(admin: httpx.Client, claimants: int, tasks: int) -> list[str]:
    # The rubric name carries a run suffix so the seeding step does not collide with the demo's
    # rubric, or with an earlier benchmark run, in a database that already holds one.
    created = admin.post("/rubrics", json={**RUBRIC, "name": f"bench-{secrets.token_hex(4)}"})
    created.raise_for_status()
    rubric = created.json()
    admin.put("/rate-cards", json=RATES).raise_for_status()
    keys = []
    for i in range(claimants):
        expert = admin.post(
            "/experts", json={"name": f"bench-{i:02d}", "tags": [TAG], "tier": "senior"}
        ).json()
        keys.append(admin.post(f"/experts/{expert['id']}/api-key").json()["key"])
    payloads = [
        {
            "prompt": f"Bench prompt {i}",
            "responses": [{"model": "model-a", "text": f"Bench response {i}"}],
            "required_tags": [TAG],
            "rubric_id": rubric["id"],
        }
        for i in range(tasks)
    ]
    for start in range(0, len(payloads), 250):
        admin.post("/tasks", json={"tasks": payloads[start : start + 250]}).raise_for_status()
    return keys


def claim_batch(key: str, count: int, base: str) -> tuple[list[float], list[str]]:
    latencies: list[float] = []
    claimed: list[str] = []
    with client(key, base) as c:
        for _ in range(count):
            started = time.perf_counter()
            r = c.post("/tasks/next")
            latencies.append(time.perf_counter() - started)
            if r.status_code == 200:
                claimed.append(r.json()["id"])
            else:
                break
    return latencies, claimed


def phase(label: str, keys: list[str], per_claimant: int, base: str) -> dict:
    started = time.perf_counter()
    if len(keys) == 1:
        results = [claim_batch(keys[0], per_claimant, base)]
    else:
        with ThreadPoolExecutor(max_workers=len(keys)) as pool:
            results = list(pool.map(lambda k: claim_batch(k, per_claimant, base), keys))
    elapsed = time.perf_counter() - started
    latencies = [x for lat, _ in results for x in lat]
    claimed = [tid for _, ids in results for tid in ids]
    return {
        "label": label,
        "claimants": len(keys),
        "claims": len(claimed),
        "distinct": len(set(claimed)),
        "p50_ms": percentile(latencies, 0.5) * 1000,
        "p95_ms": percentile(latencies, 0.95) * 1000,
        "mean_ms": statistics.fmean(latencies) * 1000 if latencies else 0.0,
        "throughput": len(claimed) / elapsed if elapsed else 0.0,
    }


def header(note: str) -> list[str]:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = "unknown"
    with session_factory()() as db:
        pg = str(db.execute(text("SHOW server_version")).scalar())
    load = ", ".join(f"{v:.2f}" for v in os.getloadavg())
    return [
        f"panelist {__version__} at {sha}, PostgreSQL {pg}, {os.cpu_count()} CPUs,"
        f" load average {load}",
        note,
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark POST /tasks/next")
    parser.add_argument("--claimants", type=int, default=40, help="concurrent claimants")
    parser.add_argument("--tasks", type=int, default=600, help="tasks to seed")
    parser.add_argument("--solo-claims", type=int, default=50, help="claims in the solo phase")
    parser.add_argument(
        "--base-url", default="", help="measure a server already running at this URL"
    )
    args = parser.parse_args(argv)

    key = bootstrap(f"pk_bench_{secrets.token_urlsafe(16)}")
    base = args.base_url or BASE
    server = None
    if args.base_url:
        note = f"server at {args.base_url}; latency is measured client side in this process"
        wait_for(base)
    else:
        note = WORKERS
        server = start_api()
    rows = []
    try:
        with client(key, base) as admin:
            keys = seed(admin, args.claimants, args.tasks)
            rows.append(phase("single claimant", keys[:1], args.solo_claims, base))
            remaining = args.tasks - args.solo_claims
            rows.append(phase("all claimants", keys, max(1, remaining // len(keys)), base))
    finally:
        if server is not None:
            server.should_exit = True

    for line in header(note):
        print(line)
    columns = ("phase", "clients", "claims", "unique", "p50 ms", "p95 ms", "claims/s")
    print(
        f"{columns[0]:<16}{columns[1]:>8}{columns[2]:>8}{columns[3]:>8}"
        f"{columns[4]:>9}{columns[5]:>9}{columns[6]:>10}"
    )
    for r in rows:
        print(
            f"{r['label']:<16}{r['claimants']:>8}{r['claims']:>8}{r['distinct']:>8}"
            f"{r['p50_ms']:>9.1f}{r['p95_ms']:>9.1f}{r['throughput']:>10.1f}"
        )
    doubled = sum(r["claims"] - r["distinct"] for r in rows)
    print(f"tasks handed to two claimants: {doubled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
