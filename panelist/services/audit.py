import csv
import io
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from panelist.models import AuditEvent

COLUMNS = ["id", "created_at", "actor", "action", "entity_type", "entity_id", "payload"]


def record(
    db: Session, actor: str, action: str, entity_type: str, entity_id, payload: dict | None = None
) -> None:
    db.add(
        AuditEvent(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            payload=payload or {},
        )
    )


def export_csv(
    db: Session, since: datetime | None = None, action: str | None = None, limit: int = 10000
) -> str:
    """The append-only trail as CSV, oldest row first, exactly as it was written."""
    stmt = select(AuditEvent).order_by(AuditEvent.id).limit(limit)
    if since is not None:
        stmt = stmt.where(AuditEvent.created_at >= since)
    if action is not None:
        stmt = stmt.where(AuditEvent.action == action)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    for event in db.scalars(stmt):
        writer.writerow(
            [
                event.id,
                event.created_at.isoformat(),
                event.actor,
                event.action,
                event.entity_type,
                event.entity_id,
                json.dumps(event.payload, sort_keys=True, separators=(",", ":")),
            ]
        )
    return buf.getvalue()
