"""One capability per container; models are loaded from administrator-controlled settings."""

import asyncio
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

CAPABILITY = os.environ.get("CAPABILITY", "embedding")
MODEL = os.environ["MODEL_NAME"]
REVISION = os.environ["MODEL_REVISION"]
API_KEY = os.environ["API_KEY"]
if not API_KEY or not REVISION:
    raise RuntimeError("API key and pinned model revision are required")


@asynccontextmanager
async def lifespan(app):
    from sentence_transformers import CrossEncoder, SentenceTransformer

    loader = SentenceTransformer if CAPABILITY == "embedding" else CrossEncoder
    app.state.model = await asyncio.to_thread(
        loader,
        MODEL,
        revision=REVISION,
        device=os.environ.get("MODEL_DEVICE", "cpu"),
        trust_remote_code=False,
    )
    app.state.lock = asyncio.Lock()
    yield


async def authenticate(authorization: str = Header(default="")):
    if not secrets.compare_digest(authorization, f"Bearer {API_KEY}"):
        raise HTTPException(401, "invalid service credential")


app = FastAPI(lifespan=lifespan, dependencies=[Depends(authenticate)])


class EmbeddingRequest(BaseModel):
    model: str
    input: list[str] = Field(min_length=1, max_length=64)


class RerankRequest(BaseModel):
    model: str
    query: str = Field(min_length=1, max_length=4000)
    documents: list[str] = Field(min_length=1, max_length=100)


def validate(model, texts, capability):
    if model != MODEL or CAPABILITY != capability:
        raise HTTPException(400, "model/capability mismatch")
    if any(not text or len(text) > 16000 for text in texts):
        raise HTTPException(413, "input text size invalid")


@app.get("/health/live")
async def health():
    return {"status": "ready", "model": MODEL, "revision": REVISION, "capability": CAPABILITY}


@app.post("/v1/embeddings")
async def embeddings(payload: EmbeddingRequest):
    validate(payload.model, payload.input, "embedding")
    async with app.state.lock:
        values = await asyncio.to_thread(
            app.state.model.encode,
            payload.input,
            normalize_embeddings=True,
            batch_size=16,
            show_progress_bar=False,
        )
    return {
        "model": MODEL,
        "revision": REVISION,
        "data": [{"index": i, "embedding": vector.tolist()} for i, vector in enumerate(values)],
    }


@app.post("/v1/rerank")
async def rerank(payload: RerankRequest):
    validate(payload.model, payload.documents, "rerank")
    async with app.state.lock:
        values = await asyncio.to_thread(
            app.state.model.predict,
            [(payload.query, text) for text in payload.documents],
            batch_size=8,
            show_progress_bar=False,
        )
    return {
        "model": MODEL,
        "revision": REVISION,
        "results": [
            {"index": i, "relevance_score": float(value)} for i, value in enumerate(values)
        ],
    }
