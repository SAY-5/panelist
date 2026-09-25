from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from panelist import schemas
from panelist.auth import Principal, require_scopes
from panelist.db import get_db
from panelist.models import Delivery
from panelist.services import delivery

router = APIRouter(prefix="/deliveries", tags=["deliveries"])


@router.post("", response_model=schemas.DeliveryOut, status_code=201)
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
    db: Session = Depends(get_db), _: Principal = Depends(require_scopes("deliveries:read"))
):
    return db.scalars(select(Delivery).order_by(Delivery.version)).all()


@router.get("/{version}/verify", response_model=schemas.DeliveryVerification)
def verify(
    version: int,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_scopes("deliveries:read")),
):
    """Read the stored object back and recompute what the delivery row claims about it."""
    row = db.scalar(select(Delivery).where(Delivery.version == version))
    if row is None:
        raise HTTPException(404, "delivery not found")
    return delivery.verify(row)
