"""Aggregates over normalized rubric scores."""

from itertools import combinations

from sqlalchemy import Integer, and_, cast, func, select
from sqlalchemy.orm import Session, aliased

from panelist.models import (
    AttentionResult,
    Grade,
    GradeScore,
    Review,
    ReviewDecision,
    Rubric,
    RubricCriterion,
    Task,
)


def criterion_means(db: Session, rubric_id=None) -> list[dict]:
    stmt = (
        select(
            Rubric.id,
            Rubric.name,
            Rubric.version,
            RubricCriterion.key,
            func.avg(GradeScore.score),
            func.stddev_samp(GradeScore.score),
            func.count(GradeScore.score),
        )
        .join(RubricCriterion, RubricCriterion.id == GradeScore.criterion_id)
        .join(Rubric, Rubric.id == RubricCriterion.rubric_id)
        .group_by(
            Rubric.id, Rubric.name, Rubric.version, RubricCriterion.key, RubricCriterion.position
        )
        .order_by(Rubric.name, Rubric.version, RubricCriterion.position)
    )
    if rubric_id is not None:
        stmt = stmt.where(Rubric.id == rubric_id)
    return [
        {
            "rubric_id": rid,
            "rubric_name": name,
            "rubric_version": int(version),
            "criterion_key": key,
            "mean": float(mean),
            "stddev": float(sd) if sd is not None else None,
            "n": int(n),
        }
        for rid, name, version, key, mean, sd, n in db.execute(stmt).all()
    ]


def pair_agreement(db: Session, expert_a, expert_b) -> dict:
    """Agreement between two experts on tasks both graded, per criterion score."""
    ga, gb = aliased(Grade), aliased(Grade)
    sa, sb = aliased(GradeScore), aliased(GradeScore)
    rows = db.execute(
        select(ga.task_id, sa.score, sb.score)
        .join(gb, (gb.task_id == ga.task_id) & (gb.expert_id == expert_b))
        .join(sa, sa.grade_id == ga.id)
        .join(sb, (sb.grade_id == gb.id) & (sb.criterion_id == sa.criterion_id))
        .where(ga.expert_id == expert_a)
    ).all()
    tasks = {r[0] for r in rows}
    diffs = [abs(a - b) for _, a, b in rows]
    n = len(diffs)
    return {
        "expert_a": expert_a,
        "expert_b": expert_b,
        "shared_tasks": len(tasks),
        "compared_scores": n,
        "exact_agreement": (sum(1 for d in diffs if d == 0) / n) if n else None,
        "mean_abs_diff": (sum(diffs) / n) if n else None,
        "within_one": (sum(1 for d in diffs if d <= 1) / n) if n else None,
    }


def task_agreement(db: Session, task_id) -> dict:
    rows = db.execute(
        select(Grade.expert_id, RubricCriterion.key, GradeScore.score)
        .join(GradeScore, GradeScore.grade_id == Grade.id)
        .join(RubricCriterion, RubricCriterion.id == GradeScore.criterion_id)
        .where(Grade.task_id == task_id)
    ).all()
    by_criterion: dict[str, dict] = {}
    for expert_id, key, score in rows:
        by_criterion.setdefault(key, {})[str(expert_id)] = score
    experts = {str(e) for e, _, _ in rows}
    pair_diffs = []
    criteria_out = {}
    for key, scores in by_criterion.items():
        values = list(scores.values())
        diffs = [abs(x - y) for x, y in combinations(values, 2)]
        pair_diffs.extend(diffs)
        criteria_out[key] = {
            "scores": scores,
            "mean": sum(values) / len(values),
            "range": max(values) - min(values),
        }
    return {
        "task_id": task_id,
        "grades": len(experts),
        "criteria": criteria_out,
        "mean_pairwise_abs_diff": (sum(pair_diffs) / len(pair_diffs)) if pair_diffs else None,
    }


def global_agreement(db: Session) -> dict:
    """Mean pairwise absolute score difference across every multi-graded, non-golden task.

    One query: every unordered pair of grades on the same task, joined on criterion, with
    `gb.id < ga.id` so each pair is counted once.
    """
    ga, gb = aliased(Grade), aliased(Grade)
    sa, sb = aliased(GradeScore), aliased(GradeScore)
    diff = func.abs(sa.score - sb.score)
    pairs, total_diff, exact, within_one, tasks = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(diff), 0.0),
            func.coalesce(func.sum(cast(sa.score == sb.score, Integer)), 0),
            func.coalesce(func.sum(cast(diff <= 1, Integer)), 0),
            func.count(func.distinct(ga.task_id)),
        )
        .select_from(ga)
        .join(gb, and_(gb.task_id == ga.task_id, gb.id < ga.id))
        .join(Task, Task.id == ga.task_id)
        .join(sa, sa.grade_id == ga.id)
        .join(sb, and_(sb.grade_id == gb.id, sb.criterion_id == sa.criterion_id))
        .where(Task.is_attention_check.is_(False))
    ).one()
    n = int(pairs)
    return {
        "multi_graded_tasks": int(tasks),
        "compared_pairs": n,
        "mean_abs_diff": (float(total_diff) / n) if n else None,
        "exact_agreement": (int(exact) / n) if n else None,
        "within_one": (int(within_one) / n) if n else None,
    }


def expert_reliability(db: Session, expert_id) -> dict:
    review_rows = db.execute(
        select(Review.decision, func.count())
        .join(Grade, Grade.id == Review.grade_id)
        .where(Grade.expert_id == expert_id)
        .group_by(Review.decision)
    ).all()
    approved = sum(int(n) for d, n in review_rows if d == ReviewDecision.approve)
    rejected = sum(int(n) for d, n in review_rows if d == ReviewDecision.reject)
    total = int(db.scalar(select(func.count(Grade.id)).where(Grade.expert_id == expert_id)) or 0)
    att = db.execute(
        select(
            func.count(), func.coalesce(func.sum(cast(AttentionResult.passed, Integer)), 0)
        ).where(AttentionResult.expert_id == expert_id)
    ).one()
    att_total, att_passed = int(att[0]), int(att[1])

    # Deviation from the mean of other experts' scores on the same task and criterion
    other = aliased(Grade)
    other_scores = aliased(GradeScore)
    mine = (
        select(Grade.task_id, GradeScore.criterion_id, GradeScore.score.label("mine"))
        .join(GradeScore, GradeScore.grade_id == Grade.id)
        .where(Grade.expert_id == expert_id)
        .subquery()
    )
    consensus = (
        select(
            other.task_id,
            other_scores.criterion_id,
            func.avg(other_scores.score).label("others"),
        )
        .join(other_scores, other_scores.grade_id == other.id)
        .where(other.expert_id != expert_id)
        .group_by(other.task_id, other_scores.criterion_id)
        .subquery()
    )
    dev = db.scalar(
        select(func.avg(func.abs(mine.c.mine - consensus.c.others))).join(
            consensus,
            (consensus.c.task_id == mine.c.task_id)
            & (consensus.c.criterion_id == mine.c.criterion_id),
        )
    )
    reviewed = approved + rejected
    return {
        "expert_id": expert_id,
        "grades_total": total,
        "grades_approved": approved,
        "grades_rejected": rejected,
        "approval_rate": (approved / reviewed) if reviewed else None,
        "attention_pass_rate": (att_passed / att_total) if att_total else None,
        "mean_abs_deviation_from_consensus": float(dev) if dev is not None else None,
    }
