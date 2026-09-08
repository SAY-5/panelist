from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from panelist import schemas
from panelist.auth import Principal, require_scopes
from panelist.db import get_db
from panelist.models import Delivery
from panelist.services import delivery

router = APIRouter(prefix="/deliveries", tags=["deliveries"])


@router.get("/export", response_model=schemas.DeliveryOut)
def export(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scopes("deliveries:write")),
):
    """Build, checksum and store a new versioned dataset of approved grades."""
    out = delivery.export(db, principal.actor)
    db.commit()
    return out


@router.get("", response_model=list[schemas.DeliveryOut])
def list_deliveries(
    db: Session = Depends(get_db), _: Principal = Depends(require_scopes("deliveries:write"))
):
    return db.scalars(select(Delivery).order_by(Delivery.version)).all()
