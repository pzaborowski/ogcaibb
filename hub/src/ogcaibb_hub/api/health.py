from __future__ import annotations

from fastapi import APIRouter, Request

from .. import endpoints

router = APIRouter()


@router.get(endpoints.HEALTH)
async def healthz(request: Request) -> dict:
    index = request.app.state.index_store
    return {
        "status": "ok",
        "traces": await index.count_traces(),
    }
