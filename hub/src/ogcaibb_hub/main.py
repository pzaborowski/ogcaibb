"""ogcaibb-hub FastAPI app.

Wires auth + storage from the plugin registries on startup and registers a
single bearer-token middleware that attaches an Identity to every request.

Unauthenticated paths: /healthz only.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .api import health, ingest
from .auth.base import HubAuth
from .auth.registry import get_hub_auth
from .config import settings
from .storage.registry import get_index_store, get_trace_store

log = logging.getLogger("ogcaibb_hub")

PUBLIC_PATHS = {"/healthz"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=settings.log_level)
    log.info("ogcaibb-hub starting on %s:%d", settings.host, settings.port)

    app.state.auth = get_hub_auth(settings.auth, keys_file=settings.apikeys_file)
    app.state.trace_store = get_trace_store(
        settings.trace_store, root=settings.trace_store_path
    )
    app.state.index_store = get_index_store(
        settings.index_store, db_path=settings.index_store_path
    )

    log.info(
        "auth=%s trace_store=%s(%s) index_store=%s(%s)",
        settings.auth,
        settings.trace_store, settings.trace_store_path,
        settings.index_store, settings.index_store_path,
    )

    try:
        yield
    finally:
        await app.state.index_store.close()


app = FastAPI(title="ogcaibb-hub", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    auth: HubAuth = request.app.state.auth
    try:
        identity = await auth.verify(dict(request.headers))
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    request.state.identity = identity
    return await call_next(request)


app.include_router(health.router)
app.include_router(ingest.router)


def run() -> None:
    import uvicorn

    uvicorn.run(
        "ogcaibb_hub.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    run()
