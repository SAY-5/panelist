# Panelist

Expert grading and data delivery platform for LLM responses. Experts pull tasks from a queue that routes by expertise tag, grade model outputs against versioned rubrics, pass hidden attention checks, and are paid per approved task. Rubric scores live in PostgreSQL as normalized rows plus a JSONB snapshot; approved grades are exported as versioned, checksummed JSONL datasets. Served by FastAPI, packaged for AWS ECS Fargate.

Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, Prometheus, Terraform.

## Architecture

```
            expert API keys              reviewer / admin keys
                  |                                |
                  v                                v
  +-----------------------------------------------------------+
  |  FastAPI (uvicorn, ECS Fargate behind an ALB)             |
  |                                                           |
  |  /tasks/next  ---> routing: tag overlap, tier gate,       |
  |                    priority + deadline, FOR UPDATE        |
  |                    SKIP LOCKED, lease + reclaim           |
  |  /grades      ---> rubric validation, normalized scores,  |
  |                    attention-check evaluation, pause      |
  |  /reviews     ---> approve/reject, payout at rate card    |
  |  /payouts     ---> ledger, period close, CSV statement    |
  |  /analytics   ---> criterion means, inter-rater agreement |
  |  /adjudications --> k-grader disagreements; a senior      |
  |                    reviewer picks the grade that ships    |
  |  /deliveries  ---> versioned JSONL, sha256, S3 or disk;   |
  |                    verify reads the object back           |
  |  /ops         ---> overview, audit CSV, scheduler tick    |
  |  /metrics     ---> Prometheus                             |
  +-----------------------------+-----------------------------+
                                |
                +---------------+----------------+
                v                                v
     PostgreSQL 16 (RDS)                 S3 bucket (deliveries)
     experts, tier_changes, tasks,       panelist-grades-vN-<sha>.jsonl
     rubrics, rate_cards, grades,
     grade_scores, reviews, consensus,
     payouts, payout_periods,
     attention_results, deliveries,
     audit_events, api_keys
```

## Quick start

```bash
make setup        # uv sync with dev extras
make demo         # compose up (postgres + LocalStack), migrate, seed, simulate, summarize
make test         # pytest against a Testcontainers PostgreSQL (or TEST_DATABASE_URL)
make lint         # ruff check + format check
make tf-validate  # terraform fmt + validate
make web-check    # browser port: npm ci, typecheck, selfcheck, production bundle, size budget
make demo-check   # rebuild the seeded world and compare it with the committed run artifact
make demo-down    # stop the local stack
```

`make run` starts the API on port 8000 against `DATABASE_URL` (default `postgresql+psycopg://panelist:panelist@localhost:5439/panelist`). Create the first admin key with `uv run panelist bootstrap --key <secret>`. `uv run panelist tick` is the scheduler pass: it reclaims expired leases, refreshes every expert's calibration score and prints a JSON report with the reminders an operator should act on. Interactive docs are at `/docs`.

## Demo

`make demo` seeds 40 experts across eight expertise tags and 500 tasks (50 of them golden attention checks with hidden expected scores), then runs all 40 experts concurrently against the HTTP API. Two experts grade carelessly, two abandon their first claim, a reviewer spot-checks every grade against the known answer, a senior reviewer settles every task whose graders disagreed beyond the tolerance, a pay period is closed, the approved grades are exported to LocalStack S3, and the run ends with a scheduler tick and `GET /ops/overview`. Output of a real run:

```
========================================================================
PANELIST DEMO SUMMARY
========================================================================
experts: 40  tasks: 500 (golden: 50)  seed: 7
config: attention fraction 0.2, window 10, min checks 2, threshold 0.7, lease 3s
claims by matched tag (681 claims; a claim matching two of the expert's tags counts under both): biology=95, finance=88, law=81, math=111, medicine=80, python=80, security=65, writing=99
tag mismatches: 0
double-assignment attempts blocked: 40/40  (concurrent first claims: 40, unique: 40)
expired leases reclaimed: 2 (admin sweep: 0)
attention checks served: 148  failed: 4
experts paused: 2 ['expert-04', 'expert-18']
grades stored: 681  approved: 667  rejected: 4  regraded after rejection: 3
tier moves: 47 (47 up, 0 down)  experts by tier: junior=2, senior=0, lead=38
adjudications: 5 resolved by the senior reviewer, 5 outvoted grades paid at partial (0.5)
task status: {'queued': 50, 'assigned': 0, 'submitted': 0, 'adjudication': 0, 'approved': 450, 'rejected': 0}
payouts created: 677  statement 2026-09-A: 666 payouts, $5,355.00 to 38 experts
payout ledger: {'pending': 0, 'withheld': 3900, 'paid': 535500}  withheld: $39.00
inter-rater agreement: 83 multi-graded tasks, 332 score pairs, mean abs diff 0.560, exact 50.6%, within one 94.3%
criterion means: accuracy=3.67, completeness=3.63, clarity=3.74, safety=3.59
delivery v1: 450 rows, 317,832 bytes, sha256 8880674f5ac78b75b414354a38cf1534cf54a05ac30f0cb623865216bcec908f
delivery location: s3://panelist-deliveries/deliveries/panelist-grades-v1-8880674f5ac7.jsonl  (s3 (http://localhost:4569))
grading wall time: 20.6s  claim latency over 797 claims: p50 331.7ms  p95 540.0ms
------------------------------------------------------------------------
OPS OVERVIEW (GET /ops/overview)
queue depth by tag: biology=12, finance=4, law=8, math=9, medicine=12, python=5, security=8, writing=12
tasks by status: {'queued': 50, 'assigned': 0, 'submitted': 0, 'adjudication': 0, 'approved': 450, 'rejected': 0}  expired leases: 0
paused experts: 2 ['expert-04', 'expert-18']  adjudication backlog: 0
period 2026-09-A: 0 payouts ($0.00) not yet in a statement, $39.00 withheld
last delivery: v1, 450 rows, 2026-09-27T00:31:40.466911Z
tick: reclaimed 0, scored 40 experts, 0 tier moves
========================================================================
```

Reading the numbers: the 50 queued tasks at the end are the golden tasks, which stay in the queue because they are reusable across experts. The two paused experts are the careless ones; their approved grades are the $39.00 withheld from the statement. The delivery carries 450 rows for 450 approved tasks: the 83 multi-graded tasks contribute the one grade their consensus round selected, not both. Five tasks fell outside the consensus tolerance and were settled by the senior reviewer, whose outvoted graders were paid half the card rate under the `partial` rule. Nothing is left in `rejected`: a rejected grade on a single-grader task sends the task back to the queue, and the three tasks that happened to were regraded by someone else. Tier moves run one way here because the spot-check reviewer approves 667 of 681 grades, so nearly every expert clears the promote edge; demotion needs a disagreement streak, which the test suite exercises directly. The tick reports nothing to chase because it runs after the statement close, with the adjudication queue already empty. The demo raises the served attention share to 0.2 and lowers the pause threshold to two checks so the guard trips inside a 500-task run; production defaults are 0.1 and 3.

What the seed fixes and what it does not: `seed: 7` fixes the expert roster with their tags and tiers, which two experts grade carelessly and which two abandon their first claim, the task set with its tags, types and priorities, which tasks are golden, the reference scores behind every task and each expert's grading noise. It does not fix which expert claims which task, because 40 threads race for rows: the per-tag claim counts, the number of checks served, which grades a reviewer rejects, the agreement statistics, the delivery checksum and every duration change from run to run. The block above is one run at commit df7ae50 on macOS 25.0.0 arm64 with 10 CPUs and PostgreSQL 16.14 under Python 3.12.13, with the machine's load average between 20 and 28 while it ran; `docs/demo-2026-09-26.json` is that run's artifact, and `make demo-check` rebuilds the world from the seed and compares its fingerprint with the one recorded there. Claim latency is measured client side with 40 threads against a single in-process uvicorn worker, so it is a contention figure, not a per-request cost; `uv run python -m sim.bench` measures both separately.

### Claim-path benchmark

`uv run python -m sim.bench` seeds one tag's worth of experts and tasks, then claims twice: once
with a single claimant, once with all of them, so the cost of a claim can be told apart from the
cost of queueing behind other claimants. Measured at commit df7ae50 on macOS 25.0.0 arm64, 10
CPUs, PostgreSQL 16.14 in the compose container, load average between 22 and 27 while the runs
happened, which is a busy machine and inflates every number below:

| claimants | API | claims | p50 | p95 | claims/s | tasks handed to two claimants |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | one worker, in process | 50 | 13.3 ms | 16.1 ms | 68.5 | 0 |
| 40 | one worker, in process | 520 | 294.9 ms | 472.4 ms | 125.0 | 0 |
| 1 | `uvicorn --workers 4` | 50 | 21.4 ms | 48.3 ms | 39.3 | 0 |
| 40 | `uvicorn --workers 4` | 520 | 144.7 ms | 350.1 ms | 234.4 | 0 |

A claim costs about 13 ms when nothing competes for the worker. The 295 ms at 40 claimants is
almost entirely queueing: four workers halve it and nearly double throughput on the same
database, because `FOR UPDATE SKIP LOCKED` lets the four processes claim different rows rather
than wait on each other. No task was ever handed to two claimants in any of the four phases. The
single-claimant row is slower against the four-worker server because each request crosses a real
socket to another process instead of staying in this one.

### Browser demo

`web/` is a static Vite and React page that runs a TypeScript port of the 1.0.0 service layer, routing, grading, attention checks, payouts, analytics and delivery, with a seeded PRNG and a virtual clock in place of PostgreSQL and the wall clock. It steps the 500-task scenario of `sim/demo.py` in the browser under those rules and prints the 1.0.0 summary block, so the claim race, the attention guard, the rate card and the delivery checksum can be poked at without a database. Rubric versions (2.0.0), calibration (3.0.0), consensus and adjudication (4.0.0) and the ops overview (5.0.0) are not modelled: the port delivers every approved grade of a multi-graded task where the service delivers the one its consensus round selected, and its summary has no tier, adjudication or ops lines. The port has its own PRNG, so its totals differ from the run above; the invariants hold in both: zero tag mismatches, every double claim blocked, the two careless experts paused, withheld money outside the statement. `npm install && npm run dev` inside `web/`. `npm run selfcheck` runs the port's own assertions and then replays `tests/fixtures/port_conformance.json`, a fixed scenario that `tests/test_port_conformance.py` runs through the PostgreSQL service: the port must reproduce every recorded claim, grade, attention verdict, payout, statement total, ledger, agreement statistic and the JSONL body's sha256. Its stylesheet tokens are checked for WCAG AA contrast in the same run. `make web-check` runs the typecheck, the selfcheck, the production bundle and `npm run size`, which holds the gzipped JavaScript under 73,728 bytes. CI runs the same steps in its `web` job. The page labels its own figures: the counters are a simulated run rather than a measurement, the only measured number is the compute time the run took, its storage line says in-memory rather than S3, and its run parameters are read off the simulation instead of typed into the copy. `web/vercel.json` builds it for a static host; no deployment of this page is claimed here, and the standalone showcase at showcases-lime.vercel.app/panelist is a different implementation with its own figures.

## API

All endpoints take `X-API-Key`. Roles: `expert`, `reviewer`, `senior_reviewer`, `admin`; each role maps to a fixed scope set enforced by a FastAPI dependency.

| Method | Path | Scope | Purpose |
| --- | --- | --- | --- |
| POST | `/experts` | experts:write | Create an expert with tags, tier and optional rate override |
| POST | `/experts/{id}/api-key` | experts:write | Issue an expert key |
| GET | `/experts/me` | experts:self | Caller's expert profile |
| GET | `/experts/{id}/attention` | tasks:read | Lifetime and rolling attention pass rate |
| GET | `/experts/{id}` | tasks:read | One expert, including tier, tags and calibration score |
| GET | `/experts/{id}/calibration` | tasks:read | Rolling agreement score, band settings and tier change history |
| PATCH | `/experts/{id}/status` | experts:write | Pause, reinstate (releases withheld payouts) |
| POST | `/rubrics` | rubrics:write | Versioned rubric with weighted, scaled criteria |
| POST | `/rubrics/{id}/versions` | rubrics:write | Publish an immutable new version; queued tasks move to it, claimed tasks stay pinned |
| GET | `/rubrics/{id}` | experts:self | One rubric version with its criteria, as an expert sees it |
| PUT | `/rate-cards` | payouts:write | Rate per (tier, task type) |
| POST | `/tasks` | tasks:write | Bulk create tasks, including golden ones |
| POST | `/tasks/next` | tasks:claim | Claim the best eligible task (204 when none) |
| POST | `/tasks/{id}/claim` | tasks:claim | Claim a specific task (409 if held) |
| POST | `/tasks/{id}/release` | tasks:claim | Give a claimed task back |
| POST | `/tasks/reclaim` | tasks:write | Return expired leases to the queue |
| GET | `/tasks/queue` | tasks:read | Depth by status and by tag |
| GET | `/tasks/{id}` | tasks:read | Full task, including hidden fields |
| POST | `/grades` | grades:write | Submit rubric scores, rationale, time spent; optional `rubric_id` must match the pinned version (409 otherwise) |
| GET | `/grades` | grades:read | Unreviewed grades |
| GET | `/grades/{id}` | grades:read | One grade with its scores, rationale and review |
| POST | `/reviews` | reviews:write | Approve or reject; approval creates the payout |
| GET | `/adjudications` | tasks:read | Tasks whose graders disagreed, with every grade and the spread |
| POST | `/adjudications/{task_id}` | adjudications:write | Pick the delivered grade; the rest are outvoted |
| GET | `/payouts` | payouts:read | Payouts filtered by expert or status |
| GET | `/payouts/ledger` | payouts:read | Derived totals by expert and status |
| POST | `/payouts/periods/close` | payouts:write | Batch pending payouts into a statement |
| GET | `/payouts/periods/{id}/export.csv` | payouts:read | Statement as CSV |
| GET | `/payouts/periods/{id}` | payouts:read | One statement with its totals |
| GET | `/analytics/criteria` | analytics:read | Per-criterion mean, stddev, n |
| GET | `/analytics/agreement` | analytics:read | Agreement between two experts |
| GET | `/analytics/tasks/{id}/agreement` | analytics:read | Pairwise agreement between the graders of one task |
| GET | `/analytics/agreement/global` | analytics:read | Agreement across all multi-graded tasks |
| GET | `/analytics/experts/{id}/reliability` | analytics:read | Approval rate, attention rate, deviation from consensus |
| POST | `/deliveries` | deliveries:write | Build, checksum and store a new dataset version |
| GET | `/deliveries` | deliveries:read | Stored versions with checksum, location, row count and size |
| GET | `/deliveries/{version}/verify` | deliveries:read | Read the stored object back and recompute its sha256, row count and size |
| GET | `/ops/overview` | tasks:read | Queue depth by tag, paused experts, adjudication backlog, period status, last delivery |
| GET | `/ops/audit.csv` | admin | Audit trail as CSV, filterable by action and start time |
| POST | `/admin/api-keys` | admin | Issue a reviewer, senior reviewer or admin key |
| DELETE | `/admin/api-keys/{id}` | admin | Revoke a key; it fails authentication from the next request on |
| GET | `/metrics` | none | Prometheus metrics |
| GET | `/healthz` | none | Liveness with a database round trip |

Experts only ever see `TaskExpertView`: prompt, responses, rubric, deadline and lease. `is_attention_check` and `expected_scores` are never serialized for them.

## Data model

- `experts`: name, `tags[]` (GIN indexed), tier, optional per-task rate override, status, `served_count`, `calibration_score` and `calibration_samples`.
- `tier_changes`: one row per automatic promotion or demotion with the score and sample count that triggered it.
- `rubrics` and `rubric_criteria`: immutable versions per name; a superseded version records `superseded_at` and `superseded_by_id`. Each criterion has key, weight, scale and position.
- `rate_cards`: `(tier, task_type) -> rate_cents`, with a `default` task type fallback.
- `tasks`: prompt, `responses` (JSONB), `required_tags[]`, task type, `min_tier`, priority, deadline, `required_grades`, `seq` for FIFO tiebreak, lease columns, `rubric_id` (follows the newest version while queued) and `pinned_rubric_id` (fixed at claim), `is_attention_check` and hidden `expected_scores`.
- `grades`: `scores_snapshot` (JSONB), rationale, time spent, weighted score; one per (task, expert).
- `grade_scores`: normalized `(grade_id, criterion_id, score)`, the source for all aggregates.
- `consensus`: one row per task graded by two or more experts, holding the spread, the tolerance in force, the delivered grade and, for an adjudicated round, the senior reviewer key and their reason.
- `reviews`, `payouts`, `payout_periods`: one payout per approved grade; totals are computed from rows, never stored by hand.
- `attention_results`, `audit_events`, `api_keys` (sha256 hashes only), `deliveries`.

See [ARCHITECTURE.md](ARCHITECTURE.md) for routing, locking, attention-check, payout and export design.

## Deployment

`deploy/terraform` provisions a VPC, an ECS Fargate service behind an ALB, RDS PostgreSQL 16, Secrets Manager secrets holding `DATABASE_URL` and `ATTENTION_KEY` (both injected into the task), a CloudWatch log group and an encrypted, versioned S3 bucket for deliveries. The task definition runs `alembic upgrade head` as a non-essential init container before the API starts. The tasks themselves sit in the public subnets with public IPs so they can reach ECR and Secrets Manager without a NAT gateway, and their security group admits only the ALB; the database stays private. Moving the tasks into the private subnets means paying for a NAT gateway or VPC endpoints, which this repository does not provision.

```bash
make tf-validate                                     # fmt + validate, no credentials needed
cd deploy/terraform && terraform plan -var-file=environments/dev.tfvars
```

Honest note on AWS: this repository was built and verified without an AWS account. `terraform fmt` and `terraform validate` pass, and `terraform plan -var-file=environments/localstack.tfvars` against the compose LocalStack produces the full 36-resource plan (the only live calls during a plan are the availability-zone and identity data sources, which LocalStack Community serves). Applying ECS, RDS and ALB resources needs real credentials and has not been done here. Local runtime is `deploy/docker-compose.yml`: the API, PostgreSQL 16 and LocalStack S3. The `Dockerfile` is a multi-stage, non-root image built by the CI `image` job.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | local compose URL | SQLAlchemy URL (psycopg 3) |
| `LEASE_SECONDS` | 900 | Assignment lease before a task is reclaimable |
| `ATTENTION_FRACTION` | 0.1 | Share of serves that prefer a golden task |
| `ATTENTION_KEY` | panelist | Salts the hash that decides which serves carry a check; set a secret in production |
| `ATTENTION_WINDOW` | 10 | Rolling window of checks per expert |
| `ATTENTION_MIN_CHECKS` | 3 | Checks required before the guard can trip |
| `ATTENTION_THRESHOLD` | 0.7 | Rolling pass rate below which the expert is paused |
| `ATTENTION_TOLERANCE` | 1.0 | Max per-criterion deviation from the expected score |
| `CALIBRATION_WINDOW` | 20 | Rolling window of agreement signals (reviews and golden checks) per expert |
| `CALIBRATION_MIN_SAMPLES` | 5 | Signals required before a tier can move |
| `CALIBRATION_PROMOTE_AT` | 0.9 | Agreement rate at or above which the expert moves up one tier |
| `CALIBRATION_DEMOTE_AT` | 0.6 | Agreement rate at or below which the expert moves down one tier |
| `CONSENSUS_TOLERANCE` | 1.0 | Weighted-score spread a k-grader task may show before it needs adjudication |
| `CONSENSUS_OUTVOTED_PAYOUT` | partial | Payout rule for outvoted graders: `full`, `partial` or `none` |
| `CONSENSUS_OUTVOTED_RATE` | 0.5 | Fraction of the card rate paid under the `partial` rule |
| `DELIVERY_DIR` | ./deliveries | Directory the export writes to when no bucket is set |
| `DELIVERY_S3_BUCKET` | empty | When set, exports go to S3; otherwise `DELIVERY_DIR` |
| `AWS_ENDPOINT_URL` | empty | Set for LocalStack |
| `AWS_REGION` | us-east-1 | Region for the S3 client |
| `LOG_LEVEL` | INFO | Level for the JSON application log |
| `BOOTSTRAP_ADMIN_KEY` | empty | Read by `panelist bootstrap` when no key is given on the command line |

## Testing

`make test` runs 69 tests: tag and priority routing, rubric version publishing and pinning, calibration promotion, demotion and hysteresis, consensus and adjudication, exact `/ops/overview` counts on a seeded fixture, the scheduler tick and the audit export, tier gates, concurrent claims from a thread pool at the service and HTTP layers, lease expiry and reclaim, attention-check pausing and payout withholding, rate lookup by tier and task type, period close totals against the ledger, CSV statements, rubric aggregates and agreement, reproducible export checksums, and role scopes. Tests run against PostgreSQL via Testcontainers, or a provided `TEST_DATABASE_URL` as in CI.

## Releases

| Version | Highlights |
| --- | --- |
| 5.1.0 | Correctness and honesty pass: one error handler, consensus redelivery and requeue of rejected tasks, `POST /deliveries` with a verify endpoint and a read scope, keyed attention scheduling, key revocation, documentation tables pinned by a test, and a browser port whose figures say what produced them |
| 5.0.0 | Operations: `/ops/overview`, the `panelist tick` scheduler pass with lease reclaim and reminders, a CSV audit export, and gauges for the adjudication backlog, paused experts and tier distribution |
| 4.0.0 | Consensus over k graders: agreement inside the tolerance picks the delivered grade, disagreement opens an adjudication queue for a senior reviewer, outvoted graders are paid by a configurable rule |
| 3.0.0 | Expert calibration: rolling agreement with reviewers and golden answers, tier promotion and demotion with a hysteresis band, routing follows the live tier |
| 2.0.0 | Immutable rubric versions: publish endpoint migrates queued tasks, claimed tasks stay pinned, grades and analytics carry the version |
| 1.0.0 | Baseline: tag routing with locking and leases, rubrics, attention checks, payouts, analytics, checksummed deliveries, Terraform |

See [CHANGELOG.md](CHANGELOG.md).

## License

MIT
