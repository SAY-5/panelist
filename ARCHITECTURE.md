# Architecture

## Queue routing and locking

`POST /tasks/next` claims one task for the calling expert in a single transaction:

1. Expired leases are reclaimed first (`UPDATE tasks SET status='queued' ... WHERE status='assigned' AND lease_expires_at < now()`), so a task abandoned by one expert is visible to the next claimant without a separate sweeper. `POST /tasks/reclaim` exposes the same sweep for operators.
2. Eligibility is a single predicate: `status = queued`, `required_tags && expert.tags` (GIN indexed array overlap), `min_tier <= expert.tier`, and no existing grade by this expert on the task.
3. Ordering is `priority DESC, deadline ASC NULLS LAST, seq ASC`. `seq` is an identity column; bulk inserts share a `created_at`, so the sequence is what makes FIFO deterministic.
4. The row is selected with `FOR UPDATE SKIP LOCKED` and `LIMIT 1`. Two concurrent claimants lock different rows; nobody waits and nobody double-assigns. The tests exercise this with 24 threads at the service layer and 20 concurrent HTTP clients.
5. The task moves to `assigned` with `assigned_expert_id`, `assigned_at` and `lease_expires_at = now() + LEASE_SECONDS`; `served_count` on the expert increments.

`POST /tasks/{id}/claim` uses the same lock. If the row is locked or already assigned the call returns 409 and increments `panelist_double_assignment_blocked_total`.

Tasks with `required_grades > 1` return to `queued` after each grade until enough grades exist; the "already graded by this expert" predicate keeps them from bouncing back to the same person. A single-grader task whose grade a reviewer rejects also returns to `queued`, with `grades_received` put back, so a different expert grades it next; the rejected grade stays on record, never joins a consensus round and is never delivered. `panelist tick` lists tasks that have been sent back twice.

## Rubric versions

A rubric row is never edited. `POST /rubrics/{id}/versions` inserts a new row with the same name and `MAX(version) + 1`, stamps the previous row with `superseded_at` and `superseded_by_id`, and retargets every queued task with no grades yet (`UPDATE tasks SET rubric_id = new WHERE rubric_id = old AND status = 'queued' AND grades_received = 0`). The response reports how many tasks migrated and how many open tasks (queued with partial grades, or assigned) still reference the previous version. Publishing from a superseded version is a 409, and so is creating tasks against one.

Claiming copies `rubric_id` into `pinned_rubric_id`. Grade validation, the weighted score and `grades.rubric_id` all use the pinned version, so an expert who was shown version 1 keeps grading version 1 even if version 2 lands mid-lease. A grade may name the `rubric_id` it was produced against; a mismatch with the pinned version is a 409 that states both versions. Criterion analytics group by rubric version and delivery rows carry the version each grade was produced against.

## Attention checks

Golden tasks are ordinary tasks with `is_attention_check = true` and a hidden `expected_scores` map. They are stored in the same table and served through the same endpoint, and `TaskExpertView` never serializes the two hidden fields, so experts cannot distinguish them.

Which serves carry a check is decided by a keyed hash: `sha256(ATTENTION_KEY:expert_id:serve_number)`, read as a fraction of 2^32 and compared with `ATTENTION_FRACTION`. The decision is fixed for a given serve, so a claim can be replayed, and the share holds over many serves, but an expert counting their own serves cannot tell which one is next. A fixed every-Nth cadence, which is what this used to be, is countable: an expert could be careful on serve 10, 20, 30 and careless in between. `tests/test_attention.py` drives exactly that adversary and asserts it still gets paused. The default key is in the source, so a local run is predictable; a deployment sets `ATTENTION_KEY` to a secret. A preferred serve takes a golden task the expert has not graded; if none matches their tags the regular queue is served. Golden tasks are never used as filler when the regular queue is empty, and they are reusable: after a grade they return to `queued` for other experts. A serve the schedule marks regular can therefore come back empty while golden work is still queued.

On submission the grade is compared criterion by criterion with `expected_scores`; a check passes when the largest deviation is within `ATTENTION_TOLERANCE`. The result is written to `attention_results`. The rolling pass rate over the last `ATTENTION_WINDOW` results is computed after every failed check; if at least `ATTENTION_MIN_CHECKS` exist and the rate is below `ATTENTION_THRESHOLD`, the expert is set to `paused`, all of their `pending` payouts become `withheld`, and further claims return 423. New approvals for a paused expert are created as `withheld`. An admin reinstatement (`PATCH /experts/{id}/status` to `active`) releases withheld payouts back to `pending`.

## Calibration and tiers

Two kinds of agreement signal exist for an expert: a reviewer decision on one of their grades (approve agrees, reject disagrees) and a golden check result (pass agrees, fail disagrees). `calibration.update` runs after every review and every golden check. It merges the newest signals from `reviews` and `attention_results`, keeps the last `CALIBRATION_WINDOW`, stores the agreement rate and sample count on the expert, and, once `CALIBRATION_MIN_SAMPLES` signals exist, moves the tier one step: up when the rate is at or above `CALIBRATION_PROMOTE_AT`, down when it is at or below `CALIBRATION_DEMOTE_AT`. Rates between the two edges leave the tier alone. That gap is the hysteresis: an expert promoted at 1.0 who then takes one rejection sits at 0.75 and stays put, and one who was demoted at 0.5 needs to climb back to the promote edge, not merely above the demote edge, to return. Every move writes a `tier_changes` row and an audit event.

Routing and direct claims read `experts.tier` on every request, and payouts copy the tier at approval time, so a tier move takes effect on the next claim and the next approval.

## Consensus and adjudication

A task with `required_grades >= 2` opens a consensus round the moment its last required grade lands. `consensus.evaluate` takes the spread of the weighted scores, `max - min`, and compares it with `CONSENSUS_TOLERANCE`. Inside the tolerance the round is `agreed` and the delivered grade is the one closest to the mean, ties going to the earliest submission. Outside it the round is `adjudicating` and the task moves to the `adjudication` status, which keeps it out of the routing queue and out of the unreviewed grade list.

While a task is waiting for its remaining graders or for an adjudicator, `POST /reviews` returns 409, so a grade is never reviewed on a task a senior reviewer is about to decide. `POST /adjudications/{task_id}` requires the `adjudications:write` scope, which only the `senior_reviewer` role and admins hold. The decision writes an approval for the chosen grade and a rejection reading `outvoted in adjudication` for every other one, so the same reviews that feed calibration also carry the adjudicator's verdict.

Payouts follow from that: the delivered grade is paid the card rate, and the outvoted grades are paid according to `CONSENSUS_OUTVOTED_PAYOUT`, which is `full` (the card rate), `partial` (`CONSENSUS_OUTVOTED_RATE` of it, rounded to whole cents) or `none` (no payout row at all). The delivery export joins on the consensus row and emits only the delivered grade for a consensus task, so a task graded by three experts contributes one dataset row, not three, and that row carries the round status, the grader count and the spread.

## Operations

`GET /ops/overview` is one read of the whole system: queue depth by required tag, task counts by status, the number of assigned tasks whose lease has already expired, the paused experts with the calibration score they were carrying, the adjudication backlog, the last closed payout period together with the payouts that are not in any statement yet, and the newest delivery. Every number is a query over the same rows the API writes; nothing is cached.

`uv run panelist tick` is the scheduler pass, meant for cron or an ECS scheduled task. It reclaims expired leases, walks every expert through `calibration.update` so a score never goes stale because nobody happened to review that expert, and returns a JSON report. The `reminders` list is the part an operator reads: experts still below `CALIBRATION_MIN_SAMPLES`, payouts sitting outside a statement together with how long ago the last period closed, and tasks still waiting for an adjudicator. The tick writes an `ops.tick` audit event, so its own runs are in the same trail as everything else, and `GET /ops/audit.csv` exports that trail for admins, filtered by action and start time.

## Payouts and the ledger

Approving a grade creates exactly one payout (unique on `grade_id` and on `(task_id, expert_id)`). The amount comes from the expert's `task_rate_cents` override if set, otherwise from `rate_cards(tier, task_type)`, falling back to `(tier, 'default')`. Tier and task type are copied onto the payout so later rate changes do not rewrite history.

`POST /payouts/periods/close` creates a `payout_periods` row and moves every `pending` payout without a period into it as `paid`. Withheld payouts are left alone. Statement totals, expert counts and the ledger are `SUM`/`COUNT` queries over `payouts`; nothing stores a total that could drift from the rows. The CSV export lists each payout in the period. Every mutation writes an `audit_events` row.

## Rubric storage and aggregates

A grade is stored twice on purpose. `grades.scores_snapshot` is a JSONB copy of what the expert submitted, immutable and self-describing for exports. `grade_scores(grade_id, criterion_id, score)` is the normalized form used for every aggregate: per-criterion mean and sample stddev, pairwise agreement between two experts (joined on task and criterion), per-task agreement across all graders, and an expert's mean absolute deviation from the average of other experts on the same task and criterion. Scores are validated against the rubric on submission (exact key set, within each criterion's scale), and the weighted score is computed from criterion weights at that time.

## Delivery export

`POST /deliveries` selects every approved grade on a non-golden task, ordered by task sequence then expert, and serializes one JSON object per line with sorted keys and compact separators. The body is hashed with SHA-256, the next version number is taken from `MAX(version) + 1`, and the object is written to `s3://<bucket>/deliveries/panelist-grades-v<N>-<sha12>.jsonl` (or to `DELIVERY_DIR` when no bucket is configured). The same approved set always yields the same checksum; a new approval changes it. The `deliveries` table records version, checksum, location, row count and size. `GET /deliveries/{version}/verify` reads the stored object back from S3 or disk and recomputes the sha256, the row count and the size, so an operator can prove a delivered file is the one the row describes rather than trust the row. The export is a `POST` because every call stores a new version; listing and verifying need only `deliveries:read`, which reviewers hold.

## Observability

`/metrics` exposes counters for claim outcomes, blocked double assignments, attention check results, grades, consensus rounds by outcome, automatic tier moves by direction, experts paused, and payouts and payout amounts by status; a histogram of assignment latency; and gauges for queue depth by tag, the global attention pass rate, the payout ledger balance by status, the adjudication backlog, the number of paused experts and the expert count per tier. The gauges are refreshed from the database on every scrape, which is why a tag whose queue has drained reads 0 rather than disappearing. Logs are JSON via structlog.

## Deployment

ECS tasks run in the public subnets with `assign_public_ip = true`, which is how they reach ECR and Secrets Manager without a NAT gateway; the security group admits only the ALB. RDS stays in the private subnets. A production account would move the tasks into the private subnets behind a NAT gateway or VPC endpoints and pay for one of the two.

The Terraform root wires four modules: `network` (VPC, two public and two private subnets, IGW), `storage` (versioned, encrypted, private S3 bucket), `database` (RDS PostgreSQL 16 in private subnets, security group admitting only the service, Secrets Manager secret with the full `DATABASE_URL`) and `service` (ECS cluster with Container Insights, task definition with an `alembic upgrade head` init container and the API container reading `DATABASE_URL` and `ATTENTION_KEY` from Secrets Manager, task role limited to the deliveries bucket, ALB with `/healthz` target group, CloudWatch log group).

The API's engine does not let psycopg prepare statements on the server (`prepare_threshold=None` in `panelist/db.py`). psycopg prepares a statement once a connection has run it five times, and a prepared statement fails with `cached plan must not change result type` after a migration changes the type of a column it returns. Dropping and recreating the enum types, as `alembic downgrade base` followed by `upgrade head` does, is enough: that is how a benchmark reset turned a request to a four-worker server that outlived it into a 500. A deployment reaches the same state, because the service may run up to twice its desired count while a new task's migrate container applies `alembic upgrade head`, so the previous tasks, still serving, would meet the error on any statement whose returned column types the migration changed. Without preparation every statement is parsed and planned on each execution. Run alternately with the last commit that prepared them, the claim benchmark showed no difference beyond the spread between runs, at one claimant or at 40 (`docs/prepared-statements-2026-09-27-solo.json`, `docs/prepared-statements-2026-09-27-contended.json`).

## Browser port

`web/` is a TypeScript port of the 1.0.0 service layer: `src/sim/platform.ts` mirrors `panelist/services/{routing,grading,attention,payouts,analytics,delivery}.py`, `src/sim/world.ts` and `src/sim/demo.ts` mirror `sim/world.py` and `sim/demo.py`, and an sfc32 PRNG and a virtual clock stand in for PostgreSQL and the wall clock. Rubric versions, calibration and tiers, consensus and adjudication, and the ops overview are not ported, so a multi-graded task delivers every approved grade there and the summary block stops at the delivery line. `tests/test_port_conformance.py` runs one fixed scenario through the PostgreSQL service and records every claim, grade, attention verdict, payout, statement total, ledger figure, agreement statistic, keyed attention decision and JSONL checksum in `tests/fixtures/port_conformance.json`; `npm run selfcheck` replays the same scenario through the port and asserts each value, so the two implementations cannot drift silently. The page states that its numbers are simulated rather than measured, and the CI `web` job runs the typecheck, the selfcheck, the production bundle and a gzip size budget.
