from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> dict:
    index = request.app.state.index_store
    return {
        "status": "ok",
        "traces": await index.count_traces(),
    }
