"""Claim-path benchmark: what the claim endpoint costs, and what contention adds to it.

The demo reports a claim latency measured with every expert hammering one in-process worker,
which mixes the cost of the request with the cost of queueing behind other claimants. This
runs the same endpoint twice against the same database: once with a single claimant, once
with `--claimants` of them, and prints both. Latency is load sensitive, so the header records
the machine, the commit, the PostgreSQL version and the load average both when the run started
and when it finished. It seeds its own rubric, experts and tasks and leaves them behind, so it
runs against an empty database or one that already holds the demo's rows.

    DATABASE_URL=postgresql+psycopg://panelist:panelist@localhost:5439/panelist \
        uv run python -m sim.bench --claimants 40 --tasks 600

With `--base-url` it measures a server that is already running instead of starting one in
this process. Give that server the settings in `SERVER_SETTINGS`, which the in-process server
runs under, or the two rows measure different configurations; by hand that is

    ATTENTION_FRACTION=0 LOG_LEVEL=WARNING \
        uv run uvicorn panelist.main:app --port 8767 --workers 4 --log-level warning
    ... uv run python -m sim.bench --base-url http://127.0.0.1:8767

and the server can stay up across `alembic downgrade base` and `alembic upgrade head` between
runs. `--json PATH` writes the run as well as printing it: both rows, the claimant and task
counts, the commit, the machine, the CPU count, the PostgreSQL and Python versions, and the load
average at the start and the end of the run. A PATH that already holds runs is appended to.

The README's benchmark table quotes whole sessions, each one artifact under `docs/`, and
`sim/bench_session.py` is how a session is run: alternating rounds on the in-process server and
on a four-worker server it starts for each round, with the schema reset before every run.
"""

import argparse
import json
import os
import platform
import secrets
import statistics
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import uvicorn
from sqlalchemy import text

# The settings a measured server runs under. The in-process server reads them from this process's
# environment, and `sim.bench_session` starts its four-worker server with the same ones.
SERVER_SETTINGS = {
    "ATTENTION_FRACTION": "0",  # no golden serves in the measurement
    "LEASE_SECONDS": "900",
    "DELIVERY_S3_BUCKET": "",
    "LOG_LEVEL": "WARNING",
}
for _name, _value in SERVER_SETTINGS.items():
    os.environ.setdefault(_name, _value)

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
        "p50_ms": round(percentile(latencies, 0.5) * 1000, 1),
        "p95_ms": round(percentile(latencies, 0.95) * 1000, 1),
        "mean_ms": round(statistics.fmean(latencies) * 1000, 1) if latencies else 0.0,
        "throughput": round(len(claimed) / elapsed, 1) if elapsed else 0.0,
    }


def load_average() -> list[float]:
    """The three load averages, rounded, so a recorded run carries the load it ran under."""
    return [round(v, 2) for v in os.getloadavg()]


def format_load(load: list[float]) -> str:
    return ", ".join(f"{v:.2f}" for v in load)


def environment(load_start: list[float], load_end: list[float]) -> dict:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = "unknown"
    with session_factory()() as db:
        pg = str(db.execute(text("SHOW server_version")).scalar())
    return {
        "panelist_version": __version__,
        "commit": sha,
        "python": platform.python_version(),
        "machine": f"{platform.system()} {platform.release()} {platform.machine()}",
        "cpu_count": os.cpu_count(),
        "postgres": pg,
        "load_average_start": load_start,
        "load_average_end": load_end,
    }


def header(env: dict, note: str) -> list[str]:
    return [
        f"panelist {env['panelist_version']} at {env['commit'][:7]}, {env['machine']},"
        f" {env['cpu_count']} CPUs, PostgreSQL {env['postgres']},"
        f" Python {env['python']}, load average {format_load(env['load_average_start'])}"
        f" at the start, {format_load(env['load_average_end'])} at the end",
        note,
    ]


def write_artifact(path: str, record: dict) -> None:
    """Append the run to PATH, so several rounds of one configuration land in one artifact."""
    target = Path(path)
    document = json.loads(target.read_text()) if target.exists() else {"runs": []}
    document["runs"].append(record)
    target.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(f"wrote {path}, holding {len(document['runs'])} runs")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark POST /tasks/next")
    parser.add_argument("--claimants", type=int, default=40, help="concurrent claimants")
    parser.add_argument("--tasks", type=int, default=600, help="tasks to seed")
    parser.add_argument("--solo-claims", type=int, default=50, help="claims in the solo phase")
    parser.add_argument(
        "--base-url", default="", help="measure a server already running at this URL"
    )
    parser.add_argument("--json", metavar="PATH", help="also write the run, appending to PATH")
    args = parser.parse_args(argv)

    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    load_start = load_average()
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

    env = environment(load_start, load_average())
    for line in header(env, note):
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
    if args.json:
        write_artifact(
            args.json,
            {
                "environment": env,
                "run": {
                    "base_url": args.base_url,
                    "claimants": args.claimants,
                    "solo_claims": args.solo_claims,
                    "started_at": started_at,
                    "tasks": args.tasks,
                },
                "rows": rows,
                "tasks_handed_to_two_claimants": doubled,
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
