from fastapi import FastAPI

app = FastAPI(
    title="Agentic Workflow for Literature Synthesis",
    description="Backend API for an agentic scientific literature synthesis system.",
    version="0.1.0",
)


@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    """Report whether the API process is responding."""
    return {
        "status": "ok",
        "service": "literature-synthesis-api",
        "version": "0.1.0",
    }