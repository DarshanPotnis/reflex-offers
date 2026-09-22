"""The deployable service: API plus the built frontend, one process, one URL."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine

from .api import register_handlers, router
from .db import ConfigurationError, create_tables, get_engine
from .runtime import cpu_info

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(engine: Engine | None = None) -> FastAPI:
    """`engine` is injectable so the tests never touch the real database."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            create_tables(engine or get_engine())
        except ConfigurationError as exc:
            # Advice, not a stack trace: this is someone's environment, not a
            # bug in the app. os._exit rather than SystemExit because the ASGI
            # server catches exceptions raised in a lifespan hook and prints
            # its own traceback on top, which is the thing we are avoiding.
            print(f"\nCan't start the app.\n\n{exc}\n", file=sys.stderr, flush=True)
            sys.stderr.flush()
            os._exit(1)
        yield

    app = FastAPI(title="Reflex supplier offer tool", lifespan=lifespan)
    # The 5,000-row offer serialises to ~5.7 MB of JSON and the workflow
    # fetches it three times. Level 6 rather than 9: the last few percent of
    # ratio costs more CPU than it saves on the wire. Measured before/after
    # in docs/speed.md; the totals are identical either way.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
    register_handlers(app)
    app.include_router(router)

    # HEAD as well as GET: uptime monitors and Render's own probes send HEAD,
    # and FastAPI does not add it automatically the way plain Starlette does.
    @app.api_route("/api/health", methods=["GET", "HEAD"])
    def health() -> dict[str, object]:
        # The CPU quota is here so every measurement records the hardware it
        # ran on. A plan change that silently did not apply is otherwise
        # indistinguishable from a change that did nothing.
        return {
            "ok": True,
            "fault_injection": os.environ.get("FAULT_INJECTION") == "1",
            **cpu_info(),
        }

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

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        candidate = FRONTEND_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
