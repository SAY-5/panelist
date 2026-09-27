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
machine: Darwin 25.0.0 arm64, 10 CPUs, PostgreSQL 16.14, Python 3.12.13, load average 15.15, 12.99, 10.83 at the start of the run, 12.22, 12.55, 10.78 at this summary
claims by matched tag (664 claims; a claim matching two of the expert's tags counts under both): biology=94, finance=80, law=83, math=106, medicine=73, python=78, security=70, writing=96
tag mismatches: 0
double-assignment attempts blocked: 40/40  (concurrent first claims: 40, unique: 40)
expired leases reclaimed: 2 (admin sweep: 0)
attention checks served: 132  failed: 4
experts paused: 2 ['expert-04', 'expert-18']
grades stored: 664  approved: 656  rejected: 2  regraded after rejection: 1
tier moves: 47 (47 up, 0 down)  experts by tier: junior=2, senior=0, lead=38
adjudications: 3 resolved by the senior reviewer, 3 outvoted grades paid at partial (0.5)
task status: {'queued': 50, 'assigned': 0, 'submitted': 0, 'adjudication': 0, 'approved': 450, 'rejected': 0}
payouts created: 662  statement 2026-09-A: 653 payouts, $5,200.50 to 38 experts
payout ledger: {'pending': 0, 'withheld': 2850, 'paid': 520050}  withheld: $28.50
inter-rater agreement: 82 multi-graded tasks, 328 score pairs, mean abs diff 0.567, exact 52.1%, within one 92.4%
criterion means: accuracy=3.67, completeness=3.65, clarity=3.72, safety=3.61
delivery v1: 450 rows, 317,807 bytes, sha256 847a92f671e2778f7ee5c4c15044b3d5aaa1018c4231bb3f0a85cbea6e15f5b3
delivery location: s3://panelist-deliveries/deliveries/panelist-grades-v1-847a92f671e2.jsonl  (s3 (http://localhost:4569))
grading wall time: 18.4s  claim latency over 780 claims: p50 301.2ms  p95 419.6ms
------------------------------------------------------------------------
OPS OVERVIEW (GET /ops/overview)
queue depth by tag: biology=12, finance=4, law=8, math=9, medicine=12, python=5, security=8, writing=12
tasks by status: {'queued': 50, 'assigned': 0, 'submitted': 0, 'adjudication': 0, 'approved': 450, 'rejected': 0}  expired leases: 0
paused experts: 2 ['expert-04', 'expert-18']  adjudication backlog: 0
period 2026-09-A: 0 payouts ($0.00) not yet in a statement, $28.50 withheld
last delivery: v1, 450 rows, 2026-09-27T05:40:02.382784Z
tick: reclaimed 0, scored 40 experts, 0 tier moves
  reminder: 1 experts have fewer than 5 calibration signals
========================================================================
```

Reading the numbers: the 50 queued tasks at the end are the golden tasks, which stay in the queue because they are reusable across experts. Both careless experts were paused in this run; their approved grades are the $28.50 withheld from the statement, and how many of the two a run pauses is not fixed, which the next paragraph sets out. The delivery carries 450 rows for 450 approved tasks: the 82 multi-graded tasks contribute the one grade their consensus round selected, not both. Three tasks fell outside the consensus tolerance and were settled by the senior reviewer, whose outvoted graders were paid half the card rate under the `partial` rule. Nothing is left in `rejected` at the end: a rejected grade on a single-grader task sends the task back to the queue, where a later round grades it again. Tier moves run one way here because the spot-check reviewer approves 656 of 664 grades, so nearly every expert clears the promote edge; demotion needs a disagreement streak, which the test suite exercises directly. The tick reclaims nothing and moves no tier because it runs after the statement close with the adjudication queue already empty; its one reminder counts the experts holding fewer than the five calibration signals a score needs, one of them here. The demo raises the served attention share to 0.2 and lowers the pause threshold to two checks so the guard trips inside a 500-task run; production defaults are 0.1 and 3.

What the seed fixes and what it does not: `seed: 7` fixes the expert roster with their tags and tiers, which two experts grade carelessly and which two abandon their first claim, the task set with its tags, types and priorities, which tasks are golden, the reference scores behind every task and each expert's grading noise. It does not fix which expert claims which task, because 40 threads race for rows: the per-tag claim counts, the number of checks served, how many of the two careless experts are paused, which grades a reviewer rejects, the agreement statistics, the delivery checksum and every duration change from run to run. The pause count is worth spelling out, because a single run reads like a rule: the guard acts only once it holds `ATTENTION_MIN_CHECKS` of an expert's checks (two in the demo) and their rolling pass rate is under the threshold, so a careless expert who has failed one is paused on their second check, while one served a single check stays active however badly they graded it. Fifteen runs of the demo at this configuration on one machine paused both careless experts in fourteen of them and one in the fifteenth, where the other careless expert had been served a single check. Only those two can fail a check at all: a careful grade stays within one of the reference score and the tolerance is one. The block above is one run at commit 67dae00 on Darwin 25.0.0 arm64 with 10 CPUs and PostgreSQL 16.14 under Python 3.12.13; `docs/demo-2026-09-26.json` is that run's artifact, its `environment` block records the one, five and fifteen minute load averages at the run's start and end (15.15 and 12.22 over one minute), and `make demo-check` rebuilds the world from the seed and compares its fingerprint with the one recorded there. Claim latency is measured client side with 40 threads against a single in-process uvicorn worker, so it is a contention figure, not a per-request cost; `uv run python -m sim.bench` measures both separately.

### Claim-path benchmark

`uv run python -m sim.bench` seeds one tag's worth of experts and tasks, then claims twice: once
with a single claimant, once with all of them, so the cost of a claim can be told apart from the
cost of queueing behind other claimants. `uv run python -m sim.bench_session --json PATH` runs a
session of three rounds on each of two servers, alternating so that a pair of rounds shares a load
window. The benchmark reaches both over loopback TCP; what differs is where they run. One is a
single uvicorn worker on a thread of the benchmark's own process, sharing that interpreter and its
GIL with the claimant threads, and the other is `uvicorn --workers 4`, four worker processes of
their own. The schema is reset before every run and every run gets a new server process with the
same settings. The session writes its six runs to one artifact, each with both of its rows, the
claimant and task counts, the commit, the machine, the CPU count, the PostgreSQL and Python
versions, and the one, five and fifteen minute load averages at the run's start and end. Two
sessions are on record, both at 40 claimants and 600 tasks, so each run makes 50 claims with one
claimant and then 520 with all 40, 13 apiece:

| session | artifact | commit | runs started, UTC | one minute load at the starts | at the ends |
| --- | --- | --- | --- | --- | --- |
| 1 | `docs/bench-2026-09-27.json` | a385a96 | 2026-09-27 11:06:14 to 11:06:47 | 7.35 to 16.60 | 7.35 to 19.60 |
| 2 | `docs/bench-2026-09-27-2.json` | e394ea6 | 2026-09-27 22:41:30 to 22:42:08 | 9.26 to 11.89 | 9.26 to 11.34 |

Both sessions ran on Darwin 25.0.0 arm64 with 10 CPUs, PostgreSQL 16.14 in the compose container and
Python 3.12.13. Session 1 was run by hand before `sim/bench_session.py` existed, and its four-worker
server was started with the command `sim/bench.py` documented at the time, which leaves the
service's defaults in place unless the shell sets them, among them an attention fraction of 0.1
where the in-process server runs with 0. Session 2 is the session script's first run. The three
readings in a cell are that session's three rounds on that server, in the order its artifact holds
them:

| session | claimants | API | claims | p50 ms | p95 ms | claims/s | tasks handed to two claimants |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 1 | one worker, in process | 50 | 9.9, 8.6, 8.4 | 25.6, 10.5, 12.2 | 81.9, 105.0, 102.6 | 0 |
| 2 | 1 | one worker, in process | 50 | 11.9, 11.8, 12.1 | 36.2, 14.3, 16.0 | 67.4, 77.9, 76.5 | 0 |
| 1 | 40 | one worker, in process | 520 | 210.3, 211.9, 220.9 | 268.7, 281.9, 315.9 | 181.7, 181.3, 170.7 | 0 |
| 2 | 40 | one worker, in process | 520 | 284.4, 323.2, 260.5 | 376.8, 634.8, 315.6 | 134.0, 110.2, 148.4 | 0 |
| 1 | 1 | `uvicorn --workers 4` | 50 | 8.3, 11.3, 9.8 | 10.8, 34.6, 25.2 | 98.7, 66.4, 83.6 | 0 |
| 2 | 1 | `uvicorn --workers 4` | 50 | 13.0, 12.3, 13.4 | 15.8, 25.3, 15.1 | 73.3, 70.8, 70.3 | 0 |
| 1 | 40 | `uvicorn --workers 4` | 520 | 96.5, 85.3, 104.1 | 204.8, 172.0, 324.6 | 308.3, 385.8, 273.1 | 0 |
| 2 | 40 | `uvicorn --workers 4` | 520 | 97.3, 94.2, 105.3 | 239.9, 222.4, 177.6 | 261.2, 277.8, 303.4 | 0 |

Across both sessions the figures range as follows:

| claimants | API | p50 ms | p95 ms | claims/s |
| --- | --- | --- | --- | --- |
| 1 | one worker, in process | 8.4 to 12.1 | 10.5 to 36.2 | 67.4 to 105.0 |
| 40 | one worker, in process | 210.3 to 323.2 | 268.7 to 634.8 | 110.2 to 181.7 |
| 1 | `uvicorn --workers 4` | 8.3 to 13.4 | 10.8 to 34.6 | 66.4 to 98.7 |
| 40 | `uvicorn --workers 4` | 85.3 to 105.3 | 172.0 to 324.6 | 261.2 to 385.8 |

A fresh session can land well outside another session's figures, and the recorded load average does
not say when it will. Session 2's runs started at one minute loads inside session 1's range, yet
every in-process round it ran had a higher p50 and a lower throughput than every one of session 1's:
its contended p50 readings sit 18 to 46 percent above session 1's highest, its single-claimant p50
readings 19 to 22 percent above, and its contended throughput 13 to 35 percent below session 1's
lowest. On the four-worker server its single-claimant p50 readings sit 9 to 19 percent above session
1's highest, while its contended p50 readings overlap session 1's. Within a session the load does
not order the rounds either: in session 1 the round that started at the highest load had each
server's highest contended p50, in session 2 it had neither server's. The absolute figures describe
the session that produced them, not the machine.

Session 2 is also the first session at a commit whose engine does not prepare statements on the
server, and a comparison finds no sign that this change accounts for the gap. Run alternately with
bc32218, the last commit whose engine prepared them, in one sitting, in process and four times each,
session 2's commit gave readings that overlap bc32218's on every figure
(`docs/prepared-statements-2026-09-27-solo.json`,
`docs/prepared-statements-2026-09-27-contended.json`). Those runs are longer than a session's: the
contended comparison seeds 2,050 tasks and makes 50 claims with one claimant and then 2,000 with all
40, 50 apiece, and the single-claimant comparison seeds 1,000 tasks for two phases of 500 claims by
one claimant. Their absolute figures are not comparable with the sessions' for that reason; what
they measure is the difference between the two commits on one workload.

What held in both sessions is the comparison between the servers. At 40 claimants every four-worker
p50 is below every one-worker p50 and every four-worker throughput above every one-worker
throughput; within a pair of rounds, one worker's p50 is 2.1 to 3.4 times four workers', and four
workers claim 1.6 to 2.5 times as fast. No task was handed to two claimants in any phase of any
round. Three things the sessions do not settle. They do not say why four workers are faster. The
four-worker server runs the claim path in four interpreters at once, with `FOR UPDATE SKIP LOCKED`
keeping their claims off each other's rows, while the in-process server runs it in one interpreter
whose GIL it shares with the 40 claimant threads; the two servers differ in both ways at once, so
these rows cannot separate the two causes. The contended p95 ranges overlap, so what four workers
reliably move is the p50 and the throughput, not the tail. And at one claimant the two servers are
within 0.5 to 2.7 ms of each other in every pair of rounds, the in-process one ahead in five of the
six, while each server's median moved by more than 3 ms between the sessions, so at one claimant
these figures do not tell the two placements apart. The artifacts record a base URL for the
four-worker rows rather than a worker count.

### Browser demo

`web/` is a static Vite and React page that runs a TypeScript port of the 1.0.0 service layer, routing, grading, attention checks, payouts, analytics and delivery, with a seeded PRNG and a virtual clock in place of PostgreSQL and the wall clock. It steps the 500-task scenario of `sim/demo.py` in the browser under those rules and prints the 1.0.0 summary block, so the claim race, the attention guard, the rate card and the delivery checksum can be poked at without a database. Rubric versions (2.0.0), calibration (3.0.0), consensus and adjudication (4.0.0) and the ops overview (5.0.0) are not modelled: the port delivers every approved grade of a multi-graded task where the service delivers the one its consensus round selected, and its summary has no tier, adjudication or ops lines. The port has its own PRNG, so its totals differ from the run above; what holds in both is what the code enforces, not what one run happened to produce: zero tag mismatches, every double claim blocked, nobody paused but a careless expert, withheld money outside the statement. The page's comparison table marks the number of paused experts as varying between service runs rather than holding, because it does. `npm install && npm run dev` inside `web/`. `npm run selfcheck` runs the port's own assertions and then replays `tests/fixtures/port_conformance.json`, a fixed scenario that `tests/test_port_conformance.py` runs through the PostgreSQL service: the port must reproduce every recorded claim, grade, attention verdict, payout, statement total, ledger, agreement statistic and the JSONL body's sha256. Its stylesheet tokens are checked for WCAG AA contrast in the same run. `make web-check` runs the typecheck, the selfcheck, the production bundle and `npm run size`, which holds the gzipped JavaScript under 73,728 bytes. CI runs the same steps in its `web` job. The page labels its own figures: the counters are a simulated run rather than a measurement, the only measured number is the compute time the run took, its storage line says in-memory rather than S3, and its run parameters are read off the simulation instead of typed into the copy. `web/vercel.json` builds it for a static host; no deployment of this page is claimed here, and the standalone showcase at showcases-lime.vercel.app/panelist is a different implementation with its own figures.

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

`make test` runs 71 tests: tag and priority routing, rubric version publishing and pinning, calibration promotion, demotion and hysteresis, consensus and adjudication, exact `/ops/overview` counts on a seeded fixture, the scheduler tick and the audit export, tier gates, concurrent claims from a thread pool at the service and HTTP layers, lease expiry and reclaim, attention-check pausing and payout withholding, rate lookup by tier and task type, period close totals against the ledger, CSV statements, rubric aggregates and agreement, reproducible export checksums, and role scopes. Tests run against PostgreSQL via Testcontainers, or a provided `TEST_DATABASE_URL` as in CI.

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
