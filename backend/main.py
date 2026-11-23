"""
Safe Web Agent API — FastAPI application entry point.

Endpoints:
  GET  /                 — Root info
  GET  /health           — Health check
  GET  /docs             — Swagger UI
  GET  /redoc            — ReDoc
  ...all API routes registered below
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from routes.task_routes import router as task_router
from routes.agent_routes import router as agent_router
from routes.approval_routes import router as approval_router
from routes.report_routes import router as report_router

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("safe_web_agent")


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    warnings = settings.validate()
    if warnings:
        for w in warnings:
            logger.warning("Config warning: %s", w)
    logger.info("Safe Web Agent API starting up...")
    yield
    logger.info("Safe Web Agent API shutting down...")


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Safe Web Agent API",
    description=(
        "PS-11 Hackathon — Safe Web Interactive Agent backend.\n\n"
        "Allows students to give natural-language instructions to an AI agent "
        "that navigates college web portals securely, with prompt-injection "
        "protection and official organizer confirmation for every irreversible action."
    ),
    version="1.0.0",
    contact={"name": "Team PS-11"},
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ── CORS ───────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(task_router)
app.include_router(agent_router)
app.include_router(approval_router)
app.include_router(report_router)


# ── Global exception handler ───────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s %s → %s", request.method, request.url, exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_server_error",
            "detail": str(exc),
            "path": str(request.url),
        },
    )


# ── Root & Health ──────────────────────────────────────────────────────────────
@app.get("/", tags=["Meta"], summary="API root")
async def root():
    return {
        "name": "Safe Web Agent API",
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc",
        "health": "/health",
        "status": "online",
    }


@app.get("/health", tags=["Meta"], summary="Health check")
async def health():
    warnings = settings.validate()
    return {
        "status": "healthy",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "config_warnings": warnings,
        "portal_url_set": bool(settings.PORTAL_URL),
        "team_code_set": bool(settings.TEAM_CODE),
        "llm_key_set": bool(settings.OPENROUTER_API_KEY),
    }


# ── Dev entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

