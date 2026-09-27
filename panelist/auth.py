import hashlib
import secrets
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.orm import Session

from panelist.db import get_db
from panelist.models import ApiKey, Expert, Role

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

REVIEWER_SCOPES = frozenset(
    {
        "grades:read",
        "reviews:write",
        "tasks:read",
        "payouts:read",
        "analytics:read",
        "deliveries:read",
    }
)

SCOPES: dict[Role, frozenset[str]] = {
    Role.expert: frozenset({"tasks:claim", "grades:write", "experts:self"}),
    Role.reviewer: REVIEWER_SCOPES,
    Role.senior_reviewer: REVIEWER_SCOPES | {"adjudications:write"},
    Role.admin: frozenset(
        {
            "tasks:claim",
            "grades:write",
            "grades:read",
            "reviews:write",
            "adjudications:write",
            "tasks:read",
            "tasks:write",
            "experts:self",
            "experts:write",
            "payouts:read",
            "payouts:write",
            "analytics:read",
            "deliveries:read",
            "deliveries:write",
            "rubrics:write",
            "admin",
        }
    ),
}


@dataclass(frozen=True)
class Principal:
    key_id: str
    role: Role
    expert_id: str | None
    scopes: frozenset[str]

    @property
    def actor(self) -> str:
        return f"{self.role.value}:{self.key_id}"


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_key(role: Role) -> str:
    return f"pk_{role.value}_{secrets.token_urlsafe(24)}"


def issue_key(db: Session, role: Role, expert_id=None, label: str = "") -> tuple[ApiKey, str]:
    raw = generate_key(role)
    key = ApiKey(key_hash=hash_key(raw), role=role, expert_id=expert_id, label=label)
    db.add(key)
    db.flush()
    return key, raw


def authenticate(
    raw: str | None = Security(api_key_header), db: Session = Depends(get_db)
) -> Principal:
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing API key")
    key = db.scalar(
        select(ApiKey).where(ApiKey.key_hash == hash_key(raw), ApiKey.revoked_at.is_(None))
    )
    if key is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")
    return Principal(
        key_id=str(key.id),
        role=key.role,
        expert_id=str(key.expert_id) if key.expert_id else None,
        scopes=SCOPES[key.role],
    )


def require_scopes(*needed: str):
    def dependency(principal: Principal = Depends(authenticate)) -> Principal:
        missing = [s for s in needed if s not in principal.scopes]
        if missing:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing scope: {', '.join(missing)}")
        return principal

    return dependency


def current_expert(
    principal: Principal = Depends(require_scopes("experts:self")),
    db: Session = Depends(get_db),
) -> Expert:
    if principal.expert_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "API key is not bound to an expert")
    expert = db.get(Expert, principal.expert_id)
    if expert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "expert not found")
    return expert
