from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logging.getLogger("packages").setLevel(logging.INFO)

from packages.api.chat_routes import get_llm
from packages.api.chat_routes import router as chat_router
from packages.api.run_log_routes import router as runs_router
from packages.api.trigger_routes import router as triggers_router
from packages.api.webhook_routes import router as webhook_router
from packages.config import settings


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if settings.gemini_api_key:
        get_llm()._ensure_client()  # type: ignore[attr-defined]
    yield


app = FastAPI(title="d2c-ai-employee", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook_router)
app.include_router(chat_router)
app.include_router(runs_router)
app.include_router(triggers_router)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
