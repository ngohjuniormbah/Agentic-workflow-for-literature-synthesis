from fastapi import FastAPI

from agentic_workflow_for_literature_synthesis.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="Backend API for an agentic scientific literature synthesis system.",
    version=settings.app_version,
)


@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    """Report whether the API process is responding."""
    return {
        "status": "ok",
        "service": "literature-synthesis-api",
        "version": settings.app_version,
    }
