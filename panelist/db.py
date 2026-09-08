from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from panelist.config import get_settings

_engine = None
_session_factory: sessionmaker | None = None


def get_engine():
    global _engine, _session_factory
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url, pool_pre_ping=True, pool_size=20, max_overflow=30
        )
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def session_factory() -> sessionmaker:
    get_engine()
    assert _session_factory is not None
    return _session_factory


def reset_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def get_db() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()
