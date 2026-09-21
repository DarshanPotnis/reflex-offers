"""The deployable service: API plus the built frontend, one process, one URL."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine

from .api import register_handlers, router
from .db import create_tables, get_engine

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(engine: Engine | None = None) -> FastAPI:
    """`engine` is injectable so the tests never touch the real database."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        create_tables(engine or get_engine())
        yield

    app = FastAPI(title="Reflex supplier offer tool", lifespan=lifespan)
    register_handlers(app)
    app.include_router(router)

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {"ok": True, "fault_injection": os.environ.get("FAULT_INJECTION") == "1"}

    if FRONTEND_DIR.is_dir():
        _serve_frontend(app)

    return app


def _serve_frontend(app: FastAPI) -> None:
    """Serve the Vite build, falling back to index.html so a hard refresh on
    /offers/{id} still loads the app."""
    assets = FRONTEND_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = FRONTEND_DIR / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        candidate = FRONTEND_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
