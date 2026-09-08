import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from panelist import schemas
from panelist.auth import Principal, require_scopes
from panelist.db import get_db
from panelist.models import RateCard, Rubric, RubricCriterion
from panelist.services import audit

router = APIRouter(tags=["rubrics"])


@router.post("/rubrics", response_model=schemas.RubricOut, status_code=201)
def create_rubric(
    body: schemas.RubricCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scopes("rubrics:write")),
):
    rubric = Rubric(name=body.name, version=body.version)
    rubric.criteria = [
        RubricCriterion(position=i, **c.model_dump()) for i, c in enumerate(body.criteria)
    ]
    db.add(rubric)
    db.flush()
    audit.record(db, principal.actor, "rubric.created", "rubric", rubric.id)
    db.commit()
    return rubric


@router.get("/rubrics/{rubric_id}", response_model=schemas.RubricOut)
def get_rubric(
    rubric_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_scopes("experts:self")),
):
    rubric = db.get(Rubric, rubric_id)
    if rubric is None:
        raise HTTPException(404, "rubric not found")
    return rubric


@router.put("/rate-cards", status_code=204)
def put_rate_cards(
    body: list[schemas.RateCardIn],
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scopes("payouts:write")),
):
    for card in body:
        db.merge(RateCard(**card.model_dump()))
    audit.record(db, principal.actor, "ratecard.updated", "rate_card", "bulk", {"count": len(body)})
    db.commit()
