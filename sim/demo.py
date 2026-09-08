"""End-to-end demo: seed, route, grade, review, pay, export. Prints a summary."""

import argparse
import os
import secrets
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx
import uvicorn
from sqlalchemy import func, select

from sim.world import RATES, RUBRIC, SimExpert, World, build_world, grade_for, weighted

# Demo tuning: short leases so abandoned claims are visibly reclaimed during the run.
os.environ.setdefault("LEASE_SECONDS", "3")
os.environ.setdefault("ATTENTION_FRACTION", "0.1")
os.environ.setdefault("DELIVERY_S3_BUCKET", "panelist-deliveries")
os.environ.setdefault("AWS_ENDPOINT_URL", "http://localhost:4566")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from panelist.cli import bootstrap  # noqa: E402
from panelist.config import get_settings  # noqa: E402
from panelist.db import session_factory  # noqa: E402
from panelist.main import app  # noqa: E402
from panelist.models import Task  # noqa: E402

PORT = int(os.environ.get("DEMO_PORT", "8765"))
BASE = f"http://127.0.0.1:{PORT}"


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


def ensure_bucket() -> str:
    settings = get_settings()
    if not settings.delivery_s3_bucket:
        return "local disk"
    import boto3

    s3 = boto3.client(
        "s3", endpoint_url=settings.aws_endpoint_url or None, region_name=settings.aws_region
    )
    try:
        s3.head_bucket(Bucket=settings.delivery_s3_bucket)
    except Exception:
        s3.create_bucket(Bucket=settings.delivery_s3_bucket)
    return f"s3 ({settings.aws_endpoint_url or 'aws'})"


def client(key: str) -> httpx.Client:
    return httpx.Client(base_url=BASE, headers={"X-API-Key": key}, timeout=30)


def seed(world: World, admin: httpx.Client) -> tuple[str, str]:
    rubric = admin.post("/rubrics", json=RUBRIC).json()
    admin.put("/rate-cards", json=RATES).raise_for_status()
    for e in world.experts:
        created = admin.post(
            "/experts", json={"name": e.name, "tags": e.tags, "tier": e.tier}
        ).json()
        e.id = created["id"]
        e.key = admin.post(f"/experts/{e.id}/api-key").json()["key"]
    payloads = [dict(t.payload, rubric_id=rubric["id"]) for t in world.tasks]
    ids = admin.post("/tasks", json={"tasks": payloads}).json()["ids"]
    for t, tid in zip(world.tasks, ids, strict=True):
        t.id = tid
    reviewer = admin.post("/admin/api-keys", json={"role": "reviewer", "label": "sim"}).json()[
        "key"
    ]
    return rubric["id"], reviewer


class Stats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.routed_by_tag: Counter = Counter()
        self.mismatches = 0
        self.double_blocked = 0
        self.double_attempts = 0
        self.claims = 0
        self.empty = 0
        self.paused: list[str] = []

    def add(self, **kw) -> None:
        with self.lock:
            for k, v in kw.items():
                cur = getattr(self, k)
                if isinstance(cur, Counter):
                    cur.update(v)
                elif isinstance(cur, list):
                    cur.append(v)
                else:
                    setattr(self, k, cur + v)


def contention_round(world: World, stats: Stats) -> tuple[int, int]:
    """Every expert claims at once, then tries to steal a task someone else holds."""

    def first_claim(e: SimExpert):
        with client(e.key) as c:
            r = c.post("/tasks/next")
            return e, (r.json()["id"] if r.status_code == 200 else None)

    with ThreadPoolExecutor(max_workers=len(world.experts)) as pool:
        held = list(pool.map(first_claim, world.experts))
    task_ids = [tid for _, tid in held if tid]
    unique = len(set(task_ids))

    holders = {tid: e for e, tid in held if tid}

    def steal(e: SimExpert):
        target = next((tid for tid, holder in holders.items() if holder is not e), None)
        if target is None:
            return 0, 0
        with client(e.key) as c:
            r = c.post(f"/tasks/{target}/claim")
        return 1, int(r.status_code == 409)

    with ThreadPoolExecutor(max_workers=len(world.experts)) as pool:
        results = list(pool.map(steal, world.experts))
    stats.add(double_attempts=sum(a for a, _ in results), double_blocked=sum(b for _, b in results))

    # Release everything so the main loop starts from a clean queue, except the abandoners.
    for e, tid in held:
        if tid and not e.abandons_first:
            with client(e.key) as c:
                c.post(f"/tasks/{tid}/release")
    return len(task_ids), unique


def run_expert(e: SimExpert, world: World, stats: Stats, tasks_by_id: dict) -> None:
    rng = world.rng.__class__(world.seed ^ hash(e.name) & 0xFFFF)
    with client(e.key) as c:
        while True:
            r = c.post("/tasks/next")
            if r.status_code == 423:
                e.paused = True
                stats.add(paused=e.name)
                return
            if r.status_code == 204:
                stats.add(empty=1)
                return
            r.raise_for_status()
            task = r.json()
            sim_task = tasks_by_id[task["id"]]
            e.served += 1
            required = set(sim_task.payload["required_tags"])
            stats.add(claims=1, routed_by_tag=Counter(sorted(required)[:1]))
            if not required & set(e.tags):
                stats.add(mismatches=1)
            body = dict(task_id=task["id"], **grade_for(rng, e, sim_task))
            g = c.post("/grades", json=body)
            if g.status_code == 409:
                continue  # lease expired and the task was reclaimed
            g.raise_for_status()
            e.graded += 1


def review_all(reviewer: httpx.Client, tasks_by_id: dict) -> tuple[int, int]:
    approved = rejected = 0
    while True:
        batch = reviewer.get("/grades", params={"limit": 1000}).json()
        if not batch:
            return approved, rejected
        for g in batch:
            truth = weighted(tasks_by_id[g["task_id"]].true_scores)
            drift = abs(g["weighted_score"] - truth)
            if len(g["rationale"]) < 20 or drift > 1.5:
                reviewer.post(
                    "/reviews",
                    json={"grade_id": g["id"], "decision": "reject", "reason": "spot check failed"},
                ).raise_for_status()
                rejected += 1
            else:
                reviewer.post(
                    "/reviews", json={"grade_id": g["id"], "decision": "approve"}
                ).raise_for_status()
                approved += 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Panelist end-to-end demo")
    parser.add_argument("--experts", type=int, default=40)
    parser.add_argument("--tasks", type=int, default=500)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)

    settings = get_settings()
    world = build_world(args.seed, args.experts, args.tasks, settings.attention_fraction)
    server = start_api()
    storage = ensure_bucket()
    admin_key = bootstrap(f"pk_admin_{secrets.token_urlsafe(16)}")
    admin = client(admin_key)
    t0 = time.perf_counter()
    _, reviewer_key = seed(world, admin)
    tasks_by_id = world.by_id()
    stats = Stats()

    print(
        f"seeded {len(world.experts)} experts, {len(world.tasks)} tasks"
        f" in {time.perf_counter() - t0:.1f}s"
    )
    claimed, unique = contention_round(world, stats)
    print(f"contention round: {claimed} concurrent claims, {unique} unique tasks")

    t1 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(world.experts)) as pool:
        list(pool.map(lambda e: run_expert(e, world, stats, tasks_by_id), world.experts))
    # Abandoned leases expire after LEASE_SECONDS; reclaim and let active experts finish them.
    time.sleep(settings.lease_seconds + 0.5)
    reclaimed_now = admin.post("/tasks/reclaim").json()["reclaimed"]
    with ThreadPoolExecutor(max_workers=len(world.experts)) as pool:
        list(
            pool.map(
                lambda e: run_expert(e, world, stats, tasks_by_id),
                [e for e in world.experts if not e.paused],
            )
        )
    grading_seconds = time.perf_counter() - t1

    reviewer = client(reviewer_key)
    approved, rejected = review_all(reviewer, tasks_by_id)
    period = admin.post("/payouts/periods/close", json={"label": "2026-09-A"}).json()
    ledger = admin.get("/payouts/ledger").json()
    agreement = admin.get("/analytics/agreement/global").json()
    criteria = admin.get("/analytics/criteria").json()
    delivery = admin.get("/deliveries/export").json()
    queue = admin.get("/tasks/queue").json()
    metrics = admin.get("/metrics").text

    with session_factory()() as db:
        reclaims = int(db.scalar(select(func.coalesce(func.sum(Task.reclaim_count), 0))))
    attention = [admin.get(f"/experts/{e.id}/attention").json() for e in world.experts]
    checks_served = sum(a["checks_total"] for a in attention)
    checks_failed = checks_served - sum(a["checks_passed"] for a in attention)
    paused = [e.name for e, a in zip(world.experts, attention, strict=True) if a["paused"]]
    withheld = ledger["totals_by_status"]["withheld"]

    def metric(name: str) -> str:
        for line in metrics.splitlines():
            if line.startswith(name + " ") or line.startswith(name + "{"):
                return line.split()[-1]
        return "n/a"

    by_tag = ", ".join(f"{k}={v}" for k, v in sorted(stats.routed_by_tag.items()))
    print()
    print("=" * 72)
    print("PANELIST DEMO SUMMARY")
    print("=" * 72)
    print(f"experts: {len(world.experts)}  tasks: {len(world.tasks)}  seed: {args.seed}")
    print(f"tasks routed by tag ({stats.claims} claims): {by_tag}")
    print(f"tag mismatches: {stats.mismatches}")
    print(
        f"double-assignment attempts blocked: {stats.double_blocked}/{stats.double_attempts}"
        f"  (concurrent first claims: {claimed}, unique: {unique})"
    )
    print(f"expired leases reclaimed: {reclaims} (of which {reclaimed_now} by the admin sweep)")
    print(f"attention checks served: {checks_served}  failed: {checks_failed}")
    print(f"experts paused: {len(paused)} {paused}")
    print(
        f"grades stored: {int(float(metric('panelist_grades_total')))}"
        f"  approved: {approved}  rejected: {rejected}"
    )
    print(f"task status: {queue['by_status']}")
    print(
        f"payouts created: {sum(r['payout_count'] for r in ledger['rows'])}"
        f"  statement {period['label']}: {period['payout_count']} payouts,"
        f" ${period['total_cents'] / 100:,.2f} to {period['expert_count']} experts"
    )
    print(f"payout ledger: {ledger['totals_by_status']}  withheld: ${withheld / 100:,.2f}")
    print(
        f"inter-rater agreement: {agreement['multi_graded_tasks']} multi-graded tasks,"
        f" {agreement['compared_pairs']} score pairs,"
        f" mean abs diff {agreement['mean_abs_diff']:.3f},"
        f" exact {agreement['exact_agreement']:.1%}, within one {agreement['within_one']:.1%}"
    )
    print(
        "criterion means: " + ", ".join(f"{c['criterion_key']}={c['mean']:.2f}" for c in criteria)
    )
    print(
        f"delivery v{delivery['version']}: {delivery['row_count']} rows,"
        f" {delivery['size_bytes']:,} bytes,"
        f" sha256 {delivery['checksum']}"
    )
    print(f"delivery location: {delivery['location']}  ({storage})")
    print(f"grading wall time: {grading_seconds:.1f}s  p50 claim latency: see /metrics histogram")
    print("=" * 72)
    server.should_exit = True
    return 0 if stats.mismatches == 0 and stats.double_blocked == stats.double_attempts else 1


if __name__ == "__main__":
    raise SystemExit(main())
