from __future__ import annotations

from fastapi import FastAPI

from packages.api.webhook_routes import router as webhook_router

app = FastAPI(title="d2c-ai-employee")
app.include_router(webhook_router)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
