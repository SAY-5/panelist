import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from panelist import schemas
from panelist.auth import Principal, current_expert, require_scopes
from panelist.db import get_db
from panelist.metrics import ASSIGNMENT_LATENCY
from panelist.models import Expert, Task
from panelist.services import audit, routing, rubrics

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=schemas.TaskBulkOut, status_code=201)
def create_tasks(
    body: schemas.TaskBulkCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scopes("tasks:write")),
):
    stale = rubrics.superseded_among(db, {t.rubric_id for t in body.tasks})
    if stale is not None:
        raise HTTPException(409, f"rubric {stale.name} version {stale.version} is superseded")
    tasks = []
    for t in body.tasks:
        if t.is_attention_check and not t.expected_scores:
            raise HTTPException(422, "attention checks require expected_scores")
        tasks.append(Task(**t.model_dump()))
    db.add_all(tasks)
    db.flush()
    audit.record(db, principal.actor, "tasks.created", "task", "bulk", {"count": len(tasks)})
    db.commit()
    return schemas.TaskBulkOut(created=len(tasks), ids=[t.id for t in tasks])


@router.post(
    "/next",
    response_model=schemas.TaskExpertView,
    responses={204: {"description": "no eligible task"}},
)
def next_task(
    response: Response, db: Session = Depends(get_db), expert: Expert = Depends(current_expert)
):
    started = time.perf_counter()
    try:
        task = routing.claim_next(db, expert)
    except routing.ClaimError as e:
        db.rollback()
        raise HTTPException(e.status_code, e.detail) from e
    db.commit()
    ASSIGNMENT_LATENCY.observe(time.perf_counter() - started)
    if task is None:
        response.status_code = 204
        return Response(status_code=204)
    return task


@router.post("/reclaim", response_model=schemas.ReclaimOut)
def reclaim(db: Session = Depends(get_db), _: Principal = Depends(require_scopes("tasks:write"))):
    n = routing.reclaim_expired(db)
    db.commit()
    return schemas.ReclaimOut(reclaimed=n)


@router.get("/queue")
def queue(db: Session = Depends(get_db), _: Principal = Depends(require_scopes("tasks:read"))):
    return {"by_status": routing.queue_summary(db), "queued_by_tag": routing.queue_depth_by_tag(db)}


@router.get("/{task_id}", response_model=schemas.TaskAdminView)
def get_task(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_scopes("tasks:read")),
):
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    return task


@router.post("/{task_id}/claim", response_model=schemas.TaskExpertView)
def claim_task(
    task_id: uuid.UUID, db: Session = Depends(get_db), expert: Expert = Depends(current_expert)
):
    try:
        task = routing.claim_by_id(db, expert, task_id)
    except routing.ClaimError as e:
        db.rollback()
        raise HTTPException(e.status_code, e.detail) from e
    db.commit()
    return task


@router.post("/{task_id}/release", status_code=204)
def release_task(
    task_id: uuid.UUID, db: Session = Depends(get_db), expert: Expert = Depends(current_expert)
):
    try:
        routing.release(db, expert, task_id)
    except routing.ClaimError as e:
        db.rollback()
        raise HTTPException(e.status_code, e.detail) from e
    db.commit()
