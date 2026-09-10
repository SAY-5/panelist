# Changelog

## 5.0.0

Operations. `GET /ops/overview` answers the questions an on-call operator asks first: queue depth by tag, task counts by status, leases that have already expired, which experts are paused and what their calibration score was, how many tasks sit in the adjudication queue, what has not yet been swept into a payout statement, and which delivery version was published last. `uv run panelist tick` is the scheduler pass: it reclaims expired leases, recomputes every expert's rolling calibration score, and prints a JSON report whose `reminders` list names the experts that are not yet calibrated, the payouts that are not in a statement and the adjudications that are still waiting. `GET /ops/audit.csv` exports the append-only audit trail, filterable by action and start time, for admins only. New Prometheus gauges cover the adjudication backlog, the number of paused experts and the expert count per tier, and a `panelist_tier_changes_total` counter records automatic promotions and demotions. `make demo` now settles its disputed tasks through a senior reviewer and prints the overview and the tick report at the end of the run. No schema change; the v4 downgrade now reassigns senior reviewer keys to `reviewer` rather than deleting rows the reviews table still references.

## 4.0.0

Consensus and adjudication for tasks that require more than one grader. When the last required grade lands, the weighted scores are compared: a spread within `CONSENSUS_TOLERANCE` records an agreed round and picks the grade closest to the mean as the one that will be delivered, and a wider spread moves the task to the new `adjudication` status and onto the queue at `GET /adjudications`. `POST /adjudications/{task_id}` needs the new `adjudications:write` scope, held by the new `senior_reviewer` role and by admins; the grade the senior reviewer picks is approved and delivered, the rest are recorded as outvoted. Outvoted graders are paid by `CONSENSUS_OUTVOTED_PAYOUT`: the full card rate, a `CONSENSUS_OUTVOTED_RATE` fraction of it, or nothing. Ordinary reviews are refused while a task is waiting for its remaining graders or for adjudication, and the delivery export now carries exactly one row per consensus task. New table `consensus`.

## 3.0.0

Expert calibration and automatic tiering. Every reviewer decision and every golden check result is an agreement signal; the rolling rate over the last `CALIBRATION_WINDOW` signals is stored on the expert and exposed at `GET /experts/{id}/calibration` with the tier change history. Once `CALIBRATION_MIN_SAMPLES` signals exist, a rate at or above `CALIBRATION_PROMOTE_AT` moves the expert up one tier and a rate at or below `CALIBRATION_DEMOTE_AT` moves them down one; the band in between changes nothing, which keeps an expert from flapping across a single edge. Routing, direct claims and payout rates read the live tier, so a demoted expert stops receiving tasks above their tier immediately. New table `tier_changes`.

## 2.0.0

Rubric versions are immutable rows. `POST /rubrics/{id}/versions` publishes the next version, moves untouched queued tasks to it and reports how many open tasks still sit on the previous one. A task pins its rubric version at claim time (`tasks.pinned_rubric_id`); grades are validated against and reference the pinned version, so an expert holding a task when a new version lands keeps grading the version they were shown. A grade that names a superseded version is rejected with 409, new tasks cannot target a superseded version, and a version can only be published from the current one. Criterion analytics and delivery rows carry the rubric version.

## 1.0.0

Baseline release. Tag-routed task queue with row-level locking, leases and reclaim; versioned rubrics with weighted criteria; hidden attention checks with rolling pass rate, pausing and payout withholding; per-grade payouts at tier and task-type rates with period statements and CSV export; criterion and inter-rater analytics; versioned, checksummed JSONL deliveries to S3 or disk; Prometheus metrics; Terraform for ECS Fargate, RDS and S3.
