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
  |  /deliveries  ---> versioned JSONL, sha256, S3 or disk    |
  |  /metrics     ---> Prometheus                             |
  +-----------------------------+-----------------------------+
                                |
                +---------------+----------------+
                v                                v
     PostgreSQL 16 (RDS)                 S3 bucket (deliveries)
     experts, tasks, rubrics,            panelist-grades-vN-<sha>.jsonl
     grades, grade_scores, reviews,
     payouts, attention_results,
     audit_events, api_keys
```

## Quick start

```bash
make setup        # uv sync with dev extras
make demo         # compose up (postgres + LocalStack), migrate, seed, simulate, summarize
make test         # pytest against a Testcontainers PostgreSQL (or TEST_DATABASE_URL)
make lint         # ruff check + format check
make tf-validate  # terraform fmt + validate
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
tasks routed by tag (658 claims): biology=119, finance=93, law=88, math=92, medicine=79, python=66, security=60, writing=61
tag mismatches: 0
double-assignment attempts blocked: 40/40  (concurrent first claims: 40, unique: 40)
expired leases reclaimed: 2 (admin sweep: 0)
attention checks served: 127  failed: 4
experts paused: 2 ['expert-04', 'expert-18']
grades stored: 658  approved: 654  rejected: 4
task status: {'queued': 50, 'assigned': 0, 'submitted': 0, 'approved': 449, 'rejected': 1}
payouts created: 654  statement 2026-09-A: 640 payouts, $3,236.50 to 38 experts
payout ledger: {'pending': 0, 'withheld': 4800, 'paid': 323650}  withheld: $48.00
inter-rater agreement: 81 multi-graded tasks, 324 score pairs, mean abs diff 0.574, exact 49.1%, within one 94.4%
criterion means: accuracy=3.66, completeness=3.66, clarity=3.67, safety=3.64
delivery v1: 529 rows, 360,618 bytes, sha256 221a22efe5ce0e186309f0b3e5ccaebfedef5f7f12b67995ca0d39491b62dff0
delivery location: s3://panelist-deliveries/deliveries/panelist-grades-v1-221a22efe5ce.jsonl  (s3 (http://localhost:4569))
grading wall time: 13.3s  claim latency over 736 claims: p50 210.6ms  p95 521.3ms
========================================================================
```

Reading the numbers: the 50 queued tasks at the end are the golden tasks, which stay in the queue because they are reusable across experts. The two paused experts are the careless ones; their 14 approved grades are the $48.00 withheld from the statement. Claim latency is measured client side with 40 threads hammering a single in-process uvicorn worker. The demo raises the served attention fraction to 0.2 and lowers the pause threshold to two checks so the guard trips inside a 500-task run; production defaults are 0.1 and 3.

### Browser demo

`web/` is a static Vite and React page that runs the routing, grading, attention, payout, analytics and delivery services as a TypeScript port, with a seeded PRNG and a virtual clock in place of PostgreSQL and the wall clock. It steps the same 500-task scenario in the browser and prints the same summary block, so the claim race, the attention guard, the rate card and the delivery checksum can be poked at without a database. `npm install && npm run dev` inside `web/`, and `npm run selfcheck` runs the assertions that hold the port to the service's behaviour.

## API

All endpoints take `X-API-Key`. Roles: `expert`, `reviewer`, `senior_reviewer`, `admin`; each role maps to a fixed scope set enforced by a FastAPI dependency.

| Method | Path | Scope | Purpose |
| --- | --- | --- | --- |
| POST | `/experts` | experts:write | Create an expert with tags, tier and optional rate override |
| POST | `/experts/{id}/api-key` | experts:write | Issue an expert key |
| GET | `/experts/me` | experts:self | Caller's expert profile |
| GET | `/experts/{id}/attention` | tasks:read | Lifetime and rolling attention pass rate |
| GET | `/experts/{id}/calibration` | tasks:read | Rolling agreement score, band settings and tier change history |
| PATCH | `/experts/{id}/status` | experts:write | Pause, reinstate (releases withheld payouts) |
| POST | `/rubrics` | rubrics:write | Versioned rubric with weighted, scaled criteria |
| POST | `/rubrics/{id}/versions` | rubrics:write | Publish an immutable new version; queued tasks move to it, claimed tasks stay pinned |
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
| POST | `/reviews` | reviews:write | Approve or reject; approval creates the payout |
| GET | `/adjudications` | tasks:read | Tasks whose graders disagreed, with every grade and the spread |
| POST | `/adjudications/{task_id}` | adjudications:write | Pick the delivered grade; the rest are outvoted |
| GET | `/payouts` | payouts:read | Payouts filtered by expert or status |
| GET | `/payouts/ledger` | payouts:read | Derived totals by expert and status |
| POST | `/payouts/periods/close` | payouts:write | Batch pending payouts into a statement |
| GET | `/payouts/periods/{id}/export.csv` | payouts:read | Statement as CSV |
| GET | `/analytics/criteria` | analytics:read | Per-criterion mean, stddev, n |
| GET | `/analytics/agreement` | analytics:read | Agreement between two experts |
| GET | `/analytics/agreement/global` | analytics:read | Agreement across all multi-graded tasks |
| GET | `/analytics/experts/{id}/reliability` | analytics:read | Approval rate, attention rate, deviation from consensus |
| GET | `/deliveries/export` | deliveries:write | Build and store a new dataset version |
| GET | `/ops/overview` | tasks:read | Queue depth by tag, paused experts, adjudication backlog, period status, last delivery |
| GET | `/ops/audit.csv` | admin | Audit trail as CSV, filterable by action and start time |
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

`deploy/terraform` provisions a VPC, an ECS Fargate service behind an ALB, RDS PostgreSQL 16, a Secrets Manager secret holding `DATABASE_URL` (injected into the task), a CloudWatch log group and an encrypted, versioned S3 bucket for deliveries. The task definition runs `alembic upgrade head` as a non-essential init container before the API starts.

```bash
make tf-validate                                     # fmt + validate, no credentials needed
cd deploy/terraform && terraform plan -var-file=environments/dev.tfvars
```

Honest note on AWS: this repository was built and verified without an AWS account. `terraform fmt` and `terraform validate` pass, and `terraform plan -var-file=environments/localstack.tfvars` against the compose LocalStack produces the full 34-resource plan (the only live calls during a plan are the availability-zone and identity data sources, which LocalStack Community serves). Applying ECS, RDS and ALB resources needs real credentials and has not been done here. Local runtime is `deploy/docker-compose.yml`: the API, PostgreSQL 16 and LocalStack S3. The `Dockerfile` is a multi-stage, non-root image built by the CI `image` job.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | local compose URL | SQLAlchemy URL (psycopg 3) |
| `LEASE_SECONDS` | 900 | Assignment lease before a task is reclaimable |
| `ATTENTION_FRACTION` | 0.1 | Share of serves that prefer a golden task |
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
| `DELIVERY_S3_BUCKET` | empty | When set, exports go to S3; otherwise `DELIVERY_DIR` |
| `AWS_ENDPOINT_URL` | empty | Set for LocalStack |

## Testing

`make test` runs 56 tests: tag and priority routing, rubric version publishing and pinning, calibration promotion, demotion and hysteresis, consensus and adjudication, exact `/ops/overview` counts on a seeded fixture, the scheduler tick and the audit export, tier gates, concurrent claims from a thread pool at the service and HTTP layers, lease expiry and reclaim, attention-check pausing and payout withholding, rate lookup by tier and task type, period close totals against the ledger, CSV statements, rubric aggregates and agreement, reproducible export checksums, and role scopes. Tests run against PostgreSQL via Testcontainers, or a provided `TEST_DATABASE_URL` as in CI.

## Releases

| Version | Highlights |
| --- | --- |
| 5.0.0 | Operations: `/ops/overview`, the `panelist tick` scheduler pass with lease reclaim and reminders, a CSV audit export, and gauges for the adjudication backlog, paused experts and tier distribution |
| 4.0.0 | Consensus over k graders: agreement inside the tolerance picks the delivered grade, disagreement opens an adjudication queue for a senior reviewer, outvoted graders are paid by a configurable rule |
| 3.0.0 | Expert calibration: rolling agreement with reviewers and golden answers, tier promotion and demotion with a hysteresis band, routing follows the live tier |
| 2.0.0 | Immutable rubric versions: publish endpoint migrates queued tasks, claimed tasks stay pinned, grades and analytics carry the version |
| 1.0.0 | Baseline: tag routing with locking and leases, rubrics, attention checks, payouts, analytics, checksummed deliveries, Terraform |

See [CHANGELOG.md](CHANGELOG.md).

## License

MIT
