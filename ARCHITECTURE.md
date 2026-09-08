# Architecture

## Queue routing and locking

`POST /tasks/next` claims one task for the calling expert in a single transaction:

1. Expired leases are reclaimed first (`UPDATE tasks SET status='queued' ... WHERE status='assigned' AND lease_expires_at < now()`), so a task abandoned by one expert is visible to the next claimant without a separate sweeper. `POST /tasks/reclaim` exposes the same sweep for operators.
2. Eligibility is a single predicate: `status = queued`, `required_tags && expert.tags` (GIN indexed array overlap), `min_tier <= expert.tier`, and no existing grade by this expert on the task.
3. Ordering is `priority DESC, deadline ASC NULLS LAST, seq ASC`. `seq` is an identity column; bulk inserts share a `created_at`, so the sequence is what makes FIFO deterministic.
4. The row is selected with `FOR UPDATE SKIP LOCKED` and `LIMIT 1`. Two concurrent claimants lock different rows; nobody waits and nobody double-assigns. The tests exercise this with 24 threads at the service layer and 20 concurrent HTTP clients.
5. The task moves to `assigned` with `assigned_expert_id`, `assigned_at` and `lease_expires_at = now() + LEASE_SECONDS`; `served_count` on the expert increments.

`POST /tasks/{id}/claim` uses the same lock. If the row is locked or already assigned the call returns 409 and increments `panelist_double_assignment_blocked_total`.

Tasks with `required_grades > 1` return to `queued` after each grade until enough grades exist; the "already graded by this expert" predicate keeps them from bouncing back to the same person.

## Rubric versions

A rubric row is never edited. `POST /rubrics/{id}/versions` inserts a new row with the same name and `MAX(version) + 1`, stamps the previous row with `superseded_at` and `superseded_by_id`, and retargets every queued task with no grades yet (`UPDATE tasks SET rubric_id = new WHERE rubric_id = old AND status = 'queued' AND grades_received = 0`). The response reports how many tasks migrated and how many open tasks (queued with partial grades, or assigned) still reference the previous version. Publishing from a superseded version is a 409, and so is creating tasks against one.

Claiming copies `rubric_id` into `pinned_rubric_id`. Grade validation, the weighted score and `grades.rubric_id` all use the pinned version, so an expert who was shown version 1 keeps grading version 1 even if version 2 lands mid-lease. A grade may name the `rubric_id` it was produced against; a mismatch with the pinned version is a 409 that states both versions. Criterion analytics group by rubric version and delivery rows carry the version each grade was produced against.

## Attention checks

Golden tasks are ordinary tasks with `is_attention_check = true` and a hidden `expected_scores` map. They are stored in the same table and served through the same endpoint, and `TaskExpertView` never serializes the two hidden fields, so experts cannot distinguish them.

Serving is deterministic per expert: with fraction `f`, every `round(1/f)`-th serve prefers a golden task the expert has not graded. If none matches the expert's tags the regular queue is served. Golden tasks are never used as filler when the regular queue is empty, and they are reusable: after a grade they return to `queued` for other experts.

On submission the grade is compared criterion by criterion with `expected_scores`; a check passes when the largest deviation is within `ATTENTION_TOLERANCE`. The result is written to `attention_results`. The rolling pass rate over the last `ATTENTION_WINDOW` results is computed after every failed check; if at least `ATTENTION_MIN_CHECKS` exist and the rate is below `ATTENTION_THRESHOLD`, the expert is set to `paused`, all of their `pending` payouts become `withheld`, and further claims return 423. New approvals for a paused expert are created as `withheld`. An admin reinstatement (`PATCH /experts/{id}/status` to `active`) releases withheld payouts back to `pending`.

## Payouts and the ledger

Approving a grade creates exactly one payout (unique on `grade_id` and on `(task_id, expert_id)`). The amount comes from the expert's `task_rate_cents` override if set, otherwise from `rate_cards(tier, task_type)`, falling back to `(tier, 'default')`. Tier and task type are copied onto the payout so later rate changes do not rewrite history.

`POST /payouts/periods/close` creates a `payout_periods` row and moves every `pending` payout without a period into it as `paid`. Withheld payouts are left alone. Statement totals, expert counts and the ledger are `SUM`/`COUNT` queries over `payouts`; nothing stores a total that could drift from the rows. The CSV export lists each payout in the period. Every mutation writes an `audit_events` row.

## Rubric storage and aggregates

A grade is stored twice on purpose. `grades.scores_snapshot` is a JSONB copy of what the expert submitted, immutable and self-describing for exports. `grade_scores(grade_id, criterion_id, score)` is the normalized form used for every aggregate: per-criterion mean and sample stddev, pairwise agreement between two experts (joined on task and criterion), per-task agreement across all graders, and an expert's mean absolute deviation from the average of other experts on the same task and criterion. Scores are validated against the rubric on submission (exact key set, within each criterion's scale), and the weighted score is computed from criterion weights at that time.

## Delivery export

`GET /deliveries/export` selects every approved grade on a non-golden task, ordered by task sequence then expert, and serializes one JSON object per line with sorted keys and compact separators. The body is hashed with SHA-256, the next version number is taken from `MAX(version) + 1`, and the object is written to `s3://<bucket>/deliveries/panelist-grades-v<N>-<sha12>.jsonl` (or to `DELIVERY_DIR` when no bucket is configured). The same approved set always yields the same checksum; a new approval changes it. The `deliveries` table records version, checksum, location, row count and size.

## Observability

`/metrics` exposes queue depth by tag, claim latency histogram, claim outcomes, blocked double assignments, attention check results and global pass rate, experts paused, grades, payouts created by status, and the payout ledger balance by status. Gauges are refreshed on scrape from the database. Logs are JSON via structlog.

## Deployment

The Terraform root wires four modules: `network` (VPC, two public and two private subnets, IGW), `storage` (versioned, encrypted, private S3 bucket), `database` (RDS PostgreSQL 16 in private subnets, security group admitting only the service, Secrets Manager secret with the full `DATABASE_URL`) and `service` (ECS cluster with Container Insights, task definition with an `alembic upgrade head` init container and the API container reading `DATABASE_URL` from Secrets Manager, task role limited to the deliveries bucket, ALB with `/healthz` target group, CloudWatch log group).
