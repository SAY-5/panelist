from datetime import datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from panelist import schemas
from panelist.auth import Principal, require_scopes
from panelist.db import get_db
from panelist.services import audit, ops

router = APIRouter(prefix="/ops", tags=["ops"])


@router.get("/overview", response_model=schemas.OpsOverview)
def overview(db: Session = Depends(get_db), _: Principal = Depends(require_scopes("tasks:read"))):
    return ops.overview(db)


@router.get("/audit.csv")
def audit_export(
    since: datetime | None = None,
    action: str | None = None,
    limit: int = 10000,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_scopes("admin")),
):
    body = audit.export_csv(db, since=since, action=action, limit=min(limit, 100000))
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="panelist-audit.csv"'},
    )
