from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest


router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready() -> dict[str, str]:
    return {"status": "ready"}


@router.get("/metrics")
def metrics() -> Response:
    data = generate_latest()  # pulls from the default registry
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)

