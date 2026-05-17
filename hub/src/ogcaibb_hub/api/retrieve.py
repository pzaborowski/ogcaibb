"""POST /v1/retrieve — fetch top-k exemplars by semantic similarity.

Body:
    {
      "query": "the user message to match against",
      "top_k": 5,
      "min_score": 0.0,
      "filter": {
        "agent": "optional agent name",
        "skill": "optional skill name",
        "scope": "self" | "all"        # default "all"
      }
    }

When scope == "self", we filter by the caller's identity (its workstation_id
attribute when present; falls back to identity.sub). When "all", any indexed
trace can come back.

Response:
    {
      "results": [
        {"trace_id": "...", "score": 0.87, "user_message": "...",
         "assistant_text": "...", "agent": "...", "skill": "...",
         "model": "..."}
      ]
    }
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import endpoints

log = logging.getLogger(__name__)
router = APIRouter()


class RetrieveFilter(BaseModel):
    agent: str | None = None
    skill: str | None = None
    scope: str = "all"  # "self" | "all"


class RetrieveRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    filter: RetrieveFilter = Field(default_factory=RetrieveFilter)


@router.post(endpoints.RETRIEVE)
async def retrieve(req: RetrieveRequest, request: Request) -> dict:
    vector_store = getattr(request.app.state, "vector_store", None)
    embedder = getattr(request.app.state, "embedder", None)
    if vector_store is None or embedder is None:
        raise HTTPException(503, "retrieval is not configured on this hub")

    identity = request.state.identity
    filter_workstation: str | None = None
    if req.filter.scope == "self":
        # Use the identity's attached workstation_id if present, else its sub.
        filter_workstation = (
            (identity.attrs or {}).get("workstation_id") or identity.sub
        )

    try:
        vectors = await embedder.embed([req.query])
    except Exception as e:
        log.warning("retrieve: embedding failed: %s", e)
        raise HTTPException(502, f"embedder failed: {e}") from e
    if not vectors:
        return {"results": []}

    hits = await vector_store.query(
        vector=vectors[0],
        top_k=req.top_k,
        filter_agent=req.filter.agent,
        filter_skill=req.filter.skill,
        filter_workstation=filter_workstation,
        min_score=req.min_score,
    )
    return {
        "results": [
            {
                "trace_id": h.trace_id,
                "score": h.score,
                "user_message": h.user_message,
                "assistant_text": h.assistant_text,
                "agent": h.agent,
                "skill": h.skill,
                "model": h.model,
            }
            for h in hits
        ]
    }
