from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agentic_workflow_for_literature_synthesis.core.config import get_settings
from agentic_workflow_for_literature_synthesis.core.logging import (
    configure_logging,
    get_logger,
)

settings = get_settings()
configure_logging(settings.log_level)

logger = get_logger("api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Manage application startup and shutdown."""
    logger.info("Application starting in %s mode", settings.environment)
    yield
    logger.info("Application shutting down")


app = FastAPI(
    title=settings.app_name,
    description="Backend API for an agentic scientific literature synthesis system.",
    version=settings.app_version,
    lifespan=lifespan,
)


@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    """Report whether the API process is responding."""
    return {
        "status": "ok",
        "service": "literature-synthesis-api",
        "version": settings.app_version,
    }
