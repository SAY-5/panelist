"""Operational commands: bootstrap the first admin key, run the scheduler tick."""

import argparse
import json
import sys

from sqlalchemy import select

from panelist.auth import hash_key
from panelist.config import get_settings
from panelist.db import session_factory
from panelist.models import ApiKey, Role
from panelist.services import ops


def bootstrap(raw_key: str | None) -> str:
    settings = get_settings()
    raw = raw_key or settings.bootstrap_admin_key
    if not raw:
        raise SystemExit("provide --key or set BOOTSTRAP_ADMIN_KEY")
    with session_factory()() as db:
        existing = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(raw)))
        if existing is None:
            db.add(ApiKey(key_hash=hash_key(raw), role=Role.admin, label="bootstrap"))
            db.commit()
    return raw


def tick() -> dict:
    """One scheduler pass: reclaim expired leases, refresh calibration, collect reminders."""
    with session_factory()() as db:
        result = ops.tick(db)
        db.commit()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="panelist")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap", help="create the initial admin API key")
    b.add_argument("--key", default=None)
    sub.add_parser("tick", help="reclaim leases, refresh calibration, print reminders")
    args = parser.parse_args(argv)
    if args.cmd == "bootstrap":
        bootstrap(args.key)
        print("admin key ready", file=sys.stderr)
    elif args.cmd == "tick":
        print(json.dumps(tick(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
