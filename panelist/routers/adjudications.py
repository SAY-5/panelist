import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from panelist import schemas
from panelist.auth import Principal, require_scopes
from panelist.config import get_settings
from panelist.db import get_db
from panelist.models import Consensus
from panelist.services import consensus

router = APIRouter(prefix="/adjudications", tags=["adjudications"])


def _view(db: Session, row: Consensus) -> schemas.AdjudicationOut:
    grades = consensus.grades_for(db, row.task_id)
    return schemas.AdjudicationOut(
        task_id=row.task_id,
        external_ref=row.task.external_ref,
        prompt=row.task.prompt,
        status=row.status,
        grade_count=row.grade_count,
        spread=row.spread,
        tolerance=row.tolerance,
        opened_at=row.created_at,
        grades=[
            schemas.ConsensusGradeOut(
                grade_id=g.id,
                expert_id=g.expert_id,
                expert_name=g.expert.name,
                weighted_score=g.weighted_score,
                scores=g.scores_snapshot,
                rationale=g.rationale,
            )
            for g in grades
        ],
    )


@router.get("", response_model=list[schemas.AdjudicationOut])
def queue(
    limit: int = 200,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_scopes("tasks:read")),
):
    return [_view(db, row) for row in consensus.pending(db, min(limit, 1000))]


@router.post("/{task_id}", response_model=schemas.AdjudicationResult)
def decide(
    task_id: uuid.UUID,
    body: schemas.AdjudicationDecision,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scopes("adjudications:write")),
):
    try:
        decision = consensus.resolve(
            db,
            task_id,
            body.delivered_grade_id,
            body.reason,
            uuid.UUID(principal.key_id),
            principal.actor,
        )
    except consensus.ConsensusError as e:
        db.rollback()
        raise HTTPException(e.status_code, e.detail) from e
    result = schemas.AdjudicationResult(
        task_id=task_id,
        status=decision.round.status,
        delivered_grade_id=body.delivered_grade_id,
        delivered_amount_cents=decision.delivered.amount_cents,
        outvoted_rule=get_settings().consensus_outvoted_payout,
        outvoted=[
            schemas.OutvotedOut(
                grade_id=grade.id,
                expert_id=grade.expert_id,
                payout_id=payout.id if payout else None,
                amount_cents=payout.amount_cents if payout else None,
            )
            for grade, payout in decision.outvoted
        ],
    )
    db.commit()
    return result
