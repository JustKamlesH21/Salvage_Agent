"""FastAPI application.

Day 0: startup applies the indexes and /health reports the safety posture.
Routes arrive with their phases (PRD §11).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from api.config import settings
from api.db.client import close_client, get_db
from api.db.collections import apply_indexes


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await apply_indexes(get_db())
    yield
    await close_client()


app = FastAPI(title="Salvage", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Safety posture, not just liveness. Which mode you are in is never a guess."""
    cfg = settings()
    return {
        "status": "ok",
        "app_env": cfg.app_env,
        "dry_run": cfg.dry_run,
        "kill_switch": cfg.kill_switch,
        "storage_tz": cfg.tz,
        "compliance_tz": cfg.compliance_tz,
    }
