from __future__ import annotations

import os
from contextlib import asynccontextmanager
import sys

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from ledger.apps.api.observability.tracing import init_tracing
from ledger.apps.api.routes import accounts, transfers, journals, reports, health
from ledger.db.models import Base
from ledger.db.session import engine, SessionLocal
from sqlalchemy import text
from ledger.settings import get_settings
from ledger.apps.api.observability.metrics import REQUEST_COUNT, REQUEST_LATENCY
from time import perf_counter


load_dotenv()


def _configure_logging() -> None:
    # Basic Loguru setup; JSON when LOG_FORMAT=json
    fmt = os.getenv("LOG_FORMAT", "plain").lower()
    logger.remove()
    if fmt == "json":
        logger.add(sys.stdout, serialize=True, backtrace=False, diagnose=False)
    else:
        logger.add(sys.stdout, backtrace=False, diagnose=False,
                   format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {message}")


def _auto_migrate() -> None:
    auto = os.getenv("MIGRATE_ON_START", os.getenv("AUTO_MIGRATE", "false")).lower() in {"1", "true", "yes"}
    if not auto:
        return
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config("alembic.ini")
        logger.info("Running Alembic migrations (upgrade head)...")
        command.upgrade(cfg, "head")
    except Exception as e:
        logger.warning("Alembic failed, falling back to create_all: {}", e)
        Base.metadata.create_all(bind=engine)


def _db_healthcheck() -> None:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connectivity: OK")
    except Exception as e:
        logger.error("Database connectivity check failed: {}", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    init_tracing(app)
    _db_healthcheck()
    _auto_migrate()
    yield


_settings = get_settings()
app = FastAPI(title=_settings.app_name, version=_settings.app_version, lifespan=lifespan)

# CORS — open to make the public landing widgets callable from any origin
# (portfolio preview, localhost, stelioszach.com itself). The API is read-
# dominant and write endpoints are idempotent + bounded to demo accounts,
# so wide-open CORS is intentional for the portfolio demo.
_cors_env = os.getenv("CORS_ALLOW_ORIGINS", "*")
_allow_origins = ["*"] if _cors_env.strip() == "*" else [o.strip() for o in _cors_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(health.router)

# API namespace
from fastapi import APIRouter

api = APIRouter(prefix="/api")
api.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
api.include_router(transfers.router, prefix="/transfers", tags=["transfers"])
api.include_router(journals.router, prefix="/journals", tags=["journals"])
api.include_router(reports.router, prefix="/reports", tags=["reports"])


@api.get("/health")
def api_health():
    return {"status": "ok", "version": _settings.app_version}


app.include_router(api)


@app.middleware("http")
async def prometheus_http_metrics(request: Request, call_next):
    method = request.method
    path = request.url.path
    start = perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        dur = perf_counter() - start
        try:
            REQUEST_COUNT.labels(method=method, path=path, status=str(status)).inc()
            REQUEST_LATENCY.labels(method=method, path=path).observe(dur)
        except Exception:
            pass


@logger.catch
def main() -> None:
    import uvicorn

    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "8000"))
    uvicorn.run("ledger.apps.api.main:app", host=host, port=port, reload=True)


if __name__ == "__main__":  # pragma: no cover
    main()
