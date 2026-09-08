from sqlalchemy.orm import Session

from panelist.models import AuditEvent


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
