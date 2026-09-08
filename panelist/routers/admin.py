from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from panelist import schemas
from panelist.auth import Principal, issue_key, require_scopes
from panelist.db import get_db
from panelist.services import audit

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/api-keys", response_model=schemas.ApiKeyOut, status_code=201)
def create_key(
    body: schemas.ApiKeyCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scopes("admin")),
):
    key, raw = issue_key(db, body.role, expert_id=body.expert_id, label=body.label)
    audit.record(db, principal.actor, "apikey.issued", "api_key", key.id, {"role": body.role.value})
    db.commit()
    return schemas.ApiKeyOut(id=key.id, role=key.role, expert_id=key.expert_id, label=key.label, key=raw)
