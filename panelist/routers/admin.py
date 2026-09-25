import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from panelist import schemas
from panelist.auth import Principal, issue_key, require_scopes
from panelist.db import get_db
from panelist.models import ApiKey
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
    return schemas.ApiKeyOut(
        id=key.id, role=key.role, expert_id=key.expert_id, label=key.label, key=raw
    )


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke_key(
    key_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scopes("admin")),
):
    """Stamp `revoked_at`; the key fails authentication from the next request on."""
    key = db.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(404, "api key not found")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        audit.record(db, principal.actor, "apikey.revoked", "api_key", key.id, {"role": key.role})
        db.commit()
    return Response(status_code=204)
