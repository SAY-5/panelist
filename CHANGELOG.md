# Changelog

## 2.0.0

Rubric versions are immutable rows. `POST /rubrics/{id}/versions` publishes the next version, moves untouched queued tasks to it and reports how many open tasks still sit on the previous one. A task pins its rubric version at claim time (`tasks.pinned_rubric_id`); grades are validated against and reference the pinned version, so an expert holding a task when a new version lands keeps grading the version they were shown. A grade that names a superseded version is rejected with 409, new tasks cannot target a superseded version, and a version can only be published from the current one. Criterion analytics and delivery rows carry the rubric version.

## 1.0.0

Baseline release. Tag-routed task queue with row-level locking, leases and reclaim; versioned rubrics with weighted criteria; hidden attention checks with rolling pass rate, pausing and payout withholding; per-grade payouts at tier and task-type rates with period statements and CSV export; criterion and inter-rater analytics; versioned, checksummed JSONL deliveries to S3 or disk; Prometheus metrics; Terraform for ECS Fargate, RDS and S3.
