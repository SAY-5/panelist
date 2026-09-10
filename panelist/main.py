from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.orm import Session

from panelist import __version__
from panelist.config import get_settings
from panelist.db import get_db, get_engine
from panelist.logging import configure_logging, get_logger
from panelist.metrics import refresh_gauges
from panelist.routers import (
    adjudications,
    admin,
    analytics,
    deliveries,
    experts,
    grades,
    ops,
    payouts,
    rubrics,
    tasks,
)

log = get_logger("panelist")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    get_engine()
    log.info("startup", version=__version__)
    yield
    log.info("shutdown")


app = FastAPI(
    title="Panelist",
    version=__version__,
    description="Expert grading and data delivery platform for LLM responses.",
    lifespan=lifespan,
)

for r in (
    experts,
    rubrics,
    tasks,
    grades,
    adjudications,
    ops,
    payouts,
    analytics,
    deliveries,
    admin,
):
    app.include_router(r.router)


@app.get("/healthz", tags=["ops"])
def healthz(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "version": __version__}


@app.get("/metrics", tags=["ops"])
def metrics(db: Session = Depends(get_db)):
    refresh_gauges(db)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
