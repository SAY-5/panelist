"""Data delivery: versioned, checksummed JSONL datasets of approved grades."""

import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from panelist.config import get_settings
from panelist.models import Consensus, Delivery, Grade, Review, ReviewDecision, Task
from panelist.services import audit


def _rows(db: Session):
    """One row per delivered grade: consensus tasks deliver only the grade that was chosen."""
    rows = db.execute(
        select(Grade, Consensus)
        .join(Review, Review.grade_id == Grade.id)
        .join(Task, Task.id == Grade.task_id)
        .outerjoin(Consensus, Consensus.task_id == Task.id)
        .where(
            Review.decision == ReviewDecision.approve,
            Task.is_attention_check.is_(False),
            or_(Consensus.id.is_(None), Consensus.delivered_grade_id == Grade.id),
        )
        .order_by(Task.seq, Grade.expert_id)
    ).all()
    for g, round_ in rows:
        task = g.task
        yield {
            "task_id": str(task.id),
            "external_ref": task.external_ref,
            "task_type": task.task_type,
            "required_tags": sorted(task.required_tags),
            "prompt": task.prompt,
            "responses": task.responses,
            "rubric": {
                "id": str(g.rubric_id),
                "name": g.rubric.name,
                "version": g.rubric.version,
            },
            "expert_id": str(g.expert_id),
            "expert_tier": g.expert.tier.value,
            "scores": {
                s.criterion.key: s.score
                for s in sorted(g.scores, key=lambda s: s.criterion.position)
            },
            "weighted_score": g.weighted_score,
            "rationale": g.rationale,
            "time_spent_seconds": g.time_spent_seconds,
            "consensus": None
            if round_ is None
            else {
                "status": round_.status.value,
                "graders": round_.grade_count,
                "spread": round_.spread,
            },
        }


def build_jsonl(db: Session) -> tuple[bytes, int]:
    lines = [json.dumps(r, sort_keys=True, separators=(",", ":")) for r in _rows(db)]
    body = ("\n".join(lines) + ("\n" if lines else "")).encode()
    return body, len(lines)


def _s3_client():
    import boto3

    settings = get_settings()
    return boto3.client(
        "s3", endpoint_url=settings.aws_endpoint_url or None, region_name=settings.aws_region
    )


def _store(body: bytes, version: int, checksum: str) -> str:
    settings = get_settings()
    name = f"panelist-grades-v{version}-{checksum[:12]}.jsonl"
    if settings.delivery_s3_bucket:
        client = _s3_client()
        key = f"deliveries/{name}"
        client.put_object(
            Bucket=settings.delivery_s3_bucket,
            Key=key,
            Body=body,
            Metadata={"sha256": checksum, "version": str(version)},
        )
        return f"s3://{settings.delivery_s3_bucket}/{key}"
    out_dir = Path(settings.delivery_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_bytes(body)
    return os.fspath(path.resolve())


def _read(location: str) -> bytes:
    if location.startswith("s3://"):
        bucket, key = location[len("s3://") :].split("/", 1)
        return _s3_client().get_object(Bucket=bucket, Key=key)["Body"].read()
    return Path(location).read_bytes()


def verify(row: Delivery) -> dict:
    """Read the stored object back; the sha256, row count and size must be what the row says."""
    body = _read(row.location)
    checksum = hashlib.sha256(body).hexdigest()
    rows_read = body.count(b"\n")
    return {
        "version": row.version,
        "location": row.location,
        "stored_checksum": row.checksum,
        "checksum": checksum,
        "row_count": row.row_count,
        "rows_read": rows_read,
        "size_bytes": row.size_bytes,
        "bytes_read": len(body),
        "match": checksum == row.checksum
        and rows_read == row.row_count
        and len(body) == row.size_bytes,
    }


def export(db: Session, actor: str) -> Delivery:
    body, count = build_jsonl(db)
    checksum = hashlib.sha256(body).hexdigest()
    version = int(db.scalar(select(func.coalesce(func.max(Delivery.version), 0))) or 0) + 1
    location = _store(body, version, checksum)
    delivery = Delivery(
        version=version,
        checksum=checksum,
        location=location,
        row_count=count,
        size_bytes=len(body),
    )
    db.add(delivery)
    db.flush()
    audit.record(
        db, actor, "delivery.exported", "delivery", delivery.id, {"version": version, "rows": count}
    )
    return delivery
